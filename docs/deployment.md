---
title: デプロイ手順
description: AI Dinner Planner を Azure Container Apps、Cosmos DB、Microsoft Foundry Hosted Agent にデプロイする手順
ms.date: 2026-06-24
ms.topic: how-to
---

## 前提

Azure CLI と uv を使ってデプロイする。Azure には事前にログインし、`.env` を読み込んで
から手順を実行する。

```bash
az login
```

`.env.exmaple` には公開してよいデプロイ値を入れている。初回は `.env` にコピーし、必要な
値を調整する。

```bash
cp -n .env.exmaple .env
```

以降のコマンドを実行する前に、シェルへ `.env` を読み込む。

```bash
set -a
. ./.env
set +a

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
```

新しいターミナルを開いた場合や、`.env` を編集した場合は、この読み込みコマンドを再実行する。

> [!NOTE]
> 初回は Container App image や Hosted Agent identity がまだ分からないため、Bicep を
> 2回実行する。1回目で基盤を作り、Web image と Hosted Agent version を作成したあと、
> 2回目で Container App image、Hosted Agent 接続設定、RBAC を反映する。

## 全体の流れ

初回デプロイは次の順に進める。

1. `az deployment sub create` で Resource group、ACR、Container Apps、Cosmos DB、
   Foundry account/project を作成する。
2. ACR に Web image と Hosted Agent image を push し、Hosted Agent version を作成する。
3. 作成後に分かる image tag と Hosted Agent identity を使って、もう一度
   `az deployment sub create` を実行し、Container App と RBAC を完成させる。

## 依存関係を確認する

ローカルで Python 依存関係と lint を確認する。

```bash
uv sync
uv run ruff check .
```

## Azure 基盤を初回デプロイする

Bicep で Resource group、Azure Container Registry、Azure Container Apps、Cosmos DB、
Foundry account/project などを作成する。

初回は Container App の placeholder image のまま作成する。この時点では Web image、
Hosted Agent 接続設定、Hosted Agent identity の RBAC はまだ反映しない。

```bash
az deployment sub create \
  --name "$DEPLOYMENT_NAME" \
  --location "$LOCATION" \
  --template-file infra/main.bicep \
  --parameters \
    location="$LOCATION" \
    environmentName="$ENVIRONMENT_NAME" \
    namePrefix="$NAME_PREFIX"
```

Foundry project endpoint は Bicep output でも確認できる。`.env` の
`FOUNDRY_PROJECT_ENDPOINT` と違う場合は `.env` を更新する。

```bash
az deployment sub show \
  --name "$DEPLOYMENT_NAME" \
  --query properties.outputs.foundryProjectEndpoint.value \
  -o tsv
```

## Web image をビルドする

Azure Container Registry の remote build を使って Web image を作成する。

```bash
WEB_IMAGE_TAG=dev-$(date +%Y%m%d%H%M%S)
WEB_IMAGE="$ACR_LOGIN_SERVER/aidinner:$WEB_IMAGE_TAG"

az acr build \
  --registry "$ACR_NAME" \
  --image "aidinner:$WEB_IMAGE_TAG" \
  --file Dockerfile \
  .
```

## モデル deployment を用意する

Hosted Agent は `AZURE_AI_MODEL_DEPLOYMENT_NAME` の値をモデル deployment 名として使う。

Hosted Agent version を作成する前に、Azure CLI または Foundry/Azure Portal のコンソールから
同名のモデル deployment を作成しておく。現在の Hosted Agent は `gpt-4.1-mini` を使っている。

## Hosted Agent image をビルドする

Hosted Agent は Web app とは別の image を使う。

```bash
AGENT_IMAGE_TAG=dev-$(date +%Y%m%d%H%M%S)
AGENT_IMAGE="$ACR_LOGIN_SERVER/aidinner-agent:$AGENT_IMAGE_TAG"

az acr build \
  --registry "$ACR_NAME" \
  --image "aidinner-agent:$AGENT_IMAGE_TAG" \
  --file Dockerfile.agent \
  .
```

## Hosted Agent version を作成する

現状は Hosted Agent version 作成を Bicep ではなく Python SDK で実行している。
既存 version の定義をもとに image だけ差し替えて、新しい version を作る。

Azure CLI の `az cognitiveservices account deployment` は model deployment を管理する
コマンドであり、Hosted Agent version は管理しない。現時点では `az` の標準コマンドに
Hosted Agent version を作成する専用コマンドがないため、`azure-ai-projects` SDK の
`client.agents.create_version` を使う。

Hosted Agent version は既存 version を直接更新するのではなく、新しい version として
作成する。ここでは稼働中の最新 version から environment variables、protocol、
blueprint reference などを引き継ぎ、container image だけを新しい tag に差し替える。

```bash
uv run python scripts/deploy_hosted_agent_version.py \
  --agent-image "$AGENT_IMAGE"
```

作成後、active になったことを確認する。

```bash
uv run python scripts/list_hosted_agent_versions.py
```

Hosted Agent instance identity の principal ID を取得する。`.env` の
`HOSTED_AGENT_PRINCIPAL_ID` と違う場合は `.env` を更新する。

```bash
HOSTED_AGENT_PRINCIPAL_ID="$(az ad sp list \
  --display-name "$FOUNDRY_ACCOUNT_NAME-$FOUNDRY_PROJECT_NAME-$FOUNDRY_AGENT_NAME-AgentIdentity" \
  --query "[0].id" \
  -o tsv)"

echo "$HOSTED_AGENT_PRINCIPAL_ID"
```

## Bicep を再デプロイして設定を反映する

Web image、Container App から Hosted Agent への接続設定、Hosted Agent identity の
Cosmos DB 読み取り権限を Bicep で反映する。

```bash
az deployment sub create \
  --name "$DEPLOYMENT_NAME" \
  --location "$LOCATION" \
  --template-file infra/main.bicep \
  --parameters \
    location="$LOCATION" \
    environmentName="$ENVIRONMENT_NAME" \
    namePrefix="$NAME_PREFIX" \
    containerImage="$WEB_IMAGE" \
    foundryAgentName="$FOUNDRY_AGENT_NAME" \
    hostedAgentPrincipalId="$HOSTED_AGENT_PRINCIPAL_ID"
```

## Cosmos DB にメニューデータを投入する

Cosmos DB はキー認証を無効化しているため、`DefaultAzureCredential` で実行する。
実行ユーザーには Cosmos DB data plane の書き込み権限が必要。

`data/menu.json` は Git 管理しない実データとして扱う。初回は example から作成し、
必要に応じて内容を編集する。

```bash
cp -n data/menu.example.json data/menu.json

uv run python scripts/seed_cosmos.py
```

## 動作確認

公開 URL を取得する。

```bash
APP_URL="https://$(az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --query properties.configuration.ingress.fqdn \
  -o tsv)"

echo "$APP_URL"
```

ヘルスチェックを実行する。

```bash
python3 - "$APP_URL" <<'PY'
import json
import sys
import urllib.request

base = sys.argv[1]
with urllib.request.urlopen(base + "/healthz", timeout=30) as response:
    print(json.loads(response.read().decode("utf-8")))
PY
```

チャット API を確認する。`source` が `foundry` なら Hosted Agent 経由で応答している。

```bash
python3 - "$APP_URL" <<'PY'
import json
import sys
import urllib.request

base = sys.argv[1]
request = urllib.request.Request(
    base + "/api/chat",
    data=json.dumps({"message": "クリームシチューの材料リストを返して。"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(request, timeout=180) as response:
    body = json.loads(response.read().decode("utf-8"))
    print(json.dumps(body, ensure_ascii=False, indent=2))
PY
```

## 開発時の再デプロイ

初回デプロイ後の開発では、変更した対象に応じて Container App か Hosted Agent を
再デプロイする。Web UI、FastAPI、設定読み込みなどを変えた場合は Container App、
Agent の指示文、tools、Agent 側のデータアクセスを変えた場合は Hosted Agent を更新する。

`.env` を読み込んでいないターミナルでは、先に次のコマンドを実行する。

```bash
set -a
. ./.env
set +a

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
```

### Container App を再デプロイする

`app/`、`static/`、`main.py`、`Dockerfile` など、Web app 側を変更した場合に実行する。
Hosted Agent version は作り直さない。

```bash
uv run ruff check .

WEB_IMAGE_TAG=dev-$(date +%Y%m%d%H%M%S)
WEB_IMAGE="$ACR_LOGIN_SERVER/aidinner:$WEB_IMAGE_TAG"

az acr build \
  --registry "$ACR_NAME" \
  --image "aidinner:$WEB_IMAGE_TAG" \
  --file Dockerfile \
  .

az containerapp update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --image "$WEB_IMAGE"
```

Container App の image 更新では、既存の `FOUNDRY_PROJECT_ENDPOINT`、`FOUNDRY_AGENT_NAME`、
Cosmos DB 関連の環境変数は保持される。Bicep の状態も更新しておきたい場合は、
`az containerapp update` の代わりに次のコマンドを実行する。

```bash
az deployment sub create \
  --name "$DEPLOYMENT_NAME" \
  --location "$LOCATION" \
  --template-file infra/main.bicep \
  --parameters \
    location="$LOCATION" \
    environmentName="$ENVIRONMENT_NAME" \
    namePrefix="$NAME_PREFIX" \
    containerImage="$WEB_IMAGE" \
    foundryAgentName="$FOUNDRY_AGENT_NAME" \
    hostedAgentPrincipalId="$HOSTED_AGENT_PRINCIPAL_ID"
```

### Hosted Agent を再デプロイする

`agent/`、`Dockerfile.agent`、Agent が使う tools や instructions を変更した場合に実行する。
Web Container App の image は作り直さない。

```bash
uv run ruff check .

AGENT_IMAGE_TAG=dev-$(date +%Y%m%d%H%M%S)
AGENT_IMAGE="$ACR_LOGIN_SERVER/aidinner-agent:$AGENT_IMAGE_TAG"

az acr build \
  --registry "$ACR_NAME" \
  --image "aidinner-agent:$AGENT_IMAGE_TAG" \
  --file Dockerfile.agent \
  .

uv run python scripts/deploy_hosted_agent_version.py \
  --agent-image "$AGENT_IMAGE"
```

作成後、最新 version が active になったことを確認する。

```bash
uv run python scripts/list_hosted_agent_versions.py
```

Hosted Agent の instance identity が変わった場合は、`HOSTED_AGENT_PRINCIPAL_ID` を取得し直して
`.env` を更新し、Bicep を再デプロイして RBAC を反映する。通常の version 更新では同じ
identity が使われるため、この作業は不要。

### 両方を再デプロイする

Web app と Hosted Agent の両方を変更した場合は、先に Container App image と Hosted Agent
image をそれぞれ build し、Hosted Agent version を作成してから、最後に Bicep を再デプロイする。

```bash
az deployment sub create \
  --name "$DEPLOYMENT_NAME" \
  --location "$LOCATION" \
  --template-file infra/main.bicep \
  --parameters \
    location="$LOCATION" \
    environmentName="$ENVIRONMENT_NAME" \
    namePrefix="$NAME_PREFIX" \
    containerImage="$WEB_IMAGE" \
    foundryAgentName="$FOUNDRY_AGENT_NAME" \
    hostedAgentPrincipalId="$HOSTED_AGENT_PRINCIPAL_ID"
```
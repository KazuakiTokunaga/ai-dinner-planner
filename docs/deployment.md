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
> 2回目で Container App image、Hosted Agent 接続設定、RBAC、Microsoft Entra ID 認証を
> 反映する。

## 全体の流れ

初回デプロイは次の順に進める。

1. `az deployment sub create` で Resource group、ACR、Container Apps、Cosmos DB、
   Foundry account/project を作成する。
2. ACR に Web image と Hosted Agent image を push し、Hosted Agent version を作成する。
3. 作成後に分かる image tag と Hosted Agent identity を使って、Container App の
    Microsoft Entra ID 認証と Bicep の再デプロイを実行する。

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

## Container App 認証を設定して Bicep を再デプロイする

Web image、Container App から Hosted Agent への接続設定、Hosted Agent identity の
Cosmos DB 読み取り権限、Container App の Microsoft Entra ID 認証を反映する。

認証は Azure Container Apps の組み込み認証を使う。`scripts/configure_container_app_auth.py` は
Entra アプリ、client secret、ユーザー割り当てを構成し、生成した client secret を表示せずに
Bicep の secure parameter として渡す。client secret は `.env` や Git 管理ファイルには保存しない。

`.env` に、アクセスを許可する Microsoft アカウントを設定する。初回は `AUTH_APP_ID`、
`AUTH_SP_OBJECT_ID`、`AUTH_TARGET_USER_ID` を空のままにできる。対象ユーザーが tenant に
存在しない場合は招待メールが送信されるため、承認後に同じコマンドを再実行する。

```bash
AUTH_APP_NAME="ai-dinner-planner-auth"
AUTH_APP_ID=""
AUTH_SP_OBJECT_ID=""
AUTH_TARGET_USER_EMAIL="<your_allowed_microsoft_account_email>"
AUTH_TARGET_USER_ID=""
AUTH_CLIENT_SECRET_NAME="microsoft-provider-authentication-secret"
```

認証設定と Bicep の再デプロイを実行する。

```bash
uv run python scripts/configure_container_app_auth.py \
  --deploy-bicep \
  --container-image "$WEB_IMAGE"
```

スクリプトの出力にある `AUTH_APP_ID`、`AUTH_SP_OBJECT_ID`、`AUTH_TARGET_USER_ID` を `.env` に
反映する。

現在の dev 環境のように、同等の role assignment が既に別名で存在する環境へ再適用する場合は、
Bicep の RBAC 作成だけを省略する。

```bash
uv run python scripts/configure_container_app_auth.py \
  --deploy-bicep \
  --container-image "$WEB_IMAGE" \
  --no-create-role-assignments
```

設定を確認する。

```bash
az containerapp auth show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --query "{enabled:platform.enabled,action:globalValidation.unauthenticatedClientAction,provider:globalValidation.redirectToProvider}"

az ad sp show \
  --id "$AUTH_APP_ID" \
  --query "{displayName:displayName,appRoleAssignmentRequired:appRoleAssignmentRequired}"

az rest \
  --method GET \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$AUTH_SP_OBJECT_ID/appRoleAssignedTo" \
  --query "value[].{principalDisplayName:principalDisplayName,principalType:principalType}"
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

認証を有効化した後は、ブラウザからアクセスすると Microsoft ログインにリダイレクトされる。
API クライアントのように `Accept: text/html` を送らないリクエストでは、未認証時に `401` が
返ることがある。

ログイン済みのブラウザでアプリを確認する。`/healthz` や `/api/chat` を Python などから
直接確認する場合は、認証済みリクエストとして実行する。

認証を無効化しているローカル環境や、認証済みリクエストを用意した環境では、次のように
ヘルスチェックを実行できる。

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

同じ条件でチャット API を確認する。`source` が `foundry` なら Hosted Agent 経由で応答している。

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

## ローカルで Agent の動作を確認する

Hosted Agent にデプロイする前に、`agent/server.py` のローカルチャットモードで Agent 実装を
確認できる。このモードはローカル CLI から `create_agent()` を実行するが、モデル呼び出しは
Foundry project に対して行う。

```bash
set -a
. ./.env
set +a

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
uv run python -m agent.server --local-chat
```

`exit` または `quit` を入力すると終了する。

`.env` の `USE_COSMOS="true"` のまま実行する場合は、実行ユーザーに Cosmos DB data plane の
読み取り権限が必要。ローカル JSON だけで確認する場合は、`data/menu.json` を用意してから
一時的に `USE_COSMOS=false` で実行する。

```bash
cp -n data/menu.example.json data/menu.json
USE_COSMOS=false uv run python -m agent.server --local-chat
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
Cosmos DB 関連の環境変数は保持される。Bicep の状態も更新しておきたい場合は、認証設定
スクリプトから Bicep を再デプロイする。

```bash
uv run python scripts/configure_container_app_auth.py \
  --deploy-bicep \
  --container-image "$WEB_IMAGE"
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
`.env` を更新し、認証設定スクリプトから Bicep を再デプロイして RBAC を反映する。通常の
version 更新では同じ identity が使われるため、この作業は不要。

### 両方を再デプロイする

Web app と Hosted Agent の両方を変更した場合は、先に Container App image と Hosted Agent
image をそれぞれ build し、Hosted Agent version を作成してから、最後に Bicep を再デプロイする。

```bash
uv run python scripts/configure_container_app_auth.py \
  --deploy-bicep \
  --container-image "$WEB_IMAGE"
```
---
title: 夕食献立サジェスト AI エージェント仕様
description: Microsoft Foundry hosted agent、Azure Container Apps、Cosmos DB を使う夕食献立サジェストアプリの MVP 仕様
ms.date: 2026-06-23
ms.topic: concept
---

## 目的

夕食の献立を提案する AI エージェントを作る。
エージェントは Microsoft Foundry の Hosted Agent としてデプロイする。

エージェントは、登録済みのメニューデータから献立を推薦し、ユーザーが選んだメニューの材料リストを提示する。ユーザーが新しい
メニューの検討を明示した場合に限り、Web 検索を使って候補を検討する。

## 採用する Azure 構成

アプリケーションの Azure インフラは Bicep で定義する。使用する Azure
サブスクリプション ID は決定済みで、リソースグループはこのアプリケーション
専用に新設する。

構成は次のとおり。

```text
Browser
  -> Azure Container Apps / FastAPI
    -> Microsoft Foundry Hosted Agent
      -> Cosmos DB for NoSQL
      -> Foundry Web Search tool
```

### Azure リソース

初期実装で想定する主なリソースは次のとおり。

* Resource group
* Azure Container Registry
* Azure Container Apps environment
* Azure Container App
* Log Analytics workspace
* Application Insights
* Azure Cosmos DB for NoSQL account
* Cosmos DB database
* Cosmos DB container for menu data
* Microsoft Foundry account/project
* Microsoft Foundry Hosted Agent
* Hosted Agent から Cosmos DB を読むための RBAC assignment

### Bicep の方針

Bicep はリソースグループ作成まで担当する。最上位の Bicep は
subscription scope とし、リソースグループを作成したうえでアプリ本体の
リソースを module としてデプロイする構成を想定する。

```text
infra/
  main.bicep              subscription scope。Resource group を作成する
  modules/
    app.bicep             resource group scope。主要 Azure リソースを作成する
```

Hosted Agent の Agent Identity は、Hosted Agent のデプロイ時に作成される
可能性がある。そのため、Cosmos DB の data plane RBAC assignment は
初回 Bicep デプロイとは分けて扱う。

初期実装では、次の順序で進める。

1. Bicep で Resource group、Container Apps、Cosmos DB、Foundry account/project
  などの基盤リソースを作成する。
2. Microsoft Agent Framework で実装した Hosted Agent をデプロイする。
3. Hosted Agent の Agent Identity の principal ID を取得する。
4. 取得した principal ID に対して、Cosmos DB の data plane RBAC として
  `Cosmos DB Built-in Data Reader` を割り当てる。

## アプリケーション構成

Web アプリは Azure Container Apps に 1 つの Container App としてデプロイする。
backend は Python の FastAPI、frontend はバニラ JavaScript とする。

初期実装では FastAPI が API と静的ファイルの両方を配信する。FastAPI は
作り込みすぎず、Microsoft Foundry の標準機能をなるべく使う。

```text
Container App
  -> FastAPI
    -> static frontend files
    -> chat API endpoint
    -> Foundry Hosted Agent endpoint call
```

FastAPI の責務は次に絞る。

* 静的 frontend ファイルを配信する。
* ブラウザからのチャット要求を受け取る。
* Foundry Hosted Agent にユーザー入力を渡す。
* Foundry の標準的な thread/session 機能を優先して使う。

## エージェント構成

エージェントは Microsoft Foundry の Prompt Agent ではなく、Hosted Agent を使う。
Hosted Agent は Microsoft Agent Framework を使って Python コードで実装する。
エージェントの LLM model は、Azure Foundry でデプロイする `gpt-5.4-mini` を使う。

Cosmos DB へのアクセスにはキーを使わず、Hosted Agent の Agent Identity を使う。
Cosmos DB 側では、Agent Identity に必要最小限の data plane RBAC を割り当てる。
初期実装 では読み取りが中心なので、原則として `Cosmos DB Built-in Data Reader` を使う。

エージェントには次のツールを提供する。

* `search_menus`
* `get_menus`
* `propose_new_menu_with_web_search`

### search_menus

Cosmos DB のメニューデータベースから候補メニューを検索する。

想定引数は次のとおり。

```text
main_ingredient: string | null
category: string | null
cook_with: string | null
max_items: integer
```

`max_items` には上限を設ける。MVP では 10 件程度を既定値にし、必要に応じて
20 件程度まで許可する。

このツールでは、AI が任意の Cosmos DB SQL を生成して実行しない。実装側で
許可した検索条件だけを受け取り、パラメーター化されたクエリまたは SDK の
安全な検索処理を使う。

### get_menus

1 件または複数件のメニュー ID を受け取り、メニュー詳細と材料リストを取得する。
ユーザーが複数日分の献立を決めた後に、まとめて材料リストを求めるケースに対応する。

想定引数は次のとおり。

```text
menu_ids: string[]
```

戻り値には、少なくとも次の項目を含める。

```text
id
name
cook_with
main_ingredient
category
ingredients
```

材料リストは、複数メニュー分をまとめて返せるようにする。MVP では専用の
計算ツールや厳密な単位変換は用意せず、プロンプトで制御して LLM 内部で
同名または近い材料をまとめさせる。単位が異なる可能性があるため、数量は
厳密に合算せず、必要に応じてメニュー別の内訳が分かる形で提示する。

### propose_new_menu_with_web_search

ユーザーが新しいメニューの検討を明示した場合だけ使う。Foundry の Web Search
tool または Bing grounding tool を Microsoft Agent Framework 経由で使う想定。

通常の献立推薦では Web 検索を使わない。既存メニューで候補が不足した場合も、
自動で Web 検索せず、ユーザーに新しいメニューを探すか確認する。

Web 検索を使った候補は、Cosmos DB に登録済みのメニューとは区別する。回答では
未登録メニューであることを明示する。

## エージェントの振る舞い

### 既存メニュー推薦

ユーザーが「鶏肉を使ったメニューを何かあげて」のように尋ねた場合、
エージェントは `search_menus` を使って Cosmos DB の登録済みメニューから候補を
検索する。

既定では Cosmos DB のメニューデータベースを信頼する。Web 検索は使わない。

### 複数日分の献立

ユーザーが複数日分の献立を求めた場合、エージェントは日数、避けたい食材、
調理器具、カテゴリの偏りなど、必要な条件を会話で確認してよい。

十分な条件がある場合は、`search_menus` で候補を取得し、できるだけ同じ
主材料やカテゴリに偏りすぎないように組み合わせる。

### 材料リスト提示

ユーザーが材料リストを求めた場合、エージェントは `get_menus` を使って
該当メニューの材料を取得し、必要な材料を提示する。

複数メニューが選ばれている場合は、複数 ID をまとめて `get_menus` に渡す。
MVP では、材料をメニュー別に表示してよい。

### 新しいメニューの検討

ユーザーが「新しいメニューを考えて」「登録されていない候補も探して」など、
新規メニュー検討を明示した場合に限り、Web 検索を使う。

Web 検索結果を使う場合、エージェントは登録済みメニューではないことを明示し、
必要に応じて根拠や参照情報を添える。

## メニューデータ

メニューデータは現在の `data/menu.json` の形式でスキーマを確定し、初期データ
として Cosmos DB for NoSQL に格納する。

基本スキーマは次のとおり。

```json
{
  "id": "beef-stew",
  "name": "ビーフシチュー",
  "cook_with": "ホットクック",
  "main_ingredient": "牛肉",
  "category": "カレー・シチュー",
  "is_high_effort": false,
  "uses_perishable_ingredients": false,
  "seasonality": "all_year",
  "ingredients": [
    {
      "name": "牛すね肉",
      "quantity": "250グラム"
    }
  ],
  "reference_url": "https://example.com/recipe",
  "image_filename": "beef-stew.jpg",
  "cooking_note": "任意の調理メモ"
}
```

### 必須フィールド

* `id`
* `name`
* `cook_with`
* `main_ingredient`
* `category`
* `is_high_effort`
* `uses_perishable_ingredients`
* `seasonality`
* `ingredients`

`ingredients` は材料オブジェクトの配列とし、各要素は次のフィールドを持つ。

* `name`
* `quantity`

### 任意フィールド

* `reference_url`
* `image_filename`
* `cooking_note`

### フィールドの扱い

`id` は機械的な連番ではなく、英語の kebab-case でメニュー内容が分かる名前にする。
`seasonality` は `all_year`、`summer`、`winter` を使う。
`is_high_effort` は休日向けなど手間のかかるメニューを示し、
`uses_perishable_ingredients` は保存しにくい食材を使うメニューを示す。

### Cosmos DB のパーティションキー

Cosmos DB のパーティションキーは、データの論理的な分割とクエリのルーティングに
使われる。単なる検索インデックスではないため、検索に使うフィールドをすべて
並べればよいわけではない。コンテナー作成後に変更しにくいので、初期実装で決める。

初期実装では、次の階層パーティションキーを使う。

* `/category`
* `/main_ingredient`
* `/id`

`category` と `main_ingredient` は献立検索でよく使うため、階層の上位に置く。
`id` は各メニューで一意なので、階層の最後に置いてデータの偏りを避ける。

この構成では、`category` を含む検索や、`category` と `main_ingredient` を
組み合わせた検索を効率よく絞り込める。`main_ingredient` だけで検索する場合は
階層の先頭ではないため、横断検索になる可能性がある。

## Web 検索ツール

Microsoft Agent Framework と Foundry の Web Search tool を使う想定。
公式ドキュメント上では、`FoundryChatClient` に Web Search tool を渡すサンプルが
ある。

実装時に確認する項目は次のとおり。

* 使用モデルが Foundry Web Search tool または Bing grounding tool をサポートするか
* 使用リージョンが該当ツールをサポートするか
* Foundry project connection が必要な場合、Bicep または手順書で作成できるか
* Web 検索利用時の引用や根拠表示を frontend でどう扱うか

参考ドキュメントは次のとおり。

* [Web search tool](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/web-search)
* [Grounding agents with Bing Search tools](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/bing-tools)
* [Microsoft Foundry provider for Agent Framework](https://learn.microsoft.com/agent-framework/agents/providers/microsoft-foundry)

## セッション管理

会話履歴と thread/session 管理は、なるべく Foundry の標準機能を使う。
FastAPI では独自の会話状態を持ちすぎない。

MVP では、ブラウザ側に session ID または thread ID 相当の識別子を保持し、
FastAPI はそれを Foundry への呼び出しに引き渡す程度に留める。

## MVP の範囲外

初期実装では、次の機能は範囲外とする。

* ユーザー認証
* 複数ユーザーのデータ分離
* 買い物リストの永続化
* 家庭内在庫管理
* 栄養計算
* 材料数量の高度な合算や単位変換
* メニューの Cosmos DB への新規登録 UI
* Private Endpoint や高度なネットワーク分離
* CDN や Azure Static Web Apps を使った frontend 分離

## 初期実装の採用値

初期実装では次の値を使う。

* Azure リージョンは `japaneast` を使う。
* 環境名は `dev` を使う。
* リソース名プレフィックスは `aidinner` を使う。
* Resource group 名は `rg-aidinner-dev-japaneast` とする。
* Cosmos DB database 名は `dinner-planner` とする。
* Cosmos DB container 名は `menus` とする。
* Cosmos DB container の階層パーティションキーは `/category`、`/main_ingredient`、`/id` とする。

リソース名にハイフンを使える場合は `aidinner-dev` を含める。Azure Container
Registry などハイフンを使えないリソースでは `aidinnerdev` を使う。グローバルに
一意な名前が必要なリソースは、Bicep 側で短い suffix を付けて衝突を避ける。

Azure サブスクリプション ID は、ローカルの `.env` に `AZURE_SUBSCRIPTION_ID` として
設定する。実値は Git 管理せず、`.env.exmaple` には次の形式を残す。

```text
AZURE_SUBSCRIPTION_ID=<your-subscription-id>
```

## 実装前の確認事項

最初のコーディング前に、次の項目を確認する。

1. Foundry Hosted Agent のデプロイ手順を確認する。
2. Agent Identity の principal ID を取得する方法を確認する。
3. Agent Identity に Cosmos DB data plane RBAC を付ける post-deploy 手順を確認する。
4. `japaneast` と使用モデルが Web Search tool をサポートするか確認する。
5. FastAPI から Foundry Hosted Agent を呼び出す最小 API 仕様を決める。

## 初期実装の推奨順序

1. 確定済みの `data/menu.json` を Cosmos DB の初期データとして投入する。
2. FastAPI で静的 frontend と最小チャット API を用意する。
3. Cosmos DB をローカルまたは開発環境から読み込む `search_menus` と
   `get_menus` を実装する。
4. Microsoft Agent Framework で Hosted Agent の最小実装を作る。
5. Cosmos DB 直接検索ツールを Hosted Agent に接続する。
6. Web Search tool を追加し、新規メニュー検討時だけ使うよう指示する。
7. Bicep で Resource group から Azure リソースまで作成する。
8. Container App と Hosted Agent をデプロイし、ブラウザから動作確認する。

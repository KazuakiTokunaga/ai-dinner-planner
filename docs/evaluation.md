---
title: 評価概要
description: Microsoft Foundry Hosted Agent の ground truth 評価データセットと binary LLM judge 評価の概要
ms.date: 2026-06-26
ms.topic: concept
---

## 目的

夕食献立サジェストエージェントの応答を、ground truth 付きデータセットと
LLM-as-a-judge で評価する。評価対象は Microsoft Foundry Hosted Agent として
デプロイ済みのエージェントであり、Foundry の cloud evaluation を使って実行する。

評価は次の 2 層で構成する。

* single-turn evaluation: 1 つのユーザー入力に対する応答を ground truth と比較する
* conversation simulation: LLM がユーザーを模して Hosted Agent と会話し、会話全体を評価する

single-turn は binary 判定とする。judge はユーザー入力、エージェント応答、ground truth を比較し、
期待される振る舞いを満たす場合は pass、満たさない場合は fail と理由を返す。conversation simulation は
Foundry の conversation-level built-in evaluator を使い、タスク完了、会話の一貫性、顧客満足度を評価する。

`--wait` 付きで conversation simulation を実行した場合は、追加で 2 段階目の custom
conversation 評価を実行する。1 段階目の simulation で生成された `messages` を取り出し、
それを 1 つの turn-level item として prompt-based custom evaluator に渡す。これにより、
Foundry の custom prompt evaluator が conversation-level evaluation に直接対応していない環境でも、
独自観点で会話全体を評価できる。

## 構成

評価関連のコードとデータは `agent/evaluation/` に置く。

| ファイル | 役割 |
|----------|------|
| `agent/evaluation/dataset.jsonl` | single-turn 評価用の `query` と `ground_truth` を持つ JSONL データセット |
| `agent/evaluation/simulation_scenarios.jsonl` | conversation simulation 用の scenario description を持つ JSONL データセット |
| `agent/evaluation/run_foundry_evaluation.py` | Dataset 登録、evaluator 登録、evaluation run 作成を行う CLI |

`dataset.jsonl` の各行は次のフィールドを持つ。

| フィールド | 内容 |
|------------|------|
| `id` | 評価項目を識別する ID |
| `query` | Hosted Agent に送るユーザー入力 |
| `ground_truth` | 期待される応答方針や制約 |

`simulation_scenarios.jsonl` の各行は、simulated user が達成しようとする状況を
`test_case_description` に持つ。`desired_num_turns` は期待する会話ターン数の目安になる。

## 評価の流れ

single-turn の評価 CLI は次の順に処理する。

1. 対象 JSONL を Foundry project の Dataset として登録する。
2. prompt-based custom evaluator を Foundry evaluator catalog に登録する。
3. OpenAI eval API で evaluation object を作成する。
4. `azure_ai_target_completions` data source で Hosted Agent に dataset の `query` を送る。
5. judge が `query`、`sample.output_text`、`ground_truth` を比較して binary 評価を返す。
6. `--wait` 指定時は run 完了まで polling し、項目ごとの pass/fail と理由を表示する。

conversation simulation は次の順に処理する。

1. `simulation_scenarios.jsonl` を Foundry project の Dataset として登録する。
2. `conversation_gen_preview` で scenario ごとの simulated conversation を生成する。
3. `extra_body={"evaluation_level": "conversation"}` を指定して conversation-level evaluation を実行する。
4. `builtin.task_completion`、`builtin.coherence`、`builtin.customer_satisfaction` で会話全体を評価する。

`--wait` を指定した conversation simulation では、続けて custom conversation 評価を実行する。

1. 完了した simulation run の output items から `datasource_item.messages` を取り出す。
2. 取り出した会話ログを inline JSONL の `file_content` として 2 段階目の eval run に渡す。
3. `ai_dinner_planner_conversation_binary_judge` custom evaluator が `messages` 全体を読む。
4. 登録済みメニューの優先、unsupported fact の抑制、Web 検索の使い方、ユーザー目的の達成を binary 評価する。

Dataset version がすでに存在する場合は再アップロードせず、既存 version を再利用する。

## Judge の判定基準

single-turn の custom evaluator は prompt-based evaluator として登録される。
judge prompt は次の観点で応答を確認する。

* エージェント応答が ground truth の期待動作を満たしている。
* 主要な要件を欠落させていない。
* ground truth と矛盾していない。
* ユーザーが新しいメニュー探索を明示していない場合、Web 検索で見つけた未登録メニューを提案していない。
* unsupported fact を作っていない。

judge は次の形式の JSON を返す。

```json
{
  "result": true,
  "reason": "brief explanation"
}
```

`result` は boolean metric として扱われる。`true` が pass、`false` が fail になる。

custom conversation evaluator も同じ JSON 形式を返す。評価対象は `query` と単一応答ではなく、
simulation で生成された `messages` 全体になる。Foundry の built-in conversation evaluator と違い、
この evaluator は turn-level custom evaluator として実行されるため、`evaluation_level="conversation"`
は指定しない。

## 実行方法

`.env` を読み込んだ状態で実行する。

```bash
set -a
. ./.env
set +a

uv run python -m agent.evaluation.run_foundry_evaluation --wait
```

特定の suite だけを実行する場合は `--suite` を指定する。

```bash
uv run python -m agent.evaluation.run_foundry_evaluation --suite single-turn --wait
uv run python -m agent.evaluation.run_foundry_evaluation --suite simulation --wait
```

主な設定値は環境変数または CLI オプションで指定できる。

| 環境変数 | CLI オプション | 内容 |
|----------|----------------|------|
| `FOUNDRY_PROJECT_ENDPOINT` | `--project-endpoint` | Foundry project endpoint |
| `FOUNDRY_AGENT_NAME` | `--agent-name` | 評価対象の Hosted Agent 名 |
| `FOUNDRY_AGENT_VERSION` | `--agent-version` | 評価対象の Hosted Agent version |
| `FOUNDRY_EVALUATION_MODEL` | `--judge-model` | judge に使う model deployment 名 |
| `FOUNDRY_EVAL_SINGLE_TURN_DATASET_NAME` | `--single-turn-dataset-name` | single-turn 用 Foundry Dataset 名 |
| `FOUNDRY_EVAL_SIMULATION_DATASET_NAME` | `--simulation-dataset-name` | simulation 用 Foundry Dataset 名 |
| `FOUNDRY_EVAL_DATASET_VERSION` | `--dataset-version` | Foundry Dataset version |
| `FOUNDRY_EVAL_EVALUATOR_NAME` | `--evaluator-name` | custom evaluator 名 |
| `FOUNDRY_EVAL_EVALUATOR_VERSION` | `--evaluator-version` | single-turn で使う custom evaluator version |
| `FOUNDRY_EVAL_CONVERSATION_EVALUATOR_NAME` | `--conversation-evaluator-name` | simulation 後段で使う custom conversation evaluator 名 |
| `FOUNDRY_EVAL_CONVERSATION_EVALUATOR_VERSION` | `--conversation-evaluator-version` | simulation 後段で使う custom conversation evaluator version |
| `FOUNDRY_AGENT_PROTOCOL` | `--protocol` | `responses` または `invocations` |

conversation simulation の生成数と最大ターン数は、`--simulation-num-conversations` と
`--simulation-max-turns` で調整できる。

`--evaluator-version` を指定すると、その custom evaluator version を固定して評価する。
指定しない場合は、既存の latest version を再利用する。指定した evaluator が存在しない場合だけ、
初回実行時に新しい version を作成する。

custom conversation evaluator も同じ方針で version を扱う。`--conversation-evaluator-version` を
指定しない場合は latest version を再利用し、存在しない場合だけ初回実行時に version 1 を作成する。

`ResponsesHostServer` で動く Hosted Agent を評価する場合は、既定値の `responses` を使う。

## 出力の見方

`--wait` を付けると、run 完了後に項目ごとの結果が表示される。

```text
item=chicken_candidates | label=fail | passed=False | reason=登録済みメニューから3つ挙げていますが、各候補がなぜ夕食候補としてよいかの短い説明がありません。
query=鶏肉を使った夕食候補を3つ提案してください。登録済みメニューから選んでください。
```

`label` と `passed` が binary 判定結果で、`reason` が judge の説明になる。
`report url` には Foundry の evaluation report への URL が表示される。

## 確認済みの実行結果

2026-06-26 時点で、Foundry cloud evaluation が `completed` まで到達することを確認している。

確認時の主な結果は次のとおり。

| suite | dataset | run | 結果 |
|-------|---------|-----|------|
| single-turn | `ai-dinner-planner-eval` version `1` | `evalrun_3cc086fb51fa4d5c991c211e7a35ca0d` | 3 件中 2 件 pass、1 件 fail |
| simulation | `ai-dinner-planner-simulation-eval` version `1` | `evalrun_ae586718463c4778aa5b9d9d2c03c4c4` | 3 conversations を生成し、task completion で 1 件 fail |

2 段階 custom conversation 評価は、2026-06-26 に次の run で `completed` まで到達することを確認している。

| suite | run | 結果 |
|-------|-----|------|
| simulation | `evalrun_176e52ca5ce04798a344d4c5ebcb7e2e` | 3 conversations を生成し、task completion で 2 件 fail |
| simulation-custom | `evalrun_2848d3b8495b4695ae2c351e1044126a` | 生成済み `messages` 3 件を custom prompt evaluator で評価し、3 件 pass |

fail になった項目は、エージェント実装や ground truth の期待値を見直すための改善候補として扱う。
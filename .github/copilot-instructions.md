### 注意事項

- 日本語で記述すること

### 開発環境

- フロントエンドなどを除き、pythonで開発を行う
- pythonでは、uvを用いて環境を管理する
    - パッケージの追加： uv add
    - スクリプト実行: uv run 
- 一時的なスクリプトであっても仮想環境で実行すること
- 関数には型をつける
    - `from typing import List` ではなく `list` を使う
    - `Union[str, int]` ではなく `str | int` を使う
- linter, formatterとしてRuffを使用する

### ドキュメント

- docs/配下にドキュメントを配置しているため、適宜参照すること
# コードレビュー報告書

作成日: 2026-04-28

対象:
- `backend`
- `frontend`
- `agent-rs`
- `supervisor`

レビュー方針:
- リポジトリ全体を横断して、認証、データ整合性、WebSocket/並行性、運用ログ、テスト健全性を重点確認
- 重大度は `High` / `Medium` / `Low` で記載
- 指摘は「実害が出る可能性があるもの」を優先

## 指摘事項

### 1. `logout` が refresh token を失効させていない

- 重大度: `High`
- 該当箇所:
  - `backend/app/api/v1/endpoints/auth/jwt.py:97`
  - `backend/app/api/v1/endpoints/auth/_shared.py:45`

現状の `/auth/logout` は cookie を消すだけで、Redis 上の refresh token JTI を失効させていません。`/auth/refresh` は JTI が Redis に残っている限り再発行できる設計なので、cookie 値が別経路で漏れていた場合、ログアウト後でも refresh が成立します。

影響:
- 「ログアウトしたのでセッションは無効化された」という期待が満たされない
- 盗まれた refresh token が有効期限まで再利用可能

補足:
- `refresh` 側は `delete(refresh_jti:{jti})` による one-time use を前提にしているため、`logout` でも同じ失効処理を呼ぶべきです

### 2. タスク削除時に `blocks` 側の参照が残り、依存関係グラフが壊れる

- 重大度: `High`
- 該当箇所:
  - `backend/app/api/v1/endpoints/tasks/crud.py:268`
  - `backend/app/services/task_links.py:331`
  - `backend/app/services/task_links.py:345`

`delete_task()` は、削除対象タスクを `blocked_by` に持つタスクだけを `cleanup_dependents()` で掃除しています。これは「削除対象が他タスクをブロックしている」方向しか見ていません。

一方で、他タスクの `blocks` に削除対象が入っているケース、つまり「削除対象が何かにブロックされている」方向の参照は消していません。そのため、削除後も生存タスクの `blocks` に削除済みタスク ID が残ります。

影響:
- 依存関係の双方向整合性が崩れる
- UI/API で `blocks` と `blocked_by` の表示が食い違う
- 後続の cycle 判定や集計が「削除済みノードを含んだ壊れたグラフ」を前提に動く

### 3. Web Terminal の受信内容が常時ブラウザコンソールへ出力される

- 重大度: `High`
- 該当箇所:
  - `frontend/src/components/workspace/TerminalView.tsx:198`
  - `frontend/src/components/workspace/TerminalView.tsx:309`
  - `frontend/src/components/workspace/TerminalView.tsx:390`

`debugEnabled()` が無効でも、`terminal_output` の受信ごとに `console.info` が実行されます。特に `ws.recv` ログは `raw.slice(0, 100)` をそのまま出しており、ターミナル出力の先頭 100 文字が常時ブラウザコンソールへ残ります。

影響:
- コマンド出力、プロンプト、秘密情報がブラウザ開発者ツール経由で露出する
- 高頻度出力のセッションでフロントエンド性能を悪化させる

補足:
- これは診断ログとしては有用ですが、少なくとも `debugEnabled()` の配下へ戻すべきです

### 4. `terminal_router` が送信失敗した WebSocket を掃除しない

- 重大度: `Medium`
- 該当箇所:
  - `backend/app/services/terminal_router.py:95`
  - `backend/app/services/terminal_router.py:125`

fan-out 化後の `dispatch()` は、各 WebSocket への `send_text()` 失敗時に例外を記録するだけで、その WebSocket をセッションバケットから除去していません。切断済み WS が一度混ざると、以後そのセッションへの全 dispatch で毎回失敗し続けます。

影響:
- stale WS の蓄積による継続的な例外ログ増加
- PTY 出力のたびに不要な送信試行が走る
- multi-mount / reconnect が多い画面ほど無駄な fan-out コストが増える

### 5. PTY の診断ログがホットパスで `info!` になっている

- 重大度: `Medium`
- 該当箇所:
  - `agent-rs/src/pty/mod.rs:270`
  - `agent-rs/src/pty/mod.rs:330`
  - `agent-rs/src/pty/mod.rs:408`

PTY reader は出力チャンクごとに `info!`、入力転送も書き込みごとに `info!` を出します。対話型ターミナルではキー入力や echo 単位でログが発生するため、ほぼ無制限の高頻度ログになります。

影響:
- 本番ログ量の急増
- I/O ホットパスの余分な負荷
- 問題調査が終わった後も運用コストだけが残る

補足:
- コミットメッセージ上は一時的な診断用途ですが、現状のコードでは常時有効です

## 参考事項

### テスト実行結果

- `backend\\.venv\\Scripts\\python -m pytest tests/unit/test_terminal_router.py -q`
  - 結果: `5 passed, 1 skipped`
- `cargo test -p mcp-workspace-agent-rs pty -- --nocapture`
  - 結果: `pty::tests::scrollback_max_default_when_unset` が失敗

### テスト失敗について

Rust 側の失敗は今回のレビュー対象修正そのものというより、環境変数の後始末またはテスト独立性の問題に見えます。ただし、少なくとも PTY 周辺のテスト群が常にクリーンに通る状態ではないため、今後の変更検知力は下がっています。

## 総評

端末セッション周りの race 対応や Rust/Python 間の整合には良い改善が入っていますが、診断ログが強すぎる状態で残っており、情報露出と運用コストの両面でリスクがあります。また、認証の logout とタスク依存関係の削除処理には、設計上の期待と実装のズレが残っています。

優先度としては、まず以下の順での修正を推奨します。

1. `logout` 時の refresh token 失効
2. タスク削除時の `blocks` / `blocked_by` 双方向クリーンアップ
3. `TerminalView` の常時 `console.info` 撤去または debug gate 復帰
4. `terminal_router` の送信失敗 WS 自動掃除
5. PTY 診断ログの `debug` 降格または feature flag 化

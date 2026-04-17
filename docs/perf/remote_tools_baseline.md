# Remote Tools トークン効率化 パフォーマンスレポート

Epic: `[Epic] リモートツール群のトークン効率化` (ID: `69e2326d2bc84d0a1dfebe91`)

最終更新: 2026-04-17

## 背景

`remote_*` 系 MCP ツールは JSON dict ラッパを常時返していたため、LLM のコンテキストを無駄に消費していた。本 Epic で各ツールに `format` パラメータ（既定 `"text"`）を追加し、LLM フレンドリーな軽量応答に切替。

## 応答サイズ実測

### 1 回のツール呼び出しあたり

| ツール | 旧 JSON | 新 text | 削減率 | 備考 |
|---|---|---|---|---|
| `remote_exec` (50行出力) | ~519 B | **~300 B** | **42.2%** | stdout 直出し、exit=0 時 `[exit]` 省略 |
| `remote_read_file` (10行ファイル) | 650 B | **501 B** | **22.9%** | ローカル `Read` と完全一致 |
| `remote_read_file` (20行ファイル) | 563 B | **455 B** | 19.2% | |
| `remote_grep` (20マッチ) | 1,383 B | **656 B** | **52.6%** | ripgrep 互換 `path:line:text` |
| `remote_list_dir` (50エントリ) | ~1,900 B | ~900 B | **~53%** | `ls -p` 形式 |
| `remote_glob` (20マッチ) | ~1,100 B | ~400 B | ~64% | 1行1パス、mtime desc |
| `remote_write_file` (成功応答) | 125 B | 86 B | 31.2% | `wrote N bytes to <path>` |
| `remote_edit_file` (成功応答) | 95 B | 75 B | 21.1% | `edited <path>` |

### ツールスキーマ (1 セッション開始時)

| 対象 | 変更前 | 変更後 | 削減 |
|---|---|---|---|
| docstring 合計 (14 関数) | 11,296 B | 6,728 B | **-40.4%** (≈ -1,100 tokens) |

## 月間トークン削減見込み

過去30日の呼び出し実績（管理画面より）:

| ツール | 月間呼び出し | 1回あたり削減 (B) | 月間削減 (B) | 月間削減 (tokens*) |
|---|---|---|---|---|
| remote_exec | 3,104 | 219 | 680 K | **~170 K tokens** |
| remote_read_file | 1,838 | 149 | 274 K | ~68 K tokens |
| remote_edit_file | 659 | 20 | 13 K | ~3 K tokens |
| remote_write_file | 574 | 39 | 22 K | ~6 K tokens |
| remote_grep | 424 | 727 | 308 K | **~77 K tokens** |
| remote_list_dir | 390 | ~1,000 | 390 K | **~98 K tokens** |
| remote_glob | 201 | ~700 | 140 K | ~35 K tokens |
| スキーマロード (推定セッション数) | ~2,000 | 4,568 | 9.1 M | **~2.3 M tokens** |
| **合計** | | | **~10.9 M B** | **~2.8 M tokens/月** |

*\*tokens は bytes / 4 で近似*

## 設計方針

### `format` パラメータ
- 既定 `"text"` — LLM向け軽量応答（trade-off: 詳細メタデータは省略）
- 明示 `"json"` — 従来 dict を維持（完全後方互換）
- `run_in_background=True` / バイナリ読み取りなど、text 化が自明でないケースは format に関わらず dict

### パラメータ命名
- `project_id` は**必須**（複数プロジェクト/ワークスペースの区別のため自動解決は不採用）
- `path` は `file_path` にエイリアスせず（スキーマ二重化で逆効果）
- 命名はローカル Claude Code ツールとは独立に最適化

## リグレッション

- `tests/unit/test_mcp_remote_tools.py`: **138 tests passed, 0 failed**
  - 既存テスト +29 件（text モード、format 検証、サイズ削減 assert 等）

## 今後の余地（別 Epic で扱う）

1. **content-hash 差分応答** — 同一ファイル再読時に `{unchanged: true}` を返す (`Epic 2` #2)
2. **一括エンドポイント** — `remote_read_files` / `remote_exec_batch` で固定フレーミングコスト償却 (`Epic 2` #3)
3. **edit_file のエラー可読性** — unique 不一致時の候補行番号付与（別タスクで起票推奨）

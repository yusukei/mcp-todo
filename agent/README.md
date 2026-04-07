# MCP Todo — Remote Terminal Agent

リモートマシンで動作し、MCP Todo backend と WebSocket で接続して
リモートコマンド実行・ファイル操作・grep などを担当する常駐エージェントです。

## 起動

```bash
python main.py --url wss://todo.example.com/api/v1/terminal/agent/ws --token ta_xxx
# または
python main.py --config ~/.mcp-terminal/config.json
```

ビルド済みバイナリは `build.bat` / `build.sh` で生成できます。

## 必須: ripgrep のインストール

`remote_grep` ツールは **ripgrep (`rg`) を必須** とします。エージェント起動時に
検出され、見つからない場合は ERROR ログを出力し、`remote_grep` リクエストは
全てエラーを返します（grep 以外のハンドラには影響しません）。

> Python フォールバックは Phase 2 で削除されました。実用的な速度が出ないためです。

| OS | コマンド |
|----|----------|
| macOS | `brew install ripgrep` |
| Ubuntu / Debian | `sudo apt install ripgrep` |
| Fedora / RHEL | `sudo dnf install ripgrep` |
| Arch | `sudo pacman -S ripgrep` |
| Windows (winget) | `winget install BurntSushi.ripgrep.MSVC` |
| Windows (scoop) | `scoop install ripgrep` |
| Windows (chocolatey) | `choco install ripgrep` |

`rg` が PATH に存在するかは以下で確認できます:

```bash
rg --version
```

エージェント起動時に検出結果がログに出力されます:

```
[INFO] ripgrep detected at /usr/local/bin/rg — remote_grep ready
```

または rg が見つからない場合:

```
[ERROR] ripgrep (rg) NOT FOUND on PATH — remote_grep will return errors. Install it ...
```

### gitignore の扱い

`remote_grep` のデフォルトは `respect_gitignore=False`：
- `.gitignore` を **尊重しない**（あらゆるファイルが検索対象）
- ただし `.git`, `.venv`, `node_modules`, `dist`, `build`, `.next`, `target`,
  `__pycache__` などの重い / vendored ディレクトリは内部で `-g '!<dir>'`
  により自動的に除外されます

`respect_gitignore=True` を渡すと、ripgrep が `.gitignore` を読み取って
それに従います（その場合は内部の skip-dir 除外は付与されません）。

## テスト

```bash
python -m pytest test_remote_handlers.py -q
```

ripgrep がインストールされていない環境では `TestGrepWithRipgrep`
（実 rg バイナリを使う end-to-end テスト）は自動スキップされますが、
mock ベースのコマンドライン検証 / エラー伝播テスト
（`TestGrepRgCommandLine`, `TestGrepRgErrorSurfacing`,
`TestGrepRequiresRipgrep`, `TestGrepValidation`）は常に実行されます。

#!/usr/bin/env bash
# ============================================================
#  MCP Todo Remote Terminal Agent — Unix build script
#
#  Builds a self-contained mcp-terminal-agent binary via
#  PyInstaller. Output: dist/mcp-terminal-agent
#
#  Prerequisites: uv (https://docs.astral.sh/uv/) on PATH
#
#  Usage:
#    ./build.sh           - normal build
#    ./build.sh --clean   - wipe build/ + dist/ first
# ============================================================
set -euo pipefail

# Always run from this script's directory.
cd "$(dirname "$0")"

if [[ "${1:-}" == "--clean" ]]; then
    echo "[build] Cleaning build artifacts..."
    rm -rf build dist
fi

echo "[build] Syncing dependencies (including dev tools)..."
uv sync --quiet

echo "[build] Running PyInstaller..."
uv run pyinstaller mcp-terminal-agent.spec --noconfirm --clean

if [[ -f dist/mcp-terminal-agent ]]; then
    size=$(stat -c%s dist/mcp-terminal-agent 2>/dev/null || stat -f%z dist/mcp-terminal-agent)
    echo
    echo "[build] Success: dist/mcp-terminal-agent"
    echo "[build] Size: ${size} bytes"
    echo
    echo "Run with:"
    echo "  ./dist/mcp-terminal-agent --url wss://your-server/api/v1/terminal/agent/ws --token ta_xxx"
    echo "or:"
    echo "  ./dist/mcp-terminal-agent --config ~/.mcp-terminal/config.json"
else
    echo "[build] Build finished but output executable not found." >&2
    exit 1
fi

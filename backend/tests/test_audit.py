"""pip-audit による依存パッケージの脆弱性チェック"""

import subprocess
import sys


# Fix バージョンが未公開の脆弱性を一時的に除外
# 新しい fix が出たら除外リストから削除すること
_IGNORED_VULNS = [
    "CVE-2025-69872",  # diskcache — fix version未公開
    "CVE-2026-4539",   # pygments — fix version未公開
]


def test_no_known_vulnerabilities():
    """Ensure no dependencies have known security vulnerabilities."""
    cmd = [sys.executable, "-m", "pip_audit", "--strict", "--progress-spinner=off"]
    for vuln_id in _IGNORED_VULNS:
        cmd.extend(["--ignore-vuln", vuln_id])

    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"pip-audit found vulnerabilities:\n{result.stderr or result.stdout}"
    )

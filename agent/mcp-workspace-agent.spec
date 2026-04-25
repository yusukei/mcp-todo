# -*- mode: python ; coding: utf-8 -*-

import sys

from PyInstaller.utils.hooks import collect_all

# Web Terminal feature uses pywinpty on Windows. The import lives inside
# PtySession._spawn_windows (deferred), so PyInstaller's static analysis
# misses the package entirely. ``collect_all`` is the catch-all helper
# that gathers .py modules, .pyd C extensions (notably ``winpty.winpty``),
# the bundled winpty.dll and OpenConsole.exe, and data files in one
# pass — using it here avoids the ``No module named 'winpty.winpty'``
# runtime error that ``collect_submodules + collect_dynamic_libs`` alone
# leaves behind, because ``collect_submodules`` does not pick up .pyd
# extension modules.
_winpty_binaries = []
_winpty_datas = []
_winpty_hidden = []
if sys.platform == "win32":
    _winpty_datas, _winpty_binaries, _winpty_hidden = collect_all("winpty")


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_winpty_binaries,
    datas=_winpty_datas,
    hiddenimports=_winpty_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='mcp-workspace-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

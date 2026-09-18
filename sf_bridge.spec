# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('mutagen')


a = Analysis(
    ['C:\\Users\\samter96\\Desktop\\SoundField\\src-tauri\\python\\sf_bridge.py'],
    pathex=['C:\\Users\\samter96\\Desktop\\SoundField\\py', 'C:\\Users\\samter96\\Desktop\\SoundField\\src-tauri\\python'],
    binaries=[('C:\\Users\\samter96\\Desktop\\SoundField\\py\\sidecar\\scsearch-monitor-fixed.exe', 'monitor')],
    datas=[('C:\\Users\\samter96\\Desktop\\SoundField\\py\\app\\data\\ucs_thesaurus.json', 'app\\data')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['app.similarity_schema', 'app.similarity', 'app.similarity_indexer', 'app.similarity_search'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='sf_bridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='C:\\Users\\samter96\\Desktop\\SoundField\\bridge_version.txt',
    icon=['C:\\Users\\samter96\\Desktop\\SoundField\\src-tauri\\icons\\icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='sf_bridge',
)

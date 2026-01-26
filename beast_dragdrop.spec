# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['beast_app_dragdrop.py'],
    pathex=[],
    binaries=[
        ('/opt/homebrew/bin/ffprobe', 'ffmpeg'),
    ],
    datas=[
        ('src', 'src'),
    ],
    hiddenimports=[
        'src.exact_report_generator',
        'src.technical_info_extractor',
        'src.csv_importer',
        'src.pdf_extractor',
        'src.conclusion_generator',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Beast Auto Reporter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Beast Auto Reporter',
)

app = BUNDLE(
    coll,
    name='Beast Auto Reporter.app',
    icon='app_icon.icns',
    bundle_identifier='com.beastautoreporter.dragdrop',
    info_plist={
        'CFBundleName': 'Beast Auto Reporter',
        'CFBundleDisplayName': 'Beast Auto Reporter',
        'CFBundleVersion': '5.13.0',
        'CFBundleShortVersionString': '5.13.0',
        'CFBundleIdentifier': 'com.beastautoreporter.dragdrop',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13.0',
    },
)

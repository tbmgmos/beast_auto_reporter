# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['beast_app_final.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src/*.py', 'src'),
    ],
    hiddenimports=[
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'docx',
        'docx.shared',
        'docx.enum.text',
        'docx.enum.table',
        'docx.oxml',
        'PyPDF2',
        'fitz',
        'PIL',
        'PIL.Image',
        'ollama',
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
    bundle_identifier='com.beast.autoreporter',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
        'CFBundleName': 'Beast Auto Reporter',
        'CFBundleDisplayName': 'Beast Auto Reporter',
        'CFBundleVersion': '5.2.0',
        'CFBundleShortVersionString': '5.2',
        'NSHumanReadableCopyright': 'Copyright © 2026',
        'CFBundleIconFile': 'app_icon.icns',
    },
)

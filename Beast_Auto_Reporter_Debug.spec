# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Beast Auto Reporter — Apple Silicon (arm64) standalone .app
"""

import os
import sys
import shutil

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

ROOT = os.path.abspath('.')

# ── ffmpeg / ffprobe binaries ─────────────────────────────────────────────
ffmpeg_bin = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
ffprobe_bin = shutil.which('ffprobe') or '/opt/homebrew/bin/ffprobe'

ffmpeg_binaries = []
if os.path.exists(ffmpeg_bin):
    ffmpeg_binaries.append((ffmpeg_bin, 'ffmpeg'))
if os.path.exists(ffprobe_bin):
    ffmpeg_binaries.append((ffprobe_bin, 'ffmpeg'))

# ── libsndfile (required by soundfile) ────────────────────────────────────
# The arm64 dylib identifies itself as @rpath/libsndfile.1.dylib but the file
# is named libsndfile_arm64.dylib.  We copy it to a temp location with the
# canonical name so the loader can find it at runtime.
import soundfile
sf_dir = os.path.dirname(soundfile.__file__)
sndfile_dylib = os.path.join(sf_dir, '_soundfile_data', 'libsndfile_arm64.dylib')
if not os.path.exists(sndfile_dylib):
    sndfile_dylib = os.path.join(sf_dir, '_soundfile_data', 'libsndfile.dylib')

sndfile_binaries = []
if os.path.exists(sndfile_dylib):
    # Bundle the original file
    sndfile_binaries.append((sndfile_dylib, '_soundfile_data'))
    # Also create a copy named libsndfile.1.dylib so @rpath resolves
    canonical = os.path.join('/tmp', 'libsndfile.1.dylib')
    shutil.copy2(sndfile_dylib, canonical)
    sndfile_binaries.append((canonical, '_soundfile_data'))

# ── python-docx template (required for Document() to work) ────────────────
import docx
docx_templates_dir = os.path.join(os.path.dirname(docx.__file__), 'templates')

# ── Data files ────────────────────────────────────────────────────────────
datas = [
    (os.path.join(ROOT, 'src'), 'src'),
    (os.path.join(ROOT, 'templates'), 'templates'),
    (os.path.join(ROOT, 'config'), 'config'),
    (os.path.join(ROOT, 'app_icon_new.png'), '.'),
    (docx_templates_dir, os.path.join('docx', 'templates')),
]
# Словари орфографии (RU морфология pymorphy3 + EN частотный словарь)
datas += collect_data_files('pymorphy3_dicts_ru')
datas += collect_data_files('spellchecker')

# ── Hidden imports ────────────────────────────────────────────────────────
hidden_imports = [
    # PyQt5
    'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets', 'PyQt5.sip',
    # Audio
    'pyloudnorm', 'soundfile', 'scipy', 'scipy.signal', 'scipy.fft',
    'numpy', 'librosa', 'audioread', 'pydub',
    # PDF
    'fitz', 'pymupdf',
    # Reports
    'docx', 'docx.shared', 'docx.enum.text', 'docx.oxml',
    'reportlab', 'reportlab.lib.pagesizes', 'reportlab.platypus',
    'pandas', 'matplotlib', 'matplotlib.backends.backend_agg', 'seaborn',
    'PIL', 'PIL.Image',
    # Misc
    'yaml', 'pyyaml', 'csv', 'json', 'tqdm',
    # Spellcheck (маркер-листы, RU/EN)
    'spellchecker', 'pymorphy3', 'pymorphy3.analyzer', 'pymorphy3_dicts_ru', 'dawg_python',
    # src modules
    'src.exact_report_generator', 'src.technical_info_extractor',
    'src.csv_importer', 'src.pdf_extractor', 'src.conclusion_generator',
    'src.parameter_exporter', 'src.audio_metrics', 'src.audio_analyzer',
    'src.defect_detector', 'src.report_generator', 'src.full_report_generator',
    'src.template_report_generator', 'src.table_parser',
    'src.marker_list_from_template', 'src.template_processor',
    'src.diagnostics', 'src.youlean_integration',
    'src.audio_analyzer_extended', 'src.spellcheck_service', 'src.secret_store',
]

a = Analysis(
    ['beast_auto_reporter (v2 beta).py'],
    pathex=[ROOT],
    binaries=ffmpeg_binaries + sndfile_binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(ROOT, 'scripts', 'rthook_errorlog.py')],
    excludes=[
        'streamlit', 'streamlit_option_menu',
        'langchain', 'langchain_community', 'langchain_core',
        'ollama', 'langsmith',
        'tkinter', '_tkinter',
        'IPython', 'notebook', 'jupyter',
        'pytest', 'black', 'flake8',
    ],
    noarchive=False,
    cipher=block_cipher,
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
    upx=False,
    console=True,
    target_arch='arm64',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Beast Auto Reporter',
)

app = BUNDLE(
    coll,
    name='Beast Auto Reporter DEBUG.app',
    icon='app_icon.icns',
    bundle_identifier='com.beast.autoreporter',
    info_plist={
        'CFBundleName': 'Beast Auto Reporter',
        'CFBundleDisplayName': 'Beast Auto Reporter',
        'CFBundleShortVersionString': '2.0.0',
        'CFBundleVersion': '2.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'NSRequiresAquaSystemAppearance': False,
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Audio File',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['public.audio'],
            },
            {
                'CFBundleTypeName': 'PDF Document',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['com.adobe.pdf'],
            },
        ],
    },
)

"""
Setup script для создания macOS .app bundle
Использует py2app
"""

from setuptools import setup
import os

APP = ['launcher.py']
DATA_FILES = [
    ('', ['app.py', 'requirements.txt', 'README.md']),
    ('config', ['config/settings.yaml']),
    ('src', [
        'src/__init__.py',
        'src/audio_analyzer.py',
        'src/defect_detector.py',
        'src/llm_integration.py',
        'src/report_generator.py'
    ]),
    ('templates', ['templates/prompts.yaml']),
]

OPTIONS = {
    'argv_emulation': False,
    'packages': [
        'streamlit',
        'pandas',
        'numpy',
        'scipy',
        'librosa',
        'pyloudnorm',
        'soundfile',
        'matplotlib',
        'reportlab',
        'docx',
        'ollama',
        'yaml',
        'pydub',
        'PIL',
        'seaborn',
    ],
    'includes': [
        'streamlit',
        'click',
        'altair',
        'tornado',
    ],
    'excludes': [
        'tkinter',
        'PyQt5',
        'PyQt6',
    ],
    'iconfile': 'icon.icns' if os.path.exists('icon.icns') else None,
    'plist': {
        'CFBundleName': 'Beast Auto Reporter',
        'CFBundleDisplayName': 'Beast Auto Reporter',
        'CFBundleGetInfoString': 'Автоматическая система генерации отчетов о качестве аудио',
        'CFBundleIdentifier': 'com.beast.autoreporter',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHumanReadableCopyright': '© 2026 Beast Audio QC',
        'LSMinimumSystemVersion': '10.13.0',
        'NSHighResolutionCapable': True,
        'LSApplicationCategoryType': 'public.app-category.productivity',
        'NSRequiresAquaSystemAppearance': False,
    },
}

setup(
    name='Beast Auto Reporter',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
    install_requires=[
        'streamlit>=1.29.0',
        'pandas>=2.1.4',
        'numpy>=1.26.2',
    ],
)


#!/bin/bash
# Скрипт для создания macOS .app bundle для Beast Auto Reporter

echo "🍎 Creating Beast Auto Reporter.app..."
echo "========================================"

# Определяем пути
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
APP_NAME="Beast Auto Reporter"
APP_DIR="$SCRIPT_DIR/dist/${APP_NAME}.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

# Создаем структуру .app
echo "📁 Creating app structure..."
rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

# Копируем все файлы приложения
echo "📋 Copying application files..."
cp -r "$SCRIPT_DIR/app.py" "$RESOURCES_DIR/"
cp -r "$SCRIPT_DIR/src" "$RESOURCES_DIR/"
cp -r "$SCRIPT_DIR/config" "$RESOURCES_DIR/"
cp -r "$SCRIPT_DIR/templates" "$RESOURCES_DIR/"
cp -r "$SCRIPT_DIR/requirements.txt" "$RESOURCES_DIR/"
cp -r "$SCRIPT_DIR/README.md" "$RESOURCES_DIR/"

# Копируем venv (если существует)
if [ -d "$SCRIPT_DIR/venv" ]; then
    echo "📦 Copying virtual environment..."
    cp -r "$SCRIPT_DIR/venv" "$RESOURCES_DIR/"
fi

# Создаем launcher скрипт
echo "🚀 Creating launcher..."
cat > "$MACOS_DIR/launcher" << 'LAUNCHER_EOF'
#!/bin/bash

# Определяем пути
APP_DIR="$(dirname "$0")/.."
RESOURCES_DIR="$APP_DIR/Resources"

cd "$RESOURCES_DIR"

# Активируем venv если существует
if [ -d "$RESOURCES_DIR/venv" ]; then
    source "$RESOURCES_DIR/venv/bin/activate"
else
    echo "⚠️  Virtual environment not found. Using system Python."
fi

# Запускаем Python launcher
python3 << 'PYTHON_EOF'
import sys
import os
import subprocess
import time
import webbrowser
from pathlib import Path

# Путь к ресурсам
resources_dir = os.environ.get('RESOURCES_DIR', os.getcwd())
os.chdir(resources_dir)

print("🎵 Beast Auto Reporter")
print("=" * 50)

# Проверка Ollama
def check_ollama():
    try:
        result = subprocess.run(
            ['curl', '-s', 'http://localhost:11434/api/tags'],
            capture_output=True,
            timeout=2
        )
        return result.returncode == 0
    except:
        return False

# Запуск Ollama
def start_ollama():
    try:
        result = subprocess.run(['which', 'ollama'], capture_output=True)
        if result.returncode == 0:
            print("🤖 Starting Ollama...")
            subprocess.Popen(
                ['ollama', 'serve'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(3)
            return True
    except:
        pass
    return False

# Проверяем Ollama
if check_ollama():
    print("✓ Ollama доступна")
else:
    print("⚠️  Ollama не запущена")
    if start_ollama():
        if check_ollama():
            print("✓ Ollama запущена")
    else:
        print("⚠️  Работа без AI заключений")

print("\n🚀 Запуск приложения...")

# Запускаем Streamlit
port = 8501

try:
    env = os.environ.copy()
    env['STREAMLIT_SERVER_HEADLESS'] = 'true'
    env['STREAMLIT_BROWSER_GATHER_USAGE_STATS'] = 'false'
    
    process = subprocess.Popen(
        [
            sys.executable,
            '-m', 'streamlit', 'run',
            'app.py',
            '--server.headless', 'true',
            '--server.port', str(port),
            '--browser.gatherUsageStats', 'false'
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    print(f"✓ Сервер запущен на порту {port}")
    
    # Ждем запуска
    print("⏳ Ожидание готовности...")
    for i in range(30):
        try:
            import urllib.request
            urllib.request.urlopen(f'http://localhost:{port}', timeout=1)
            break
        except:
            time.sleep(0.5)
    
    print("✓ Сервер готов!")
    
    # Открываем браузер
    time.sleep(1)
    webbrowser.open(f'http://localhost:{port}')
    
    print(f"\n{'=' * 50}")
    print("✅ Beast Auto Reporter запущен!")
    print(f"   URL: http://localhost:{port}")
    print(f"\n💡 Для остановки закройте это окно")
    print(f"{'=' * 50}\n")
    
    # Показываем вывод
    for line in iter(process.stdout.readline, ''):
        if line:
            print(line.rstrip())
        if process.poll() is not None:
            break
    
except KeyboardInterrupt:
    print("\n⏹  Остановка...")
    if 'process' in locals():
        process.terminate()
    print("✓ Остановлено")
except Exception as e:
    print(f"❌ Ошибка: {e}")
    input("Нажмите Enter...")

PYTHON_EOF

LAUNCHER_EOF

chmod +x "$MACOS_DIR/launcher"

# Создаем Info.plist
echo "📄 Creating Info.plist..."
cat > "$CONTENTS_DIR/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>
    <string>Beast Auto Reporter</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIdentifier</key>
    <string>com.beast.autoreporter</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Beast Auto Reporter</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>NSHumanReadableCopyright</key>
    <string>© 2026 Beast Audio QC</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.productivity</string>
</dict>
</plist>
EOF

# Создаем иконку (простую)
echo "🎨 Creating icon..."
# Здесь можно добавить создание .icns файла если есть исходная иконка

echo ""
echo "✅ Beast Auto Reporter.app создан!"
echo "📍 Путь: $APP_DIR"
echo ""
echo "🚀 Для запуска:"
echo "   1. Откройте Finder"
echo "   2. Перейдите в: $SCRIPT_DIR/dist/"
echo "   3. Дважды кликните на 'Beast Auto Reporter.app'"
echo ""
echo "📦 Или перетащите .app в /Applications"
echo ""


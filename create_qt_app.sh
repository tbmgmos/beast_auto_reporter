#!/bin/bash

APP_NAME="Beast Auto Reporter"
APP_DIR="dist/${APP_NAME}.app"
MACOS_DIR="${APP_DIR}/Contents/MacOS"
RESOURCES_DIR="${APP_DIR}/Contents/Resources"
VENV_PATH="./venv"

echo "🍎 Создание ${APP_NAME}.app (PyQt5 версия)..."
echo "========================================"

# 1. Очистка старой версии
if [ -d "${APP_DIR}" ]; then
    echo "🗑️  Удаление старой версии..."
    rm -rf "${APP_DIR}"
fi

# 2. Создание структуры .app
echo "📁 Создание структуры app..."
mkdir -p "${MACOS_DIR}"
mkdir -p "${RESOURCES_DIR}"

# 3. Копирование файлов приложения
echo "📋 Копирование файлов приложения..."
cp beast_app_qt.py "${RESOURCES_DIR}/"
cp -R src "${RESOURCES_DIR}/"
cp -R config "${RESOURCES_DIR}/"
cp -R templates "${RESOURCES_DIR}/"
cp template.docx "${RESOURCES_DIR}/" 2>/dev/null || echo "⚠️  template.docx не найден"
cp requirements.txt "${RESOURCES_DIR}/"

# 4. Копирование виртуального окружения
echo "📦 Копирование виртуального окружения..."
cp -R "${VENV_PATH}" "${RESOURCES_DIR}/"

# 5. Создание launcher скрипта
echo "🚀 Создание launcher..."
LAUNCHER_SCRIPT="${MACOS_DIR}/${APP_NAME}"
cat << 'EOF' > "${LAUNCHER_SCRIPT}"
#!/bin/bash

# Переход в директорию Resources
cd "$(dirname "$0")/../Resources"

# Активация виртуального окружения
source venv/bin/activate

# Установка переменных окружения
export PYTHONPATH="$(pwd):$PYTHONPATH"

# Запуск PyQt5 приложения
exec venv/bin/python3 beast_app_qt.py

EOF
chmod +x "${LAUNCHER_SCRIPT}"

# 6. Создание Info.plist
echo "📄 Создание Info.plist..."
cat << EOF > "${APP_DIR}/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>com.beast.autoreporter</string>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>${APP_NAME}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>2.0</string>
    <key>CFBundleVersion</key>
    <string>2.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
EOF

echo ""
echo "✅ ${APP_NAME}.app СОЗДАН!"
echo "========================================"
echo "📍 Расположение: $(pwd)/${APP_DIR}"
echo ""
echo "🚀 ДЛЯ ЗАПУСКА:"
echo "   1. Откройте Finder"
echo "   2. Перейдите в: $(pwd)/dist/"
echo "   3. Дважды кликните на '${APP_NAME}.app'"
echo ""
echo "📦 ДЛЯ УСТАНОВКИ:"
echo "   Перетащите .app в папку /Applications"
echo ""
echo "🎯 ВОЗМОЖНОСТИ:"
echo "   ✓ PyQt5 Desktop GUI"
echo "   ✓ Современный интерфейс"
echo "   ✓ Выбор папки через диалог"
echo "   ✓ Прогресс бар с анимацией"
echo "   ✓ Автоматическое именование"
echo "   ✓ Создание выходной папки на Desktop"
echo "   ✓ Копирование всех исходных файлов"
echo "   ✓ Генерация DOCX отчета"
echo ""
echo "🧪 ТЕСТ ЗАПУСКА:"
echo "   python beast_app_qt.py"
echo ""


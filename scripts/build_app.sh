#!/bin/bash
# ============================================================================
# Build Beast Auto Reporter.app for Apple Silicon (arm64)
# Standalone .app with all dependencies bundled
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "══════════════════════════════════════════════════════════"
echo "  Beast Auto Reporter — Build for Apple Silicon"
echo "══════════════════════════════════════════════════════════"

# ── Check architecture ────────────────────────────────────────────────────
ARCH=$(uname -m)
if [ "$ARCH" != "arm64" ]; then
    echo "⚠️  Внимание: текущая архитектура $ARCH, а не arm64"
    echo "   Сборка предназначена для Apple Silicon"
fi

# ── Activate venv ─────────────────────────────────────────────────────────
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo "✅ venv активирован: $(python3 --version)"
else
    echo "❌ venv не найден. Запустите: make setup-arm"
    exit 1
fi

# ── Check dependencies ───────────────────────────────────────────────────
echo ""
echo "Проверка зависимостей..."

python3 -c "import PyQt5" 2>/dev/null || { echo "❌ PyQt5 не установлен"; exit 1; }
python3 -c "import fitz" 2>/dev/null || { echo "❌ PyMuPDF не установлен. Установите: pip install PyMuPDF"; exit 1; }
python3 -c "import PyInstaller" 2>/dev/null || { echo "❌ PyInstaller не установлен. Установите: pip install pyinstaller"; exit 1; }
python3 -c "import certifi; assert certifi.where()" 2>/dev/null || { echo "❌ certifi не установлен — вход в Яндекс OAuth не будет работать в standalone .app"; exit 1; }

if ! command -v ffmpeg &>/dev/null; then
    echo "⚠️  ffmpeg не найден в PATH — приложение не сможет анализировать аудио"
    echo "   Установите: brew install ffmpeg"
fi

echo "✅ Все зависимости найдены"

# ── Clean previous build ─────────────────────────────────────────────────
echo ""
echo "Очистка предыдущей сборки..."
rm -rf build/Beast\ Auto\ Reporter
rm -rf "dist/Beast Auto Reporter"
rm -rf "dist/Beast Auto Reporter.app"

# ── Build ─────────────────────────────────────────────────────────────────
echo ""
echo "Запуск PyInstaller..."
echo "Это может занять несколько минут..."
echo ""

python3 -m PyInstaller \
    --noconfirm \
    --clean \
    "Beast_Auto_Reporter.spec"

# ── Verify ────────────────────────────────────────────────────────────────
APP_PATH="dist/Beast Auto Reporter.app"
if [ -d "$APP_PATH" ]; then
    echo ""
    echo "══════════════════════════════════════════════════════════"
    echo "  ✅ Сборка завершена!"
    echo "══════════════════════════════════════════════════════════"
    echo ""
    echo "  Приложение: $APP_PATH"
    echo "  Размер:     $(du -sh "$APP_PATH" | cut -f1)"
    echo ""

    # Check architecture of the binary
    BINARY="$APP_PATH/Contents/MacOS/Beast Auto Reporter"
    if [ -f "$BINARY" ]; then
        echo "  Архитектура: $(file "$BINARY" | grep -o 'arm64\|x86_64')"
    fi

    # Check ffmpeg is bundled
    if [ -f "$APP_PATH/Contents/Frameworks/ffmpeg/ffmpeg" ] || \
       find "$APP_PATH" -name "ffmpeg" -type f 2>/dev/null | head -1 | grep -q .; then
        echo "  ffmpeg:      включён в сборку ✅"
    else
        echo "  ffmpeg:      НЕ найден в сборке ⚠️"
    fi

    if find "$APP_PATH" -path "*/certifi/cacert.pem" -type f 2>/dev/null | head -1 | grep -q .; then
        echo "  OAuth CA:    включён в сборку ✅"
    else
        echo "  OAuth CA:    НЕ найден — вход в Яндекс работать не будет ❌"
        exit 1
    fi

    echo ""
    echo "  Для установки на другой Mac скопируйте .app в /Applications"
    echo "  При первом запуске: правый клик → Открыть (для обхода Gatekeeper)"
    echo ""

    # Create DMG option
    if command -v hdiutil &>/dev/null; then
        echo "  Для создания DMG-образа:"
        echo "    ./scripts/build_dmg.sh"
    fi
else
    echo ""
    echo "❌ Сборка не удалась — .app не найден"
    exit 1
fi

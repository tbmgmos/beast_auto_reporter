#!/bin/bash
# ============================================================================
# Build Beast Auto Reporter.app for Intel (x86_64) via Rosetta 2
# Standalone .app with all dependencies bundled
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "══════════════════════════════════════════════════════════"
echo "  Beast Auto Reporter — Build for Intel (x86_64)"
echo "══════════════════════════════════════════════════════════"

INTEL_VENV="${PROJECT_DIR}/venv_x86"
INTEL_PYTHON="${INTEL_VENV}/bin/python3"

# ── Check venv ─────────────────────────────────────────────────────────────
if [ ! -f "${INTEL_PYTHON}" ]; then
    echo "❌ Intel venv не найден: ${INTEL_VENV}"
    echo "   Сначала запустите: ./scripts/setup_intel.sh"
    exit 1
fi

echo "✅ Intel venv найден: $(arch -x86_64 "${INTEL_PYTHON}" --version)"

# ── Check static ffmpeg/ffprobe (x86_64) required by Beast_Auto_Reporter_Intel.spec ──
if [ ! -f "/tmp/ffmpeg_x86/ffmpeg" ] || [ ! -f "/tmp/ffmpeg_x86/ffprobe" ]; then
    echo "❌ Не найдены /tmp/ffmpeg_x86/ffmpeg и /tmp/ffmpeg_x86/ffprobe"
    echo "   Это статичные x86_64-сборки ffmpeg/ffprobe (например, с evermeet.cx),"
    echo "   которые нужно положить туда перед сборкой Intel-версии."
    exit 1
fi
echo "✅ Статичный ffmpeg/ffprobe (x86_64) найден в /tmp/ffmpeg_x86"

# ── Check dependencies ───────────────────────────────────────────────────
echo ""
echo "Проверка зависимостей..."

arch -x86_64 "${INTEL_PYTHON}" -c "import PyQt5" 2>/dev/null || { echo "❌ PyQt5 не установлен в venv_x86"; exit 1; }
arch -x86_64 "${INTEL_PYTHON}" -c "import fitz" 2>/dev/null || { echo "❌ PyMuPDF не установлен в venv_x86"; exit 1; }
arch -x86_64 "${INTEL_PYTHON}" -c "import PyInstaller" 2>/dev/null || { echo "❌ PyInstaller не установлен в venv_x86"; exit 1; }

echo "✅ Все зависимости найдены"

# ── Clean previous build ─────────────────────────────────────────────────
echo ""
echo "Очистка предыдущей сборки..."
rm -rf build_intel
rm -rf dist_intel/"Beast Auto Reporter"
rm -rf dist_intel/"Beast Auto Reporter.app"

# ── Build ─────────────────────────────────────────────────────────────────
echo ""
echo "Запуск PyInstaller (x86_64 через Rosetta 2)..."
echo "Это может занять несколько минут..."
echo ""

arch -x86_64 "${INTEL_PYTHON}" -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath dist_intel \
    --workpath build_intel \
    "Beast_Auto_Reporter_Intel.spec"

# ── Verify ────────────────────────────────────────────────────────────────
APP_PATH="dist_intel/Beast Auto Reporter.app"
if [ -d "$APP_PATH" ]; then
    echo ""
    echo "══════════════════════════════════════════════════════════"
    echo "  ✅ Сборка завершена!"
    echo "══════════════════════════════════════════════════════════"
    echo ""
    echo "  Приложение: $APP_PATH"
    echo "  Размер:     $(du -sh "$APP_PATH" | cut -f1)"
    echo ""

    BINARY="$APP_PATH/Contents/MacOS/Beast Auto Reporter"
    if [ -f "$BINARY" ]; then
        echo "  Архитектура: $(file "$BINARY" | grep -o 'arm64\|x86_64')"
    fi

    if find "$APP_PATH" -name "ffmpeg" -type f 2>/dev/null | head -1 | grep -q .; then
        echo "  ffmpeg:      включён в сборку ✅"
    else
        echo "  ffmpeg:      НЕ найден в сборке ⚠️"
    fi

    echo ""
    echo "  Для создания DMG-образа:"
    echo "    ./scripts/build_dmg_intel.sh"
    echo ""
else
    echo ""
    echo "❌ Сборка не удалась — .app не найден"
    exit 1
fi

#!/bin/bash
# ============================================================================
# Create DMG from dist_intel/Beast Auto Reporter.app (x86_64)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

APP_VERSION="$(grep -m1 '^APP_VERSION = ' "beast_auto_reporter (v2 beta).py" | sed -E 's/APP_VERSION = "(.*)"/\1/')"
APP_PATH="dist_intel/Beast Auto Reporter.app"
DMG_NAME="Beast_Auto_Reporter_${APP_VERSION}_x86_64.dmg"
DMG_PATH="dist_intel/$DMG_NAME"
VOLUME_NAME="Beast Auto Reporter"

if [ ! -d "$APP_PATH" ]; then
    echo "❌ $APP_PATH не найден. Сначала запустите: ./scripts/build_app_intel.sh"
    exit 1
fi

echo "Создание DMG-образа (Intel/x86_64)..."

# Remove old DMG
rm -f "$DMG_PATH"

# Create temp dir for DMG contents
DMG_TEMP="dist_intel/dmg_temp"
rm -rf "$DMG_TEMP"
mkdir -p "$DMG_TEMP"
cp -R "$APP_PATH" "$DMG_TEMP/"
ln -s /Applications "$DMG_TEMP/Applications"

# Create DMG
hdiutil create \
    -volname "$VOLUME_NAME" \
    -srcfolder "$DMG_TEMP" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

rm -rf "$DMG_TEMP"

echo ""
echo "✅ DMG создан: $DMG_PATH"
echo "   Размер: $(du -sh "$DMG_PATH" | cut -f1)"
echo ""
echo "   Отправьте этот файл на Intel Mac для установки."

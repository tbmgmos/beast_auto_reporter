#!/bin/bash
# Beast Auto Reporter — Диагностика запуска
# Запустите этот скрипт в Terminal на проблемном Mac:
#   chmod +x diagnose_app.sh && ./diagnose_app.sh

APP_PATH="/Applications/Beast Auto Reporter.app"
BINARY="$APP_PATH/Contents/MacOS/Beast Auto Reporter"
FRAMEWORKS="$APP_PATH/Contents/Frameworks"

echo "╔══════════════════════════════════════════════╗"
echo "║  Beast Auto Reporter — Диагностика           ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

echo "=== Система ==="
sw_vers
uname -m
echo ""

echo "=== Проверка .app ==="
if [ ! -d "$APP_PATH" ]; then
    echo "❌ Приложение не найдено в /Applications"
    echo "   Укажите путь к .app:"
    read -p "   > " APP_PATH
    BINARY="$APP_PATH/Contents/MacOS/Beast Auto Reporter"
    FRAMEWORKS="$APP_PATH/Contents/Frameworks"
fi

echo "=== Карантин ==="
QATTR=$(xattr "$APP_PATH" 2>/dev/null | grep quarantine)
if [ -n "$QATTR" ]; then
    echo "⚠️  Карантин АКТИВЕН! Выполните:"
    echo "   xattr -cr \"$APP_PATH\""
    echo ""
    echo "Снять карантин сейчас? (y/n)"
    read -p "> " ANSWER
    if [ "$ANSWER" = "y" ]; then
        xattr -cr "$APP_PATH"
        echo "✅ Карантин снят"
    fi
else
    echo "✅ Карантин снят"
fi
echo ""

echo "=== Архитектура бинарника ==="
file "$BINARY" 2>/dev/null
echo ""

echo "=== Подпись ==="
codesign --verify --deep --strict "$APP_PATH" 2>&1
echo ""

echo "=== Проверка ключевых библиотек ==="
for lib in libQt5Core.dylib libQt5Gui.dylib libQt5Widgets.dylib libsndfile_arm64.dylib libsndfile.1.dylib; do
    found=$(find "$FRAMEWORKS" -name "$lib" 2>/dev/null | head -1)
    if [ -n "$found" ]; then
        echo "✅ $lib"
    else
        echo "❌ $lib — НЕ НАЙДЕН!"
    fi
done
echo ""

echo "=== Запуск приложения (вывод ошибок) ==="
echo "Нажмите Ctrl+C чтобы остановить"
echo "---"
DYLD_PRINT_LIBRARIES=1 "$BINARY" 2>&1 | head -100
echo ""
echo "=== Код завершения: $? ==="
echo ""

echo "=== Лог краша ==="
if [ -f ~/Library/Logs/BeastAutoReporter/crash.log ]; then
    echo "Содержимое crash.log:"
    cat ~/Library/Logs/BeastAutoReporter/crash.log
else
    echo "crash.log не найден"
fi

echo ""
echo "=== Системные логи краша ==="
log show --predicate 'process == "Beast Auto Reporter"' --last 2m --style compact 2>/dev/null | tail -20
echo ""
echo "Скопируйте весь вывод выше и отправьте разработчику."

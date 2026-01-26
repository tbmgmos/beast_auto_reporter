#!/bin/bash
# Автоматическое исправление запуска Beast Auto Reporter

echo "=========================================="
echo "🔧 Исправление запуска Beast Auto Reporter"
echo "=========================================="
echo ""

# Определяем возможные пути к приложению
app_paths=(
    "$HOME/Desktop/Beast Auto Reporter.app"
    "$HOME/Downloads/Beast Auto Reporter.app"
    "/Applications/Beast Auto Reporter.app"
    "$HOME/Applications/Beast Auto Reporter.app"
)

found_app=""

# Ищем приложение
echo "🔍 Поиск приложения..."
for path in "${app_paths[@]}"; do
    if [ -d "$path" ]; then
        echo "✅ Найдено: $path"
        found_app="$path"
        break
    fi
done

if [ -z "$found_app" ]; then
    echo ""
    echo "❌ Приложение не найдено!"
    echo ""
    echo "Пожалуйста, укажите путь к приложению вручную:"
    echo "Перетащите файл 'Beast Auto Reporter.app' в это окно терминала"
    echo "и нажмите Enter:"
    echo ""
    read -r found_app
    
    if [ -z "$found_app" ] || [ ! -d "$found_app" ]; then
        echo "❌ Неправильный путь. Выход."
        exit 1
    fi
fi

echo ""
echo "=========================================="
echo "🔓 Снятие блокировки macOS..."
echo "=========================================="
echo ""

# Снимаем карантин
xattr -dr com.apple.quarantine "$found_app"

if [ $? -eq 0 ]; then
    echo "✅ Карантин успешно снят!"
else
    echo "⚠️  Возможно, потребуются права администратора"
    echo "Попробуйте с sudo:"
    sudo xattr -dr com.apple.quarantine "$found_app"
fi

echo ""
echo "=========================================="
echo "🚀 Запуск приложения..."
echo "=========================================="
echo ""

# Пытаемся открыть приложение
open "$found_app"

if [ $? -eq 0 ]; then
    echo "✅ Приложение запущено!"
    echo ""
    echo "Если окно приложения не появилось:"
    echo "1. Проверьте Dock внизу экрана"
    echo "2. Проверьте Finder → Программы"
    echo "3. Нажмите Cmd+Tab для переключения между окнами"
else
    echo "❌ Не удалось запустить приложение"
    echo ""
    echo "Попробуйте вручную:"
    echo "1. Правой кнопкой на приложение"
    echo "2. Выбрать 'Открыть'"
    echo "3. Подтвердить в окне"
fi

echo ""
echo "=========================================="
echo "📋 Дополнительная информация"
echo "=========================================="
echo ""
echo "Если приложение все равно не запускается:"
echo ""
echo "1. Проверьте версию macOS:"
echo "   Требуется: macOS 11.0 (Big Sur) или новее"
echo "   У вас: $(sw_vers -productVersion)"
echo ""
echo "2. Проверьте процессор:"
echo "   Поддерживается: Intel и Apple Silicon (M1/M2/M3)"
echo "   У вас: $(uname -m)"
echo ""
echo "3. Попробуйте переустановить:"
echo "   - Удалите текущее приложение"
echo "   - Скачайте заново"
echo "   - Запустите этот скрипт снова"
echo ""
echo "4. Проверьте логи ошибок:"
echo "   - Откройте Консоль.app"
echo "   - Найдите 'Beast Auto Reporter'"
echo "   - Пришлите скриншот ошибок"
echo ""

echo "Готово!"
echo ""

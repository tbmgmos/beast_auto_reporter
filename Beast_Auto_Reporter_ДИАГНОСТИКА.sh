#!/bin/bash
# Beast Auto Reporter - Диагностический скрипт
# Запустите этот скрипт на проблемном компьютере

echo "=========================================="
echo "Beast Auto Reporter - Диагностика"
echo "=========================================="
echo ""

# Информация о системе
echo "📋 ИНФОРМАЦИЯ О СИСТЕМЕ:"
echo "  macOS версия: $(sw_vers -productVersion)"
echo "  Процессор: $(uname -m)"
echo "  Имя компьютера: $(hostname)"
echo ""

# Проверка FFmpeg/ffprobe
echo "🔍 ПРОВЕРКА FFPROBE:"
if command -v ffprobe &> /dev/null; then
    echo "  ✅ ffprobe найден: $(which ffprobe)"
    echo "  Версия: $(ffprobe -version | head -n1)"
else
    echo "  ❌ ffprobe НЕ НАЙДЕН!"
    echo "     Установите: brew install ffmpeg"
fi
echo ""

# Проверка стандартных путей
echo "🔍 ПРОВЕРКА СТАНДАРТНЫХ ПУТЕЙ:"
paths=(
    "/opt/homebrew/bin/ffprobe"
    "/usr/local/bin/ffprobe"
    "/usr/bin/ffprobe"
)

for path in "${paths[@]}"; do
    if [ -f "$path" ]; then
        echo "  ✅ Найден: $path"
    else
        echo "  ❌ Не найден: $path"
    fi
done
echo ""

# Проверка Homebrew
echo "🔍 ПРОВЕРКА HOMEBREW:"
if command -v brew &> /dev/null; then
    echo "  ✅ Homebrew установлен: $(which brew)"
    echo "  Архитектура: $(brew --prefix)"
    
    if brew list ffmpeg &> /dev/null; then
        echo "  ✅ FFmpeg установлен через Homebrew"
    else
        echo "  ❌ FFmpeg НЕ установлен через Homebrew"
        echo "     Установите: brew install ffmpeg"
    fi
else
    echo "  ❌ Homebrew НЕ установлен!"
    echo "     Установите: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
fi
echo ""

# Проверка Python
echo "🔍 ПРОВЕРКА PYTHON:"
if command -v python3 &> /dev/null; then
    echo "  ✅ Python3 найден: $(which python3)"
    echo "  Версия: $(python3 --version)"
else
    echo "  ❌ Python3 НЕ найден!"
fi
echo ""

# Проверка Ollama (опционально)
echo "🔍 ПРОВЕРКА OLLAMA (опционально для AI):"
if command -v ollama &> /dev/null; then
    echo "  ✅ Ollama установлен: $(which ollama)"
    if pgrep -x "ollama" > /dev/null; then
        echo "  ✅ Ollama запущен"
    else
        echo "  ⚠️  Ollama установлен, но не запущен"
        echo "     Запустите: ollama serve"
    fi
else
    echo "  ℹ️  Ollama не установлен (не обязательно)"
    echo "     Нужен только для AI генерации"
fi
echo ""

# Рекомендации
echo "=========================================="
echo "💡 РЕКОМЕНДАЦИИ:"
echo "=========================================="

if ! command -v ffprobe &> /dev/null; then
    echo ""
    echo "❗ КРИТИЧНО: Установите FFmpeg"
    echo "   Выполните команды:"
    echo "   "
    if [[ $(uname -m) == "arm64" ]]; then
        echo "   # Для Apple Silicon (M1/M2/M3):"
        echo "   /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "   echo 'eval \"\$(/opt/homebrew/bin/brew shellenv)\"' >> ~/.zprofile"
        echo "   eval \"\$(/opt/homebrew/bin/brew shellenv)\""
    else
        echo "   # Для Intel Mac:"
        echo "   /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    fi
    echo "   brew install ffmpeg"
    echo ""
fi

echo ""
echo "=========================================="
echo "Диагностика завершена!"
echo "=========================================="
echo ""
echo "Сохраните этот вывод и отправьте разработчику"
echo "для решения проблем."
echo ""

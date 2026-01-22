# 🚀 Инструкция по установке Beast Auto Reporter

## Системные требования

- **Python**: 3.10 или выше
- **ОС**: macOS, Linux, или Windows
- **RAM**: минимум 8 GB (рекомендуется 16 GB для LLM)
- **Свободное место**: ~10 GB (для моделей Ollama)

## Шаг 1: Клонирование репозитория

```bash
git clone https://github.com/yourusername/Beast_Auto_Reporter.git
cd Beast_Auto_Reporter
```

## Шаг 2: Создание виртуального окружения

### macOS / Linux:
```bash
python3 -m venv venv
source venv/bin/activate
```

### Windows:
```bash
python -m venv venv
venv\Scripts\activate
```

## Шаг 3: Установка Python зависимостей

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Возможные проблемы:

**Ошибка с librosa:**
```bash
# Установите дополнительные зависимости
pip install numba==0.58.1
```

**Ошибка с soundfile:**
```bash
# macOS:
brew install libsndfile

# Ubuntu/Debian:
sudo apt-get install libsndfile1

# Windows: обычно работает из коробки
```

## Шаг 4: Установка FFmpeg

FFmpeg необходим для работы с различными аудиоформатами.

### macOS:
```bash
brew install ffmpeg
```

### Ubuntu/Debian:
```bash
sudo apt update
sudo apt install ffmpeg
```

### Windows:

1. Скачайте FFmpeg: https://ffmpeg.org/download.html
2. Распакуйте архив
3. Добавьте путь к `bin` в PATH
4. Проверьте: `ffmpeg -version`

Или через scoop:
```bash
scoop install ffmpeg
```

## Шаг 5: Установка Ollama (для AI заключений)

Ollama - это локальный сервер для запуска LLM моделей.

### macOS:
```bash
brew install ollama

# Или скачайте с сайта:
# https://ollama.ai/download
```

### Linux:
```bash
curl https://ollama.ai/install.sh | sh
```

### Windows:
Скачайте установщик с: https://ollama.ai/download

## Шаг 6: Запуск Ollama и установка модели

1. **Запустите Ollama сервер:**

```bash
ollama serve
```

Оставьте этот терминал открытым или запустите как службу.

2. **В новом терминале скачайте модель:**

```bash
# Рекомендуемая модель (меньше размер)
ollama pull llama3.2

# Или более мощная модель
ollama pull mistral

# Или для лучшей работы на русском
ollama pull qwen2.5
```

3. **Проверьте установленные модели:**

```bash
ollama list
```

## Шаг 7: Проверка установки

Запустите тестовый скрипт для проверки всех компонентов:

```bash
python -c "
import librosa
import pyloudnorm
import soundfile
import streamlit
import ollama
print('✓ Все зависимости установлены успешно!')
"
```

## Шаг 8: Первый запуск

```bash
streamlit run app.py
```

Приложение откроется в браузере по адресу: `http://localhost:8501`

## Устранение неполадок

### Ollama не подключается

1. Проверьте, что Ollama запущена:
```bash
curl http://localhost:11434/api/tags
```

2. Если не работает, запустите:
```bash
ollama serve
```

3. Проверьте настройки в `config/settings.yaml`:
```yaml
llm:
  ollama_host: "http://localhost:11434"
```

### Ошибки с памятью (RAM)

Если не хватает памяти для LLM:

1. Используйте более легкую модель:
```bash
ollama pull llama3.2:1b  # 1 миллиард параметров
```

2. Отключите AI заключения в настройках приложения (будет использоваться fallback)

### Проблемы с аудио библиотеками

```bash
# Переустановите ключевые библиотеки
pip uninstall librosa soundfile
pip install librosa soundfile --no-cache-dir
```

### Streamlit не запускается

```bash
# Очистите кэш Streamlit
streamlit cache clear

# Проверьте версию
streamlit --version

# Переустановите
pip install streamlit --upgrade
```

## Опциональные шаги

### Настройка как системной службы (Linux/macOS)

Для автоматического запуска Ollama при загрузке системы:

**macOS:**
```bash
brew services start ollama
```

**Linux (systemd):**
Создайте файл `/etc/systemd/system/ollama.service`:
```ini
[Unit]
Description=Ollama Service
After=network.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=yourusername
Restart=always

[Install]
WantedBy=multi-user.target
```

Затем:
```bash
sudo systemctl enable ollama
sudo systemctl start ollama
```

### Ускорение (GPU)

Если у вас есть совместимая GPU:

1. Установите CUDA (NVIDIA) или ROCm (AMD)
2. Ollama автоматически использует GPU
3. Проверьте: `ollama run llama3.2` (должно быть быстрее)

## Проверка всей системы

Запустите полный тест:

```bash
python tests/test_analyzer.py
```

## Обновление

Для обновления до последней версии:

```bash
git pull origin main
pip install -r requirements.txt --upgrade
ollama pull llama3.2  # Обновить модель
```

## Получение помощи

- 📖 Документация: [PROJECT_PLAN.md](PROJECT_PLAN.md)
- 🐛 Issues: https://github.com/yourusername/Beast_Auto_Reporter/issues
- 💬 Discussions: https://github.com/yourusername/Beast_Auto_Reporter/discussions

---

**Готово!** 🎉 Теперь вы можете использовать Beast Auto Reporter.

Запустите приложение:
```bash
streamlit run app.py
```


# Установка Beast Auto Reporter

## Системные требования

- **Python**: 3.10+
- **ОС**: macOS (Apple Silicon или Intel)
- **RAM**: 8 GB (16 GB для LLM)
- **FFmpeg**: обязателен
- **Ollama**: опционально (для AI-заключений)

## Быстрая установка

### ARM (Apple Silicon — M1/M2/M3/M4)

```bash
git clone https://github.com/yourusername/Beast_Auto_Reporter.git
cd Beast_Auto_Reporter
./scripts/setup_arm.sh
```

### Intel (x86_64 — нативный или через Rosetta 2)

```bash
git clone https://github.com/yourusername/Beast_Auto_Reporter.git
cd Beast_Auto_Reporter
./scripts/setup_intel.sh
```

Скрипт автоматически:
- Проверит/установит Rosetta 2 (на Apple Silicon)
- Установит Intel Homebrew в `/usr/local`
- Установит Intel Python, FFmpeg, libsndfile
- Создаст отдельный venv (`venv_x86/`) со всеми зависимостями
- Проверит работоспособность всех компонентов

## Запуск

### ARM
```bash
./scripts/run_arm.sh              # Запуск приложения
./scripts/run_arm.sh --test       # Тесты
./scripts/run_arm.sh --verify     # Проверка окружения
```

### Intel
```bash
./scripts/run_intel.sh            # Запуск через Rosetta 2
./scripts/run_intel.sh --test     # Тесты в x86_64 эмуляции
./scripts/run_intel.sh --verify   # Проверка Intel окружения
./scripts/run_intel.sh --shell    # Интерактивный Intel shell
```

### Через Makefile
```bash
make help          # Все доступные команды

make setup-arm     # Настроить ARM
make setup-intel   # Настроить Intel

make run-arm       # Запуск ARM
make run-intel     # Запуск Intel

make test-arm      # Тесты ARM
make test-intel    # Тесты Intel
make test-both     # Тесты на обеих архитектурах

make verify-arm    # Проверка ARM
make verify-intel  # Проверка Intel
```

## Архитектура окружений

```
Beast_Auto_Reporter/
├── venv/           # ARM (Apple Silicon) — Python + зависимости arm64
├── venv_x86/       # Intel — Python + зависимости x86_64
├── scripts/
│   ├── setup_arm.sh     # Установка ARM окружения
│   ├── setup_intel.sh   # Установка Intel окружения
│   ├── run_arm.sh       # Запуск ARM версии
│   └── run_intel.sh     # Запуск Intel версии (через Rosetta 2)
└── Makefile             # Единая точка управления
```

### Разделение зависимостей

| Компонент       | ARM (arm64)                     | Intel (x86_64)                  |
|-----------------|----------------------------------|---------------------------------|
| Homebrew        | `/opt/homebrew`                  | `/usr/local`                    |
| Python          | `/opt/homebrew/bin/python3`      | `/usr/local/bin/python3`        |
| FFmpeg          | `/opt/homebrew/bin/ffmpeg`       | `/usr/local/bin/ffmpeg`         |
| venv            | `venv/`                          | `venv_x86/`                     |
| Запуск          | `./scripts/run_arm.sh`           | `./scripts/run_intel.sh`        |

Оба окружения полностью изолированы — разные бинарники Python, разные скомпилированные библиотеки (numpy, scipy, librosa), разные FFmpeg.

## Переменная окружения BEAST_ARCH

При запуске через скрипты устанавливается `BEAST_ARCH=arm64` или `BEAST_ARCH=x86_64`. Приложение может использовать её для диагностики:

```python
import os
arch = os.environ.get("BEAST_ARCH", "unknown")
```

## Зависимости

### Python-пакеты (requirements.txt)

**Аудио-анализ:**
- `pyloudnorm` — ITU-R BS.1770-4 (LUFS, LRA, True Peak)
- `librosa` — анализ аудио
- `scipy`, `numpy` — числовые вычисления
- `soundfile` — чтение аудиофайлов
- `pydub` — обработка аудио
- `ffmpeg-python` — привязка к FFmpeg

**GUI и отчёты:**
- `PyQt5` — десктопный интерфейс
- `python-docx` — генерация DOCX
- `Pillow` — обработка изображений
- `reportlab` — генерация PDF (fallback)

**LLM (опционально):**
- `ollama` — локальный LLM
- `langchain`, `langchain-community` — оркестрация LLM

**Данные:**
- `pandas` — CSV/данные
- `matplotlib`, `seaborn` — графики
- `pyyaml` — конфигурация

### Системные зависимости

- **FFmpeg/ffprobe** — обязателен для аудио-анализа
- **libsndfile** — для soundfile
- **Ollama** — опционально, для AI-заключений

## Установка Ollama (опционально)

```bash
brew install ollama
ollama serve              # В отдельном терминале
ollama pull llama3.2      # Скачать модель
```

Проверка:
```bash
curl http://localhost:11434/api/tags
```

## Устранение неполадок

### librosa не устанавливается
```bash
pip install numba==0.58.1
pip install librosa --no-cache-dir
```

### soundfile не работает
```bash
brew install libsndfile  # ARM
# или
arch -x86_64 /usr/local/bin/brew install libsndfile  # Intel
```

### PyQt5 не находится
Скрипт запуска автоматически ищет Python с PyQt5. Убедитесь, что PyQt5 установлен в venv:
```bash
./scripts/run_arm.sh --verify   # или run_intel.sh
```

### Тесты падают только на одной архитектуре
Запустите `make test-both` для сравнения. Числовые результаты (LUFS, True Peak) могут незначительно отличаться между arm64 и x86_64 из-за различий в FPU.

## Очистка

```bash
make clean-arm     # Удалить ARM venv
make clean-intel   # Удалить Intel venv
make clean-all     # Удалить оба
```

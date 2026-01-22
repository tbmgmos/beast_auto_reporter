# 🎵 Beast Auto Reporter

**Автоматическая система генерации отчетов о контроле качества аудио**

## 📖 Описание

Beast Auto Reporter - это локальное приложение для автоматической проверки качества звука в видеоконтенте. Система анализирует аудиофайлы, обнаруживает дефекты и генерирует профессиональные отчеты с использованием AI.

## ✨ Возможности

- 🔊 **Анализ аудио параметров**
  - LUFS (интегральная громкость)
  - TRUE PEAK (пиковые значения)
  - LRA (динамический диапазон)
  - Поддержка форматов 2.0 и 5.1

- 🔍 **Автоматическая детекция дефектов**
  - Клипинг и перегруз
  - Клики и треск
  - Высокочастотное шипение
  - Отсутствие звука
  - Проблемы с каналами

- 🤖 **AI-генерация заключений**
  - Локальная LLM (Ollama)
  - Интеллектуальный анализ
  - Рекомендации по исправлению

- 📊 **Профессиональные отчеты**
  - CSV с таймкодами
  - PDF с графиками
  - DOCX итоговый документ

## 🚀 Быстрый старт

### Требования

- Python 3.10+
- FFmpeg
- Ollama (для AI-заключений)

### Установка

```bash
# 1. Клонировать репозиторий
git clone https://github.com/yourusername/Beast_Auto_Reporter.git
cd Beast_Auto_Reporter

# 2. Создать виртуальное окружение
python -m venv venv
source venv/bin/activate  # На Windows: venv\Scripts\activate

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Установить FFmpeg (если не установлен)
# macOS: brew install ffmpeg
# Ubuntu: sudo apt install ffmpeg
# Windows: scoop install ffmpeg

# 5. Установить Ollama
# macOS: brew install ollama
# Linux: curl https://ollama.ai/install.sh | sh
# Windows: https://ollama.ai/download

# 6. Скачать модель
ollama pull llama3.2
```

### Запуск

```bash
streamlit run app.py
```

Откройте браузер: http://localhost:8501

## 📁 Структура проекта

```
Beast_Auto_Reporter/
├── app.py                    # Главное приложение
├── requirements.txt          # Зависимости
├── README.md
├── PROJECT_PLAN.md          # Детальный план
├── config/
│   └── settings.yaml        # Настройки
├── src/
│   ├── audio_analyzer.py    # Анализ аудио
│   ├── defect_detector.py   # Детекция дефектов
│   ├── llm_integration.py   # AI интеграция
│   └── report_generator.py  # Генерация отчетов
├── templates/
│   ├── report_template.docx
│   └── prompts.yaml
└── output/                   # Выходные файлы
```

## 🎯 Использование

1. **Загрузите файлы**
   - Аудио 2.0 (стерео)
   - Аудио 5.1 (surround)
   - Видео (опционально)

2. **Настройте параметры**
   - Target LUFS: -23 dB
   - TRUE PEAK: -2.0 dBTP
   - LRA: 18 LU

3. **Запустите анализ**
   - Система проанализирует файлы
   - Обнаружит дефекты
   - Сгенерирует заключение

4. **Скачайте отчеты**
   - CSV, PDF, DOCX
   - Или все вместе (ZIP)

## 📊 Поддерживаемые форматы

**Аудио:**
- WAV, AIFF (PCM 24bit 48kHz рекомендуется)
- MP3, AAC, FLAC

**Видео:**
- MP4, MOV, MKV (для извлечения таймкодов)

## 🔧 Настройка

Отредактируйте `config/settings.yaml`:

```yaml
audio:
  target_lufs: -23.0
  lufs_tolerance: 0.5
  true_peak: -2.0
  lra_max: 18.0

detection:
  click_threshold: 0.05
  noise_threshold: -60
  clipping_threshold: -1.0

llm:
  model: "llama3.2"
  temperature: 0.7
  language: "ru"
```

## 🛠️ Разработка

```bash
# Запуск тестов
pytest tests/

# Форматирование кода
black src/

# Линтер
flake8 src/
```

## 📝 TODO

- [ ] Поддержка Dolby Atmos
- [ ] Экспорт в EDL
- [ ] Интеграция с DaVinci Resolve
- [ ] Batch processing
- [ ] API для автоматизации

## 🤝 Вклад

Приветствуются pull requests! Для крупных изменений сначала откройте issue.

## 📄 Лицензия

MIT

## 👨‍💻 Автор

Beast Auto Reporter Team

## 📧 Контакты

- GitHub Issues: [создать issue](https://github.com/yourusername/Beast_Auto_Reporter/issues)
- Email: support@beast-reporter.com

---

**⚠️ Важно:** Все обработки происходят локально. Ваши файлы никуда не отправляются.


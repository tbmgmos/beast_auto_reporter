# 🚀 Быстрый старт Beast Auto Reporter

## За 5 минут до первого отчета!

### Шаг 1: Установка зависимостей (1 мин)

```bash
# Создайте виртуальное окружение
python3 -m venv venv
source venv/bin/activate  # На Windows: venv\Scripts\activate

# Установите зависимости
pip install -r requirements.txt
```

### Шаг 2: Установка Ollama (2 мин)

**macOS:**
```bash
brew install ollama
ollama serve &  # Запустить в фоне
ollama pull llama3.2
```

**Linux:**
```bash
curl https://ollama.ai/install.sh | sh
ollama serve &
ollama pull llama3.2
```

**Windows:**
1. Скачайте: https://ollama.ai/download
2. Установите и запустите
3. В командной строке: `ollama pull llama3.2`

### Шаг 3: Запуск приложения (10 сек)

```bash
streamlit run app.py
```

Откроется браузер с приложением! 🎉

### Шаг 4: Создание первого отчета (2 мин)

1. **Загрузите аудиофайл** (стерео 2.0 или 5.1)
2. **Настройте параметры** (или оставьте по умолчанию)
3. **Нажмите "Запустить анализ"**
4. **Скачайте отчеты** (CSV, PDF, DOCX)

## 📋 Что делает приложение?

### Автоматически анализирует:
- ✅ **LUFS** - интегральная громкость (стандарт EBU R128)
- ✅ **TRUE PEAK** - пиковые значения
- ✅ **LRA** - динамический диапазон

### Обнаруживает дефекты:
- 🔍 Клипинг и перегруз
- 🔍 Клики и треск
- 🔍 Высокочастотное шипение
- 🔍 Тишина / отсутствие звука
- 🔍 Проблемы с каналами 5.1

### Генерирует отчеты:
- 📄 **CSV** - таблица с таймкодами дефектов
- 📕 **PDF** - отчет с графиками
- 📘 **DOCX** - итоговый документ
- 🤖 **AI заключение** - профессиональное резюме

## 🎯 Пример использования

```python
# Или используйте программно:
from src.audio_analyzer import AudioAnalyzer
from src.defect_detector import DefectDetector

# Анализ
analyzer = AudioAnalyzer(config)
results = analyzer.analyze_file("audio.wav")

# Детекция
detector = DefectDetector(config)
defects = detector.analyze_file("audio.wav", "2.0")

print(f"LUFS: {results['measurements']['lufs']}")
print(f"Дефектов: {len(defects)}")
```

## ⚙️ Основные настройки

Отредактируйте `config/settings.yaml`:

```yaml
audio:
  target_lufs: -23.0  # Целевая громкость
  lufs_tolerance: 0.5  # Допуск ±dB
  true_peak: -2.0      # Макс. пик
  lra_max: 18.0        # Макс. динамический диапазон

llm:
  model: "llama3.2"    # Или mistral, qwen2.5
  temperature: 0.7     # Креативность AI (0-1)
  language: "ru"       # ru или en
```

## 🔧 Устранение проблем

### Ollama не работает?

```bash
# Проверьте статус
curl http://localhost:11434/api/tags

# Если не отвечает, запустите:
ollama serve
```

### Ошибки с аудио?

```bash
# Установите FFmpeg
brew install ffmpeg  # macOS
sudo apt install ffmpeg  # Linux
```

### Не хватает памяти?

```bash
# Используйте легкую модель
ollama pull llama3.2:1b
```

Затем в `config/settings.yaml`:
```yaml
llm:
  model: "llama3.2:1b"
```

## 📚 Дополнительные материалы

- 📖 **Полная документация**: [PROJECT_PLAN.md](PROJECT_PLAN.md)
- 🔧 **Детальная установка**: [INSTALL.md](INSTALL.md)
- 📄 **README**: [README.md](README.md)

## 💡 Советы

1. **Используйте качественные файлы**: WAV 48kHz 24bit
2. **Проверяйте Ollama**: Должна быть запущена для AI
3. **Смотрите логи**: Если что-то не работает
4. **Экспортируйте все**: Кнопка "Все (ZIP)" скачает всё сразу

## 🎬 Что дальше?

1. Попробуйте разные модели LLM
2. Настройте пороги детекции под себя
3. Создайте свои шаблоны отчетов
4. Автоматизируйте через API (скоро!)

---

**Готово!** Теперь вы готовы создавать профессиональные отчеты! 🚀

**Нужна помощь?** Создайте issue на GitHub или напишите нам.


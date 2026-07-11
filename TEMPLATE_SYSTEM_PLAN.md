# СИСТЕМА ШАБЛОНОВ DOCX

## Проблема

На разных ПК отчеты выглядят по-разному из-за:
1. Различий в версиях python-docx
2. Различий в системных шрифтах
3. Различий в настройках Word
4. Программного создания сложных таблиц с низкоуровневым XML

## Решение: Шаблонный подход

### Преимущества

✅ **Консистентность**: Одинаковый вид на всех ПК
✅ **Простота редактирования**: Можно редактировать в Word
✅ **Независимость от версий**: Не зависит от python-docx версии
✅ **Быстрые изменения**: Меняем шаблон, а не код
✅ **Ручное редактирование**: Легко подправить вручную если нужно

### Архитектура

```
templates/docx/
├── standard_template.docx       # Шаблон стандартного отчета
├── me_template.docx             # Шаблон M&E отчета
└── marker_list_template.docx    # Шаблон маркер-листа
```

### Как работает

1. **Создание шаблона** (один раз):
   - Открываем Word
   - Создаем таблицу с правильным форматированием
   - Вставляем плейсхолдеры: `{{ДАТА}}`, `{{ИМЯ}}`, `{{ФАЙЛ_1}}`, etc.
   - Сохраняем как .docx шаблон

2. **Использование шаблона** (в коде):
   ```python
   from docx import Document
   
   # Загружаем шаблон
   doc = Document('templates/docx/standard_template.docx')
   
   # Заменяем плейсхолдеры на реальные данные
   replace_placeholder(doc, '{{ДАТА}}', '27.01.2026')
   replace_placeholder(doc, '{{ИМЯ}}', 'Влад')
   replace_placeholder(doc, '{{ФАЙЛ_1}}', 'audio_20_cens.wav')
   
   # Сохраняем результат
   doc.save('отчет.docx')
   ```

3. **Динамические данные** (таблицы):
   - Для маркер-листа: копируем строку-шаблон N раз
   - Для технической таблицы: заполняем предопределенные ячейки

## Плейсхолдеры

### Технические данные

```
{{ДАТА}}                  # Дата отчета
{{ИМЯ}}                   # Отчёт подготовил
{{ФАЙЛ_20_CENS}}          # Имя файла 2.0 cens
{{ФАЙЛ_20_UNCENS}}        # Имя файла 2.0 uncens
{{ФАЙЛ_51_CENS}}          # Имя файла 5.1 cens
{{ФАЙЛ_51_UNCENS}}        # Имя файла 5.1 uncens
{{ФАЙЛ_VIDEO}}            # Имя видео файла

{{ХРОНО_20_CENS}}         # Хронометраж 2.0 cens
{{ХРОНО_20_UNCENS}}       # Хронометраж 2.0 uncens
{{ХРОНО_51_CENS}}         # Хронометраж 5.1 cens
{{ХРОНО_51_UNCENS}}       # Хронометраж 5.1 uncens
{{ХРОНО_VIDEO}}           # Хронометраж видео

{{LUFS_20_CENS}}          # LUFS 2.0 cens
{{LUFS_20_UNCENS}}        # LUFS 2.0 uncens
{{LUFS_51_CENS}}          # LUFS 5.1 cens
{{LUFS_51_UNCENS}}        # LUFS 5.1 uncens

{{PEAK_20_CENS}}          # TRUE PEAK 2.0 cens
{{PEAK_20_UNCENS}}        # TRUE PEAK 2.0 uncens
{{PEAK_51_CENS}}          # TRUE PEAK 5.1 cens
{{PEAK_51_UNCENS}}        # TRUE PEAK 5.1 uncens

{{LRA_20_CENS}}           # LRA 2.0 cens
{{LRA_20_UNCENS}}         # LRA 2.0 uncens
{{LRA_51_CENS}}           # LRA 5.1 cens
{{LRA_51_UNCENS}}         # LRA 5.1 uncens

{{ФОРМАТ_20_CENS}}        # Формат файла 2.0 cens
{{ФОРМАТ_20_UNCENS}}      # Формат файла 2.0 uncens
{{ФОРМАТ_51_CENS}}        # Формат файла 5.1 cens
{{ФОРМАТ_51_UNCENS}}      # Формат файла 5.1 uncens
{{ФОРМАТ_VIDEO}}          # Формат видео файла
```

### Заключения

```
{{ЗАКЛЮЧЕНИЕ_ТЕХНИЧЕСКОЕ}}   # Техническое заключение
{{ЗАКЛЮЧЕНИЕ_СУБЪЕКТИВНОЕ}}  # Субъективное заключение
```

### Маркер-лист (повторяющиеся строки)

```
{{ТАЙМКОД_IN}}            # Таймкод начала
{{ТАЙМКОД_OUT}}           # Таймкод окончания
{{ОПИСАНИЕ}}              # Описание проблемы
{{ДЛИТЕЛЬНОСТЬ}}          # Длительность
{{20_C}}                  # 2.0 C
{{51_C}}                  # 5.1 C
{{БЛОКЕР}}                # Блокер
{{ИСПРАВЛЕНИЕ}}           # Требует исправления
{{КОММЕНТАРИЙ}}           # Требует комментария
```

## Реализация

### Этап 1: Создание шаблонов

1. Создать `standard_template.docx`:
   - Страница 1: Техническая таблица с плейсхолдерами
   - Страница 2: Маркер-лист (1 строка-шаблон)

2. Создать `me_template.docx`:
   - Страница 1: M&E таблица с плейсхолдерами
   - Страница 2: Маркер-лист (1 строка-шаблон)

### Этап 2: Утилита для замены плейсхолдеров

```python
# src/template_processor.py

from docx import Document
from typing import Dict

class TemplateProcessor:
    """Обработчик шаблонов DOCX"""
    
    def __init__(self, template_path: str):
        self.doc = Document(template_path)
    
    def replace_placeholders(self, replacements: Dict[str, str]):
        """Замена плейсхолдеров в документе"""
        for paragraph in self.doc.paragraphs:
            for key, value in replacements.items():
                if key in paragraph.text:
                    paragraph.text = paragraph.text.replace(key, value)
        
        for table in self.doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        for key, value in replacements.items():
                            if key in paragraph.text:
                                paragraph.text = paragraph.text.replace(key, value)
    
    def duplicate_row(self, table_index: int, row_index: int, count: int):
        """Дублирование строки таблицы"""
        table = self.doc.tables[table_index]
        row = table.rows[row_index]
        
        for _ in range(count):
            new_row = table.add_row()
            for i, cell in enumerate(row.cells):
                new_row.cells[i].text = cell.text
    
    def save(self, output_path: str):
        """Сохранение документа"""
        self.doc.save(output_path)
```

### Этап 3: Модификация exact_report_generator.py

```python
class ExactReportGenerator:
    def __init__(self, template_dir: str = "templates/docx"):
        self.template_dir = Path(template_dir)
    
    def create_exact_report(self, ..., use_template: bool = True):
        if use_template:
            return self._create_from_template(...)
        else:
            return self._create_programmatically(...)
    
    def _create_from_template(self, ...):
        # Выбираем шаблон
        if report_type == "me":
            template = "me_template.docx"
        else:
            template = "standard_template.docx"
        
        # Загружаем шаблон
        processor = TemplateProcessor(self.template_dir / template)
        
        # Подготавливаем замены
        replacements = {
            '{{ДАТА}}': datetime.now().strftime('%d.%m.%Y'),
            '{{ИМЯ}}': prepared_by,
            '{{ФАЙЛ_20_CENS}}': tech_info.get('audio_20_c', {}).get('file_name', ''),
            # ... и т.д.
        }
        
        # Заменяем плейсхолдеры
        processor.replace_placeholders(replacements)
        
        # Добавляем маркер-лист
        self._add_marker_list_from_template(processor, issues)
        
        # Сохраняем
        processor.save(output_path)
```

## Преимущества для пользователя

1. **Редактируемость**: Можно открыть шаблон в Word и изменить:
   - Шрифты
   - Размеры ячеек
   - Цвета
   - Границы таблиц
   - Расположение элементов

2. **Консистентность**: Одинаковый вид на всех ПК

3. **Скорость**: Быстрее генерация (не нужно создавать таблицы программно)

4. **Ручное редактирование**: После генерации можно легко подправить

## План внедрения

### Шаг 1: Создать рабочий шаблон
- Взять готовый отчет с правильным форматированием
- Заменить данные на плейсхолдеры
- Сохранить как шаблон

### Шаг 2: Написать утилиту замены
- Создать `template_processor.py`
- Реализовать замену плейсхолдеров
- Реализовать дублирование строк для маркер-листа

### Шаг 3: Модифицировать генератор
- Добавить опцию `use_template=True`
- Реализовать генерацию из шаблона
- Сохранить старый код как fallback

### Шаг 4: Тестирование
- Создать отчеты на разных ПК
- Сравнить форматирование
- При необходимости подправить шаблон

## Миграция

**Переход будет плавным:**
1. Оставим старый код (программная генерация)
2. Добавим новый код (шаблонная генерация)
3. Добавим флаг `use_template` в настройки
4. Пользователь сможет выбрать метод

**После тестирования:**
- Если шаблоны работают идеально → сделаем default
- Если нужны доработки → продолжим использовать программную генерацию

## Вопросы

1. **У вас есть готовый отчет с правильным форматированием?**
   - Если да, можем его использовать как базу для шаблона

2. **Какие параметры наиболее важны?**
   - Размеры ячеек
   - Шрифты
   - Цвета индикаторов
   - Границы таблиц

3. **Нужна ли совместимость со старыми версиями?**
   - Или можем полностью перейти на шаблоны?

## Следующие шаги

1. **Создаю утилиту для работы с шаблонами**
2. **Модифицирую exact_report_generator.py**
3. **Создаю базовый шаблон из существующего отчета**
4. **Тестируем на разных ПК**

Готов начать реализацию! Скажите, если есть какие-то предпочтения по форматированию.

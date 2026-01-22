"""
Conclusion Generator Module

Генерирует заключение по субъективной оценке на основе импортированных проблем
Использует локальный LLM (Ollama) для генерации
"""

import logging
import sys
from pathlib import Path
from typing import List

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.csv_importer import Issue

logger = logging.getLogger(__name__)


class ConclusionGenerator:
    """Класс для генерации заключения"""
    
    def __init__(self, use_llm: bool = True):
        """
        Инициализация генератора
        
        Args:
            use_llm: Использовать ли LLM (Ollama) для генерации
        """
        self.use_llm = use_llm
        logger.info(f"ConclusionGenerator инициализирован (LLM: {use_llm})")
    
    def generate_technical_conclusion(self, tech_info: dict, params: dict = None) -> str:
        """
        Генерация технического заключения на основе параметров
        
        Args:
            tech_info: Техническая информация из аудио/видео/PDF
            params: Номинальные параметры из Параметры.txt
            
        Returns:
            Текст технического заключения
        """
        # Сначала собираем все проблемы
        problems = []
        
        # Параметры по умолчанию
        target_lufs = params.get('target_lufs', -23.0) if params else -23.0
        target_peak = params.get('true_peak', -1.0) if params else -1.0
        target_lra = params.get('lra_max', 15.0) if params else 15.0
        lufs_tolerance = 0.5
        
        # Проверяем хронометраж
        durations = []
        file_names = []
        for key in ['audio_20_c', 'audio_51_c', 'audio_20_uc', 'audio_51_uc', 'video']:
            if key in tech_info and tech_info[key]:
                data = tech_info[key]
                if data.get('duration'):
                    durations.append(data['duration'])
                    file_names.append(data.get('file_name', key))
        
        # Проверяем совпадение хронометража
        if len(durations) > 1:
            reference_duration = durations[0]
            mismatches = []
            for i, duration in enumerate(durations[1:], start=1):
                if abs(duration - reference_duration) > 0.1:  # Допуск 100 мс
                    diff = abs(duration - reference_duration)
                    mismatches.append(f"{file_names[i]} (разница {diff:.3f} сек)")
            
            if mismatches:
                problems.append(f"Хронометраж не совпадает между файлами: {', '.join(mismatches)}")
        
        # Проверяем кратность кадру (24 или 25 fps)
        if '_frame_issues' in tech_info and tech_info['_frame_issues']:
            frame_issues = tech_info['_frame_issues']
            fps = frame_issues[0].get('fps', 25)
            frame_duration = 1.0 / fps
            frame_duration_ms = frame_duration * 1000
            
            issue_files = [issue['file'] for issue in frame_issues]
            if len(issue_files) == 1:
                problems.append(
                    f"Длительность файла {issue_files[0]} не кратна кадру "
                    f"({fps} fps, длительность кадра {frame_duration_ms:.2f} мс)"
                )
            else:
                problems.append(
                    f"Длительность файлов {', '.join(issue_files)} не кратна кадру "
                    f"({fps} fps, длительность кадра {frame_duration_ms:.2f} мс)"
                )
        
        # Проверяем LUFS, TRUE PEAK, LRA для PDF файлов
        for pdf_key in ['pdf_20', 'pdf_51']:
            if pdf_key in tech_info and tech_info[pdf_key]:
                pdf_data = tech_info[pdf_key]
                file_type = "2.0" if "20" in pdf_key else "5.1"
                
                # LUFS
                lufs = pdf_data.get('lufs')
                if lufs is not None and abs(lufs - target_lufs) > lufs_tolerance:
                    problems.append(
                        f"Интегральная громкость (LUFS) фонограммы {file_type} "
                        f"({lufs:.1f} LUFS) отклоняется от номинального значения ({target_lufs:.1f} LUFS)"
                    )
                
                # TRUE PEAK
                true_peak = pdf_data.get('true_peak')
                if true_peak is not None and true_peak > target_peak:
                    problems.append(
                        f"Максимальный пик (TRUE PEAK) фонограммы {file_type} "
                        f"({true_peak:.1f} dBTP) превышает допустимое значение ({target_peak:.1f} dBTP)"
                    )
                
                # LRA
                lra = pdf_data.get('lra')
                if lra is not None and lra > target_lra:
                    problems.append(
                        f"Динамический диапазон (LRA) фонограммы {file_type} "
                        f"({lra:.1f} LU) превышает допустимое значение ({target_lra:.1f} LU)"
                    )
        
        # Формируем заключение
        if not problems:
            return "По техническим характеристикам нареканий не выявлено"
        
        # Если включен LLM - генерируем через Ollama
        if self.use_llm:
            try:
                return self._generate_technical_with_llm(problems)
            except Exception as e:
                logger.error(f"Ошибка генерации через Ollama: {e}")
                logger.info("Переключаемся на шаблонную генерацию")
                # Продолжаем с шаблонным методом
        
        # Шаблонная генерация
        conclusion = "По техническим характеристикам выявлены следующие недочёты:\n"
        conclusion += "\n".join(f"- {problem}" for problem in problems)
        
        logger.info(f"Техническое заключение: {len(problems)} проблем")
        return conclusion
    
    def generate_subjective_conclusion(self, issues: List[Issue]) -> str:
        """
        Генерация субъективного заключения на основе списка проблем
        
        Args:
            issues: Список проблем из CSV
            
        Returns:
            Текст субъективного заключения
        """
        if not issues:
            return "По субъективной оценке нареканий не выявлено"
        
        # Если включен LLM - генерируем через Ollama
        if self.use_llm:
            try:
                return self._generate_subjective_with_llm(issues)
            except Exception as e:
                logger.error(f"Ошибка генерации через Ollama: {e}")
                logger.info("Переключаемся на шаблонную генерацию")
                # Продолжаем с шаблонным методом
        
        # Группируем проблемы по типам
        problem_groups = self._group_issues_by_type(issues)
        
        # Убираем "другие проблемы" из основного списка
        other_problems = problem_groups.pop('другие проблемы', [])
        
        # Формируем список недочетов
        problem_list = []
        
        # Обрабатываем каждую группу
        for problem_type, items in sorted(problem_groups.items(), key=lambda x: len(x[1]), reverse=True):
            # Специальная обработка для щелчков и слюны
            if problem_type == 'щелчки_и_слюна':
                description = self._format_clicks_and_saliva(items)
                if len(items) == 1:
                    issue = items[0]
                    # Для единичного случая убираем "в фонограмме"
                    desc_single = description.replace("в фонограмме ", "")
                    problem_list.append(f"На таймкоде {issue.timecode_in} {desc_single}")
                elif len(items) <= 3:
                    # 2-3 случая - перечисляем таймкоды
                    timecodes = " и ".join([item.timecode_in for item in items])
                    desc_multiple = description.replace("в фонограмме ", "")
                    problem_list.append(f"На таймкодах {timecodes} {desc_multiple}")
                else:
                    # Много случаев - обобщаем с "В фонограмме"
                    problem_list.append(f"{description.capitalize()}")
            elif len(items) == 1:
                # Единичная проблема - указываем с таймкодом
                issue = items[0]
                problem_list.append(
                    f"На таймкоде {issue.timecode_in} присутствует {problem_type}"
                )
            elif len(items) <= 3:
                # 2-3 случая - перечисляем таймкоды
                timecodes = " и ".join([item.timecode_in for item in items])
                problem_list.append(
                    f"На таймкодах {timecodes} присутствует {problem_type}"
                )
            else:
                # Повторяющаяся проблема - обобщаем БЕЗ количества
                problem_list.append(f"В фонограмме присутствует {problem_type}")
        
        # Обрабатываем "другие проблемы"
        for issue in other_problems:
            # Каждую "другую проблему" показываем с таймкодом
            problem_list.append(
                f"На таймкоде {issue.timecode_in}: {issue.description.lower()}"
            )
        
        # Добавляем информацию о критичности
        blockers = sum(1 for i in issues if i.blocker)
        fix_required = sum(1 for i in issues if i.fix_required)
        
        if blockers > 0:
            problem_list.append(f"Обнаружено {blockers} критических дефектов, требующих обязательного исправления")
        elif fix_required > 5:
            problem_list.append(f"Выявлено {fix_required} замечаний, требующих исправления")
        
        # Формируем финальное заключение
        conclusion = "По субъективной оценке выявлены следующие недочёты:\n"
        conclusion += "\n".join(f"- {problem}" for problem in problem_list)
        
        logger.info(f"Субъективное заключение: {len(issues)} проблем")
        return conclusion
    
    def _generate_technical_with_llm(self, problems: list) -> str:
        """
        Генерация технического заключения через Ollama
        
        Args:
            problems: Список выявленных технических проблем
            
        Returns:
            Сгенерированное заключение
        """
        try:
            import ollama
            
            problems_text = "\n".join(f"- {problem}" for problem in problems)
            
            prompt = f"""Ты - эксперт по техническому контролю качества аудио/видео материалов. Составь техническое заключение по выявленным проблемам.

ВАЖНО: Начни заключение СТРОГО с фразы "По техническим характеристикам выявлены следующие недочёты:"

ВЫЯВЛЕННЫЕ ТЕХНИЧЕСКИЕ ПРОБЛЕМЫ:
{problems_text}

ТРЕБОВАНИЯ К ЗАКЛЮЧЕНИЮ:
1. Начни СТРОГО с "По техническим характеристикам выявлены следующие недочёты:"
2. После заголовка перейди на новую строку
3. Каждую проблему начинай с "- " (дефис с пробелом)
4. Используй простые, понятные формулировки
5. Сохраняй все технические значения и параметры из исходных проблем
6. Стиль: лаконичный, технический, но доступный

СТИЛЬ ФОРМУЛИРОВОК:
- Простой и понятный язык
- Короткие фразы без излишних деталей
- Сохраняй точные значения (LUFS, dBTP, fps и т.д.)
- НЕ добавляй выводы или рекомендации

ПРИМЕРЫ ИЗ РЕАЛЬНОГО ОТЧЁТА:
- Хронометраж видеофайла и аудиодорожек не совпадает
- Порядок каналов в 5.1 аудиодорожке некорректный
- Интегральная громкость (LUFS) фонограммы 2.0 (-22.3 LUFS) отклоняется от номинального значения (-23.0 LUFS)
- Длительность файла 2.0 cens не кратна кадру (25 fps, длительность кадра 40.00 мс)

Сгенерируй заключение:"""
            
            logger.info("Генерация технического заключения через Ollama...")
            
            response = ollama.generate(
                model='llama3.2',
                prompt=prompt,
                options={
                    'temperature': 0.2,  # Очень низкая температура для технических текстов
                    'num_predict': 400
                }
            )
            
            conclusion = response['response'].strip()
            
            # Проверяем, что заключение начинается правильно
            if not conclusion.startswith("По техническим характеристикам"):
                conclusion = "По техническим характеристикам выявлены следующие недочеты:\n" + conclusion
            
            logger.info("Техническое заключение сгенерировано через Ollama")
            return conclusion
            
        except Exception as e:
            logger.error(f"Ошибка Ollama при генерации технического заключения: {e}")
            raise
    
    def _generate_subjective_with_llm(self, issues: List[Issue]) -> str:
        """
        Генерация субъективного заключения через Ollama
        
        Args:
            issues: Список проблем из CSV
            
        Returns:
            Сгенерированное заключение
        """
        try:
            import ollama
            
            # Группируем проблемы для анализа
            problem_groups = self._group_issues_by_type(issues)
            
            # Формируем краткое описание проблем
            problem_summary = []
            for problem_type, items in sorted(problem_groups.items(), key=lambda x: len(x[1]), reverse=True):
                if problem_type != 'другие проблемы':
                    problem_summary.append(f"- {problem_type}: {len(items)} случаев")
                    # Добавляем примеры таймкодов
                    examples = [f"{item.timecode_in}" for item in items[:2]]
                    problem_summary.append(f"  Примеры: {', '.join(examples)}")
            
            # Добавляем "другие проблемы" с описаниями
            if 'другие проблемы' in problem_groups:
                for item in problem_groups['другие проблемы'][:3]:
                    problem_summary.append(f"- {item.timecode_in}: {item.description}")
            
            # Считаем критичность
            blockers = sum(1 for i in issues if i.blocker)
            fix_required = sum(1 for i in issues if i.fix_required)
            
            prompt = f"""Ты - эксперт по аудио контролю качества. Составь субъективное заключение по выявленным проблемам в фонограмме.

ВАЖНО: Начни заключение СТРОГО с фразы "По субъективной оценке выявлены следующие недочёты:"

ВЫЯВЛЕННЫЕ ПРОБЛЕМЫ:
{chr(10).join(problem_summary)}

СТАТИСТИКА:
- Всего проблем: {len(issues)}
- Критических (блокеров): {blockers}
- Требуют исправления: {fix_required}

ТРЕБОВАНИЯ К ЗАКЛЮЧЕНИЮ:
1. Начни СТРОГО с "По субъективной оценке выявлены следующие недочёты:"
2. После заголовка перейди на новую строку
3. Каждую проблему начинай с "- " (дефис с пробелом)
4. Используй простой, понятный язык без излишней технической терминологии

ПРАВИЛА ФОРМУЛИРОВОК:
• Если проблема встречается 1 раз - укажи таймкод в начале:
  "- На таймкоде 01:23:45:12 присутствует [описание]"
  
• Если проблема на 2-3 таймкодах - перечисли их через "и":
  "- На таймкодах 01:23:45:12 и 01:45:23:08 [описание]"
  
• Если проблема повторяется много раз - обобщи БЕЗ указания количества:
  "- В фонограмме присутствуют посторонние щёлкающие звуки"
  "- В фонограмме слишком высокое низкочастотное шипение на репликах актёров"
  
• Для щелчков и слюны вместе:
  "- В фонограмме присутствуют посторонние щёлкающие звуки и яркие звуки слюны"

СТИЛЬ:
- Простой, лаконичный, без технических терминов
- Описательный: "слишком высокое", "прерывисто", "неоднородно"
- НЕ указывай количество случаев в скобках
- НЕ добавляй выводы или рекомендации

ПРИМЕРЫ ИЗ РЕАЛЬНОГО ОТЧЁТА:
- Отсутствует звук на заставках "Кинопоиск" и "Студия плюс"
- Звуковая дорожка начинается на таймкоде 01:00:07:12
- В фонограмме слишком высокое низкочастотное шипение на репликах актёров
- В фонограмме присутствуют посторонние щёлкающие звуки
- Некоторые реплики звучат прерывисто. Ощущение, что есть перегруз
- На таймкодах 01:11:42:06 и 01:14:44:22 реплики актёра выглядят неоднородно с изображением

Сгенерируй заключение:"""
            
            logger.info("Генерация субъективного заключения через Ollama...")
            
            response = ollama.generate(
                model='llama3.2',
                prompt=prompt,
                options={
                    'temperature': 0.3,  # Низкая температура для большей предсказуемости
                    'num_predict': 500   # Ограничение длины ответа
                }
            )
            
            conclusion = response['response'].strip()
            
            # Проверяем, что заключение начинается правильно
            if not conclusion.startswith("По субъективной оценке"):
                conclusion = "По субъективной оценке выявлены следующие недочеты:\n" + conclusion
            
            logger.info("Субъективное заключение сгенерировано через Ollama")
            return conclusion
            
        except Exception as e:
            logger.error(f"Ошибка Ollama при генерации субъективного заключения: {e}")
            raise
    
    def _format_clicks_and_saliva(self, items: List[Issue]) -> str:
        """
        Форматирование описания для щелчков и слюны
        
        Args:
            items: Список проблем из категории щелчков и слюны
            
        Returns:
            Отформатированное описание
        """
        has_clicks = False
        has_saliva = False
        
        click_keywords = ['клик', 'щелч', 'щёлк', 'click']
        saliva_keywords = ['слюна', 'слюн']
        
        for item in items:
            desc_lower = item.description.lower()
            if any(key in desc_lower for key in click_keywords):
                has_clicks = True
            if any(key in desc_lower for key in saliva_keywords):
                has_saliva = True
        
        # Формируем описание в зависимости от того, что найдено
        if has_clicks and has_saliva:
            return "в фонограмме присутствуют посторонние щёлкающие звуки и яркие звуки слюны"
        elif has_saliva:
            return "в фонограмме присутствуют яркие звуки слюны"
        else:
            return "в фонограмме присутствуют посторонние щёлкающие звуки"
    
    def _group_issues_by_type(self, issues: List[Issue]) -> dict:
        """
        Группировка проблем по типам
        
        Args:
            issues: Список проблем
            
        Returns:
            Словарь {тип_проблемы: [список_проблем]}
        """
        groups = {}
        
        # Ключевые слова для категоризации
        keywords = {
            'щелчки_и_слюна': ['клик', 'щелч', 'щёлк', 'click', 'слюна', 'слюн'],
            'шипение': ['шип', 'шипение'],
            'треск': ['треск'],
            'перегрузка': ['перегруз', 'клиппинг', 'clipping'],
            'несинхронность': ['синхрон', 'синхр', 'несинх'],
            'отсутствие звука': ['отсутств', 'нет звука', 'тишина'],
            'искажения': ['искажен', 'distortion'],
            'шумы': ['шум', 'noise'],
            'стук': ['стук'],
            'артефакты': ['артефакт', 'artifact'],
        }
        
        # Группируем по типам
        for issue in issues:
            desc_lower = issue.description.lower()
            matched = False
            
            for group_name, keys in keywords.items():
                if any(key in desc_lower for key in keys):
                    if group_name not in groups:
                        groups[group_name] = []
                    groups[group_name].append(issue)
                    matched = True
                    break
            
            # Если не подошла ни одна категория
            if not matched:
                if 'другие проблемы' not in groups:
                    groups['другие проблемы'] = []
                groups['другие проблемы'].append(issue)
        
        return groups
    
    def generate_conclusion(self, issues: List[Issue], categories: dict, tech_info: dict = None) -> str:
        """
        Генерация заключения на основе списка проблем
        
        Args:
            issues: Список проблем из CSV
            categories: Категоризация проблем
            tech_info: Техническая информация (опционально)
            
        Returns:
            Текст заключения
        """
        if self.use_llm:
            try:
                return self._generate_with_llm(issues, categories, tech_info)
            except Exception as e:
                logger.error(f"Ошибка генерации через LLM: {e}")
                logger.info("Переключаемся на шаблонную генерацию")
                return self._generate_template_based(issues, categories, tech_info)
        else:
            return self._generate_template_based(issues, categories, tech_info)
    
    def _generate_with_llm(self, issues: List[Issue], categories: dict, tech_info: dict = None) -> str:
        """
        Генерация заключения с помощью LLM (Ollama)
        
        Args:
            issues: Список проблем
            categories: Категоризация
            tech_info: Техническая информация
            
        Returns:
            Сгенерированное заключение
        """
        try:
            import ollama
            
            # Подготавливаем контекст для LLM
            prompt = self._prepare_llm_prompt(issues, categories, tech_info)
            
            logger.info("Генерация заключения через Ollama...")
            
            response = ollama.generate(
                model='llama3.2',
                prompt=prompt
            )
            
            conclusion = response['response'].strip()
            
            logger.info("Заключение сгенерировано через LLM")
            
            return conclusion
            
        except Exception as e:
            logger.error(f"Ошибка Ollama: {e}")
            raise
    
    def _prepare_llm_prompt(self, issues: List[Issue], categories: dict, tech_info: dict = None) -> str:
        """
        Подготовка промпта для LLM
        
        Args:
            issues: Список проблем
            categories: Категоризация
            tech_info: Техническая информация
            
        Returns:
            Текст промпта
        """
        # Суммаризация проблем
        issue_summary = []
        
        for issue in issues[:10]:  # Первые 10 для примера
            issue_summary.append(f"- {issue.timecode_in}: {issue.description}")
        
        prompt = f"""Ты - эксперт по аудио контролю качества. На основе списка выявленных проблем составь краткое профессиональное заключение на русском языке.

СТАТИСТИКА:
- Всего проблем: {categories['total']}
- Блокеров (критических): {len(categories['blockers'])}
- Требуют исправления: {len(categories['fix_required'])}
- Требуют комментария: {len(categories['comment_required'])}

ПРИМЕРЫ ПРОБЛЕМ:
{chr(10).join(issue_summary)}

ТРЕБОВАНИЯ К ЗАКЛЮЧЕНИЮ:
1. Объем: 3-5 предложений
2. Стиль: профессиональный, формальный
3. Укажи общую оценку качества аудио
4. Укажи основные категории проблем
5. Дай рекомендации (исправить/принять к сведению)

ЗАКЛЮЧЕНИЕ:"""
        
        return prompt
    
    def _generate_template_based(self, issues: List[Issue], categories: dict, tech_info: dict = None) -> str:
        """
        Генерация заключения на основе шаблонов (fallback без LLM)
        
        Args:
            issues: Список проблем
            categories: Категоризация
            tech_info: Техническая информация
            
        Returns:
            Сгенерированное заключение
        """
        total = categories['total']
        blockers = len(categories['blockers'])
        fix_required = len(categories['fix_required'])
        comment_required = len(categories['comment_required'])
        
        # Определяем общую оценку
        if blockers > 0:
            quality_assessment = "выявлены критические проблемы, требующие обязательного исправления"
        elif fix_required > 10:
            quality_assessment = "выявлено значительное количество проблем, требующих исправления"
        elif fix_required > 0:
            quality_assessment = "выявлены проблемы, требующие исправления"
        else:
            quality_assessment = "качество в целом соответствует требованиям, выявлены незначительные замечания"
        
        # Категоризация проблем по типам
        problem_types = self._analyze_problem_types(issues)
        
        # Формируем заключение
        conclusion_parts = []
        
        # Вступление
        conclusion_parts.append(
            f"В результате контроля качества аудиодорожек было обнаружено {total} замечаний."
        )
        
        # Общая оценка
        conclusion_parts.append(
            f"По результатам проверки {quality_assessment}."
        )
        
        # Детали по категориям
        if blockers > 0:
            conclusion_parts.append(
                f"Обнаружено {blockers} критических дефектов (блокеров), препятствующих выпуску материала."
            )
        
        if fix_required > 0:
            conclusion_parts.append(
                f"Выявлено {fix_required} замечаний, требующих исправления."
            )
        
        if comment_required > 0:
            conclusion_parts.append(
                f"Зафиксировано {comment_required} замечаний, требующих комментария или принятия решения."
            )
        
        # Основные типы проблем
        if problem_types:
            types_str = ", ".join([f"{ptype} ({count})" for ptype, count in list(problem_types.items())[:3]])
            conclusion_parts.append(
                f"Основные выявленные проблемы: {types_str}."
            )
        
        # Рекомендации
        if blockers > 0:
            conclusion_parts.append(
                "Рекомендуется устранить все критические дефекты перед выпуском материала."
            )
        elif fix_required > 5:
            conclusion_parts.append(
                "Рекомендуется исправить выявленные замечания для повышения качества материала."
            )
        else:
            conclusion_parts.append(
                "Материал может быть принят после рассмотрения и согласования выявленных замечаний."
            )
        
        conclusion = " ".join(conclusion_parts)
        
        logger.info("Заключение сгенерировано по шаблону")
        
        return conclusion
    
    def _analyze_problem_types(self, issues: List[Issue]) -> dict:
        """
        Анализ типов проблем
        
        Args:
            issues: Список проблем
            
        Returns:
            Словарь с подсчетом типов проблем
        """
        types = {}
        
        keywords = {
            'клик': 'щёлкающий',
            'шипение': 'шипение',
            'перегруз': 'перегруз',
            'треск': 'треск',
            'синхронизация': 'несинхронно',
            'отсутствие звука': 'отсутствует звук',
            'каналы': 'перепутана последовательность каналов',
            'склейка': 'склейка'
        }
        
        for issue in issues:
            desc_lower = issue.description.lower()
            for key, keyword in keywords.items():
                if keyword in desc_lower:
                    types[key] = types.get(key, 0) + 1
        
        # Сортируем по количеству
        sorted_types = dict(sorted(types.items(), key=lambda x: x[1], reverse=True))
        
        return sorted_types


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    from src.csv_importer import CSVImporter
    
    # Импортируем проблемы
    importer = CSVImporter()
    issues = importer.import_issues('/Users/vladog/Desktop/ШАБЛОН/petr_2_s1_e2_2024_09_12_rus.csv')
    categories = importer.categorize_issues(issues)
    
    # Генерируем заключение
    generator = ConclusionGenerator(use_llm=False)  # Без LLM для теста
    conclusion = generator.generate_conclusion(issues, categories)
    
    print("\n=== ЗАКЛЮЧЕНИЕ ===\n")
    print(conclusion)
    print("\n" + "="*50 + "\n")


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
        
        # Проверяем LUFS, TRUE PEAK, LRA для PDF файлов
        lufs_issues_20 = []
        lufs_issues_51 = []
        peak_issues_20 = []
        peak_issues_51 = []
        lra_issues_20 = []
        lra_issues_51 = []
        
        for pdf_key in ['pdf_20_c', 'pdf_20_uc', 'pdf_20', 'pdf_51_c', 'pdf_51_uc', 'pdf_51']:
            if pdf_key in tech_info and tech_info[pdf_key]:
                pdf_data = tech_info[pdf_key]
                is_20 = "20" in pdf_key
                
                # LUFS
                lufs = pdf_data.get('lufs')
                if lufs is not None and abs(lufs - target_lufs) > lufs_tolerance:
                    if is_20:
                        lufs_issues_20.append(lufs)
                    else:
                        lufs_issues_51.append(lufs)
                
                # TRUE PEAK
                true_peak = pdf_data.get('true_peak')
                if true_peak is not None and true_peak > target_peak:
                    if is_20:
                        peak_issues_20.append(true_peak)
                    else:
                        peak_issues_51.append(true_peak)
                
                # LRA
                lra = pdf_data.get('lra')
                if lra is not None and lra > target_lra:
                    if is_20:
                        lra_issues_20.append(lra)
                    else:
                        lra_issues_51.append(lra)
        
        # Формируем проблемы по интегральной громкости
        if lufs_issues_20 and lufs_issues_51:
            problems.append("Параметр «интегральная громкость» не соответствует допустимым значениям в фонограммах 2.0 и 5.1")
        elif lufs_issues_20:
            problems.append("Параметр «интегральная громкость» не соответствует допустимым значениям в фонограмме 2.0")
        elif lufs_issues_51:
            problems.append("Параметр «интегральная громкость» не соответствует допустимым значениям в фонограмме 5.1")
        
        # Формируем проблемы по пиковым значениям
        if peak_issues_20 and peak_issues_51:
            problems.append("Параметр «пиковые значения» превышает допустимое значение в фонограммах 2.0 и 5.1")
        elif peak_issues_20:
            problems.append("Параметр «пиковые значения» превышает допустимое значение в фонограмме 2.0")
        elif peak_issues_51:
            problems.append("Параметр «пиковые значения» превышает допустимое значение в фонограмме 5.1")
        
        # Формируем проблемы по диапазону громкости
        if lra_issues_20 and lra_issues_51:
            problems.append("Параметр «диапазон громкости» превышает допустимое значение в фонограммах 2.0 и 5.1")
        elif lra_issues_20:
            problems.append("Параметр «диапазон громкости» превышает допустимое значение в фонограмме 2.0")
        elif lra_issues_51:
            problems.append("Параметр «диапазон громкости» превышает допустимое значение в фонограмме 5.1")
        
        # Проверяем порядок каналов для 5.1
        for audio_key in ['audio_51_c', 'audio_51_uc']:
            if audio_key in tech_info and tech_info[audio_key]:
                data = tech_info[audio_key]
                channel_order = data.get('channel_order', '')
                # Проверяем, заполнен ли порядок каналов (если нет запятых, значит неполный)
                if channel_order and ',' not in channel_order and 'Stereo' not in channel_order:
                    problems.append("Некорректный порядок каналов в 5.1 дорожке")
                    break
        
        # Проверяем хронометраж
        durations = {}
        has_video = False
        has_audio = False
        
        for key in ['audio_20_c', 'audio_51_c', 'audio_20_uc', 'audio_51_uc', 'video']:
            if key in tech_info and tech_info[key]:
                data = tech_info[key]
                duration = data.get('duration')
                if duration and duration > 0:
                    durations[key] = duration
                    if key == 'video':
                        has_video = True
                    elif key.startswith('audio'):
                        has_audio = True
        
        # Проверяем совпадение хронометража
        if len(durations) > 1:
            duration_list = list(durations.values())
            reference_duration = duration_list[0]
            
            # Проверяем несовпадение видео и аудио
            video_audio_mismatch = False
            audio_mismatch = False
            
            if has_video and has_audio:
                video_dur = durations.get('video', 0)
                for key, dur in durations.items():
                    if key.startswith('audio'):
                        if abs(dur - video_dur) > 0.1:  # Допуск 100 мс
                            video_audio_mismatch = True
                            break
            
            # Проверяем несовпадение между аудиофайлами
            audio_durations = [dur for key, dur in durations.items() if key.startswith('audio')]
            if len(audio_durations) > 1:
                ref_audio = audio_durations[0]
                for dur in audio_durations[1:]:
                    if abs(dur - ref_audio) > 0.1:
                        audio_mismatch = True
                        break
            
            if video_audio_mismatch:
                problems.append("Хронометраж видеофайла и аудиодорожек не совпадает")
            elif audio_mismatch:
                problems.append("Звуковые файлы имеют разный хронометраж")
        
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
        
        # Формируем заключение
        if not problems:
            return "По технической оценке нареканий не обнаружено"
        
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
            return "По субъективной оценке нареканий не обнаружено"
        
        # Если включен LLM - генерируем через Ollama (ВСЕГДА приоритет)
        if self.use_llm:
            try:
                return self._generate_subjective_with_llm(issues)
            except Exception as e:
                logger.error(f"Ошибка генерации через Ollama: {e}")
                logger.warning("LLM недоступен, используем упрощенное заключение")
                # Fallback - простое перечисление
                return self._generate_simple_fallback(issues)
        else:
            # Без LLM - простое перечисление
            return self._generate_simple_fallback(issues)
    
    def _generate_simple_fallback(self, issues: List[Issue]) -> str:
        """
        Упрощенное заключение без LLM (fallback)
        
        Args:
            issues: Список проблем
            
        Returns:
            Простое заключение со списком проблем
        """
        # Разделяем блокеры и обычные проблемы
        blockers = [issue for issue in issues if issue.blocker]
        regular_issues = [issue for issue in issues if not issue.blocker]
        
        problem_list = []
        
        # Блокеры всегда с таймкодами
        for blocker in blockers:
            problem_list.append(f"На таймкоде {blocker.timecode_in}: {blocker.description}")
        
        # Обычные проблемы - все с таймкодами (простой fallback)
        for issue in regular_issues:
            problem_list.append(f"На таймкоде {issue.timecode_in}: {issue.description}")
        
        conclusion = "По субъективной оценке выявлены следующие недочёты:\n"
        conclusion += "\n".join(f"- {problem}" for problem in problem_list)
        
        logger.info(f"Субъективное заключение (fallback): {len(issues)} проблем")
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
        Генерация субъективного заключения через Ollama (ЧИСТЫЙ промпт)
        
        Args:
            issues: Список проблем из CSV
            
        Returns:
            Сгенерированное заключение
        """
        try:
            import ollama
            
            # Разделяем блокеры и обычные проблемы
            blockers = [issue for issue in issues if issue.blocker]
            regular_issues = [issue for issue in issues if not issue.blocker]
            
            # Формируем простой список проблем
            all_problems = []
            
            if blockers:
                all_problems.append("БЛОКЕРЫ (критические):")
                for b in blockers:
                    all_problems.append(f"  {b.timecode_in} - {b.description}")
                all_problems.append("")
            
            if regular_issues:
                all_problems.append("ОБЫЧНЫЕ ПРОБЛЕМЫ:")
                for r in regular_issues:
                    all_problems.append(f"  {r.timecode_in} - {r.description}")
            
            problems_text = "\n".join(all_problems)
            
            prompt = f"""Проанализируй проблемы и составь заключение.

{problems_text}

ПРАВИЛА:
1. Начни с: "По субъективной оценке выявлены следующие недочёты:"
2. Блокеры - ПЕРВЫМИ, формат: "- На таймкоде 01:00:10:08: [описание]"
3. Если проблема 1 раз → с таймкодом: "- На таймкоде XX:XX:XX:XX присутствует..."
4. Если 2-3 раза И важная → перечисли таймкоды: "- На таймкодах 01:19:09:23, 01:19:25:20 и 01:21:34:16 присутствуют реплики, которые выглядят несинхронно"
5. Если 4+ раз → обобщи БЕЗ таймкодов: "- В фонограмме присутствуют посторонние щёлкающие звуки"
6. Щелчки И слюна (если обоих 4+) → "- В фонограмме присутствуют посторонние щёлкающие звуки и яркие звуки слюны"

Примеры фраз:
- В фонограмме слышно высокочастотное шипение на репликах актёров
- Некоторые реплики звучат пережато. Ощущение, что есть перегруз
- В нескольких фрагментах изменяется реверберация на речи актёров
- Есть ряд не исправленных маркеров (не исправлена маскировка...)

ТОЛЬКО заключение, БЕЗ пояснений:"""
            
            logger.info("Генерация субъективного заключения через Ollama...")
            
            response = ollama.generate(
                model='llama3.2',
                prompt=prompt,
                options={
                    'temperature': 0.2,
                    'num_predict': 800
                }
            )
            
            conclusion = response['response'].strip()
            
            # Убираем лишнее если LLM добавил
            lines = conclusion.split('\n')
            clean_lines = []
            for line in lines:
                # Пропускаем строки с инструкциями
                if any(x in line.lower() for x in ['правила', 'примеры', 'важно:', 'критически', 'сложные проблемы']):
                    continue
                clean_lines.append(line)
            
            conclusion = '\n'.join(clean_lines).strip()
            
            # Проверяем начало
            if not conclusion.startswith("По субъективной оценке"):
                conclusion = "По субъективной оценке выявлены следующие недочёты:\n\n" + conclusion
            
            logger.info("Субъективное заключение сгенерировано через Ollama")
            return conclusion
            
        except Exception as e:
            logger.error(f"Ошибка Ollama при генерации субъективного заключения: {e}")
            raise
    
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


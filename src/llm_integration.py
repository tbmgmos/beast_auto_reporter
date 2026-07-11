"""
LLM Integration Module

Legacy-слой интеграции с локальными LLM моделями через Ollama.
Оставлен для старого Streamlit-пайплайна, но использует общий OllamaService,
и актуальный ConclusionGenerator, чтобы не дублировать логику промптов,
валидации и сетевой конфигурации.
"""

from typing import Dict, List
import logging

from src.conclusion_generator import ConclusionGenerator
from src.csv_importer import Issue
from src.ollama_service import OllamaService

logger = logging.getLogger(__name__)


class LLMIntegration:
    """Совместимая legacy-обёртка над актуальным ConclusionGenerator."""
    
    def __init__(self, config: Dict = None):
        """
        Инициализация LLM интеграции
        
        Args:
            config: Словарь с настройками
        """
        self.config = config or {}
        llm_cfg = self.config.get('llm', {})
        
        self.model = llm_cfg.get('model', 'gemma4:12b')
        self.temperature = llm_cfg.get('temperature', 0.7)
        self.max_tokens = llm_cfg.get('max_tokens', 2000)
        self.language = llm_cfg.get('language', 'ru')
        self.ollama_host = llm_cfg.get('ollama_host', 'http://localhost:11434')
        self.ollama_service = OllamaService(self.config)
        self.conclusion_generator = ConclusionGenerator(use_llm=True, config=self.config)
        
        logger.info(f"LLM Integration инициализирован (модель: {self.model})")
    
    def check_ollama_status(self) -> bool:
        """
        Проверка доступности Ollama
        
        Returns:
            True если Ollama доступна
        """
        return self.ollama_service.check_status()
    
    def generate_conclusion(
        self, 
        audio_analysis: Dict, 
        defects: List, 
        file_info: Dict
    ) -> str:
        """
        Генерация заключения на основе анализа
        
        Args:
            audio_analysis: Результаты измерений (LUFS, TP, LRA)
            defects: Список обнаруженных дефектов
            file_info: Информация о файле
            
        Returns:
            Сгенерированное заключение
        """
        try:
            issues = self._convert_defects_to_issues(defects)
            subjective = self.conclusion_generator.generate_subjective_conclusion(issues, "main")
            technical = self._build_technical_summary(audio_analysis, file_info)

            if subjective == "По субъективной оценке нареканий не обнаружено.":
                return technical

            return f"{technical}\n\n{subjective}"
        except Exception as e:
            logger.error(f"Ошибка генерации заключения: {e}")
            return self._fallback_conclusion(audio_analysis, defects)

    def _convert_defects_to_issues(self, defects: List) -> List[Issue]:
        """Адаптация legacy Defect объектов к Issue для ConclusionGenerator."""
        issues = []

        for defect in defects:
            severity = getattr(defect, 'severity', 'comment_required')
            channels = getattr(defect, 'channels', []) or []

            issues.append(Issue(
                timecode_in=getattr(defect, 'timecode_in', '') or '',
                timecode_out=getattr(defect, 'timecode_out', '') or '',
                description=getattr(defect, 'description', '') or '',
                audio_20_c='2.0' in channels or '*' in channels,
                audio_20_uc=False,
                audio_51_c='5.1' in channels or '*' in channels,
                audio_51_uc=False,
                blocker=severity == 'blocker',
                fix_required=severity == 'fix_required',
                comment_required=severity == 'comment_required',
                comments='',
            ))

        return issues

    def _build_technical_summary(self, audio_analysis: Dict, file_info: Dict) -> str:
        """
        Краткий верхний блок для legacy Streamlit-пути.
        Это не AI-логика, а совместимый технический summary перед субъективным списком.
        """
        compliance = audio_analysis.get('compliance', {})
        measurements = audio_analysis.get('measurements', {})

        file_name = file_info.get('file_name') or audio_analysis.get('file_name') or 'N/A'
        lufs = measurements.get('lufs', 'N/A')
        tp = measurements.get('true_peak', 'N/A')
        lra = measurements.get('lra', 'N/A')

        problems = []
        if not compliance.get('lufs_compliant', True):
            problems.append(f"LUFS не соответствует норме ({lufs})")
        if not compliance.get('true_peak_compliant', True):
            problems.append(f"TRUE PEAK не соответствует норме ({tp})")
        if not compliance.get('lra_compliant', True):
            problems.append(f"LRA не соответствует норме ({lra})")

        if problems:
            return (
                f"Файл: {file_name}\n"
                f"По техническим характеристикам выявлены следующие недочёты:\n"
                + "\n".join(f"- {problem}" for problem in problems)
            )

        return (
            f"Файл: {file_name}\n"
            "По техническим характеристикам нареканий не обнаружено."
        )
    
    def _fallback_conclusion(self, audio_analysis: Dict, defects: List) -> str:
        """Резервное заключение если основной путь недоступен."""
        
        measurements = audio_analysis.get('measurements', {})
        compliance = audio_analysis.get('compliance', {})
        
        conclusion = f"""ЗАКЛЮЧЕНИЕ О КАЧЕСТВЕ ЗВУКА

Общая информация:
- Файл: {audio_analysis.get('file_name', 'N/A')}
- Длительность: {audio_analysis.get('duration', 0)} сек
- Формат: {audio_analysis.get('channel_layout', 'N/A')}

Измерения громкости (EBU R128):
- LUFS: {measurements.get('lufs', 'N/A')} dB {'✓' if compliance.get('lufs_compliant') else '✗'}
- TRUE PEAK: {measurements.get('true_peak', 'N/A')} dBTP {'✓' if compliance.get('true_peak_compliant') else '✗'}
- LRA: {measurements.get('lra', 'N/A')} LU {'✓' if compliance.get('lra_compliant') else '✗'}

Обнаружено дефектов: {len(defects)}

{'Материал соответствует стандартам качества.' if compliance.get('overall_compliant') else 'Материал требует доработки перед использованием.'}

Примечание: Это автоматически сгенерированное заключение. 
Для детального анализа обратитесь к таблице дефектов.
"""
        
        return conclusion
    
    def generate_defect_description(self, defect_type: str, details: Dict) -> str:
        """
        Генерация описания дефекта (можно улучшить через LLM)
        
        Args:
            defect_type: Тип дефекта
            details: Детали дефекта
            
        Returns:
            Описание
        """
        # Простые шаблоны (можно заменить на LLM генерацию)
        templates = {
            'clipping': "В данном фрагменте {item} звучит пережато. Ощущение, что есть перегруз",
            'click': "Посторонний щёлкающий звук{location}",
            'high_frequency_noise': "В данном фрагменте слышно высокочастотное шипение на {item}",
            'silence': "В данном фрагменте отсутствует звук",
            'channel_order': "В звуковой дорожке 5.1 перепутана последовательность каналов"
        }
        
        template = templates.get(defect_type, "Обнаружен дефект типа {type}")
        
        # Заполнение шаблона
        if defect_type == 'clipping':
            item = details.get('item', 'реплика актёра')
            return template.format(item=item)
        elif defect_type == 'click':
            location = details.get('location', '')
            return template.format(location=f" {location}" if location else "")
        elif defect_type == 'high_frequency_noise':
            item = details.get('item', 'реплике актёра')
            return template.format(item=item)
        else:
            return template.format(type=defect_type)


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    config = {
        'llm': {
            'model': 'gemma4:12b',
            'temperature': 0.7,
            'language': 'ru'
        }
    }
    
    llm = LLMIntegration(config)
    
    # Проверка доступности Ollama
    if llm.check_ollama_status():
        print("✓ Ollama доступна и готова к работе")
    else:
        print("✗ Ollama недоступна. Будет использоваться fallback режим")
    
    print("LLMIntegration готов к использованию!")

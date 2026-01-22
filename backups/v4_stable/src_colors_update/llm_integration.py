"""
LLM Integration Module

Интеграция с локальными LLM моделями через Ollama
для генерации профессиональных заключений
"""

import ollama
from typing import Dict, List, Optional
import logging
import json

logger = logging.getLogger(__name__)


class LLMIntegration:
    """Класс для работы с локальными LLM"""
    
    def __init__(self, config: Dict = None):
        """
        Инициализация LLM интеграции
        
        Args:
            config: Словарь с настройками
        """
        self.config = config or {}
        llm_cfg = self.config.get('llm', {})
        
        self.model = llm_cfg.get('model', 'llama3.2')
        self.temperature = llm_cfg.get('temperature', 0.7)
        self.max_tokens = llm_cfg.get('max_tokens', 2000)
        self.language = llm_cfg.get('language', 'ru')
        self.ollama_host = llm_cfg.get('ollama_host', 'http://localhost:11434')
        
        logger.info(f"LLM Integration инициализирован (модель: {self.model})")
    
    def check_ollama_status(self) -> bool:
        """
        Проверка доступности Ollama
        
        Returns:
            True если Ollama доступна
        """
        try:
            models = ollama.list()
            logger.info(f"Ollama доступна. Доступные модели: {len(models)}")
            return True
        except Exception as e:
            logger.error(f"Ollama недоступна: {e}")
            return False
    
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
            # Подготовка данных для промпта
            prompt = self._create_conclusion_prompt(audio_analysis, defects, file_info)
            
            logger.info("Генерация заключения через LLM...")
            
            # Вызов Ollama
            response = ollama.generate(
                model=self.model,
                prompt=prompt,
                options={
                    'temperature': self.temperature,
                    'num_predict': self.max_tokens
                }
            )
            
            conclusion = response['response']
            logger.info("Заключение успешно сгенерировано")
            
            return conclusion
            
        except Exception as e:
            logger.error(f"Ошибка генерации заключения: {e}")
            return self._fallback_conclusion(audio_analysis, defects)
    
    def _create_conclusion_prompt(
        self, 
        audio_analysis: Dict, 
        defects: List, 
        file_info: Dict
    ) -> str:
        """Создание промпта для LLM"""
        
        # Подсчет дефектов по типам
        defect_summary = self._summarize_defects(defects)
        
        # Статус соответствия стандартам
        compliance = audio_analysis.get('compliance', {})
        measurements = audio_analysis.get('measurements', {})
        
        if self.language == 'ru':
            prompt = f"""Ты - профессиональный звукорежиссер и специалист по контролю качества аудио для видеопроизводства.
Напиши профессиональное заключение на русском языке о качестве аудиодорожки на основе следующих данных:

ИНФОРМАЦИЯ О ФАЙЛЕ:
- Название: {file_info.get('file_name', 'Не указано')}
- Длительность: {audio_analysis.get('duration', 0)} секунд
- Формат: {audio_analysis.get('channel_layout', 'Не указан')}
- Sample Rate: {audio_analysis.get('sample_rate', 48000)} Hz

ИЗМЕРЕНИЯ ГРОМКОСТИ (Стандарт EBU R128):
- LUFS (интегральная громкость): {measurements.get('lufs', 'N/A')} dB (норма: -23.0 ±0.5 LUFS)
  Статус: {'✓ Соответствует' if compliance.get('lufs_compliant') else '✗ Не соответствует'}
  
- TRUE PEAK (пиковое значение): {measurements.get('true_peak', 'N/A')} dBTP (норма: ≤ -2.0 dBTP)
  Статус: {'✓ Соответствует' if compliance.get('true_peak_compliant') else '✗ Не соответствует'}
  
- LRA (динамический диапазон): {measurements.get('lra', 'N/A')} LU (норма: ≤ 18 LU)
  Статус: {'✓ Соответствует' if compliance.get('lra_compliant') else '✗ Не соответствует'}

ОБНАРУЖЕННЫЕ ДЕФЕКТЫ:
{self._format_defects_for_prompt(defect_summary)}

Твоё заключение должно включать:
1. Общую оценку качества звука (1-2 предложения)
2. Анализ соответствия стандартам громкости
3. Перечисление основных проблем по категориям важности:
   - КРИТИЧНЫЕ (блокеры) - требуют обязательного исправления
   - ВАЖНЫЕ - рекомендуется исправить
   - НЕКРИТИЧНЫЕ - требуют комментария
4. Рекомендации по исправлению дефектов
5. Итоговый вывод о готовности материала к использованию

Пиши профессионально, но понятно. Используй технические термины где уместно."""

        else:  # English
            prompt = f"""You are a professional sound engineer and audio quality control specialist for video production.
Write a professional conclusion about the audio track quality based on the following data:

FILE INFORMATION:
- Name: {file_info.get('file_name', 'Not specified')}
- Duration: {audio_analysis.get('duration', 0)} seconds
- Format: {audio_analysis.get('channel_layout', 'Not specified')}
- Sample Rate: {audio_analysis.get('sample_rate', 48000)} Hz

LOUDNESS MEASUREMENTS (EBU R128 Standard):
- LUFS (integrated loudness): {measurements.get('lufs', 'N/A')} dB (target: -23.0 ±0.5 LUFS)
  Status: {'✓ Compliant' if compliance.get('lufs_compliant') else '✗ Non-compliant'}
  
- TRUE PEAK: {measurements.get('true_peak', 'N/A')} dBTP (target: ≤ -2.0 dBTP)
  Status: {'✓ Compliant' if compliance.get('true_peak_compliant') else '✗ Non-compliant'}
  
- LRA (loudness range): {measurements.get('lra', 'N/A')} LU (target: ≤ 18 LU)
  Status: {'✓ Compliant' if compliance.get('lra_compliant') else '✗ Non-compliant'}

DETECTED DEFECTS:
{self._format_defects_for_prompt(defect_summary)}

Your conclusion should include:
1. Overall audio quality assessment (1-2 sentences)
2. Compliance analysis with loudness standards
3. List of main issues by severity:
   - CRITICAL (blockers) - must be fixed
   - IMPORTANT - should be fixed
   - MINOR - requires comment
4. Recommendations for fixing defects
5. Final conclusion about material readiness

Write professionally but clearly. Use technical terms where appropriate."""
        
        return prompt
    
    def _summarize_defects(self, defects: List) -> Dict:
        """Подсчет дефектов по категориям"""
        summary = {
            'total': len(defects),
            'blocker': [],
            'fix_required': [],
            'comment_required': []
        }
        
        for defect in defects:
            severity = getattr(defect, 'severity', 'comment_required')
            defect_info = {
                'type': getattr(defect, 'type', 'unknown'),
                'description': getattr(defect, 'description', ''),
                'timecode': getattr(defect, 'timecode_in', ''),
                'duration': getattr(defect, 'duration', 0)
            }
            summary[severity].append(defect_info)
        
        return summary
    
    def _format_defects_for_prompt(self, summary: Dict) -> str:
        """Форматирование дефектов для промпта"""
        lines = []
        
        lines.append(f"Всего обнаружено: {summary['total']} дефектов\n")
        
        if summary['blocker']:
            lines.append(f"КРИТИЧНЫЕ (блокеры): {len(summary['blocker'])}")
            for d in summary['blocker'][:5]:  # Первые 5
                lines.append(f"  - {d['description']} (таймкод: {d['timecode']})")
        
        if summary['fix_required']:
            lines.append(f"\nВАЖНЫЕ (требуют исправления): {len(summary['fix_required'])}")
            for d in summary['fix_required'][:5]:
                lines.append(f"  - {d['description']} (таймкод: {d['timecode']})")
        
        if summary['comment_required']:
            lines.append(f"\nНЕКРИТИЧНЫЕ (требуют комментария): {len(summary['comment_required'])}")
            for d in summary['comment_required'][:5]:
                lines.append(f"  - {d['description']} (таймкод: {d['timecode']})")
        
        return "\n".join(lines)
    
    def _fallback_conclusion(self, audio_analysis: Dict, defects: List) -> str:
        """Резервное заключение если LLM недоступна"""
        
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
            'model': 'llama3.2',
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


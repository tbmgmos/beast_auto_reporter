"""
PDF Extractor Module

Извлечение технической информации из PDF отчетов
"""

import PyPDF2
import fitz  # PyMuPDF - fallback для проблемных PDF
import re
import json
from typing import Dict, List
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class PDFExtractor:
    """Класс для извлечения данных из PDF"""
    
    def __init__(self):
        """Инициализация экстрактора"""
        logger.info("PDFExtractor инициализирован")
    
    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """
        Извлечение текста из PDF
        
        Args:
            pdf_path: Путь к PDF файлу
            
        Returns:
            Извлеченный текст
        """
        text = ""
        
        # Попытка 1: PyPDF2
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page in pdf_reader.pages:
                    text += page.extract_text()
            
            if text.strip():
                logger.info(f"✅ Текст извлечен из PDF через PyPDF2")
                return text
                
        except Exception as e:
            logger.warning(f"⚠️  PyPDF2 не смог прочитать PDF: {e}")
        
        # Попытка 2: PyMuPDF (fitz) - более надежный
        try:
            logger.info(f"Пробуем PyMuPDF (fitz)...")
            pdf_doc = fitz.open(pdf_path)
            for page_num in range(len(pdf_doc)):
                page = pdf_doc[page_num]
                text += page.get_text()
            pdf_doc.close()
            
            if text.strip():
                logger.info(f"✅ Текст извлечен из PDF через PyMuPDF")
                return text
            else:
                logger.warning(f"⚠️  PDF прочитан, но текст пустой")
                
        except Exception as e:
            logger.error(f"❌ PyMuPDF также не смог прочитать PDF: {e}")
        
        logger.error(f"❌ Не удалось извлечь текст из PDF")
        return ""
    
    def extract_technical_info(self, pdf_path: str) -> Dict:
        """
        Извлечение технической информации из PDF
        
        Args:
            pdf_path: Путь к PDF файлу
            
        Returns:
            Словарь с технической информацией
        """
        text = self.extract_text_from_pdf(pdf_path)
        
        # Debug: log text length and sample
        if text:
            logger.debug(f"  Extracted {len(text)} chars from PDF")
            logger.debug(f"  First 500 chars: {text[:500]}")
            logger.debug(f"  Last 500 chars: {text[-500:]}")
        else:
            logger.warning(f"  ⚠️ No text extracted from PDF!")
        
        info = {
            'lufs': None,
            'true_peak': None,
            'lra': None,
            'sample_rate': None,
            'bit_depth': None,
            'format': None,
            'channels': None
        }
        
        try:
            # LUFS (INTEGRATED в Youlean) - число ПЕРЕД словом INTEGRATED
            # В Youlean: "-24.4\nINTEGRATED"
            lufs_patterns = [
                r'(-?\d+\.?\d*)\s*\n?\s*INTEGRATED',  # Число перед INTEGRATED (с возможным переносом строки)
                r'INTEGRATED\s*:\s*(-?\d+\.?\d*)',
                r'LUFS[:\s]+(-?\d+\.?\d*)',
                r'Integrated[:\s]+(-?\d+\.?\d*)',
                r'Loudness[:\s]+(-?\d+\.?\d*)',
                r'[-]?\d+\.?\d*\s+LUFS',  # Fallback: number before LUFS
            ]
            for pattern in lufs_patterns:
                lufs_match = re.search(pattern, text, re.IGNORECASE)
                if lufs_match:
                    info['lufs'] = float(lufs_match.group(1))
                    logger.debug(f"  -> LUFS matched pattern: {pattern}")
                    break
            
            # TRUE PEAK - число ПЕРЕД словами TRUE PEAK MAX
            # В Youlean: "-6.5\nTRUE PEAK MAX"
            tp_patterns = [
                r'(-?\d+\.?\d*)\s*\n?\s*TRUE\s*PEAK\s*MAX',  # Число перед TRUE PEAK MAX (с возможным переносом)
                r'TRUE\s*PEAK\s*MAX\s*:\s*(-?\d+\.?\d*)',
                r'TRUE\s*PEAK[:\s]+(-?\d+\.?\d*)',
                r'Peak[:\s]+(-?\d+\.?\d*)',
                r'[-+]?\d+\.?\d*\s*dBTP?',  # Fallback: number before dBTP
                r'[-+]?\d+\.?\d*\s*dB',  # Fallback: number before dB
            ]
            for pattern in tp_patterns:
                tp_match = re.search(pattern, text, re.IGNORECASE)
                if tp_match:
                    info['true_peak'] = float(tp_match.group(1))
                    logger.debug(f"  -> TRUE PEAK matched pattern: {pattern}")
                    break
            
            # LRA (LOUDNESS RANGE в Youlean) - число ПЕРЕД словами LOUDNESS RANGE
            # В Youlean: "23.0\nLOUDNESS RANGE"
            lra_patterns = [
                r'(-?\d+\.?\d*)\s*\n?\s*LOUDNESS\s*RANGE',  # Число перед LOUDNESS RANGE (с возможным переносом)
                r'LRA[:\s]+(-?\d+\.?\d*)',
                r'Loudness\s*Range[:\s]+(-?\d+\.?\d*)',
                r'[-]?\d+\.?\d*\s+LU\s*LRA',  # Fallback: number before LU LRA
                r'[-]?\d+\.?\d*\s+LU',  # Fallback: number before LU
            ]
            for pattern in lra_patterns:
                lra_match = re.search(pattern, text, re.IGNORECASE)
                if lra_match:
                    info['lra'] = float(lra_match.group(1))
                    logger.debug(f"  -> LRA matched pattern: {pattern}")
                    break
            
            # Sample Rate
            sr_match = re.search(r'(\d+)\s*kHz', text)
            if sr_match:
                info['sample_rate'] = int(sr_match.group(1)) * 1000
            
            # Bit Depth
            bit_match = re.search(r'(\d+)\s*bit', text)
            if bit_match:
                info['bit_depth'] = int(bit_match.group(1))
            
            # Format (PCM, MP3, etc.)
            format_match = re.search(r'(PCM|MP3|AAC|FLAC)', text, re.IGNORECASE)
            if format_match:
                info['format'] = format_match.group(1).upper()
            
            # Channels (2.0, 5.1, etc.)
            channels_match = re.search(r'(2\.0|5\.1|7\.1)', text, re.IGNORECASE)
            if channels_match:
                info['channels'] = channels_match.group(1)
            else:
                if re.search(r'(stereo|2\s*ch|2\s*channel)', text, re.IGNORECASE):
                    info['channels'] = '2.0'
                elif re.search(r'(5\.0|surround|6\s*ch|6\s*channel)', text, re.IGNORECASE):
                    info['channels'] = '5.1'
            
            logger.info(f"Техническая информация извлечена: {info}")
            
            # Debug: verify extracted values
            if info['lufs'] is None:
                logger.warning(f"  ⚠️ LUFS not found in PDF text!")
            if info['true_peak'] is None:
                logger.warning(f"  ⚠️ TRUE PEAK not found in PDF text!")
            if info['lra'] is None:
                logger.warning(f"  ⚠️ LRA not found in PDF text!")
            
        except Exception as e:
            logger.error(f"Ошибка парсинга технической информации: {e}")
        
        return info
    
    def extract_all_pdfs(self, pdf_paths: List[str]) -> Dict[str, Dict]:
        """
        Извлечение информации из нескольких PDF
        
        Args:
            pdf_paths: Список путей к PDF файлам
            
        Returns:
            Словарь {имя_файла: техническая_информация}
        """
        results = {}
        
        for pdf_path in pdf_paths:
            try:
                from pathlib import Path
                file_name = Path(pdf_path).name
                
                info = self.extract_technical_info(pdf_path)
                results[file_name] = info
                
            except Exception as e:
                logger.error(f"Ошибка обработки {pdf_path}: {e}")
        
        return results
    
    def save_tech_info_to_json(self, tech_info: Dict, json_path: str) -> None:
        """
        Сохранение технической информации в JSON файл
        
        Args:
            tech_info: Словарь с технической информацией PDF
            json_path: Путь к JSON файлу
        """
        try:
            # Добавляем метаданные о сохранении
            output_data = {
                '_metadata': {
                    'created_at': str(datetime.now()),
                    'version': '1.0'
                },
                '_data': tech_info
            }
            
            logger.info(f"📄 Saving to JSON: {json_path}")
            logger.info(f"   tech_info keys: {list(tech_info.keys())}")
            
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"✅ PDF данные сохранены в JSON: {json_path}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения JSON: {e}")
    
    def load_tech_info_from_json(self, json_path: str) -> Dict:
        """
        Загрузка технической информации из JSON файла
        
        Args:
            json_path: Путь к JSON файлу
            
        Returns:
            Словарь с технической информацией
        """
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            logger.info(f"📄 JSON loaded from: {json_path}")
            logger.info(f"   Root keys: {list(data.keys())}")
            
            # Извлекаем data из metadata wrapper
            # Проверяем разные форматы JSON
            if '_data' in data and isinstance(data['_data'], dict):
                tech_info = data['_data']
                logger.info(f"✅ Found '_data' wrapper")
            elif isinstance(data, dict):
                # JSON без wrapper - используем данные напрямую
                tech_info = data
                logger.info(f"✅ Using JSON directly (no wrapper)")
            else:
                logger.warning(f"⚠️ Unexpected JSON format")
                tech_info = {}
            
            logger.info(f"✅ PDF данные загружены из JSON: {json_path}")
            logger.info(f"   Загружено ключей: {list(tech_info.keys())}")
            
            return tech_info
            
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки JSON: {e}")
            return {}
    
    def extract_and_save_to_json(self, pdf_path: str, json_path: str) -> Dict:
        """
        Извлечение данных из PDF и сохранение в JSON
        
        Args:
            pdf_path: Путь к PDF файлу
            json_path: Путь к JSON файлу
            
        Returns:
            Словарь с технической информацией
        """
        info = self.extract_technical_info(pdf_path)
        self.save_tech_info_to_json({Path(pdf_path).stem: info}, json_path)
        return info


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    extractor = PDFExtractor()
    print("PDFExtractor готов к использованию!")

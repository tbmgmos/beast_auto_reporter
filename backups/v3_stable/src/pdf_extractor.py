"""
PDF Extractor Module

Извлечение технической информации из PDF отчетов
"""

import PyPDF2
import fitz  # PyMuPDF - fallback для проблемных PDF
import re
from typing import Dict, List
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
            ]
            for pattern in lufs_patterns:
                lufs_match = re.search(pattern, text, re.IGNORECASE)
                if lufs_match:
                    info['lufs'] = float(lufs_match.group(1))
                    break
            
            # TRUE PEAK - число ПЕРЕД словами TRUE PEAK MAX
            # В Youlean: "-6.5\nTRUE PEAK MAX"
            tp_patterns = [
                r'(-?\d+\.?\d*)\s*\n?\s*TRUE\s*PEAK\s*MAX',  # Число перед TRUE PEAK MAX (с возможным переносом)
                r'TRUE\s*PEAK\s*MAX\s*:\s*(-?\d+\.?\d*)',
                r'TRUE\s*PEAK[:\s]+(-?\d+\.?\d*)',
                r'Peak[:\s]+(-?\d+\.?\d*)'
            ]
            for pattern in tp_patterns:
                tp_match = re.search(pattern, text, re.IGNORECASE)
                if tp_match:
                    info['true_peak'] = float(tp_match.group(1))
                    break
            
            # LRA (LOUDNESS RANGE в Youlean) - число ПЕРЕД словами LOUDNESS RANGE
            # В Youlean: "23.0\nLOUDNESS RANGE"
            lra_patterns = [
                r'(-?\d+\.?\d*)\s*\n?\s*LOUDNESS\s*RANGE',  # Число перед LOUDNESS RANGE (с возможным переносом)
                r'LRA[:\s]+(-?\d+\.?\d*)',
                r'Loudness\s*Range[:\s]+(-?\d+\.?\d*)'
            ]
            for pattern in lra_patterns:
                lra_match = re.search(pattern, text, re.IGNORECASE)
                if lra_match:
                    info['lra'] = float(lra_match.group(1))
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
            channels_match = re.search(r'(2\.0|5\.1|7\.1)', text)
            if channels_match:
                info['channels'] = channels_match.group(1)
            
            logger.info(f"Техническая информация извлечена: {info}")
            
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


if __name__ == "__main__":
    # Тестирование
    logging.basicConfig(level=logging.INFO)
    
    extractor = PDFExtractor()
    print("PDFExtractor готов к использованию!")


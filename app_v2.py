"""
Beast Auto Reporter V2 - Complete Integrated System

Полностью интегрированная система генерации отчетов по шаблону
"""

import streamlit as st
import yaml
from pathlib import Path
import sys
import logging
from datetime import datetime
import tempfile
import os

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from audio_analyzer import AudioAnalyzer
from defect_detector import DefectDetector
from llm_integration import LLMIntegration
from template_report_generator import TemplateReportGenerator
from pdf_extractor import PDFExtractor
from timecode_analyzer import TimecodeAnalyzer

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация страницы
st.set_page_config(
    page_title="Beast Auto Reporter V2",
    page_icon="🎵",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Кастомные стили
st.markdown("""
<style>
    .main-header {
        text-align: center;
        color: #1f77b4;
        font-size: 3em;
        font-weight: bold;
        margin-bottom: 0.5em;
    }
    .sub-header {
        text-align: center;
        color: #666;
        font-size: 1.2em;
        margin-bottom: 2em;
    }
    .file-upload-section {
        background-color: #f0f2f6;
        padding: 1.5em;
        border-radius: 0.5em;
        margin: 1em 0;
    }
    .success-box {
        background-color: #d4edda;
        border-left: 4px solid #28a745;
        padding: 1em;
        margin: 1em 0;
    }
    .warning-box {
        background-color: #fff3cd;
        border-left: 4px solid #ffc107;
        padding: 1em;
        margin: 1em 0;
    }
    .info-box {
        background-color: #d1ecf1;
        border-left: 4px solid #17a2b8;
        padding: 1em;
        margin: 1em 0;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def load_config():
    """Загрузка конфигурации"""
    config_path = Path(__file__).parent / 'config' / 'settings.yaml'
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


@st.cache_resource
def initialize_components(_config):
    """Инициализация всех компонентов"""
    analyzer = AudioAnalyzer(_config)
    detector = DefectDetector(_config)
    llm = LLMIntegration(_config)
    generator = TemplateReportGenerator()
    pdf_extractor = PDFExtractor()
    tc_analyzer = TimecodeAnalyzer()
    
    return analyzer, detector, llm, generator, pdf_extractor, tc_analyzer


def save_uploaded_file(uploaded_file, suffix=".wav"):
    """Сохранение загруженного файла"""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            return tmp_file.name
    except Exception as e:
        st.error(f"Ошибка сохранения файла: {e}")
        return None


def read_params_file(file):
    """Чтение файла Параметры.txt"""
    try:
        content = file.read().decode('utf-8')
        
        # Парсинг параметров
        params = {
            'target_lufs': -23.0,
            'lufs_tolerance': 0.5,
            'true_peak': -2.0,
            'lra_max': 18.0,
            'sample_rate': 48000,
            'bit_depth': 24,
            'format': 'PCM'
        }
        
        # Извлекаем значения из текста
        import re
        
        lufs_match = re.search(r'(-?\d+\.?\d*)\s*LUFS', content)
        if lufs_match:
            params['target_lufs'] = float(lufs_match.group(1))
        
        tolerance_match = re.search(r'\+-?\s*(\d+\.?\d*)\s*LUFS', content)
        if tolerance_match:
            params['lufs_tolerance'] = float(tolerance_match.group(1))
        
        tp_match = re.search(r'(-?\d+\.?\d*)\s*dBTP', content)
        if tp_match:
            params['true_peak'] = float(tp_match.group(1))
        
        lra_match = re.search(r'(\d+\.?\d*)\s*LU', content)
        if lra_match:
            params['lra_max'] = float(lra_match.group(1))
        
        sr_match = re.search(r'(\d+)\s*kHz', content)
        if sr_match:
            params['sample_rate'] = int(sr_match.group(1)) * 1000
        
        bit_match = re.search(r'(\d+)\s*bit', content)
        if bit_match:
            params['bit_depth'] = int(bit_match.group(1))
        
        return params, content
        
    except Exception as e:
        logger.error(f"Ошибка чтения параметров: {e}")
        return None, None


def main():
    """Главная функция"""
    
    # Заголовок
    st.markdown('<div class="main-header">🎵 Beast Auto Reporter V2</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Автоматическая генерация отчетов по шаблону</div>', unsafe_allow_html=True)
    
    # Загрузка конфигурации
    config = load_config()
    
    # Инициализация компонентов
    try:
        analyzer, detector, llm, generator, pdf_extractor, tc_analyzer = initialize_components(config)
    except Exception as e:
        st.error(f"Ошибка инициализации: {e}")
        return
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Информация")
        
        # Проверка Ollama
        llm_status = llm.check_ollama_status()
        if llm_status:
            st.success("✓ Ollama подключена")
        else:
            st.warning("⚠️ Ollama недоступна")
        
        st.markdown("---")
        
        st.subheader("📋 Формат отчета")
        st.info("""
        Отчет генерируется **точно** 
        в формате вашего шаблона:
        
        - Таблица MARKER LIST
        - 11 столбцов
        - Timecode In/Out
        - 2.0 C/UC, 5.1 C/UC
        - БЛОКЕР / ТРЕБУЕТ ИСПРАВЛЕНИЯ
        - ТРЕБУЕТ КОММЕНТАРИЯ
        """)
        
        st.markdown("---")
        
        st.subheader("О программе")
        st.info("""
        **Beast Auto Reporter V2**
        
        - ✓ Анализ LUFS, TP, LRA
        - ✓ Детекция дефектов
        - ✓ Извлечение из PDF
        - ✓ Анализ хронометража
        - ✓ Генерация по шаблону
        
        Все локально!
        """)
    
    # Основной интерфейс
    st.markdown("---")
    st.header("📁 Шаг 1: Загрузка файлов")
    
    # Создаем вкладки для разных типов файлов
    tab1, tab2, tab3 = st.tabs(["🎵 Аудио файлы", "📄 Документы", "🎬 Видео (опционально)"])
    
    with tab1:
        st.markdown('<div class="file-upload-section">', unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Аудио 2.0 (Стерео)")
            audio_20_c = st.file_uploader(
                "Censored версия",
                type=['wav', 'mp3', 'aiff', 'flac'],
                key='audio_20_c'
            )
            audio_20_uc = st.file_uploader(
                "Uncensored версия (опционально)",
                type=['wav', 'mp3', 'aiff', 'flac'],
                key='audio_20_uc'
            )
        
        with col2:
            st.subheader("Аудио 5.1 (Surround)")
            audio_51_c = st.file_uploader(
                "Censored версия",
                type=['wav', 'mp3', 'aiff', 'flac'],
                key='audio_51_c'
            )
            audio_51_uc = st.file_uploader(
                "Uncensored версия (опционально)",
                type=['wav', 'mp3', 'aiff', 'flac'],
                key='audio_51_uc'
            )
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    with tab2:
        st.markdown('<div class="file-upload-section">', unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("📝 Параметры.txt")
            params_file = st.file_uploader(
                "Файл с техническими требованиями",
                type=['txt'],
                key='params'
            )
            
            if params_file:
                st.success("✓ Параметры загружены")
        
        with col2:
            st.subheader("📊 PDF отчеты (опционально)")
            pdf_20 = st.file_uploader(
                "PDF для 2.0",
                type=['pdf'],
                key='pdf_20'
            )
            pdf_51 = st.file_uploader(
                "PDF для 5.1",
                type=['pdf'],
                key='pdf_51'
            )
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    with tab3:
        st.markdown('<div class="file-upload-section">', unsafe_allow_html=True)
        
        st.subheader("🎬 Видео файл")
        st.info("Видео используется для определения стартового таймкода и проверки хронометража")
        
        video_file = st.file_uploader(
            "MP4, MOV, MKV",
            type=['mp4', 'mov', 'mkv', 'avi'],
            key='video'
        )
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    # Кнопка запуска
    st.markdown("---")
    st.header("🚀 Шаг 2: Запуск анализа")
    
    if st.button("🎯 СОЗДАТЬ ОТЧЕТ", type="primary", use_container_width=True):
        
        # Проверка минимальных требований
        if not (audio_20_c or audio_51_c):
            st.error("❌ Загрузите хотя бы один аудиофайл (2.0 или 5.1)!")
            return
        
        if not params_file:
            st.warning("⚠️ Рекомендуется загрузить файл Параметры.txt")
        
        # Контейнер для результатов
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            # === 1. Чтение параметров ===
            params_dict = None
            params_text = None
            
            if params_file:
                status_text.text("📝 Чтение параметров...")
                progress_bar.progress(5)
                params_dict, params_text = read_params_file(params_file)
                
                if params_dict:
                    # Обновляем конфигурацию
                    config['audio'].update(params_dict)
                    analyzer = AudioAnalyzer(config)
                    detector = DefectDetector(config)
                    st.success("✓ Параметры загружены и применены")
            
            # === 2. Сохранение файлов ===
            status_text.text("💾 Сохранение файлов...")
            progress_bar.progress(10)
            
            temp_files = {}
            
            if audio_20_c:
                temp_files['audio_20_c'] = save_uploaded_file(audio_20_c)
            if audio_20_uc:
                temp_files['audio_20_uc'] = save_uploaded_file(audio_20_uc)
            if audio_51_c:
                temp_files['audio_51_c'] = save_uploaded_file(audio_51_c)
            if audio_51_uc:
                temp_files['audio_51_uc'] = save_uploaded_file(audio_51_uc)
            if video_file:
                temp_files['video'] = save_uploaded_file(video_file, suffix='.mp4')
            if pdf_20:
                temp_files['pdf_20'] = save_uploaded_file(pdf_20, suffix='.pdf')
            if pdf_51:
                temp_files['pdf_51'] = save_uploaded_file(pdf_51, suffix='.pdf')
            
            # === 3. Анализ хронометража ===
            status_text.text("⏱️ Анализ хронометража...")
            progress_bar.progress(20)
            
            timecode_info = tc_analyzer.analyze_all_files(
                audio_20=temp_files.get('audio_20_c'),
                audio_51=temp_files.get('audio_51_c'),
                video=temp_files.get('video')
            )
            
            st.success(f"✓ Хронометраж проанализирован")
            
            # Показываем информацию о хронометраже
            with st.expander("📊 Информация о хронометраже", expanded=False):
                for file_type, info in timecode_info['files'].items():
                    st.write(f"**{file_type}:** {info['duration']:.2f} сек ({info['duration_tc']})")
                
                # Проверка совпадений
                for comp in timecode_info['comparisons']:
                    if not comp['matches']:
                        st.warning(f"⚠️ Несовпадение: {comp['file1']} и {comp['file2']} ({comp['difference']:.2f} сек)")
            
            # === 4. Извлечение из PDF ===
            pdf_info = {}
            
            if pdf_20 or pdf_51:
                status_text.text("📄 Извлечение данных из PDF...")
                progress_bar.progress(30)
                
                if temp_files.get('pdf_20'):
                    pdf_info['2.0'] = pdf_extractor.extract_technical_info(temp_files['pdf_20'])
                
                if temp_files.get('pdf_51'):
                    pdf_info['5.1'] = pdf_extractor.extract_technical_info(temp_files['pdf_51'])
                
                st.success("✓ Данные из PDF извлечены")
            
            # === 5. Анализ аудио ===
            all_analysis = {}
            
            if temp_files.get('audio_20_c'):
                status_text.text("📊 Анализ аудио 2.0...")
                progress_bar.progress(40)
                
                all_analysis['audio_20'] = analyzer.analyze_file(temp_files['audio_20_c'])
                st.success("✓ Анализ 2.0 завершен")
            
            if temp_files.get('audio_51_c'):
                status_text.text("📊 Анализ аудио 5.1...")
                progress_bar.progress(50)
                
                all_analysis['audio_51'] = analyzer.analyze_file(temp_files['audio_51_c'])
                st.success("✓ Анализ 5.1 завершен")
            
            # === 6. Детекция дефектов ===
            all_defects = []
            
            status_text.text("🔍 Детекция дефектов...")
            progress_bar.progress(60)
            
            if temp_files.get('audio_20_c'):
                defects_20 = detector.analyze_file(temp_files['audio_20_c'], "2.0")
                all_defects.extend(defects_20)
                st.success(f"✓ Найдено {len(defects_20)} дефектов в 2.0")
            
            if temp_files.get('audio_51_c'):
                defects_51 = detector.analyze_file(temp_files['audio_51_c'], "5.1")
                all_defects.extend(defects_51)
                st.success(f"✓ Найдено {len(defects_51)} дефектов в 5.1")
            
            # Сортируем дефекты по таймкоду
            all_defects.sort(key=lambda d: getattr(d, 'timecode_in', '00:00:00:00'))
            
            # === 7. Генерация отчета ===
            status_text.text("📄 Генерация отчета по шаблону...")
            progress_bar.progress(80)
            
            output_dir = tempfile.mkdtemp()
            timestamp = datetime.now().strftime('%Y_%m_%d')
            
            # Имя файла
            base_name = "отчет"
            if audio_20_c:
                base_name += f"_{Path(audio_20_c.name).stem}"
            
            output_filename = f"{base_name}_{timestamp}.docx"
            output_path = Path(output_dir) / output_filename
            
            # Генерируем отчет
            report_path = generator.create_report_from_template(
                defects=all_defects,
                output_path=str(output_path),
                params_info=params_dict,
                timecode_info=timecode_info
            )
            
            progress_bar.progress(100)
            status_text.text("✅ Отчет готов!")
            
            # === 8. Отображение результатов ===
            st.markdown("---")
            st.header("📊 Результаты")
            
            # Метрики
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("📝 Всего дефектов", len(all_defects))
            
            with col2:
                blockers = sum(1 for d in all_defects if getattr(d, 'severity', '') == 'blocker')
                st.metric("🚫 Блокеры", blockers)
            
            with col3:
                fix_req = sum(1 for d in all_defects if getattr(d, 'severity', '') == 'fix_required')
                st.metric("⚠️ Требуют исправления", fix_req)
            
            with col4:
                comment_req = sum(1 for d in all_defects if getattr(d, 'severity', '') == 'comment_required')
                st.metric("💬 Требуют комментария", comment_req)
            
            # Детальная информация
            with st.expander("📋 Список дефектов (первые 20)", expanded=False):
                for i, defect in enumerate(all_defects[:20], 1):
                    st.markdown(f"**{i}. {getattr(defect, 'timecode_in', 'N/A')}** - {getattr(defect, 'description', 'N/A')}")
            
            # Скачивание
            st.markdown("---")
            st.header("📥 Скачать отчет")
            
            with open(report_path, 'rb') as f:
                st.download_button(
                    label="📘 Скачать DOCX отчет",
                    data=f,
                    file_name=output_filename,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True
                )
            
            st.success("🎉 Отчет успешно создан по вашему шаблону!")
            
            # Очистка временных файлов
            for temp_file in temp_files.values():
                try:
                    if temp_file and os.path.exists(temp_file):
                        os.unlink(temp_file)
                except:
                    pass
            
        except Exception as e:
            st.error(f"❌ Ошибка: {e}")
            logger.exception("Ошибка в main процессе")
            import traceback
            st.code(traceback.format_exc())


if __name__ == "__main__":
    main()


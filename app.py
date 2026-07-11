"""
Beast Auto Reporter - Main Application

Streamlit приложение для автоматической генерации отчетов о качестве аудио
"""

import streamlit as st
import yaml
from pathlib import Path
import sys
import logging
from datetime import datetime
import tempfile
import zipfile
import os

# Добавляем src в путь
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from audio_analyzer import AudioAnalyzer
from defect_detector import DefectDetector
from conclusion_generator import ConclusionGenerator
from csv_importer import Issue
from report_generator import ReportGenerator

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация страницы
st.set_page_config(
    page_title="Beast Auto Reporter",
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
    .metric-card {
        background-color: #f0f2f6;
        padding: 1em;
        border-radius: 0.5em;
        margin: 0.5em 0;
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
    .error-box {
        background-color: #f8d7da;
        border-left: 4px solid #dc3545;
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
    """Инициализация компонентов системы"""
    analyzer = AudioAnalyzer(_config)
    detector = DefectDetector(_config)
    conclusion_gen = ConclusionGenerator(use_llm=True, config=_config)
    generator = ReportGenerator(_config)
    return analyzer, detector, conclusion_gen, generator


def save_uploaded_file(uploaded_file, suffix=".wav"):
    """Сохранение загруженного файла во временную директорию"""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            return tmp_file.name
    except Exception as e:
        st.error(f"Ошибка сохранения файла: {e}")
        return None


def format_timedelta(seconds):
    """Форматирование времени"""
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def convert_defects_to_issues(defects):
    """Адаптация Defect объектов к Issue для ConclusionGenerator."""
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


def build_streamlit_conclusion(conclusion_gen, audio_analysis, defects, file_info):
    """
    Собирает совместимый текст заключения для legacy Streamlit UI:
    короткий технический summary + субъективное заключение через актуальный ConclusionGenerator.
    """
    compliance = audio_analysis.get('compliance', {}) if audio_analysis else {}
    measurements = audio_analysis.get('measurements', {}) if audio_analysis else {}
    file_name = file_info.get('file_name', 'N/A')

    problems = []
    if not compliance.get('lufs_compliant', True):
        problems.append(f"LUFS не соответствует норме ({measurements.get('lufs', 'N/A')})")
    if not compliance.get('true_peak_compliant', True):
        problems.append(f"TRUE PEAK не соответствует норме ({measurements.get('true_peak', 'N/A')})")
    if not compliance.get('lra_compliant', True):
        problems.append(f"LRA не соответствует норме ({measurements.get('lra', 'N/A')})")

    if problems:
        technical = (
            f"Файл: {file_name}\n"
            "По техническим характеристикам выявлены следующие недочёты:\n"
            + "\n".join(f"- {problem}" for problem in problems)
        )
    else:
        technical = (
            f"Файл: {file_name}\n"
            "По техническим характеристикам нареканий не обнаружено."
        )

    subjective = conclusion_gen.generate_subjective_conclusion(
        convert_defects_to_issues(defects),
        "main",
    )

    if subjective == "По субъективной оценке нареканий не обнаружено.":
        return technical

    return f"{technical}\n\n{subjective}"


def main():
    """Главная функция приложения"""
    
    # Заголовок
    st.markdown('<div class="main-header">🎵 Beast Auto Reporter</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Автоматическая система генерации отчетов о качестве аудио</div>', unsafe_allow_html=True)
    
    # Загрузка конфигурации
    config = load_config()
    
    # Инициализация компонентов
    try:
        analyzer, detector, conclusion_gen, generator = initialize_components(config)
    except Exception as e:
        st.error(f"Ошибка инициализации: {e}")
        return
    
    # Проверка Ollama
    with st.sidebar:
        st.header("⚙️ Настройки")
        
        # Проверка LLM
        llm_status = conclusion_gen.check_ollama_status()
        if llm_status:
            st.success("✓ Ollama подключена")
        else:
            st.warning("⚠️ Ollama недоступна (будет использован fallback режим)")
        
        st.markdown("---")
        
        # Настройки анализа
        st.subheader("Параметры анализа")
        
        target_lufs = st.number_input(
            "Target LUFS (dB)",
            value=float(config.get('audio', {}).get('target_lufs', -23.0)),
            step=0.1
        )
        
        lufs_tolerance = st.number_input(
            "LUFS допуск (±dB)",
            value=float(config.get('audio', {}).get('lufs_tolerance', 0.5)),
            step=0.1
        )
        
        true_peak = st.number_input(
            "TRUE PEAK (dBTP)",
            value=float(config.get('audio', {}).get('true_peak', -2.0)),
            step=0.1
        )
        
        lra_max = st.number_input(
            "LRA максимум (LU)",
            value=float(config.get('audio', {}).get('lra_max', 18.0)),
            step=0.5
        )
        
        st.markdown("---")
        
        # О программе
        st.subheader("О программе")
        st.info("""
        **Beast Auto Reporter v1.0**
        
        Профессиональная система контроля качества аудио для видеопроизводства.
        
        - ✓ Анализ LUFS, TRUE PEAK, LRA
        - ✓ Детекция дефектов
        - ✓ AI-заключения (Ollama)
        - ✓ Генерация отчетов
        
        Все обработки локальные!
        """)
    
    # Основной интерфейс
    st.header("📁 Загрузка файлов")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Аудио 2.0 (Стерео)")
        audio_20_file = st.file_uploader(
            "Выберите стерео файл",
            type=['wav', 'mp3', 'aiff', 'flac'],
            key='audio_20'
        )
    
    with col2:
        st.subheader("Аудио 5.1 (Surround)")
        audio_51_file = st.file_uploader(
            "Выберите 5.1 файл (опционально)",
            type=['wav', 'mp3', 'aiff', 'flac'],
            key='audio_51'
        )
    
    # Дополнительные опции
    st.markdown("---")
    st.header("🔧 Опции анализа")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        analyze_loudness = st.checkbox("Анализ громкости (LUFS, TP, LRA)", value=True)
    with col2:
        detect_defects = st.checkbox("Детекция дефектов", value=True)
    with col3:
        generate_conclusion = st.checkbox("Генерация заключения (AI)", value=True)
    
    # Кнопка запуска
    st.markdown("---")
    
    if st.button("🚀 Запустить анализ", type="primary", use_container_width=True):
        if not audio_20_file and not audio_51_file:
            st.error("Пожалуйста, загрузите хотя бы один аудиофайл!")
            return
        
        # Контейнер для результатов
        results_container = st.container()
        
        with results_container:
            # Прогресс бар
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            try:
                all_defects = []
                analysis_results = {}
                
                # Обработка стерео файла
                if audio_20_file:
                    status_text.text("📊 Анализ стерео файла...")
                    progress_bar.progress(10)
                    
                    # Сохранение файла
                    temp_20_path = save_uploaded_file(audio_20_file)
                    
                    if analyze_loudness:
                        status_text.text("📊 Измерение параметров (LUFS, TRUE PEAK, LRA)...")
                        progress_bar.progress(20)
                        
                        analysis_results['stereo'] = analyzer.analyze_file(temp_20_path)
                        
                        st.success("✓ Анализ стерео завершен")
                    
                    if detect_defects:
                        status_text.text("🔍 Детекция дефектов в стерео...")
                        progress_bar.progress(40)
                        
                        defects_20 = detector.analyze_file(temp_20_path, "2.0")
                        all_defects.extend(defects_20)
                        
                        st.success(f"✓ Обнаружено {len(defects_20)} дефектов в стерео")
                    
                    # Удаление временного файла
                    os.unlink(temp_20_path)
                
                # Обработка 5.1 файла
                if audio_51_file:
                    status_text.text("📊 Анализ 5.1 файла...")
                    progress_bar.progress(50)
                    
                    temp_51_path = save_uploaded_file(audio_51_file)
                    
                    if analyze_loudness:
                        status_text.text("📊 Измерение параметров 5.1...")
                        progress_bar.progress(60)
                        
                        analysis_results['surround'] = analyzer.analyze_file(temp_51_path)
                        
                        st.success("✓ Анализ 5.1 завершен")
                    
                    if detect_defects:
                        status_text.text("🔍 Детекция дефектов в 5.1...")
                        progress_bar.progress(70)
                        
                        defects_51 = detector.analyze_file(temp_51_path, "5.1")
                        all_defects.extend(defects_51)
                        
                        st.success(f"✓ Обнаружено {len(defects_51)} дефектов в 5.1")
                    
                    os.unlink(temp_51_path)
                
                # Генерация заключения
                conclusion = ""
                if generate_conclusion and analysis_results:
                    status_text.text("🤖 Генерация AI-заключения...")
                    progress_bar.progress(80)
                    
                    # Берем первый доступный результат анализа
                    primary_analysis = analysis_results.get('stereo') or analysis_results.get('surround')
                    file_info = {
                        'file_name': audio_20_file.name if audio_20_file else audio_51_file.name
                    }
                    
                    conclusion_gen.use_llm = True
                    conclusion = build_streamlit_conclusion(
                        conclusion_gen,
                        primary_analysis,
                        all_defects,
                        file_info,
                    )
                    
                    st.success("✓ Заключение сгенерировано")
                
                # Генерация отчетов
                status_text.text("📄 Генерация отчетов...")
                progress_bar.progress(90)
                
                output_dir = tempfile.mkdtemp()
                primary_analysis = analysis_results.get('stereo') or analysis_results.get('surround')
                
                report_paths = generator.generate_all_reports(
                    primary_analysis,
                    all_defects,
                    conclusion,
                    output_dir
                )
                
                progress_bar.progress(100)
                status_text.text("✅ Анализ завершен!")
                
                # Отображение результатов
                st.markdown("---")
                st.header("📊 Результаты анализа")
                
                # Отображение измерений
                if analysis_results:
                    st.subheader("Измерения громкости")
                    
                    for audio_type, results in analysis_results.items():
                        with st.expander(f"📊 {audio_type.upper()}", expanded=True):
                            measurements = results.get('measurements', {})
                            compliance = results.get('compliance', {})
                            
                            col1, col2, col3 = st.columns(3)
                            
                            with col1:
                                lufs_val = measurements.get('lufs', 'N/A')
                                lufs_ok = compliance.get('lufs_compliant', False)
                                st.metric(
                                    "LUFS",
                                    f"{lufs_val} dB",
                                    delta=f"{results.get('deviations', {}).get('lufs_deviation', 0)} от нормы",
                                    delta_color="inverse"
                                )
                                if lufs_ok:
                                    st.success("✓ Соответствует")
                                else:
                                    st.error("✗ Не соответствует")
                            
                            with col2:
                                tp_val = measurements.get('true_peak', 'N/A')
                                tp_ok = compliance.get('true_peak_compliant', False)
                                st.metric(
                                    "TRUE PEAK",
                                    f"{tp_val} dBTP",
                                    delta=f"{results.get('deviations', {}).get('true_peak_headroom', 0)} запас"
                                )
                                if tp_ok:
                                    st.success("✓ Соответствует")
                                else:
                                    st.error("✗ Не соответствует")
                            
                            with col3:
                                lra_val = measurements.get('lra', 'N/A')
                                lra_ok = compliance.get('lra_compliant', False)
                                st.metric("LRA", f"{lra_val} LU")
                                if lra_ok:
                                    st.success("✓ Соответствует")
                                else:
                                    st.error("✗ Не соответствует")
                
                # Отображение дефектов
                if all_defects:
                    st.subheader(f"🔍 Обнаруженные дефекты ({len(all_defects)})")
                    
                    # Группировка по severity
                    blockers = [d for d in all_defects if getattr(d, 'severity', '') == 'blocker']
                    fix_req = [d for d in all_defects if getattr(d, 'severity', '') == 'fix_required']
                    comment_req = [d for d in all_defects if getattr(d, 'severity', '') == 'comment_required']
                    
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.metric("🚫 Критичные", len(blockers))
                    with col2:
                        st.metric("⚠️ Требуют исправления", len(fix_req))
                    with col3:
                        st.metric("💬 Требуют комментария", len(comment_req))
                    
                    # Таблица дефектов
                    with st.expander("📋 Детальный список дефектов", expanded=False):
                        for i, defect in enumerate(all_defects[:20], 1):  # Первые 20
                            st.markdown(f"**{i}. {getattr(defect, 'description', 'N/A')}**")
                            st.text(f"   Таймкод: {getattr(defect, 'timecode_in', 'N/A')}")
                            st.text(f"   Тип: {getattr(defect, 'type', 'N/A')}")
                            st.text(f"   Важность: {getattr(defect, 'severity', 'N/A')}")
                            st.markdown("---")
                
                # Заключение
                if conclusion:
                    st.subheader("🤖 AI Заключение")
                    st.markdown(conclusion)
                
                # Скачивание отчетов
                st.markdown("---")
                st.header("📥 Скачать отчеты")
                
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    if 'csv' in report_paths:
                        with open(report_paths['csv'], 'rb') as f:
                            st.download_button(
                                "📄 CSV",
                                f,
                                file_name=Path(report_paths['csv']).name,
                                mime="text/csv"
                            )
                
                with col2:
                    if 'pdf' in report_paths:
                        with open(report_paths['pdf'], 'rb') as f:
                            st.download_button(
                                "📕 PDF",
                                f,
                                file_name=Path(report_paths['pdf']).name,
                                mime="application/pdf"
                            )
                
                with col3:
                    if 'docx' in report_paths:
                        with open(report_paths['docx'], 'rb') as f:
                            st.download_button(
                                "📘 DOCX",
                                f,
                                file_name=Path(report_paths['docx']).name,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                            )
                
                with col4:
                    # Создание ZIP со всеми отчетами
                    zip_path = Path(output_dir) / "all_reports.zip"
                    with zipfile.ZipFile(zip_path, 'w') as zipf:
                        for report_type, path in report_paths.items():
                            zipf.write(path, Path(path).name)
                    
                    with open(zip_path, 'rb') as f:
                        st.download_button(
                            "📦 Все (ZIP)",
                            f,
                            file_name="beast_reports.zip",
                            mime="application/zip"
                        )
                
            except Exception as e:
                st.error(f"Ошибка во время анализа: {e}")
                logger.exception("Ошибка в main процессе")


if __name__ == "__main__":
    main()

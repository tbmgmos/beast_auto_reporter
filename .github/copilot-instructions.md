# Copilot Instructions for Beast Auto Reporter

## Project Overview
**Beast Auto Reporter** is a macOS desktop application for automated audio/video QA reporting. It reads a CSV file with defect markers, analyzes audio files (LUFS, TRUE PEAK, LRA), extracts technical data from PDFs (Youlean reports), and generates comprehensive DOCX reports following industry standards.

## Architecture

### Core Components
- **Main UI**: `beast_app_final.py` (PyQt5) - Desktop application with folder selection and progress tracking
- **CSV Processing**: `src/csv_importer.py` - Parses defect markers into `Issue` dataclass objects
- **Audio Analysis**: `src/audio_analyzer.py` - Measures loudness metrics (LUFS, TRUE PEAK, LRA) using `pyloudnorm` and `librosa`
- **PDF Extraction**: `src/pdf_extractor.py` - Extracts Youlean data using PyPDF2 with PyMuPDF fallback
- **Report Generation**: `src/exact_report_generator.py` - Creates DOCX reports with technical tables and conclusions
- **Conclusions**: `src/conclusion_generator.py` - Generates technical/subjective conclusions (with optional LLM via Ollama)

### Data Flow
```
Input Folder (CSV + audio/video/PDFs)
  ↓
Find Files (identify by extension patterns: *_20_cens.wav, *_51_cens.pdf, etc.)
  ↓
Extract Base Name (strip format suffixes, timestamps)
  ↓
Create Output Folder (Desktop/{output_name}_YYYY_MM_DD/)
  ↓
Parse CSV (Issues) → Analyze Audio → Extract PDFs → Generate DOCX
  ↓
Output: {name}.docx + {name}.csv (optional)
```

## File Naming Conventions

The project uses aggressive filename parsing:
- **Audio files**: `*_20_cens.wav`, `*_20_unc.wav`, `*_51_cens.wav`, `*_51_unc.wav`
- **Video files**: `*.mov`, `*.mp4`, `*.mkv`
- **PDF reports**: `*_20_cens.pdf`, `*_51_cens.pdf` (Youlean loudness reports)
- **Parameters**: `параметры.txt` or `parameters.txt`

**Base name extraction** (in `extract_base_name()`):
- Removes: `_20`, `_51`, `_2.0`, `_5.1`, `_stereo`, `_cens*`, `_uncens*`, timestamps
- Example: `petr_2_s1_e2_2.0_cens_2024_09_12.wav` → `petr_2_s1_e2`

## Key Patterns

### CSV Format Support
- **Delimiters**: Auto-detects tab or comma
- **Column mapping**: Supports both English and Russian headers (via `_get_column_value()` with multiple fallbacks)
- **Issue object**: `Issue(timecode_in, timecode_out, description, audio_20_c, audio_20_uc, audio_51_c, audio_51_uc, blocker, fix_required, comment_required, comments)`

### Audio Analysis
- Uses `soundfile` + `pyloudnorm` for accurate loudness measurement
- Sample rate auto-detection (default: 48kHz)
- Fallback handling for corrupted audio files
- Returns: `{lufs, true_peak, lra, channels, duration, format}`

### PDF Data Extraction
- **Primary**: PyPDF2 text extraction
- **Fallback**: PyMuPDF (fitz) for problematic PDFs
- **Regex patterns** for Youlean data: INTEGRATED LUFS, TRUE PEAK, LRA (handles newlines)
- Each PDF key maps to: `{lufs, true_peak, lra, sample_rate, bit_depth, channels}`

### Report Generation
- **Template-based**: Uses DOCX templates from `templates/docx/` (standard_original.docx, me_original.docx)
- **Report types**: "main" (standard), "me" (Music & Effects), "me_ours", "tifflo"
- **Page format**: A3 landscape (42.02cm × 29.70cm) with 2cm margins
- **Content**: Header + marker list (from CSV) + technical table + conclusions

### Conclusion Generation
- **Technical**: Compares measured values (LUFS, TRUE PEAK, LRA) against target parameters
- **Subjective**: Categorizes issues by type (clicks, saliva, wind, etc.) with smart grouping
- **LLM fallback**: Uses Ollama (llama3.2) if enabled, otherwise template-based generation
- **Report-aware**: M&E reports skip LUFS/LRA checks (only TRUE PEAK matters)

## Critical Dependencies
- **PyQt5**: Desktop GUI framework
- **python-docx**: DOCX file generation
- **PyMuPDF (fitz)**: Fallback PDF reading
- **pyloudnorm**: ITU-R BS.1770-4 loudness measurement
- **ffprobe**: Audio metadata extraction (must be in PATH or Homebrew)
- **ollama**: Optional local LLM for conclusions

## Startup Diagnostics
On launch, `run_startup_diagnostics()` checks:
- Python version and environment
- Package availability (PyMuPDF, PyQt5, python-docx)
- ffprobe location (searches /opt/homebrew/bin, /usr/local/bin, /usr/bin)
- Logs full diagnostic report to Desktop

## Threading & UI
- **ProcessingThread**: Runs file analysis on separate thread (QThread)
- **Signals**: `status_update`, `progress_update`, `finished` for UI synchronization
- **Progress steps**: 10% file search → 15% naming → 20% output folder → 70% processing → 100% done

## Common Modifications

### Adding New Report Types
Edit `beast_app_final.py` → `ProcessingThread.run()` and `src/exact_report_generator.py`:
1. Add radio button option in UI
2. Pass `report_type` parameter through pipeline
3. Update parameter validation logic (e.g., M&E ignores LUFS checks)

### Customizing Conclusion Logic
Edit `src/conclusion_generator.py`:
- `generate_technical_conclusion()`: Modify threshold comparisons
- `generate_subjective_conclusion()`: Adjust issue categorization
- `_smart_group_issues()`: Change how issues are grouped (currently by type)

### Supporting New Audio Formats
Edit `src/audio_analyzer.py`:
- `load_audio()`: Add format to soundfile loader
- Ensure sample rate detection works for new format

## Testing Strategy
- **Unit tests**: `tests/test_analyzer.py` for individual modules
- **Integration**: Point app at test folder with minimal CSV + WAV files
- **Manual**: Check output folder structure and DOCX formatting
- **CLI fallback**: Most modules can be tested via `python -c` imports

## Build & Distribution
- **Desktop app**: `pyinstaller Beast_Auto_Reporter.spec` creates `dist/Beast Auto Reporter.app`
- **Requirements**: See `requirements_pinned.txt` for exact versions
- **Notarization**: For production, requires Apple Developer signature
- **Run locally**: `python beast_app_final.py` (requires PyQt5 and dependencies installed)

## Troubleshooting Guide
- **ffprobe not found**: `brew install ffmpeg` (auto-detected from Homebrew paths)
- **PDF extraction fails**: App falls back to PyMuPDF silently, continues with available data
- **GUI freezes**: Check ProcessingThread isn't blocking—use status_update signals
- **Import errors**: Verify `sys.path.insert(0, str(Path(__file__).parent))` in main module

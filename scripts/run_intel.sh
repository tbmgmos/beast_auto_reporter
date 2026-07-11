#!/usr/bin/env bash
# ============================================================================
# Beast Auto Reporter — Запуск Intel (x86_64) версии через Rosetta 2
# ============================================================================
# Использование:
#   ./scripts/run_intel.sh              # Запуск приложения
#   ./scripts/run_intel.sh --test       # Запуск тестов
#   ./scripts/run_intel.sh --verify     # Проверка окружения
#   ./scripts/run_intel.sh --shell      # Интерактивный shell в Intel venv
#   ./scripts/run_intel.sh script.py    # Запуск произвольного скрипта
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[x86_64]${NC} $*"; }
ok()    { echo -e "${GREEN}[x86_64]${NC} $*"; }
err()   { echo -e "${RED}[x86_64]${NC} $*" >&2; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INTEL_VENV="${REPO_ROOT}/venv_x86"
INTEL_PYTHON="${INTEL_VENV}/bin/python"
INTEL_BREW_PREFIX="/usr/local"
APP_ENTRY="${REPO_ROOT}/beast_auto_reporter (v2 beta).py"

# ── Проверки ───────────────────────────────────────────────────────────────
if [[ ! -d "${INTEL_VENV}" ]]; then
    err "Intel venv не найден: ${INTEL_VENV}"
    err "Сначала запустите: ./scripts/setup_intel.sh"
    exit 1
fi

if [[ ! -f "${INTEL_PYTHON}" ]]; then
    err "Intel Python не найден: ${INTEL_PYTHON}"
    exit 1
fi

# ── Формируем Intel-совместимый PATH ──────────────────────────────────────
# Intel Homebrew бинарники имеют приоритет над ARM
export PATH="${INTEL_VENV}/bin:${INTEL_BREW_PREFIX}/bin:${INTEL_BREW_PREFIX}/sbin:${PATH}"

# Для корректной линковки Intel библиотек (libsndfile, etc.)
export DYLD_LIBRARY_PATH="${INTEL_BREW_PREFIX}/lib:${DYLD_LIBRARY_PATH:-}"

# Маркер для приложения
export BEAST_ARCH="x86_64"

# ── Обработка аргументов ──────────────────────────────────────────────────
MODE="${1:-app}"

case "${MODE}" in
    --test|-t)
        info "Запуск тестов (x86_64 эмуляция)..."
        echo ""
        arch -x86_64 "${INTEL_PYTHON}" -c "import platform; print(f'Архитектура: {platform.machine()}')"
        echo ""
        shift || true
        arch -x86_64 "${INTEL_PYTHON}" -m pytest "${REPO_ROOT}/tests/" -v "$@"
        ;;

    --verify|-v)
        info "Проверка Intel окружения..."
        echo ""
        arch -x86_64 "${INTEL_PYTHON}" -c "
import sys, platform, shutil, subprocess

print(f'Python:       {sys.version}')
print(f'Архитектура:  {platform.machine()}')
print(f'Executable:   {sys.executable}')
print()

for mod, pkg in [
    ('PyQt5', 'PyQt5'), ('pyloudnorm', 'pyloudnorm'),
    ('librosa', 'librosa'), ('soundfile', 'soundfile'),
    ('docx', 'python-docx'), ('pandas', 'pandas'),
    ('numpy', 'numpy'), ('scipy', 'scipy'),
    ('yaml', 'pyyaml'), ('PIL', 'Pillow'),
]:
    try:
        __import__(mod)
        print(f'  ✓ {pkg}')
    except ImportError as e:
        print(f'  ✗ {pkg}: {e}')

ffmpeg = shutil.which('ffmpeg')
if ffmpeg:
    arch_info = subprocess.check_output(['file', ffmpeg]).decode()
    tag = 'x86_64' if 'x86_64' in arch_info else 'NOT x86_64'
    print(f'  ✓ ffmpeg ({tag}): {ffmpeg}')
else:
    print('  ✗ ffmpeg не найден')
"
        ;;

    --shell|-s)
        info "Интерактивный Intel shell..."
        info "Для выхода: exit"
        echo ""
        arch -x86_64 env \
            PATH="${PATH}" \
            DYLD_LIBRARY_PATH="${DYLD_LIBRARY_PATH}" \
            BEAST_ARCH="x86_64" \
            VIRTUAL_ENV="${INTEL_VENV}" \
            PS1="(x86_64-venv) \$ " \
            /bin/bash --norc
        ;;

    --help|-h)
        echo "Beast Auto Reporter — Intel Runner"
        echo ""
        echo "Использование:"
        echo "  ./scripts/run_intel.sh              Запуск приложения"
        echo "  ./scripts/run_intel.sh --test       Запуск тестов"
        echo "  ./scripts/run_intel.sh --verify     Проверка окружения"
        echo "  ./scripts/run_intel.sh --shell      Интерактивный shell"
        echo "  ./scripts/run_intel.sh script.py    Запуск скрипта"
        ;;

    --*)
        err "Неизвестный флаг: ${MODE}"
        err "Используйте --help для справки"
        exit 1
        ;;

    *)
        # Если передан .py файл — запускаем его, иначе запускаем приложение
        if [[ "${MODE}" == *.py ]]; then
            SCRIPT="${MODE}"
            shift
            info "Запуск ${SCRIPT} (x86_64 эмуляция)..."
            arch -x86_64 "${INTEL_PYTHON}" "${SCRIPT}" "$@"
        else
            info "Запуск Beast Auto Reporter (x86_64 эмуляция)..."
            arch -x86_64 "${INTEL_PYTHON}" "${APP_ENTRY}" "$@"
        fi
        ;;
esac

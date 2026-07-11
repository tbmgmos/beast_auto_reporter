#!/usr/bin/env bash
# ============================================================================
# Beast Auto Reporter — Запуск ARM (Apple Silicon) версии
# ============================================================================
# Использование:
#   ./scripts/run_arm.sh              # Запуск приложения
#   ./scripts/run_arm.sh --test       # Запуск тестов
#   ./scripts/run_arm.sh --verify     # Проверка окружения
#   ./scripts/run_arm.sh script.py    # Запуск произвольного скрипта
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[arm64]${NC} $*"; }
ok()    { echo -e "${GREEN}[arm64]${NC} $*"; }
err()   { echo -e "${RED}[arm64]${NC} $*" >&2; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARM_VENV="${REPO_ROOT}/venv"
ARM_PYTHON="${ARM_VENV}/bin/python"
ARM_BREW_PREFIX="/opt/homebrew"
APP_ENTRY="${REPO_ROOT}/beast_auto_reporter (v2 beta).py"

# ── Проверки ───────────────────────────────────────────────────────────────
if [[ ! -d "${ARM_VENV}" ]]; then
    err "ARM venv не найден: ${ARM_VENV}"
    err "Создайте: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

export PATH="${ARM_VENV}/bin:${ARM_BREW_PREFIX}/bin:${ARM_BREW_PREFIX}/sbin:${PATH}"
export BEAST_ARCH="arm64"

MODE="${1:-app}"

case "${MODE}" in
    --test|-t)
        info "Запуск тестов (arm64)..."
        shift || true
        "${ARM_PYTHON}" -m pytest "${REPO_ROOT}/tests/" -v "$@"
        ;;

    --verify|-v)
        info "Проверка ARM окружения..."
        echo ""
        "${ARM_PYTHON}" -c "
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
    tag = 'arm64' if 'arm64' in arch_info else 'NOT arm64'
    print(f'  ✓ ffmpeg ({tag}): {ffmpeg}')
else:
    print('  ✗ ffmpeg не найден')
"
        ;;

    --help|-h)
        echo "Beast Auto Reporter — ARM Runner"
        echo ""
        echo "Использование:"
        echo "  ./scripts/run_arm.sh              Запуск приложения"
        echo "  ./scripts/run_arm.sh --test       Запуск тестов"
        echo "  ./scripts/run_arm.sh --verify     Проверка окружения"
        echo "  ./scripts/run_arm.sh script.py    Запуск скрипта"
        ;;

    --*)
        err "Неизвестный флаг: ${MODE}"
        exit 1
        ;;

    *)
        if [[ "${MODE}" == *.py ]]; then
            SCRIPT="${MODE}"
            shift
            info "Запуск ${SCRIPT} (arm64)..."
            "${ARM_PYTHON}" "${SCRIPT}" "$@"
        else
            info "Запуск Beast Auto Reporter (arm64)..."
            "${ARM_PYTHON}" "${APP_ENTRY}" "$@"
        fi
        ;;
esac

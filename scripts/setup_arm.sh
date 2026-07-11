#!/usr/bin/env bash
# ============================================================================
# Beast Auto Reporter — ARM (Apple Silicon) Environment Setup
# ============================================================================
# Создаёт ARM venv и устанавливает все зависимости.
#
# Использование:
#   chmod +x scripts/setup_arm.sh
#   ./scripts/setup_arm.sh
# ============================================================================

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERR]${NC}   $*" >&2; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARM_VENV="${REPO_ROOT}/venv"
ARM_BREW="/opt/homebrew/bin/brew"
ARM_PYTHON="/opt/homebrew/bin/python3"

info "Корень проекта: ${REPO_ROOT}"

# ── Homebrew ───────────────────────────────────────────────────────────────
echo ""
info "═══ Шаг 1/4: ARM Homebrew ═══"
if [[ -f "${ARM_BREW}" ]]; then
    ok "ARM Homebrew установлен"
else
    err "ARM Homebrew не найден. Установите: https://brew.sh"
    exit 1
fi

# ── Python ─────────────────────────────────────────────────────────────────
echo ""
info "═══ Шаг 2/4: ARM Python ═══"
if [[ -f "${ARM_PYTHON}" ]]; then
    ok "ARM Python: $(${ARM_PYTHON} --version)"
else
    info "Устанавливаю Python через Homebrew..."
    brew install python@3.13
    ok "ARM Python установлен"
fi

# ── Системные зависимости ─────────────────────────────────────────────────
echo ""
info "═══ Шаг 3/4: Системные зависимости ═══"

for pkg in ffmpeg libsndfile; do
    if brew list "${pkg}" &>/dev/null; then
        ok "${pkg} установлен"
    else
        info "Устанавливаю ${pkg}..."
        brew install "${pkg}"
        ok "${pkg} установлен"
    fi
done

# ── Virtual Environment ───────────────────────────────────────────────────
echo ""
info "═══ Шаг 4/4: ARM Virtual Environment ═══"

if [[ -d "${ARM_VENV}" ]]; then
    warn "ARM venv уже существует: ${ARM_VENV}"
    read -p "Пересоздать? (y/N): " RECREATE
    if [[ "${RECREATE}" =~ ^[Yy]$ ]]; then
        rm -rf "${ARM_VENV}"
    else
        info "Используем существующий venv"
    fi
fi

if [[ ! -d "${ARM_VENV}" ]]; then
    info "Создаю ARM venv..."
    "${ARM_PYTHON}" -m venv "${ARM_VENV}"
    ok "ARM venv создан"
fi

info "Устанавливаю зависимости..."
"${ARM_VENV}/bin/python" -m pip install --upgrade pip
"${ARM_VENV}/bin/pip" install -r "${REPO_ROOT}/requirements.txt"
ok "Все зависимости установлены"

echo ""
ok "ARM окружение готово!"
echo ""
info "Запуск:  ./scripts/run_arm.sh"
info "Тесты:  ./scripts/run_arm.sh --test"
echo ""

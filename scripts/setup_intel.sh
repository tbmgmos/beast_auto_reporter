#!/usr/bin/env bash
# ============================================================================
# Beast Auto Reporter — Intel (x86_64) Environment Setup
# ============================================================================
# Устанавливает Intel-версию Homebrew, Python, FFmpeg и создаёт x86_64 venv
# со всеми зависимостями. Запускается на Apple Silicon через Rosetta 2.
#
# Использование:
#   chmod +x scripts/setup_intel.sh
#   ./scripts/setup_intel.sh
# ============================================================================

set -euo pipefail

# ── Цвета ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERR]${NC}   $*" >&2; }

# ── Проверка архитектуры ───────────────────────────────────────────────────
CURRENT_ARCH=$(uname -m)
info "Текущая архитектура: ${CURRENT_ARCH}"

if [[ "$CURRENT_ARCH" == "arm64" ]]; then
    info "Apple Silicon обнаружен — проверяю Rosetta 2..."
    if ! arch -x86_64 /usr/bin/true 2>/dev/null; then
        warn "Rosetta 2 не установлена. Устанавливаю..."
        softwareupdate --install-rosetta --agree-to-license
        ok "Rosetta 2 установлена"
    else
        ok "Rosetta 2 доступна"
    fi
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INTEL_VENV="${REPO_ROOT}/venv_x86"
INTEL_BREW="/usr/local/bin/brew"
INTEL_BREW_PREFIX="/usr/local"

info "Корень проекта: ${REPO_ROOT}"
info "Intel venv:     ${INTEL_VENV}"

# ── Шаг 1: Intel Homebrew ─────────────────────────────────────────────────
echo ""
info "═══ Шаг 1/5: Intel Homebrew ═══"

if [[ -f "${INTEL_BREW}" ]]; then
    ok "Intel Homebrew уже установлен: ${INTEL_BREW}"
else
    info "Устанавливаю Intel Homebrew в /usr/local..."
    arch -x86_64 /bin/bash -c \
        "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    ok "Intel Homebrew установлен"
fi

# Функция для запуска Intel brew
ibrew() {
    arch -x86_64 "${INTEL_BREW}" "$@"
}

# ── Шаг 2: Intel Python ───────────────────────────────────────────────────
echo ""
info "═══ Шаг 2/5: Intel Python ═══"

INTEL_PYTHON="${INTEL_BREW_PREFIX}/bin/python3"

if [[ -f "${INTEL_PYTHON}" ]]; then
    PYVER=$("${INTEL_PYTHON}" --version 2>&1)
    PYARCH=$(arch -x86_64 "${INTEL_PYTHON}" -c "import platform; print(platform.machine())")
    ok "Intel Python найден: ${PYVER} (${PYARCH})"
else
    info "Устанавливаю Intel Python через Intel Homebrew..."
    ibrew install python@3.13
    ok "Intel Python установлен"
fi

# Определяем точный путь к python3 (может быть python3.13)
if [[ -f "${INTEL_BREW_PREFIX}/bin/python3" ]]; then
    INTEL_PYTHON="${INTEL_BREW_PREFIX}/bin/python3"
elif [[ -f "${INTEL_BREW_PREFIX}/bin/python3.13" ]]; then
    INTEL_PYTHON="${INTEL_BREW_PREFIX}/bin/python3.13"
else
    # Ищем любой python3.x
    INTEL_PYTHON=$(ls "${INTEL_BREW_PREFIX}"/bin/python3.* 2>/dev/null | head -1 || true)
    if [[ -z "${INTEL_PYTHON}" ]]; then
        err "Не удалось найти Intel Python после установки"
        exit 1
    fi
fi

info "Используется: ${INTEL_PYTHON}"

# ── Шаг 3: Intel FFmpeg ───────────────────────────────────────────────────
echo ""
info "═══ Шаг 3/5: Intel FFmpeg ═══"

INTEL_FFMPEG="${INTEL_BREW_PREFIX}/bin/ffmpeg"
INTEL_FFPROBE="${INTEL_BREW_PREFIX}/bin/ffprobe"

if [[ -f "${INTEL_FFMPEG}" ]]; then
    FFARCH=$(file "${INTEL_FFMPEG}" | grep -o 'x86_64' || echo "unknown")
    ok "Intel FFmpeg найден (${FFARCH})"
else
    info "Устанавливаю Intel FFmpeg..."
    ibrew install ffmpeg
    ok "Intel FFmpeg установлен"
fi

# ── Шаг 4: Intel libsndfile ───────────────────────────────────────────────
echo ""
info "═══ Шаг 4/5: Системные зависимости (Intel) ═══"

if ibrew list libsndfile &>/dev/null; then
    ok "Intel libsndfile уже установлен"
else
    info "Устанавливаю Intel libsndfile..."
    ibrew install libsndfile
    ok "Intel libsndfile установлен"
fi

# ── Шаг 5: Создание Intel venv ────────────────────────────────────────────
echo ""
info "═══ Шаг 5/5: Intel Virtual Environment ═══"

if [[ -d "${INTEL_VENV}" ]]; then
    warn "Intel venv уже существует: ${INTEL_VENV}"
    read -p "Пересоздать? (y/N): " RECREATE
    if [[ "${RECREATE}" =~ ^[Yy]$ ]]; then
        info "Удаляю старый venv..."
        rm -rf "${INTEL_VENV}"
    else
        info "Используем существующий venv"
    fi
fi

if [[ ! -d "${INTEL_VENV}" ]]; then
    info "Создаю Intel venv..."
    arch -x86_64 "${INTEL_PYTHON}" -m venv "${INTEL_VENV}"
    ok "Intel venv создан"
fi

# Активируем Intel venv и устанавливаем зависимости
info "Устанавливаю зависимости в Intel venv..."

# Обновляем pip
arch -x86_64 "${INTEL_VENV}/bin/python" -m pip install --upgrade pip

# Устанавливаем зависимости
arch -x86_64 "${INTEL_VENV}/bin/pip" install -r "${REPO_ROOT}/requirements.txt"

ok "Все зависимости установлены"

# ── Верификация ────────────────────────────────────────────────────────────
echo ""
info "═══ Верификация установки ═══"

VERIFY_SCRIPT='
import sys
import platform

print(f"Python:       {sys.version}")
print(f"Архитектура:  {platform.machine()}")
print(f"Executable:   {sys.executable}")
print()

errors = []
modules = {
    "PyQt5":        "PyQt5",
    "pyloudnorm":   "pyloudnorm",
    "librosa":      "librosa",
    "soundfile":    "soundfile",
    "docx":         "python-docx",
    "pandas":       "pandas",
    "numpy":        "numpy",
    "scipy":        "scipy",
    "yaml":         "pyyaml",
    "PIL":          "Pillow",
}

for mod, pkg in modules.items():
    try:
        __import__(mod)
        print(f"  ✓ {pkg}")
    except ImportError as e:
        print(f"  ✗ {pkg}: {e}")
        errors.append(pkg)

# Проверяем ffmpeg
import shutil
ffmpeg = shutil.which("ffmpeg")
if ffmpeg:
    import subprocess
    arch = subprocess.check_output(["file", ffmpeg]).decode()
    if "x86_64" in arch:
        print(f"  ✓ ffmpeg (x86_64): {ffmpeg}")
    else:
        print(f"  ⚠ ffmpeg найден, но НЕ x86_64: {ffmpeg}")
else:
    print("  ✗ ffmpeg не найден в PATH")
    errors.append("ffmpeg")

if errors:
    print(f"\n⚠ Проблемы с: {", ".join(errors)}")
    sys.exit(1)
else:
    print("\n✓ Все компоненты Intel-версии установлены успешно!")
'

# Запускаем верификацию с Intel PATH
arch -x86_64 env PATH="${INTEL_BREW_PREFIX}/bin:${INTEL_VENV}/bin:${PATH}" \
    "${INTEL_VENV}/bin/python" -c "${VERIFY_SCRIPT}"

echo ""
ok "Intel окружение готово!"
echo ""
info "Запуск приложения:"
echo "  ./scripts/run_intel.sh"
echo ""
info "Запуск тестов:"
echo "  ./scripts/run_intel.sh --test"
echo ""

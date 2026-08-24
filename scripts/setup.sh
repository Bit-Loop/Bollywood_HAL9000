#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${PYTHON:-python3}"

"${PYTHON_BIN}" - <<'PY'
import sys
if not ((3, 12) <= sys.version_info[:2] < (3, 15)):
    raise SystemExit("HAL 9000 requires Python 3.12 through 3.14")
PY

if [[ ! -x "${VENV_PATH}/bin/python" ]]; then
    "${PYTHON_BIN}" -m venv "${VENV_PATH}"
elif grep -q '^include-system-site-packages = true$' "${VENV_PATH}/pyvenv.cfg"; then
    echo "Rebuilding the legacy system-site development environment in isolation."
    "${PYTHON_BIN}" -m venv --clear "${VENV_PATH}"
fi

"${VENV_PATH}/bin/python" -m pip install --upgrade "pip>=25.2,<27"
"${VENV_PATH}/bin/python" -m pip install -e "${PROJECT_ROOT}[xtts,test,package]"

echo "HAL 9000 development environment is ready."
echo "Launch: ${PROJECT_ROOT}/scripts/dev.sh"

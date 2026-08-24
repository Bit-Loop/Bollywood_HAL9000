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
# DataSketches 5.2.0's generated Python 3.14 bindings assign through a const
# view under GCC 16. The supported wheel build is otherwise clean; permissive
# mode is scoped to this dependency installation until upstream ships cp314.
if "${VENV_PATH}/bin/python" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 14))'; then
    env CXXFLAGS="${CXXFLAGS:-} -fpermissive" \
        "${VENV_PATH}/bin/python" -m pip install -e "${PROJECT_ROOT}[xtts,test,package]"
else
    "${VENV_PATH}/bin/python" -m pip install -e "${PROJECT_ROOT}[xtts,test,package]"
fi

echo "HAL 9000 development environment is ready."
echo "Launch: ${PROJECT_ROOT}/scripts/dev.sh"

#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Run ${PROJECT_ROOT}/scripts/setup.sh first." >&2
    exit 2
fi

cd "${PROJECT_ROOT}"
"${PYTHON_BIN}" -m build
echo "Wheel and source package created under ${PROJECT_ROOT}/dist/."

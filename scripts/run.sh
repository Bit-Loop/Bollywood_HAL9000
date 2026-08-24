#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HAL_EXECUTABLE="${PROJECT_ROOT}/.venv/bin/hal9000"

if [[ ! -x "${HAL_EXECUTABLE}" ]]; then
    echo "HAL 9000 is not set up. Run ${PROJECT_ROOT}/scripts/setup.sh." >&2
    exit 2
fi

exec "${HAL_EXECUTABLE}" "$@"

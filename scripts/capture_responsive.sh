#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="${PYTHON:-python3}"
fi

QA_ROOT="$(mktemp -d -t hal9000-layout-XXXXXX)"
trap 'rm -rf -- "${QA_ROOT}"' EXIT
mkdir -p "${QA_ROOT}/config/hal9000" "${PROJECT_ROOT}/artifacts/screenshots"
printf '%s\n' '{"version":1,"general":{"setup_complete":true,"start_in_standby":true}}' > "${QA_ROOT}/config/hal9000/config.json"

SIZES=("1080x1920" "900x1600" "720x1280" "800x1000" "1280x900" "600x800")
for SIZE in "${SIZES[@]}"; do
    XDG_CONFIG_HOME="${QA_ROOT}/config" \
    XDG_DATA_HOME="${QA_ROOT}/data" \
    XDG_STATE_HOME="${QA_ROOT}/state" \
    XDG_CACHE_HOME="${QA_ROOT}/cache" \
    QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" \
    QT_QUICK_BACKEND="${QT_QUICK_BACKEND:-software}" \
    PYTHONPATH="${PROJECT_ROOT}/src" \
    "${PYTHON_BIN}" -m hal9000 --no-services --size "${SIZE}" \
        --screenshot "${PROJECT_ROOT}/artifacts/screenshots/closed-${SIZE}.png"
done

XDG_CONFIG_HOME="${QA_ROOT}/config" XDG_DATA_HOME="${QA_ROOT}/data" \
XDG_STATE_HOME="${QA_ROOT}/state" XDG_CACHE_HOME="${QA_ROOT}/cache" \
QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" QT_QUICK_BACKEND="${QT_QUICK_BACKEND:-software}" \
PYTHONPATH="${PROJECT_ROOT}/src" "${PYTHON_BIN}" -m hal9000 --no-services --open-manual \
    --size 900x1600 --screenshot "${PROJECT_ROOT}/artifacts/screenshots/manual-900x1600.png"

XDG_CONFIG_HOME="${QA_ROOT}/config" XDG_DATA_HOME="${QA_ROOT}/data" \
XDG_STATE_HOME="${QA_ROOT}/state" XDG_CACHE_HOME="${QA_ROOT}/cache" \
QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" QT_QUICK_BACKEND="${QT_QUICK_BACKEND:-software}" \
PYTHONPATH="${PROJECT_ROOT}/src" "${PYTHON_BIN}" -m hal9000 --no-services --open-settings \
    --size 1280x900 --screenshot "${PROJECT_ROOT}/artifacts/screenshots/settings-1280x900.png"

echo "Responsive screenshots written to ${PROJECT_ROOT}/artifacts/screenshots/."

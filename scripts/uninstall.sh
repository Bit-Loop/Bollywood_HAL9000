#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${XDG_DATA_HOME:-${HOME}/.local/share}"
CONFIG_ROOT="${XDG_CONFIG_HOME:-${HOME}/.config}"
CACHE_ROOT="${XDG_CACHE_HOME:-${HOME}/.cache}"
STATE_ROOT="${XDG_STATE_HOME:-${HOME}/.local/state}"

rm -f "${HOME}/.local/bin/hal9000"
rm -f "${DATA_ROOT}/applications/com.bitloop.HAL9000.desktop"
rm -f "${DATA_ROOT}/icons/hicolor/scalable/apps/com.bitloop.HAL9000.svg"
rm -f "${CONFIG_ROOT}/autostart/com.bitloop.HAL9000.desktop"
rm -rf "${DATA_ROOT}/hal9000"

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "${CONFIG_ROOT}/hal9000" "${CACHE_ROOT}/hal9000" "${STATE_ROOT}/hal9000"
    echo "HAL 9000 and its local settings/model cache were removed."
else
    echo "HAL 9000 was removed. Settings and model cache were preserved; use --purge to remove them."
fi

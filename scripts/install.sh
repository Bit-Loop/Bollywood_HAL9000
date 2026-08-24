#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="${XDG_DATA_HOME:-${HOME}/.local/share}"
BIN_ROOT="${HOME}/.local/bin"
INSTALL_ROOT="${DATA_ROOT}/hal9000"
APPLICATION_ROOT="${DATA_ROOT}/applications"
ICON_ROOT="${DATA_ROOT}/icons/hicolor/scalable/apps"

mkdir -p "${INSTALL_ROOT}" "${BIN_ROOT}" "${APPLICATION_ROOT}" "${ICON_ROOT}"
if [[ ! -x "${INSTALL_ROOT}/venv/bin/python" ]]; then
    "${PYTHON:-python3}" -m venv "${INSTALL_ROOT}/venv"
elif grep -q '^include-system-site-packages = true$' "${INSTALL_ROOT}/venv/pyvenv.cfg"; then
    echo "Rebuilding the legacy system-site installation in isolation."
    "${PYTHON:-python3}" -m venv --clear "${INSTALL_ROOT}/venv"
fi
"${INSTALL_ROOT}/venv/bin/python" -m pip install --upgrade "pip>=25.2,<27"
if "${INSTALL_ROOT}/venv/bin/python" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 14))'; then
    env CXXFLAGS="${CXXFLAGS:-} -fpermissive" \
        "${INSTALL_ROOT}/venv/bin/python" -m pip install "${PROJECT_ROOT}[xtts]"
else
    "${INSTALL_ROOT}/venv/bin/python" -m pip install "${PROJECT_ROOT}[xtts]"
fi

cp "${PROJECT_ROOT}/packaging/com.bitloop.HAL9000.desktop" "${APPLICATION_ROOT}/com.bitloop.HAL9000.desktop"
cp "${PROJECT_ROOT}/src/hal9000/resources/hal9000.svg" "${ICON_ROOT}/com.bitloop.HAL9000.svg"

for command_name in hal9000 hal-self hal-self-mcp; do
    launcher="${BIN_ROOT}/${command_name}"
    printf '%s\n' '#!/usr/bin/env sh' \
        "exec \"${INSTALL_ROOT}/venv/bin/${command_name}\" \"\$@\"" > "${launcher}"
    chmod 0755 "${launcher}"
done

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${APPLICATION_ROOT}" >/dev/null 2>&1 || true
fi

echo "HAL 9000 installed for ${USER}. Launch with: hal9000"

# Bollywood HAL 9000

A native Linux HAL 9000 console and local voice frontend for
[Hermes Agent](https://github.com/NousResearch/hermes-agent). Hermes remains the
agent, session, model, memory, MCP, Codex, browser, terminal, tool, and approval
orchestrator. This application owns the physical HAL interface, lifecycle,
local audio capture, wake detection, transcription, and speech output.

The visible standby shell is deliberately only the HAL nameplate, dim optical
eye, and speaker grille. Double-clicking the grille folds it open into the
shared Hermes conversation; triple-clicking closes it without the preceding
double-click reopening it.

## Architecture

- Python 3.12–3.14, PySide6/Qt 6, and responsive QML; native X11 and Wayland
  windows where Qt supports the compositor.
- An explicit state machine for boot, standby, wake, listening, transcription,
  thinking, tools, approvals, speech, manual mode, errors, and disabled mode.
- Hermes `serve` JSON-RPC over WebSocket, including durable session resume,
  streaming text, actual tool events, cancellation, approvals, health probing,
  reconnect/backoff, and owned-process shutdown.
- Session-scoped Hermes model selection from its authenticated provider list.
  HAL uses the isolated `codex-cloud` profile with the ChatGPT-subscription
  `openai-codex/gpt-5.6-sol` route at medium reasoning, without changing other
  Hermes sessions or global config and without Hybrid-MoA's local advisors.
- One 16 kHz microphone stream shared by Sherpa open-vocabulary wake detection,
  VAD capture, and push-to-talk. Raw microphone audio is not persisted.
- Faster-Whisper `small`/English, with CUDA inference attempted first and a
  tested CPU/int8 fallback when CTranslate2 cannot use the host CUDA runtime.
- Real Coqui XTTS-v2 inference with `CoderCowMoo/XTTS-v2.0-HAL-9000`, plus real
  Piper inference with `campwill/HAL-9000-Piper-TTS`. Piper is the low-latency
  default; XTTS remains an explicit high-fidelity option, and Auto chooses the
  faster healthy engine. HAL sanitizes streamed Markdown and begins synthesizing
  short complete phrases before the full response finishes.
- Atomic versioned XDG configuration, desktop-keyring storage for an optional
  remote Hermes token, managed model caches, rotating logs, and reversible XDG
  autostart.

Model weights are downloaded directly from their upstream repositories into
the user cache and are not redistributed in this project. The XTTS cache is
approximately 5.3 GiB on disk; the first-run screen discloses a 5.6 GB download
allowance before setup begins.

## Install and launch

Development:

```bash
./scripts/setup.sh
./scripts/dev.sh
```

User installation and normal launch:

```bash
./scripts/install.sh
hal9000
```

Dedicated-display launch:

```bash
hal9000 --fullscreen
```

The first launch opens a guided local speech setup. Typed mode remains usable
if a microphone, wake model, STT model, GPU, or either voice is unavailable.
Downloads and model loading run outside the UI thread.

Other useful launch forms:

```bash
./scripts/run.sh --windowed
./scripts/run.sh --fullscreen
./scripts/run.sh --windowed --size 900x1600
```

Create wheel and source distributions with `./scripts/package.sh`. Remove the
user installation with `./scripts/uninstall.sh`; add `--purge` only when you
also intend to remove HAL configuration, state, logs, and cached model weights.

## Controls

- Double-click speaker grille: open the manual console.
- Triple-click grille or exposed handle: close the console.
- Right-click empty black chassis space: open settings. The eye and speaker
  grille deliberately retain their own controls.
- `Ctrl+Shift+S`: open settings while HAL has keyboard focus.
- `Ctrl+L`: focus the open manual composer.
- `Ctrl+Enter`: send the typed prompt.
- `Ctrl+Shift+M`: toggle microphone mute.
- `Esc`: close settings or the manual drawer when no approval is unresolved.
- `F11`: toggle fullscreen.

Settings include window/monitor/autostart behavior, ZIP/postal context for
weather and local questions, a live Hermes model selector and reasoning level,
Hermes backend management, wake and STT controls, input/output devices and live
mic level, voice mode and A/B playback, benchmark results, appearance controls,
approval status, and a diagnostics report with log/config reveal actions.

## Configuration and security

HAL follows XDG locations:

```text
~/.config/hal9000/config.json
~/.local/share/hal9000/
~/.local/state/hal9000/logs/hal9000.log
~/.cache/hal9000/models/
```

Hermes conversations remain in Hermes storage. Remote tokens are read from
`HAL9000_HERMES_TOKEN` or stored through the desktop keyring; they are never
written to `config.json` or shown after entry. Voice activation is not
authentication, and HAL forwards approval decisions through Hermes rather than
bypassing its safeguards. HAL terminates only a Hermes backend it launched.

## Verification

Fast deterministic and UI suite:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q -m 'not models'
```

Downloaded-model inference suite:

```bash
HAL9000_RUN_MODEL_TESTS=1 .venv/bin/pytest -q tests/test_model_inference.py
```

Regenerate the required responsive captures:

```bash
./scripts/capture_responsive.sh
```

The capture script verifies 1080×1920, 900×1600, 720×1280, 800×1000,
1280×900, and the 600×800 minimum, plus the manual and settings surfaces.
Artifacts are written under `artifacts/screenshots/` and are intentionally not
included in source distributions.

All direct dependencies and the compatibility-sensitive XTTS stack are exactly
pinned in `pyproject.toml`. Both setup scripts create isolated virtual
environments so unrelated host Python packages cannot change the runtime.

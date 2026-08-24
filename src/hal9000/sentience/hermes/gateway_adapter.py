"""Canonical Hermes Gateway MCP registration payload; no agent-loop fork."""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SelfMcpRegistration:
    name: str = "hal-self"
    module: str = "hal9000.sentience.hermes.self_mcp_server"

    @property
    def command(self) -> str:
        return sys.executable

    @property
    def args(self) -> tuple[str, ...]:
        return ("-m", self.module)

    def add_params(self, *, profile: str = "") -> dict:
        return {
            "name": self.name,
            "config": {
                "command": self.command,
                "args": list(self.args),
                "env": {},
                "enabled": True,
            },
            **({"profile": profile} if profile else {}),
        }

    def matches(self, server: dict) -> bool:
        return (
            str(server.get("name") or "") == self.name
            and str(server.get("command") or "") == self.command
            and tuple(map(str, server.get("args") or ())) == self.args
            and bool(server.get("enabled", True))
        )

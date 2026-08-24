"""Small stable UI payload for the restrained Settings diagnostics pane."""

from __future__ import annotations


def diagnostics_ui_model(report: dict) -> dict:
    storage = report.get("storage") or {}
    degradation = report.get("degradation") or {}
    return {
        "capabilities": list(report.get("capabilities") or []),
        "degradation": degradation,
        "storage": {
            "used": storage.get("total_bytes", 0),
            "budget": storage.get("budget_bytes", 0),
            "pressure": storage.get("pressure", "unknown"),
        },
        "integrity": report.get("integrity") or {},
        "sketches": list(report.get("sketches") or []),
        "recentEvents": list(report.get("recent_exact_events") or []),
    }

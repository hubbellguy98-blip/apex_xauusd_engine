"""Shared selector profile loading and normalization.

Backtests, shadow runs, and demo execution should interpret the same profile
keys the same way. This module keeps that translation in one place.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "backtest_profiles.json"
DEFAULT_SHADOW_PROFILE = "v3_candidate_safety"


def load_selector_profile(profile_name: str, profile_path: str | Path | None = None) -> dict[str, Any]:
    """Load a named selector/backtest profile from JSON."""

    path = Path(profile_path) if profile_path else DEFAULT_PROFILE_PATH
    profiles = json.loads(path.read_text(encoding="utf-8"))
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles))
        raise ValueError(f"Unknown selector profile {profile_name!r}. Available profiles: {available}")
    profile = _json_copy(profiles[profile_name])
    profile["profile_name"] = profile_name
    profile["profile_path"] = str(path)
    return profile


def normalize_selector_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Map profile keys into the selector/generator config contract."""

    minimum_rr = float(profile.get("minimum_rr", 1.5))
    minimum_score = float(profile.get("minimum_score", 88.0))
    thresholds = dict(profile.get("displacement_thresholds", {}) or {})
    normalized = {
        "profile_name": profile.get("profile_name"),
        "enabled_strategies": list(profile.get("enabled_strategies", []) or []),
        "disabled_strategies": list(profile.get("disabled_strategies", []) or []),
        "minimum_score": minimum_score,
        "minimum_rr": minimum_rr,
        "min_rr": minimum_rr,
        "minimum_setup_score": float(profile.get("minimum_setup_score", minimum_score / 10.0)),
        "strategy_min_rr": dict(profile.get("strategy_min_rr", {}) or {}),
        "strategy_min_scores": dict(profile.get("strategy_min_scores", {}) or {}),
        "session_filters": dict(profile.get("session_filters", {}) or {}),
        "strict_displacement": bool(profile.get("strict_displacement", False)),
        "displacement_mode": str(
            profile.get(
                "displacement_mode",
                "reject_weak_or_unverified" if profile.get("strict_displacement") else "off",
            )
        ),
        "displacement_thresholds": thresholds,
        "early_trap_filter": dict(profile.get("early_trap_filter", {}) or {}),
        "setup_timeframe": str(profile.get("setup_timeframe", "1m")),
        "entry_timeframe": str(profile.get("entry_timeframe", "1m")),
        "bias_timeframe": str(profile.get("bias_timeframe", "1h")),
        "entry_mode": str(profile.get("entry_mode", "balanced")),
        "minimum_risk_to_cost_ratio": float(profile.get("minimum_risk_to_cost_ratio", 0.0)),
        "cost_adjusted_target_buffer_rr": float(profile.get("cost_adjusted_target_buffer_rr", 0.0)),
        "spread_price": float(profile.get("spread_price", 0.0)),
        "slippage_price": float(profile.get("slippage_price", 0.0)),
        "target_ladder": dict(profile.get("target_ladder", {}) or {}),
        "deployment_gate": dict(profile.get("deployment_gate", {}) or {}),
    }
    if "body_to_range_ratio" in thresholds:
        normalized["displacement_min_body_to_range"] = float(thresholds["body_to_range_ratio"])
    if "range_to_atr_ratio" in thresholds:
        normalized["displacement_min_range_to_atr"] = float(thresholds["range_to_atr_ratio"])
    if "close_position_score" in thresholds:
        normalized["displacement_min_close_position"] = float(thresholds["close_position_score"])
    return normalized


def profile_hash(profile: Mapping[str, Any]) -> str:
    payload = json.dumps(profile, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))

from __future__ import annotations

import json

from reports.ai.gemini_client import GeminiClient
from reports.report_config import ReportingConfig


FALLBACK_SUMMARY = (
    "AI summary unavailable. The deterministic metrics and verification files were generated successfully; "
    "review those files for the verified trading numbers."
)


def build_prompt(period: str, metrics: dict, verification: dict) -> str:
    verified_payload = {
        "period": period,
        "metrics": metrics,
        "verification": {
            "status": verification.get("status"),
            "confidence": verification.get("confidence"),
            "issue_counts": verification.get("issue_counts", {}),
            "checks": verification.get("checks", {}),
        },
        "rules": [
            "Do not calculate or invent numbers.",
            "Only explain the verified JSON metrics provided here.",
            "Call out weaknesses, risks, and data-quality issues plainly.",
            "Keep the narrative concise and suitable for Telegram plus Markdown.",
        ],
    }
    return "Write a weekly institutional trading report narrative from this verified JSON only:\n" + json.dumps(
        verified_payload,
        indent=2,
        sort_keys=True,
    )


def write_ai_summary(
    period: str,
    metrics: dict,
    verification: dict,
    config: ReportingConfig,
    client: GeminiClient | None = None,
) -> tuple[str, dict]:
    if not config.ai_enabled:
        return FALLBACK_SUMMARY, {"enabled": False, "success": False, "error": "ai_disabled"}
    if config.ai_provider.lower() != "gemini":
        return FALLBACK_SUMMARY, {"enabled": True, "success": False, "error": "unsupported_ai_provider"}
    gemini = client or GeminiClient(
        api_key=config.gemini_api_key,
        model=config.gemini_model,
        timeout_seconds=config.ai_timeout_seconds,
        max_retries=config.ai_max_retries,
    )
    result = gemini.generate(build_prompt(period, metrics, verification))
    if not result["success"]:
        return FALLBACK_SUMMARY, {"enabled": True, "success": False, "error": result["error"]}
    return result["text"], {"enabled": True, "success": True, "error": None, "model": config.gemini_model}


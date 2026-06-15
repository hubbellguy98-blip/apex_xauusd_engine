"""Live ICT/SMC strategy selection layer.

The selector lets the live setup orchestrator evaluate the richer ICT/SMC
strategy library without changing the downstream risk and MT5 execution path.
It normalizes the different strategy payload shapes into the engine's existing
SetupOpportunityNode + ConfirmationSnapshot contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

import structlog

from src.core.domain.confirmation_models import (
    AlignmentStatus,
    ConfirmationMetrics,
    ConfirmationSnapshot,
    ConfirmationTier,
)
from src.core.domain.constants import OrderDirection
from src.core.domain.setup_models import SetupOpportunityNode, SetupQualityTier, SetupType
from src.strategy.ict_smc_strategies import (
    generate_amd_signal,
    generate_breaker_signal,
    generate_fvg_continuation_signal,
    generate_htf_poi_ltf_confirmation_signal,
    generate_judas_swing_signal,
    generate_killzone_scalp_signal,
    generate_liquidity_to_liquidity_signal,
    generate_news_sweep_signal,
    generate_ob_retest_signal,
    generate_pdh_pdl_raid_signal,
    generate_silver_bullet_signal,
    generate_sweep_mss_fvg_signal,
)

logger = structlog.get_logger()

SignalGenerator = Callable[[Mapping[str, Any], Mapping[str, Any] | None], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    key: str
    label: str
    generator: SignalGenerator
    setup_type: SetupType
    priority: int = 50
    required_timeframes: tuple[str, ...] = ("1m",)
    min_candles: int = 12
    session_tags: tuple[str, ...] = ()
    requires_sweep: bool = False
    requires_htf_bias: bool = False
    news_only: bool = False


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    definition: StrategyDefinition
    signal: dict[str, Any] = field(default_factory=dict)
    status: str = "SKIPPED"
    reason: str = ""
    normalized_score: float = 0.0
    estimated_rr: float = 0.0
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    direction: OrderDirection | None = None

    @property
    def is_tradeable(self) -> bool:
        return (
            self.status == "TRADEABLE"
            and self.direction is not None
            and self.entry_price is not None
            and self.stop_loss is not None
            and self.take_profit is not None
            and self.estimated_rr >= 1.5
        )


@dataclass(frozen=True, slots=True)
class StrategySelectionResult:
    selected: StrategyEvaluation | None
    evaluations: tuple[StrategyEvaluation, ...]

    @property
    def diagnostics(self) -> dict[str, Any]:
        selected_key = self.selected.definition.key if self.selected else None
        return {
            "ict_selector_evaluated": len(self.evaluations),
            "ict_selector_tradeable": sum(1 for item in self.evaluations if item.is_tradeable),
            "ict_selector_selected": selected_key,
            "ict_selector_rejections": [
                {
                    "strategy": item.definition.key,
                    "status": item.status,
                    "reason": item.reason,
                    "score": round(item.normalized_score, 2),
                    "rr": round(item.estimated_rr, 2),
                }
                for item in self.evaluations
                if not item.is_tradeable
            ][:12],
        }


class ICTSMCStrategySelector:
    """Choose the best currently valid ICT/SMC setup for live execution."""

    def __init__(self, definitions: Sequence[StrategyDefinition] | None = None) -> None:
        self._definitions = tuple(definitions or self._default_definitions())

    def evaluate(
        self, context: Mapping[str, Any], config: Mapping[str, Any] | None = None
    ) -> StrategySelectionResult:
        evaluations = [self._evaluate_one(definition, context, config) for definition in self._definitions]
        tradeable = [item for item in evaluations if item.is_tradeable]
        selected = max(
            tradeable,
            key=lambda item: (item.normalized_score, item.estimated_rr, item.definition.priority),
            default=None,
        )
        if selected:
            logger.info(
                "ict_smc_selector.strategy_selected",
                strategy=selected.definition.key,
                score=round(selected.normalized_score, 2),
                rr=round(selected.estimated_rr, 2),
            )
        return StrategySelectionResult(selected=selected, evaluations=tuple(evaluations))

    def build_setup_node(
        self,
        evaluation: StrategyEvaluation,
        *,
        setup_id: str,
        now: datetime,
        correlation_id: str,
        timeframe: str,
    ) -> SetupOpportunityNode:
        if not evaluation.is_tradeable:
            raise ValueError("Cannot build a setup node from a non-tradeable strategy evaluation.")

        return SetupOpportunityNode(
            id=setup_id,
            setup_type=self._resolved_setup_type(evaluation),
            direction=evaluation.direction,  # type: ignore[arg-type]
            entry_price=float(evaluation.entry_price),
            stop_loss=float(evaluation.stop_loss),
            take_profit=float(evaluation.take_profit),
            estimated_rr=float(evaluation.estimated_rr),
            quality_tier=self._quality_tier(evaluation.normalized_score),
            confidence_score=float(evaluation.normalized_score),
            creation_time=now,
            expiration_time=now + timedelta(minutes=45),
            correlation_id=correlation_id,
            timeframe=timeframe,
        )

    def build_confirmation_snapshot(self, evaluation: StrategyEvaluation, *, now: datetime) -> ConfirmationSnapshot:
        score = _clamp(evaluation.normalized_score, 0.0, 100.0)
        component_scores = _component_scores(evaluation.signal)
        return ConfirmationSnapshot(
            timestamp=now,
            overall_tier=self._confirmation_tier(score),
            confidence_score=score,
            is_validated=True,
            alignment=AlignmentStatus.FULLY_ALIGNED if score >= 75.0 else AlignmentStatus.PARTIALLY_ALIGNED,
            metrics=ConfirmationMetrics(
                momentum_velocity_score=_component(component_scores, "momentum", score),
                displacement_ratio=_component(component_scores, "displacement", score) / 10.0,
                wick_rejection_pct=_component(component_scores, "wick", score),
                mtf_alignment_score=_component(component_scores, "htf", score),
                volatility_expansion_factor=max(1.0, _component(component_scores, "volatility", score) / 10.0),
                session_efficiency_index=_component(component_scores, "session", score),
            ),
            validated_components=[
                evaluation.definition.key,
                evaluation.definition.label,
                str(evaluation.signal.get("signal_id", "")),
            ],
            invalidation_reasons=[],
        )

    def _evaluate_one(
        self,
        definition: StrategyDefinition,
        context: Mapping[str, Any],
        config: Mapping[str, Any] | None,
    ) -> StrategyEvaluation:
        eligible, reason = self._is_eligible(definition, context)
        if not eligible:
            return StrategyEvaluation(definition=definition, status="SKIPPED", reason=reason)

        try:
            signal = definition.generator(context, config)
        except Exception as exc:  # pragma: no cover - exact strategy failure path is defensive.
            logger.warning("ict_smc_selector.strategy_error", strategy=definition.key, error=str(exc))
            return StrategyEvaluation(definition=definition, status="ERROR", reason=str(exc))

        score = _normalized_score(signal)
        direction = _direction(signal)
        entry = _extract_float(
            signal,
            (
                "entry_price",
                "entry.entry_price",
                "entry_model.entry_price",
                "ltf_entry_poi.entry_price",
                "entry_poi.entry_price",
                "risk.entry_price",
                "risk.entry",
            ),
        )
        stop = _extract_float(
            signal,
            ("stop_loss", "risk.stop_loss", "entry_model.stop_loss", "ltf_entry_poi.stop_loss"),
        )
        target = _extract_float(
            signal,
            (
                "take_profit",
                "target",
                "target.price",
                "target.target_price",
                "risk.target",
                "risk.final_target",
                "risk.target_2",
                "risk.target_1",
                "risk.take_profit",
            ),
        )
        rr = _extract_float(signal, ("estimated_rr", "rr", "risk.rr", "risk.rr_to_final_target")) or _calculate_rr(
            direction, entry, stop, target
        )
        trade_allowed = bool(signal.get("trade_allowed") or _nested(signal, "score.trade_allowed"))
        reasons = _rejection_reason(signal)

        if not trade_allowed:
            return StrategyEvaluation(
                definition=definition,
                signal=signal,
                status="REJECTED",
                reason=reasons or "strategy_trade_allowed_false",
                normalized_score=score,
                estimated_rr=rr,
                entry_price=entry,
                stop_loss=stop,
                take_profit=target,
                direction=direction,
            )

        missing = []
        if direction is None:
            missing.append("direction")
        if entry is None:
            missing.append("entry")
        if stop is None:
            missing.append("stop")
        if target is None:
            missing.append("target")
        if rr < 1.5:
            missing.append("rr_below_1_5")
        status = "TRADEABLE" if not missing else "REJECTED"
        return StrategyEvaluation(
            definition=definition,
            signal=signal,
            status=status,
            reason=",".join(missing) if missing else "",
            normalized_score=score,
            estimated_rr=rr,
            entry_price=entry,
            stop_loss=stop,
            take_profit=target,
            direction=direction,
        )

    def _is_eligible(self, definition: StrategyDefinition, context: Mapping[str, Any]) -> tuple[bool, str]:
        candles_by_tf = context.get("candles_by_timeframe", {}) or {}
        for timeframe in definition.required_timeframes:
            candles = candles_by_tf.get(timeframe, context.get("candles", []))
            if len(candles or []) < definition.min_candles:
                return False, f"not_enough_{timeframe}_candles"

        raw_session = _nested(context, "session_context.session") or context.get("session") or ""
        session = str(getattr(raw_session, "value", raw_session)).upper()
        if definition.session_tags and session and session not in definition.session_tags:
            return False, f"session_{session}_not_eligible"

        if definition.requires_sweep and not context.get("latest_sweep_event"):
            return False, "no_recent_liquidity_sweep"

        htf_bias = str(_nested(context, "htf_bias.bias_direction") or context.get("higher_timeframe_bias") or "").lower()
        if definition.requires_htf_bias and htf_bias not in {"bullish", "bearish"}:
            return False, "htf_bias_not_directional"

        if definition.news_only:
            news_status = context.get("news_status", {}) or {}
            if not bool(news_status.get("high_impact_recent") or news_status.get("post_news_window_active")):
                return False, "no_recent_high_impact_news_window"

        return True, ""

    @staticmethod
    def _quality_tier(score: float) -> SetupQualityTier:
        if score >= 88.0:
            return SetupQualityTier.ELITE_INSTITUTIONAL
        if score >= 75.0:
            return SetupQualityTier.HIGH_PROBABILITY
        if score >= 60.0:
            return SetupQualityTier.STANDARD
        return SetupQualityTier.INVALID_SETUP

    @staticmethod
    def _confirmation_tier(score: float) -> ConfirmationTier:
        if score >= 80.0:
            return ConfirmationTier.HIGH_CONVICTION
        if score >= 65.0:
            return ConfirmationTier.MEDIUM_CONVICTION
        if score >= 50.0:
            return ConfirmationTier.LOW_CONVICTION
        return ConfirmationTier.INVALID

    @staticmethod
    def _resolved_setup_type(evaluation: StrategyEvaluation) -> SetupType:
        if evaluation.definition.key == "htf_poi_ltf_confirmation":
            text = str(evaluation.signal.get("ltf_entry_poi", evaluation.signal.get("entry", {}))).lower()
            if "fvg" in text:
                return SetupType.FVG_CONTINUATION
        return evaluation.definition.setup_type

    @staticmethod
    def _default_definitions() -> tuple[StrategyDefinition, ...]:
        return (
            StrategyDefinition(
                "sweep_mss_fvg",
                "Liquidity Sweep + MSS + FVG Entry",
                generate_sweep_mss_fvg_signal,
                SetupType.LIQUIDITY_SWEEP_REVERSAL,
                priority=95,
                min_candles=18,
                requires_sweep=True,
            ),
            StrategyDefinition(
                "silver_bullet",
                "ICT Silver Bullet",
                generate_silver_bullet_signal,
                SetupType.FVG_CONTINUATION,
                priority=90,
                min_candles=18,
                session_tags=("LONDON_KILLZONE", "NEWYORK_KILLZONE"),
            ),
            StrategyDefinition(
                "judas_swing",
                "Judas Swing / Session Manipulation",
                generate_judas_swing_signal,
                SetupType.LIQUIDITY_SWEEP_REVERSAL,
                priority=88,
                min_candles=25,
                requires_sweep=True,
            ),
            StrategyDefinition(
                "order_block_retest",
                "Order Block Retest After Sweep",
                generate_ob_retest_signal,
                SetupType.ORDER_BLOCK_CONTINUATION,
                priority=86,
                min_candles=18,
                requires_sweep=True,
            ),
            StrategyDefinition(
                "fvg_continuation",
                "FVG Continuation",
                generate_fvg_continuation_signal,
                SetupType.FVG_CONTINUATION,
                priority=82,
                min_candles=25,
                requires_htf_bias=True,
            ),
            StrategyDefinition(
                "breaker_block",
                "Breaker Block",
                generate_breaker_signal,
                SetupType.ORDER_BLOCK_CONTINUATION,
                priority=80,
                min_candles=25,
            ),
            StrategyDefinition(
                "power_of_three_amd",
                "Power of Three AMD",
                generate_amd_signal,
                SetupType.LIQUIDITY_SWEEP_REVERSAL,
                priority=78,
                min_candles=30,
                session_tags=("ASI_ACCUMULATION", "ASIAN_ACCUMULATION", "LONDON_KILLZONE", "NEWYORK_KILLZONE"),
            ),
            StrategyDefinition(
                "pdh_pdl_raid",
                "Previous Day High/Low Raid",
                generate_pdh_pdl_raid_signal,
                SetupType.LIQUIDITY_SWEEP_REVERSAL,
                priority=84,
                min_candles=25,
                requires_sweep=True,
            ),
            StrategyDefinition(
                "killzone_scalping",
                "Kill Zone Scalping",
                generate_killzone_scalp_signal,
                SetupType.LIQUIDITY_SWEEP_REVERSAL,
                priority=83,
                min_candles=18,
                session_tags=("LONDON_KILLZONE", "NEWYORK_KILLZONE"),
            ),
            StrategyDefinition(
                "liquidity_to_liquidity",
                "Liquidity-to-Liquidity",
                generate_liquidity_to_liquidity_signal,
                SetupType.LIQUIDITY_SWEEP_REVERSAL,
                priority=81,
                min_candles=18,
                requires_sweep=True,
            ),
            StrategyDefinition(
                "news_liquidity_sweep",
                "News Liquidity Sweep",
                generate_news_sweep_signal,
                SetupType.LIQUIDITY_SWEEP_REVERSAL,
                priority=92,
                min_candles=12,
                news_only=True,
            ),
            StrategyDefinition(
                "htf_poi_ltf_confirmation",
                "HTF POI + LTF Confirmation",
                generate_htf_poi_ltf_confirmation_signal,
                SetupType.ORDER_BLOCK_CONTINUATION,
                priority=89,
                required_timeframes=("1m", "15m"),
                min_candles=18,
                requires_htf_bias=True,
            ),
        )


def _normalized_score(signal: Mapping[str, Any]) -> float:
    raw = _extract_float(
        signal,
        (
            "score.total_score",
            "score.final_score",
            "total_score",
            "final_score",
            "quality_score",
            "confidence_score",
        ),
    )
    if raw is None:
        return 0.0
    return round(_clamp(raw * 10.0 if raw <= 10.0 else raw, 0.0, 100.0), 2)


def _component_scores(signal: Mapping[str, Any]) -> Mapping[str, Any]:
    score = signal.get("score", {})
    if isinstance(score, Mapping) and isinstance(score.get("component_scores"), Mapping):
        return score["component_scores"]
    if isinstance(signal.get("component_scores"), Mapping):
        return signal["component_scores"]  # type: ignore[return-value]
    return {}


def _component(scores: Mapping[str, Any], needle: str, fallback_score: float) -> float:
    values = [float(value) for key, value in scores.items() if needle in str(key).lower() and _is_number(value)]
    if values:
        value = max(values)
        return _clamp(value * 10.0 if value <= 10.0 else value, 0.0, 100.0)
    return _clamp(fallback_score, 0.0, 100.0)


def _direction(signal: Mapping[str, Any]) -> OrderDirection | None:
    raw = str(signal.get("direction") or _nested(signal, "entry.direction") or _nested(signal, "risk.direction") or "").lower()
    if raw in {"bullish", "buy", "long", "bull", "bsl"}:
        return OrderDirection.BUY
    if raw in {"bearish", "sell", "short", "bear", "ssl"}:
        return OrderDirection.SELL
    return None


def _calculate_rr(direction: OrderDirection | None, entry: float | None, stop: float | None, target: float | None) -> float:
    if direction is None or entry is None or stop is None or target is None:
        return 0.0
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    reward = target - entry if direction is OrderDirection.BUY else entry - target
    return round(max(0.0, reward / risk), 4)


def _extract_float(signal: Mapping[str, Any], paths: Sequence[str]) -> float | None:
    for path in paths:
        value = _nested(signal, path)
        if _is_number(value):
            return float(value)
    return None


def _nested(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _rejection_reason(signal: Mapping[str, Any]) -> str:
    reasons = signal.get("rejection_reasons") or _nested(signal, "score.hard_filter_failures") or []
    if isinstance(reasons, str):
        return reasons
    if isinstance(reasons, Sequence):
        return ",".join(str(item) for item in reasons)
    return ""


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))

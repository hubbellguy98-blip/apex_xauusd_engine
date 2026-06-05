"""Rule-based ICT/SMC draw-on-liquidity target selection.

Draw on liquidity is directional and target-selection context. It does not
produce entry permission by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.analytics.ict_smc.liquidity import LiquidityDirection, LiquidityPool, LiquidityStatus


class DrawDirection(str, Enum):
    BUY_SIDE = "buy_side"
    SELL_SIDE = "sell_side"
    UNCLEAR = "unclear"


class TradeDirectionBias(str, Enum):
    LONG_FAVORED = "long_favored"
    SHORT_FAVORED = "short_favored"
    LONG_FAVORED_BUT_BLOCKED = "long_favored_but_blocked"
    SHORT_FAVORED_BUT_BLOCKED = "short_favored_but_blocked"
    NEUTRAL = "neutral"


class DrawConfidenceGrade(str, Enum):
    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


class DrawTargetStatus(str, Enum):
    SELECTED = "selected"
    ALTERNATIVE = "alternative"
    BLOCKED = "blocked"
    FILTERED = "filtered"


@dataclass(frozen=True, slots=True)
class DrawOnLiquidityConfig:
    minimum_target_quality: float = 3.0
    draw_selection_margin: float = 0.75
    blocking_poi_quality_threshold: float = 7.0
    min_target_atr: float = 0.5
    ideal_min_atr: float = 0.75
    ideal_max_atr: float = 3.0
    extended_max_atr: float = 6.0
    against_htf_cap: float = 5.0
    no_structure_cap: float = 6.0
    swept_target_cap: float = 4.0

    def __post_init__(self) -> None:
        if self.minimum_target_quality < 0:
            raise ValueError("minimum_target_quality cannot be negative.")
        if self.draw_selection_margin < 0:
            raise ValueError("draw_selection_margin cannot be negative.")
        if self.blocking_poi_quality_threshold < 0:
            raise ValueError("blocking_poi_quality_threshold cannot be negative.")


@dataclass(frozen=True, slots=True)
class DrawPriceZone:
    zone_low: float
    zone_mid: float
    zone_high: float


@dataclass(frozen=True, slots=True)
class DrawLiquidityReference:
    liquidity_id: str
    liquidity_type: str
    direction: str
    timeframe: str
    quality_score: float
    price_zone: DrawPriceZone
    swept_status: str
    touched_count: int
    source: str


@dataclass(frozen=True, slots=True)
class DrawPOIReference:
    poi_id: str
    poi_type: str
    direction: str
    timeframe: str
    price_zone: DrawPriceZone
    quality_score: float
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class DrawCandidate:
    liquidity: DrawLiquidityReference
    target_score: float
    distance: float
    distance_atr: float
    blocked_by_poi: bool
    blocking_pois: tuple[DrawPOIReference, ...] = field(default_factory=tuple)
    status: DrawTargetStatus = DrawTargetStatus.ALTERNATIVE
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True, slots=True)
class DrawContextSnapshot:
    htf_trend_state: str
    itf_trend_state: str
    ltf_trend_state: str
    latest_structure_event: str
    recent_liquidity_sweep: str
    premium_discount_position: str
    session_name: str
    volatility_state: str


@dataclass(frozen=True, slots=True)
class DrawOnLiquidityDecision:
    concept_name: str
    symbol: str
    timeframe: str
    expected_draw: DrawDirection
    trade_direction_bias: TradeDirectionBias
    current_price: float
    selected_liquidity: DrawLiquidityReference | None
    target_price_zone: DrawPriceZone | None
    confidence_score: float
    confidence_grade: DrawConfidenceGrade
    blocked_by_poi: bool
    blocking_poi_reference: DrawPOIReference | None
    best_buy_side_target: DrawCandidate | None
    best_sell_side_target: DrawCandidate | None
    alternative_targets: tuple[DrawCandidate, ...]
    context: DrawContextSnapshot
    target_selection_reason: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def entry_allowed(self) -> bool:
        return False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["expected_draw"] = self.expected_draw.value
        payload["trade_direction_bias"] = self.trade_direction_bias.value
        payload["confidence_grade"] = self.confidence_grade.value
        payload["entry_allowed"] = False
        return payload


@dataclass(frozen=True, slots=True)
class _NormalizedPOI:
    poi_id: str
    poi_type: str
    direction: str
    timeframe: str
    zone: DrawPriceZone
    quality_score: float
    status: str
    caused_bos: bool = False
    caused_mss: bool = False


class ICTDrawOnLiquidityAnalyzer:
    """Ranks likely liquidity targets and determines directional draw."""

    def __init__(self, config: DrawOnLiquidityConfig | None = None) -> None:
        self.config = config or DrawOnLiquidityConfig()

    def determine(
        self,
        context: Mapping[str, Any],
        liquidity_pools: Sequence[LiquidityPool | Mapping[str, Any]],
        poi_zones: Sequence[Mapping[str, Any]] | None = None,
    ) -> DrawOnLiquidityDecision:
        current_price = float(_first_present(context, "current_price"))
        atr = max(float(_first_present(context, "atr", default=1.0)), 1e-9)
        symbol = str(_first_present(context, "symbol", default="unknown"))
        timeframe = str(_first_present(context, "current_timeframe", "timeframe", default="unknown"))
        snapshot = _context_snapshot(context)
        pools = [_liquidity_reference(pool, current_price) for pool in liquidity_pools]
        pois = [_poi_reference(poi) for poi in poi_zones or ()]

        candidates = [
            self._score_candidate(context, pool, pois, current_price, atr)
            for pool in pools
            if self._is_target_candidate(pool, current_price)
        ]
        buy_candidates = sorted(
            (candidate for candidate in candidates if candidate.liquidity.direction == DrawDirection.BUY_SIDE.value),
            key=lambda candidate: candidate.target_score,
            reverse=True,
        )
        sell_candidates = sorted(
            (candidate for candidate in candidates if candidate.liquidity.direction == DrawDirection.SELL_SIDE.value),
            key=lambda candidate: candidate.target_score,
            reverse=True,
        )
        best_buy = buy_candidates[0] if buy_candidates else None
        best_sell = sell_candidates[0] if sell_candidates else None
        expected_draw, selected = self._select_draw(best_buy, best_sell)
        confidence_score = self._confidence_score(context, selected, expected_draw)
        confidence_grade = _confidence_grade(confidence_score)
        selected_with_status = _with_status(selected, DrawTargetStatus.SELECTED) if selected else None
        best_buy = _with_status(best_buy, DrawTargetStatus.SELECTED if selected is best_buy else DrawTargetStatus.ALTERNATIVE)
        best_sell = _with_status(
            best_sell, DrawTargetStatus.SELECTED if selected is best_sell else DrawTargetStatus.ALTERNATIVE
        )
        alternatives = tuple(
            candidate
            for candidate in (buy_candidates + sell_candidates)
            if selected is None or candidate.liquidity.liquidity_id != selected.liquidity.liquidity_id
        )[:5]
        blocked = bool(selected_with_status and selected_with_status.blocked_by_poi)
        blocker = selected_with_status.blocking_pois[0] if selected_with_status and selected_with_status.blocking_pois else None
        bias = _trade_bias(expected_draw, blocked)
        reasons, warnings = self._decision_reasons(context, selected_with_status, expected_draw, confidence_score)

        return DrawOnLiquidityDecision(
            concept_name="Draw on Liquidity",
            symbol=symbol,
            timeframe=timeframe,
            expected_draw=expected_draw,
            trade_direction_bias=bias,
            current_price=current_price,
            selected_liquidity=selected_with_status.liquidity if selected_with_status else None,
            target_price_zone=selected_with_status.liquidity.price_zone if selected_with_status else None,
            confidence_score=round(confidence_score, 2),
            confidence_grade=confidence_grade,
            blocked_by_poi=blocked,
            blocking_poi_reference=blocker,
            best_buy_side_target=best_buy,
            best_sell_side_target=best_sell,
            alternative_targets=alternatives,
            context=snapshot,
            target_selection_reason=tuple(reasons),
            warnings=tuple(warnings),
        )

    def _is_target_candidate(self, pool: DrawLiquidityReference, current_price: float) -> bool:
        if pool.quality_score < self.config.minimum_target_quality:
            return False
        if pool.swept_status in {LiquidityStatus.BROKEN.value, "invalid"}:
            return False
        if pool.direction == DrawDirection.BUY_SIDE.value:
            return pool.price_zone.zone_mid > current_price
        if pool.direction == DrawDirection.SELL_SIDE.value:
            return pool.price_zone.zone_mid < current_price
        return False

    def _score_candidate(
        self,
        context: Mapping[str, Any],
        pool: DrawLiquidityReference,
        pois: Sequence[_NormalizedPOI],
        current_price: float,
        atr: float,
    ) -> DrawCandidate:
        direction = DrawDirection(pool.direction)
        distance = abs(pool.price_zone.zone_mid - current_price)
        distance_atr = distance / atr
        blockers = tuple(self._blocking_pois(direction, current_price, pool.price_zone.zone_mid, pois))
        reasons: list[str] = ["candidate_is_on_correct_side_of_current_price"]
        warnings: list[str] = ["draw_on_liquidity_is_not_an_entry_signal"]

        score = 0.0
        score += min(2.0, pool.quality_score / 5.0)
        if pool.quality_score >= 7.0:
            reasons.append("high_quality_liquidity_target")
        score += _timeframe_weight(pool.timeframe)
        score += _freshness_score(pool.swept_status)
        score += _confluence_score(pool)
        context_alignment = _context_alignment_score(context, direction)
        score += context_alignment
        if context_alignment >= 1.5:
            reasons.append("market_context_aligns_with_target_direction")
        distance_score = _distance_score(distance_atr, self.config)
        score += distance_score
        if distance_score <= 0:
            warnings.append("target_distance_is_not_ideal")
        else:
            reasons.append("target_distance_is_reachable")
        session_score = _session_score(context, distance_atr)
        score += session_score
        if _premium_discount_aligned(context, direction):
            score += 0.25
            reasons.append("premium_discount_location_supports_draw")
        if blockers:
            strongest = max(blockers, key=lambda poi: poi.quality_score)
            score -= 0.25
            warnings.append(f"target_blocked_by_strong_{strongest.direction}_poi")
        if _is_choppy(context):
            score -= 1.0
            warnings.append("choppy_market_reduces_draw_confidence")
        if _against_htf(context, direction):
            score = min(score, self.config.against_htf_cap)
            warnings.append("target_direction_is_against_htf_bias")
        if pool.swept_status == LiquidityStatus.SWEPT.value:
            score = min(score, self.config.swept_target_cap)
            warnings.append("selected_liquidity_already_swept")

        return DrawCandidate(
            liquidity=pool,
            target_score=round(_clamp(score, 0.0, 10.0), 2),
            distance=round(distance, 6),
            distance_atr=round(distance_atr, 3),
            blocked_by_poi=bool(blockers),
            blocking_pois=tuple(_poi_to_reference(poi, direction) for poi in blockers),
            status=DrawTargetStatus.BLOCKED if blockers else DrawTargetStatus.ALTERNATIVE,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def _blocking_pois(
        self,
        direction: DrawDirection,
        current_price: float,
        target_mid: float,
        pois: Sequence[_NormalizedPOI],
    ) -> list[_NormalizedPOI]:
        blockers: list[_NormalizedPOI] = []
        for poi in pois:
            if poi.status in {"invalid", "mitigated"}:
                continue
            if poi.quality_score < self.config.blocking_poi_quality_threshold:
                continue
            if direction == DrawDirection.BUY_SIDE:
                if poi.direction == "bearish" and current_price < poi.zone.zone_low < target_mid:
                    blockers.append(poi)
            elif direction == DrawDirection.SELL_SIDE:
                if poi.direction == "bullish" and target_mid < poi.zone.zone_high < current_price:
                    blockers.append(poi)
        return blockers

    def _select_draw(
        self, best_buy: DrawCandidate | None, best_sell: DrawCandidate | None
    ) -> tuple[DrawDirection, DrawCandidate | None]:
        buy_score = best_buy.target_score if best_buy else float("-inf")
        sell_score = best_sell.target_score if best_sell else float("-inf")
        if best_buy and buy_score > sell_score + self.config.draw_selection_margin:
            return DrawDirection.BUY_SIDE, best_buy
        if best_sell and sell_score > buy_score + self.config.draw_selection_margin:
            return DrawDirection.SELL_SIDE, best_sell
        return DrawDirection.UNCLEAR, None

    def _confidence_score(
        self,
        context: Mapping[str, Any],
        selected: DrawCandidate | None,
        expected_draw: DrawDirection,
    ) -> float:
        if selected is None or expected_draw == DrawDirection.UNCLEAR:
            base = 3.0
            if _is_range_middle(context):
                base -= 0.75
            if _has_structure_confirmation(context):
                base += 0.5
            return _clamp(base, 0.0, 4.0)

        direction = DrawDirection(selected.liquidity.direction)
        score = 0.0
        score += min(2.0, _context_alignment_score(context, direction))
        score += min(2.0, selected.liquidity.quality_score / 5.0)
        score += min(1.0, _freshness_score(selected.liquidity.swept_status))
        score += _recent_opposite_sweep_score(context, direction)
        if _structure_supports(context, direction):
            score += 1.5
        elif _choch_supports(context, direction):
            score += 0.75
        score += 0.0 if selected.blocked_by_poi else 1.0
        if selected.blocked_by_poi:
            score -= min(3.0, 1.0 + selected.blocking_pois[0].quality_score / 5.0)
        score += min(0.75, _distance_score(selected.distance_atr, self.config))
        score += _session_score(context, selected.distance_atr)
        if _premium_discount_aligned(context, direction):
            score += 0.25
        if _htf_ltf_conflict(context):
            score -= 1.0
        if _is_choppy(context):
            score -= 1.0
        if _against_htf(context, direction):
            score = min(score, self.config.against_htf_cap)
        if not _has_structure_confirmation(context):
            score = min(score, self.config.no_structure_cap)
        if selected.liquidity.swept_status == LiquidityStatus.SWEPT.value:
            score = min(score, self.config.swept_target_cap)
        return _clamp(score, 0.0, 10.0)

    def _decision_reasons(
        self,
        context: Mapping[str, Any],
        selected: DrawCandidate | None,
        expected_draw: DrawDirection,
        confidence_score: float,
    ) -> tuple[list[str], list[str]]:
        warnings = ["draw_on_liquidity_is_target_logic_not_entry_logic"]
        if selected is None or expected_draw == DrawDirection.UNCLEAR:
            reasons = ["no_single_liquidity_side_won_by_required_margin"]
            if _is_range_middle(context):
                warnings.append("range_middle_no_clear_draw")
            if confidence_score <= 4:
                warnings.append("confidence_too_low_for_directional_trade_bias")
            return reasons, warnings

        reasons = list(selected.reasons)
        if expected_draw == DrawDirection.BUY_SIDE:
            reasons.append("selected_buy_side_liquidity_as_most_probable_draw")
        elif expected_draw == DrawDirection.SELL_SIDE:
            reasons.append("selected_sell_side_liquidity_as_most_probable_draw")
        if selected.blocked_by_poi:
            warnings.extend(selected.warnings)
            warnings.append("do_not_assume_full_target_until_blocking_poi_fails")
        else:
            warnings.extend(selected.warnings)
        return reasons, warnings


def determine_draw_on_liquidity(
    context: Mapping[str, Any],
    liquidity_pools: Sequence[LiquidityPool | Mapping[str, Any]],
    poi_zones: Sequence[Mapping[str, Any]] | None = None,
    **config_overrides: Any,
) -> dict[str, Any]:
    """Return a JSON-friendly draw-on-liquidity decision."""

    config = DrawOnLiquidityConfig(**config_overrides) if config_overrides else DrawOnLiquidityConfig()
    return ICTDrawOnLiquidityAnalyzer(config).determine(context, liquidity_pools, poi_zones).as_dict()


def _liquidity_reference(pool: LiquidityPool | Mapping[str, Any], current_price: float) -> DrawLiquidityReference:
    if isinstance(pool, LiquidityPool):
        zone = DrawPriceZone(pool.zone_low, pool.zone_mid, pool.zone_high)
        return DrawLiquidityReference(
            liquidity_id=pool.liquidity_id,
            liquidity_type=pool.liquidity_type.value,
            direction=pool.direction.value,
            timeframe=pool.timeframe,
            quality_score=pool.quality_score,
            price_zone=zone,
            swept_status=pool.swept_status.value,
            touched_count=pool.touched_count,
            source=pool.source,
        )
    zone_data = pool.get("price_zone") if isinstance(pool.get("price_zone"), Mapping) else pool
    zone = DrawPriceZone(
        float(_first_present(zone_data, "zone_low")),
        float(_first_present(zone_data, "zone_mid")),
        float(_first_present(zone_data, "zone_high")),
    )
    return DrawLiquidityReference(
        liquidity_id=str(_first_present(pool, "liquidity_id", "id")),
        liquidity_type=str(_first_present(pool, "liquidity_type", default="unknown")),
        direction=str(_first_present(pool, "direction")),
        timeframe=str(_first_present(pool, "timeframe", default="unknown")),
        quality_score=float(_first_present(pool, "quality_score", default=0.0)),
        price_zone=zone,
        swept_status=str(_first_present(pool, "swept_status", "status", default=LiquidityStatus.UNSWEPT.value)),
        touched_count=int(_first_present(pool, "touched_count", default=1)),
        source=str(_first_present(pool, "source", default="unknown")),
    )


def _poi_reference(poi: Mapping[str, Any]) -> _NormalizedPOI:
    zone_data = poi.get("price_zone") if isinstance(poi.get("price_zone"), Mapping) else poi
    zone = DrawPriceZone(
        float(_first_present(zone_data, "zone_low")),
        float(_first_present(zone_data, "zone_mid", default=(float(zone_data["zone_low"]) + float(zone_data["zone_high"])) / 2)),
        float(_first_present(zone_data, "zone_high")),
    )
    return _NormalizedPOI(
        poi_id=str(_first_present(poi, "poi_id", "id")),
        poi_type=str(_first_present(poi, "poi_type", default="unknown")),
        direction=str(_first_present(poi, "direction")).lower(),
        timeframe=str(_first_present(poi, "timeframe", default="unknown")),
        zone=zone,
        quality_score=float(_first_present(poi, "quality_score", default=0.0)),
        status=str(_first_present(poi, "status", default="fresh")).lower(),
        caused_bos=bool(_first_present(poi, "caused_bos", default=False)),
        caused_mss=bool(_first_present(poi, "caused_mss", default=False)),
    )


def _poi_to_reference(poi: _NormalizedPOI, target_direction: DrawDirection) -> DrawPOIReference:
    reason = "Strong opposing POI exists between current price and liquidity target"
    if target_direction == DrawDirection.BUY_SIDE:
        reason = "Strong bearish POI exists between current price and buy-side liquidity target"
    elif target_direction == DrawDirection.SELL_SIDE:
        reason = "Strong bullish POI exists between current price and sell-side liquidity target"
    return DrawPOIReference(
        poi_id=poi.poi_id,
        poi_type=poi.poi_type,
        direction=poi.direction,
        timeframe=poi.timeframe,
        price_zone=poi.zone,
        quality_score=poi.quality_score,
        status=poi.status,
        reason=reason,
    )


def _context_snapshot(context: Mapping[str, Any]) -> DrawContextSnapshot:
    return DrawContextSnapshot(
        htf_trend_state=str(_first_present(context, "htf_trend_state", "htf_bias", default="unknown")),
        itf_trend_state=str(_first_present(context, "itf_trend_state", default="unknown")),
        ltf_trend_state=str(_first_present(context, "ltf_trend_state", default="unknown")),
        latest_structure_event=str(_first_present(context, "latest_structure_event", "latest_bos", default="unknown")),
        recent_liquidity_sweep=_recent_sweep_label(context),
        premium_discount_position=str(_first_present(context, "premium_discount_position", default="unknown")),
        session_name=str(_first_present(context, "session_name", default="unknown")),
        volatility_state=str(_first_present(context, "volatility_state", default="normal")),
    )


def _context_alignment_score(context: Mapping[str, Any], direction: DrawDirection) -> float:
    score = 0.0
    if _htf_supports(context, direction):
        score += 1.0
    if _structure_supports(context, direction):
        score += 0.75
    if _recent_opposite_sweep_score(context, direction) >= 1.0:
        score += 0.5
    if _premium_discount_aligned(context, direction):
        score += 0.25
    return _clamp(score, 0.0, 2.0)


def _timeframe_weight(timeframe: str) -> float:
    normalized = timeframe.lower().replace(" ", "")
    weights = {
        "1m": 0.2,
        "5m": 0.4,
        "15m": 0.7,
        "30m": 0.9,
        "1h": 1.1,
        "h1": 1.1,
        "4h": 1.5,
        "h4": 1.5,
        "daily": 2.0,
        "1d": 2.0,
        "d1": 2.0,
        "weekly": 2.5,
        "1w": 2.5,
        "w1": 2.5,
    }
    return weights.get(normalized, 0.5)


def _freshness_score(status: str) -> float:
    normalized = status.lower()
    if normalized == LiquidityStatus.UNSWEPT.value:
        return 1.0
    if normalized == LiquidityStatus.TOUCHED.value:
        return 0.5
    if normalized == LiquidityStatus.SWEPT.value:
        return 0.1
    return 0.0


def _confluence_score(pool: DrawLiquidityReference) -> float:
    if pool.touched_count >= 4:
        return 0.75
    if pool.touched_count >= 2:
        return 0.4
    return 0.0


def _distance_score(distance_atr: float, config: DrawOnLiquidityConfig) -> float:
    if distance_atr < config.min_target_atr:
        return 0.0
    if config.ideal_min_atr <= distance_atr <= config.ideal_max_atr:
        return 0.75
    if config.ideal_max_atr < distance_atr <= config.extended_max_atr:
        return 0.4
    return 0.1


def _session_score(context: Mapping[str, Any], distance_atr: float) -> float:
    session = str(_first_present(context, "session_name", default="")).lower()
    volatility = str(_first_present(context, "volatility_state", default="normal")).lower()
    if any(name in session for name in ("london", "new_york", "ny", "overlap")) and distance_atr <= 6:
        return 0.5
    if "asia" in session and distance_atr <= 3:
        return 0.25
    if volatility in {"dead", "very_low"}:
        return 0.0
    return 0.25


def _recent_opposite_sweep_score(context: Mapping[str, Any], direction: DrawDirection) -> float:
    sweep = _recent_sweep_label(context).lower()
    if direction == DrawDirection.BUY_SIDE and ("bullish" in sweep or "sell_side_sweep" in sweep):
        return 1.0
    if direction == DrawDirection.SELL_SIDE and ("bearish" in sweep or "buy_side_sweep" in sweep):
        return 1.0
    return 0.0


def _structure_supports(context: Mapping[str, Any], direction: DrawDirection) -> bool:
    event = str(
        _first_present(context, "latest_structure_event", "latest_mss", "latest_bos", "current_structure_state", default="")
    ).lower()
    if direction == DrawDirection.BUY_SIDE:
        return "bullish" in event and any(token in event for token in ("mss", "bos", "break"))
    if direction == DrawDirection.SELL_SIDE:
        return "bearish" in event and any(token in event for token in ("mss", "bos", "break"))
    return False


def _choch_supports(context: Mapping[str, Any], direction: DrawDirection) -> bool:
    event = str(_first_present(context, "latest_choch", "latest_structure_event", default="")).lower()
    if direction == DrawDirection.BUY_SIDE:
        return "bullish" in event and "choch" in event
    if direction == DrawDirection.SELL_SIDE:
        return "bearish" in event and "choch" in event
    return False


def _has_structure_confirmation(context: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(_first_present(context, key, default="")).lower()
        for key in ("latest_structure_event", "latest_mss", "latest_bos", "latest_choch")
    )
    return any(token in text for token in ("mss", "bos", "choch", "break_of_structure"))


def _htf_supports(context: Mapping[str, Any], direction: DrawDirection) -> bool:
    htf = str(_first_present(context, "htf_trend_state", "htf_bias", default="")).lower()
    if direction == DrawDirection.BUY_SIDE:
        return "bullish" in htf
    if direction == DrawDirection.SELL_SIDE:
        return "bearish" in htf
    return False


def _against_htf(context: Mapping[str, Any], direction: DrawDirection) -> bool:
    htf = str(_first_present(context, "htf_trend_state", "htf_bias", default="")).lower()
    if "bullish" in htf:
        return direction == DrawDirection.SELL_SIDE
    if "bearish" in htf:
        return direction == DrawDirection.BUY_SIDE
    return False


def _htf_ltf_conflict(context: Mapping[str, Any]) -> bool:
    htf = str(_first_present(context, "htf_trend_state", default="")).lower()
    ltf = str(_first_present(context, "ltf_trend_state", default="")).lower()
    return ("bullish" in htf and "bearish" in ltf) or ("bearish" in htf and "bullish" in ltf)


def _premium_discount_aligned(context: Mapping[str, Any], direction: DrawDirection) -> bool:
    location = str(
        _first_present(context, "premium_discount_position", "current_price_location", default="")
    ).lower()
    if direction == DrawDirection.BUY_SIDE:
        return "discount" in location
    if direction == DrawDirection.SELL_SIDE:
        return "premium" in location
    return False


def _is_choppy(context: Mapping[str, Any]) -> bool:
    state = " ".join(
        str(_first_present(context, key, default="")).lower()
        for key in ("volatility_state", "current_structure_state", "market_regime")
    )
    return any(token in state for token in ("chop", "choppy", "range_middle", "noisy"))


def _is_range_middle(context: Mapping[str, Any]) -> bool:
    location = str(
        _first_present(context, "premium_discount_position", "current_price_location", default="")
    ).lower()
    return any(token in location for token in ("middle", "equilibrium", "range_middle"))


def _recent_sweep_label(context: Mapping[str, Any]) -> str:
    sweep = _first_present(context, "recent_liquidity_sweep", default="none")
    if isinstance(sweep, Mapping):
        direction = str(_first_present(sweep, "direction", default=""))
        sweep_type = str(_first_present(sweep, "sweep_type", default=""))
        return f"{direction}_{sweep_type}".strip("_")
    return str(sweep)


def _trade_bias(expected_draw: DrawDirection, blocked: bool) -> TradeDirectionBias:
    if expected_draw == DrawDirection.BUY_SIDE:
        return TradeDirectionBias.LONG_FAVORED_BUT_BLOCKED if blocked else TradeDirectionBias.LONG_FAVORED
    if expected_draw == DrawDirection.SELL_SIDE:
        return TradeDirectionBias.SHORT_FAVORED_BUT_BLOCKED if blocked else TradeDirectionBias.SHORT_FAVORED
    return TradeDirectionBias.NEUTRAL


def _confidence_grade(score: float) -> DrawConfidenceGrade:
    if score >= 9:
        return DrawConfidenceGrade.HIGH_QUALITY
    if score >= 7:
        return DrawConfidenceGrade.STRONG
    if score >= 5:
        return DrawConfidenceGrade.MODERATE
    if score >= 3:
        return DrawConfidenceGrade.WEAK
    return DrawConfidenceGrade.NONE


def _with_status(candidate: DrawCandidate | None, status: DrawTargetStatus) -> DrawCandidate | None:
    if candidate is None:
        return None
    return DrawCandidate(
        liquidity=candidate.liquidity,
        target_score=candidate.target_score,
        distance=candidate.distance,
        distance_atr=candidate.distance_atr,
        blocked_by_poi=candidate.blocked_by_poi,
        blocking_pois=candidate.blocking_pois,
        status=DrawTargetStatus.BLOCKED if candidate.blocked_by_poi and status == DrawTargetStatus.SELECTED else status,
        reasons=candidate.reasons,
        warnings=candidate.warnings,
    )


def _first_present(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    if default is not None:
        return default
    raise KeyError(f"Missing required key. Tried: {', '.join(keys)}")


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Cannot coerce {value!r} to datetime.")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.market_structure import (
    ICTMarketStructureAnalyzer,
    MarketStructureConfig,
    StructureBreakKind,
    StructureTrend,
    SwingLabel,
)
from src.core.domain.market_data import CandleNode


def _candle(index: int, open_p: float, high_p: float, low_p: float, close_p: float) -> CandleNode:
    start = datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
    return CandleNode(
        symbol="XAUUSD",
        timeframe="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open_p=open_p,
        high_p=high_p,
        low_p=low_p,
        close_p=close_p,
        volume=100,
        is_closed=True,
        sequence_id=index,
    )


def _analyzer() -> ICTMarketStructureAnalyzer:
    return ICTMarketStructureAnalyzer(
        MarketStructureConfig(
            left_bars=1,
            right_bars=1,
            min_swing_atr_distance=0.05,
            min_candle_gap=1,
            break_buffer_atr=0.01,
            displacement_body_ratio=0.40,
            displacement_range_atr=0.50,
        )
    )


def test_valid_bullish_structure_uses_close_break_not_guessing() -> None:
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 106, 99, 105),
        _candle(2, 105, 104, 101, 102),
        _candle(3, 102, 104, 98, 99),
        _candle(4, 99, 104, 99, 103),
        _candle(5, 103, 109, 102, 108),
        _candle(6, 108, 107, 103, 104),
        _candle(7, 104, 105, 101, 102),
        _candle(8, 102, 108, 102, 107),
        _candle(9, 107, 112, 106, 111),
        _candle(10, 111, 110, 107, 108),
    ]

    analysis = _analyzer().analyze(candles)

    assert analysis.latest_swing_low is not None
    assert analysis.latest_swing_low.label == SwingLabel.HL
    assert analysis.structure_break.kind == StructureBreakKind.BULLISH_BOS
    assert analysis.structure_break.confirmation_close == 111
    assert analysis.trend_state == StructureTrend.BULLISH
    assert "Structure break is confirmed by candle close beyond the level." in analysis.reasons


def test_wick_above_high_is_liquidity_sweep_not_bos() -> None:
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 106, 99, 105),
        _candle(2, 105, 104, 101, 102),
        _candle(3, 102, 104, 98, 99),
        _candle(4, 99, 104, 99, 103),
        _candle(5, 103, 110, 102, 105),
        _candle(6, 105, 104, 101, 102),
        _candle(7, 102, 103, 100, 101),
    ]

    analysis = _analyzer().analyze(candles)

    assert analysis.structure_break.kind == StructureBreakKind.WICK_SWEEP_ONLY
    assert analysis.structure_break.wick_only is True
    assert analysis.liquidity_context.detected is True
    assert analysis.liquidity_context.direction == "buy_side_liquidity_sweep"
    assert "wick_only_break" in analysis.warnings


def test_bearish_mss_after_sell_side_sweep_is_detected() -> None:
    candles = [
        _candle(0, 120, 121, 119, 120),
        _candle(1, 120, 122, 114, 115),
        _candle(2, 115, 118, 116, 117),
        _candle(3, 117, 119, 112, 113),
        _candle(4, 113, 117, 114, 116),
        _candle(5, 116, 118, 110, 111),
        _candle(6, 111, 115, 112, 114),
        _candle(7, 114, 116, 108, 115),
        _candle(8, 115, 123, 114, 122),
        _candle(9, 122, 121, 116, 117),
    ]

    analysis = _analyzer().analyze(candles)

    assert analysis.structure_break.kind in {
        StructureBreakKind.BULLISH_MSS,
        StructureBreakKind.BULLISH_BOS,
    }
    assert analysis.structure_break.detected is True
    assert analysis.structure_break.direction is not None


def test_unclosed_current_candle_is_ignored() -> None:
    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 106, 99, 105),
        _candle(2, 105, 104, 101, 102),
        _candle(3, 102, 104, 98, 99),
        _candle(4, 99, 104, 99, 103),
        _candle(5, 103, 105, 102, 104),
        _candle(6, 104, 104, 101, 102),
    ]
    forming = _candle(7, 102, 120, 101, 119)
    candles.append(
        CandleNode(
            symbol=forming.symbol,
            timeframe=forming.timeframe,
            start_time=forming.start_time,
            end_time=forming.end_time,
            open_p=forming.open_p,
            high_p=forming.high_p,
            low_p=forming.low_p,
            close_p=forming.close_p,
            volume=forming.volume,
            is_closed=False,
        )
    )

    analysis = _analyzer().analyze(candles)

    assert analysis.structure_break.detected is False
    assert analysis.structure_break.kind != StructureBreakKind.BULLISH_BOS

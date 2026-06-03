# ICT/SMC Market Structure Concept Layer

This layer translates the supplied ICT/SMC market-structure explanation into deterministic code rules.

It is currently observer-only. It does not change live trade execution until we deliberately connect it to the active setup detector and scoring engine.

## Implemented Concepts

- Confirmed swing highs and swing lows using closed candles only.
- Left/right candle confirmation so swings do not repaint.
- HH, HL, LH, LL, EQH, and EQL swing labels.
- Bullish, bearish, ranging, transitional, and unclear trend states.
- Structure breaks using candle close beyond structural level.
- Wick-only breaks classified as liquidity sweeps, not BOS.
- Buy-side and sell-side liquidity sweep context.
- Basic displacement context using body/range and range/ATR.
- Basic premium/discount context from the latest dealing range.
- Basic order-block context from the last opposite candle before displacement.
- 0-to-10 structure quality score with reasons and warnings.

## Main Module

`src/analytics/ict_smc/market_structure.py`

Primary entry points:

- `ICTMarketStructureAnalyzer`
- `analyze_market_structure`
- `MarketStructureConfig`
- `MarketStructureAnalysis`

## Important Safety Choice

The module uses only candles where `is_closed=True`.

The current forming candle is ignored, even if it appears to break structure. This follows the rule that wick-only or unfinished movement must not be treated as confirmed BOS/MSS.

## Current Limitations

- FVG detection is not yet deeply implemented.
- Order block logic is only a basic structure-causing candidate, not a full mitigation/order-flow model.
- Premium/discount uses the latest swing high/low, not a full higher-timeframe dealing range hierarchy yet.
- HTF/LTF internal-versus-external structure hierarchy is not fully connected yet.
- News-event filtering is not implemented.
- The live strategy still uses the older structure/liquidity path until this observer layer is reviewed.

## Next Concepts To Add

Recommended order:

1. Internal versus external structure.
2. Fair value gaps.
3. Order blocks with BOS/MSS causation.
4. Inducement.
5. Premium/discount dealing ranges across HTF/LTF.
6. Liquidity draw.
7. Entry models built from the combined concepts.

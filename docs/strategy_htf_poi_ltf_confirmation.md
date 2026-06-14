# Higher-Timeframe POI + Lower-Timeframe Confirmation

This strategy treats higher-timeframe points of interest as location and bias only. A higher-timeframe POI touch is never enough to create a trade.

## Required Sequence

1. Detect an active HTF POI from closed candles or validated POI input.
2. Map that POI into the lower-timeframe candle stream.
3. Wait for price to enter the HTF POI.
4. Require an LTF liquidity sweep inside or near the mapped HTF POI.
5. Require LTF market structure shift after the sweep.
6. Require displacement after the MSS.
7. Require an LTF FVG or order-block entry zone and retest reaction.
8. Validate target, RR, spread, news, and HTF-bias conflict filters.

## Rejection Principles

- HTF POI touch alone is context only, not a trade.
- LTF confirmation outside the HTF POI context is invalid.
- Unclosed candles are ignored.
- Huge HTF POIs are rejected unless refined on the lower timeframe.
- A signal conflicting with strong HTF bias is rejected.
- Targets that are swept, too close, or produce poor RR are rejected.

## Outputs

The model returns a pure dictionary payload with:

- `signal_status`
- `trade_allowed`
- `htf_poi`
- `mapped_poi`
- `ltf_sweep`
- `ltf_mss`
- `ltf_displacement`
- `ltf_entry_poi`
- `target`
- `risk`
- `score`
- `rejection_reasons`

This keeps the strategy research layer separate from broker execution. Live deployment should still route any accepted setup through the existing risk firewall, order guard, and MT5 execution pipeline.

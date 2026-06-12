# ICT/SMC Stop-Loss Logic

This layer calculates a logical stop-loss for an already selected ICT/SMC
entry. It is an analytics and validation layer only. It does not size a
position, submit orders, or change the VPS live runner by itself.

## Purpose

The stop must sit beyond the level where the trade idea becomes invalid.

For bullish setups, the stop belongs below the invalidation low.

For bearish setups, the stop belongs above the invalidation high.

The function is:

```python
calculate_smc_stop_loss(setup, df, atr, spread_buffer)
```

## Required Output

The required fields are:

```python
{
    "stop_loss": float | None,
    "invalidation_reason": str,
    "risk_distance": float | None,
}
```

The implementation also returns the selected invalidation level, ATR/spread
buffer details, stop mode, RR if a target is available, candidate levels,
validity, warnings, and execution permission.

## Invalidation Sources

The stop can be anchored to:

- Liquidity sweep high or low
- Order block boundary
- FVG invalidation boundary
- Recent swing high or low
- POI boundary
- Setup-defined invalidation level
- LTF sweep high or low
- Recent closed candle extremes

The function uses closed candles only for recent swing and ATR context.

## Stop Modes

`conservative`

Uses the widest structural invalidation. For bullish setups this is the lowest
valid candidate. For bearish setups this is the highest valid candidate.

`aggressive`

Prefers the closest precise invalidation such as LTF sweep, FVG, OB, entry
zone, or POI. This can improve RR but has higher wick-out risk on XAUUSD.

`balanced`

Prefers the entry zone plus nearby structure and only includes the sweep extreme
if it is close enough. This avoids blindly using a huge sweep wick when the
trade would become unusable.

`entry_zone_based`

Uses the active entry zone, FVG, OB, or POI boundary.

`sweep_based`

Uses the manipulation or liquidity sweep extreme.

`structure_based`

Uses swing, custom invalidation, or recent closed-candle structure.

## Buffers

The stop is calculated as:

```text
bullish stop = invalidation level - ATR buffer - spread buffer
bearish stop = invalidation level + ATR buffer + spread buffer
```

ATR buffer protects against normal wick volatility.

Spread buffer protects against XAUUSD bid/ask execution noise.

## Safety Rules

The layer rejects or warns when:

- Direction is missing or invalid
- Entry price is missing
- No closed candle context exists
- No valid invalidation level exists
- Stop is inside the active POI
- Stop is on the wrong side of entry
- Stop distance is too small for spread
- Stop distance is too wide versus ATR
- Stop makes reward-to-risk unacceptable

## Final Principle

If the structurally correct stop makes RR poor, the trade should be skipped or
the system should wait for a better entry. The bot must not force a tighter stop
inside the POI just to make the trade look better.

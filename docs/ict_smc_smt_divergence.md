# ICT/SMC SMT Divergence

SMT divergence compares two related assets and detects when one market takes a
meaningful high or low while the related market refuses to confirm that move.

This module is analytics and confluence only. It must not be used as a
standalone entry trigger.

## Function

```python
detect_smt_divergence(asset_a_df, asset_b_df, swing_points_a, swing_points_b)
```

Optional configuration includes:

- `primary_asset_symbol`
- `comparison_asset_symbol`
- `correlation_type`: `positive`, `inverse`, or `unknown`
- `time_tolerance_bars`
- `min_swing_strength`
- `divergence_threshold_percent`
- `min_correlation_abs`
- `rolling_correlation_period`
- `news_spike_indices`

## Data Requirements

Both assets must provide synchronized closed OHLCV candles. The detector ignores
forming candles by requiring `is_closed=True` when that field exists.

Swing inputs must contain confirmed, meaningful swing points:

- `swing_id`
- `timestamp`
- `index`
- `type`: `swing_high` or `swing_low`
- `price`
- `strength_score`
- `confirmed_status`
- `timeframe`

Weak or unconfirmed swings are ignored because SMT should compare important
market structure points, not random candle highs and lows.

## Positive Correlation Logic

For positively correlated assets, the markets are expected to confirm the same
side of structure.

Bullish SMT:

- Asset A makes a lower low.
- Asset B fails to make a lower low.
- Asset A has swept sell-side liquidity.
- Reclaim, MSS, displacement, FVG/OB, and target liquidity strengthen the setup.

Bearish SMT:

- Asset A makes a higher high.
- Asset B fails to make a higher high.
- Asset A has swept buy-side liquidity.
- Rejection, MSS, displacement, FVG/OB, and target liquidity strengthen the setup.

## Inverse Correlation Logic

For inversely correlated assets, confirmation is opposite.

Bullish SMT for Asset A:

- Asset A makes a lower low.
- Asset B fails to make a higher high.
- Example: XAUUSD sweeps a low while DXY does not confirm USD strength.

Bearish SMT for Asset A:

- Asset A makes a higher high.
- Asset B fails to make a lower low.
- Example: XAUUSD sweeps a high while DXY does not confirm USD weakness.

## Confidence Model

Confidence increases when:

- Both assets are synchronized.
- Rolling correlation matches the configured relationship.
- The swings are strong and confirmed.
- Asset A performs a meaningful liquidity sweep.
- Price reclaims or rejects the swept level.
- MSS confirms after the sweep.
- FVG follow-through appears after the divergence.

Confidence is reduced or capped when:

- Synchronization is poor.
- Swing timing is mismatched.
- Correlation is weak or unstable.
- No reclaim or rejection occurs.
- No MSS confirms.
- News spike indices mark the divergence candle.

## Output

The top-level response represents the best SMT event and includes all candidates
inside `smt_events`.

Core fields:

- `divergence_type`
- `reference_swings`
- `direction_bias`
- `confidence_score`

Additional fields:

- `primary_asset`
- `comparison_asset`
- `correlation_type`
- `data_quality`
- `liquidity_context`
- `confirmation`
- `false_positive_flags`
- `warnings`
- `entry_allowed_from_smt_alone`

## Safety Boundary

SMT divergence is not an entry model. It is a confluence filter that can improve
or weaken another setup such as a liquidity sweep, stop-hunt reversal, Judas
Swing, London or New York raid, AMD manipulation phase, or FVG/OB entry model.

The production strategy should still require:

- liquidity context
- reclaim or rejection
- MSS or CHoCH
- displacement
- FVG or OB entry zone
- target liquidity
- risk and execution validation

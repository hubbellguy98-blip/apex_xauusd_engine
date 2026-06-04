# ICT/SMC Change Of Character

`CHoCH` means `Change of Character`. In this repository it is implemented as
an observer-only early warning that short-term behavior may be changing.

## Operational Definition

A bullish CHoCH is detected when price was previously bearish and a closed
candle breaks above a confirmed internal swing high.

A bearish CHoCH is detected when price was previously bullish and a closed
candle breaks below a confirmed internal swing low.

The detector only uses:

- Closed candles.
- Confirmed swing highs/lows.
- Swing levels confirmed before the CHoCH candle.
- Optional liquidity sweep evidence supplied as structured events.

It does not use the currently forming candle and it does not repaint past
signals.

## CHoCH Versus BOS Versus MSS

`BOS` is continuation structure. A bullish BOS breaks a prior high in a bullish
structure. A bearish BOS breaks a prior low in a bearish structure.

`MSS` is stronger reversal structure. It requires a meaningful level, better
displacement, and preferably liquidity sweep context.

`CHoCH` is earlier and weaker. It can appear as an internal break before a full
reversal is confirmed. It is useful for warning, not standalone execution.

## Output Policy

The detector always marks CHoCH as:

- `warning_signal=True`
- `entry_allowed=False`

This prevents CHoCH from becoming an automatic trade trigger before it is
combined with MSS, POI, FVG, session, risk, and execution confirmation.

## Quality Inputs

CHoCH quality improves when:

- The broken swing has enough strength.
- Price closes beyond the level instead of only wicking through it.
- A relevant sell-side or buy-side liquidity sweep happened first.
- The break candle shows displacement.
- A same-direction FVG forms around the break.

Quality weakens when:

- The break is wick-only.
- The swing is minor/noisy.
- No liquidity sweep happened first.
- Displacement is weak.
- Price action is choppy.

## MSS Upgrade Candidate

A CHoCH can be tagged as `upgraded_to_mss_candidate` when it breaks a strong
level after liquidity sweep with meaningful displacement. Even then,
`entry_allowed` remains false. The execution system must wait for a separate
entry model.

## Current Integration Boundary

This module is intentionally isolated under `src.analytics.ict_smc`. It is not
connected to the live VPS execution runner. That keeps ongoing demo execution
stable while ICT/SMC concept layers are formalized and tested.

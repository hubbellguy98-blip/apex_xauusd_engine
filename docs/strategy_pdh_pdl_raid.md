# Previous Day High / Previous Day Low Raid Strategy Layer

This layer models PDH/PDL raids as previous-day external liquidity reversals.

It does not automatically sell PDH or buy PDL. It first decides whether price raided the level and rejected/reclaimed, or accepted beyond the level and continued.

## Public Functions

- `calculate_previous_day_levels()` calculates PDH and PDL from the previous completed daily/session candle only.
- `detect_pdh_pdl_raid()` detects a buy-side raid above PDH or sell-side raid below PDL.
- `detect_reclaim_or_rejection()` confirms PDL reclaim, PDH rejection, or accepted breakout.
- `detect_post_raid_mss()` confirms post-raid MSS and displacement in the reversal direction.
- `detect_post_raid_fvg_or_ob()` selects a post-MSS FVG or order-block entry POI.
- `generate_pdh_pdl_raid_signal()` orchestrates the full no-trade, waiting, or valid setup decision.
- `score_pdh_pdl_raid_setup()` scores the completed setup from 0 to 10.

## Bullish PDL Raid

1. Previous Day Low is identified from the previous completed day.
2. Price trades below PDL by a configured raid buffer.
3. Price closes back above PDL.
4. Bullish MSS confirms by close above post-raid structure.
5. Bullish displacement validates the reversal.
6. Bullish FVG or order block forms.
7. Price retraces into the POI and reacts.
8. Stop is below the raid low with ATR and spread buffer.
9. Target is real buy-side liquidity, preferably PDH or a higher external pool.

## Bearish PDH Raid

1. Previous Day High is identified from the previous completed day.
2. Price trades above PDH by a configured raid buffer.
3. Price closes back below PDH.
4. Bearish MSS confirms by close below post-raid structure.
5. Bearish displacement validates the reversal.
6. Bearish FVG or order block forms.
7. Price retraces into the POI and reacts.
8. Stop is above the raid high with ATR and spread buffer.
9. Target is real sell-side liquidity, preferably PDL or a lower external pool.

## Hard Rejections

- `invalid_previous_day_levels`
- `no_pdh_pdl_raid`
- `pdh_accepted_breakout_not_raid`
- `pdl_accepted_breakout_not_raid`
- `no_reclaim_or_rejection`
- `no_post_raid_mss`
- `no_post_raid_displacement`
- `no_valid_entry_poi`
- `waiting_for_fvg_or_ob_retest`
- `target_already_swept`
- `target_too_close`
- `rr_below_minimum`
- `news_restricted`
- `spread_too_high`
- `double_sided_raid_no_clear_direction`
- `abnormal_raid_range`

## Backtesting Rules

- Uses closed candles only.
- PDH/PDL must come from a completed previous daily/session period.
- Current day high/low is never used as PDH/PDL.
- A touch beyond PDH/PDL is context only, not a trade.
- Accepted continuation beyond PDH/PDL is rejected for this reversal model.
- News and high-spread conditions are hard filters.
- Stop and RR are calculated after ATR and spread buffers.

## Trading Impact

This strategy is designed to filter the common mistake of fading obvious liquidity too early. It waits for proof that the raid failed, then requires post-raid structure and a retracement entry. That makes it more selective, but safer than reacting to every PDH/PDL wick.

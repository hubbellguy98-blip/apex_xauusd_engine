# ICT/SMC Kill Zone Layer

The Kill Zone layer converts session timing into deterministic bot context.
It answers one narrow question:

> Did this existing setup happen inside a configured active trading window?

It does not create a trade signal by itself.

## Purpose

Kill zones help the engine separate meaningful session activity from random
low-quality timing. London, New York, and London/New York overlap windows often
contain liquidity sweeps, manipulation, displacement, FVG creation, and target
expansion. The module therefore labels, filters, or score-adjusts setups that
already came from price-action logic.

## Safety Rule

The module must never allow this logic:

```text
inside_killzone == true -> enter trade
```

The correct model is:

```text
inside_killzone
+ liquidity sweep
+ MSS/BOS/CHoCH
+ displacement
+ FVG/OB
+ target liquidity
+ risk management
= higher-quality candidate
```

If a setup lacks price-action confirmation, the module keeps it invalid and
adds `killzone_alone_not_enough`.

## Main Functions

`is_in_killzone(timestamp, killzone_config)`

- Converts the timestamp from its source timezone into the strategy timezone.
- Checks configured enabled kill zones.
- Handles windows that cross midnight.
- Returns all matched windows and the highest-priority primary window.
- Emits warnings for unknown timezones, invalid windows, or disallowed days.

`filter_setups_by_killzone(setups, killzone_config)`

- Selects the correct timestamp for each model.
- Applies one of three modes:
  - `strict`: reject outside configured kill zones.
  - `score_modifier`: boost inside windows and penalize outside windows.
  - `label_only`: only tag timing context for research.
- Preserves existing setup validity instead of creating validity from timing.

## Timestamp Selection

The module uses model-specific timestamp fields:

- Silver Bullet: `fvg_creation_timestamp`, then `sweep_timestamp`.
- Judas Swing: `manipulation_timestamp`, then `sweep_timestamp`.
- Liquidity Sweep: `sweep_timestamp`.
- MSS: `mss_confirmation_timestamp`.
- FVG: `fvg_creation_timestamp`.
- Generic setups: `confirmation_timestamp`, `entry_timestamp`, then `timestamp`.

## Example Configuration

```python
killzone_config = {
    "timestamp_timezone": "UTC",
    "strategy_timezone": "America/New_York",
    "filter_mode": "score_modifier",
    "inside_killzone_bonus": 1.0,
    "outside_killzone_penalty": 1.0,
    "allowed_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    "killzones": [
        {
            "name": "london_killzone",
            "session": "london",
            "start_time": "02:00",
            "end_time": "05:00",
            "enabled": True,
            "priority_weight": 1.0,
        },
        {
            "name": "new_york_am_killzone",
            "session": "new_york",
            "start_time": "07:00",
            "end_time": "10:30",
            "enabled": True,
            "priority_weight": 1.2,
        },
        {
            "name": "london_new_york_overlap",
            "session": "overlap",
            "start_time": "08:00",
            "end_time": "11:00",
            "enabled": True,
            "priority_weight": 1.3,
        },
    ],
}
```

## Timezone Notes

The module prefers real timezone names such as `America/New_York`. If the local
Python environment does not have timezone data installed, it falls back to known
fixed offsets for common trading zones and adds a warning. This keeps tests and
VPS diagnostics deterministic while still surfacing the timezone risk.

## Output Fields

Both functions return explicit fields such as:

- `in_killzone`
- `killzone_name`
- `session_name`
- `timezone_used`
- `time_filter_passed`
- `killzone_score_adjustment`
- `filtered_setups`
- `rejected_setups`
- `warnings`

## Current Integration Status

This is an analytics/research layer. It is not wired directly into live VPS
execution. Deploy it only after backtesting and reviewing the effect on setup
quality, rejection rate, and timing bias.

# ICT/SMC Risk Management Engine

This layer converts a detected ICT/SMC setup into a risk decision before any
execution module is allowed to place an order.

It is intentionally separate from setup detection. A beautiful setup can still
be rejected if the account, spread, news window, drawdown, or exposure state is
unsafe.

## Position In The Pipeline

```text
Market data
  -> ICT/SMC detection
  -> setup scoring
  -> stop and target validation
  -> risk management gate
  -> broker execution
```

Risk comes after the setup exists and before execution. It must not generate a
trade idea by itself.

## Required Functions

### `calculate_position_size(account_balance, risk_percent, entry, stop, pip_value)`

Calculates the trade size from:

- account balance
- risk percentage
- entry price
- stop-loss price
- account-currency value per price unit

The sizing rule is:

```text
risk_amount = account_balance * risk_percent / 100
stop_distance = abs(entry - stop)
position_size = risk_amount / (stop_distance * pip_value)
```

If the stop is wider, the position size becomes smaller. The stop must never be
moved just to force a larger position.

### `validate_trade_risk(signal, account_state, risk_config)`

Approves or rejects a setup using account and execution safety rules.

Required output fields:

- `approved`
- `position_size`
- `max_loss`
- `rr`
- `rejection_reason`

The full output also includes decision metadata, account limits, execution
safety details, correlation exposure, and warnings.

## Validation Gates

The risk gate checks:

- direction is valid
- entry, stop, and target exist
- stop is on the correct side of entry
- stop distance is usable
- reward-to-risk meets the minimum threshold
- account balance and equity are valid
- account protection lock is not active
- news no-trade window is not active
- XAUUSD spread is not too high
- projected daily loss stays below limit
- projected weekly loss stays below limit
- total open risk stays below limit
- correlated exposure stays below limit
- maximum open-position rules are respected
- position size fits broker minimum, maximum, and step rules

## XAUUSD Spread And Slippage Buffer

The validator stress-tests the setup with an execution buffer before approving
it.

For bullish trades:

```text
adjusted_entry = entry + half_spread + slippage_buffer
adjusted_stop = stop - half_spread
adjusted_target = target - half_spread
```

For bearish trades:

```text
adjusted_entry = entry - half_spread - slippage_buffer
adjusted_stop = stop + half_spread
adjusted_target = target + half_spread
```

This makes the RR calculation more conservative and avoids approving setups
that only work under perfect fills.

## Rejection Examples

```python
{
    "approved": False,
    "position_size": 0.0,
    "max_loss": 0.0,
    "rr": None,
    "rejection_reason": "news_restricted",
}
```

```python
{
    "approved": False,
    "position_size": 0.0,
    "max_loss": 0.0,
    "rr": 1.12,
    "rejection_reason": "reward_to_risk_below_minimum",
}
```

```python
{
    "approved": False,
    "position_size": 0.0,
    "max_loss": 0.0,
    "rr": 2.04,
    "rejection_reason": "correlated_exposure_too_high",
}
```

## Design Rules

- Account protection comes before setup quality.
- A high-scoring setup can be blocked by news, spread, drawdown, or exposure.
- Position size is derived from the stop distance.
- The stop is never moved to make sizing more convenient.
- Risk is rejected or reduced before execution, never after the order is sent.
- The module is deterministic so every decision can be tested and audited.

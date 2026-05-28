# Telegram Reporting

The Telegram reporter is an observer-only layer. It records runtime evidence and can send summaries, but it never changes trading decisions, lot size, dry-run mode, broker login, or order routing.

## What It Records

- Runner start and connection state
- Mode, symbol, dry-run state, max lot, and safety gates
- Live quote counts, candle ingestion, inactive quote reads, and tick gaps
- Liquidity sweeps, reversal candidates, confirmation blocks, quality blocks, and cooldown blocks
- Candidate blocks with scoring/risk reasons
- Risk-approved candidates
- Pre-submission checks
- Order result events when demo execution is explicitly enabled
- Final session summaries and runtime errors

Events are written to:

```text
.apex_runtime/notifications/telegram_events.jsonl
```

This folder is intentionally gitignored so credentials and runtime evidence do not get committed.

## Required `.env` Fields

Keep Telegram disabled until the bot token and chat id are ready:

```text
APEX_TELEGRAM_ENABLED=false
APEX_TELEGRAM_BOT_TOKEN=replace_with_telegram_bot_token
APEX_TELEGRAM_CHAT_ID=replace_with_telegram_chat_id
APEX_TELEGRAM_PARSE_MODE=HTML
APEX_TELEGRAM_MIN_SEVERITY=INFO
APEX_TELEGRAM_TIMEOUT_SECONDS=10
APEX_REPORTING_EVENT_LOG=.apex_runtime/notifications/telegram_events.jsonl
APEX_DAILY_REPORT_ENABLED=false
APEX_DAILY_REPORT_LOOKBACK_HOURS=24
APEX_DAILY_REPORT_TIMEZONE=Asia/Kolkata
```

Do not commit your real Telegram token or MT5 password.

## Smoke Test

After enabling Telegram locally on the VPS, run:

```powershell
.\.venv\Scripts\python.exe scripts\telegram_smoke_test.py
```

If successful, Telegram will receive a safe test message. No trading path is invoked.

## Daily Report

To print and send the latest daily intelligence report:

```powershell
.\.venv\Scripts\python.exe scripts\telegram_daily_report.py --lookback-hours 24
```

The report includes market-data health, strategy detections, risk/execution checkpoints, runtime warnings/errors, top rejection reasons, and what to inspect next.

## Safety Notes

- Telegram delivery failures are logged as warnings and do not stop the engine.
- Sensitive fields such as tokens, passwords, secrets, and keys are masked in JSONL events.
- The runner labels shadow messages clearly as no-order paths unless demo execution is explicitly confirmed.

# Weekly AI Trading Report

The weekly report system is read-only. It reads exported CSV/log files, calculates deterministic metrics locally, writes report files, and can optionally ask Gemini to write narrative text from verified JSON only.

It does not change MT5, broker connections, live/demo order flow, SL/TP, risk sizing, strategy selection, or account state.

## Inputs

Minimum input:

```powershell
python -m reports.weekly_report_runner --trade-log .\trade_log.csv
```

Optional inputs can be supplied with:

```powershell
--execution-log .\execution_log.csv
--broker-history .\broker_history.csv
--signal-log .\signal_log.csv
--risk-log .\risk_log.csv
--equity-csv .\equity.csv
```

Missing optional files are logged as verification information and do not stop the report.

## Commands

Current week:

```powershell
python -m reports.weekly_report_runner --week current --trade-log .\trade_log.csv
```

Previous week:

```powershell
python -m reports.weekly_report_runner --week previous --trade-log .\trade_log.csv
```

Custom date range:

```powershell
python -m reports.weekly_report_runner --start 2026-06-15 --end 2026-06-21 --trade-log .\trade_log.csv
```

Disable AI or Telegram for a run:

```powershell
python -m reports.weekly_report_runner --trade-log .\trade_log.csv --no-ai --no-telegram
```

Send Telegram:

```powershell
python -m reports.weekly_report_runner --trade-log .\trade_log.csv --send-telegram
```

## Outputs

Files are written to:

```text
reports/output/weekly/<period>/
```

Each run writes:

- `weekly_report_<period>.md`
- `weekly_report_<period>.html`
- `weekly_ai_summary_<period>.txt`
- `weekly_metrics_<period>.json`
- `weekly_verification_<period>.json`
- `weekly_manual_chart_review_<period>.csv`
- `weekly_trade_summary_<period>.csv`
- `report_manifest_<period>.json`

Telegram delivery attempts are appended to:

```text
reports/output/delivery_logs/report_delivery_log.csv
```

## Timestamp Handling

The runner normalizes trade timestamps to UTC, broker timezone, and display timezone. Defaults:

- Display timezone: `Asia/Kolkata`
- Broker timezone: `UTC`

Naive timestamps are treated as broker timezone and recorded as warnings in verification JSON.

Manual review rows include candle floors for M1, M3, M5, and M15. For example, `15:42:11` maps to M1 `15:42`, M3 `15:42`, M5 `15:40`, and M15 `15:30`.

## AI Boundary

Gemini receives only verified metrics and verification summaries. It is instructed not to calculate or invent numbers. If Gemini is disabled, not configured, or fails, the runner writes a fallback summary and still produces the deterministic report files.

## Environment

Use `.env` or process environment:

```text
REPORTING_ENABLED=true
REPORT_OUTPUT_DIR=reports/output
REPORT_DISPLAY_TIMEZONE=Asia/Kolkata
REPORT_BROKER_TIMEZONE=UTC
REPORT_SYMBOL_FILTER=GOLD.i#
REPORT_TRADE_LOG=trade_log.csv
REPORT_AI_ENABLED=false
AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-1.5-flash
REPORT_TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Scheduling

No scheduler is installed by this feature. On the VPS, schedule the command only after confirming the trade log path and output folder.


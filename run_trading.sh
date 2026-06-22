#!/bin/bash
# Run trading agent — called by cron every hour during US market hours
# Logs output to trading_log.txt

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$SCRIPT_DIR/trading_log.txt"
PROMPT="$SCRIPT_DIR/trading_prompt.md"

echo "" >> "$LOG"
echo "========================================" >> "$LOG"
echo "$(date '+%Y-%m-%d %H:%M:%S') Starting trading scan" >> "$LOG"

claude -p "$(cat "$PROMPT")" --allowedTools "mcp__robinhood-trading__get_equity_positions,mcp__robinhood-trading__get_portfolio,mcp__robinhood-trading__get_equity_historicals,mcp__robinhood-trading__get_equity_fundamentals,mcp__robinhood-trading__get_equity_quotes,mcp__robinhood-trading__place_equity_order,mcp__robinhood-trading__get_equity_orders,mcp__robinhood-trading__review_equity_order" \
  >> "$LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') Scan complete" >> "$LOG"

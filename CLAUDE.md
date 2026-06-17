# Agent Stock — Event-Driven Trading Bot

## Project overview
An automated stock trading system based on an event-driven strategy, executing trades via the Robinhood MCP Server or the robin_stocks library.

## Quick start

```bash
# Step 1: register the Robinhood MCP in Claude Code (official method)
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
# Then inside Claude Code run /mcp, select robinhood-trading and complete authentication

# Install dependencies
pip install -r requirements.txt

# Configure credentials (only needed for robin_stocks fallback mode)
cp .env.example .env

# Paper trade (no real orders)
python main.py --dry-run --scan-once

# List available Robinhood MCP tools
python main.py --list-mcp-tools

# Live trading
python main.py --live
```

## Architecture

```
main.py                  # entry point, CLI argument parsing, scheduled tasks
src/
  robinhood_client.py    # Robinhood connection layer (MCP preferred, robin_stocks fallback)
  screener.py            # stock screener (RSI, PE, PB, volume)
  event_monitor.py       # event monitor (earnings calendar, insider buying)
  risk_manager.py        # risk management (position sizing, stop-loss/take-profit, daily limits)
  strategy.py            # main strategy logic (integrates all modules above)
```

## Robinhood MCP Server
- URL: https://agent.robinhood.com/mcp/trading
- Registration command: `claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`
- Config location: .claude/settings.json
- Protocol: HTTP transport (MCP 2025-03-26)
- Fallback: robin_stocks Python library (automatic downgrade)

## Strategy logic
1. Technical + fundamental screening (RSI<40, PE<25, volume surge 1.5x)
2. Event catalyst matching (1-14 days before earnings, net insider buying within 7 days)
3. Position sizing (max 5% per stock, max 10 simultaneous positions)
4. Limit buy orders + automatic stop-loss orders
5. Stop-loss 7% / take-profit 20%

## Notes
- Default is paper trading mode; add --live to place real orders
- Backtest the strategy before going live
- robin_stocks is an unofficial API; Robinhood may change its interface at any time

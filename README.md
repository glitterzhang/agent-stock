# agent-stock

Event-driven stock trading bot for Robinhood, connecting via the official Robinhood Trading MCP.

## Connect the Robinhood MCP (official setup)

```bash
# Step 1 — register the MCP server in Claude Code
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading

# Step 2 — inside Claude Code, run /mcp, select robinhood-trading and authenticate
```

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in credentials if using robin_stocks fallback

python main.py --list-mcp-tools       # verify MCP connection
python main.py --dry-run --scan-once  # paper-trade one scan
python main.py --live                 # real trading (prompts for confirmation)
```

See `CLAUDE.md` for full architecture documentation.

"""
Event-driven trading bot — main entry point

Scheduling logic:
  - Buy scan: once per day, before US market open (Beijing time 21:25, i.e. EST 9:25)
  - Position monitor: once per hour (only checks currently held stocks)

Usage:
  python main.py --dry-run          # paper trading mode (default)
  python main.py --live             # live trading mode
  python main.py --scan-once        # run once and exit (for debugging)
"""
import os
import argparse
import schedule
import time
import logging
from src.secrets import load_encrypted_credentials
from src.strategy import EventDrivenStrategy

load_encrypted_credentials()  # load encrypted credentials first (local mode); cloud uses env vars directly

log = logging.getLogger(__name__)

# Pre-market scan time (Beijing time); 21:25 during daylight saving, 22:25 otherwise
DAILY_SCAN_TIME_BJT = os.getenv("DAILY_SCAN_TIME", "21:25")


def parse_args():
    parser = argparse.ArgumentParser(description="Event-driven stock trading bot")
    parser.add_argument("--live", action="store_true", help="live trading mode")
    parser.add_argument("--dry-run", action="store_true", default=True, help="paper trading mode (default)")
    parser.add_argument("--scan-once", action="store_true", help="run one scan and exit (debugging)")
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompt (for CI/automation)")
    parser.add_argument("--watchlist", nargs="+", help="custom stock watchlist")
    return parser.parse_args()


def main():
    args = parse_args()
    dry_run = not args.live

    print("=" * 60)
    print("  📈 Event-Driven Stock Trading Bot")
    print(f"  Mode: {'🟡 Paper trading' if dry_run else '🔴 Live trading'}")
    print("=" * 60)

    in_ci = os.getenv("CI") == "true"
    if not dry_run and not args.yes and not in_ci:
        print("\n⚠️  Warning: live trading mode is enabled — real orders will be placed!")
        confirm = input("Type YES to confirm: ")
        if confirm.strip() != "YES":
            print("Cancelled")
            return

    strategy = EventDrivenStrategy(watchlist=args.watchlist, dry_run=dry_run)

    if args.scan_once:
        strategy.run_scan()
        return

    # ── Scheduled tasks ───────────────────────────────────
    # 1. Scan for buy opportunities once per day before market open (full 49-stock watchlist)
    schedule.every().day.at(DAILY_SCAN_TIME_BJT).do(strategy.run_scan)

    # 2. Monitor positions for stop-loss/take-profit/trend every hour (only held stocks)
    schedule.every(60).minutes.do(strategy.monitor_only)

    # 3. Reset daily PnL after market close each day
    schedule.every().day.at("04:10").do(strategy.risk_manager.reset_daily_pnl)  # Beijing 04:10 = EST 16:10

    print(f"\n📅 Buy scan: daily at {DAILY_SCAN_TIME_BJT} Beijing time (before US market open)")
    print(f"👀 Position monitor: checked every hour")
    print(f"\nRunning an immediate scan now...")

    strategy.run_scan()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()

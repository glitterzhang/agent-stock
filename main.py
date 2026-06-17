"""
事件驱动交易机器人 — 主入口

调度逻辑：
  - 买入扫描：每天一次，美股开盘前（北京时间 21:25，即 EST 9:25）
  - 持仓监控：每小时一次（只检查已持仓的股票）

用法:
  python main.py --dry-run          # 模拟模式（默认）
  python main.py --live             # 实盘模式
  python main.py --scan-once        # 只跑一次后退出（调试用）
"""
import os
import argparse
import schedule
import time
import logging
from src.secrets import load_encrypted_credentials
from src.strategy import EventDrivenStrategy

load_encrypted_credentials()  # 优先加载加密凭证（本地模式），云端直接用环境变量

log = logging.getLogger(__name__)

# 美股开盘前扫描时间（北京时间），夏令时21:25，冬令时22:25
DAILY_SCAN_TIME_BJT = os.getenv("DAILY_SCAN_TIME", "21:25")


def parse_args():
    parser = argparse.ArgumentParser(description="事件驱动股票交易机器人")
    parser.add_argument("--live", action="store_true", help="实盘模式")
    parser.add_argument("--dry-run", action="store_true", default=True, help="模拟模式（默认）")
    parser.add_argument("--scan-once", action="store_true", help="只扫描一次后退出（调试）")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示（CI/自动化使用）")
    parser.add_argument("--watchlist", nargs="+", help="自定义监控股票")
    return parser.parse_args()


def main():
    args = parse_args()
    dry_run = not args.live

    print("=" * 60)
    print("  📈 事件驱动股票交易机器人")
    print(f"  模式: {'🟡 模拟交易' if dry_run else '🔴 实盘交易'}")
    print("=" * 60)

    in_ci = os.getenv("CI") == "true"
    if not dry_run and not args.yes and not in_ci:
        print("\n⚠️  警告: 实盘模式已启用，将真实下单！")
        confirm = input("输入 YES 确认继续: ")
        if confirm.strip() != "YES":
            print("已取消")
            return

    strategy = EventDrivenStrategy(watchlist=args.watchlist, dry_run=dry_run)

    if args.scan_once:
        strategy.run_scan()
        return

    # ── 调度任务 ──────────────────────────────────────────
    # 1. 每天开盘前扫描买入机会（全量49只股票）
    schedule.every().day.at(DAILY_SCAN_TIME_BJT).do(strategy.run_scan)

    # 2. 每小时监控持仓止损/止盈/趋势（只检查已持仓）
    schedule.every(60).minutes.do(strategy.monitor_only)

    # 3. 每天收盘后重置当日盈亏
    schedule.every().day.at("04:10").do(strategy.risk_manager.reset_daily_pnl)  # 北京时间04:10 = EST 16:10

    print(f"\n📅 买入扫描: 每天 {DAILY_SCAN_TIME_BJT}（北京时间，美股开盘前）")
    print(f"👀 持仓监控: 每小时检查一次")
    print(f"\n立即执行一次扫描...")

    strategy.run_scan()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()

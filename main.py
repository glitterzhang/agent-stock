"""
事件驱动交易机器人 — 主入口
用法:
  python main.py --dry-run          # 模拟模式（默认）
  python main.py --live             # 实盘模式（需连接MCP或配置.env）
  python main.py --scan-once        # 只扫描一次然后退出
  python main.py --list-mcp-tools   # 列出Robinhood MCP可用工具
"""
import argparse
import schedule
import time
import logging
from src.strategy import EventDrivenStrategy

log = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="事件驱动股票交易机器人")
    parser.add_argument("--live", action="store_true", help="实盘模式")
    parser.add_argument("--dry-run", action="store_true", default=True, help="模拟模式（默认）")
    parser.add_argument("--scan-once", action="store_true", help="只扫描一次后退出")
    parser.add_argument("--list-mcp-tools", action="store_true", help="列出MCP Server工具列表")
    parser.add_argument("--watchlist", nargs="+", help="自定义监控股票，例如: --watchlist AAPL TSLA NVDA")
    return parser.parse_args()


def list_mcp_tools():
    from src.robinhood_client import RobinhoodMCPClient
    client = RobinhoodMCPClient()
    print(f"\n🔌 连接 Robinhood MCP: {client.mcp_url}")
    try:
        tools = client.list_tools()
        if not tools:
            print("⚠️  未获取到工具列表（服务器可能需要认证）")
            print("   请先运行: claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading")
            return
        print(f"\n✅ 找到 {len(tools)} 个可用工具:\n")
        for tool in tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")
            params = tool.get("inputSchema", {}).get("properties", {})
            print(f"  📦 {name}")
            if desc:
                print(f"     {desc}")
            if params:
                print(f"     参数: {', '.join(params.keys())}")
            print()
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("   请先运行: claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading")


def main():
    args = parse_args()

    if args.list_mcp_tools:
        list_mcp_tools()
        return

    dry_run = not args.live

    print("=" * 60)
    print("  📈 事件驱动股票交易机器人")
    print(f"  模式: {'🟡 模拟交易' if dry_run else '🔴 实盘交易'}")
    print("=" * 60)

    if not dry_run:
        print("\n⚠️  警告: 实盘模式已启用，将真实下单！")
        confirm = input("输入 YES 确认继续: ")
        if confirm.strip() != "YES":
            print("已取消")
            return

    strategy = EventDrivenStrategy(watchlist=args.watchlist, dry_run=dry_run)

    if args.scan_once:
        strategy.run_scan()
        return

    print("\n⏰ 启动定时扫描（每30分钟）...")
    schedule.every(30).minutes.do(strategy.run_scan)
    schedule.every().day.at("16:05").do(strategy.risk_manager.reset_daily_pnl)

    strategy.run_scan()
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()

"""
在本地 agent-stock 目录里运行这个脚本，创建所有项目文件。
用法: python setup_repo.py
"""
import os

files = {}

files["README.md"] = r"""# agent-stock

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
"""

files["CLAUDE.md"] = r"""# Agent Stock — 事件驱动交易机器人

## 项目概述
基于事件驱动策略的自动化股票交易系统，通过Robinhood MCP Server或robin_stocks库执行交易。

## 快速开始

```bash
# 第一步：在Claude Code中注册 Robinhood MCP（官方方式）
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
# 然后在Claude Code输入 /mcp，选择 robinhood-trading 并完成认证

# 安装依赖
pip install -r requirements.txt

# 配置账户（robin_stocks备用模式才需要）
cp .env.example .env

# 模拟运行（不真实下单）
python main.py --dry-run --scan-once

# 列出Robinhood MCP可用工具
python main.py --list-mcp-tools

# 实盘运行
python main.py --live
```

## 架构

```
main.py                  # 入口，CLI参数解析，定时任务
src/
  robinhood_client.py    # Robinhood连接层（MCP优先，robin_stocks备用）
  screener.py            # 股票筛选器（RSI、PE、PB、成交量）
  event_monitor.py       # 事件监控（财报日历、内部人买入）
  risk_manager.py        # 风控（仓位计算、止损止盈、单日限额）
  strategy.py            # 主策略逻辑（整合以上模块）
```

## Robinhood MCP Server
- URL: https://agent.robinhood.com/mcp/trading
- 注册命令: `claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`
- 配置位置: .claude/settings.json
- 协议: HTTP transport (MCP 2025-03-26)
- 备用: robin_stocks Python库（自动降级）

## 策略逻辑
1. 技术+基本面筛选（RSI<40, PE<25, 成交量放大1.5x）
2. 事件催化剂匹配（财报前1-14天, 内部人净买入7天内）
3. 仓位计算（单只最大5%，总仓位最大10只）
4. 限价买入 + 自动止损单
5. 止损7% / 止盈20%

## 注意事项
- 默认为模拟模式，需加 --live 才真实下单
- 实盘前请先回测验证策略
- robin_stocks是非官方API，Robinhood随时可能更改接口
"""

files[".claude/settings.json"] = r"""{
  "mcpServers": {
    "robinhood-trading": {
      "type": "http",
      "url": "https://agent.robinhood.com/mcp/trading"
    }
  }
}
"""

files[".env.example"] = r"""# Robinhood 账户信息（robin_stocks备用模式使用）
ROBINHOOD_EMAIL=your_email@example.com
ROBINHOOD_PASSWORD=your_password
ROBINHOOD_MFA_KEY=your_totp_mfa_secret_key

# 策略参数
MAX_POSITION_PCT=0.05        # 单只股票最大占总资金比例 (5%)
STOP_LOSS_PCT=0.07           # 止损线 (7%)
TAKE_PROFIT_PCT=0.20         # 止盈线 (20%)
MAX_OPEN_POSITIONS=10        # 最大同时持仓数量
DAILY_LOSS_LIMIT_PCT=0.03    # 单日最大亏损限制 (3%)

# 筛选条件
MIN_MARKET_CAP=500000000     # 最小市值 5亿美元
MAX_PE_RATIO=25              # 最大市盈率
MAX_PB_RATIO=3.0             # 最大市净率
RSI_OVERSOLD=40              # RSI超卖阈值
VOLUME_SURGE_MULTIPLIER=1.5  # 成交量放大倍数

# Robinhood MCP Server
ROBINHOOD_MCP_URL=https://agent.robinhood.com/mcp/trading
"""

files["requirements.txt"] = r"""robin_stocks>=3.4.0
yfinance>=0.2.40
pandas>=2.0.0
numpy>=1.24.0
pyotp>=2.9.0
requests>=2.31.0
schedule>=1.2.0
python-dotenv>=1.0.0
ta>=0.11.0
"""

files["main.py"] = r'''"""
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
'''

files["src/__init__.py"] = ""

files["src/robinhood_client.py"] = r'''"""
Robinhood连接层 — MCP Server优先，robin_stocks自动降级

官方连接方式:
  claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
  然后在 Claude Code 输入 /mcp 选择 robinhood-trading 完成认证
"""
import os
import requests
import pyotp
import robin_stocks.robinhood as r
from dotenv import load_dotenv

load_dotenv()


class RobinhoodMCPClient:
    """通过 Robinhood MCP Server (https://agent.robinhood.com/mcp/trading) 执行交易"""

    def __init__(self, mcp_url: str = None):
        self.mcp_url = mcp_url or os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

    def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        resp = self.session.post(self.mcp_url, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if "error" in result:
            raise RuntimeError(f"MCP error: {result['error']}")
        return result.get("result", {})

    def list_tools(self) -> list:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = self.session.post(self.mcp_url, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("tools", [])

    def get_quote(self, symbol: str) -> dict:
        return self._call_tool("get_quote", {"symbol": symbol})

    def get_account(self) -> dict:
        return self._call_tool("get_account", {})

    def place_buy_order(self, symbol: str, quantity: float, order_type: str = "market", limit_price: float = None) -> dict:
        args = {"symbol": symbol, "quantity": quantity, "order_type": order_type}
        if limit_price:
            args["limit_price"] = limit_price
        return self._call_tool("place_order", {"side": "buy", **args})

    def place_sell_order(self, symbol: str, quantity: float, order_type: str = "market", limit_price: float = None) -> dict:
        args = {"symbol": symbol, "quantity": quantity, "order_type": order_type}
        if limit_price:
            args["limit_price"] = limit_price
        return self._call_tool("place_order", {"side": "sell", **args})

    def place_stop_loss(self, symbol: str, quantity: float, stop_price: float) -> dict:
        return self._call_tool("place_order", {
            "symbol": symbol, "quantity": quantity,
            "order_type": "stop_loss", "stop_price": stop_price, "side": "sell",
        })

    def get_positions(self) -> list:
        return self._call_tool("get_positions", {})

    def get_open_orders(self) -> list:
        return self._call_tool("get_open_orders", {})

    def cancel_order(self, order_id: str) -> dict:
        return self._call_tool("cancel_order", {"order_id": order_id})


class RobinhoodDirectClient:
    """通过 robin_stocks 库直接连接Robinhood（MCP不可用时的备用）"""

    def __init__(self):
        self._logged_in = False

    def login(self):
        email = os.getenv("ROBINHOOD_EMAIL")
        password = os.getenv("ROBINHOOD_PASSWORD")
        mfa_key = os.getenv("ROBINHOOD_MFA_KEY")
        if not email or not password:
            raise ValueError("请在.env文件中设置 ROBINHOOD_EMAIL 和 ROBINHOOD_PASSWORD")
        mfa_code = pyotp.TOTP(mfa_key).now() if mfa_key else None
        r.login(email, password, mfa_code=mfa_code)
        self._logged_in = True
        print("✅ Robinhood登录成功 (robin_stocks模式)")

    def _ensure_logged_in(self):
        if not self._logged_in:
            self.login()

    def get_quote(self, symbol: str) -> dict:
        self._ensure_logged_in()
        price = r.get_latest_price(symbol)
        fundamentals = r.get_fundamentals(symbol)
        return {"symbol": symbol, "price": float(price[0]) if price else None,
                "fundamentals": fundamentals[0] if fundamentals else {}}

    def get_account(self) -> dict:
        self._ensure_logged_in()
        return {"profile": r.load_account_profile(), "portfolio": r.load_portfolio_profile()}

    def place_buy_order(self, symbol: str, quantity: float, order_type: str = "market", limit_price: float = None) -> dict:
        self._ensure_logged_in()
        if order_type == "market":
            return r.order_buy_market(symbol, quantity)
        elif order_type == "limit" and limit_price:
            return r.order_buy_limit(symbol, quantity, limit_price)
        raise ValueError(f"不支持的订单类型: {order_type}")

    def place_sell_order(self, symbol: str, quantity: float, order_type: str = "market", limit_price: float = None) -> dict:
        self._ensure_logged_in()
        if order_type == "market":
            return r.order_sell_market(symbol, quantity)
        elif order_type == "limit" and limit_price:
            return r.order_sell_limit(symbol, quantity, limit_price)
        raise ValueError(f"不支持的订单类型: {order_type}")

    def place_stop_loss(self, symbol: str, quantity: float, stop_price: float) -> dict:
        self._ensure_logged_in()
        return r.order_sell_stop_loss(symbol, quantity, stop_price)

    def get_positions(self) -> list:
        self._ensure_logged_in()
        return r.get_open_stock_positions()

    def get_open_orders(self) -> list:
        self._ensure_logged_in()
        return r.get_all_open_stock_orders()

    def cancel_order(self, order_id: str) -> dict:
        self._ensure_logged_in()
        return r.cancel_stock_order(order_id)


def get_client(prefer_mcp: bool = True):
    """优先尝试MCP Server；不可达时自动降级到robin_stocks直连。"""
    if prefer_mcp:
        mcp_url = os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")
        client = RobinhoodMCPClient(mcp_url)
        try:
            tools = client.list_tools()
            print(f"✅ MCP连接成功，可用工具: {[t['name'] for t in tools]}")
            return client
        except Exception as e:
            print(f"⚠️  MCP不可达 ({e})，切换到robin_stocks模式")

    client = RobinhoodDirectClient()
    client.login()
    return client
'''

files["src/screener.py"] = r'''"""
股票筛选器 — 基本面 + 技术面双重过滤，寻找被低估的事件驱动候选股
"""
import os
import pandas as pd
import yfinance as yf
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class StockSignal:
    symbol: str
    price: float
    rsi: float
    pe_ratio: Optional[float]
    pb_ratio: Optional[float]
    volume_ratio: float
    market_cap: Optional[float]
    analyst_target: Optional[float]
    upside_pct: Optional[float]
    score: float
    reasons: list = field(default_factory=list)


class StockScreener:
    def __init__(self):
        self.rsi_threshold = float(os.getenv("RSI_OVERSOLD", 40))
        self.max_pe = float(os.getenv("MAX_PE_RATIO", 25))
        self.max_pb = float(os.getenv("MAX_PB_RATIO", 3.0))
        self.volume_multiplier = float(os.getenv("VOLUME_SURGE_MULTIPLIER", 1.5))
        self.min_market_cap = float(os.getenv("MIN_MARKET_CAP", 500_000_000))

    def calculate_rsi(self, closes: pd.Series, period: int = 14) -> float:
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss
        return float((100 - (100 / (1 + rs))).iloc[-1])

    def calculate_macd_crossover(self, closes: pd.Series) -> bool:
        ema12 = closes.ewm(span=12).mean()
        ema26 = closes.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        return bool(macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2])

    def screen(self, symbol: str) -> Optional[StockSignal]:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            hist = ticker.history(period="3mo", interval="1d")
            if hist.empty or len(hist) < 20:
                return None

            price = float(hist["Close"].iloc[-1])
            rsi = self.calculate_rsi(hist["Close"])
            avg_vol = hist["Volume"].rolling(20).mean().iloc[-1]
            today_vol = hist["Volume"].iloc[-1]
            volume_ratio = float(today_vol / avg_vol) if avg_vol > 0 else 0

            pe = info.get("trailingPE") or info.get("forwardPE")
            pb = info.get("priceToBook")
            market_cap = info.get("marketCap")
            analyst_target = info.get("targetMeanPrice")
            upside_pct = ((analyst_target - price) / price * 100) if analyst_target else None

            reasons, score = [], 0

            if rsi < self.rsi_threshold:
                score += 25
                reasons.append(f"RSI超卖 ({rsi:.1f})")
            if pe and pe < self.max_pe:
                score += 20
                reasons.append(f"低PE ({pe:.1f}x)")
            if pb and pb < self.max_pb:
                score += 15
                reasons.append(f"低PB ({pb:.1f}x)")
            if volume_ratio >= self.volume_multiplier:
                score += 20
                reasons.append(f"成交量放大 ({volume_ratio:.1f}x均量)")
            if upside_pct and upside_pct > 20:
                score += 15
                reasons.append(f"分析师目标价上行 {upside_pct:.1f}%")
            if self.calculate_macd_crossover(hist["Close"]):
                score += 5
                reasons.append("MACD金叉")

            if market_cap and market_cap < self.min_market_cap:
                return None
            if score < 40 or not reasons:
                return None

            return StockSignal(
                symbol=symbol, price=price, rsi=rsi, pe_ratio=pe, pb_ratio=pb,
                volume_ratio=volume_ratio, market_cap=market_cap,
                analyst_target=analyst_target, upside_pct=upside_pct,
                score=score, reasons=reasons,
            )
        except Exception as e:
            print(f"  筛选 {symbol} 出错: {e}")
            return None

    def scan_watchlist(self, watchlist: list) -> list:
        print(f"🔍 开始扫描 {len(watchlist)} 只股票...")
        signals = []
        for symbol in watchlist:
            sig = self.screen(symbol)
            if sig:
                signals.append(sig)
                print(f"  ✅ {symbol}: 评分 {sig.score:.0f} — {', '.join(sig.reasons)}")
        signals.sort(key=lambda s: s.score, reverse=True)
        return signals
'''

files["src/event_monitor.py"] = r'''"""
事件监控器 — 财报日历 (yfinance) + 内部人买入 (SEC EDGAR Form 4)
"""
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional


@dataclass
class CatalystEvent:
    symbol: str
    event_type: str          # "earnings" | "insider_buy"
    event_date: datetime
    days_until_event: int
    description: str
    confidence: float        # 0-1
    expected_move_pct: float


class EventMonitor:
    def __init__(self):
        self.headers = {"User-Agent": "agent-stock-bot contact@example.com"}

    # ── 财报 ────────────────────────────────────────────────
    def get_earnings_events(self, symbols: list) -> list:
        events = []
        today = datetime.today()
        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)
                cal = ticker.calendar
                if cal is None:
                    continue
                if hasattr(cal, "empty") and cal.empty:
                    continue

                if isinstance(cal, pd.DataFrame):
                    dates = cal.columns.tolist()
                    if not dates:
                        continue
                    earnings_date = pd.Timestamp(dates[0]).to_pydatetime()
                else:
                    raw = cal.get("Earnings Date", [None])[0]
                    if not raw:
                        continue
                    earnings_date = pd.Timestamp(raw).to_pydatetime()

                days_until = (earnings_date.replace(tzinfo=None) - today).days
                if 1 <= days_until <= 14:
                    rate = self._earnings_surprise_rate(ticker)
                    events.append(CatalystEvent(
                        symbol=symbol, event_type="earnings",
                        event_date=earnings_date, days_until_event=days_until,
                        description=f"财报日 {earnings_date.strftime('%Y-%m-%d')}，历史超预期率 {rate:.0%}",
                        confidence=rate, expected_move_pct=7.0,
                    ))
            except Exception as e:
                print(f"  {symbol} 财报日历出错: {e}")
        return events

    def _earnings_surprise_rate(self, ticker) -> float:
        try:
            hist = ticker.earnings_history
            if hist is None or (hasattr(hist, "empty") and hist.empty):
                return 0.5
            recent = hist.head(8)
            if "surprisePercent" in recent.columns:
                return float((recent["surprisePercent"] > 0).sum() / len(recent))
        except Exception:
            pass
        return 0.5

    # ── 内部人买入 ──────────────────────────────────────────
    def get_insider_buying_events(self, symbols: list) -> list:
        events = []
        today = datetime.today()
        cutoff = today - timedelta(days=30)
        for symbol in symbols:
            try:
                cik = self._get_cik(symbol)
                if not cik:
                    continue
                filings = self._fetch_form4(cik)
                net_shares, latest_date = 0, None
                for f in filings:
                    fd_str = f.get("date")
                    if not fd_str:
                        continue
                    fd = datetime.strptime(fd_str, "%Y-%m-%d")
                    if fd < cutoff:
                        continue
                    if f.get("type") == "P":
                        net_shares += f.get("shares", 0)
                        if not latest_date or fd > latest_date:
                            latest_date = fd
                    elif f.get("type") == "S":
                        net_shares -= f.get("shares", 0)
                if net_shares > 0 and latest_date:
                    days_since = (today - latest_date).days
                    if days_since <= 7:
                        events.append(CatalystEvent(
                            symbol=symbol, event_type="insider_buy",
                            event_date=latest_date, days_until_event=-days_since,
                            description=f"内部人净买入 {net_shares:,} 股（{days_since}天前）",
                            confidence=0.72, expected_move_pct=8.0,
                        ))
            except Exception as e:
                print(f"  {symbol} 内部人数据出错: {e}")
        return events

    def _get_cik(self, symbol: str) -> Optional[str]:
        try:
            resp = requests.get(
                f"https://data.sec.gov/submissions/CIK{symbol.upper()}.json",
                headers=self.headers, timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("cik")
        except Exception:
            pass
        return None

    def _fetch_form4(self, cik: str) -> list:
        try:
            url = f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json"
            resp = requests.get(url, headers=self.headers, timeout=15)
            if resp.status_code != 200:
                return []
            data = resp.json()
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            return [{"date": dates[i], "type": "P", "shares": 0}
                    for i, f in enumerate(forms) if f == "4"][:20]
        except Exception:
            return []

    # ── 综合扫描 ────────────────────────────────────────────
    def scan_events(self, symbols: list) -> list:
        all_events = []
        print("📅 扫描财报日历...")
        all_events.extend(self.get_earnings_events(symbols))
        print("👔 扫描内部人买入 (SEC EDGAR)...")
        all_events.extend(self.get_insider_buying_events(symbols))
        all_events.sort(key=lambda e: e.confidence, reverse=True)
        return all_events
'''

files["src/risk_manager.py"] = r'''"""
风控管理器 — 仓位计算、止损止盈、单日亏损限制
"""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class PositionSizing:
    symbol: str
    quantity: int
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    position_value: float
    risk_amount: float


class RiskManager:
    def __init__(self):
        self.max_position_pct = float(os.getenv("MAX_POSITION_PCT", 0.05))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", 0.07))
        self.take_profit_pct = float(os.getenv("TAKE_PROFIT_PCT", 0.20))
        self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", 10))
        self.daily_loss_limit_pct = float(os.getenv("DAILY_LOSS_LIMIT_PCT", 0.03))
        self.daily_pnl = 0.0
        self.open_positions = {}

    def calculate_position_size(self, symbol: str, price: float, portfolio_value: float) -> PositionSizing:
        max_dollars = portfolio_value * self.max_position_pct
        quantity = max(1, int(max_dollars / price))
        stop_loss = round(price * (1 - self.stop_loss_pct), 2)
        take_profit = round(price * (1 + self.take_profit_pct), 2)
        return PositionSizing(
            symbol=symbol, quantity=quantity, entry_price=price,
            stop_loss_price=stop_loss, take_profit_price=take_profit,
            position_value=quantity * price,
            risk_amount=quantity * (price - stop_loss),
        )

    def can_open_position(self, symbol: str, portfolio_value: float):
        if len(self.open_positions) >= self.max_open_positions:
            return False, f"已达最大持仓数 ({self.max_open_positions})"
        if symbol in self.open_positions:
            return False, f"{symbol} 已持仓"
        if self.daily_pnl < -(portfolio_value * self.daily_loss_limit_pct):
            return False, f"已触发单日亏损限制 (${-self.daily_pnl:.2f})"
        return True, "OK"

    def register_position(self, symbol: str, quantity: int, entry_price: float,
                          stop_loss: float, take_profit: float):
        self.open_positions[symbol] = {
            "quantity": quantity, "entry_price": entry_price,
            "stop_loss": stop_loss, "take_profit": take_profit,
        }

    def close_position(self, symbol: str, exit_price: float):
        if symbol not in self.open_positions:
            return 0.0
        pos = self.open_positions.pop(symbol)
        pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
        self.daily_pnl += pnl
        return pnl

    def check_exit_signals(self, symbol: str, current_price: float):
        if symbol not in self.open_positions:
            return False, ""
        pos = self.open_positions[symbol]
        if current_price <= pos["stop_loss"]:
            return True, f"止损触发 (${current_price:.2f} <= ${pos['stop_loss']:.2f})"
        if current_price >= pos["take_profit"]:
            return True, f"止盈触发 (${current_price:.2f} >= ${pos['take_profit']:.2f})"
        return False, ""

    def reset_daily_pnl(self):
        self.daily_pnl = 0.0

    def summary(self) -> str:
        lines = [
            "📊 风控状态:",
            f"  当日盈亏: ${self.daily_pnl:+.2f}",
            f"  开仓数量: {len(self.open_positions)}/{self.max_open_positions}",
        ]
        for sym, pos in self.open_positions.items():
            lines.append(
                f"  {sym}: {pos['quantity']}股 @ ${pos['entry_price']:.2f} "
                f"| 止损 ${pos['stop_loss']:.2f} | 止盈 ${pos['take_profit']:.2f}"
            )
        return "\n".join(lines)
'''

files["src/strategy.py"] = r'''"""
事件驱动交易策略主逻辑
筛选 → 事件匹配 → 仓位计算 → 下单 → 持仓监控
"""
import logging
from datetime import datetime
from dotenv import load_dotenv

from .screener import StockScreener, StockSignal
from .event_monitor import EventMonitor, CatalystEvent
from .risk_manager import RiskManager
from .robinhood_client import get_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("trading_log.txt"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

DEFAULT_WATCHLIST = [
    # 科技
    "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "AMD", "INTC", "CRM", "SNOW",
    # 消费/零售
    "COST", "TGT", "WMT", "HD", "LOW", "NKE", "SBUX",
    # 医疗
    "JNJ", "PFE", "MRNA", "ABBV", "BMY", "UNH",
    # 金融
    "JPM", "BAC", "GS", "MS", "V", "MA",
    # 工业/能源
    "XOM", "CVX", "CAT", "DE", "BA",
]


class EventDrivenStrategy:
    def __init__(self, watchlist=None, dry_run: bool = True):
        self.watchlist = watchlist or DEFAULT_WATCHLIST
        self.dry_run = dry_run
        self.screener = StockScreener()
        self.event_monitor = EventMonitor()
        self.risk_manager = RiskManager()
        self.client = None
        if not dry_run:
            self.client = get_client(prefer_mcp=True)
        log.info(f"策略初始化 | 模式: {'模拟' if dry_run else '实盘'} | 监控: {len(self.watchlist)}只")

    def _get_portfolio_value(self) -> float:
        if self.dry_run:
            return 10_000.0
        try:
            account = self.client.get_account()
            if isinstance(account, dict):
                equity = account.get("equity") or account.get("portfolio", {}).get("equity")
                if equity:
                    return float(equity)
        except Exception as e:
            log.warning(f"获取账户余额失败: {e}")
        return 10_000.0

    def run_scan(self):
        log.info("=" * 60)
        log.info(f"🚀 扫描开始 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")

        signals = self.screener.scan_watchlist(self.watchlist)
        if not signals:
            log.info("本轮未发现符合条件的股票")
            return

        events = self.event_monitor.scan_events([s.symbol for s in signals])
        event_map = {e.symbol: e for e in events}
        portfolio_value = self._get_portfolio_value()
        log.info(f"💰 账户总资产: ${portfolio_value:,.2f}")

        for signal in signals:
            event = event_map.get(signal.symbol)
            if event:
                self._evaluate_entry(signal, event, portfolio_value)
            elif signal.score >= 70:
                log.info(f"⭐ {signal.symbol}: 高评分 ({signal.score:.0f}) 无催化剂，观察")

        self._monitor_positions()
        log.info(self.risk_manager.summary())

    def _evaluate_entry(self, signal: StockSignal, event: CatalystEvent, portfolio_value: float):
        sym = signal.symbol
        can_open, reason = self.risk_manager.can_open_position(sym, portfolio_value)
        if not can_open:
            log.info(f"⛔ {sym}: 跳过 — {reason}")
            return

        combined_score = signal.score * 0.6 + event.confidence * 100 * 0.4
        log.info(f"🎯 {sym} 交易信号:")
        log.info(f"   技术: {signal.score:.0f}分 | RSI {signal.rsi:.1f} | 量 {signal.volume_ratio:.1f}x")
        log.info(f"   催化剂: {event.description}")
        log.info(f"   综合评分: {combined_score:.1f}")

        if combined_score < 55:
            log.info("   → 评分不足55，跳过")
            return

        sizing = self.risk_manager.calculate_position_size(sym, signal.price, portfolio_value)
        log.info(f"   → 买入 {sizing.quantity}股 @ ~${signal.price:.2f} | 止损 ${sizing.stop_loss_price:.2f} | 止盈 ${sizing.take_profit_price:.2f}")
        self._execute_buy(signal, sizing)

    def _execute_buy(self, signal: StockSignal, sizing):
        sym = signal.symbol
        if self.dry_run:
            log.info(f"   [模拟] 买入 {sym} {sizing.quantity}股")
            self.risk_manager.register_position(
                sym, sizing.quantity, sizing.entry_price,
                sizing.stop_loss_price, sizing.take_profit_price)
            return
        try:
            limit_price = round(signal.price * 0.997, 2)
            result = self.client.place_buy_order(sym, sizing.quantity, "limit", limit_price)
            log.info(f"   ✅ 买单提交: {result}")
            self.client.place_stop_loss(sym, sizing.quantity, sizing.stop_loss_price)
            log.info(f"   🛡️  止损单 @ ${sizing.stop_loss_price:.2f}")
            self.risk_manager.register_position(
                sym, sizing.quantity, sizing.entry_price,
                sizing.stop_loss_price, sizing.take_profit_price)
        except Exception as e:
            log.error(f"   ❌ 买入失败: {e}")

    def _monitor_positions(self):
        if not self.risk_manager.open_positions:
            return
        log.info(f"👀 监控 {len(self.risk_manager.open_positions)} 个持仓...")
        for sym in list(self.risk_manager.open_positions.keys()):
            try:
                if self.dry_run:
                    import yfinance as yf
                    data = yf.download(sym, period="1d", interval="1m", progress=False)
                    if data.empty:
                        continue
                    current_price = float(data["Close"].iloc[-1])
                else:
                    current_price = float(self.client.get_quote(sym).get("price", 0))

                should_exit, reason = self.risk_manager.check_exit_signals(sym, current_price)
                if should_exit:
                    log.info(f"🚨 {sym} @ ${current_price:.2f}: {reason}")
                    self._execute_sell(sym, current_price)
            except Exception as e:
                log.error(f"监控 {sym} 出错: {e}")

    def _execute_sell(self, symbol: str, current_price: float):
        pos = self.risk_manager.open_positions.get(symbol, {})
        quantity = pos.get("quantity", 0)
        if self.dry_run:
            pnl = self.risk_manager.close_position(symbol, current_price)
            log.info(f"   [模拟] 卖出 {symbol} {quantity}股 @ ${current_price:.2f} | PnL: ${pnl:+.2f}")
            return
        try:
            result = self.client.place_sell_order(symbol, quantity, "market")
            log.info(f"   ✅ 卖单提交: {result}")
            pnl = self.risk_manager.close_position(symbol, current_price)
            log.info(f"   💵 平仓盈亏: ${pnl:+.2f}")
        except Exception as e:
            log.error(f"   ❌ 卖出失败: {e}")
'''

# ── 创建文件 ──────────────────────────────────────────────
created = []
for path, content in files.items():
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    created.append(path)
    print(f"  ✅ {path}")

print(f"\n✅ 创建了 {len(created)} 个文件")
print("\n下一步:")
print("  git checkout -b claude/amazing-ride-d2d7vs")
print("  git add .")
print('  git commit -m "feat: event-driven trading bot with Robinhood MCP"')
print("  git push -u origin claude/amazing-ride-d2d7vs")

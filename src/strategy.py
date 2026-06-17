"""
事件驱动交易策略主逻辑
将筛选器、事件监控、风控、Robinhood客户端整合在一起
"""
import os
import time
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
    handlers=[
        logging.FileHandler("trading_log.txt"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# 默认监控股票池（可按需修改）
DEFAULT_WATCHLIST = [
    # 科技 — 大盘
    "AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "NVDA", "AMD", "INTC", "CRM",
    "SNOW", "ORCL", "IBM", "CSCO", "QCOM", "TXN", "AVGO", "MU", "AMAT", "LRCX",
    "KLAC", "MRVL", "ADI", "NXPI", "ON", "MPWR", "ENPH", "FSLR",
    # 软件/云
    "NOW", "ADBE", "INTU", "PANW", "CRWD", "ZS", "DDOG", "MDB", "NET", "FTNT",
    "OKTA", "TEAM", "WDAY", "HUBS", "GTLB", "TTD", "RBLX", "U",
    # 消费/零售
    "COST", "TGT", "WMT", "HD", "LOW", "NKE", "SBUX", "MCD", "YUM", "CMG",
    "LULU", "TJX", "ROST", "DG", "DLTR", "EBAY", "ETSY", "W",
    # 通信/媒体
    "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR", "PARA", "WBD",
    # 医疗健康
    "JNJ", "PFE", "MRNA", "ABBV", "BMY", "UNH", "CVS", "CI", "HUM", "ELV",
    "LLY", "MRK", "AMGN", "GILD", "BIIB", "REGN", "VRTX", "ISRG", "BSX", "MDT",
    "ABT", "TMO", "DHR", "A", "IQV", "ZBH",
    # 金融
    "JPM", "BAC", "GS", "MS", "V", "MA", "WFC", "C", "AXP", "BLK",
    "SCHW", "COF", "USB", "PNC", "TFC", "BK", "STT", "CB", "AIG", "MET",
    # 工业/制造
    "CAT", "DE", "BA", "HON", "GE", "MMM", "LMT", "RTX", "NOC", "GD",
    "UPS", "FDX", "CSX", "UNP", "NSC",
    # 能源
    "XOM", "CVX", "COP", "SLB", "EOG", "PXD", "MPC", "VLO", "PSX",
    # 原材料
    "LIN", "APD", "NEM", "FCX", "NUE", "CF",
    # 房地产/公用事业
    "NEE", "DUK", "SO", "AEP", "PLD", "AMT", "EQIX", "SPG",
    # 消费必需品
    "PG", "KO", "PEP", "CL", "KMB", "GIS", "K", "HSY", "MKC",
]


class EventDrivenStrategy:
    def __init__(self, watchlist: list[str] = None, dry_run: bool = True):
        """
        dry_run=True: 模拟模式，只打印信号不真实下单
        dry_run=False: 实盘模式
        """
        self.watchlist = watchlist or DEFAULT_WATCHLIST
        self.dry_run = dry_run
        self.screener = StockScreener()
        self.event_monitor = EventMonitor()
        self.risk_manager = RiskManager()
        self.client = None

        if not dry_run:
            self.client = get_client(prefer_mcp=True)

        log.info(f"策略初始化完成 | 模式: {'模拟' if dry_run else '实盘'} | 监控股票: {len(self.watchlist)}只")

    def _get_portfolio_value(self) -> float:
        """获取账户总资产"""
        if self.dry_run:
            return 10_000.0  # 模拟1万美元

        try:
            account = self.client.get_account()
            # MCP模式
            if isinstance(account, dict) and "equity" in account:
                return float(account["equity"])
            # robin_stocks模式
            portfolio = account.get("portfolio", {})
            return float(portfolio.get("equity", 10_000))
        except Exception as e:
            log.warning(f"获取账户余额失败: {e}，使用默认值$10,000")
            return 10_000.0

    def run_scan(self):
        """主扫描循环：筛选 → 事件匹配 → 生成交易信号"""
        log.info("=" * 60)
        log.info(f"🚀 开始新一轮扫描 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")

        # 第一步：技术+基本面筛选
        signals = self.screener.scan_watchlist(self.watchlist)
        if not signals:
            log.info("本轮扫描未发现符合条件的股票")
            return

        # 第二步：事件催化剂扫描
        candidate_symbols = [s.symbol for s in signals]
        events = self.event_monitor.scan_events(candidate_symbols)

        # 第三步：信号匹配 — 同时具备技术信号 + 催化剂事件的才考虑建仓
        event_symbols = {e.symbol: e for e in events}

        portfolio_value = self._get_portfolio_value()
        log.info(f"💰 账户总资产: ${portfolio_value:,.2f}")

        for signal in signals:
            sym = signal.symbol
            event = event_symbols.get(sym)

            # 有催化剂事件的优先，无事件但评分>70也可考虑
            if event:
                self._evaluate_entry(signal, event, portfolio_value)
            elif signal.score >= 70:
                log.info(f"⭐ {sym}: 高评分 ({signal.score:.0f}) 无催化剂，继续观察")

        # 第四步：检查现有持仓止损/止盈
        self._monitor_positions(portfolio_value)

        log.info(self.risk_manager.summary())

    def _evaluate_entry(self, signal: StockSignal, event: CatalystEvent, portfolio_value: float):
        """评估是否入场"""
        sym = signal.symbol

        can_open, reason = self.risk_manager.can_open_position(sym, portfolio_value)
        if not can_open:
            log.info(f"⛔ {sym}: 跳过 — {reason}")
            return

        # 信号综合置信度
        combined_score = signal.score * 0.6 + event.confidence * 100 * 0.4

        log.info(f"🎯 {sym} 触发交易信号:")
        log.info(f"   技术评分: {signal.score:.0f} | RSI: {signal.rsi:.1f} | 成交量: {signal.volume_ratio:.1f}x")
        log.info(f"   催化剂: {event.description}")
        log.info(f"   综合评分: {combined_score:.1f}")

        if combined_score < 55:
            log.info(f"   → 综合评分不足55，跳过")
            return

        sizing = self.risk_manager.calculate_position_size(sym, signal.price, portfolio_value)
        log.info(f"   → 计划买入 {sizing.quantity}股 @ ~${signal.price:.2f}")
        log.info(f"   → 止损 ${sizing.stop_loss_price:.2f} | 止盈 ${sizing.take_profit_price:.2f}")
        log.info(f"   → 最大风险 ${sizing.risk_amount:.2f}")

        self._execute_buy(signal, sizing)

    def _execute_buy(self, signal: StockSignal, sizing):
        """执行买入订单"""
        sym = signal.symbol

        if self.dry_run:
            log.info(f"   [模拟] 买入 {sym} {sizing.quantity}股")
            self.risk_manager.register_position(
                sym, sizing.quantity, sizing.entry_price,
                sizing.stop_loss_price, sizing.take_profit_price
            )
            return

        try:
            # 限价单，价格比市价低0.3%避免滑点
            limit_price = round(signal.price * 0.997, 2)
            result = self.client.place_buy_order(sym, sizing.quantity, "limit", limit_price)
            log.info(f"   ✅ 买单已提交: {result}")

            # 同时设置止损单
            self.client.place_stop_loss(sym, sizing.quantity, sizing.stop_loss_price)
            log.info(f"   🛡️  止损单已设置 @ ${sizing.stop_loss_price:.2f}")

            self.risk_manager.register_position(
                sym, sizing.quantity, sizing.entry_price,
                sizing.stop_loss_price, sizing.take_profit_price
            )
        except Exception as e:
            log.error(f"   ❌ 买入失败: {e}")

    def _monitor_positions(self, portfolio_value: float):
        """监控持仓，检查止盈止损"""
        if not self.risk_manager.open_positions:
            return

        log.info(f"👀 监控 {len(self.risk_manager.open_positions)} 个持仓...")

        for sym in list(self.risk_manager.open_positions.keys()):
            try:
                if self.dry_run:
                    import yfinance as yf
                    price_data = yf.download(sym, period="1d", interval="1m", progress=False)
                    if price_data.empty:
                        continue
                    current_price = float(price_data["Close"].iloc[-1])
                else:
                    quote = self.client.get_quote(sym)
                    current_price = float(quote.get("price", 0))

                should_exit, exit_reason = self.risk_manager.check_exit_signals(sym, current_price)
                if should_exit:
                    log.info(f"🚨 {sym} @ ${current_price:.2f}: {exit_reason}")
                    self._execute_sell(sym, current_price)

            except Exception as e:
                log.error(f"监控 {sym} 出错: {e}")

    def _execute_sell(self, symbol: str, current_price: float):
        """执行卖出"""
        pos = self.risk_manager.open_positions.get(symbol, {})
        quantity = pos.get("quantity", 0)

        if self.dry_run:
            pnl = self.risk_manager.close_position(symbol, current_price)
            log.info(f"   [模拟] 卖出 {symbol} {quantity}股 @ ${current_price:.2f} | PnL: ${pnl:+.2f}")
            return

        try:
            result = self.client.place_sell_order(symbol, quantity, "market")
            log.info(f"   ✅ 卖单已提交: {result}")
            pnl = self.risk_manager.close_position(symbol, current_price)
            log.info(f"   💵 平仓盈亏: ${pnl:+.2f}")
        except Exception as e:
            log.error(f"   ❌ 卖出失败: {e}")

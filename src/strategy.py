"""
事件驱动交易策略主逻辑
- 最多同时持有2只股票
- 无机会时等待，不强行入场
- 持仓中持续判断趋势，及时止盈/止损/趋势反转卖出
"""
import os
import time
import logging
import robin_stocks.robinhood as r
import pandas as pd
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

MAX_POSITIONS = 2          # 最多同时持有2只
MIN_COMBINED_SCORE = 65    # 提高入场门槛

DEFAULT_WATCHLIST = [
    # 科技大盘 (28只)
    "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "AMD", "INTC", "CRM", "SNOW",
    "ORCL", "CSCO", "QCOM", "TXN", "AVGO", "MU", "AMAT", "LRCX", "KLAC", "MRVL",
    "ADI", "NXPI", "ON", "MPWR", "ENPH", "FSLR", "TSM", "ASML",
    # 软件/云 (21只)
    "NOW", "ADBE", "INTU", "PANW", "CRWD", "ZS", "DDOG", "MDB", "NET", "FTNT",
    "OKTA", "TEAM", "WDAY", "HUBS", "GTLB", "TTD", "RBLX", "U", "PLTR", "SHOP", "COIN",
]


class EventDrivenStrategy:
    def __init__(self, watchlist: list[str] = None, dry_run: bool = True):
        self.watchlist = watchlist or DEFAULT_WATCHLIST
        self.dry_run = dry_run
        self.screener = StockScreener()
        self.event_monitor = EventMonitor()
        self.risk_manager = RiskManager()
        self.client = None

        if not dry_run:
            self.client = get_client(prefer_mcp=True)

        log.info(f"策略初始化 | 模式: {'模拟' if dry_run else '实盘'} | 股票池: {len(self.watchlist)}只 | 最大持仓: {MAX_POSITIONS}只")

    def _get_portfolio_value(self) -> float:
        if self.dry_run:
            return 10_000.0

        try:
            import robin_stocks.robinhood as r
            portfolio = r.load_portfolio_profile()
            return float(portfolio.get("equity", 10_000))
        except Exception as e:
            log.warning(f"获取账户余额失败: {e}")
            return 10_000.0

    def _get_current_price(self, symbol: str) -> float:
        """获取实时价格"""
        try:
            if self.dry_run:
                hist = r.get_stock_historicals(symbol, interval="5minute", span="day", bounds="regular")
                if hist:
                    return float(hist[-1]["close_price"])
            else:
                quote = self.client.get_quote(symbol)
                return float(quote.get("price", 0))
        except Exception:
            pass
        return 0.0

    def _check_trend_exit(self, symbol: str, entry_price: float) -> tuple[bool, str]:
        """
        趋势判断卖出逻辑（止损/止盈之外的额外判断）：
        - RSI > 75：超买，获利了结
        - MACD死叉：动能反转
        - 价格跌破20日均线且亏损中：趋势破坏
        """
        try:
            hist_raw = r.get_stock_historicals(symbol, interval="day", span="3month", bounds="regular")
            if not hist_raw or len(hist_raw) < 26:
                return False, ""

            hist = pd.DataFrame(hist_raw)
            closes = hist["close_price"].astype(float)
            current_price = float(closes.iloc[-1])

            # RSI超买
            delta = closes.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rsi = float((100 - 100 / (1 + gain / loss)).iloc[-1])

            if rsi > 75:
                return True, f"RSI超买({rsi:.1f})，获利了结"

            # MACD死叉
            ema12 = closes.ewm(span=12).mean()
            ema26 = closes.ewm(span=26).mean()
            macd = ema12 - ema26
            signal_line = macd.ewm(span=9).mean()
            if macd.iloc[-1] < signal_line.iloc[-1] and macd.iloc[-2] >= signal_line.iloc[-2]:
                pnl_pct = (current_price - entry_price) / entry_price * 100
                if pnl_pct > 3:  # 有盈利才在死叉时卖，亏损留给止损处理
                    return True, f"MACD死叉，锁定盈利 ({pnl_pct:+.1f}%)"

            # 跌破20日均线且亏损
            ma20 = float(closes.rolling(20).mean().iloc[-1])
            if current_price < ma20 and current_price < entry_price:
                return True, f"跌破20日均线且亏损，趋势破坏"

        except Exception as e:
            log.debug(f"趋势判断出错 {symbol}: {e}")

        return False, ""

    def monitor_only(self):
        """每小时从Robinhood拉取真实持仓，检查止损/止盈/趋势"""
        log.info(f"👀 持仓监控 [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")

        # 直接从Robinhood读取真实持仓（不依赖内存状态）
        positions = self._load_robinhood_positions()
        if not positions:
            log.info("   当前无持仓，跳过")
            return

        log.info(f"   从Robinhood读取到 {len(positions)} 个持仓")
        for symbol, pos in positions.items():
            self.risk_manager.open_positions[symbol] = pos

        self._monitor_positions()

    def _load_robinhood_positions(self) -> dict:
        """从Robinhood拉取当前持仓，转换为risk_manager格式"""
        if self.dry_run:
            return self.risk_manager.open_positions

        try:
            raw = r.get_open_stock_positions()
            if not raw:
                return {}

            positions = {}
            for pos in raw:
                symbol = r.get_symbol_by_url(pos.get("instrument"))
                if not symbol:
                    continue
                qty = float(pos.get("quantity", 0))
                avg_price = float(pos.get("average_buy_price", 0))
                if qty <= 0 or avg_price <= 0:
                    continue

                positions[symbol] = {
                    "quantity": qty,
                    "entry_price": avg_price,
                    "stop_loss": round(avg_price * (1 - float(os.getenv("STOP_LOSS_PCT", 0.07))), 2),
                    "take_profit": round(avg_price * (1 + float(os.getenv("TAKE_PROFIT_PCT", 0.20))), 2),
                }
                log.info(f"   {symbol}: {qty}股 @ ${avg_price:.2f} | 止损${positions[symbol]['stop_loss']:.2f} | 止盈${positions[symbol]['take_profit']:.2f}")

            return positions
        except Exception as e:
            log.error(f"读取Robinhood持仓失败: {e}")
            return {}

    def run_scan(self):
        log.info("=" * 60)
        log.info(f"🚀 开始扫描 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")

        # 每次启动先从Robinhood同步真实持仓
        real_positions = self._load_robinhood_positions()
        self.risk_manager.open_positions = real_positions

        log.info(f"📊 当前持仓: {len(real_positions)}/{MAX_POSITIONS}")

        # 第一步：先检查现有持仓是否需要卖出
        if self.risk_manager.open_positions:
            self._monitor_positions()

        # 第二步：如果已满仓，不再买入
        if len(self.risk_manager.open_positions) >= MAX_POSITIONS:
            log.info(f"⏸️  已持有{MAX_POSITIONS}只股票，等待卖出信号...")
            return

        # 第三步：扫描买入机会
        slots_available = MAX_POSITIONS - len(self.risk_manager.open_positions)
        log.info(f"🔍 寻找买入机会（还可买入{slots_available}只）...")

        signals = self.screener.scan_watchlist(self.watchlist)
        if not signals:
            log.info("⏳ 本轮未发现符合技术条件的股票，等待下次扫描...")
            return

        # 事件催化剂匹配
        candidate_symbols = [s.symbol for s in signals]
        events = self.event_monitor.scan_events(candidate_symbols)
        event_map = {e.symbol: e for e in events}

        portfolio_value = self._get_portfolio_value()
        log.info(f"💰 账户总资产: ${portfolio_value:,.2f}")

        # 按综合评分排序，取最优的填满剩余槽位
        candidates = []
        for signal in signals:
            event = event_map.get(signal.symbol)
            if not event:
                if signal.score >= 75:
                    log.info(f"⭐ {signal.symbol}: 评分{signal.score:.0f}但无催化剂，观察")
                continue
            combined = signal.score * 0.6 + event.confidence * 100 * 0.4
            candidates.append((combined, signal, event))

        candidates.sort(key=lambda x: x[0], reverse=True)

        if candidates:
            log.info(f"📋 候选股票排名（综合评分 = 技术×60% + 事件×40%，门槛>{MIN_COMBINED_SCORE}）:")
            for score, sig, ev in candidates[:5]:
                status = "✅ 可买入" if score >= MIN_COMBINED_SCORE else f"❌ 不足{MIN_COMBINED_SCORE}分"
                log.info(f"   {sig.symbol}: 技术{sig.score:.0f} + 事件{ev.confidence*100:.0f} = 综合{score:.1f} {status}")
        else:
            log.info("⏳ 当前无符合条件的买入机会（需同时满足技术信号+催化剂事件），继续等待...")

        bought = 0
        for combined_score, signal, event in candidates:
            if bought >= slots_available:
                break
            if combined_score < MIN_COMBINED_SCORE:
                break
            if self._evaluate_entry(signal, event, combined_score, portfolio_value):
                bought += 1

        log.info(self.risk_manager.summary())

    def _evaluate_entry(self, signal: StockSignal, event: CatalystEvent,
                        combined_score: float, portfolio_value: float) -> bool:
        sym = signal.symbol

        can_open, reason = self.risk_manager.can_open_position(sym, portfolio_value)
        if not can_open:
            log.info(f"⛔ {sym}: {reason}")
            return False

        log.info(f"🎯 【买入候选】{sym}")
        log.info(f"   技术评分: {signal.score:.0f} | RSI: {signal.rsi:.1f} | 成交量: {signal.volume_ratio:.1f}x")
        log.info(f"   催化剂: {event.description}")
        log.info(f"   综合评分: {combined_score:.1f} / 100")

        sizing = self.risk_manager.calculate_position_size(sym, signal.price, portfolio_value)
        log.info(f"   买入: {sizing.quantity}股 @ ~${signal.price:.2f}")
        log.info(f"   止损: ${sizing.stop_loss_price:.2f} (-7%) | 止盈: ${sizing.take_profit_price:.2f} (+20%)")

        self._execute_buy(signal, sizing)
        return True

    def _execute_buy(self, signal: StockSignal, sizing):
        sym = signal.symbol

        if self.dry_run:
            log.info(f"   [模拟] 买入 {sym} {sizing.quantity}股")
            self.risk_manager.register_position(
                sym, sizing.quantity, sizing.entry_price,
                sizing.stop_loss_price, sizing.take_profit_price
            )
            return

        try:
            limit_price = round(signal.price * 0.997, 2)
            result = self.client.place_buy_order(sym, sizing.quantity, "limit", limit_price)
            log.info(f"   ✅ 买单已提交: {result}")
            self.client.place_stop_loss(sym, sizing.quantity, sizing.stop_loss_price)
            log.info(f"   🛡️  止损单: ${sizing.stop_loss_price:.2f}")
            self.risk_manager.register_position(
                sym, sizing.quantity, sizing.entry_price,
                sizing.stop_loss_price, sizing.take_profit_price
            )
        except Exception as e:
            log.error(f"   ❌ 买入失败: {e}")

    def _monitor_positions(self):
        """检查持仓：止损/止盈 + 趋势判断"""
        log.info(f"👀 监控 {len(self.risk_manager.open_positions)} 个持仓...")

        for sym in list(self.risk_manager.open_positions.keys()):
            pos = self.risk_manager.open_positions[sym]
            entry_price = pos["entry_price"]

            try:
                current_price = self._get_current_price(sym)
                if current_price <= 0:
                    continue

                pnl_pct = (current_price - entry_price) / entry_price * 100
                log.info(f"   {sym}: ${current_price:.2f} | 盈亏: {pnl_pct:+.1f}%")

                # 止损/止盈检查
                should_exit, exit_reason = self.risk_manager.check_exit_signals(sym, current_price)
                if should_exit:
                    log.info(f"🚨 {sym}: {exit_reason}")
                    self._execute_sell(sym, current_price)
                    continue

                # 趋势判断
                trend_exit, trend_reason = self._check_trend_exit(sym, entry_price)
                if trend_exit:
                    log.info(f"📉 {sym}: {trend_reason}")
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
            log.info(f"   ✅ 卖单已提交: {result}")
            pnl = self.risk_manager.close_position(symbol, current_price)
            log.info(f"   💵 平仓盈亏: ${pnl:+.2f}")
        except Exception as e:
            log.error(f"   ❌ 卖出失败: {e}")

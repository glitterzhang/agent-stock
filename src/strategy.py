"""
Event-driven trading strategy — main logic
- Hold at most 2 positions simultaneously
- Wait when there are no opportunities; never force entry
- Continuously evaluate trend while in a position; exit promptly on take-profit/stop-loss/trend reversal
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

MAX_POSITIONS = 2          # maximum simultaneous positions
MIN_COMBINED_SCORE = 65    # raise entry bar

DEFAULT_WATCHLIST = [
    # Large-cap tech (28 stocks)
    "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "AMD", "INTC", "CRM", "SNOW",
    "ORCL", "CSCO", "QCOM", "TXN", "AVGO", "MU", "AMAT", "LRCX", "KLAC", "MRVL",
    "ADI", "NXPI", "ON", "MPWR", "ENPH", "FSLR", "TSM", "ASML",
    # Software/cloud (21 stocks)
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

        log.info(f"Strategy initialized | mode: {'paper' if dry_run else 'live'} | watchlist: {len(self.watchlist)} stocks | max positions: {MAX_POSITIONS}")

    def _get_portfolio_value(self) -> float:
        if self.dry_run:
            return 10_000.0

        try:
            import robin_stocks.robinhood as r
            portfolio = r.load_portfolio_profile()
            return float(portfolio.get("equity", 10_000))
        except Exception as e:
            log.warning(f"Failed to fetch account balance: {e}")
            return 10_000.0

    def _get_current_price(self, symbol: str) -> float:
        """Fetch real-time price"""
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
        Trend-based exit logic (additional checks beyond stop-loss/take-profit):
        - RSI > 75: overbought, take profit
        - MACD death cross: momentum reversal
        - Price drops below 20-day MA while in a loss: trend breakdown
        """
        try:
            hist_raw = r.get_stock_historicals(symbol, interval="day", span="3month", bounds="regular")
            if not hist_raw or len(hist_raw) < 26:
                return False, ""

            hist = pd.DataFrame(hist_raw)
            closes = hist["close_price"].astype(float)
            current_price = float(closes.iloc[-1])

            # RSI overbought
            delta = closes.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rsi = float((100 - 100 / (1 + gain / loss)).iloc[-1])

            if rsi > 75:
                return True, f"RSI overbought ({rsi:.1f}), taking profit"

            # MACD death cross
            ema12 = closes.ewm(span=12).mean()
            ema26 = closes.ewm(span=26).mean()
            macd = ema12 - ema26
            signal_line = macd.ewm(span=9).mean()
            if macd.iloc[-1] < signal_line.iloc[-1] and macd.iloc[-2] >= signal_line.iloc[-2]:
                pnl_pct = (current_price - entry_price) / entry_price * 100
                if pnl_pct > 3:  # only sell on death cross when profitable; let stop-loss handle losses
                    return True, f"MACD death cross, locking in profit ({pnl_pct:+.1f}%)"

            # Price drops below 20-day MA while in a loss
            ma20 = float(closes.rolling(20).mean().iloc[-1])
            if current_price < ma20 and current_price < entry_price:
                return True, f"Price broke below 20-day MA while in a loss, trend breakdown"

        except Exception as e:
            log.debug(f"Trend check error for {symbol}: {e}")

        return False, ""

    def monitor_only(self):
        """Pull real positions from Robinhood every hour and check stop-loss/take-profit/trend"""
        log.info(f"👀 Position monitor [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")

        # Read real positions directly from Robinhood (not relying on in-memory state)
        positions = self._load_robinhood_positions()
        if not positions:
            log.info("   No open positions, skipping")
            return

        log.info(f"   Loaded {len(positions)} positions from Robinhood")
        for symbol, pos in positions.items():
            self.risk_manager.open_positions[symbol] = pos

        self._monitor_positions()

    def _load_robinhood_positions(self) -> dict:
        """Pull current positions from Robinhood and convert to risk_manager format"""
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
                log.info(f"   {symbol}: {qty} shares @ ${avg_price:.2f} | stop ${positions[symbol]['stop_loss']:.2f} | target ${positions[symbol]['take_profit']:.2f}")

            return positions
        except Exception as e:
            log.error(f"Failed to load Robinhood positions: {e}")
            return {}

    def run_scan(self):
        log.info("=" * 60)
        log.info(f"🚀 Starting scan [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")

        # Sync real positions from Robinhood at the start of each run
        real_positions = self._load_robinhood_positions()
        self.risk_manager.open_positions = real_positions

        log.info(f"📊 Current positions: {len(real_positions)}/{MAX_POSITIONS}")

        # Step 1: check whether existing positions need to be sold
        if self.risk_manager.open_positions:
            self._monitor_positions()

        # Step 2: if fully invested, do not buy more
        if len(self.risk_manager.open_positions) >= MAX_POSITIONS:
            log.info(f"⏸️  Already holding {MAX_POSITIONS} stocks, waiting for sell signals...")
            return

        # Step 3: scan for buy opportunities
        slots_available = MAX_POSITIONS - len(self.risk_manager.open_positions)
        log.info(f"🔍 Looking for buy opportunities ({slots_available} slot(s) available)...")

        signals = self.screener.scan_watchlist(self.watchlist)
        if not signals:
            log.info("⏳ No stocks met technical criteria this round, waiting for next scan...")
            return

        # Match event catalysts
        candidate_symbols = [s.symbol for s in signals]
        events = self.event_monitor.scan_events(candidate_symbols)
        event_map = {e.symbol: e for e in events}

        portfolio_value = self._get_portfolio_value()
        log.info(f"💰 Total portfolio value: ${portfolio_value:,.2f}")

        # Sort by combined score; fill remaining slots with the best candidates
        candidates = []
        for signal in signals:
            event = event_map.get(signal.symbol)
            if not event:
                if signal.score >= 75:
                    log.info(f"⭐ {signal.symbol}: score {signal.score:.0f} but no catalyst, watching")
                continue
            combined = signal.score * 0.6 + event.confidence * 100 * 0.4
            candidates.append((combined, signal, event))

        candidates.sort(key=lambda x: x[0], reverse=True)

        if candidates:
            log.info(f"📋 Candidate ranking (combined = technical×60% + event×40%, threshold >{MIN_COMBINED_SCORE}):")
            for score, sig, ev in candidates[:5]:
                status = "✅ buyable" if score >= MIN_COMBINED_SCORE else f"❌ below {MIN_COMBINED_SCORE}"
                log.info(f"   {sig.symbol}: technical {sig.score:.0f} + event {ev.confidence*100:.0f} = combined {score:.1f} {status}")
        else:
            log.info("⏳ No qualifying buy opportunities right now (need both technical signal + catalyst), waiting...")

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

        log.info(f"🎯 [Buy candidate] {sym}")
        log.info(f"   Technical score: {signal.score:.0f} | RSI: {signal.rsi:.1f} | Volume: {signal.volume_ratio:.1f}x")
        log.info(f"   Catalyst: {event.description}")
        log.info(f"   Combined score: {combined_score:.1f} / 100")

        sizing = self.risk_manager.calculate_position_size(sym, signal.price, portfolio_value)
        log.info(f"   Buy: {sizing.quantity} shares @ ~${signal.price:.2f}")
        log.info(f"   Stop: ${sizing.stop_loss_price:.2f} (-7%) | Target: ${sizing.take_profit_price:.2f} (+20%)")

        self._execute_buy(signal, sizing)
        return True

    def _execute_buy(self, signal: StockSignal, sizing):
        sym = signal.symbol

        if self.dry_run:
            log.info(f"   [paper] Buy {sym} {sizing.quantity} shares")
            self.risk_manager.register_position(
                sym, sizing.quantity, sizing.entry_price,
                sizing.stop_loss_price, sizing.take_profit_price
            )
            return

        try:
            limit_price = round(signal.price * 0.997, 2)
            result = self.client.place_buy_order(sym, sizing.quantity, "limit", limit_price)
            log.info(f"   ✅ Buy order submitted: {result}")
            self.client.place_stop_loss(sym, sizing.quantity, sizing.stop_loss_price)
            log.info(f"   🛡️  Stop-loss order: ${sizing.stop_loss_price:.2f}")
            self.risk_manager.register_position(
                sym, sizing.quantity, sizing.entry_price,
                sizing.stop_loss_price, sizing.take_profit_price
            )
        except Exception as e:
            log.error(f"   ❌ Buy failed: {e}")

    def _monitor_positions(self):
        """Check positions: stop-loss/take-profit + trend evaluation"""
        log.info(f"👀 Monitoring {len(self.risk_manager.open_positions)} position(s)...")

        for sym in list(self.risk_manager.open_positions.keys()):
            pos = self.risk_manager.open_positions[sym]
            entry_price = pos["entry_price"]

            try:
                current_price = self._get_current_price(sym)
                if current_price <= 0:
                    continue

                pnl_pct = (current_price - entry_price) / entry_price * 100
                log.info(f"   {sym}: ${current_price:.2f} | PnL: {pnl_pct:+.1f}%")

                # Stop-loss/take-profit check
                should_exit, exit_reason = self.risk_manager.check_exit_signals(sym, current_price)
                if should_exit:
                    log.info(f"🚨 {sym}: {exit_reason}")
                    self._execute_sell(sym, current_price)
                    continue

                # Trend evaluation
                trend_exit, trend_reason = self._check_trend_exit(sym, entry_price)
                if trend_exit:
                    log.info(f"📉 {sym}: {trend_reason}")
                    self._execute_sell(sym, current_price)

            except Exception as e:
                log.error(f"Error monitoring {sym}: {e}")

    def _execute_sell(self, symbol: str, current_price: float):
        pos = self.risk_manager.open_positions.get(symbol, {})
        quantity = pos.get("quantity", 0)

        if self.dry_run:
            pnl = self.risk_manager.close_position(symbol, current_price)
            log.info(f"   [paper] Sell {symbol} {quantity} shares @ ${current_price:.2f} | PnL: ${pnl:+.2f}")
            return

        try:
            result = self.client.place_sell_order(symbol, quantity, "market")
            log.info(f"   ✅ Sell order submitted: {result}")
            pnl = self.risk_manager.close_position(symbol, current_price)
            log.info(f"   💵 Closed PnL: ${pnl:+.2f}")
        except Exception as e:
            log.error(f"   ❌ Sell failed: {e}")

"""
Stock screener — data source: robin_stocks (direct Robinhood connection, no network restrictions)
"""
import os
import time
import numpy as np
import pandas as pd
import robin_stocks.robinhood as r
from dataclasses import dataclass
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
    reasons: list[str]


class StockScreener:
    def __init__(self):
        self.rsi_threshold = float(os.getenv("RSI_OVERSOLD", 40))
        self.max_pe = float(os.getenv("MAX_PE_RATIO", 25))
        self.max_pb = float(os.getenv("MAX_PB_RATIO", 3.0))
        self.volume_multiplier = float(os.getenv("VOLUME_SURGE_MULTIPLIER", 1.5))
        self.min_market_cap = float(os.getenv("MIN_MARKET_CAP", 500_000_000))
        self._logged_in = False

    def _ensure_login(self):
        if not self._logged_in:
            import pyotp
            email = os.getenv("ROBINHOOD_EMAIL")
            password = os.getenv("ROBINHOOD_PASSWORD")
            mfa_key = os.getenv("ROBINHOOD_MFA_KEY")
            if not email or not password:
                raise ValueError("Please set ROBINHOOD_EMAIL and ROBINHOOD_PASSWORD in the .env file")
            mfa_code = pyotp.TOTP(mfa_key).now() if mfa_key else None
            r.login(email, password, mfa_code=mfa_code)
            self._logged_in = True
            print("✅ Robinhood login successful, fetching data...")

    def _fetch(self, symbol: str):
        """Fetch stock data from Robinhood"""
        self._ensure_login()

        # Use hourly bars for RSI (more real-time), daily bars for volume ratio
        hist_hour_raw = r.get_stock_historicals(
            symbol, interval="hour", span="month", bounds="regular"
        )
        hist_day_raw = r.get_stock_historicals(
            symbol, interval="day", span="3month", bounds="regular"
        )
        if not hist_hour_raw or not hist_day_raw:
            return None, {}, None

        # Hourly bars for RSI/MACD (past 1 month, ~160 candles)
        hist = pd.DataFrame(hist_hour_raw)
        hist["Close"] = hist["close_price"].astype(float)
        hist["Volume"] = hist["volume"].astype(float)
        hist["Date"] = pd.to_datetime(hist["begins_at"])
        hist = hist.set_index("Date").sort_index()

        # Daily bars for volume ratio (today's volume vs 20-day average)
        hist_day = pd.DataFrame(hist_day_raw)
        hist_day["Volume"] = hist_day["volume"].astype(float)
        hist_day["Date"] = pd.to_datetime(hist_day["begins_at"])
        hist_day = hist_day.set_index("Date").sort_index()

        # Fundamental data
        fundamentals = r.get_fundamentals(symbol)
        info_raw = fundamentals[0] if fundamentals else {}

        quotes = r.get_quotes(symbol)
        quote = quotes[0] if quotes else {}

        info = {
            "trailingPE": _safe_float(info_raw.get("pe_ratio")),
            "priceToBook": _safe_float(info_raw.get("pb_ratio")),
            "marketCap": _safe_float(info_raw.get("market_cap")),
            "targetMeanPrice": _safe_float(quote.get("last_trade_price")),
        }

        return None, info, hist, hist_day

    def calculate_rsi(self, closes: pd.Series, period: int = 14) -> float:
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    def calculate_macd_crossover(self, closes: pd.Series) -> bool:
        ema12 = closes.ewm(span=12).mean()
        ema26 = closes.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        return bool(macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2])

    def screen(self, symbol: str) -> Optional[StockSignal]:
        try:
            _, info, hist, hist_day = self._fetch(symbol)
            if hist is None or hist.empty or len(hist) < 20:
                return None

            price = float(hist["Close"].iloc[-1])
            # RSI uses hourly bars (real-time); MACD also uses hourly bars
            rsi = self.calculate_rsi(hist["Close"])

            # Volume ratio uses daily bars (today's volume vs 20-day average)
            avg_vol = hist_day["Volume"].rolling(20).mean().iloc[-1]
            today_vol = hist_day["Volume"].iloc[-1]
            volume_ratio = float(today_vol / avg_vol) if avg_vol > 0 else 0

            pe = info.get("trailingPE")
            pb = info.get("priceToBook")
            market_cap = info.get("marketCap")
            analyst_target = info.get("targetMeanPrice")
            upside_pct = ((analyst_target - price) / price * 100) if analyst_target else None

            reasons = []
            score = 0
            disqualifiers = []

            # Negative PE = company is losing money, disqualify immediately
            if pe and pe < 0:
                disqualifiers.append(f"Loss-making company (PE={pe:.1f}x)")

            if disqualifiers:
                print(f"  ❌ {symbol} disqualified: {', '.join(disqualifiers)}")
                return None

            if rsi < self.rsi_threshold:
                score += 25
                reasons.append(f"RSI oversold ({rsi:.1f})")

            if pe and 0 < pe < self.max_pe:
                score += 20
                reasons.append(f"Low PE ({pe:.1f}x)")
            if pb and pb < self.max_pb:
                score += 15
                reasons.append(f"Low PB ({pb:.1f}x)")

            if volume_ratio >= self.volume_multiplier:
                score += 20
                reasons.append(f"Volume surge ({volume_ratio:.1f}x average)")

            if upside_pct and upside_pct > 20:
                score += 15
                reasons.append(f"Analyst target upside {upside_pct:.1f}%")

            if self.calculate_macd_crossover(hist["Close"]):
                score += 5
                reasons.append("MACD golden cross")

            if market_cap and market_cap < self.min_market_cap:
                return None

            if score < 40 or not reasons:
                return None

            return StockSignal(
                symbol=symbol,
                price=price,
                rsi=rsi,
                pe_ratio=pe,
                pb_ratio=pb,
                volume_ratio=volume_ratio,
                market_cap=market_cap,
                analyst_target=analyst_target,
                upside_pct=upside_pct,
                score=score,
                reasons=reasons,
            )

        except Exception as e:
            print(f"  Error screening {symbol}: {e}")
            return None

    def scan_watchlist(self, watchlist: list[str]) -> list[StockSignal]:
        print(f"🔍 Starting scan of {len(watchlist)} stocks...")
        self._ensure_login()
        signals = []
        for symbol in watchlist:
            time.sleep(0.3)  # avoid hitting rate limits
            sig = self.screen(symbol)
            if sig:
                signals.append(sig)
                print(f"  ✅ {symbol}: score {sig.score:.0f} — {', '.join(sig.reasons)}")

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals


def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None

"""
股票筛选器 — 数据源：robin_stocks（直连Robinhood，无网络限制）
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
                raise ValueError("请在 .env 文件中设置 ROBINHOOD_EMAIL 和 ROBINHOOD_PASSWORD")
            mfa_code = pyotp.TOTP(mfa_key).now() if mfa_key else None
            r.login(email, password, mfa_code=mfa_code)
            self._logged_in = True
            print("✅ Robinhood 登录成功，开始获取数据...")

    def _fetch(self, symbol: str):
        """从 Robinhood 获取股票数据"""
        self._ensure_login()

        # 用小时线计算RSI（更实时），日线计算成交量比率
        hist_hour_raw = r.get_stock_historicals(
            symbol, interval="hour", span="month", bounds="regular"
        )
        hist_day_raw = r.get_stock_historicals(
            symbol, interval="day", span="3month", bounds="regular"
        )
        if not hist_hour_raw or not hist_day_raw:
            return None, {}, None

        # 小时线用于RSI/MACD（过去1个月，约160根K线）
        hist = pd.DataFrame(hist_hour_raw)
        hist["Close"] = hist["close_price"].astype(float)
        hist["Volume"] = hist["volume"].astype(float)
        hist["Date"] = pd.to_datetime(hist["begins_at"])
        hist = hist.set_index("Date").sort_index()

        # 日线用于成交量比率（20日均量对比）
        hist_day = pd.DataFrame(hist_day_raw)
        hist_day["Volume"] = hist_day["volume"].astype(float)
        hist_day["Date"] = pd.to_datetime(hist_day["begins_at"])
        hist_day = hist_day.set_index("Date").sort_index()

        # 基本面数据
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
            # RSI用小时线（实时），MACD同样用小时线
            rsi = self.calculate_rsi(hist["Close"])

            # 成交量比率用日线（今日量 vs 20日均量）
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
            print(f"  筛选 {symbol} 出错: {e}")
            return None

    def scan_watchlist(self, watchlist: list[str]) -> list[StockSignal]:
        print(f"🔍 开始扫描 {len(watchlist)} 只股票...")
        self._ensure_login()
        signals = []
        for symbol in watchlist:
            time.sleep(0.3)  # 避免请求过快
            sig = self.screen(symbol)
            if sig:
                signals.append(sig)
                print(f"  ✅ {symbol}: 评分 {sig.score:.0f} — {', '.join(sig.reasons)}")

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals


def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None

"""
股票筛选器 — 基本面 + 技术面双重过滤，寻找被低估的事件驱动候选股
数据源：akshare（中国网络友好，无需API Key）+ yfinance备用
"""
import os
import time
import random
import numpy as np
import pandas as pd
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
        self._use_akshare = None  # 延迟检测

    def _check_akshare(self) -> bool:
        if self._use_akshare is None:
            try:
                import akshare as ak
                self._use_akshare = True
                print("📡 数据源: akshare")
            except ImportError:
                self._use_akshare = False
                print("📡 数据源: yfinance")
        return self._use_akshare

    def _fetch_akshare(self, symbol: str):
        """用akshare获取美股数据（中国网络友好）"""
        import akshare as ak

        # 获取历史价格
        hist = ak.stock_us_hist(
            symbol=symbol,
            period="daily",
            adjust="qfq"
        )
        if hist is None or hist.empty:
            return None, {}, None

        hist = hist.tail(90)  # 取最近3个月
        hist = hist.rename(columns={
            "日期": "Date", "开盘": "Open", "收盘": "Close",
            "最高": "High", "最低": "Low", "成交量": "Volume"
        })
        hist["Date"] = pd.to_datetime(hist["Date"])
        hist = hist.set_index("Date")

        # 获取基本面数据
        try:
            info_df = ak.stock_us_fundamental(symbol=symbol)
            info = {}
            if info_df is not None and not info_df.empty:
                row = info_df.iloc[0]
                info["trailingPE"] = float(row.get("pe", 0) or 0) or None
                info["priceToBook"] = float(row.get("pb", 0) or 0) or None
                info["marketCap"] = float(row.get("总市值", 0) or 0) or None
        except Exception:
            info = {}

        return None, info, hist

    def _fetch_yfinance(self, symbol: str):
        """yfinance备用数据源"""
        import yfinance as yf
        for i in range(3):
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="3mo", interval="1d")
                info = ticker.info
                return ticker, info, hist
            except Exception as e:
                if "Rate" in str(e) or "429" in str(e) or "Too Many" in str(e):
                    wait = (2 ** i) + random.uniform(1, 3)
                    print(f"  ⏳ {symbol} 限速，等待 {wait:.1f}秒...")
                    time.sleep(wait)
                else:
                    raise
        return None, {}, None

    def _fetch(self, symbol: str):
        if self._check_akshare():
            try:
                return self._fetch_akshare(symbol)
            except Exception as e:
                print(f"  ⚠️  akshare获取{symbol}失败({e})，尝试yfinance...")
        return self._fetch_yfinance(symbol)

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
            _, info, hist = self._fetch(symbol)
            if hist is None or hist.empty or len(hist) < 20:
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
        signals = []
        for symbol in watchlist:
            time.sleep(random.uniform(0.3, 0.8))
            sig = self.screen(symbol)
            if sig:
                signals.append(sig)
                print(f"  ✅ {symbol}: 评分 {sig.score:.0f} — {', '.join(sig.reasons)}")

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals

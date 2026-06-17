"""
股票筛选器 — 基本面 + 技术面双重过滤，寻找被低估的事件驱动候选股
"""
import os
import time
import random
import numpy as np
import pandas as pd
import yfinance as yf
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
    volume_ratio: float       # 今日成交量 / 20日均量
    market_cap: Optional[float]
    analyst_target: Optional[float]
    upside_pct: Optional[float]
    score: float              # 综合评分 0-100
    reasons: list[str]


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
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    def calculate_macd_crossover(self, closes: pd.Series) -> bool:
        """返回True表示MACD金叉（短期动能向上）"""
        ema12 = closes.ewm(span=12).mean()
        ema26 = closes.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        # 金叉：MACD从下方穿越信号线
        return bool(macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2])

    def _fetch_with_retry(self, symbol: str, retries: int = 3):
        """带重试和限速保护的数据获取"""
        for i in range(retries):
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

    def screen(self, symbol: str) -> Optional[StockSignal]:
        try:
            ticker, info, hist = self._fetch_with_retry(symbol)
            if ticker is None or hist is None:
                return None

            if hist.empty or len(hist) < 20:
                return None

            price = float(hist["Close"].iloc[-1])
            rsi = self.calculate_rsi(hist["Close"])

            # 成交量比率
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

            # RSI超卖（低估信号）
            if rsi < self.rsi_threshold:
                score += 25
                reasons.append(f"RSI超卖 ({rsi:.1f})")

            # 估值偏低
            if pe and pe < self.max_pe:
                score += 20
                reasons.append(f"低PE ({pe:.1f}x)")
            if pb and pb < self.max_pb:
                score += 15
                reasons.append(f"低PB ({pb:.1f}x)")

            # 成交量放大
            if volume_ratio >= self.volume_multiplier:
                score += 20
                reasons.append(f"成交量放大 ({volume_ratio:.1f}x均量)")

            # 分析师目标价上行空间
            if upside_pct and upside_pct > 20:
                score += 15
                reasons.append(f"分析师目标价上行 {upside_pct:.1f}%")

            # MACD金叉
            if self.calculate_macd_crossover(hist["Close"]):
                score += 5
                reasons.append("MACD金叉")

            # 过滤市值太小的股票
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
        """批量筛选，返回按评分排序的信号列表"""
        print(f"🔍 开始扫描 {len(watchlist)} 只股票...")
        signals = []
        for symbol in watchlist:
            time.sleep(random.uniform(0.5, 1.5))  # 每只股票之间随机延迟
            sig = self.screen(symbol)
            if sig:
                signals.append(sig)
                print(f"  ✅ {symbol}: 评分 {sig.score:.0f} — {', '.join(sig.reasons)}")

        signals.sort(key=lambda s: s.score, reverse=True)
        return signals

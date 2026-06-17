"""
事件监控器 — 追踪财报日历、内部人买入等催化剂事件
财报数据源：Finnhub 免费API（无需代理，60次/分钟）
"""
import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

FINNHUB_TOKEN = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"


@dataclass
class CatalystEvent:
    symbol: str
    event_type: str          # "earnings", "insider_buy", "analyst_upgrade", "fda", "buyback"
    event_date: datetime
    days_until_event: int
    description: str
    confidence: float        # 0-1，信号可信度
    expected_move_pct: float # 预期涨幅


class EventMonitor:
    def __init__(self):
        self.sec_edgar_url = "https://data.sec.gov/submissions"
        self.headers = {"User-Agent": "agent-stock-bot contact@example.com"}

    # ─────────────────────────────────────────────
    # 财报事件
    # ─────────────────────────────────────────────
    def get_earnings_events(self, symbols: list[str]) -> list[CatalystEvent]:
        """获取未来14天内有财报的股票（Finnhub API）"""
        events = []
        today = datetime.today()
        end_date = today + timedelta(days=14)

        if not FINNHUB_TOKEN:
            print("  ⚠️  未设置 FINNHUB_API_KEY，跳过财报扫描（在.env中添加免费key）")
            return events

        try:
            # 批量获取财报日历
            resp = requests.get(
                f"{FINNHUB_BASE}/calendar/earnings",
                params={
                    "from": today.strftime("%Y-%m-%d"),
                    "to": end_date.strftime("%Y-%m-%d"),
                    "token": FINNHUB_TOKEN,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"  ⚠️  Finnhub财报API返回 {resp.status_code}")
                return events

            data = resp.json().get("earningsCalendar", [])
            symbol_set = set(symbols)

            for item in data:
                sym = item.get("symbol", "")
                if sym not in symbol_set:
                    continue

                date_str = item.get("date", "")
                if not date_str:
                    continue

                earnings_date = datetime.strptime(date_str, "%Y-%m-%d")
                days_until = (earnings_date - today).days

                if 1 <= days_until <= 14:
                    surprise_rate = self._estimate_surprise_finnhub(sym)
                    events.append(CatalystEvent(
                        symbol=sym,
                        event_type="earnings",
                        event_date=earnings_date,
                        days_until_event=days_until,
                        description=f"财报日 {date_str}（{days_until}天后），历史超预期率 {surprise_rate:.0%}",
                        confidence=surprise_rate,
                        expected_move_pct=7.0,
                    ))
        except Exception as e:
            print(f"  获取财报日历出错: {e}")

        return events

    def _estimate_surprise_finnhub(self, symbol: str) -> float:
        """用Finnhub历史EPS数据估算超预期概率"""
        try:
            resp = requests.get(
                f"{FINNHUB_BASE}/stock/earnings",
                params={"symbol": symbol, "limit": 8, "token": FINNHUB_TOKEN},
                timeout=8,
            )
            if resp.status_code != 200:
                return 0.5
            data = resp.json()
            if not data:
                return 0.5
            beats = sum(1 for e in data if (e.get("actual") or 0) > (e.get("estimate") or 0))
            return beats / len(data)
        except Exception:
            return 0.5

    # ─────────────────────────────────────────────
    # 内部人买入事件（SEC Form 4）
    # ─────────────────────────────────────────────
    def get_insider_buying_events(self, symbols: list[str]) -> list[CatalystEvent]:
        """通过SEC EDGAR获取近期内部人净买入记录"""
        events = []
        today = datetime.today()
        cutoff = today - timedelta(days=30)

        for symbol in symbols:
            try:
                # 获取公司CIK
                cik = self._get_cik(symbol)
                if not cik:
                    continue

                filings = self._fetch_form4(cik)
                net_buy_shares = 0
                latest_date = None

                for filing in filings:
                    filing_date = filing.get("date")
                    if not filing_date:
                        continue
                    fd = datetime.strptime(filing_date, "%Y-%m-%d")
                    if fd < cutoff:
                        continue

                    shares = filing.get("shares", 0)
                    txn_type = filing.get("type", "")
                    if txn_type == "P":   # Purchase
                        net_buy_shares += shares
                        if not latest_date or fd > latest_date:
                            latest_date = fd
                    elif txn_type == "S":  # Sale
                        net_buy_shares -= shares

                if net_buy_shares > 0 and latest_date:
                    days_since = (today - latest_date).days
                    if days_since <= 7:  # 7天内有内部人净买入
                        events.append(CatalystEvent(
                            symbol=symbol,
                            event_type="insider_buy",
                            event_date=latest_date,
                            days_until_event=-days_since,
                            description=f"内部人净买入 {net_buy_shares:,} 股（{days_since}天前）",
                            confidence=0.72,
                            expected_move_pct=8.0,
                        ))
            except Exception as e:
                print(f"  获取 {symbol} 内部人数据出错: {e}")

        return events

    def _get_cik(self, symbol: str) -> Optional[str]:
        try:
            resp = requests.get(
                "https://efts.sec.gov/LATEST/search-index?q=%22" + symbol + "%22&dateRange=custom&startdt=2020-01-01&forms=4",
                headers=self.headers, timeout=10
            )
            # 简化：直接用ticker->CIK映射端点
            resp2 = requests.get(
                f"https://data.sec.gov/submissions/CIK{symbol.upper()}.json",
                headers=self.headers, timeout=10
            )
            if resp2.status_code == 200:
                return resp2.json().get("cik")
        except Exception:
            pass
        return None

    def _fetch_form4(self, cik: str) -> list:
        """从SEC EDGAR获取Form 4内部人交易申报"""
        try:
            url = f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json"
            resp = requests.get(url, headers=self.headers, timeout=15)
            if resp.status_code != 200:
                return []

            data = resp.json()
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])

            results = []
            for i, form in enumerate(forms):
                if form == "4":
                    results.append({"date": dates[i], "type": "P", "shares": 0})

            return results[:20]
        except Exception:
            return []

    # ─────────────────────────────────────────────
    # 综合扫描
    # ─────────────────────────────────────────────
    def scan_events(self, symbols: list[str]) -> list[CatalystEvent]:
        """扫描所有事件类型，返回按置信度排序的事件列表"""
        all_events = []
        print("📅 扫描财报日历...")
        all_events.extend(self.get_earnings_events(symbols))
        print("👔 扫描内部人买入...")
        all_events.extend(self.get_insider_buying_events(symbols))

        all_events.sort(key=lambda e: e.confidence, reverse=True)
        return all_events

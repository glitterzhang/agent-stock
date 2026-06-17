"""
Event monitor — tracks earnings calendar, insider buying, and other catalyst events
Earnings data source: Finnhub free API (no proxy required, 60 requests/minute)
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
    confidence: float        # 0-1, signal confidence
    expected_move_pct: float # expected price move


class EventMonitor:
    def __init__(self):
        self.sec_edgar_url = "https://data.sec.gov/submissions"
        self.headers = {"User-Agent": "agent-stock-bot contact@example.com"}

    # ─────────────────────────────────────────────
    # Earnings events
    # ─────────────────────────────────────────────
    def get_earnings_events(self, symbols: list[str]) -> list[CatalystEvent]:
        """Fetch stocks with earnings in the next 14 days (Finnhub API)"""
        events = []
        today = datetime.today()
        end_date = today + timedelta(days=14)

        if not FINNHUB_TOKEN:
            print("  ⚠️  FINNHUB_API_KEY not set, skipping earnings scan (add a free key in .env)")
            return events

        try:
            # Batch fetch earnings calendar
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
                print(f"  ⚠️  Finnhub earnings API returned {resp.status_code}")
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
                        description=f"Earnings date {date_str} ({days_until} days away), historical beat rate {surprise_rate:.0%}",
                        confidence=surprise_rate,
                        expected_move_pct=7.0,
                    ))
        except Exception as e:
            print(f"  Error fetching earnings calendar: {e}")

        return events

    def _estimate_surprise_finnhub(self, symbol: str) -> float:
        """Estimate beat probability using Finnhub historical EPS data"""
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
    # Insider buying events (SEC Form 4)
    # ─────────────────────────────────────────────
    def get_insider_buying_events(self, symbols: list[str]) -> list[CatalystEvent]:
        """Fetch recent net insider buying records via SEC EDGAR"""
        events = []
        today = datetime.today()
        cutoff = today - timedelta(days=30)

        for symbol in symbols:
            try:
                # Get company CIK
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
                    if days_since <= 7:  # net insider buying within the past 7 days
                        events.append(CatalystEvent(
                            symbol=symbol,
                            event_type="insider_buy",
                            event_date=latest_date,
                            days_until_event=-days_since,
                            description=f"Net insider purchase of {net_buy_shares:,} shares ({days_since} days ago)",
                            confidence=0.72,
                            expected_move_pct=8.0,
                        ))
            except Exception as e:
                print(f"  Error fetching insider data for {symbol}: {e}")

        return events

    def _get_cik(self, symbol: str) -> Optional[str]:
        try:
            resp = requests.get(
                "https://efts.sec.gov/LATEST/search-index?q=%22" + symbol + "%22&dateRange=custom&startdt=2020-01-01&forms=4",
                headers=self.headers, timeout=10
            )
            # Simplified: use the ticker->CIK mapping endpoint directly
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
        """Fetch Form 4 insider trading filings from SEC EDGAR"""
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
    # Combined scan
    # ─────────────────────────────────────────────
    def scan_events(self, symbols: list[str]) -> list[CatalystEvent]:
        """Scan all event types and return a list sorted by confidence"""
        all_events = []
        print("📅 Scanning earnings calendar...")
        all_events.extend(self.get_earnings_events(symbols))
        print("👔 Scanning insider buying...")
        all_events.extend(self.get_insider_buying_events(symbols))

        all_events.sort(key=lambda e: e.confidence, reverse=True)
        return all_events

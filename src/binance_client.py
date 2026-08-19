"""Public REST client for Binance USD-M Futures. No API key."""

from __future__ import annotations

import time
from typing import Any

import requests

from config import get
from src.utils import safe_float


class BinanceRESTError(RuntimeError):
    pass


class BinanceClient:
    def __init__(self):
        self.base = get("binance.rest_base", "https://fapi.binance.com")
        self.timeout = float(get("binance.request_timeout_s", 12))
        self.retries = int(get("binance.rest_retries", 3))
        self.backoff = float(get("binance.rest_backoff_s", 0.6))
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "gt-dashboard/1.0"})

    def close(self) -> None:
        self.session.close()

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = self.base + path
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429:
                    wait = float(r.headers.get("Retry-After", self.backoff * (attempt + 2)))
                    time.sleep(wait)
                    continue
                if r.status_code >= 400:
                    raise BinanceRESTError(f"HTTP {r.status_code} {path} {r.text[:240]}")
                return r.json()
            except (requests.RequestException, ValueError, BinanceRESTError) as exc:
                last_exc = exc
                time.sleep(self.backoff * (attempt + 1))
        raise BinanceRESTError(f"REST failed {path}: {last_exc}")

    def ping(self) -> bool:
        self._get("/fapi/v1/ping")
        return True

    def exchange_symbol(self, symbol: str) -> dict:
        info = self._get("/fapi/v1/exchangeInfo", {"symbol": symbol.upper()})
        symbols = info.get("symbols") or []
        if not symbols:
            raise BinanceRESTError(f"Unknown symbol {symbol}")
        return symbols[0]

    def describe_symbol(self, symbol: str) -> dict:
        s = self.exchange_symbol(symbol)
        tick = 0.01
        for f in s.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                tick = safe_float(f.get("tickSize"), 0.01)
        if s.get("status") != "TRADING":
            raise BinanceRESTError(f"{symbol} status is {s.get('status')}, not TRADING")
        if s.get("contractType") not in (None, "PERPETUAL"):
            # still allow, but label
            pass
        return {
            "symbol": s.get("symbol"),
            "pair": s.get("pair"),
            "contractType": s.get("contractType"),
            "status": s.get("status"),
            "pricePrecision": s.get("pricePrecision"),
            "quantityPrecision": s.get("quantityPrecision"),
            "tickSize": tick,
            "marginAsset": s.get("marginAsset"),
            "quoteAsset": s.get("quoteAsset"),
        }

    def klines(self, symbol: str, interval: str, limit: int = 300) -> list[dict]:
        raw = self._get(
            "/fapi/v1/klines",
            {"symbol": symbol.upper(), "interval": interval, "limit": limit},
        )
        out = []
        for c in raw:
            out.append(
                {
                    "open_time": int(c[0]),
                    "open": safe_float(c[1]),
                    "high": safe_float(c[2]),
                    "low": safe_float(c[3]),
                    "close": safe_float(c[4]),
                    "volume": safe_float(c[5]),
                    "close_time": int(c[6]),
                    "quote_volume": safe_float(c[7]),
                    "trades": int(c[8]),
                    "taker_buy_base": safe_float(c[9]),
                    "interval": interval,
                    "closed": True,
                }
            )
        return out

    def ticker_24h(self, symbol: str) -> dict:
        d = self._get("/fapi/v1/ticker/24hr", {"symbol": symbol.upper()})
        return {
            "last": safe_float(d.get("lastPrice")),
            "open": safe_float(d.get("openPrice")),
            "high": safe_float(d.get("highPrice")),
            "low": safe_float(d.get("lowPrice")),
            "change_pct": safe_float(d.get("priceChangePercent")),
            "quote_volume": safe_float(d.get("quoteVolume")),
            "ts": int(d.get("closeTime") or 0),
        }

    def premium_index(self, symbol: str) -> dict:
        d = self._get("/fapi/v1/premiumIndex", {"symbol": symbol.upper()})
        return {
            "mark": safe_float(d.get("markPrice")),
            "index": safe_float(d.get("indexPrice")),
            "last_funding": safe_float(d.get("lastFundingRate")),
            "next_funding_time": int(d.get("nextFundingTime") or 0),
            "ts": int(d.get("time") or 0),
        }

    def funding_history(self, symbol: str, limit: int = 200) -> list[dict]:
        raw = self._get(
            "/fapi/v1/fundingRate",
            {"symbol": symbol.upper(), "limit": limit},
        )
        return [
            {
                "funding_time": int(x.get("fundingTime") or 0),
                "funding_rate": safe_float(x.get("fundingRate")),
                "mark": safe_float(x.get("markPrice")),
            }
            for x in raw
        ]

    def open_interest(self, symbol: str) -> dict:
        d = self._get("/fapi/v1/openInterest", {"symbol": symbol.upper()})
        return {
            "oi": safe_float(d.get("openInterest")),
            "ts": int(d.get("time") or 0),
        }

    def open_interest_hist(self, symbol: str, period: str = "5m", limit: int = 50) -> list[dict]:
        raw = self._get(
            "/futures/data/openInterestHist",
            {"symbol": symbol.upper(), "period": period, "limit": limit},
        )
        return [
            {
                "oi": safe_float(x.get("sumOpenInterest")),
                "oi_value": safe_float(x.get("sumOpenInterestValue")),
                "ts": int(x.get("timestamp") or 0),
            }
            for x in raw
        ]

    def global_ls_ratio(self, symbol: str, period: str = "5m", limit: int = 30) -> list[dict]:
        raw = self._get(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol.upper(), "period": period, "limit": limit},
        )
        return [
            {
                "long_account": safe_float(x.get("longAccount")),
                "short_account": safe_float(x.get("shortAccount")),
                "ls_ratio": safe_float(x.get("longShortRatio")),
                "ts": int(x.get("timestamp") or 0),
            }
            for x in raw
        ]

    def top_ls_position(self, symbol: str, period: str = "5m", limit: int = 30) -> list[dict]:
        raw = self._get(
            "/futures/data/topLongShortPositionRatio",
            {"symbol": symbol.upper(), "period": period, "limit": limit},
        )
        return [
            {
                "long_account": safe_float(x.get("longAccount")),
                "short_account": safe_float(x.get("shortAccount")),
                "ls_ratio": safe_float(x.get("longShortRatio")),
                "ts": int(x.get("timestamp") or 0),
            }
            for x in raw
        ]

    def taker_ls(self, symbol: str, period: str = "5m", limit: int = 30) -> list[dict]:
        raw = self._get(
            "/futures/data/takerlongshortRatio",
            {"symbol": symbol.upper(), "period": period, "limit": limit},
        )
        return [
            {
                "buy_vol": safe_float(x.get("buyVol")),
                "sell_vol": safe_float(x.get("sellVol")),
                "buy_sell_ratio": safe_float(x.get("buySellRatio")),
                "ts": int(x.get("timestamp") or 0),
            }
            for x in raw
        ]

    def agg_trades(self, symbol: str, limit: int = 500) -> list[dict]:
        raw = self._get("/fapi/v1/aggTrades", {"symbol": symbol.upper(), "limit": limit})
        out = []
        for t in raw:
            is_buyer_maker = bool(t.get("m"))
            qty = safe_float(t.get("q"))
            px = safe_float(t.get("p"))
            out.append(
                {
                    "ts": int(t.get("T") or 0),
                    "price": px,
                    "qty": qty,
                    "is_buyer_maker": is_buyer_maker,
                    "side": "sell" if is_buyer_maker else "buy",
                    "id": t.get("a"),
                }
            )
        return out

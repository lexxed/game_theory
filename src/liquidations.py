"""
Observed Binance force-order (liquidation) events.

Public REST historical liquidation endpoints are no longer available without a key
(/fapi/v1/forceOrders → 401, /fapi/v1/allForceOrders → 404).

We subscribe to the public !forceOrder@arr stream and filter by symbol.
Binance publishes at most the latest liquidation per symbol per ~1000ms window
— this is an incomplete sample, not a full liquidation tape.
"""

from __future__ import annotations

from src.utils import Ring, now_ms, safe_float


class LiquidationTracker:
    def __init__(self, maxlen: int = 3000):
        self.events = Ring(maxlen)
        self.last_ts = 0
        self.socket_ok = False
        self.limitation = (
            "OBSERVED only. Binance does not publish a hidden liquidation-price map. "
            "REST force-order history requires an API key. Stream is sampled (~1 event/symbol/sec)."
        )

    def reset(self) -> None:
        self.events.clear()
        self.last_ts = 0

    def ingest_ws(self, data: dict, symbol: str) -> dict | None:
        o = data.get("o") or data
        if not isinstance(o, dict):
            return None
        if str(o.get("s", "")).upper() != symbol.upper():
            return None
        side = str(o.get("S", "")).upper()  # SELL = longs liquidated
        qty = safe_float(o.get("z") or o.get("q"))
        price = safe_float(o.get("ap") or o.get("p"))
        ts = int(o.get("T") or data.get("E") or now_ms())
        if qty <= 0 or price <= 0:
            return None
        rec = {
            "ts": ts,
            "side": side,
            "liq_of": "long" if side == "SELL" else "short",
            "qty": qty,
            "price": price,
            "notional": qty * price,
        }
        self.events.append(rec)
        self.last_ts = ts
        return rec

    def window(self, lookback_ms: int, liq_of: str | None = None) -> list[dict]:
        end = now_ms()
        start = end - lookback_ms
        out = []
        for e in self.events:
            if e["ts"] < start:
                continue
            if liq_of and e["liq_of"] != liq_of:
                continue
            out.append(e)
        return out

    def stats(self, lookback_ms: int) -> dict:
        ev = self.window(lookback_ms)
        long_n = [e for e in ev if e["liq_of"] == "long"]
        short_n = [e for e in ev if e["liq_of"] == "short"]
        return {
            "n": len(ev),
            "long_n": len(long_n),
            "short_n": len(short_n),
            "long_qty": sum(e["qty"] for e in long_n),
            "short_qty": sum(e["qty"] for e in short_n),
            "long_notional": sum(e["notional"] for e in long_n),
            "short_notional": sum(e["notional"] for e in short_n),
        }

    def volume_by_price(self, lookback_ms: int, tick: float) -> list[dict]:
        from collections import defaultdict
        from src.utils import bucket_price

        acc: dict[tuple, list] = defaultdict(lambda: [0.0, 0.0])
        for e in self.window(lookback_ms):
            px = bucket_price(e["price"], tick)
            if e["liq_of"] == "long":
                acc[px][0] += e["notional"]
            else:
                acc[px][1] += e["notional"]
        rows = [
            {"price": px, "long_notional": v[0], "short_notional": v[1]}
            for px, v in acc.items()
        ]
        rows.sort(key=lambda r: r["price"], reverse=True)
        return rows

    def snapshot(self) -> dict:
        s1 = self.stats(60_000)
        s5 = self.stats(5 * 60_000)
        s15 = self.stats(15 * 60_000)
        return {
            "last_ts": self.last_ts,
            "m1": s1,
            "m5": s5,
            "m15": s15,
            "limitation": self.limitation,
        }

"""Aggressive trade tape. Binance aggTrade: m=true ⇒ buyer is maker ⇒ sell aggressor."""

from __future__ import annotations

from src.utils import Ring, now_ms, safe_float


class TradeTape:
    def __init__(self, maxlen: int = 25000):
        self.trades = Ring(maxlen)
        self.last_ts = 0
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self.buy_notional = 0.0
        self.sell_notional = 0.0
        self.last_price = 0.0
        self.last_side = ""

    def reset(self) -> None:
        self.trades.clear()
        self.buy_vol = self.sell_vol = 0.0
        self.buy_notional = self.sell_notional = 0.0
        self.last_ts = 0

    def ingest_ws(self, data: dict) -> dict | None:
        # Combined and raw both use aggTrade fields p,q,m,T
        try:
            px = safe_float(data.get("p"))
            qty = safe_float(data.get("q"))
            ts = int(data.get("T") or data.get("E") or now_ms())
            is_buyer_maker = bool(data.get("m"))
        except (TypeError, ValueError):
            return None
        if px <= 0 or qty <= 0:
            return None
        return self._push(ts, px, qty, is_buyer_maker)

    def ingest_rest(self, trade: dict) -> dict | None:
        return self._push(
            int(trade["ts"]),
            float(trade["price"]),
            float(trade["qty"]),
            bool(trade["is_buyer_maker"]),
        )

    def _push(self, ts: int, px: float, qty: float, is_buyer_maker: bool) -> dict:
        side = "sell" if is_buyer_maker else "buy"
        notional = px * qty
        rec = {
            "ts": ts,
            "price": px,
            "qty": qty,
            "notional": notional,
            "side": side,
            "is_buyer_maker": is_buyer_maker,
        }
        self.trades.append(rec)
        self.last_ts = ts
        self.last_price = px
        self.last_side = side
        if side == "buy":
            self.buy_vol += qty
            self.buy_notional += notional
        else:
            self.sell_vol += qty
            self.sell_notional += notional
        return rec

    def window(self, start_ms: int, end_ms: int | None = None) -> list[dict]:
        end = end_ms if end_ms is not None else (self.last_ts or now_ms())
        return [t for t in self.trades if start_ms <= t["ts"] <= end]

    def window_stats(self, lookback_ms: int) -> dict:
        end = self.last_ts or now_ms()
        start = end - lookback_ms
        buy = sell = buy_n = sell_n = 0.0
        n = 0
        first_px = last_px = None
        for t in self.trades:
            if t["ts"] < start:
                continue
            n += 1
            if first_px is None:
                first_px = t["price"]
            last_px = t["price"]
            if t["side"] == "buy":
                buy += t["qty"]
                buy_n += t["notional"]
            else:
                sell += t["qty"]
                sell_n += t["notional"]
        return {
            "n": n,
            "buy_vol": buy,
            "sell_vol": sell,
            "buy_notional": buy_n,
            "sell_notional": sell_n,
            "delta": buy - sell,
            "delta_notional": buy_n - sell_n,
            "volume": buy + sell,
            "first_px": first_px,
            "last_px": last_px,
        }

    def taker_ratio(self, lookback_ms: int, label: str = "") -> dict:
        """
        Rolling taker buy/sell volume ratio over the last `lookback_ms`.
        Complementary to CVD: CVD is a running cumulative total, this is a
        bounded relative measure of how one-sided RECENT flow is, independent
        of how large the absolute move has been. ratio in [-1, 1]:
        +1 = all buy-side aggression, -1 = all sell-side aggression, 0 = even.
        """
        st = self.window_stats(lookback_ms)
        buy, sell = st["buy_vol"], st["sell_vol"]
        total = buy + sell
        ratio = (buy - sell) / total if total > 0 else 0.0
        lbl = label or f"{lookback_ms // 60_000}m"
        return {
            "ratio": round(ratio, 4),
            "buy_vol": buy,
            "sell_vol": sell,
            "n": st["n"],
            "reason": (
                f"taker flow {lbl}: buy {buy:,.3f} vs sell {sell:,.3f} "
                f"(ratio {ratio:+.2f}) — relative one-sidedness of recent aggressive "
                f"volume, not a cumulative total like CVD, and not proof of net "
                f"positioning by itself"
            ),
        }

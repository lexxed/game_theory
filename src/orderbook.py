"""
Order book depth tracker. Public partial-depth stream only
(<symbol>@depth20@100ms) — resting liquidity, not executed volume.

IMPORTANT CAVEAT (shows up in every reason string below): resting limit
orders can be cancelled or replaced at any time before they trade. A "thin
ask wall" means little size is CURRENTLY resting above price, not that a
seller is unable or unwilling to add more. This is a snapshot of displayed
liquidity, not a guarantee of what will happen when price gets there.
"""

from __future__ import annotations

from src.utils import safe_float


class OrderBookTracker:
    def __init__(self):
        self.bids: dict[float, float] = {}  # price -> qty
        self.asks: dict[float, float] = {}
        self.last_update_id = 0
        self.last_ts = 0
        self.source = "ws:<symbol>@depth20@100ms"

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.last_update_id = 0
        self.last_ts = 0

    def on_depth(self, data: dict) -> None:
        """
        Binance Futures partial book depth payload:
        {"e":"depthUpdate","E":..,"T":..,"s":"BTCUSDT","U":..,"u":..,
         "b":[["px","qty"], ...], "a":[["px","qty"], ...]}
        For the @depth20 partial-book stream, each message is a fresh top-20
        snapshot (not a diff to apply), so we simply replace the book.
        """
        b = data.get("b") or data.get("bids") or []
        a = data.get("a") or data.get("asks") or []
        new_bids: dict[float, float] = {}
        new_asks: dict[float, float] = {}
        for px, qty in b:
            p, q = safe_float(px), safe_float(qty)
            if q > 0:
                new_bids[p] = q
        for px, qty in a:
            p, q = safe_float(px), safe_float(qty)
            if q > 0:
                new_asks[p] = q
        if not new_bids and not new_asks:
            return
        self.bids = new_bids
        self.asks = new_asks
        self.last_update_id = int(data.get("u") or data.get("lastUpdateId") or self.last_update_id)
        self.last_ts = int(data.get("E") or data.get("T") or self.last_ts)

    @property
    def best_bid(self) -> float:
        return max(self.bids) if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return min(self.asks) if self.asks else 0.0

    def imbalance(self, price_range_pct: float = 0.5, thin_wall_ratio: float = 0.20) -> dict:
        bb, ba = self.best_bid, self.best_ask
        if not bb or not ba:
            return {
                "ready": False,
                "imbalance_ratio": 0.0,
                "bid_notional": 0.0,
                "ask_notional": 0.0,
                "thin_ask_wall_above": False,
                "thin_bid_wall_below": False,
                "reason": "order book not yet populated",
            }

        bid_floor = bb * (1 - price_range_pct / 100.0)
        ask_ceiling = ba * (1 + price_range_pct / 100.0)

        bid_notional = sum(p * q for p, q in self.bids.items() if p >= bid_floor)
        ask_notional = sum(p * q for p, q in self.asks.items() if p <= ask_ceiling)

        total = bid_notional + ask_notional
        imbalance_ratio = (bid_notional - ask_notional) / total if total > 0 else 0.0

        thin_ask_wall_above = ask_notional > 0 and bid_notional > 0 and (ask_notional / bid_notional) < thin_wall_ratio
        thin_bid_wall_below = bid_notional > 0 and ask_notional > 0 and (bid_notional / ask_notional) < thin_wall_ratio
        # also treat a genuinely empty opposing side within the band as "thin"
        if ask_notional == 0 and bid_notional > 0:
            thin_ask_wall_above = True
        if bid_notional == 0 and ask_notional > 0:
            thin_bid_wall_below = True

        reason = (
            f"book imbalance {imbalance_ratio:+.2f} in \u00b1{price_range_pct:g}% band around "
            f"best bid/ask: bid notional ${bid_notional:,.0f} vs ask notional ${ask_notional:,.0f} "
            f"({'thin ask wall — little resting size above price' if thin_ask_wall_above else 'ask side not thin'}; "
            f"{'thin bid wall — little resting size below price' if thin_bid_wall_below else 'bid side not thin'}) "
            f"— snapshot of resting orders only, not proof of intent or that liquidity stays put"
        )

        return {
            "ready": True,
            "imbalance_ratio": round(imbalance_ratio, 4),
            "bid_notional": round(bid_notional, 2),
            "ask_notional": round(ask_notional, 2),
            "thin_ask_wall_above": bool(thin_ask_wall_above),
            "thin_bid_wall_below": bool(thin_bid_wall_below),
            "best_bid": bb,
            "best_ask": ba,
            "ts": self.last_ts,
            "source": self.source,
            "reason": reason,
        }

"""Tick-aware footprint: bid/ask volume at price for the current interval."""

from __future__ import annotations

from collections import defaultdict

from src.utils import KLINE_INTERVALS, bar_open_ms, bucket_price, now_ms, tf_ms


class FootprintEngine:
    def __init__(self, tick_size: float = 0.01, tick_mult: int = 1, keep_bars: int = 48):
        self.tick_size = max(tick_size, 1e-12)
        self.tick_mult = max(int(tick_mult), 1)
        self.keep_bars = keep_bars
        self.bucket = self.tick_size * self.tick_mult
        # tf -> {open_time: {price: {bid, ask}}}
        self.books: dict[str, dict[int, dict[float, dict[str, float]]]] = {
            k: {} for k in KLINE_INTERVALS
        }

    def reset(self, tick_size: float | None = None) -> None:
        # Keep the active symbol tick unless a new one is supplied.
        # Do not silently fall back to the constructor default 0.01.
        if tick_size is not None and float(tick_size) > 0:
            self.tick_size = max(float(tick_size), 1e-12)
        self.bucket = self.tick_size * self.tick_mult
        for k in self.books:
            self.books[k] = {}

    def set_tick(self, tick_size: float, tick_mult: int | None = None) -> None:
        self.tick_size = max(float(tick_size), 1e-12)
        if tick_mult is not None:
            self.tick_mult = max(int(tick_mult), 1)
        self.bucket = self.tick_size * self.tick_mult

    def meta(self) -> dict:
        return {
            "tick_size": self.tick_size,
            "tick_multiplier": self.tick_mult,
            "bucket": self.bucket,
        }

    def on_trade(self, trade: dict) -> None:
        px = bucket_price(trade["price"], self.bucket)
        ts = int(trade["ts"])
        qty = float(trade["qty"])
        key = "bid" if trade["side"] == "sell" else "ask"  # bid=passive buy / sell aggressor
        for tf, store in self.books.items():
            ot = bar_open_ms(ts, tf)
            bar = store.setdefault(ot, {})
            lvl = bar.setdefault(px, {"bid": 0.0, "ask": 0.0})
            lvl[key] += qty
            # prune old bars
            if len(store) > self.keep_bars + 4:
                for old in sorted(store)[: -self.keep_bars]:
                    store.pop(old, None)

    def levels(self, tf: str, bar_open: int | None = None, max_levels: int = 40) -> list[dict]:
        store = self.books.get(tf, {})
        if not store:
            return []
        ot = bar_open if bar_open is not None else max(store)
        bar = store.get(ot, {})
        rows = []
        for px, v in bar.items():
            bid, ask = v["bid"], v["ask"]
            rows.append(
                {
                    "price": px,
                    "bid": bid,
                    "ask": ask,
                    "delta": ask - bid,
                    "total": bid + ask,
                    "bar_open": ot,
                    "bucket": self.bucket,
                    "tick_size": self.tick_size,
                }
            )
        rows.sort(key=lambda r: r["price"], reverse=True)
        if len(rows) > max_levels:
            # keep highest total-volume levels, then re-sort by price
            rows = sorted(rows, key=lambda r: r["total"], reverse=True)[:max_levels]
            rows.sort(key=lambda r: r["price"], reverse=True)
        return rows

    def bar_totals(self, tf: str, bar_open: int | None = None) -> dict:
        rows = self.levels(tf, bar_open, max_levels=10_000)
        bid = sum(r["bid"] for r in rows)
        ask = sum(r["ask"] for r in rows)
        return {"bid": bid, "ask": ask, "delta": ask - bid, "total": bid + ask, "n": len(rows)}

    def stacked_imbalance(self, rows: list[dict], ratio: float, min_levels: int) -> dict:
        """Consecutive ask-dominant or bid-dominant levels."""
        ask_run = bid_run = 0
        best_ask = best_bid = 0
        for r in rows:
            if r["bid"] > 0 and r["ask"] / r["bid"] >= ratio:
                ask_run += 1
                bid_run = 0
            elif r["ask"] > 0 and r["bid"] / r["ask"] >= ratio:
                bid_run += 1
                ask_run = 0
            else:
                ask_run = bid_run = 0
            best_ask = max(best_ask, ask_run)
            best_bid = max(best_bid, bid_run)
        return {
            "ask_stack": best_ask if best_ask >= min_levels else 0,
            "bid_stack": best_bid if best_bid >= min_levels else 0,
        }

    def detect_absorption(
        self,
        tf: str,
        price_progress: float,
        atr: float,
        cfg: dict,
    ) -> dict:
        """
        Buy absorption: large +delta (aggressive buying) + small upward progress.
        Sell absorption: large -delta + small downward progress.
        """
        tot = self.bar_totals(tf)
        delta = tot["delta"]
        vol = tot["total"]
        min_frac = float(cfg.get("min_delta_frac_of_volume", 0.18))
        max_atr = float(cfg.get("max_progress_atr", 0.35))
        imb_frac = float(cfg.get("large_imbalance_frac", 0.30))
        reason = "insufficient footprint volume"
        buy_abs = sell_abs = False
        imb = False
        strength = 0.0
        if vol <= 0 or atr <= 0:
            return {
                "buy_absorption": False,
                "sell_absorption": False,
                "imbalance": False,
                "delta_exhaustion": False,
                "strength": 0.0,
                "delta": delta,
                "volume": vol,
                "reason": reason,
            }
        progress_atr = abs(price_progress) / atr
        delta_frac = abs(delta) / vol
        imb = delta_frac >= imb_frac
        if delta > 0 and delta_frac >= min_frac and progress_atr <= max_atr and price_progress <= atr * max_atr:
            buy_abs = True
            strength = min(1.0, delta_frac / min_frac * 0.5 + (1 - progress_atr / max(max_atr, 1e-9)) * 0.5)
            reason = (
                f"buy absorption: aggressive ask {delta:.4g} "
                f"({delta_frac:.0%} of volume) but price progress {price_progress:.6g} "
                f"is only {progress_atr:.2f} ATR"
            )
        elif delta < 0 and delta_frac >= min_frac and progress_atr <= max_atr and price_progress >= -atr * 5:
            # sell absorption if selling hard and not breaking down much
            if price_progress >= -atr * max_atr:
                sell_abs = True
                strength = min(1.0, delta_frac / min_frac * 0.5 + (1 - progress_atr / max(max_atr, 1e-9)) * 0.5)
                reason = (
                    f"sell absorption: aggressive bid {delta:.4g} "
                    f"({delta_frac:.0%} of volume) but price progress {price_progress:.6g} "
                    f"is only {progress_atr:.2f} ATR"
                )
            else:
                reason = "large negative delta with real downside — not absorption"
        else:
            reason = (
                f"no absorption: delta_frac={delta_frac:.2f} progress/ATR={progress_atr:.2f}"
            )
        exhaustion = imb and progress_atr < 0.15
        return {
            "buy_absorption": buy_abs,
            "sell_absorption": sell_abs,
            "imbalance": imb,
            "delta_exhaustion": exhaustion,
            "strength": strength,
            "delta": delta,
            "volume": vol,
            "reason": reason,
        }

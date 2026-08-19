"""
Cumulative Volume Delta from aggressive tape.

CVD(t) = CVD(t-1) + (buy_qty - sell_qty)

Divergence is quantitative, not visual:
  bearish: last swing HIGH of price > prior swing HIGH by min_price_hh_pct
           AND last swing HIGH of CVD <= prior swing HIGH of CVD
  bullish: last swing LOW of price  < prior swing LOW  by min_price_ll_pct
           AND last swing LOW of CVD  >= prior swing LOW of CVD
"""

from __future__ import annotations

from src.utils import KLINE_INTERVALS, Ring, linear_slope, local_extrema, normalize_interval, now_ms, pct_change, tf_ms


def _kline_delta(bar: dict) -> float:
    """Aggressive buy - sell from Binance kline taker-buy volume."""
    vol = float(bar.get("volume") or 0.0)
    buy = float(bar.get("taker_buy_base") or 0.0)
    if vol < 0:
        vol = 0.0
    if buy < 0:
        buy = 0.0
    if buy > vol:
        buy = vol
    return buy - (vol - buy)


class CVDEngine:
    def __init__(self, maxlen: int = 4000):
        self.cvd = 0.0
        self.points = Ring(maxlen)  # {ts, cvd, price, delta}
        self.by_tf: dict[str, list[dict]] = {k: [] for k in KLINE_INTERVALS}
        self.last_ts = 0
        # Trades at or before this ts are already inside kline-backfilled bars.
        self.live_after_ts = 0

    def reset(self) -> None:
        self.cvd = 0.0
        self.points.clear()
        self.by_tf = {k: [] for k in KLINE_INTERVALS}
        self.last_ts = 0
        self.live_after_ts = 0

    def mark_live(self, ts: int | None = None) -> None:
        self.live_after_ts = int(ts if ts is not None else now_ms())

    def seed_from_klines(self, tf: str, bars: list[dict]) -> None:
        """
        Rebuild one timeframe's CVD from kline taker-buy volume.

        delta = taker_buy_base - (volume - taker_buy_base)
        This is Binance's published aggressive-buy split, not a full tape replay.
        """
        tf = normalize_interval(tf)
        rows: list[dict] = []
        cvd = 0.0
        for b in sorted(bars, key=lambda x: int(x["open_time"])):
            delta = _kline_delta(b)
            cvd += delta
            rows.append(
                {
                    "open_time": int(b["open_time"]),
                    "cvd": cvd,
                    "delta": delta,
                    "open_px": float(b.get("open") or 0.0),
                    "close_px": float(b.get("close") or 0.0),
                    "high_px": float(b.get("high") or 0.0),
                    "low_px": float(b.get("low") or 0.0),
                    "source": "kline_taker_buy",
                }
            )
        self.by_tf[tf] = rows[-400:]

    def last_for(self, tf: str) -> float:
        series = self.series(tf)
        if series:
            return float(series[-1]["cvd"])
        return self.cvd

    def on_trade(self, trade: dict) -> None:
        delta = trade["qty"] if trade["side"] == "buy" else -trade["qty"]
        self.cvd += delta
        ts = int(trade["ts"])
        self.last_ts = ts
        rec = {"ts": ts, "cvd": self.cvd, "price": trade["price"], "delta": delta}
        self.points.append(rec)
        if ts <= self.live_after_ts:
            return
        for tf in list(self.by_tf):
            self._update_tf(tf, rec)

    def _update_tf(self, tf: str, rec: dict) -> None:
        """Add this trade's delta onto the TF series (does not replace kline CVD)."""
        step = tf_ms(tf)
        open_t = rec["ts"] - (rec["ts"] % step)
        bars = self.by_tf[tf]
        if bars and bars[-1]["open_time"] == open_t:
            b = bars[-1]
            b["cvd"] += rec["delta"]
            b["delta"] += rec["delta"]
            if rec.get("price"):
                b["close_px"] = rec["price"]
                b["high_px"] = max(b["high_px"], rec["price"])
                b["low_px"] = min(b["low_px"], rec["price"]) if b["low_px"] else rec["price"]
            return
        prev = bars[-1]["cvd"] if bars else 0.0
        bars.append(
            {
                "open_time": open_t,
                "cvd": prev + rec["delta"],
                "delta": rec["delta"],
                "open_px": rec.get("price") or 0.0,
                "close_px": rec.get("price") or 0.0,
                "high_px": rec.get("price") or 0.0,
                "low_px": rec.get("price") or 0.0,
            }
        )
        if len(bars) > 400:
            del bars[: len(bars) - 400]

    def change(self, lookback_ms: int) -> float:
        """CVD now minus CVD at/just before (now - lookback)."""
        pts = self.points.snapshot()
        if not pts:
            return 0.0
        cutoff = (self.last_ts or now_ms()) - lookback_ms
        baseline = pts[0]
        for p in pts:
            if p["ts"] <= cutoff:
                baseline = p
            else:
                break
        return self.cvd - baseline["cvd"]

    def slope(self, n: int = 40) -> float:
        pts = self.points.last(n)
        if not pts:
            return 0.0
        return linear_slope(p["cvd"] for p in pts)

    def series(self, tf: str) -> list[dict]:
        tf = normalize_interval(tf)
        if tf not in self.by_tf:
            self.by_tf[tf] = []
        return list(self.by_tf.get(tf, []))

    def change_from_series(self, tf: str, lookback_ms: int) -> float:
        bars = self.series(tf)
        if len(bars) < 2:
            return self.change(lookback_ms)
        end = float(bars[-1]["cvd"])
        cutoff = int(bars[-1]["open_time"]) - lookback_ms
        base = float(bars[0]["cvd"])
        for b in bars:
            if int(b["open_time"]) <= cutoff:
                base = float(b["cvd"])
        return end - base

    def detect_divergence(self, tf: str, cfg: dict) -> dict:
        tf = normalize_interval(tf)
        bars = self.by_tf.get(tf, [])
        lookback = int(cfg.get("swing_lookback", 5))
        min_gap = int(cfg.get("min_swing_gap_bars", 3))
        min_hh = float(cfg.get("min_price_hh_pct", 0.04))
        min_ll = float(cfg.get("min_price_ll_pct", 0.04))
        empty = {
            "bearish": False,
            "bullish": False,
            "bearish_strength": 0.0,
            "bullish_strength": 0.0,
            "reason": "not enough bars for swing detection",
        }
        if len(bars) < lookback * 2 + 3:
            return empty

        prices = [b["close_px"] for b in bars]
        cvds = [b["cvd"] for b in bars]
        hi, lo = local_extrema(prices, lookback)
        if len(hi) < 2 and len(lo) < 2:
            empty["reason"] = "fewer than 2 confirmed swings"
            return empty

        out = dict(empty)
        out["reason"] = "no quantitative divergence"

        if len(hi) >= 2:
            i1, i2 = hi[-2], hi[-1]
            if i2 - i1 >= min_gap:
                p1, p2 = prices[i1], prices[i2]
                c1, c2 = cvds[i1], cvds[i2]
                hh = pct_change(p2, p1)
                if hh >= min_hh and c2 <= c1:
                    fail = abs(c2 - c1)
                    mag = max(abs(c1), 1e-9)
                    strength = min(1.0, (hh / max(min_hh, 1e-9)) * 0.5 + min(1.0, fail / mag) * 0.5)
                    out["bearish"] = True
                    out["bearish_strength"] = strength
                    out["reason"] = (
                        f"bearish CVD div: price HH {hh:.3f}% ({p1:.6g}→{p2:.6g}) "
                        f"but CVD {c1:.4g}→{c2:.4g} (no HH)"
                    )

        if len(lo) >= 2:
            i1, i2 = lo[-2], lo[-1]
            if i2 - i1 >= min_gap:
                p1, p2 = prices[i1], prices[i2]
                c1, c2 = cvds[i1], cvds[i2]
                ll = pct_change(p1, p2)  # positive if p2 lower
                if ll >= min_ll and c2 >= c1:
                    fail = abs(c2 - c1)
                    mag = max(abs(c1), 1e-9)
                    strength = min(1.0, (ll / max(min_ll, 1e-9)) * 0.5 + min(1.0, fail / mag) * 0.5)
                    out["bullish"] = True
                    out["bullish_strength"] = strength
                    extra = (
                        f"bullish CVD div: price LL {ll:.3f}% ({p1:.6g}→{p2:.6g}) "
                        f"but CVD {c1:.4g}→{c2:.4g} (no LL)"
                    )
                    out["reason"] = (out["reason"] + " | " + extra) if out["bearish"] else extra
        return out

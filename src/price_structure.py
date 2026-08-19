"""Local swing structure from seeded + live klines. No lookahead."""

from __future__ import annotations

from src.utils import KLINE_INTERVALS, atr, local_extrema, normalize_interval, pct_change


class PriceStructure:
    def __init__(self):
        self.klines: dict[str, list[dict]] = {k: [] for k in KLINE_INTERVALS}
        self.last_price = 0.0
        self.last_ts = 0

    def reset(self) -> None:
        self.klines = {k: [] for k in KLINE_INTERVALS}
        self.last_price = 0.0

    def seed(self, interval: str, bars: list[dict]) -> None:
        self.klines[interval] = list(bars)
        if bars:
            self.last_price = bars[-1]["close"]
            self.last_ts = bars[-1]["open_time"]

    def on_kline(self, interval: str, k: dict) -> None:
        """k is Binance kline payload k-object or our candle dict."""
        if "t" in k:
            bar = {
                "open_time": int(k["t"]),
                "open": float(k["o"]),
                "high": float(k["h"]),
                "low": float(k["l"]),
                "close": float(k["c"]),
                "volume": float(k["v"]),
                "closed": bool(k.get("x", False)),
                "interval": interval,
            }
        else:
            bar = dict(k)
            bar["interval"] = interval
        bars = self.klines.setdefault(interval, [])
        if bars and bars[-1]["open_time"] == bar["open_time"]:
            bars[-1] = bar
        else:
            bars.append(bar)
            if len(bars) > 500:
                del bars[: len(bars) - 500]
        self.last_price = bar["close"]
        self.last_ts = bar["open_time"]

    def last_n(self, tf: str, n: int = 80) -> list[dict]:
        tf = normalize_interval(tf)
        return self.klines.get(tf, [])[-n:]

    def atr(self, tf: str, n: int = 14) -> float:
        bars = self.last_n(tf, n + 2)
        if len(bars) < 2:
            return 0.0
        return atr(
            [b["high"] for b in bars],
            [b["low"] for b in bars],
            [b["close"] for b in bars],
            n,
        )

    def change_pct(self, tf: str, bars_back: int) -> float:
        bars = self.last_n(tf, bars_back + 1)
        if len(bars) < 2:
            return 0.0
        return pct_change(bars[-1]["close"], bars[0]["open"])

    def analyze(self, tf: str, lookback: int = 5, near_pct: float = 0.15) -> dict:
        bars = self.last_n(tf, 120)
        empty = {
            "near_high": False,
            "near_low": False,
            "lost_support": False,
            "lost_resistance": False,
            "failed_breakout": False,
            "failed_breakdown": False,
            "swing_high": None,
            "swing_low": None,
            "atr": 0.0,
            "reason": "not enough structure bars",
        }
        if len(bars) < lookback * 2 + 5:
            return empty
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        closes = [b["close"] for b in bars]
        px = closes[-1]
        hi_idx, lo_idx = local_extrema(highs, lookback)
        # also treat window max/min as structure if no swings
        swing_high = highs[hi_idx[-1]] if hi_idx else max(highs[:-1])
        swing_low = lows[lo_idx[-1]] if lo_idx else min(lows[:-1])
        a = self.atr(tf)
        near_high = a > 0 and (swing_high - px) / a <= near_pct * 10 or (
            swing_high > 0 and (swing_high - px) / swing_high * 100 <= near_pct
        )
        near_low = swing_low > 0 and (px - swing_low) / swing_low * 100 <= near_pct
        # lost support: last close below last swing low after having been above
        lost_support = False
        if lo_idx:
            sl = lows[lo_idx[-1]]
            if len(closes) > 2 and closes[-3] > sl and closes[-1] < sl:
                lost_support = True
        lost_resistance = False
        if hi_idx:
            sh = highs[hi_idx[-1]]
            if len(closes) > 2 and closes[-3] < sh and closes[-1] > sh:
                lost_resistance = True

        failed_breakout = False
        failed_breakdown = False
        if len(bars) >= 4 and a > 0:
            # broke above prior 10-bar high then closed back inside
            prior_high = max(highs[-12:-2]) if len(highs) >= 12 else max(highs[:-2])
            prior_low = min(lows[-12:-2]) if len(lows) >= 12 else min(lows[:-2])
            if max(highs[-2:]) > prior_high and closes[-1] < prior_high:
                failed_breakout = True
            if min(lows[-2:]) < prior_low and closes[-1] > prior_low:
                failed_breakdown = True

        return {
            "near_high": bool(near_high),
            "near_low": bool(near_low),
            "lost_support": lost_support,
            "lost_resistance": lost_resistance,
            "failed_breakout": failed_breakout,
            "failed_breakdown": failed_breakdown,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "atr": a,
            "price": px,
            "reason": self._reason(
                near_high, near_low, lost_support, lost_resistance, failed_breakout, failed_breakdown
            ),
        }

    @staticmethod
    def _reason(nh, nl, ls, lr, fb, fd) -> str:
        bits = []
        if nh:
            bits.append("price is near local high")
        if nl:
            bits.append("price is near local low")
        if ls:
            bits.append("last swing low (support) was lost")
        if lr:
            bits.append("last swing high (resistance) was broken")
        if fb:
            bits.append("failed breakout (traded above range, closed back inside)")
        if fd:
            bits.append("failed breakdown (traded below range, closed back inside)")
        return "; ".join(bits) if bits else "no notable structure event"

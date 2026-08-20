"""Small numeric / time helpers. No I/O."""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Iterable

import numpy as np


def now_ms() -> int:
    return int(time.time() * 1000)


def now_s() -> float:
    return time.time()


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if x != x:  # NaN
        return 0.0
    return lo if x < lo else hi if x > hi else x


def safe_float(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except (TypeError, ValueError):
        pass
    return default


def pct_change(new: float, old: float) -> float:
    if old == 0 or old != old or new != new:
        return 0.0
    return (new / old - 1.0) * 100.0


def linear_slope(ys: Iterable[float]) -> float:
    """OLS slope of y vs index. Returns 0 if < 2 finite points."""
    arr = np.asarray(list(ys), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = arr.size
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    x = x - x.mean()
    y = arr - arr.mean()
    den = float(np.dot(x, x))
    if den == 0:
        return 0.0
    return float(np.dot(x, y) / den)


def percentile_rank(value: float, history: Iterable[float]) -> float:
    """0-100 rank of value vs history (inclusive of value)."""
    arr = np.asarray(list(history), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0 or not math.isfinite(value):
        return 50.0
    return float((arr <= value).mean() * 100.0)


def true_range(high, low, prev_close) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(highs, lows, closes, n: int = 14) -> float:
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)
    if h.size < 2:
        return 0.0
    prev = np.roll(c, 1)
    prev[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    take = tr[-n:] if tr.size >= n else tr
    return float(np.mean(take))


def local_extrema(values: list[float], lookback: int) -> tuple[list[int], list[int]]:
    """Return indices of local highs and lows using a ±lookback window."""
    highs, lows = [], []
    n = len(values)
    if n < lookback * 2 + 1:
        return highs, lows
    for i in range(lookback, n - lookback):
        w = values[i - lookback : i + lookback + 1]
        v = values[i]
        if v == max(w) and w.count(v) == 1:
            highs.append(i)
        if v == min(w) and w.count(v) == 1:
            lows.append(i)
    return highs, lows


class Ring:
    """Fixed-capacity deque with fast list snapshot."""

    def __init__(self, maxlen: int):
        self._q: deque = deque(maxlen=maxlen)

    def append(self, item) -> None:
        self._q.append(item)

    def extend(self, items) -> None:
        self._q.extend(items)

    def __len__(self) -> int:
        return len(self._q)

    def __iter__(self):
        return iter(self._q)

    def last(self, n: int | None = None):
        if not self._q:
            return None if n is None else []
        if n is None:
            return self._q[-1]
        if n >= len(self._q):
            return list(self._q)
        return list(self._q)[-n:]

    def snapshot(self) -> list:
        return list(self._q)

    def clear(self) -> None:
        self._q.clear()


def bucket_price(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 12)


def tick_display_decimals(tick: float, tick_raw: str | None = None) -> int:
    """Decimal places needed to show adjacent buckets as different strings."""
    from decimal import Decimal

    src = None
    if tick_raw not in (None, ""):
        src = str(tick_raw).strip()
    elif tick is not None and float(tick) > 0:
        src = str(tick)
    if not src:
        return 4
    try:
        d = Decimal(src).normalize()
    except Exception:
        return 4
    exp = d.as_tuple().exponent
    if not isinstance(exp, int) or exp >= 0:
        return 0
    return min(12, -exp)


def format_price_level(price: float, tick: float, tick_raw: str | None = None) -> str:
    """Format a (bucketed) price using tick/bucket size. Avoids 0.100000000001."""
    from decimal import Decimal, ROUND_HALF_EVEN

    n = tick_display_decimals(tick, tick_raw)
    if price is None:
        return f"{0:.{n}f}"
    try:
        p = Decimal(str(price))
    except Exception:
        return f"{0:.{n}f}"
    step = float(tick or 0.0)
    if step > 0:
        try:
            t = Decimal(str(tick_raw).strip()) if tick_raw not in (None, "") else Decimal(str(tick))
            if t > 0:
                steps = (p / t).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
                p = steps * t
        except Exception:
            pass
    q = Decimal("1").scaleb(-n) if n else Decimal("1")
    p = p.quantize(q, rounding=ROUND_HALF_EVEN)
    return f"{p:.{n}f}"


# Binance kline intervals. UI "24H" maps to 1d.
KLINE_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
INTERVAL_ALIASES = {
    "4H": "4h",
    "24H": "1d",
    "24h": "1d",
    "1H": "1h",
    "1D": "1d",
    "1d": "1d",
}


def normalize_interval(tf: str) -> str:
    raw = (tf or "").strip()
    if raw in INTERVAL_ALIASES:
        return INTERVAL_ALIASES[raw]
    low = raw.lower()
    if low in INTERVAL_ALIASES:
        return INTERVAL_ALIASES[low]
    return low


def tf_ms(tf: str) -> int:
    tf = normalize_interval(tf)
    unit = tf[-1]
    n = int(tf[:-1])
    return n * {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]


def bar_open_ms(ts_ms: int, tf: str) -> int:
    step = tf_ms(tf)
    return ts_ms - (ts_ms % step)

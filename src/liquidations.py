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

    def rolling_window_notionals(
        self,
        side: str,
        window_ms: int = 15 * 60_000,
        lookback_ms: int = 4 * 60 * 60_000,
    ) -> list[float]:
        """Non-overlapping window sums of observed liquidation notional."""
        end = now_ms()
        start = end - lookback_ms
        n_windows = max(1, int(lookback_ms // window_ms))
        buckets = [0.0] * n_windows
        for e in self.events:
            ts = int(e.get("ts") or 0)
            if ts < start or e.get("liq_of") != side:
                continue
            idx = min(n_windows - 1, int((ts - start) // window_ms))
            buckets[idx] += float(e.get("notional") or 0.0)
        return buckets

    def baseline(self, window_ms: int = 15 * 60_000, lookback_ms: int = 4 * 60 * 60_000) -> dict:
        """Rolling median / history of 15m liquidation windows, if enough data."""
        long_w = self.rolling_window_notionals("long", window_ms, lookback_ms)
        short_w = self.rolling_window_notionals("short", window_ms, lookback_ms)
        return {
            "long": _baseline_from_windows(long_w),
            "short": _baseline_from_windows(short_w),
            "window_ms": window_ms,
            "lookback_ms": lookback_ms,
        }


def _baseline_from_windows(windows: list[float]) -> dict:
    nonzero = [float(x) for x in windows if x and x > 0]
    usable = len(nonzero) >= 4
    if not nonzero:
        return {"median": 0.0, "history": [], "usable": False, "n": 0}
    s = sorted(nonzero)
    n = len(s)
    mid = n // 2
    median = float(s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid]))
    return {"median": median, "history": nonzero, "usable": usable, "n": n}


def oi_usdt(snap: dict) -> float:
    """Open-interest notional in USDT. OI contracts are NOT a USD size."""
    oi = snap.get("oi")
    contracts = 0.0
    value = float(snap.get("oi_value") or 0.0)
    if isinstance(oi, dict):
        value = float(oi.get("oi_value") or value or 0.0)
        contracts = float(oi.get("oi") or 0.0)
    elif oi is not None:
        contracts = float(oi or 0.0)
    if value > 0:
        return value
    px = float(snap.get("price") or 0.0)
    if contracts > 0 and px > 0:
        return contracts * px
    return 0.0


def classify_liquidation(
    notional: float,
    *,
    oi_usdt_value: float = 0.0,
    median: float = 0.0,
    history: list[float] | None = None,
    cfg: dict | None = None,
) -> dict:
    """
    Separate a liquidation EVENT from a MEANINGFUL / EXTREME print.

    Levels: none | tiny | meaningful | extreme
    A print with notional > 0 is always an event. It is not automatically
    meaningful: $50k on a $150M book is a tiny sample, not a squeeze.
    """
    from src.utils import percentile_rank

    cfg = cfg or {}
    notion = float(notional or 0.0)
    oi = float(oi_usdt_value or 0.0)
    med = float(median or 0.0)
    hist = list(history or [])
    min_n = float(cfg.get("liq_min_notional", 25_000))
    fallback = float(cfg.get("forced_flow_notional", 50_000))
    to_oi_min = float(cfg.get("liq_to_oi_ratio", 0.0005))
    to_oi_ext = float(cfg.get("liq_to_oi_extreme", 0.002))
    spike_m = float(cfg.get("liq_spike_multiplier", 4.0))
    spike_e = float(cfg.get("liq_spike_extreme", 8.0))
    pct_m = float(cfg.get("liq_significance_pctile", 80))
    pct_e = float(cfg.get("liq_extreme_pctile", 95))
    min_windows = int(cfg.get("liq_baseline_min_windows", 4))

    to_oi = (notion / oi) if oi > 0 and notion > 0 else 0.0
    vs_median = (notion / med) if med > 0 and notion > 0 else 0.0
    usable_hist = len(hist) >= min_windows
    pctile = percentile_rank(notion, hist) if usable_hist and notion > 0 else None

    is_event = notion > 0
    reasons: list[str] = []
    failed: list[str] = []

    if notion <= 0:
        return {
            "notional": 0.0,
            "to_oi": 0.0,
            "vs_median": 0.0,
            "percentile": pctile,
            "level": "none",
            "is_event": False,
            "is_meaningful": False,
            "is_extreme": False,
            "reasons": ["no observed liquidation"],
            "failed": ["notional=0"],
        }

    if notion < min_n:
        failed.append(f"notional ${notion:,.0f} < liq_min_notional ${min_n:,.0f} (tiny print)")
        return {
            "notional": round(notion, 2),
            "to_oi": round(to_oi, 8),
            "vs_median": round(vs_median, 3),
            "percentile": pctile,
            "level": "tiny",
            "is_event": True,
            "is_meaningful": False,
            "is_extreme": False,
            "reasons": [f"observed ${notion:,.0f} (event only)"],
            "failed": failed,
        }

    meaningful = False
    if oi > 0:
        if to_oi >= to_oi_min:
            meaningful = True
            reasons.append(f"liq/OI {to_oi*100:.4f}% >= {to_oi_min*100:.4f}%")
        else:
            failed.append(f"liq/OI {to_oi*100:.4f}% < {to_oi_min*100:.4f}%")
    else:
        failed.append("OI USDT unavailable — cannot normalize vs open interest")

    if med > 0:
        if vs_median >= spike_m:
            meaningful = True
            reasons.append(f"vs rolling median {vs_median:.2f}x >= {spike_m:.1f}x")
        else:
            failed.append(f"vs rolling median {vs_median:.2f}x < {spike_m:.1f}x")
    else:
        failed.append("no rolling liquidation baseline")

    if usable_hist and pctile is not None:
        if pctile >= pct_m:
            meaningful = True
            reasons.append(f"liquidation percentile {pctile:.0f} >= {pct_m:.0f}")
        else:
            failed.append(f"liquidation percentile {pctile:.0f} < {pct_m:.0f}")
    else:
        failed.append("insufficient liquidation history for percentile")

    # Fallback only when we cannot normalize (no OI and no baseline).
    if not meaningful and oi <= 0 and med <= 0 and not usable_hist:
        if notion >= fallback:
            meaningful = True
            reasons.append(
                f"fallback: ${notion:,.0f} >= forced_flow_notional ${fallback:,.0f} "
                "(no OI/baseline — do not treat this as proof on a large book)"
            )
        else:
            failed.append(f"fallback notional ${notion:,.0f} < ${fallback:,.0f}")

    extreme = False
    if meaningful:
        if oi > 0 and to_oi >= to_oi_ext:
            extreme = True
            reasons.append(f"extreme liq/OI {to_oi*100:.4f}%")
        if med > 0 and vs_median >= spike_e:
            extreme = True
            reasons.append(f"extreme vs median {vs_median:.2f}x")
        if usable_hist and pctile is not None and pctile >= pct_e:
            extreme = True
            reasons.append(f"extreme percentile {pctile:.0f}")

    if not meaningful:
        level = "tiny"
        failed.append("observed print is not significant vs OI/baseline")
    elif extreme:
        level = "extreme"
    else:
        level = "meaningful"

    return {
        "notional": round(notion, 2),
        "to_oi": round(to_oi, 8),
        "vs_median": round(vs_median, 3),
        "percentile": None if pctile is None else round(float(pctile), 1),
        "level": level,
        "is_event": is_event,
        "is_meaningful": bool(meaningful),
        "is_extreme": bool(extreme),
        "reasons": reasons or [f"observed ${notion:,.0f}"],
        "failed": failed,
    }

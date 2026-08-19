"""Open interest tracker. OI has no public USD-M websocket — REST snapshots only."""

from __future__ import annotations

from src.utils import Ring, now_ms, pct_change


class OpenInterestTracker:
    def __init__(self, maxlen: int = 4000):
        self.points = Ring(maxlen)
        self.current = 0.0
        self.current_value = 0.0
        self.last_ts = 0
        self.source = "rest:/fapi/v1/openInterest"

    def reset(self) -> None:
        self.points.clear()
        self.current = 0.0
        self.last_ts = 0

    def update(self, oi: float, ts: int | None = None, oi_value: float = 0.0) -> None:
        ts = ts or now_ms()
        if oi <= 0:
            return
        self.current = oi
        self.current_value = oi_value
        self.last_ts = ts
        self.points.append({"ts": ts, "oi": oi, "oi_value": oi_value})

    def seed_hist(self, rows: list[dict]) -> None:
        for r in rows:
            self.update(r["oi"], r["ts"], r.get("oi_value", 0.0))

    def change_pct(self, lookback_ms: int) -> float:
        pts = self.points.snapshot()
        if len(pts) < 2:
            return 0.0
        cutoff = (self.last_ts or now_ms()) - lookback_ms
        first = pts[0]
        for p in pts:
            if p["ts"] <= cutoff:
                first = p
            else:
                break
        return pct_change(self.current, first["oi"])

    def slope(self, n: int = 20) -> float:
        pts = self.points.last(n)
        if not pts or len(pts) < 2:
            return 0.0
        return pts[-1]["oi"] - pts[0]["oi"]

    def snapshot(self) -> dict:
        return {
            "oi": self.current,
            "oi_value": self.current_value,
            "chg_1m_pct": self.change_pct(60_000),
            "chg_5m_pct": self.change_pct(5 * 60_000),
            "chg_15m_pct": self.change_pct(15 * 60_000),
            "chg_1h_pct": self.change_pct(60 * 60_000),
            "ts": self.last_ts,
            "source": self.source,
            "note": "OI snapshots are REST-polled (~3s). There is no public per-symbol OI websocket.",
        }

"""Funding rate + percentile vs recent history."""

from __future__ import annotations

from src.utils import Ring, now_ms, percentile_rank, safe_float


class FundingTracker:
    def __init__(self, maxlen: int = 2000):
        self.points = Ring(maxlen)
        self.current = 0.0
        self.mark = 0.0
        self.next_ts = 0
        self.last_ts = 0

    def reset(self) -> None:
        self.points.clear()
        self.current = 0.0
        self.last_ts = 0

    def seed_hist(self, rows: list[dict]) -> None:
        for r in rows:
            self.points.append(
                {"ts": r["funding_time"], "funding": r["funding_rate"], "mark": r.get("mark", 0.0)}
            )
            self.current = r["funding_rate"]
            self.last_ts = r["funding_time"]

    def update_live(self, rate: float, ts: int | None = None, mark: float = 0.0, next_ts: int = 0) -> None:
        ts = ts or now_ms()
        self.current = safe_float(rate)
        self.mark = mark
        self.next_ts = next_ts
        self.last_ts = ts
        # live mark-price funding is the *predicted/last* rate; store sparsely
        last = self.points.last()
        if last is None or abs(ts - last["ts"]) > 30_000 or abs(last["funding"] - self.current) > 1e-8:
            self.points.append({"ts": ts, "funding": self.current, "mark": mark})

    def percentile(self) -> float:
        hist = [p["funding"] for p in self.points]
        return percentile_rank(self.current, hist)

    def snapshot(self) -> dict:
        return {
            "funding": self.current,
            "funding_pctile": self.percentile(),
            "mark": self.mark,
            "next_funding_time": self.next_ts,
            "ts": self.last_ts,
            "n_hist": len(self.points),
            "note": "Funding is a payment between longs and shorts, not proof of positioning.",
        }

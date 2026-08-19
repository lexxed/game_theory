"""Hysteresis state machine. Does not flip on a single tick."""

from __future__ import annotations

import time

from config import get
from src.gates import evaluate as evaluate_gates

STATES = [
    "NEUTRAL",
    "LONG-SIDE CROWDING ESTIMATE",
    "SHORT-SIDE CROWDING ESTIMATE",
    "POTENTIAL LONG TRAP",
    "POTENTIAL SHORT TRAP",
    "BUY ABSORPTION",
    "SELL ABSORPTION",
    "FAILED BREAKOUT",
    "FAILED BREAKDOWN",
    "LONG LIQUIDATION CASCADE",
    "SHORT LIQUIDATION CASCADE",
    "SHORT SQUEEZE",
    "LONG SQUEEZE",
    "CASCADE EXHAUSTION",
]


class StateMachine:
    def __init__(self):
        self.state = "NEUTRAL"
        self.since = time.time()
        self.prev = "NEUTRAL"
        self.reason = "init"
        self.history: list[dict] = []

    def reset(self) -> None:
        self.state = "NEUTRAL"
        self.prev = "NEUTRAL"
        self.since = time.time()
        self.reason = "reset"
        self.history.clear()

    def update(self, scores: dict, snap: dict) -> str:
        cfg = get("state_machine", {})
        dwell = float(cfg.get("min_dwell_s", 25))
        now = time.time()
        candidate, reason = self._candidate(scores, snap, cfg)

        if candidate == self.state:
            return self.state

        # hysteresis: leaving a state requires being below exit or dwell elapsed
        if now - self.since < dwell and self.state != "NEUTRAL":
            # allow upgrade to a higher-priority cascade/squeeze immediately
            if _priority(candidate) >= 80 and _priority(candidate) > _priority(self.state):
                pass
            else:
                return self.state

        if candidate != self.state:
            self.prev = self.state
            self.state = candidate
            self.since = now
            self.reason = reason
            self.history.append(
                {"ts": int(now * 1000), "state": self.state, "prev": self.prev, "reason": reason}
            )
            if len(self.history) > 400:
                self.history = self.history[-400:]
        return self.state

    def _candidate(self, scores: dict, snap: dict, cfg: dict) -> tuple[str, str]:
        setup_e = float(cfg.get("setup_enter", 70))
        setup_x = float(cfg.get("setup_exit", 55))
        abs_e = float(cfg.get("absorption_enter", 60))
        crowd_e = float(cfg.get("crowding_enter", 60))
        cas_x = float(get("cascade.exit", 50))

        gates = scores.get("gates") or evaluate_gates(snap, scores)
        ls = scores["long_setup"]["total"]
        ss = scores["short_setup"]["total"]
        cl = scores["cascade_long"]
        cs = scores["cascade_short"]
        sq = scores.get("squeeze") or {}
        ab = snap.get("absorption") or {}
        st = snap.get("structure") or {}

        # Cascades require observed liquidation (gate), not intensity alone.
        if gates.get("long_cascade"):
            return "LONG LIQUIDATION CASCADE", "observed long liq above threshold + cascade intensity"
        if gates.get("short_cascade"):
            return "SHORT LIQUIDATION CASCADE", "observed short liq above threshold + cascade intensity"

        if self.state == "LONG LIQUIDATION CASCADE" and gates.get("long_cascade"):
            return self.state, "cascade still active"
        if self.state == "SHORT LIQUIDATION CASCADE" and gates.get("short_cascade"):
            return self.state, "cascade still active"
        if self.state in ("LONG LIQUIDATION CASCADE", "SHORT LIQUIDATION CASCADE"):
            if cl < cas_x and cs < cas_x:
                return "CASCADE EXHAUSTION", "cascade intensity fell through exit threshold"

        if sq.get("long_squeeze"):
            return "LONG SQUEEZE", sq.get("reason", "short covering / short liqs")
        if sq.get("short_squeeze"):
            return "SHORT SQUEEZE", sq.get("reason", "long covering / long liqs")

        # Confirm score is NOT used as a trap-confirmed signal.
        if gates.get("long_trap_confirmation") and ls >= setup_x:
            return "POTENTIAL LONG TRAP", "structure+flow GATE met (confirm score ignored)"
        if gates.get("short_trap_confirmation") and ss >= setup_x:
            return "POTENTIAL SHORT TRAP", "structure+flow GATE met (confirm score ignored)"

        if st.get("failed_breakout") and ls >= setup_x:
            return "FAILED BREAKOUT", "range high taken out then rejected"
        if st.get("failed_breakdown") and ss >= setup_x:
            return "FAILED BREAKDOWN", "range low taken out then rejected"

        if ab.get("buy_absorption") and (ab.get("strength") or 0) * 100 >= abs_e:
            return "BUY ABSORPTION", ab.get("reason", "buy absorption")
        if ab.get("sell_absorption") and (ab.get("strength") or 0) * 100 >= abs_e:
            return "SELL ABSORPTION", ab.get("reason", "sell absorption")

        if ls >= setup_e:
            return "POTENTIAL LONG TRAP", f"vulnerability {ls:.0f}/100 — NOT a confirmed trap"
        if ss >= setup_e:
            return "POTENTIAL SHORT TRAP", f"vulnerability {ss:.0f}/100 — NOT a confirmed trap"

        crowd_long = next((c for c in scores["long_setup"]["components"] if c["name"] == "crowding"), None)
        crowd_short = next((c for c in scores["short_setup"]["components"] if c["name"] == "crowding"), None)
        if crowd_long and crowd_long["normalized"] * 100 >= crowd_e and ls > ss:
            return "LONG-SIDE CROWDING ESTIMATE", crowd_long["reason"]
        if crowd_short and crowd_short["normalized"] * 100 >= crowd_e and ss > ls:
            return "SHORT-SIDE CROWDING ESTIMATE", crowd_short["reason"]

        return "NEUTRAL", "no combination cleared enter thresholds"


def _priority(state: str) -> int:
    order = {
        "LONG LIQUIDATION CASCADE": 100,
        "SHORT LIQUIDATION CASCADE": 100,
        "LONG SQUEEZE": 80,
        "SHORT SQUEEZE": 80,
        "CASCADE EXHAUSTION": 70,
        "POTENTIAL LONG TRAP": 60,
        "POTENTIAL SHORT TRAP": 60,
        "FAILED BREAKOUT": 50,
        "FAILED BREAKDOWN": 50,
        "BUY ABSORPTION": 40,
        "SELL ABSORPTION": 40,
        "LONG-SIDE CROWDING ESTIMATE": 20,
        "SHORT-SIDE CROWDING ESTIMATE": 20,
        "NEUTRAL": 0,
    }
    return order.get(state, 0)

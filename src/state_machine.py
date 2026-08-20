"""Hysteresis state machine. Does not flip on a single tick.

Architecture (do not invert):
    scoring.py  -> raw evidence / intensity (including squeeze flags, cascade_long)
    gates.py    -> authoritative booleans (long_squeeze, short_squeeze, long_cascade, ...)
    this module -> STATE, by reading gates only for trade-event labels

Priority (highest wins among states whose GATE is already true):
    CASCADE (100) > SQUEEZE (90) > FORCED FLOW (80) > CASCADE EXHAUSTION (70)
    > TRAP CONFIRM / POTENTIAL TRAP (60) > FAILED BO/BD (50) > BREAKOUT/DOWN (45)
    > ABSORPTION (40) > SETUP (via potential trap enter) > CROWDING (20) > NEUTRAL (0)

Priority never creates a cascade/squeeze/forced-flow whose gate is false.
Cascade intensity (cascade_long / cascade_short) is diagnostic; it is used only as
EXIT hysteresis into CASCADE EXHAUSTION after a real cascade, never as entry.
"""

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
    "LONG TRAP DEVELOPING",
    "LONG TRAP DEVELOPING STRONG",
    "BUY ABSORPTION",
    "SELL ABSORPTION",
    "BREAKOUT",
    "BREAKDOWN",
    "FAILED BREAKOUT",
    "FAILED BREAKDOWN",
    "LONG FORCED FLOW",
    "SHORT FORCED FLOW",
    "LONG SQUEEZE",
    "SHORT SQUEEZE",
    "LONG LIQUIDATION CASCADE",
    "SHORT LIQUIDATION CASCADE",
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
            # allow upgrade to a stronger event (forced-flow / squeeze / cascade)
            if _priority(candidate) >= 70 and _priority(candidate) > _priority(self.state):
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

        # Authoritative booleans live in gates.py. If the caller omitted them,
        # compute once here — do not re-derive squeeze/cascade from raw scores.
        gates = scores.get("gates") or evaluate_gates(snap, scores)
        ls = scores["long_setup"]["total"]
        ss = scores["short_setup"]["total"]
        cl = float(scores.get("cascade_long") or 0.0)
        cs = float(scores.get("cascade_short") or 0.0)
        ab = snap.get("absorption") or {}
        st = snap.get("structure") or {}

        # CASCADE entry: gate only. Intensity is never sufficient.
        if gates.get("long_cascade"):
            return "LONG LIQUIDATION CASCADE", (gates.get("explanation_text") or "long cascade gate")
        if gates.get("short_cascade"):
            return "SHORT LIQUIDATION CASCADE", (gates.get("explanation_text") or "short cascade gate")

        # Leave cascade via intensity EXIT only (hysteresis). Gate already false here.
        if self.state in ("LONG LIQUIDATION CASCADE", "SHORT LIQUIDATION CASCADE"):
            if cl < cas_x and cs < cas_x:
                return "CASCADE EXHAUSTION", "cascade intensity fell through exit threshold"

        # SQUEEZE / FORCED FLOW: gates only. Raw scores["squeeze"] is evidence, not STATE.
        if gates.get("short_squeeze"):
            return "SHORT SQUEEZE", gates.get("trade_status_reason") or "short squeeze gate"
        if gates.get("long_squeeze"):
            return "LONG SQUEEZE", gates.get("trade_status_reason") or "long squeeze gate"

        if gates.get("short_forced_flow"):
            return "SHORT FORCED FLOW", gates.get("trade_status_reason") or "short forced-flow gate"
        if gates.get("long_forced_flow"):
            return "LONG FORCED FLOW", gates.get("trade_status_reason") or "long forced-flow gate"

        # Confirm score is NOT used as a trap-confirmed signal.
        if gates.get("long_trap_confirmation"):
            return "POTENTIAL LONG TRAP", "structure+flow GATE met (confirm score ignored)"
        if gates.get("short_trap_confirmation"):
            return "POTENTIAL SHORT TRAP", "structure+flow GATE met (confirm score ignored)"

        if gates.get("long_trap_developing_strong"):
            return "LONG TRAP DEVELOPING STRONG", gates.get("long_trap_developing_reason") or "early warning — not confirmation"
        if gates.get("long_trap_developing"):
            return "LONG TRAP DEVELOPING", gates.get("long_trap_developing_reason") or "early warning — not confirmation"

        if st.get("failed_breakout") and ls >= setup_x:
            return "FAILED BREAKOUT", "range high taken out then rejected"
        if st.get("failed_breakdown") and ss >= setup_x:
            return "FAILED BREAKDOWN", "range low taken out then rejected"

        # Clean breakout/breakdown: a discrete close-over-close crossing of the prior
        # range level (see price_structure.analyze), confirmed by OI expanding and CVD
        # following in the same direction. Without that confirmation this is just price
        # poking outside a range on thin participation — not scored as a breakout state.
        oi15 = float(snap.get("oi_chg_15m_pct") or 0.0)
        cvd5 = float(snap.get("cvd_chg_5m") or 0.0)
        if st.get("breakout") and oi15 > 0 and cvd5 > 0:
            return "BREAKOUT", f"close crossed prior range high, OI {oi15:+.3f}% CVD5 {cvd5:+.4g} confirming"
        if st.get("breakdown") and oi15 > 0 and cvd5 < 0:
            return "BREAKDOWN", f"close crossed prior range low, OI {oi15:+.3f}% CVD5 {cvd5:+.4g} confirming"

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
    """Rank among candidates. Does not authorize a state whose gate is false."""
    order = {
        "LONG LIQUIDATION CASCADE": 100,
        "SHORT LIQUIDATION CASCADE": 100,
        "LONG SQUEEZE": 90,
        "SHORT SQUEEZE": 90,
        "LONG FORCED FLOW": 80,
        "SHORT FORCED FLOW": 80,
        "CASCADE EXHAUSTION": 70,
        "POTENTIAL LONG TRAP": 60,
        "POTENTIAL SHORT TRAP": 60,
        "LONG TRAP DEVELOPING STRONG": 58,
        "LONG TRAP DEVELOPING": 56,
        "FAILED BREAKOUT": 50,
        "FAILED BREAKDOWN": 50,
        "BREAKOUT": 45,
        "BREAKDOWN": 45,
        "BUY ABSORPTION": 40,
        "SELL ABSORPTION": 40,
        "LONG-SIDE CROWDING ESTIMATE": 20,
        "SHORT-SIDE CROWDING ESTIMATE": 20,
        "NEUTRAL": 0,
    }
    return order.get(state, 0)

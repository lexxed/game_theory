import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.state_machine import StateMachine


def _scores(ls=10, lc=5, ss=10, sc=5, cl=0, cs=0, squeeze=None):
    def card(total):
        return {"total": total, "components": [{"name": "crowding", "normalized": total / 100, "reason": "t"}]}

    return {
        "long_setup": card(ls),
        "short_setup": card(ss),
        "long_confirm": card(lc),
        "short_confirm": card(sc),
        "cascade_long": cl,
        "cascade_short": cs,
        "squeeze": squeeze or {},
    }


def test_starts_neutral():
    sm = StateMachine()
    assert sm.update(_scores(), {}) == "NEUTRAL"


def test_high_setup_becomes_potential_trap():
    sm = StateMachine()
    st = sm.update(_scores(ls=85, lc=20), {})
    assert st == "POTENTIAL LONG TRAP"


def test_does_not_flip_every_tick_inside_dwell():
    sm = StateMachine()
    sm.update(_scores(ls=85), {})
    sm.since = time.time()  # just entered
    st = sm.update(_scores(ls=10), {})
    assert st == "POTENTIAL LONG TRAP"


def test_cascade_can_interrupt():
    from src.gates import evaluate

    sm = StateMachine()
    sm.update(_scores(ls=85), {})
    scores = _scores(ls=85, cl=90)
    snap = {
        "liq_15m": {"long_notional": 200000, "short_notional": 0},
        "liq_5m": {"long_notional": 200000, "short_notional": 0},
        "structure": {},
        "oi_chg_15m_pct": -1.0,
        "price_chg_15m_pct": -1.0,
        "price_chg_5m_pct": -0.8,
        "cvd_chg_5m": -10,
        "cvd_chg_15m": -10,
    }
    scores["gates"] = evaluate(snap, scores)
    st = sm.update(scores, snap)
    assert st == "LONG LIQUIDATION CASCADE"


def test_high_cascade_score_without_liq_is_not_cascade_state():
    sm = StateMachine()
    st = sm.update(_scores(ls=85, cl=90), {"liq_15m": {"long_notional": 0, "short_notional": 0}, "structure": {}})
    assert st != "LONG LIQUIDATION CASCADE"


def test_breakout_requires_oi_and_cvd_confirmation():
    sm = StateMachine()
    snap_unconfirmed = {
        "structure": {"breakout": True},
        "oi_chg_15m_pct": -0.1,  # OI shrinking -> not confirmed
        "cvd_chg_5m": 5.0,
    }
    st = sm.update(_scores(), snap_unconfirmed)
    assert st != "BREAKOUT"

    sm2 = StateMachine()
    snap_confirmed = {
        "structure": {"breakout": True},
        "oi_chg_15m_pct": 0.5,
        "cvd_chg_5m": 5.0,
    }
    st2 = sm2.update(_scores(), snap_confirmed)
    assert st2 == "BREAKOUT"


def test_breakdown_requires_oi_and_cvd_confirmation():
    sm = StateMachine()
    snap_confirmed = {
        "structure": {"breakdown": True},
        "oi_chg_15m_pct": 0.5,
        "cvd_chg_5m": -5.0,
    }
    st = sm.update(_scores(), snap_confirmed)
    assert st == "BREAKDOWN"


def _gates(**kw):
    """Injected authoritative gates. Missing keys are false."""
    base = {
        "long_cascade": False,
        "short_cascade": False,
        "long_squeeze": False,
        "short_squeeze": False,
        "long_forced_flow": False,
        "short_forced_flow": False,
        "long_trap_confirmation": False,
        "short_trap_confirmation": False,
        "trade_status_reason": "injected",
    }
    base.update(kw)
    return base


def test_raw_short_squeeze_does_not_bypass_false_gate():
    """TEST 1: raw short_squeeze True, gate False → STATE is not SHORT SQUEEZE."""
    sm = StateMachine()
    scores = _scores(squeeze={"short_squeeze": True, "long_squeeze": False, "reason": "raw"})
    scores["gates"] = _gates(short_squeeze=False, short_forced_flow=True)
    st = sm.update(scores, {})
    assert st != "SHORT SQUEEZE"


def test_raw_long_squeeze_does_not_bypass_false_gate():
    """TEST 2: raw long_squeeze True, gate False → STATE is not LONG SQUEEZE."""
    sm = StateMachine()
    scores = _scores(squeeze={"short_squeeze": False, "long_squeeze": True, "reason": "raw"})
    scores["gates"] = _gates(long_squeeze=False, long_forced_flow=True)
    st = sm.update(scores, {})
    assert st != "LONG SQUEEZE"


def test_short_squeeze_gate_true_is_state():
    """TEST 3: gates.short_squeeze True → SHORT SQUEEZE."""
    sm = StateMachine()
    scores = _scores(squeeze={"short_squeeze": False})
    scores["gates"] = _gates(short_squeeze=True)
    assert sm.update(scores, {}) == "SHORT SQUEEZE"


def test_long_squeeze_gate_true_is_state():
    """TEST 4: gates.long_squeeze True → LONG SQUEEZE."""
    sm = StateMachine()
    scores = _scores(squeeze={"long_squeeze": False})
    scores["gates"] = _gates(long_squeeze=True)
    assert sm.update(scores, {}) == "LONG SQUEEZE"


def test_high_cascade_long_intensity_without_gate_is_not_cascade():
    """TEST 5: cascade_long high, gate False → not LONG LIQUIDATION CASCADE."""
    sm = StateMachine()
    scores = _scores(ls=85, cl=95, cs=0)
    scores["gates"] = _gates(long_cascade=False)
    st = sm.update(scores, {})
    assert st != "LONG LIQUIDATION CASCADE"


def test_high_cascade_short_intensity_without_gate_is_not_cascade():
    """TEST 6: cascade_short high, gate False → not SHORT LIQUIDATION CASCADE."""
    sm = StateMachine()
    scores = _scores(ss=85, cl=0, cs=95)
    scores["gates"] = _gates(short_cascade=False)
    st = sm.update(scores, {})
    assert st != "SHORT LIQUIDATION CASCADE"


def test_long_cascade_gate_true_is_state():
    """TEST 7: gates.long_cascade True → LONG LIQUIDATION CASCADE."""
    sm = StateMachine()
    scores = _scores(cl=10)
    scores["gates"] = _gates(long_cascade=True)
    assert sm.update(scores, {}) == "LONG LIQUIDATION CASCADE"


def test_short_cascade_gate_true_is_state():
    """TEST 8: gates.short_cascade True → SHORT LIQUIDATION CASCADE."""
    sm = StateMachine()
    scores = _scores(cs=10)
    scores["gates"] = _gates(short_cascade=True)
    assert sm.update(scores, {}) == "SHORT LIQUIDATION CASCADE"


def test_cascade_gate_outranks_squeeze_gate():
    sm = StateMachine()
    scores = _scores()
    scores["gates"] = _gates(long_cascade=True, short_squeeze=True)
    assert sm.update(scores, {}) == "LONG LIQUIDATION CASCADE"

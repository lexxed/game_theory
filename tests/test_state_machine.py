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

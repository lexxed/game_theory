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
    sm = StateMachine()
    sm.update(_scores(ls=85), {})
    st = sm.update(_scores(ls=85, cl=90), {})
    assert st == "LONG LIQUIDATION CASCADE"

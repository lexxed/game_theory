import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gates import evaluate
from src.scoring import ScoreEngine
from src.state_machine import StateMachine
from tests.test_scoring import _base


def _scores_from(snap):
    out = ScoreEngine().compute(snap)
    out["gates"] = evaluate(snap, out)
    return out


def test_high_setup_is_vulnerability_not_confirmed():
    snap = _base(
        funding=0.001,
        funding_pctile=99,
        ls_account_ratio=2.5,
        oi_chg_15m_pct=0.8,
        price_chg_15m_pct=0.2,
    )
    g = evaluate(snap, ScoreEngine().compute(snap))
    assert g["long_vulnerability"] > 20
    assert g["long_trap_confirmation"] is False
    assert g["trade_status"] in ("WAIT", "WATCH LONG-TRAP")


def test_confirm_score_alone_does_not_confirm_long_trap():
    # Lost nothing, no failed breakout, but confirm-like numbers via liq/cvd
    snap = _base(
        cvd_chg_5m=-20,
        price_chg_5m_pct=-0.5,
        oi_chg_15m_pct=-0.4,
        price_chg_15m_pct=-0.4,
        liq_15m={"long_notional": 400000, "short_notional": 0, "long_n": 5, "short_n": 0},
        structure={
            "near_high": True,
            "near_low": False,
            "lost_support": False,
            "lost_resistance": False,
            "failed_breakout": False,
            "failed_breakdown": False,
            "reason": "high only",
        },
    )
    scores = _scores_from(snap)
    assert scores["long_confirm"]["total"] > 40
    assert scores["gates"]["long_trap_confirmation"] is False


def test_long_trap_confirmed_needs_structure_and_flow():
    snap = _base(
        cvd_chg_5m=-8,
        price_chg_5m_pct=-0.2,
        oi_chg_15m_pct=-0.3,
        price_chg_15m_pct=-0.2,
        structure={
            "near_high": False,
            "near_low": False,
            "lost_support": True,
            "lost_resistance": False,
            "failed_breakout": False,
            "failed_breakdown": False,
            "reason": "lost support",
        },
    )
    g = evaluate(snap, ScoreEngine().compute(snap))
    assert g["long_structure_gate"] is True
    assert g["long_flow_gate"] is True
    assert g["long_trap_confirmation"] is True
    assert g["trade_status"] == "LONG-TRAP CONFIRMING"


def test_failed_breakout_plus_long_liq_confirms():
    snap = _base(
        liq_15m={"long_notional": 10, "short_notional": 0, "long_n": 1, "short_n": 0},
        structure={
            "near_high": True,
            "near_low": False,
            "lost_support": False,
            "lost_resistance": False,
            "failed_breakout": True,
            "failed_breakdown": False,
            "reason": "failed bo",
        },
    )
    g = evaluate(snap, ScoreEngine().compute(snap))
    assert g["long_trap_confirmation"] is True


def test_short_trap_confirmed_needs_structure_and_flow():
    snap = _base(
        cvd_chg_5m=8,
        price_chg_5m_pct=0.2,
        oi_chg_15m_pct=-0.3,
        price_chg_15m_pct=0.2,
        structure={
            "near_high": False,
            "near_low": True,
            "lost_support": False,
            "lost_resistance": True,
            "failed_breakout": False,
            "failed_breakdown": False,
            "reason": "broke res",
        },
    )
    g = evaluate(snap, ScoreEngine().compute(snap))
    assert g["short_trap_confirmation"] is True
    assert g["trade_status"] == "SHORT-TRAP CONFIRMING"


def test_cascade_requires_observed_liq_not_just_intensity():
    # Strong down move / OI drop / CVD — intensity high, but no liq
    snap = _base(
        oi_chg_15m_pct=-1.0,
        price_chg_5m_pct=-0.8,
        cvd_chg_5m=-50,
        liq_15m={"long_notional": 0, "short_notional": 0, "long_n": 0, "short_n": 0},
        liq_5m={"long_notional": 0, "short_notional": 0},
    )
    scores = _scores_from(snap)
    assert scores["cascade_long"] >= 70
    assert scores["gates"]["long_cascade"] is False
    sm = StateMachine()
    assert sm.update(scores, snap) != "LONG LIQUIDATION CASCADE"


def test_cascade_fires_when_liq_and_intensity():
    snap = _base(
        oi_chg_15m_pct=-1.0,
        price_chg_5m_pct=-0.8,
        cvd_chg_5m=-50,
        liq_15m={"long_notional": 250000, "short_notional": 0, "long_n": 4, "short_n": 0},
        liq_5m={"long_notional": 200000, "short_notional": 0},
    )
    scores = _scores_from(snap)
    assert scores["gates"]["long_cascade"] is True
    assert scores["gates"]["trade_status"] == "CASCADE / DO NOT CHASE"
    sm = StateMachine()
    assert sm.update(scores, snap) == "LONG LIQUIDATION CASCADE"


def test_forced_flow_threshold():
    snap = _base(liq_15m={"long_notional": 60000, "short_notional": 0, "long_n": 2, "short_n": 0})
    g = evaluate(snap, ScoreEngine().compute(snap))
    assert g["long_forced_flow"] is True
    assert g["short_forced_flow"] is False
    assert g["trade_status"] == "LONG FORCED-FLOW"


def test_squeeze_still_needs_opposite_liq():
    from src.scoring import ScoreEngine

    no_liq = _base(oi_chg_15m_pct=-0.2, price_chg_15m_pct=0.2, cvd_chg_15m=10)
    yes = _base(
        oi_chg_15m_pct=-0.2,
        price_chg_15m_pct=0.2,
        cvd_chg_15m=10,
        liq_15m={"long_notional": 0, "short_notional": 1, "long_n": 0, "short_n": 1},
    )
    a = ScoreEngine().compute(no_liq)
    b = ScoreEngine().compute(yes)
    assert a["squeeze"]["long_squeeze"] is False
    assert b["squeeze"]["long_squeeze"] is True


def test_watch_status_from_setup_only():
    snap = _base(funding=0.002, funding_pctile=99, ls_account_ratio=2.4, oi_chg_15m_pct=0.5, price_chg_15m_pct=0.1)
    g = evaluate(snap, ScoreEngine().compute(snap))
    if g["long_vulnerability"] >= 70:
        assert g["trade_status"] == "WATCH LONG-TRAP"
        assert g["long_trap_confirmation"] is False

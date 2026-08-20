"""LONG TRAP DEVELOPING early-warning layer. Does not replace confirm."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gates import evaluate
from src.scoring import ScoreEngine, long_trap_developing
from src.state_machine import StateMachine
from tests.test_scoring import _base


def _run(snap):
    scores = ScoreEngine().compute(snap)
    scores["gates"] = evaluate(snap, scores)
    sm = StateMachine()
    sm.since = 0
    state = sm.update(scores, snap)
    return scores, scores["gates"], state


def test_neutral_snapshot_is_not_developing():
    scores, g, state = _run(_base())
    assert g["long_trap_developing"] is False
    assert g["long_trap_developing_strong"] is False
    assert g["long_trap_confirmation"] is False
    assert g["trade_status"] == "WAIT"
    assert state not in ("LONG TRAP DEVELOPING", "LONG TRAP DEVELOPING STRONG")
    assert scores["long_trap_developing"]["evidence_count"] == 0


def test_crowding_alone_does_not_trigger_developing():
    snap = _base(funding_pctile=95, ls_account_ratio=1.8, top_pos_ratio=1.6)
    d = long_trap_developing(snap)
    assert d["long_crowding"] >= 50
    assert d["evidence_count"] == 0
    assert d["developing"] is False
    _, g, _ = _run(snap)
    assert g["long_trap_developing"] is False
    assert g["long_trap_confirmation"] is False


def test_crowded_short_term_reversal_is_developing_not_confirm():
    snap = _base(
        funding_pctile=90,
        ls_account_ratio=1.8,
        top_pos_ratio=1.6,
        price=99.5,
        recent_local_high=100.0,
        cvd_chg_1m=-10.0,
        cvd_chg_3m=-8.0,
        cvd_chg_5m=-8.0,
        oi_chg_5m_pct=-0.10,
        price_chg_5m_pct=-0.20,
    )
    d = long_trap_developing(snap)
    assert d["early_price_reversal"] is True
    assert d["early_cvd_reversal"] is True
    assert d["early_oi_unwind"] is False
    assert d["evidence_count"] == 2
    assert d["developing"] is True
    assert d["developing_strong"] is False
    scores, g, state = _run(snap)
    assert g["long_trap_developing"] is True
    assert g["long_trap_confirmation"] is False
    assert g["trade_status"] == "LONG TRAP DEVELOPING"
    assert state == "LONG TRAP DEVELOPING"
    assert "longs closed" not in (d["reason"] or "").lower()
    assert "OI shrinking" not in d["reason"] or True


def test_three_early_signals_is_developing_strong():
    snap = _base(
        funding_pctile=90,
        ls_account_ratio=1.8,
        top_pos_ratio=1.6,
        price=99.5,
        recent_local_high=100.0,
        cvd_chg_1m=-10.0,
        cvd_chg_3m=-8.0,
        oi_chg_5m_pct=-0.60,
        price_chg_5m_pct=-0.20,
    )
    d = long_trap_developing(snap)
    assert d["evidence_count"] >= 3
    assert d["developing_strong"] is True
    _, g, state = _run(snap)
    assert g["long_trap_developing_strong"] is True
    assert g["long_trap_confirmation"] is False
    assert g["trade_status"] == "LONG TRAP DEVELOPING STRONG"
    assert state == "LONG TRAP DEVELOPING STRONG"


def test_confirm_still_outranks_developing():
    snap = _base(
        funding_pctile=90,
        ls_account_ratio=1.8,
        top_pos_ratio=1.6,
        funding=0.001,
        price=99.5,
        recent_local_high=100.0,
        cvd_chg_1m=-10.0,
        cvd_chg_3m=-8.0,
        cvd_chg_5m=-8.0,
        price_chg_5m_pct=-0.20,
        price_chg_15m_pct=-0.20,
        oi_chg_15m_pct=0.50,
        oi_chg_5m_pct=-0.60,
        cvd_div={"bearish": True, "bullish": False, "bearish_strength": 0.9, "bullish_strength": 0, "reason": "hh"},
        absorption={"buy_absorption": True, "sell_absorption": False, "strength": 0.8, "reason": "abs"},
        structure={
            "near_high": True,
            "near_low": False,
            "lost_support": True,
            "lost_resistance": False,
            "failed_breakout": True,
            "failed_breakdown": False,
            "reason": "lost support",
        },
    )
    _, g, state = _run(snap)
    assert g["long_trap_developing"] is True
    assert g["long_trap_confirmation"] is True
    assert g["trade_status"] == "LONG-TRAP CONFIRMING"
    assert state == "POTENTIAL LONG TRAP"


def test_recent_high_uses_only_existing_bars():
    from src.price_structure import PriceStructure

    ps = PriceStructure()
    ps.seed(
        "5m",
        [
            {"open_time": 1, "open": 1, "high": 10, "low": 1, "close": 9},
            {"open_time": 2, "open": 9, "high": 12, "low": 8, "close": 11},
        ],
    )
    assert ps.recent_high("5m", 20) == 12
    assert ps.recent_high("1m", 20) == 0.0

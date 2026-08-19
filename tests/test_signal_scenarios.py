"""Event vs forced-flow vs trap vs squeeze vs cascade scenarios."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gates import evaluate
from src.liquidations import classify_liquidation
from src.scoring import ScoreEngine
from src.state_machine import StateMachine
from tests.test_scoring import _base


def _run(snap):
    scores = ScoreEngine().compute(snap)
    scores["gates"] = evaluate(snap, scores)
    sm = StateMachine()
    sm.since = 0
    state = sm.update(scores, snap)
    return scores, scores["gates"], state


def test_classify_tiny_vs_meaningful_vs_extreme():
    tiny = classify_liquidation(63520, oi_usdt_value=151_000_000)
    assert tiny["is_event"] is True
    assert tiny["is_meaningful"] is False
    assert tiny["level"] == "tiny"

    mean = classify_liquidation(500_000, oi_usdt_value=10_000_000)
    assert mean["is_meaningful"] is True

    ext = classify_liquidation(400_000, oi_usdt_value=10_000_000)
    assert ext["is_extreme"] is True

    none = classify_liquidation(0, oi_usdt_value=10_000_000)
    assert none["is_event"] is False
    assert none["level"] == "none"


def test_1_short_forced_flow_and_possible_squeeze():
    """Price ↑ CVD ↑ OI ↓ meaningful short liq → SHORT FORCED FLOW; squeeze if magnitude ok."""
    snap = _base(
        price=1.0,
        oi={"oi": 100_000_000, "oi_value": 100_000_000},
        oi_chg_15m_pct=-0.40,
        price_chg_15m_pct=0.50,
        price_chg_5m_pct=0.30,
        cvd_chg_5m=80_000,
        cvd_chg_15m=80_000,
        liq_15m={"long_notional": 0, "short_notional": 70_000, "long_n": 0, "short_n": 4},
        liq_5m={"long_notional": 0, "short_notional": 50_000},
    )
    scores, g, state = _run(snap)
    assert g["short_liq_event"] is True
    assert g["short_forced_flow"] is True
    assert g["long_forced_flow"] is False
    assert g["short_squeeze"] is True
    assert g["long_squeeze"] is False
    assert g["short_cascade"] is False
    assert g["trade_status"] in ("SHORT FORCED-FLOW", "SHORT SQUEEZE")
    assert state in ("SHORT FORCED FLOW", "SHORT SQUEEZE")
    assert scores["squeeze"]["short_squeeze"] is True


def test_2_long_forced_flow_and_possible_squeeze():
    """Price ↓ CVD ↓ OI ↓ meaningful long liq → LONG FORCED FLOW."""
    snap = _base(
        price=1.0,
        oi={"oi": 100_000_000, "oi_value": 100_000_000},
        oi_chg_15m_pct=-0.40,
        price_chg_15m_pct=-0.50,
        price_chg_5m_pct=-0.30,
        cvd_chg_5m=-80_000,
        cvd_chg_15m=-80_000,
        liq_15m={"long_notional": 70_000, "short_notional": 0, "long_n": 4, "short_n": 0},
        liq_5m={"long_notional": 50_000, "short_notional": 0},
    )
    scores, g, state = _run(snap)
    assert g["long_liq_event"] is True
    assert g["long_forced_flow"] is True
    assert g["short_forced_flow"] is False
    assert g["long_squeeze"] is True
    assert g["long_cascade"] is False
    assert g["trade_status"] in ("LONG FORCED-FLOW", "LONG SQUEEZE")
    assert state in ("LONG FORCED FLOW", "LONG SQUEEZE")


def test_3_falling_price_small_short_liq_is_event_only():
    """Price ↓ CVD ↓ OI ↓ small short liq → event yes, forced-flow/squeeze no."""
    snap = _base(
        price=0.65062,
        oi={"oi": 232_705_074, "oi_value": 232_705_074 * 0.65062},
        oi_chg_15m_pct=-0.1567,
        price_chg_15m_pct=-1.118,
        price_chg_5m_pct=-1.118,
        cvd_chg_5m=-411_966,
        cvd_chg_15m=-411_966,
        liq_15m={"long_notional": 0, "short_notional": 63_519.56, "long_n": 0, "short_n": 1},
        liq_5m={"long_notional": 0, "short_notional": 63_519.56},
    )
    scores, g, state = _run(snap)
    assert g["short_liq_event"] is True
    assert g["short_forced_flow"] is False
    assert g["short_squeeze"] is False
    assert g["short_trap_confirmation"] is False
    assert g["trade_status"] not in (
        "SHORT FORCED-FLOW",
        "SHORT SQUEEZE",
        "SHORT-TRAP CONFIRMING",
        "CASCADE / DO NOT CHASE",
    )
    assert state not in ("SHORT FORCED FLOW", "SHORT SQUEEZE", "SHORT LIQUIDATION CASCADE")
    assert "price not rising" in g["explanation_text"]
    assert "CVD not rising" in g["explanation_text"]


def test_4_small_short_liq_with_bullish_tape_is_not_auto_squeeze():
    """Price ↑ CVD ↑ OI ↓ tiny short liq → covering evidence, not a squeeze."""
    snap = _base(
        price=1.0,
        oi={"oi": 20_000_000, "oi_value": 20_000_000},
        oi_chg_15m_pct=-0.20,
        price_chg_15m_pct=0.25,
        price_chg_5m_pct=0.15,
        cvd_chg_5m=5_000,
        cvd_chg_15m=5_000,
        liq_15m={"long_notional": 0, "short_notional": 8_000, "long_n": 0, "short_n": 1},
    )
    scores, g, state = _run(snap)
    assert g["short_liq_event"] is True
    assert g["short_liq_level"] == "tiny"
    assert g["short_forced_flow"] is False
    assert g["short_squeeze"] is False
    assert scores["squeeze"]["short_squeeze"] is False


def test_5_funding_percentile_is_crowding_proxy_only():
    snap = _base(
        funding=0.00119635,
        funding_pctile=96.3,
        ls_account_ratio=0.4257,
        oi_chg_15m_pct=0.0,
        price_chg_15m_pct=0.0,
        cvd_chg_5m=0.0,
        cvd_chg_15m=0.0,
    )
    scores, g, state = _run(snap)
    assert g["long_forced_flow"] is False
    assert g["short_forced_flow"] is False
    assert g["long_trap_confirmation"] is False
    assert g["short_trap_confirmation"] is False
    assert g["long_squeeze"] is False
    assert g["short_squeeze"] is False
    assert g["trade_status"] in ("WAIT", "WATCH LONG-TRAP", "WATCH SHORT-TRAP")
    crowd = g["long_crowding_proxy"]
    assert crowd["score"] > 0
    assert "PROXY" in crowd["label"]
    assert state not in ("LONG FORCED FLOW", "SHORT FORCED FLOW", "LONG SQUEEZE", "SHORT SQUEEZE")


def test_6_expanding_book_rally_is_not_short_squeeze():
    """Price ↑ OI ↑ CVD ↑ no significant liq → not a squeeze."""
    snap = _base(
        price=1.0,
        oi={"oi": 10_000_000, "oi_value": 10_000_000},
        oi_chg_15m_pct=0.50,
        price_chg_15m_pct=0.80,
        price_chg_5m_pct=0.40,
        cvd_chg_5m=90_000,
        cvd_chg_15m=90_000,
        liq_15m={"long_notional": 0, "short_notional": 0, "long_n": 0, "short_n": 0},
    )
    scores, g, state = _run(snap)
    assert g["short_liq_event"] is False
    assert g["short_forced_flow"] is False
    assert g["short_squeeze"] is False
    assert scores["squeeze"]["short_squeeze"] is False
    assert state != "SHORT SQUEEZE"


def test_7_long_forced_flow_significant_long_liq():
    snap = _base(
        price=1.0,
        oi={"oi": 8_000_000, "oi_value": 8_000_000},
        oi_chg_15m_pct=-0.35,
        price_chg_15m_pct=-0.90,
        price_chg_5m_pct=-0.60,
        cvd_chg_5m=-40_000,
        cvd_chg_15m=-40_000,
        liq_15m={"long_notional": 300_000, "short_notional": 0, "long_n": 5, "short_n": 0},
        liq_5m={"long_notional": 200_000, "short_notional": 0},
    )
    scores, g, state = _run(snap)
    assert g["long_liq_event"] is True
    assert g["long_forced_flow"] is True
    assert g["long_squeeze"] is True
    assert g["short_forced_flow"] is False
    assert state in ("LONG FORCED FLOW", "LONG SQUEEZE", "LONG LIQUIDATION CASCADE")


def test_btwusdt_regression_short_print_is_not_forced_flow():
    """Exact dashboard case: falling tape + $63k short liq must not be SHORT FORCED-FLOW."""
    snap = _base(
        price=0.65062,
        oi={"oi": 232_705_074, "oi_value": 232_705_074 * 0.65062},
        oi_chg_15m_pct=-0.1567,
        price_chg_15m_pct=-1.118,
        price_chg_5m_pct=-1.118,
        price_chg_1m_pct=-0.4,
        funding=0.00119635,
        funding_pctile=96.3,
        ls_account_ratio=0.4257,
        cvd_chg_5m=-411_966,
        cvd_chg_15m=-411_966,
        liq_15m={"long_notional": 0, "short_notional": 63_519.56, "long_n": 0, "short_n": 1},
        liq_5m={"long_notional": 0, "short_notional": 63_519.56},
    )
    scores, g, state = _run(snap)
    assert g["short_liq_event"] is True
    assert g["short_liq_level"] in ("tiny", "none")
    assert g["short_forced_flow"] is False
    assert g["long_forced_flow"] is False
    assert g["short_squeeze"] is False
    assert g["long_squeeze"] is False
    assert g["short_cascade"] is False
    assert g["long_cascade"] is False
    assert g["short_trap_confirmation"] is False
    assert g["trade_status"] == "WAIT"
    assert "EVENT only" in g["trade_status_reason"] or "WAIT" in g["trade_status"]
    # Intensity can still be high on a down tape; that is not a cascade.
    assert scores["cascade_long"] > scores["cascade_short"]
    assert "CASCADE INTENSITY" in g["explanation_text"]
    assert state not in ("SHORT FORCED FLOW", "SHORT SQUEEZE", "SHORT LIQUIDATION CASCADE")


def test_cascade_intensity_is_not_a_cascade_without_liq():
    snap = _base(
        oi_chg_15m_pct=-1.0,
        price_chg_5m_pct=-0.8,
        price_chg_15m_pct=-0.8,
        cvd_chg_5m=-50,
        cvd_chg_15m=-50,
    )
    scores, g, state = _run(snap)
    assert scores["cascade_long"] >= 70
    assert g["long_cascade"] is False
    assert g["trade_status"] != "CASCADE / DO NOT CHASE"
    assert state != "LONG LIQUIDATION CASCADE"


def test_short_liq_while_price_up_without_cvd_is_not_forced_flow():
    snap = _base(
        price=1.0,
        oi={"oi": 5_000_000, "oi_value": 5_000_000},
        oi_chg_15m_pct=-0.3,
        price_chg_15m_pct=0.4,
        cvd_chg_5m=-10_000,
        cvd_chg_15m=-10_000,
        liq_15m={"long_notional": 0, "short_notional": 200_000, "long_n": 0, "short_n": 3},
    )
    _, g, _ = _run(snap)
    assert g["short_liq_event"] is True
    assert g["short_forced_flow"] is False
    assert "CVD not rising" in g["explanation_text"]

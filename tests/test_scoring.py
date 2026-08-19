import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scoring import ScoreEngine


def _base(**kw):
    s = {
        "funding": 0.0001,
        "funding_pctile": 50,
        "ls_account_ratio": 1.0,
        "oi_chg_15m_pct": 0.0,
        "price_chg_1m_pct": 0.0,
        "price_chg_5m_pct": 0.0,
        "price_chg_15m_pct": 0.0,
        "cvd_div": {"bearish": False, "bullish": False, "bearish_strength": 0, "bullish_strength": 0, "reason": "none"},
        "absorption": {"buy_absorption": False, "sell_absorption": False, "strength": 0, "reason": "none"},
        "liq_15m": {"long_notional": 0, "short_notional": 0, "long_n": 0, "short_n": 0},
        "liq_5m": {"long_notional": 0, "short_notional": 0},
        "structure": {"near_high": False, "near_low": False, "lost_support": False, "lost_resistance": False,
                      "failed_breakout": False, "failed_breakdown": False, "reason": "none"},
        "cvd_chg_5m": 0.0,
        "cvd_chg_15m": 0.0,
    }
    s.update(kw)
    return s


def test_neutral_scores_low():
    out = ScoreEngine().compute(_base())
    assert 0 <= out["long_setup"]["total"] <= 40
    assert out["long_setup"]["total"] + 1e-9 >= 0
    assert len(out["long_setup"]["components"]) == 7


def test_long_trap_setup_rises_with_crowding_and_div():
    out = ScoreEngine().compute(
        _base(
            funding=0.001,
            funding_pctile=98,
            ls_account_ratio=2.2,
            oi_chg_15m_pct=0.8,
            price_chg_15m_pct=0.1,
            cvd_div={"bearish": True, "bullish": False, "bearish_strength": 0.9, "bullish_strength": 0, "reason": "hh"},
            absorption={"buy_absorption": True, "sell_absorption": False, "strength": 0.8, "reason": "abs"},
            structure={"near_high": True, "near_low": False, "lost_support": False, "lost_resistance": False,
                       "failed_breakout": True, "failed_breakdown": False, "reason": "high"},
        )
    )
    assert out["long_setup"]["total"] > 60
    assert out["long_setup"]["total"] > out["short_setup"]["total"]


def test_confirmation_requires_adverse_flow():
    setup_like = _base(
        funding=0.001, funding_pctile=95, ls_account_ratio=2.0,
        oi_chg_15m_pct=0.5, price_chg_15m_pct=0.2,
    )
    low = ScoreEngine().compute(setup_like)
    high = ScoreEngine().compute(
        _base(
            oi_chg_15m_pct=-0.5,
            price_chg_15m_pct=-0.4,
            price_chg_5m_pct=-0.3,
            price_chg_1m_pct=-0.4,
            cvd_chg_5m=-10,
            liq_15m={"long_notional": 500000, "short_notional": 0, "long_n": 8, "short_n": 0},
            liq_5m={"long_notional": 300000, "short_notional": 0},
            structure={"near_high": False, "near_low": False, "lost_support": True, "lost_resistance": False,
                       "failed_breakout": False, "failed_breakdown": False, "reason": "broke"},
        )
    )
    assert high["long_confirm"]["total"] > low["long_confirm"]["total"]
    assert high["long_confirm"]["total"] > 40


def test_weights_sum_displayed():
    out = ScoreEngine().compute(_base())
    w = sum(c["weight"] for c in out["long_setup"]["components"])
    assert abs(w - 100) < 1e-6

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
        "orderbook": {"ready": False, "imbalance_ratio": 0.0, "bid_notional": 0.0, "ask_notional": 0.0,
                      "thin_ask_wall_above": False, "thin_bid_wall_below": False, "reason": "not populated"},
        "taker_ratio_1m": {"ratio": 0.0, "buy_vol": 0.0, "sell_vol": 0.0, "n": 0, "reason": "no trades"},
        "taker_ratio_5m": {"ratio": 0.0, "buy_vol": 0.0, "sell_vol": 0.0, "n": 0, "reason": "no trades"},
    }
    s.update(kw)
    return s


def test_neutral_scores_low():
    out = ScoreEngine().compute(_base())
    assert 0 <= out["long_setup"]["total"] <= 40
    assert out["long_setup"]["total"] + 1e-9 >= 0
    assert len(out["long_setup"]["components"]) == 9


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


def test_book_imbalance_raises_long_setup_only_with_thin_wall():
    neutral = ScoreEngine().compute(
        _base(orderbook={"ready": True, "imbalance_ratio": 0.6, "bid_notional": 80000,
                          "ask_notional": 20000, "thin_ask_wall_above": False,
                          "thin_bid_wall_below": False, "reason": "bid-heavy, ask not thin"})
    )
    thin = ScoreEngine().compute(
        _base(orderbook={"ready": True, "imbalance_ratio": 0.6, "bid_notional": 80000,
                          "ask_notional": 4000, "thin_ask_wall_above": True,
                          "thin_bid_wall_below": False, "reason": "bid-heavy, thin ask wall"})
    )
    long_comp_neutral = next(c for c in neutral["long_setup"]["components"] if c["name"] == "book_imbalance")
    long_comp_thin = next(c for c in thin["long_setup"]["components"] if c["name"] == "book_imbalance")
    assert long_comp_thin["points"] > long_comp_neutral["points"] > 0
    # short setup should not be boosted by a positive (bid-heavy) imbalance
    short_comp_thin = next(c for c in thin["short_setup"]["components"] if c["name"] == "book_imbalance")
    assert short_comp_thin["points"] == 0


def test_taker_flow_rewards_intensifying_one_sided_buying():
    base_kw = dict(
        taker_ratio_1m={"ratio": 0.8, "buy_vol": 90, "sell_vol": 10, "n": 50, "reason": "1m heavy buy"},
        taker_ratio_5m={"ratio": 0.5, "buy_vol": 300, "sell_vol": 100, "n": 200, "reason": "5m heavy buy"},
    )
    intensifying = ScoreEngine().compute(_base(**base_kw))

    fading_kw = dict(
        taker_ratio_1m={"ratio": 0.1, "buy_vol": 55, "sell_vol": 45, "n": 50, "reason": "1m cooling"},
        taker_ratio_5m={"ratio": 0.5, "buy_vol": 300, "sell_vol": 100, "n": 200, "reason": "5m heavy buy"},
    )
    fading = ScoreEngine().compute(_base(**fading_kw))

    ic = next(c for c in intensifying["long_setup"]["components"] if c["name"] == "taker_flow")
    fc = next(c for c in fading["long_setup"]["components"] if c["name"] == "taker_flow")
    assert ic["points"] > fc["points"] > 0

    # heavy BUY flow should not boost the short setup's taker_flow component
    short_c = next(c for c in intensifying["short_setup"]["components"] if c["name"] == "taker_flow")
    assert short_c["points"] == 0

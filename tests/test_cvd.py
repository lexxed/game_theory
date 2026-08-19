import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cvd import CVDEngine


def test_cvd_accumulates_signed_volume():
    e = CVDEngine()
    e.on_trade({"ts": 1, "price": 100, "qty": 1, "side": "buy"})
    e.on_trade({"ts": 2, "price": 100, "qty": 2, "side": "sell"})
    e.on_trade({"ts": 3, "price": 100, "qty": 3, "side": "buy"})
    assert abs(e.cvd - 2.0) < 1e-9  # +1 -2 +3


def test_cvd_change_window():
    e = CVDEngine()
    e.on_trade({"ts": 10_000, "price": 1, "qty": 5, "side": "buy"})
    e.on_trade({"ts": 70_000, "price": 1, "qty": 2, "side": "sell"})
    assert abs(e.change(20_000) - (-2.0)) < 1e-9


def test_bearish_divergence_requires_price_hh_without_cvd_hh():
    e = CVDEngine()
    prices = [10, 11, 10, 10, 12, 11, 10, 10, 13, 12, 11]
    cvds = [0, 5, 4, 4, 8, 6, 5, 5, 7, 6, 5]  # second high 13 > 12 but CVD 7 < 8
    t0 = 1_700_000_000_000
    e.by_tf["1m"] = [
        {
            "open_time": t0 + i * 60_000,
            "cvd": c,
            "delta": 0,
            "open_px": p,
            "close_px": p,
            "high_px": p,
            "low_px": p,
        }
        for i, (p, c) in enumerate(zip(prices, cvds))
    ]
    cfg = {"swing_lookback": 1, "min_swing_gap_bars": 2, "min_price_hh_pct": 0.04, "min_price_ll_pct": 0.04}
    d = e.detect_divergence("1m", cfg)
    assert d["bearish"] is True
    assert d["bearish_strength"] > 0


def test_seed_from_klines_uses_taker_buy_split():
    e = CVDEngine()
    bars = [
        {"open_time": 100, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 10, "taker_buy_base": 7},
        {"open_time": 200, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 10, "taker_buy_base": 2},
    ]
    e.seed_from_klines("4h", bars)
    s = e.series("4H")
    assert len(s) == 2
    # bar1: buy 7 sell 3 → +4 ; bar2: buy 2 sell 8 → -6 ; cvd 4 then -2
    assert abs(s[0]["delta"] - 4) < 1e-9
    assert abs(s[1]["delta"] - (-6)) < 1e-9
    assert abs(s[1]["cvd"] - (-2)) < 1e-9
    assert abs(e.last_for("4h") - (-2)) < 1e-9


def test_live_trades_do_not_overwrite_kline_cvd_history():
    e = CVDEngine()
    e.seed_from_klines(
        "1d",
        [{"open_time": 0, "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 100, "taker_buy_base": 80}],
    )
    e.mark_live(ts=50)
    hist = e.series("1d")[0]["cvd"]  # +60
    e.on_trade({"ts": 10, "price": 1, "qty": 999, "side": "sell"})  # before live_after — ignore TF
    assert abs(e.series("1d")[0]["cvd"] - hist) < 1e-9
    e.on_trade({"ts": 60, "price": 1, "qty": 5, "side": "buy"})
    assert abs(e.series("1d")[0]["cvd"] - (hist + 5)) < 1e-9

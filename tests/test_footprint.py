import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.footprint import FootprintEngine


def test_buckets_and_delta():
    fp = FootprintEngine(tick_size=0.1, tick_mult=1)
    ts = 1_700_000_000_000
    fp.on_trade({"ts": ts, "price": 100.04, "qty": 2, "side": "buy"})
    fp.on_trade({"ts": ts + 10, "price": 100.02, "qty": 1, "side": "sell"})
    rows = fp.levels("1m", max_levels=10)
    assert len(rows) == 1
    r = rows[0]
    assert abs(r["price"] - 100.0) < 1e-9 or abs(r["price"] - 100.1) < 1e-9 or abs(r["price"] - 100.0) < 0.15
    assert r["ask"] == 2
    assert r["bid"] == 1
    assert r["delta"] == 1


def test_buy_absorption_large_delta_small_progress():
    fp = FootprintEngine(tick_size=1.0)
    ts = 1_700_000_000_000
    for i in range(20):
        fp.on_trade({"ts": ts + i, "price": 100, "qty": 10, "side": "buy"})
    cfg = {"min_delta_frac_of_volume": 0.18, "max_progress_atr": 0.35, "large_imbalance_frac": 0.3}
    d = fp.detect_absorption("1m", price_progress=0.1, atr=2.0, cfg=cfg)
    assert d["buy_absorption"] is True
    assert d["strength"] > 0


def test_red_like_adjacent_buckets_display_differently():
    from src.utils import format_price_level

    tick = 0.00001
    a = format_price_level(0.10000, tick, "0.00001000")
    b = format_price_level(0.10001, tick, "0.00001000")
    c = format_price_level(0.09999, tick, "0.00001000")
    assert a != b != c
    assert a == "0.10000"
    assert b == "0.10001"
    assert c == "0.09999"
    # the old dashboard .4f path would collapse these
    assert f"{0.10000:.4f}" == f"{0.10001:.4f}" == "0.1000"
    assert "0000000001" not in a


def test_btc_like_prices_display_with_tick():
    from src.utils import format_price_level

    tick = 0.10
    a = format_price_level(97000.10, tick, "0.10")
    b = format_price_level(97000.20, tick, "0.10")
    assert a != b
    assert a == "97000.1"
    assert b == "97000.2"


def test_float_junk_is_quantized_to_tick():
    from src.utils import format_price_level

    junk = 0.1 + 1e-16
    s = format_price_level(junk, 0.00001, "0.00001")
    assert s == "0.10000"
    assert "000000000" not in s


def test_reset_keeps_symbol_tick_size():
    fp = FootprintEngine(tick_size=0.01, tick_mult=1)
    fp.set_tick(0.00001, 1)
    assert abs(fp.bucket - 0.00001) < 1e-18
    fp.reset()
    assert abs(fp.tick_size - 0.00001) < 1e-18
    assert abs(fp.bucket - 0.00001) < 1e-18
    fp.set_tick(0.10, 1)
    assert abs(fp.tick_size - 0.10) < 1e-12
    fp.reset()
    assert abs(fp.tick_size - 0.10) < 1e-12


def test_fp_html_does_not_collapse_red_levels():
    from src.dashboard_app import _fp_html

    rows = [
        {"price": 0.10001, "bid": 1, "ask": 2, "delta": 1, "bucket": 0.00001, "tick_size": 0.00001},
        {"price": 0.10000, "bid": 1, "ask": 1, "delta": 0, "bucket": 0.00001, "tick_size": 0.00001},
    ]
    html = _fp_html(rows, tick_size=0.00001, bucket=0.00001, tick_mult=1, tick_raw="0.00001000")
    assert "0.10001" in html
    assert "0.10000" in html
    assert "tickSize=" in html
    assert "bucket=" in html


def test_bucket_is_tick_times_multiplier():
    fp = FootprintEngine(tick_size=0.00001, tick_mult=10)
    assert abs(fp.bucket - 0.0001) < 1e-18
    fp.set_tick(0.00001, 1)
    assert abs(fp.bucket - 0.00001) < 1e-18

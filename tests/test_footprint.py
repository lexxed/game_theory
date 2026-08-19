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

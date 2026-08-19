import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.orderbook import OrderBookTracker


def _depth(bids, asks, ts=1000):
    """bids/asks: list of (price, qty) tuples."""
    return {
        "e": "depthUpdate",
        "E": ts,
        "b": [[str(p), str(q)] for p, q in bids],
        "a": [[str(p), str(q)] for p, q in asks],
    }


def test_not_ready_before_any_update():
    ob = OrderBookTracker()
    out = ob.imbalance()
    assert out["ready"] is False


def test_balanced_book_near_zero_imbalance_no_thin_wall():
    ob = OrderBookTracker()
    bids = [(100 - i * 0.01, 10) for i in range(10)]
    asks = [(100.01 + i * 0.01, 10) for i in range(10)]
    ob.on_depth(_depth(bids, asks))
    out = ob.imbalance(price_range_pct=0.5, thin_wall_ratio=0.20)
    assert out["ready"] is True
    assert abs(out["imbalance_ratio"]) < 0.05
    assert out["thin_ask_wall_above"] is False
    assert out["thin_bid_wall_below"] is False


def test_heavy_bids_thin_asks_flags_thin_ask_wall():
    ob = OrderBookTracker()
    bids = [(100 - i * 0.01, 100) for i in range(10)]   # heavy resting bids
    asks = [(100.01 + i * 0.01, 1) for i in range(10)]   # light resting asks
    ob.on_depth(_depth(bids, asks))
    out = ob.imbalance(price_range_pct=0.5, thin_wall_ratio=0.20)
    assert out["imbalance_ratio"] > 0.5
    assert out["thin_ask_wall_above"] is True
    assert out["thin_bid_wall_below"] is False


def test_heavy_asks_thin_bids_flags_thin_bid_wall():
    ob = OrderBookTracker()
    bids = [(100 - i * 0.01, 1) for i in range(10)]
    asks = [(100.01 + i * 0.01, 100) for i in range(10)]
    ob.on_depth(_depth(bids, asks))
    out = ob.imbalance(price_range_pct=0.5, thin_wall_ratio=0.20)
    assert out["imbalance_ratio"] < -0.5
    assert out["thin_bid_wall_below"] is True
    assert out["thin_ask_wall_above"] is False


def test_price_range_pct_excludes_far_levels():
    ob = OrderBookTracker()
    # heavy bid support, but it's far outside the 0.1% band, so it shouldn't count
    bids = [(100, 5), (90, 10_000)]
    asks = [(100.05, 5)]
    ob.on_depth(_depth(bids, asks))
    out = ob.imbalance(price_range_pct=0.1, thin_wall_ratio=0.20)
    # only the 100 bid level (within band) should count, not the 90 level
    assert out["bid_notional"] < 1000

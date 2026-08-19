import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.funding import FundingTracker
from src.liquidations import LiquidationTracker
from src.oi import OpenInterestTracker
from src.trades import TradeTape


def test_oi_change_windows():
    oi = OpenInterestTracker()
    oi.update(100, ts=1_000_000)
    oi.update(102, ts=1_000_000 + 60_000)
    # 30s back from last print still sits after the 100-OI snapshot
    assert abs(oi.change_pct(30_000) - 2.0) < 1e-6
    ch = oi.change_pct(120_000)
    assert abs(ch - 2.0) < 1e-6


def test_funding_percentile():
    f = FundingTracker()
    f.seed_hist([{"funding_time": i, "funding_rate": i * 0.0001, "mark": 1} for i in range(10)])
    f.update_live(0.0009, ts=99, mark=1)
    assert f.percentile() >= 80


def test_liquidation_side_mapping():
    liq = LiquidationTracker()
    rec = liq.ingest_ws(
        {"e": "forceOrder", "E": 50, "o": {"s": "BTCUSDT", "S": "SELL", "z": "0.5", "ap": "60000", "T": 50}},
        "BTCUSDT",
    )
    assert rec["liq_of"] == "long"
    rec2 = liq.ingest_ws(
        {"o": {"s": "ETHUSDT", "S": "BUY", "q": "1", "p": "2000", "T": 51}},
        "BTCUSDT",
    )
    assert rec2 is None  # other symbol filtered
    rec3 = liq.ingest_ws(
        {"o": {"s": "BTCUSDT", "S": "BUY", "z": "2", "ap": "61000", "T": 52}},
        "btcusdt",
    )
    assert rec3["liq_of"] == "short"
    from src.utils import now_ms

    st = liq.stats(now_ms() + 1_000)
    assert st["long_n"] == 1
    assert st["short_n"] == 1


def test_taker_ratio_all_buy_is_plus_one():
    t = TradeTape()
    for i in range(5):
        t._push(1000 + i, px=100.0, qty=1.0, is_buyer_maker=False)  # buy aggressor
    out = t.taker_ratio(60_000)
    assert abs(out["ratio"] - 1.0) < 1e-6
    assert out["buy_vol"] == 5.0
    assert out["sell_vol"] == 0.0


def test_taker_ratio_all_sell_is_minus_one():
    t = TradeTape()
    for i in range(5):
        t._push(1000 + i, px=100.0, qty=2.0, is_buyer_maker=True)  # sell aggressor
    out = t.taker_ratio(60_000)
    assert abs(out["ratio"] - (-1.0)) < 1e-6
    assert out["sell_vol"] == 10.0


def test_taker_ratio_respects_lookback_window():
    t = TradeTape()
    # old sells outside the window, recent buys inside it
    t._push(0, px=100.0, qty=100.0, is_buyer_maker=True)
    t._push(120_000, px=100.0, qty=5.0, is_buyer_maker=False)
    t._push(121_000, px=100.0, qty=5.0, is_buyer_maker=False)
    out = t.taker_ratio(10_000)  # only last 10s -> just the two recent buys
    assert out["sell_vol"] == 0.0
    assert out["buy_vol"] == 10.0
    assert abs(out["ratio"] - 1.0) < 1e-6


def test_taker_ratio_no_trades_is_neutral():
    t = TradeTape()
    out = t.taker_ratio(60_000)
    assert out["ratio"] == 0.0
    assert out["n"] == 0

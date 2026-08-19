import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.funding import FundingTracker
from src.liquidations import LiquidationTracker
from src.oi import OpenInterestTracker


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

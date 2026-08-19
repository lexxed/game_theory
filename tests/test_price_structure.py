import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.price_structure import PriceStructure


def _bars(closes, highs=None, lows=None):
    """Build minimal synthetic 1m bars from a list of closes."""
    highs = highs or [c + 0.5 for c in closes]
    lows = lows or [c - 0.5 for c in closes]
    out = []
    for i, c in enumerate(closes):
        out.append(
            {
                "open_time": i * 60_000,
                "open": c,
                "high": highs[i],
                "low": lows[i],
                "close": c,
                "volume": 100.0,
            }
        )
    return out


def test_clean_breakout_flagged_when_close_crosses_and_holds():
    ps = PriceStructure()
    # flat range around 100 for a while, prior high ~101, then a close that
    # crosses and holds above 101 on the last bar.
    closes = [100, 100.2, 99.8, 100.1, 99.9, 100.3, 100.0, 100.2, 99.9, 100.1, 100.5, 102.0]
    ps.on_kline("1m", {"t": 0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1})
    for i, b in enumerate(_bars(closes)):
        ps.klines["1m"] = ps.klines.get("1m", [])
        ps.on_kline(
            "1m",
            {
                "t": b["open_time"],
                "o": b["open"],
                "h": b["high"],
                "l": b["low"],
                "c": b["close"],
                "v": b["volume"],
            },
        )
    out = ps.analyze("1m", lookback=2, near_pct=0.15)
    assert out["breakout"] is True
    assert out["failed_breakout"] is False


def test_failed_breakout_not_also_flagged_as_breakout():
    ps = PriceStructure()
    # wick above prior high but closes back inside -> failed_breakout, not breakout
    closes = [100, 100.2, 99.8, 100.1, 99.9, 100.3, 100.0, 100.2, 99.9, 100.1, 100.5, 100.15]
    highs = list(closes)
    highs[-1] = 102.0  # spike wick on last bar, but close stayed low, below prior_high (100.3)
    for i, c in enumerate(closes):
        ps.on_kline(
            "1m",
            {
                "t": i * 60_000,
                "o": c,
                "h": highs[i],
                "l": c - 0.5,
                "c": c,
                "v": 1,
            },
        )
    out = ps.analyze("1m", lookback=2, near_pct=0.15)
    assert out["failed_breakout"] is True
    assert out["breakout"] is False

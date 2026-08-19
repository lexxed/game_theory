"""
Historical score events → forward returns.

Does NOT claim the score is predictive. It only reports what happened
after past snapshots that were stored in DuckDB.

Usage (from this folder):
    python backtest.py
    python backtest.py --symbol BTCUSDT --score long_setup --min 80
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.storage import Storage  # noqa: E402


HORIZONS = {
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
}


def load_joined(symbol: str) -> pd.DataFrame:
    st = Storage()
    scores = st.query(
        "SELECT * FROM scores WHERE symbol = ? ORDER BY ts",
        [symbol],
    )
    candles = st.query(
        "SELECT open_time, close, high, low FROM candles "
        "WHERE symbol = ? AND interval = '15m' ORDER BY open_time",
        [symbol],
    )
    return scores, candles


def forward_stats(scores: pd.DataFrame, candles: pd.DataFrame, col: str, thresh: float) -> pd.DataFrame:
    if scores.empty or candles.empty:
        return pd.DataFrame()
    ev = scores[scores[col] >= thresh].copy()
    if ev.empty:
        return pd.DataFrame()
    candles = candles.sort_values("open_time")
    rows = []
    closes = candles["close"].to_numpy()
    highs = candles["high"].to_numpy()
    lows = candles["low"].to_numpy()
    times = candles["open_time"].to_numpy()
    for _, e in ev.iterrows():
        ts = int(e["ts"])
        i = int((times <= ts).sum() - 1)
        if i < 0 or i >= len(closes) - 1:
            continue
        entry = float(closes[i])
        rec = {"ts": ts, "score": float(e[col]), "state": e.get("state"), "entry": entry}
        for name, ms in HORIZONS.items():
            j = int((times <= ts + ms).sum() - 1)
            if j <= i:
                rec[f"ret_{name}"] = None
                continue
            rec[f"ret_{name}"] = closes[j] / entry - 1.0
            window_h = highs[i + 1 : j + 1]
            window_l = lows[i + 1 : j + 1]
            rec[f"mfe_{name}"] = (window_h.max() / entry - 1.0) if len(window_h) else None
            rec[f"mae_{name}"] = (window_l.min() / entry - 1.0) if len(window_l) else None
        rows.append(rec)
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    rows = []
    for name in HORIZONS:
        col = f"ret_{name}"
        s = df[col].dropna() if col in df else pd.Series(dtype=float)
        if s.empty:
            rows.append({"horizon": name, "n": 0})
            continue
        rows.append(
            {
                "horizon": name,
                "n": int(s.shape[0]),
                "avg_return": float(s.mean()),
                "median_return": float(s.median()),
                "win_rate": float((s > 0).mean()),
                "avg_mfe": float(df[f"mfe_{name}"].mean()) if f"mfe_{name}" in df else None,
                "avg_mae": float(df[f"mae_{name}"].mean()) if f"mae_{name}" in df else None,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Forward-return report after high scores. Not a performance claim.")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--score", default="long_setup", choices=["long_setup", "short_setup", "long_confirm", "short_confirm"])
    p.add_argument("--min", dest="thresh", type=float, default=80.0)
    args = p.parse_args()
    scores, candles = load_joined(args.symbol)
    print(f"Loaded {len(scores)} score rows, {len(candles)} 15m candles for {args.symbol}")
    if scores.empty:
        print("No stored scores yet. Run the dashboard so snapshots flush to DuckDB.")
        return
    ev = forward_stats(scores, candles, args.score, args.thresh)
    print(f"Events with {args.score} >= {args.thresh}: {len(ev)}")
    if ev.empty:
        print("Not enough overlapping candle history to measure forward returns.")
        print("This is expected on a fresh database. The score is NOT claimed predictive.")
        return
    sm = summarize(ev)
    print(sm.to_string(index=False))
    print()
    print("LIMIT: this is a descriptive report on stored snapshots, not a validated edge.")


if __name__ == "__main__":
    main()

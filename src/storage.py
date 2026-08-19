"""DuckDB + Parquet persistence. Restart-safe. Never stores unlimited raw trades."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import duckdb
import pandas as pd

from config import ROOT, data_dir, get


_SCHEMA = {
    "candles": """
        CREATE TABLE IF NOT EXISTS candles (
            symbol VARCHAR, interval VARCHAR, open_time BIGINT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, closed BOOLEAN,
            PRIMARY KEY (symbol, interval, open_time)
        )
    """,
    "oi": """
        CREATE TABLE IF NOT EXISTS oi (
            symbol VARCHAR, ts BIGINT, oi DOUBLE, oi_value DOUBLE,
            PRIMARY KEY (symbol, ts)
        )
    """,
    "funding": """
        CREATE TABLE IF NOT EXISTS funding (
            symbol VARCHAR, ts BIGINT, funding_rate DOUBLE, mark DOUBLE,
            PRIMARY KEY (symbol, ts)
        )
    """,
    "liquidations": """
        CREATE TABLE IF NOT EXISTS liquidations (
            symbol VARCHAR, ts BIGINT, side VARCHAR, qty DOUBLE,
            price DOUBLE, notional DOUBLE
        )
    """,
    "cvd": """
        CREATE TABLE IF NOT EXISTS cvd (
            symbol VARCHAR, ts BIGINT, cvd DOUBLE, buy_vol DOUBLE, sell_vol DOUBLE,
            PRIMARY KEY (symbol, ts)
        )
    """,
    "scores": """
        CREATE TABLE IF NOT EXISTS scores (
            symbol VARCHAR, ts BIGINT,
            long_setup DOUBLE, long_confirm DOUBLE,
            short_setup DOUBLE, short_confirm DOUBLE,
            cascade_long DOUBLE, cascade_short DOUBLE,
            state VARCHAR,
            PRIMARY KEY (symbol, ts)
        )
    """,
    "states": """
        CREATE TABLE IF NOT EXISTS states (
            symbol VARCHAR, ts BIGINT, state VARCHAR, prev VARCHAR, reason VARCHAR
        )
    """,
    "footprint": """
        CREATE TABLE IF NOT EXISTS footprint (
            symbol VARCHAR, interval VARCHAR, bar_open BIGINT, price DOUBLE,
            bid_vol DOUBLE, ask_vol DOUBLE, delta DOUBLE, total DOUBLE,
            PRIMARY KEY (symbol, interval, bar_open, price)
        )
    """,
}


class Storage:
    def __init__(self):
        self.dir = data_dir()
        db_rel = get("storage.duckdb_file", "data/market.duckdb")
        self.db_path = ROOT / db_rel
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.parquet_dir = ROOT / get("storage.parquet_dir", "data/parquet")
        self.parquet_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        try:
            self.con = duckdb.connect(str(self.db_path))
        except Exception:
            # Another kernel still holds market.duckdb — isolate this session.
            fallback = self.db_path.with_name(f"market_{os.getpid()}.duckdb")
            self.db_path = fallback
            self.con = duckdb.connect(str(self.db_path))
        for sql in _SCHEMA.values():
            self.con.execute(sql)

    def close(self) -> None:
        with self._lock:
            self.con.close()

    def upsert_df(self, table: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        with self._lock:
            self.con.register("_tmp_df", df)
            cols = ", ".join(df.columns)
            try:
                if table in ("liquidations", "states"):
                    self.con.execute(f"INSERT INTO {table} SELECT {cols} FROM _tmp_df")
                else:
                    self.con.execute(
                        f"INSERT OR REPLACE INTO {table} SELECT {cols} FROM _tmp_df"
                    )
            finally:
                self.con.unregister("_tmp_df")

    def query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        with self._lock:
            return self.con.execute(sql, params or []).fetchdf()

    def export_parquet(self, table: str, symbol: str) -> Path | None:
        path = self.parquet_dir / f"{table}_{symbol}.parquet"
        with self._lock:
            try:
                self.con.execute(
                    f"COPY (SELECT * FROM {table} WHERE symbol = ?) TO ? (FORMAT PARQUET)",
                    [symbol, str(path)],
                )
                return path
            except Exception:
                return None

    def load_recent_scores(self, symbol: str, limit: int = 2000) -> pd.DataFrame:
        return self.query(
            "SELECT * FROM scores WHERE symbol = ? ORDER BY ts DESC LIMIT ?",
            [symbol, limit],
        )

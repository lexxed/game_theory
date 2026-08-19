"""Live session: REST seed + WS + pollers + scoring loop. Thread-safe snapshot()."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pandas as pd

from config import get, reload as reload_cfg
from src.binance_client import BinanceClient, BinanceRESTError
from src.cvd import CVDEngine
from src.footprint import FootprintEngine
from src.funding import FundingTracker
from src.game_theory import interpret
from src.gates import evaluate as evaluate_gates
from src.liquidations import LiquidationTracker
from src.oi import OpenInterestTracker
from src.orderbook import OrderBookTracker
from src.price_structure import PriceStructure
from src.scoring import ScoreEngine
from src.state_machine import StateMachine
from src.storage import Storage
from src.trades import TradeTape
from src.utils import KLINE_INTERVALS, normalize_interval, now_ms
from src.websocket_manager import WebsocketManager

log = logging.getLogger("gt.session")


class MarketSession:
    def __init__(self):
        self.symbol = get("symbol_default", "BTCUSDT")
        self.timeframe = get("timeframe_default", "15m")
        self.client = BinanceClient()
        self.storage: Storage | None = None
        self.trades = TradeTape(int(get("buffers.max_trades", 25000)))
        self.cvd = CVDEngine(int(get("buffers.max_cvd_rows", 4000)))
        self.foot = FootprintEngine(
            tick_mult=int(get("footprint.tick_multiplier", 1)),
            keep_bars=int(get("buffers.footprint_bars", 48)),
        )
        self.oi = OpenInterestTracker(int(get("buffers.max_oi_rows", 4000)))
        self.orderbook = OrderBookTracker()
        self.funding = FundingTracker(int(get("buffers.max_funding_rows", 2000)))
        self.liqs = LiquidationTracker(int(get("buffers.max_liquidations", 3000)))
        self.structure = PriceStructure()
        self.scorer = ScoreEngine()
        self.states = StateMachine()
        self.ws = WebsocketManager(self._on_ws, self._on_ws_status)
        self.ws_book = WebsocketManager(
            self._on_ws,
            self._on_ws_status,
            base_url=get("binance.ws_public_combined", "wss://fstream.binance.com/public/stream"),
            status_key="orderbook",
            thread_name="gt-ws-book",
        )

        self.meta: dict[str, Any] = {}
        self.ticker: dict[str, Any] = {}
        self.ls_account_ratio: float | None = None
        self.top_pos_ratio: float | None = None
        self.taker_ratio: float | None = None

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._score_thread: threading.Thread | None = None
        self.running = False
        self.last_scores: dict = {}
        self.score_hist: list[dict] = []
        self.status: dict[str, dict] = {
            k: {"state": "DOWN", "ts": 0, "detail": ""}
            for k in ("ws", "price", "trades", "oi", "funding", "liquidation", "kline", "orderbook")
        }
        self.last_flush = 0.0
        self.last_error = ""
        self._narrative = ""

    # ------------------------------------------------------------------
    def start(self, symbol: str | None = None, timeframe: str | None = None) -> None:
        self.stop()
        self._stop.clear()
        if symbol:
            self.symbol = symbol.upper().strip()
        if timeframe:
            self.timeframe = normalize_interval(timeframe)
        if self.storage is None:
            self.storage = Storage()
        self._reset_ram()
        try:
            self._seed()
        except Exception as exc:
            self.last_error = f"seed failed: {exc}"
            log.exception("seed")
            raise
        self.ws.start(self._stream_list())
        self.ws_book.start(self._depth_stream_list())
        self.running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, name="gt-poll", daemon=True)
        self._score_thread = threading.Thread(target=self._score_loop, name="gt-score", daemon=True)
        self._poll_thread.start()
        self._score_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        try:
            self.ws.stop()
        except Exception:
            pass
        try:
            self.ws_book.stop()
        except Exception:
            pass
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2)
        if self._score_thread and self._score_thread.is_alive():
            self._score_thread.join(timeout=2)
        try:
            if self.storage is not None:
                self._flush()
        except Exception:
            pass

    def switch(self, symbol: str, timeframe: str | None = None) -> None:
        self.start(symbol, timeframe or self.timeframe)

    def set_timeframe(self, timeframe: str) -> str:
        """Switch chart/analysis interval without tearing down the socket."""
        tf = normalize_interval(timeframe)
        self.timeframe = tf
        self.ensure_klines(tf)
        return tf

    def ensure_klines(self, timeframe: str) -> list[dict]:
        """Always have REST candles for this interval (4h / 1d included)."""
        aliases = {
            "4H": "4h",
            "24H": "1d",
            "24h": "1d",
            "1H": "1h",
            "1D": "1d",
        }
        tf = aliases.get(str(timeframe).strip()) or aliases.get(str(timeframe).strip().lower())
        if not tf:
            try:
                tf = normalize_interval(timeframe)
            except Exception:
                tf = str(timeframe).strip().lower()
        have = self.structure.last_n(tf, 500)
        if len(have) >= 10:
            if hasattr(self.cvd, "seed_from_klines") and len(self.cvd.series(tf)) < 3:
                self.cvd.seed_from_klines(tf, have)
            return have
        limit = int(get("binance.kline_seed_limit", 300))
        bars = self.client.klines(self.symbol, tf, limit=limit)
        self.structure.seed(tf, bars)
        if hasattr(self.cvd, "seed_from_klines"):
            self.cvd.seed_from_klines(tf, bars)
        self._status("kline", "live", f"seeded {len(bars)} {tf}")
        return self.structure.last_n(tf, 500)

    def reload_config(self) -> None:
        reload_cfg()

    def _reset_ram(self) -> None:
        self.trades.reset()
        self.cvd.reset()
        self.foot.reset()
        self.oi.reset()
        self.orderbook.reset()
        self.funding.reset()
        self.liqs.reset()
        self.structure.reset()
        self.states.reset()
        self.score_hist.clear()
        self.last_scores = {}
        self.ls_account_ratio = None
        self.last_error = ""

    def _stream_list(self) -> list[str]:
        s = self.symbol.lower()
        return [
            f"{s}@aggTrade",
            f"{s}@kline_1m",
            f"{s}@kline_5m",
            f"{s}@kline_15m",
            f"{s}@kline_1h",
            f"{s}@kline_4h",
            f"{s}@kline_1d",
            f"{s}@markPrice@1s",
            f"{s}@ticker",
            "!forceOrder@arr",
        ]

    def _depth_stream_list(self) -> list[str]:
        s = self.symbol.lower()
        levels = int(get("orderbook.depth_stream_levels", 20))
        return [f"{s}@depth{levels}@100ms"]

    # ------------------------------------------------------------------
    def _seed(self) -> None:
        self.meta = self.client.describe_symbol(self.symbol)
        self.foot.set_tick(float(self.meta["tickSize"]), int(get("footprint.tick_multiplier", 1)))
        limit = int(get("binance.kline_seed_limit", 300))
        seed_tfs = list(
            dict.fromkeys(
                ["1m", "5m", "15m", "1h", "4h", "1d", *list(KLINE_INTERVALS), *list(get("timeframes", []))]
            )
        )
        for tf in seed_tfs:
            tf = normalize_interval(tf)
            bars = self.client.klines(self.symbol, tf, limit=limit)
            self.structure.seed(tf, bars)
            self.cvd.seed_from_klines(tf, bars)
            self._status("kline", "live", f"seeded {len(bars)} {tf}")
        self.cvd.mark_live()
        self.ticker = self.client.ticker_24h(self.symbol)
        prem = self.client.premium_index(self.symbol)
        self.funding.seed_hist(
            self.client.funding_history(self.symbol, int(get("binance.funding_hist_limit", 200)))
        )
        self.funding.update_live(
            prem["last_funding"], prem["ts"], prem["mark"], prem["next_funding_time"]
        )
        oi = self.client.open_interest(self.symbol)
        self.oi.update(oi["oi"], oi["ts"])
        try:
            hist = self.client.open_interest_hist(
                self.symbol, "5m", int(get("binance.oi_hist_limit", 100))
            )
            self.oi.seed_hist(hist)
        except BinanceRESTError as exc:
            log.warning("oi hist: %s", exc)
        try:
            ls = self.client.global_ls_ratio(self.symbol, "5m", 30)
            if ls:
                self.ls_account_ratio = ls[-1]["ls_ratio"]
        except BinanceRESTError:
            pass
        try:
            tp = self.client.top_ls_position(self.symbol, "5m", 12)
            if tp:
                self.top_pos_ratio = tp[-1]["ls_ratio"]
        except BinanceRESTError:
            pass
        seed_n = int(get("binance.agg_trade_seed", 500))
        for t in self.client.agg_trades(self.symbol, seed_n):
            rec = self.trades.ingest_rest(t)
            if rec:
                self.cvd.on_trade(rec)
                self.foot.on_trade(rec)
        if self.trades.last_price:
            self._status("price", "live")
            self._status("trades", "live")
        self._status("oi", "live")
        self._status("funding", "live")
        self._status("liquidation", "idle", "waiting for observed force-orders")
        try:
            levels = int(get("orderbook.depth_stream_levels", 20))
            snap = self.client.depth(self.symbol, limit=levels)
            self.orderbook.on_depth(snap)
            if self.orderbook.bids and self.orderbook.asks:
                self._status("orderbook", "live", "REST depth seed")
        except Exception as exc:
            log.warning("depth seed: %s", exc)
            self._status("orderbook", "stale", str(exc))

    # ------------------------------------------------------------------
    def _on_ws_status(self, key: str, msg: str) -> None:
        st = "live" if msg == "live" else ("connecting" if msg == "connecting" else "stale")
        target = key if key in self.status else "ws"
        self._status(target, st, msg)
        if key == "ws":
            if msg == "live":
                self.liqs.socket_ok = True
                if self.status["liquidation"]["state"] == "DOWN":
                    self._status("liquidation", "idle", "socket up, no event yet")
            elif st == "stale":
                self.liqs.socket_ok = False
                self._status("liquidation", "stale", msg)

    def _on_ws(self, stream: str, data: dict) -> None:
        ev = data.get("e")
        try:
            if ev == "aggTrade" or stream.endswith("@aggTrade"):
                rec = self.trades.ingest_ws(data)
                if rec:
                    self.cvd.on_trade(rec)
                    self.foot.on_trade(rec)
                    self._status("trades", "live")
                    self._status("price", "live")
            elif ev == "kline" or "@kline_" in stream:
                k = data.get("k") or data
                interval = normalize_interval(k.get("i") or stream.split("@kline_")[-1])
                self.structure.on_kline(interval, k)
                self._status("kline", "live")
            elif ev == "markPriceUpdate" or "markPrice" in stream:
                from src.utils import safe_float

                self.funding.update_live(
                    safe_float(data.get("r")),
                    int(data.get("E") or now_ms()),
                    safe_float(data.get("p")),
                    int(data.get("T") or 0),
                )
                if not self.trades.last_price:
                    self.trades.last_price = safe_float(data.get("p"))
                self._status("funding", "live")
                self._status("price", "live")
            elif ev == "24hrTicker" or stream.endswith("@ticker"):
                from src.utils import safe_float

                self.ticker = {
                    "last": safe_float(data.get("c")),
                    "open": safe_float(data.get("o")),
                    "high": safe_float(data.get("h")),
                    "low": safe_float(data.get("l")),
                    "change_pct": safe_float(data.get("P")),
                    "quote_volume": safe_float(data.get("q")),
                    "ts": int(data.get("E") or 0),
                }
                self._status("price", "live")
            elif ev == "forceOrder" or "forceOrder" in stream:
                rec = self.liqs.ingest_ws(data, self.symbol)
                if rec:
                    self._status("liquidation", "live", f"{rec['liq_of']} @ {rec['price']}")
            elif ev == "depthUpdate" or "@depth" in stream:
                self.orderbook.on_depth(data)
                self._status("orderbook", "live")
        except Exception:
            log.exception("ws handle %s", stream)

    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        oi_every = float(get("binance.oi_poll_s", 3))
        prem_every = float(get("binance.premium_poll_s", 5))
        tick_every = float(get("binance.ticker_poll_s", 10))
        hist_every = float(get("binance.hist_poll_s", 60))
        last_oi = last_prem = last_tick = last_hist = 0.0
        while not self._stop.is_set():
            now = time.time()
            try:
                if now - last_oi >= oi_every:
                    d = self.client.open_interest(self.symbol)
                    self.oi.update(d["oi"], d["ts"])
                    self._status("oi", "live")
                    last_oi = now
                if now - last_prem >= prem_every:
                    p = self.client.premium_index(self.symbol)
                    self.funding.update_live(
                        p["last_funding"], p["ts"], p["mark"], p["next_funding_time"]
                    )
                    self._status("funding", "live")
                    last_prem = now
                if now - last_tick >= tick_every:
                    self.ticker = self.client.ticker_24h(self.symbol)
                    last_tick = now
                if now - last_hist >= hist_every:
                    self._poll_hist()
                    last_hist = now
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("poll: %s", exc)
            self._stop.wait(0.4)

    def _poll_hist(self) -> None:
        try:
            ls = self.client.global_ls_ratio(self.symbol, "5m", 12)
            if ls:
                self.ls_account_ratio = ls[-1]["ls_ratio"]
        except Exception:
            pass
        try:
            tp = self.client.top_ls_position(self.symbol, "5m", 6)
            if tp:
                self.top_pos_ratio = tp[-1]["ls_ratio"]
        except Exception:
            pass

    def _score_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._recompute()
                if time.time() - self.last_flush >= float(get("storage.flush_every_s", 15)):
                    self._flush()
                    self.last_flush = time.time()
            except Exception:
                log.exception("score loop")
            self._stop.wait(1.0)

    def _recompute(self) -> None:
        snap = self._features()
        scores = self.scorer.compute(snap)
        scores["gates"] = evaluate_gates(snap, scores)
        state = self.states.update(scores, snap)
        text = interpret(self.symbol, snap, scores, state, self.states.reason)
        with self._lock:
            self.last_scores = scores
            self._narrative = text
            self.score_hist.append(
                {
                    "ts": now_ms(),
                    "long_setup": scores["long_setup"]["total"],
                    "long_confirm": scores["long_confirm"]["total"],
                    "short_setup": scores["short_setup"]["total"],
                    "short_confirm": scores["short_confirm"]["total"],
                    "cascade_long": scores["cascade_long"],
                    "cascade_short": scores["cascade_short"],
                    "state": state,
                    "price": snap.get("price"),
                }
            )
            cap = int(get("buffers.max_score_rows", 2500))
            if len(self.score_hist) > cap:
                self.score_hist = self.score_hist[-cap:]

    def _features(self) -> dict:
        tf = self.timeframe
        price = self.trades.last_price or (self.ticker.get("last") if self.ticker else 0.0)
        stats3 = self.trades.window_stats(180_000)
        bars = self.structure.last_n(tf, 8)
        progress = 0.0
        if len(bars) >= 2:
            progress = bars[-1]["close"] - bars[0]["open"]
        atr = self.structure.atr(tf, int(get("absorption.atr_bars", 14)))
        absorption = self.foot.detect_absorption(tf, progress, atr, get("absorption", {}))
        cvd_div = self.cvd.detect_divergence(tf, get("divergence", {}))
        structure = self.structure.analyze(
            tf, int(get("divergence.swing_lookback", 5)), float(get("scoring_refs.near_extreme_pct", 0.15))
        )
        oi_s = self.oi.snapshot()
        fund_s = self.funding.snapshot()
        ob_s = self.orderbook.imbalance(
            float(get("orderbook.price_range_pct", 0.5)),
            float(get("orderbook.thin_wall_ratio", 0.20)),
        )
        taker_1m = self.trades.taker_ratio(60_000, "1m")
        taker_5m = self.trades.taker_ratio(5 * 60_000, "5m")
        return {
            "price": price,
            "change_24h_pct": (self.ticker or {}).get("change_pct", 0.0),
            "oi": oi_s,
            "oi_chg_15m_pct": oi_s["chg_15m_pct"],
            "price_chg_1m_pct": self.structure.change_pct("1m", 1),
            "price_chg_5m_pct": self.structure.change_pct("5m", 1),
            "price_chg_15m_pct": self.structure.change_pct("15m", 1),
            "funding": fund_s["funding"],
            "funding_pctile": fund_s["funding_pctile"],
            "ls_account_ratio": self.ls_account_ratio,
            "top_pos_ratio": self.top_pos_ratio,
            "cvd": self.cvd.last_for(tf),
            "cvd_chg_5m": self.cvd.change_from_series(tf, 5 * 60_000),
            "cvd_chg_15m": self.cvd.change_from_series(tf, 15 * 60_000),
            "cvd_div": cvd_div,
            "delta_3m": stats3["delta"],
            "absorption": absorption,
            "liq_5m": self.liqs.stats(5 * 60_000),
            "liq_15m": self.liqs.stats(15 * 60_000),
            "liq_baseline": self.liqs.baseline(15 * 60_000, 4 * 60 * 60_000),
            "oi_value": float(oi_s.get("oi_value") or 0.0),
            "structure": structure,
            "orderbook": ob_s,
            "taker_ratio_1m": taker_1m,
            "taker_ratio_5m": taker_5m,
        }

    def _flush(self) -> None:
        if self.storage is None:
            return
        sym = self.symbol
        ts = now_ms()
        if self.last_scores:
            row = {
                "symbol": [sym],
                "ts": [ts],
                "long_setup": [self.last_scores["long_setup"]["total"]],
                "long_confirm": [self.last_scores["long_confirm"]["total"]],
                "short_setup": [self.last_scores["short_setup"]["total"]],
                "short_confirm": [self.last_scores["short_confirm"]["total"]],
                "cascade_long": [self.last_scores["cascade_long"]],
                "cascade_short": [self.last_scores["cascade_short"]],
                "state": [self.states.state],
            }
            self.storage.upsert_df("scores", pd.DataFrame(row))
        if self.oi.current:
            self.storage.upsert_df(
                "oi",
                pd.DataFrame(
                    [{"symbol": sym, "ts": self.oi.last_ts, "oi": self.oi.current, "oi_value": self.oi.current_value}]
                ),
            )
        if self.funding.last_ts:
            self.storage.upsert_df(
                "funding",
                pd.DataFrame(
                    [
                        {
                            "symbol": sym,
                            "ts": self.funding.last_ts,
                            "funding_rate": self.funding.current,
                            "mark": self.funding.mark,
                        }
                    ]
                ),
            )
        self.storage.upsert_df(
            "cvd",
            pd.DataFrame(
                [{"symbol": sym, "ts": ts, "cvd": self.cvd.cvd, "buy_vol": self.trades.buy_vol, "sell_vol": self.trades.sell_vol}]
            ),
        )
        # persist closed 15m candles (last 5)
        bars = self.structure.last_n("15m", 5)
        if bars:
            df = pd.DataFrame(
                [
                    {
                        "symbol": sym,
                        "interval": "15m",
                        "open_time": b["open_time"],
                        "open": b["open"],
                        "high": b["high"],
                        "low": b["low"],
                        "close": b["close"],
                        "volume": b["volume"],
                        "closed": bool(b.get("closed", True)),
                    }
                    for b in bars
                ]
            )
            self.storage.upsert_df("candles", df)
        ev = self.liqs.window(20_000)
        if ev:
            self.storage.upsert_df(
                "liquidations",
                pd.DataFrame(
                    [
                        {
                            "symbol": sym,
                            "ts": e["ts"],
                            "side": e["liq_of"],
                            "qty": e["qty"],
                            "price": e["price"],
                            "notional": e["notional"],
                        }
                        for e in ev
                    ]
                ),
            )

    def _status(self, key: str, state: str, detail: str = "") -> None:
        self.status[key] = {"state": state, "ts": now_ms(), "detail": detail}

    def _health(self) -> dict:
        now = now_ms()
        stale_cfg = get("stale_after_s", {})
        out = {}
        for k, v in self.status.items():
            age = (now - v["ts"]) / 1000 if v["ts"] else 1e9
            st = v["state"]
            if k == "liquidation":
                if not self.ws.connected:
                    st = "DOWN"
                elif st == "live":
                    pass
                else:
                    st = "IDLE"
            elif k == "ws":
                st = "LIVE" if self.ws.connected else ("STALE" if self.running else "DOWN")
            else:
                limit = float(stale_cfg.get(k, 20))
                if st in ("live", "LIVE") and age > limit:
                    st = "STALE"
                st = st.upper()
            out[k] = {"state": st, "age_s": round(age, 1), "detail": v.get("detail", "")}
        return out

    def snapshot(self) -> dict:
        with self._lock:
            scores = self.last_scores or {
                "long_setup": {"total": 0, "components": []},
                "short_setup": {"total": 0, "components": []},
                "long_confirm": {"total": 0, "components": []},
                "short_confirm": {"total": 0, "components": []},
                "cascade_long": 0,
                "cascade_short": 0,
                "squeeze": {"long_squeeze": False, "short_squeeze": False},
                "gates": {"trade_status": "WAIT"},
            }
            feat = self._features()
            fp = self.foot.levels(self.timeframe, max_levels=int(get("footprint.max_levels", 40)))
            stacks = self.foot.stacked_imbalance(
                fp,
                float(get("footprint.stacked_imbalance_ratio", 3)),
                int(get("footprint.stacked_min_levels", 3)),
            )
            return {
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "running": self.running,
                "meta": self.meta,
                "features": feat,
                "scores": scores,
                "state": self.states.state,
                "state_reason": self.states.reason,
                "trade_status": (scores.get("gates") or {}).get("trade_status", "WAIT"),
                "gates": scores.get("gates") or {},
                "state_history": list(self.states.history[-40:]),
                "score_hist": list(self.score_hist[-400:]),
                "klines": {
                    tf: self.structure.last_n(tf, 180)
                    for tf in dict.fromkeys(
                        [
                            "1m",
                            "5m",
                            "15m",
                            "1h",
                            "4h",
                            "1d",
                            *list(KLINE_INTERVALS),
                            self.timeframe,
                            *self.structure.klines.keys(),
                        ]
                    )
                },
                "oi_hist": self.oi.points.last(300),
                "funding_hist": self.funding.points.last(200),                "cvd_tf": self.cvd.series(self.timeframe),
                "liq_events": self.liqs.window(60 * 60_000),
                "footprint": fp,
                "footprint_stacks": stacks,
                "health": self._health(),
                "narrative": self._narrative,
                "error": self.last_error,
                "ws_url": self.ws.url,
                "ws_reconnects": self.ws.reconnects,
            }

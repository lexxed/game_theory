"""Jupyter dashboard: ipywidgets + Plotly. Analysis only."""

from __future__ import annotations

import html as html_lib
import importlib
import json
import sys
import threading
import time
import traceback
from pathlib import Path

import ipywidgets as W
import pandas as pd
import plotly.graph_objects as go
from IPython.display import Javascript, display

from src import grok_interface
from src.market_data import MarketSession

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Keep a local copy so a stale Jupyter `src.utils` cannot break START.
_TF_ALIAS = {"4H": "4h", "24H": "1d", "24h": "1d", "1H": "1h", "1D": "1d"}


def _tf(tf: str) -> str:
    raw = (tf or "").strip()
    return _TF_ALIAS.get(raw) or _TF_ALIAS.get(raw.lower(), raw.lower())


def format_copy_snapshot(snap: dict, symbol_override: str = "", grok_comment: str = "") -> str:
    """Plain-text dump of STATE, KPIs, and scores for clipboard."""
    from datetime import datetime, timezone

    f = snap.get("features") or {}
    oi = f.get("oi") or {}
    liq = f.get("liq_15m") or {}
    sc = snap.get("scores") or {}
    st = f.get("structure") or {}
    ab = f.get("absorption") or {}
    div = f.get("cvd_div") or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def card_lines(title: str, card: dict) -> list[str]:
        lines = [f"{title}: {card.get('total', 0):.1f}/100"]
        for c in card.get("components") or []:
            lines.append(
                f"  {c.get('name')}: {c.get('points', 0):.1f}/{c.get('weight', 0):.0f}"
                f"  |  {c.get('reason', '')}"
            )
        return lines

    symbol = (symbol_override or snap.get("symbol") or "").strip().upper()
    lines = [
        f"SNAPSHOT  {symbol}  {snap.get('timeframe')}  {now}",
        f"SYMBOL  {symbol}   TF  {snap.get('timeframe')}",
        f"STATE   {snap.get('state')}",
        f"REASON  {snap.get('state_reason')}",
        "",
        "KPIs",
        f"  PRICE           {f.get('price')}",
        f"  24H             {f.get('change_24h_pct')}%",
        f"  OI              {oi.get('oi')}",
        f"  OI 1m/5m/15m/1h {oi.get('chg_1m_pct')} / {oi.get('chg_5m_pct')} / {oi.get('chg_15m_pct')} / {oi.get('chg_1h_pct')} %",
        f"  FUNDING         {f.get('funding')}   pctile {f.get('funding_pctile')}",
        f"  CVD             {f.get('cvd')}   Δ5m {f.get('cvd_chg_5m')}   Δ15m {f.get('cvd_chg_15m')}",
        f"  DELTA 3m        {f.get('delta_3m')}",
        f"  LIQ 15m L/S     {liq.get('long_notional')} / {liq.get('short_notional')}   n={liq.get('long_n')}/{liq.get('short_n')}",
        f"  LS ACCOUNT      {f.get('ls_account_ratio')}",
        "",
        *card_lines("LONG TRAP SETUP", sc.get("long_setup") or {}),
        *card_lines("LONG TRAP CONFIRM", sc.get("long_confirm") or {}),
        *card_lines("SHORT TRAP SETUP", sc.get("short_setup") or {}),
        *card_lines("SHORT TRAP CONFIRM", sc.get("short_confirm") or {}),
        "",
        f"TRADE STATUS        {(snap.get('trade_status') or (sc.get('gates') or {}).get('trade_status'))}",
        f"TRADE REASON        {(sc.get('gates') or {}).get('trade_status_reason')}",
        f"LONG LIQ EVENT      {(sc.get('gates') or {}).get('long_liq_event')}  level={(sc.get('gates') or {}).get('long_liq_level')}",
        f"LONG FORCED FLOW    {(sc.get('gates') or {}).get('long_forced_flow')}",
        f"LONG TRAP SETUP     {(sc.get('gates') or {}).get('long_vulnerability')}  CONFIRM={(sc.get('gates') or {}).get('long_trap_confirmation')}",
        f"LONG SQUEEZE        {(sc.get('gates') or {}).get('long_squeeze')}",
        f"SHORT LIQ EVENT     {(sc.get('gates') or {}).get('short_liq_event')}  level={(sc.get('gates') or {}).get('short_liq_level')}",
        f"SHORT FORCED FLOW   {(sc.get('gates') or {}).get('short_forced_flow')}",
        f"SHORT TRAP SETUP    {(sc.get('gates') or {}).get('short_vulnerability')}  CONFIRM={(sc.get('gates') or {}).get('short_trap_confirmation')}",
        f"SHORT SQUEEZE       {(sc.get('gates') or {}).get('short_squeeze')}",
        f"CASCADE INTENSITY L/S  {sc.get('cascade_long')} / {sc.get('cascade_short')}  (price/OI/CVD only, not a cascade)",
        f"CASCADE GATE L/S    {(sc.get('gates') or {}).get('long_cascade')} / {(sc.get('gates') or {}).get('short_cascade')}",
        f"SQUEEZE DETAIL      {sc.get('squeeze')}",
        f"CVD DIV  {div.get('reason')}",
        f"ABSORB   {ab.get('reason')}",
        f"STRUCT   {st.get('reason')}",
        "",
        (sc.get("gates") or {}).get("explanation_text") or "",
        "",
        "LIMITS: OI is not side-identified. Funding is not positioning.",
        "CVD is aggressive flow. Liquidations are OBSERVED force-orders only.",
        "A liquidation EVENT is not forced-flow, a trap, or a squeeze.",
        "High setup is not a price forecast.",
        "",
        "GROK COMMENT",
        grok_comment.strip() if (grok_comment or "").strip() else "(none — click Grok comment first)",
    ]
    return "\n".join(str(x) for x in lines)


def _to_clipboard(text: str) -> str:
    """Best-effort copy. Always also show the text in the dashboard box."""
    try:
        display(Javascript(f"navigator.clipboard.writeText({json.dumps(text)})"))
    except Exception:
        pass
    try:
        import subprocess

        subprocess.run(["clip"], input=text, text=True, encoding="utf-8", check=False)
        return "Copied to Windows clipboard. Also shown below."
    except Exception:
        return "Clipboard helper failed. Select the text below and Ctrl+C."


def _yn(flag: bool) -> str:
    return "YES" if flag else "NO"


def _gates_html(s: dict) -> str:
    g = s.get("gates") or (s.get("scores") or {}).get("gates") or {}
    status = s.get("trade_status") or g.get("trade_status") or "WAIT"
    expl = html_lib.escape(g.get("explanation_text") or g.get("trade_status_reason") or "")
    expl_html = expl.replace("\n", "<br>")
    return f"""
    <div style="font-family:Segoe UI,system-ui,sans-serif;margin:8px 0">
      {_card("TRADE STATUS", status)}
      <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:8px">
        {_card("LONG LIQ EVENT", _yn(bool(g.get("long_liq_event"))))}
        {_card("LONG FORCED FLOW", _yn(bool(g.get("long_forced_flow"))))}
        {_card("LONG TRAP SETUP", f"{g.get('long_vulnerability', 0):.0f}/100")}
        {_card("LONG TRAP CONFIRM", _yn(bool(g.get("long_trap_confirmation"))))}
        {_card("LONG SQUEEZE", _yn(bool(g.get("long_squeeze"))))}
        {_card("SHORT LIQ EVENT", _yn(bool(g.get("short_liq_event"))))}
        {_card("SHORT FORCED FLOW", _yn(bool(g.get("short_forced_flow"))))}
        {_card("SHORT TRAP SETUP", f"{g.get('short_vulnerability', 0):.0f}/100")}
        {_card("SHORT TRAP CONFIRM", _yn(bool(g.get("short_trap_confirmation"))))}
        {_card("SHORT SQUEEZE", _yn(bool(g.get("short_squeeze"))))}
        {_card("CASCADE INTENSITY L/S", f"{g.get('cascade_long_intensity', 0):.0f} / {g.get('cascade_short_intensity', 0):.0f}")}
        {_card("CASCADE GATE L/S", f"{_yn(bool(g.get('long_cascade')))} / {_yn(bool(g.get('short_cascade')))}")}
      </div>
      <div style="font-size:11px;color:#333;margin-top:8px;white-space:normal;line-height:1.35">
        {expl_html}
      </div>
      <div style="font-size:11px;color:#555;margin-top:6px">
        A liquidation EVENT is not forced-flow. Confirmation is a structure+flow GATE.
        Confirm score is diagnostic only. Cascade intensity is price/OI/CVD, not a cascade.
        Crowding is a PROXY, not OI positioning. Analysis only — no orders.
      </div>
    </div>
    """


def _backtest_html(symbol: str, tf: str, col: str, thresh: float, n_scores: int, n_ev: int, summary) -> str:
    head = (
        f"<div style='font-family:Segoe UI,system-ui,sans-serif;font-size:13px'>"
        f"<b>BACKTEST</b> {symbol} {tf} &nbsp; {col} ≥ {thresh:g} &nbsp; "
        f"(session snapshots n={n_scores}, events with forward bars n={n_ev})<br>"
        f"<span style='color:#555'>pct_price_down = P(close lower after that horizon). "
        f"Setup ≥ 60 is vulnerability, not a confirmed short. Not a proven edge.</span>"
    )
    if summary is None or (hasattr(summary, "empty") and summary.empty) or n_ev == 0:
        return head + "<div style='margin-top:8px'>No events with enough forward candles. Keep START running, then try again.</div></div>"
    cols = [c for c in ["horizon", "n", "pct_price_down", "pct_price_up", "avg_return", "median_return", "avg_mfe", "avg_mae"] if c in summary.columns]
    body = ["<table style='border-collapse:collapse;margin-top:8px;font-size:12px'><tr>"]
    for c in cols:
        body.append(f"<th style='border:1px solid #ccc;padding:4px 8px;text-align:left'>{c}</th>")
    body.append("</tr>")
    for _, row in summary.iterrows():
        body.append("<tr>")
        for c in cols:
            v = row.get(c)
            if c in ("pct_price_down", "pct_price_up", "avg_return", "median_return", "avg_mfe", "avg_mae") and v == v and v is not None:
                cell = f"{100*float(v):.1f}%" if c.startswith("pct_") else f"{100*float(v):+.2f}%"
            elif v != v or v is None:
                cell = "—"
            else:
                cell = str(v)
            body.append(f"<td style='border:1px solid #ccc;padding:4px 8px'>{cell}</td>")
        body.append("</tr>")
    body.append("</table></div>")
    return head + "".join(body)


def _kpi_html(s: dict) -> str:
    f = s.get("features") or {}
    oi = f.get("oi") or {}
    liq = f.get("liq_15m") or {}
    px = f.get("price") or 0
    return f"""
    <div style="font-family:Segoe UI,system-ui,sans-serif;display:flex;flex-wrap:wrap;gap:10px">
      {_card('PRICE', f'{px:,.6g}')}
      {_card('24H', f"{f.get('change_24h_pct',0):+.2f}%")}
      {_card('OI', f"{oi.get('oi',0):,.2f}")}
      {_card('OI 15m', f"{oi.get('chg_15m_pct',0):+.3f}%")}
      {_card('FUNDING', f"{f.get('funding',0):.6f}")}
      {_card('CVD', f"{f.get('cvd',0):+.4g}")}
      {_card('DELTA 3m', f"{f.get('delta_3m',0):+.4g}")}
      {_card('LIQ 15m L/S', f"${liq.get('long_notional',0):,.0f} / ${liq.get('short_notional',0):,.0f}")}
    </div>
    """


def _card(title: str, val: str) -> str:
    return (
        f'<div style="min-width:120px;padding:8px 12px;border:1px solid #ccc;'
        f'border-radius:8px;background:#fafafa">'
        f'<div style="font-size:11px;color:#666">{title}</div>'
        f'<div style="font-size:16px;font-weight:650">{val}</div></div>'
    )


def _health_html(h: dict) -> str:
    def badge(name, rec):
        st = (rec or {}).get("state", "?")
        color = {
            "LIVE": "#0a0",
            "IDLE": "#a80",
            "STALE": "#c60",
            "DOWN": "#c00",
            "CONNECTING": "#06c",
        }.get(st, "#444")
        return (
            f'<span style="margin-right:10px"><b>{name}</b> '
            f'<span style="color:{color}">{st}</span></span>'
        )

    return (
        "<div style='font-family:Segoe UI,system-ui,sans-serif;font-size:13px'>"
        "DATA STATUS: "
        + badge("Price", h.get("price"))
        + badge("Trades", h.get("trades"))
        + badge("OI", h.get("oi"))
        + badge("Funding", h.get("funding"))
        + badge("Liquidation", h.get("liquidation"))
        + badge("Book", h.get("orderbook"))
        + badge("WS", h.get("ws"))
        + "</div>"
    )


def _score_html(title: str, setup: dict, confirm: dict, state: str) -> str:
    def rows(card):
        out = []
        for c in card.get("components") or []:
            out.append(
                f"<tr><td>{c['name']}</td>"
                f"<td style='text-align:right'>{c['points']:.1f}/{c['weight']:.0f}</td>"
                f"<td style='color:#555;font-size:11px'>{c['reason']}</td></tr>"
            )
        return "".join(out)

    return f"""
    <div style="font-family:Segoe UI,system-ui,sans-serif;border:1px solid #bbb;border-radius:8px;padding:10px;margin:6px 0">
      <h3 style="margin:0 0 6px 0">{title}</h3>
      <div><b>SETUP</b> {setup.get('total',0):.0f} / 100 &nbsp;&nbsp;
           <b>CONFIRMATION</b> {confirm.get('total',0):.0f} / 100</div>
      <div style="margin:4px 0 8px 0">STATE: <b>{state}</b></div>
      <table style="width:100%;font-size:12px;border-collapse:collapse">
        {rows(setup)}
      </table>
      <div style="margin-top:8px;font-size:12px;color:#333"><b>Confirmation components</b></div>
      <table style="width:100%;font-size:12px;border-collapse:collapse">
        {rows(confirm)}
      </table>
    </div>
    """


def _fp_html(rows: list[dict], n: int = 18) -> str:
    if not rows:
        return "<i>No footprint yet — waiting for trades on this candle.</i>"
    body = []
    for r in rows[:n]:
        d = r["delta"]
        col = "#0a0" if d > 0 else "#c00" if d < 0 else "#444"
        body.append(
            f"<tr><td>{r['price']:.6g}</td>"
            f"<td style='text-align:right'>{r['bid']:.4g}</td>"
            f"<td style='text-align:right'>{r['ask']:.4g}</td>"
            f"<td style='text-align:right;color:{col}'>{d:+.4g}</td></tr>"
        )
    return (
        "<div style='font-family:Consolas,monospace;font-size:12px'>"
        "<b>FOOTPRINT</b> (ask = aggressive buy, bid = aggressive sell)<br>"
        "<table><tr><th>PRICE</th><th>BID</th><th>ASK</th><th>DELTA</th></tr>"
        + "".join(body)
        + "</table></div>"
    )


def _layout(title: str, height: int) -> dict:
    return dict(
        height=height,
        margin=dict(l=40, r=10, t=30, b=30),
        title=title,
        legend=dict(orientation="h"),
        xaxis_rangeslider_visible=False,
        template="plotly_white",
    )


def _fig_html(fig: go.Figure, height: int) -> str:
    """
    ipywidgets.HTML strips <script>, so Plotly never draws and the pane
    collapses. A srcdoc iframe is allowed to run plotly.js.
    """
    fig.update_layout(height=height, autosize=True)
    inner = fig.to_html(
        include_plotlyjs="cdn",
        full_html=True,
        config={"displayModeBar": False, "responsive": True},
    )
    escaped = html_lib.escape(inner, quote=True)
    return (
        f'<iframe srcdoc="{escaped}" '
        f'style="width:100%;height:{height + 24}px;border:1px solid #ddd;'
        f'border-radius:6px;display:block;background:#fff" '
        f'sandbox="allow-scripts allow-same-origin"></iframe>'
    )


def _empty_chart(title: str, height: int = 240) -> W.HTML:
    placeholder = (
        f'<div style="height:{height}px;border:1px dashed #bbb;border-radius:8px;'
        f'padding:14px;background:#f7f7f7;font-family:Segoe UI,system-ui,sans-serif">'
        f"<b>{title}</b>"
        f'<div style="color:#555;margin-top:8px">Click <b>START</b> — '
        f"candles and live series appear here after the REST seed.</div></div>"
    )
    w = W.HTML(placeholder)
    w.layout.min_height = f"{height}px"
    w._chart_height = height
    w._chart_title = title
    return w


def _set_chart(widget: W.HTML, fig: go.Figure) -> None:
    height = int(getattr(widget, "_chart_height", 240))
    title = getattr(widget, "_chart_title", None)
    if title and not fig.layout.title.text:
        fig.update_layout(title=title)
    widget.value = _fig_html(fig, height)


class Dashboard:
    def __init__(self):
        self.engine = MarketSession()
        self._timer: threading.Timer | None = None
        self._last_chart = 0.0
        self._last_grok = ""
        self._lock = threading.Lock()

        self.symbol = W.Text(value="BTCUSDT", description="SYMBOL", style={"description_width": "70px"})
        self.tf = W.Dropdown(
            options=[
                ("1m", "1m"),
                ("5m", "5m"),
                ("15m", "15m"),
                ("1h", "1h"),
                ("4H", "4h"),
                ("24H", "1d"),
            ],
            value="15m",
            description="TIMEFRAME",
            style={"description_width": "90px"},
        )
        self.btn_start = W.Button(description="START", button_style="success")
        self.btn_stop = W.Button(description="STOP", button_style="danger")
        self.btn_grok = W.Button(description="Grok comment", button_style="info")
        self.btn_copy = W.Button(description="Copy snapshot")
        self.btn_bt = W.Button(description="Backtest", button_style="warning")
        self.bt_score = W.Dropdown(
            options=["long_setup", "short_setup", "long_confirm", "short_confirm"],
            value="long_setup",
            description="SCORE",
            style={"description_width": "60px"},
            layout=W.Layout(width="220px"),
        )
        self.bt_min = W.FloatText(value=60.0, description="MIN", style={"description_width": "40px"}, layout=W.Layout(width="140px"))
        self.bt_out = W.HTML("<i>Backtest uses this session’s snapshots + chart timeframe candles. Descriptive only.</i>")
        self.copy_box = W.Textarea(
            value="",
            placeholder="Click Copy snapshot to fill this box (and the clipboard).",
            layout=W.Layout(width="100%", height="180px"),
        )
        self.health = W.HTML()
        self.kpis = W.HTML()
        self.gates_box = W.HTML()
        self.long_box = W.HTML()
        self.short_box = W.HTML()
        self.game = W.HTML()
        self.fp = W.HTML()
        self.grok_out = W.HTML(
            "<i>Optional. Applies this engine's state labels and metric definitions. "
            "Does not re-score. Python remains the source of STATE and numbers.</i>"
        )
        self.log = W.HTML()

        # Regular Plotly figures rendered as HTML — Plotly 6 FigureWidget
        # requires anywidget and breaks if the kernel imported plotly first.
        self.fig_px = _empty_chart("Price", 320)
        self.fig_oi = _empty_chart("Open Interest", 240)
        self.fig_cvd = _empty_chart("CVD", 240)
        self.fig_fund = _empty_chart("Funding", 220)
        self.fig_liq = _empty_chart("Observed liquidations (notional)", 220)
        self.fig_score = _empty_chart("Trap scores", 260)
        self.fig_state = _empty_chart("State timeline", 180)

        self.btn_start.on_click(self._on_start)
        self.btn_stop.on_click(self._on_stop)
        self.btn_grok.on_click(self._on_grok)
        self.btn_copy.on_click(self._on_copy)
        self.btn_bt.on_click(self._on_backtest)
        self.tf.observe(self._on_tf_change, names="value")

        controls = W.HBox(
            [self.symbol, self.tf, self.btn_start, self.btn_stop, self.btn_copy, self.btn_grok]
        )
        bt_row = W.HBox([self.bt_score, self.bt_min, self.btn_bt])
        scores = W.HBox([self.long_box, self.short_box])
        charts = W.VBox(
            [
                self.fig_px,
                self.fig_oi,
                self.fig_cvd,
                self.fig_fund,
                self.fig_liq,
                self.fp,
                self.fig_score,
                self.fig_state,
            ]
        )
        self.widget = W.VBox(
            [
                W.HTML("<h2>Binance USD-M Game Theory Dashboard</h2>"
                       "<div style='color:#444'>Public market data only. No orders. "
                       "Scores are deterministic. High score ≠ prediction.</div>"),
                controls,
                self.health,
                self.kpis,
                self.gates_box,
                W.HTML("<h3>Backtest (this session — no orders)</h3>"),
                bt_row,
                self.bt_out,
                self.copy_box,
                scores,
                W.HTML("<h3>Game-theory panel</h3>"),
                self.game,
                W.HTML("<h3>Charts</h3>"),
                charts,
                W.HTML("<h3>Grok comment (engine rules — not narration)</h3>"),
                self.grok_out,
                self.log,
            ]
        )

    def _on_start(self, _=None) -> None:
        try:
            self.log.value = "<i>Starting… seeding REST + opening WebSocket</i>"
            self.engine.start(self.symbol.value, self.tf.value)
            if hasattr(self.engine, "ensure_klines"):
                self.engine.ensure_klines(self.tf.value)
            # First paint on the Jupyter click thread so charts show immediately.
            self._last_chart = 0.0
            self.refresh()
            self._arm()
            self.log.value = f"<span style='color:green'>LIVE {self.engine.symbol} {self.engine.timeframe}</span>"
        except Exception as exc:
            self.log.value = f"<pre style='color:red'>{exc}\n{traceback.format_exc()}</pre>"

    def _on_tf_change(self, change) -> None:
        if change.get("name") != "value":
            return
        try:
            if hasattr(self.engine, "set_timeframe"):
                tf = self.engine.set_timeframe(change["new"])
            elif hasattr(self.engine, "ensure_klines"):
                tf = self.engine.ensure_klines(change["new"]) and _tf(change["new"])
            else:
                tf = _tf(change["new"])
                self.engine.timeframe = tf
            self._last_chart = 0.0
            self.refresh()
            if self.engine.running:
                self.log.value = (
                    f"<span style='color:green'>LIVE {self.engine.symbol} {tf}</span> "
                    f"— charts switched to {tf}"
                )
            else:
                self.log.value = f"Timeframe set to {tf}. Click START to stream."
        except Exception as exc:
            self.log.value = f"<pre style='color:red'>timeframe switch failed: {exc}\n{traceback.format_exc()}</pre>"

    def _on_stop(self, _=None) -> None:
        self.engine.stop()
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self.log.value = "Stopped."

    def _on_backtest(self, _=None) -> None:
        try:
            import backtest as bt

            col = str(self.bt_score.value)
            thresh = float(self.bt_min.value)
            scores, candles = bt.frames_from_session(self.engine)
            n_scores = 0 if scores is None or scores.empty else len(scores)
            if n_scores == 0 or col not in (scores.columns if n_scores else []):
                self.bt_out.value = (
                    "<i>No score snapshots in this session yet. Click START and wait "
                    "(scores flush ~every 15s), then Backtest.</i>"
                )
                return
            if candles is None or candles.empty or "open_time" not in candles.columns:
                self.bt_out.value = "<i>No candles loaded for this timeframe. START first.</i>"
                return
            ev, sm = bt.run_report(scores, candles, col, thresh)
            n_ev = 0 if ev is None or ev.empty else len(ev)
            self.bt_out.value = _backtest_html(
                self.engine.symbol,
                self.engine.timeframe,
                col,
                thresh,
                n_scores,
                n_ev,
                sm,
            )
            self.log.value = f"<span style='color:green'>Backtest {col} ≥ {thresh:g}: {n_ev} events</span>"
        except Exception as exc:
            self.bt_out.value = f"<pre style='color:red'>{exc}\n{traceback.format_exc()}</pre>"

    def _on_copy(self, _=None) -> None:
        try:
            snap = self.engine.snapshot()
            text = format_copy_snapshot(
                snap,
                symbol_override=self.symbol.value,
                grok_comment=self._last_grok,
            )
            self.copy_box.value = text
            msg = _to_clipboard(text)
            self.log.value = f"<span style='color:green'>{msg}</span>"
        except Exception as exc:
            self.log.value = f"<pre style='color:red'>copy failed: {exc}\n{traceback.format_exc()}</pre>"

    def _on_grok(self, _=None) -> None:
        self.grok_out.value = "<i>Sending snapshot + engine rules to local Grok CLI…</i>"

        def work():
            try:
                gi = importlib.reload(grok_interface)
                snap = self.engine.snapshot()
                text = gi.compact_snapshot(
                    snap["symbol"],
                    snap["features"],
                    snap["scores"],
                    snap["state"],
                    state_reason=snap.get("state_reason") or "",
                )
                ans = gi.ask_grok(text)
                self._last_grok = ans or ""
                self.grok_out.value = f"<pre style='white-space:pre-wrap'>{ans}</pre>"
            except Exception as exc:
                self.grok_out.value = f"<pre style='color:red'>{exc}</pre>"

        threading.Thread(target=work, daemon=True).start()

    def _arm(self) -> None:
        if self._timer:
            self._timer.cancel()

        def tick():
            try:
                self.refresh()
            except Exception:
                pass
            if self.engine.running:
                self._arm()

        self._timer = threading.Timer(1.0, tick)
        self._timer.daemon = True
        self._timer.start()

    def refresh(self) -> None:
        snap = self.engine.snapshot()
        self.health.value = _health_html(snap.get("health") or {})
        self.kpis.value = _kpi_html(snap)
        self.gates_box.value = _gates_html(snap)
        sc = snap["scores"]
        st = snap["state"]
        self.long_box.value = _score_html("LONG TRAP", sc.get("long_setup") or {}, sc.get("long_confirm") or {}, st)
        self.short_box.value = _score_html("SHORT TRAP", sc.get("short_setup") or {}, sc.get("short_confirm") or {}, st)
        nar = (snap.get("narrative") or "").replace("\n", "<br>")
        self.game.value = f"<div style='font-family:Segoe UI,system-ui,sans-serif;font-size:13px;line-height:1.35'>{nar}</div>"
        self.fp.value = _fp_html(snap.get("footprint") or [])
        now = time.time()
        if now - self._last_chart >= 2.0:
            self._update_charts(snap)
            self._last_chart = now

    def _bars_for(self, snap: dict, tf: str) -> list:
        kl = snap.get("klines") or {}
        for key in (tf, tf.lower(), tf.upper(), "4h", "1d"):
            bars = kl.get(key)
            if bars:
                return bars
        if tf == "4h":
            for key in ("4h", "4H"):
                if kl.get(key):
                    return kl[key]
        if tf in ("1d", "24h", "24H"):
            for key in ("1d", "24h", "24H"):
                if kl.get(key):
                    return kl[key]
        try:
            if hasattr(self.engine, "ensure_klines"):
                return self.engine.ensure_klines(tf) or []
            return self.engine.client.klines(self.engine.symbol, tf, limit=300)
        except Exception:
            return []

    def _update_charts(self, snap: dict) -> None:
        tf = _tf(snap.get("timeframe") or self.tf.value)
        bars = self._bars_for(snap, tf)
        x = []
        if not bars:
            fig = go.Figure()
            fig.update_layout(**_layout(f"{snap.get('symbol', '')} {tf} — no candles yet", 320))
            fig.add_annotation(text=f"No {tf} candles loaded. Click START or wait for seed.", xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
            _set_chart(self.fig_px, fig)
        if bars:
            x = [pd.to_datetime(b["open_time"], unit="ms", utc=True) for b in bars]
            fig = go.Figure(
                go.Candlestick(
                    name="price",
                    x=x,
                    open=[b["open"] for b in bars],
                    high=[b["high"] for b in bars],
                    low=[b["low"] for b in bars],
                    close=[b["close"] for b in bars],
                )
            )
            fig.update_layout(**_layout(f"{snap['symbol']} {tf}", 320))
            _set_chart(self.fig_px, fig)

        oi = snap.get("oi_hist") or []
        fig = go.Figure()
        if oi:
            ox = [pd.to_datetime(p["ts"], unit="ms", utc=True) for p in oi]
            fig.add_scatter(name="open interest", x=ox, y=[p["oi"] for p in oi], mode="lines")
        fig.update_layout(**_layout("Open Interest", 240))
        _set_chart(self.fig_oi, fig)

        cvd = snap.get("cvd_tf") or []
        fig = go.Figure()
        if cvd:
            cx = [pd.to_datetime(b["open_time"], unit="ms", utc=True) for b in cvd]
            fig.add_scatter(name="cvd", x=cx, y=[b["cvd"] for b in cvd], mode="lines")
            fig.add_scatter(name="delta", x=cx, y=[b["delta"] for b in cvd], mode="lines")
        fig.update_layout(**_layout("CVD", 240))
        _set_chart(self.fig_cvd, fig)

        fh = snap.get("funding_hist") or []
        fig = go.Figure()
        if fh:
            fx = [pd.to_datetime(p["ts"], unit="ms", utc=True) for p in fh]
            fig.add_scatter(name="funding", x=fx, y=[p["funding"] for p in fh], mode="lines")
        fig.update_layout(**_layout("Funding", 220))
        _set_chart(self.fig_fund, fig)

        liq = snap.get("liq_events") or []
        fig = go.Figure()
        if liq:
            df = pd.DataFrame(liq)
            df["t"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            g = (
                df.set_index("t")
                .groupby([pd.Grouper(freq="1min"), "liq_of"])["notional"]
                .sum()
                .unstack(fill_value=0)
            )
            idx = list(g.index)
            fig.add_scatter(name="long_liq", x=idx, y=list(g["long"]) if "long" in g else [0] * len(idx), mode="lines")
            fig.add_scatter(name="short_liq", x=idx, y=list(g["short"]) if "short" in g else [0] * len(idx), mode="lines")
        fig.update_layout(**_layout("Observed liquidations (notional)", 220))
        _set_chart(self.fig_liq, fig)

        sh = snap.get("score_hist") or []
        fig = go.Figure()
        if sh:
            sx = [pd.to_datetime(r["ts"], unit="ms", utc=True) for r in sh]
            for key in ["long_setup", "long_confirm", "short_setup", "short_confirm"]:
                fig.add_scatter(name=key, x=sx, y=[r.get(key, 0) for r in sh], mode="lines")
        fig.update_layout(**_layout("Trap scores", 260))
        _set_chart(self.fig_score, fig)

        fig = go.Figure()
        if sh:
            uniq, ids = [], []
            for r in sh:
                st = r.get("state") or "NEUTRAL"
                if st not in uniq:
                    uniq.append(st)
                ids.append(uniq.index(st))
            fig.add_scatter(name="state", x=sx, y=ids, mode="lines+markers")
            fig.update_layout(yaxis=dict(tickmode="array", ticktext=uniq, tickvals=list(range(len(uniq)))))
        fig.update_layout(**_layout("State timeline", 180))
        _set_chart(self.fig_state, fig)


_APP: Dashboard | None = None


def start_dashboard(rebuild: bool = True) -> Dashboard:
    global _APP
    try:
        import src.utils as _utils

        importlib.reload(_utils)
    except Exception:
        pass
    if rebuild or _APP is None:
        if _APP is not None:
            try:
                _APP.engine.stop()
            except Exception:
                pass
        _APP = Dashboard()
    display(_APP.widget)
    return _APP

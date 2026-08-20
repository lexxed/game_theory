"""
Gating layer on top of 0-100 component scores.

Separates, in increasing strength:
  liquidation_event   — a force-order print was observed (not a trade signal)
  trap SETUP          — vulnerability score (crowding proxy + structure, etc.)
  trap CONFIRMATION   — structure AND directional flow (not the confirm score)
  forced_flow         — meaningful liquidation + price/CVD/OI confirmation
  squeeze             — stronger forced-flow (magnitude gates)
  cascade             — intensity + meaningful same-side liq + direction

A liquidation event is NEVER enough for forced_flow, trap, squeeze, or entry.

SHORT FORCED FLOW is bullish (shorts being forced out, price/CVD up).
LONG FORCED FLOW is bearish (longs being forced out, price/CVD down).
"""

from __future__ import annotations

from config import get
from src.liquidations import classify_liquidation, oi_usdt

TRADE_STATUSES = (
    "WAIT",
    "WATCH LONG-TRAP",
    "LONG-TRAP CONFIRMING",
    "LONG FORCED-FLOW",
    "LONG SQUEEZE",
    "WATCH SHORT-TRAP",
    "SHORT-TRAP CONFIRMING",
    "SHORT FORCED-FLOW",
    "SHORT SQUEEZE",
    "CASCADE / DO NOT CHASE",
)


def _liq(snap: dict, side: str, window: str = "liq_15m") -> float:
    key = "long_notional" if side == "long" else "short_notional"
    return float((snap.get(window) or {}).get(key) or 0.0)


def _baseline_side(snap: dict, side: str) -> tuple[float, list[float]]:
    base = (snap.get("liq_baseline") or {}).get(side) or {}
    return float(base.get("median") or 0.0), list(base.get("history") or [])


def _crowd_proxy(scores: dict, side: str) -> dict:
    card = scores.get("long_setup" if side == "long" else "short_setup") or {}
    comp = next((c for c in card.get("components") or [] if c.get("name") == "crowding"), None)
    score = float((comp or {}).get("normalized") or 0.0) * 100.0
    return {
        "score": round(score, 2),
        "label": (
            "PROXY only — 0.50 funding percentile + 0.25 LS_ACCOUNT "
            "(global account ratio) + 0.25 top_pos_ratio (top-trader position-size ratio). "
            "Not long/short OI."
        ),
        "reason": (comp or {}).get("reason", ""),
    }


def _sig(snap: dict, side: str, notional: float, cfg: dict) -> dict:
    med, hist = _baseline_side(snap, side)
    return classify_liquidation(
        notional,
        oi_usdt_value=oi_usdt(snap),
        median=med,
        history=hist,
        cfg=cfg,
    )


def _fmt_pct(x: float) -> str:
    return f"{x:+.4f}%"


def _line(ok: bool, title: str, triggered: list[str], failed: list[str]) -> str:
    flag = "YES" if ok else "NO"
    bits = []
    if triggered:
        bits.append("Triggered by: " + "; ".join(triggered))
    if failed:
        bits.append("Failed: " + "; ".join(failed))
    extra = ("  " + " | ".join(bits)) if bits else ""
    return f"{title}: {flag}{extra}"


def evaluate(snap: dict, scores: dict) -> dict:
    cfg = get("gates", {})
    watch = float(cfg.get("setup_watch", 70))
    cascade_min = float(cfg.get("cascade_min_observed_notional", 100_000))
    cascade_to_oi = float(cfg.get("cascade_min_liq_to_oi", 0.001))
    cas_enter = float(get("cascade.enter", 70))

    st = snap.get("structure") or {}
    oi15 = float(snap.get("oi_chg_15m_pct") or 0.0)
    px15 = float(snap.get("price_chg_15m_pct") or 0.0)
    px5 = float(snap.get("price_chg_5m_pct") or 0.0)
    cvd5 = float(snap.get("cvd_chg_5m") or 0.0)
    cvd15 = float(snap.get("cvd_chg_15m") or 0.0)

    # Direction: 15m is the dashboard window; 5m may confirm a same-sign move.
    # A 15m decline with a tiny 5m bounce is still a decline.
    price_up = px15 > 0 or (px5 > 0 and px15 >= 0)
    price_down = px15 < 0 or (px5 < 0 and px15 <= 0)
    cvd_up = cvd5 > 0 or (cvd15 > 0 and cvd5 >= 0)
    cvd_down = cvd5 < 0 or (cvd15 < 0 and cvd5 <= 0)
    oi_down = oi15 < 0

    long_struct = bool(st.get("failed_breakout") or st.get("lost_support"))
    short_struct = bool(st.get("failed_breakdown") or st.get("lost_resistance"))

    # Trap-confirm flow must be DIRECTIONAL. A liquidation print while price
    # and CVD travel the other way is not confirmation of a trap on that side.
    long_cvd_follow = cvd_down
    short_cvd_follow = cvd_up
    long_unwind = oi_down and price_down
    short_unwind = oi_down and price_up

    long_liq = _liq(snap, "long")
    short_liq = _liq(snap, "short")
    long_liq5 = _liq(snap, "long", "liq_5m")
    short_liq5 = _liq(snap, "short", "liq_5m")
    long_liq_obs = max(long_liq, long_liq5)
    short_liq_obs = max(short_liq, short_liq5)

    long_sig = _sig(snap, "long", long_liq_obs, cfg)
    short_sig = _sig(snap, "short", short_liq_obs, cfg)

    long_liq_event = bool(long_sig["is_event"])
    short_liq_event = bool(short_sig["is_event"])

    # Meaningful same-side liq only counts as trap-flow if price is moving
    # AGAINST that side (longs liquidated into a decline, shorts into a rally).
    long_liq_flow = bool(long_sig["is_meaningful"] and price_down)
    short_liq_flow = bool(short_sig["is_meaningful"] and price_up)

    long_flow = bool(long_cvd_follow or long_unwind or long_liq_flow)
    short_flow = bool(short_cvd_follow or short_unwind or short_liq_flow)

    long_confirmed = bool(long_struct and long_flow)
    short_confirmed = bool(short_struct and short_flow)

    # Forced flow: event ≠ signal. Requires meaningful liq AND direction.
    # SHORT forced flow = shorts forced out contributing to UPWARD pressure.
    # LONG forced flow  = longs forced out contributing to DOWNWARD pressure.
    short_ff_trig, short_ff_fail = [], []
    long_ff_trig, long_ff_fail = [], []

    if short_liq_event:
        short_ff_trig.append(f"short_liq=${short_liq_obs:,.0f} level={short_sig['level']}")
    else:
        short_ff_fail.append("no short liquidation event")
    if short_sig["is_meaningful"]:
        short_ff_trig.extend(short_sig["reasons"])
    else:
        short_ff_fail.append("short liquidation not meaningful: " + "; ".join(short_sig["failed"][:3]))
    if price_up:
        short_ff_trig.append(f"price_change={_fmt_pct(px15)} (15m)")
    else:
        short_ff_fail.append(f"price not rising (15m {_fmt_pct(px15)}, 5m {_fmt_pct(px5)})")
    if cvd_up:
        short_ff_trig.append(f"CVD_change={cvd5:+.4g} (5m)")
    else:
        short_ff_fail.append(f"CVD not rising (5m {cvd5:+.4g}, 15m {cvd15:+.4g})")
    if oi_down:
        short_ff_trig.append(f"OI_change={_fmt_pct(oi15)}")
    else:
        short_ff_fail.append(f"OI not declining ({_fmt_pct(oi15)}) — OI down is the covering signature")

    if long_liq_event:
        long_ff_trig.append(f"long_liq=${long_liq_obs:,.0f} level={long_sig['level']}")
    else:
        long_ff_fail.append("no long liquidation event")
    if long_sig["is_meaningful"]:
        long_ff_trig.extend(long_sig["reasons"])
    else:
        long_ff_fail.append("long liquidation not meaningful: " + "; ".join(long_sig["failed"][:3]))
    if price_down:
        long_ff_trig.append(f"price_change={_fmt_pct(px15)} (15m)")
    else:
        long_ff_fail.append(f"price not falling (15m {_fmt_pct(px15)}, 5m {_fmt_pct(px5)})")
    if cvd_down:
        long_ff_trig.append(f"CVD_change={cvd5:+.4g} (5m)")
    else:
        long_ff_fail.append(f"CVD not falling (5m {cvd5:+.4g}, 15m {cvd15:+.4g})")
    if oi_down:
        long_ff_trig.append(f"OI_change={_fmt_pct(oi15)}")
    else:
        long_ff_fail.append(f"OI not declining ({_fmt_pct(oi15)}) — OI down is the covering signature")

    short_forced = bool(short_sig["is_meaningful"] and price_up and cvd_up and oi_down)
    long_forced = bool(long_sig["is_meaningful"] and price_down and cvd_down and oi_down)

    long_vuln = float((scores.get("long_setup") or {}).get("total") or 0.0)
    short_vuln = float((scores.get("short_setup") or {}).get("total") or 0.0)

    long_intensity = float(scores.get("cascade_long") or 0.0)
    short_intensity = float(scores.get("cascade_short") or 0.0)

    def _cascade_liq_ok(sig: dict, notion: float) -> bool:
        if not sig.get("is_meaningful"):
            return False
        if notion >= cascade_min:
            return True
        if sig.get("is_extreme"):
            return True
        if oi_usdt(snap) > 0 and float(sig.get("to_oi") or 0.0) >= cascade_to_oi:
            return True
        return False

    long_cas_trig, long_cas_fail = [], []
    short_cas_trig, short_cas_fail = [], []
    if long_intensity >= cas_enter:
        long_cas_trig.append(f"cascade_intensity={long_intensity:.1f} (price/OI/CVD, not a cascade by itself)")
    else:
        long_cas_fail.append(f"intensity {long_intensity:.1f} < {cas_enter:.0f}")
    if _cascade_liq_ok(long_sig, long_liq_obs):
        long_cas_trig.append(f"long_liq=${long_liq_obs:,.0f} {long_sig['level']}")
    else:
        long_cas_fail.append(
            f"long liq ${long_liq_obs:,.0f} is not a cascade-scale flow "
            f"(need meaningful + (notional>=${cascade_min:,.0f} or extreme / liq/OI>={cascade_to_oi*100:.2f}%))"
        )
    if price_down and cvd_down:
        long_cas_trig.append("price and CVD falling")
    else:
        long_cas_fail.append("long cascade needs falling price AND falling CVD")

    if short_intensity >= cas_enter:
        short_cas_trig.append(f"cascade_intensity={short_intensity:.1f} (price/OI/CVD, not a cascade by itself)")
    else:
        short_cas_fail.append(f"intensity {short_intensity:.1f} < {cas_enter:.0f}")
    if _cascade_liq_ok(short_sig, short_liq_obs):
        short_cas_trig.append(f"short_liq=${short_liq_obs:,.0f} {short_sig['level']}")
    else:
        short_cas_fail.append(
            f"short liq ${short_liq_obs:,.0f} is not a cascade-scale flow "
            f"(need meaningful + (notional>=${cascade_min:,.0f} or extreme / liq/OI>={cascade_to_oi*100:.2f}%))"
        )
    if price_up and cvd_up:
        short_cas_trig.append("price and CVD rising")
    else:
        short_cas_fail.append("short cascade needs rising price AND rising CVD")

    long_cascade = bool(
        long_intensity >= cas_enter
        and _cascade_liq_ok(long_sig, long_liq_obs)
        and price_down
        and cvd_down
    )
    short_cascade = bool(
        short_intensity >= cas_enter
        and _cascade_liq_ok(short_sig, short_liq_obs)
        and price_up
        and cvd_up
    )

    sq = scores.get("squeeze") or {}
    # Canonical names: short_squeeze = shorts forced out (bullish).
    # Do not honor a squeeze flag without the matching forced-flow gate.
    short_squeeze = bool(sq.get("short_squeeze")) and short_forced
    long_squeeze = bool(sq.get("long_squeeze")) and long_forced

    trade_status, trade_reason = _trade_status(
        long_cascade=long_cascade,
        short_cascade=short_cascade,
        long_squeeze=long_squeeze,
        short_squeeze=short_squeeze,
        long_forced=long_forced,
        short_forced=short_forced,
        long_confirmed=long_confirmed,
        short_confirmed=short_confirmed,
        long_vuln=long_vuln,
        short_vuln=short_vuln,
        watch=watch,
        long_liq_event=long_liq_event,
        short_liq_event=short_liq_event,
    )

    explanations = {
        "short_liq_event": {
            "ok": short_liq_event,
            "triggered_by": [f"short_liq=${short_liq_obs:,.0f}"] if short_liq_event else [],
            "failed": [] if short_liq_event else ["no observed short force-order"],
        },
        "long_liq_event": {
            "ok": long_liq_event,
            "triggered_by": [f"long_liq=${long_liq_obs:,.0f}"] if long_liq_event else [],
            "failed": [] if long_liq_event else ["no observed long force-order"],
        },
        "short_forced_flow": {
            "ok": short_forced,
            "triggered_by": short_ff_trig,
            "failed": short_ff_fail,
        },
        "long_forced_flow": {
            "ok": long_forced,
            "triggered_by": long_ff_trig,
            "failed": long_ff_fail,
        },
        "short_trap_confirmation": {
            "ok": short_confirmed,
            "triggered_by": [
                x
                for x, hit in (
                    ("failed_breakdown/lost_resistance", short_struct),
                    ("positive CVD follow", short_cvd_follow),
                    ("OI unwind into rally", short_unwind),
                    ("meaningful short-liq flow with price up", short_liq_flow),
                )
                if hit
            ],
            "failed": [
                x
                for x, hit in (
                    ("no failed_breakdown / lost_resistance", not short_struct),
                    ("no bullish CVD / unwind / meaningful short-liq flow", not short_flow),
                )
                if hit
            ],
        },
        "long_trap_confirmation": {
            "ok": long_confirmed,
            "triggered_by": [
                x
                for x, hit in (
                    ("failed_breakout/lost_support", long_struct),
                    ("negative CVD follow", long_cvd_follow),
                    ("OI unwind into decline", long_unwind),
                    ("meaningful long-liq flow with price down", long_liq_flow),
                )
                if hit
            ],
            "failed": [
                x
                for x, hit in (
                    ("no failed_breakout / lost_support", not long_struct),
                    ("no bearish CVD / unwind / meaningful long-liq flow", not long_flow),
                )
                if hit
            ],
        },
        "short_squeeze": {
            "ok": short_squeeze,
            "triggered_by": (["squeeze magnitude gates + short forced-flow"] if short_squeeze else []),
            "failed": ([] if short_squeeze else ["requires short forced-flow AND squeeze magnitude (price/OI/CVD/liq)"]),
        },
        "long_squeeze": {
            "ok": long_squeeze,
            "triggered_by": (["squeeze magnitude gates + long forced-flow"] if long_squeeze else []),
            "failed": ([] if long_squeeze else ["requires long forced-flow AND squeeze magnitude (price/OI/CVD/liq)"]),
        },
        "short_cascade": {
            "ok": short_cascade,
            "triggered_by": short_cas_trig,
            "failed": short_cas_fail,
        },
        "long_cascade": {
            "ok": long_cascade,
            "triggered_by": long_cas_trig,
            "failed": long_cas_fail,
        },
    }

    explanation_text = "\n".join(
        [
            _line(short_liq_event, "SHORT LIQ EVENT", explanations["short_liq_event"]["triggered_by"], explanations["short_liq_event"]["failed"]),
            _line(short_forced, "SHORT FORCED FLOW", short_ff_trig, short_ff_fail),
            _line(short_confirmed, "SHORT TRAP CONFIRM", explanations["short_trap_confirmation"]["triggered_by"], explanations["short_trap_confirmation"]["failed"]),
            _line(short_squeeze, "SHORT SQUEEZE", explanations["short_squeeze"]["triggered_by"], explanations["short_squeeze"]["failed"]),
            _line(long_liq_event, "LONG LIQ EVENT", explanations["long_liq_event"]["triggered_by"], explanations["long_liq_event"]["failed"]),
            _line(long_forced, "LONG FORCED FLOW", long_ff_trig, long_ff_fail),
            _line(long_confirmed, "LONG TRAP CONFIRM", explanations["long_trap_confirmation"]["triggered_by"], explanations["long_trap_confirmation"]["failed"]),
            _line(long_squeeze, "LONG SQUEEZE", explanations["long_squeeze"]["triggered_by"], explanations["long_squeeze"]["failed"]),
            _line(short_cascade, "SHORT CASCADE (actual)", short_cas_trig, short_cas_fail),
            _line(long_cascade, "LONG CASCADE (actual)", long_cas_trig, long_cas_fail),
            f"CASCADE INTENSITY (price/OI/CVD only, not a cascade): long {long_intensity:.2f} / short {short_intensity:.2f}",
            f"TRADE STATUS: {trade_status} — {trade_reason}",
        ]
    )

    return {
        "long_crowding_proxy": _crowd_proxy(scores, "long"),
        "short_crowding_proxy": _crowd_proxy(scores, "short"),
        "long_vulnerability": round(long_vuln, 2),
        "short_vulnerability": round(short_vuln, 2),
        "long_trap_confirmation": long_confirmed,
        "short_trap_confirmation": short_confirmed,
        "long_trap_confirmation_score": float((scores.get("long_confirm") or {}).get("total") or 0.0),
        "short_trap_confirmation_score": float((scores.get("short_confirm") or {}).get("total") or 0.0),
        "long_structure_gate": long_struct,
        "short_structure_gate": short_struct,
        "long_flow_gate": long_flow,
        "short_flow_gate": short_flow,
        "long_liq_event": long_liq_event,
        "short_liq_event": short_liq_event,
        "long_liq_level": long_sig["level"],
        "short_liq_level": short_sig["level"],
        "long_liq_significance": long_sig,
        "short_liq_significance": short_sig,
        "long_forced_flow": long_forced,
        "short_forced_flow": short_forced,
        "long_forced_notional": long_liq,
        "short_forced_notional": short_liq,
        "long_squeeze": long_squeeze,
        "short_squeeze": short_squeeze,
        "long_cascade": long_cascade,
        "short_cascade": short_cascade,
        "cascade_long_intensity": long_intensity,
        "cascade_short_intensity": short_intensity,
        "trade_status": trade_status,
        "trade_status_reason": trade_reason,
        "explanations": explanations,
        "explanation_text": explanation_text,
        "note": (
            "liquidation_event ≠ forced_flow ≠ trap ≠ squeeze ≠ cascade. "
            "Confirmation is a structure+flow GATE. The 0-100 confirm score is diagnostic only. "
            "Crowding is a PROXY, not OI positioning. "
            "SHORT FORCED FLOW requires meaningful short liq AND rising price AND rising CVD AND declining OI. "
            "A short print while price and CVD are falling is an event only. "
            "Forced-flow may fire with a low trap setup when that independent event-based gate is met."
        ),
    }


def _trade_status(
    *,
    long_cascade: bool,
    short_cascade: bool,
    long_squeeze: bool,
    short_squeeze: bool,
    long_forced: bool,
    short_forced: bool,
    long_confirmed: bool,
    short_confirmed: bool,
    long_vuln: float,
    short_vuln: float,
    watch: float,
    long_liq_event: bool,
    short_liq_event: bool,
) -> tuple[str, str]:
    if long_cascade or short_cascade:
        side = "long" if long_cascade else "short"
        return "CASCADE / DO NOT CHASE", f"actual {side} liquidation cascade gate"
    if long_squeeze and not short_squeeze:
        return "LONG SQUEEZE", "longs forced out (price down, CVD down, OI down, meaningful long liq)"
    if short_squeeze and not long_squeeze:
        return "SHORT SQUEEZE", "shorts forced out (price up, CVD up, OI down, meaningful short liq)"
    if long_squeeze and short_squeeze:
        if long_vuln >= short_vuln:
            return "LONG SQUEEZE", "both squeeze flags — using higher long vulnerability"
        return "SHORT SQUEEZE", "both squeeze flags — using higher short vulnerability"
    if long_forced and short_forced:
        if long_vuln >= short_vuln:
            return "LONG FORCED-FLOW", "both forced-flow gates — using higher long vulnerability"
        return "SHORT FORCED-FLOW", "both forced-flow gates — using higher short vulnerability"
    if long_forced:
        return "LONG FORCED-FLOW", "independent long forced-flow event (not derived from trap scores)"
    if short_forced:
        return "SHORT FORCED-FLOW", "independent short forced-flow event (not derived from trap scores)"
    if long_confirmed and not short_confirmed:
        return "LONG-TRAP CONFIRMING", "structure+flow gate (confirm score ignored)"
    if short_confirmed and not long_confirmed:
        return "SHORT-TRAP CONFIRMING", "structure+flow gate (confirm score ignored)"
    if long_confirmed and short_confirmed:
        if long_vuln >= short_vuln:
            return "LONG-TRAP CONFIRMING", "both confirm gates — using higher long vulnerability"
        return "SHORT-TRAP CONFIRMING", "both confirm gates — using higher short vulnerability"
    if long_vuln >= watch and long_vuln >= short_vuln:
        return "WATCH LONG-TRAP", f"long setup {long_vuln:.0f} ≥ watch {watch:.0f} — vulnerability only"
    if short_vuln >= watch:
        return "WATCH SHORT-TRAP", f"short setup {short_vuln:.0f} ≥ watch {watch:.0f} — vulnerability only"
    if long_liq_event or short_liq_event:
        sides = []
        if long_liq_event:
            sides.append("long")
        if short_liq_event:
            sides.append("short")
        return "WAIT", (
            f"{'/'.join(sides)} liquidation EVENT only — forced-flow gate failed "
            "(a print is not a trap, squeeze, or entry)"
        )
    return "WAIT", "no trap watch, confirm, forced-flow, squeeze, or cascade"

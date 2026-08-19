"""
Gating layer on top of 0-100 component scores.

Separates:
  positioning/crowding PROXY
  trap VULNERABILITY (setup score)
  trap CONFIRMATION (boolean structure+flow gate — not the confirm score)
  FORCED liquidation flow (observed notional)

A high setup or confirm score cannot by itself mean a confirmed trap.
"""

from __future__ import annotations

from config import get

TRADE_STATUSES = (
    "WAIT",
    "WATCH LONG-TRAP",
    "LONG-TRAP CONFIRMING",
    "LONG FORCED-FLOW",
    "WATCH SHORT-TRAP",
    "SHORT-TRAP CONFIRMING",
    "SHORT FORCED-FLOW",
    "CASCADE / DO NOT CHASE",
)


def _liq(snap: dict, side: str, window: str = "liq_15m") -> float:
    key = "long_notional" if side == "long" else "short_notional"
    return float((snap.get(window) or {}).get(key) or 0.0)


def _crowd_proxy(scores: dict, side: str) -> dict:
    card = scores.get("long_setup" if side == "long" else "short_setup") or {}
    comp = next((c for c in card.get("components") or [] if c.get("name") == "crowding"), None)
    score = float((comp or {}).get("normalized") or 0.0) * 100.0
    return {
        "score": round(score, 2),
        "label": "PROXY only — funding percentile + advertised account LS ratio. Not long/short OI.",
        "reason": (comp or {}).get("reason", ""),
    }


def evaluate(snap: dict, scores: dict) -> dict:
    cfg = get("gates", {})
    watch = float(cfg.get("setup_watch", 70))
    forced_min = float(cfg.get("forced_flow_notional", 50_000))
    cascade_min = float(cfg.get("cascade_min_observed_notional", 100_000))
    cas_enter = float(get("cascade.enter", 70))

    st = snap.get("structure") or {}
    oi15 = float(snap.get("oi_chg_15m_pct") or 0.0)
    px15 = float(snap.get("price_chg_15m_pct") or 0.0)
    cvd5 = float(snap.get("cvd_chg_5m") or 0.0)

    long_struct = bool(st.get("failed_breakout") or st.get("lost_support"))
    short_struct = bool(st.get("failed_breakdown") or st.get("lost_resistance"))

    long_cvd_follow = cvd5 < 0
    short_cvd_follow = cvd5 > 0
    long_unwind = oi15 < 0 and px15 < 0
    short_unwind = oi15 < 0 and px15 > 0

    long_liq = _liq(snap, "long")
    short_liq = _liq(snap, "short")
    long_liq5 = _liq(snap, "long", "liq_5m")
    short_liq5 = _liq(snap, "short", "liq_5m")

    long_flow = long_cvd_follow or long_unwind or long_liq > 0
    short_flow = short_cvd_follow or short_unwind or short_liq > 0

    # Gate: structure AND flow. Confirm SCORE is ignored here.
    long_confirmed = bool(long_struct and long_flow)
    short_confirmed = bool(short_struct and short_flow)

    long_forced = long_liq >= forced_min
    short_forced = short_liq >= forced_min

    long_vuln = float((scores.get("long_setup") or {}).get("total") or 0.0)
    short_vuln = float((scores.get("short_setup") or {}).get("total") or 0.0)

    # Intensity (price/OI/CVD) may be high; cascade STATE requires observed liq.
    long_intensity = float(scores.get("cascade_long") or 0.0)
    short_intensity = float(scores.get("cascade_short") or 0.0)
    long_liq_obs = max(long_liq, long_liq5)
    short_liq_obs = max(short_liq, short_liq5)
    long_cascade = long_intensity >= cas_enter and long_liq_obs >= cascade_min
    short_cascade = short_intensity >= cas_enter and short_liq_obs >= cascade_min

    trade_status = _trade_status(
        long_cascade=long_cascade,
        short_cascade=short_cascade,
        long_forced=long_forced,
        short_forced=short_forced,
        long_confirmed=long_confirmed,
        short_confirmed=short_confirmed,
        long_vuln=long_vuln,
        short_vuln=short_vuln,
        watch=watch,
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
        "long_forced_flow": long_forced,
        "short_forced_flow": short_forced,
        "long_forced_notional": long_liq,
        "short_forced_notional": short_liq,
        "long_cascade": long_cascade,
        "short_cascade": short_cascade,
        "trade_status": trade_status,
        "note": (
            "Confirmation is a structure+flow GATE. "
            "The 0-100 confirm score is diagnostic only and does not confirm a trap. "
            "Crowding is a PROXY, not OI positioning."
        ),
    }


def _trade_status(
    *,
    long_cascade: bool,
    short_cascade: bool,
    long_forced: bool,
    short_forced: bool,
    long_confirmed: bool,
    short_confirmed: bool,
    long_vuln: float,
    short_vuln: float,
    watch: float,
) -> str:
    if long_cascade or short_cascade:
        return "CASCADE / DO NOT CHASE"
    if long_forced and short_forced:
        return "LONG FORCED-FLOW" if long_vuln >= short_vuln else "SHORT FORCED-FLOW"
    if long_forced:
        return "LONG FORCED-FLOW"
    if short_forced:
        return "SHORT FORCED-FLOW"
    if long_confirmed and not short_confirmed:
        return "LONG-TRAP CONFIRMING"
    if short_confirmed and not long_confirmed:
        return "SHORT-TRAP CONFIRMING"
    if long_confirmed and short_confirmed:
        return "LONG-TRAP CONFIRMING" if long_vuln >= short_vuln else "SHORT-TRAP CONFIRMING"
    if long_vuln >= watch and long_vuln >= short_vuln:
        return "WATCH LONG-TRAP"
    if short_vuln >= watch:
        return "WATCH SHORT-TRAP"
    return "WAIT"

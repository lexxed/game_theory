"""
Deterministic 0-100 scores. No LLM.

LONG/SHORT TRAP SETUP and CONFIRMATION are separate.
Every component returns raw, normalized [0,1], points, reason.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from config import get
from src.utils import clip


@dataclass
class Component:
    name: str
    raw: float
    normalized: float
    weight: float
    points: float
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoreCard:
    total: float
    max_total: float
    components: list[Component] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total": round(self.total, 2),
            "max_total": self.max_total,
            "components": [c.as_dict() for c in self.components],
        }


def _comp(name: str, raw: float, norm: float, weight: float, reason: str) -> Component:
    n = clip(norm)
    return Component(name, raw, n, weight, round(n * weight, 2), reason)


def _weights(kind: str) -> dict:
    key = "weights_setup" if kind == "setup" else "weights_confirm"
    w = dict(get(key, {}))
    return w


# Inside the crowding COMPONENT (setup still has its own weight):
#   0.50 funding percentile + 0.25 LS_ACCOUNT + 0.25 top_pos_ratio
CROWD_FUND_W = 0.50
CROWD_ACCT_W = 0.25
CROWD_TOP_W = 0.25


def _ratio_to_long_100(ratio) -> tuple[float, bool]:
    """
    Map a Binance long/short ratio to a 0-100 LONG crowding score.
    1.0 → 50 (neutral). >1 increasingly long. <1 increasingly short.
    Extreme values clip to 0 or 100. Missing/invalid → 50, present=False.
    """
    if ratio is None:
        return 50.0, False
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return 50.0, False
    if r != r:  # NaN
        return 50.0, False
    return round(100.0 * clip(r / 2.0), 4), True


def crowding_breakdown(snap: dict) -> dict:
    """Authoritative crowding mix. Returns 0-100 long/short plus contributions."""
    fund_pct = float(snap.get("funding_pctile") if snap.get("funding_pctile") is not None else 50.0)
    fund_long = clip(fund_pct, 0.0, 100.0)
    ls_long, ls_ok = _ratio_to_long_100(snap.get("ls_account_ratio"))
    top_long, top_ok = _ratio_to_long_100(snap.get("top_pos_ratio"))
    long_100 = clip(
        CROWD_FUND_W * fund_long + CROWD_ACCT_W * ls_long + CROWD_TOP_W * top_long,
        0.0,
        100.0,
    )
    short_100 = clip(100.0 - long_100, 0.0, 100.0)
    fund_short = clip(100.0 - fund_long, 0.0, 100.0)
    ls_short = clip(100.0 - ls_long, 0.0, 100.0)
    top_short = clip(100.0 - top_long, 0.0, 100.0)
    ls_raw = snap.get("ls_account_ratio")
    top_raw = snap.get("top_pos_ratio")

    def _reason(side: str) -> str:
        if side == "long":
            f_c, a_c, t_c, total = CROWD_FUND_W * fund_long, CROWD_ACCT_W * ls_long, CROWD_TOP_W * top_long, long_100
            label = "LONG"
        else:
            f_c, a_c, t_c, total = CROWD_FUND_W * fund_short, CROWD_ACCT_W * ls_short, CROWD_TOP_W * top_short, short_100
            label = "SHORT"
        ls_txt = f"{float(ls_raw):.3f}" if ls_ok else "n/a (missing — treated as 1.0 / 50)"
        top_txt = f"{float(top_raw):.3f}" if top_ok else "n/a (missing — treated as 1.0 / 50)"
        return (
            "CROWDING PROXY (not OI split; not a trap):\n"
            f"  funding percentile = {fund_long:.1f}\n"
            f"  LS_ACCOUNT = {ls_txt}  (Binance global account long/short ratio)\n"
            f"  top_pos_ratio = {top_txt}  (Binance top-trader position-size long/short ratio)\n"
            f"  funding contribution = {f_c:.2f}/{CROWD_FUND_W*100:.0f}\n"
            f"  account contribution = {a_c:.2f}/{CROWD_ACCT_W*100:.0f}\n"
            f"  top-position contribution = {t_c:.2f}/{CROWD_TOP_W*100:.0f}\n"
            f"  {label} CROWDING = {total:.2f}/100"
        )

    return {
        "fund_pct": fund_long,
        "ls_account_ratio": float(ls_raw) if ls_ok else None,
        "top_pos_ratio": float(top_raw) if top_ok else None,
        "ls_long_100": ls_long,
        "top_long_100": top_long,
        "long_100": round(float(long_100), 4),
        "short_100": round(float(short_100), 4),
        "long_reason": _reason("long"),
        "short_reason": _reason("short"),
        "weights": {"funding": CROWD_FUND_W, "ls_account": CROWD_ACCT_W, "top_pos": CROWD_TOP_W},
    }


def _neg(val) -> bool:
    return val is not None and float(val) < 0.0


def long_trap_developing(snap: dict) -> dict:
    """
    Early-warning layer. Not confirmation. Uses only fields already on the snap
    (no future candles). OI down is 'book shrinking', not 'longs closed'.
    """
    cfg = get("developing", {})
    early_px_th = float(cfg.get("early_price_reversal_pct", 0.20))
    strong_px_th = float(cfg.get("strong_early_price_reversal_pct", 0.40))
    oi_th = float(cfg.get("early_oi_unwind_5m_pct", -0.50))
    score_th = float(cfg.get("threshold", 35.0))
    strong_th = float(cfg.get("strong_threshold", 50.0))
    crowd_th = float(cfg.get("long_crowding_early_threshold", 50.0))

    crowd = crowding_breakdown(snap)
    long_crowding = float(crowd["long_100"])
    crowding_component = min(max(long_crowding, 0.0), 100.0) * 0.20

    price = float(snap.get("price") or 0.0)
    high = float(snap.get("recent_local_high") or 0.0)
    if high <= 0:
        st5 = snap.get("structure_5m") or {}
        high = float(st5.get("swing_high") or 0.0)
    if high <= 0:
        st = snap.get("structure") or {}
        high = float(st.get("swing_high") or 0.0)
    drawdown = ((high - price) / high * 100.0) if high > 0 and price > 0 else 0.0
    early_price_reversal = drawdown >= early_px_th
    strong_early_price_reversal = drawdown >= strong_px_th

    cvd1 = snap.get("cvd_chg_1m")
    cvd3 = snap.get("cvd_chg_3m")
    cvd5 = snap.get("cvd_chg_5m")
    if cvd1 is not None and cvd3 is not None:
        early_cvd_reversal = _neg(cvd1) and _neg(cvd3)
    elif cvd1 is not None:
        early_cvd_reversal = _neg(cvd1) and _neg(cvd5)
    else:
        early_cvd_reversal = _neg(cvd5) and _neg(snap.get("delta_3m"))

    oi_blob = snap.get("oi") or {}
    oi5 = snap.get("oi_chg_5m_pct")
    if oi5 is None:
        oi5 = oi_blob.get("chg_5m_pct")
    oi5 = float(oi5 or 0.0)
    px5 = float(snap.get("price_chg_5m_pct") or 0.0)
    early_oi_unwind = oi5 <= oi_th and px5 < 0.0

    st5 = snap.get("structure_5m") or {}
    st1 = snap.get("structure_1m") or {}
    early_support_break = bool(st5.get("lost_support") or st1.get("lost_support"))

    price_component = 20.0 if early_price_reversal else 0.0
    cvd_component = 20.0 if early_cvd_reversal else 0.0
    oi_component = 20.0 if early_oi_unwind else 0.0
    structure_component = 20.0 if early_support_break else 0.0
    score = round(
        crowding_component + price_component + cvd_component + oi_component + structure_component,
        2,
    )
    evidence = [
        ("price_reversal", early_price_reversal),
        ("cvd_reversal", early_cvd_reversal),
        ("oi_unwind", early_oi_unwind),
        ("short_term_support_break", early_support_break),
    ]
    evidence_count = sum(1 for _, hit in evidence if hit)
    crowding_ok = long_crowding >= crowd_th
    developing = bool(crowding_ok and score >= score_th and evidence_count >= 2)
    developing_strong = bool(crowding_ok and score >= strong_th and evidence_count >= 3)

    bits = []
    if crowding_ok:
        bits.append("crowded long-side proxy")
    if early_price_reversal:
        bits.append("short-term price reversal")
    if early_cvd_reversal:
        bits.append("negative CVD")
    if early_oi_unwind:
        bits.append("OI shrinking while price declines")
    if early_support_break:
        bits.append("short-term support lost")
    reason = " + ".join(bits) if bits else "no early long-trap evidence"

    return {
        "score": score,
        "long_crowding": round(long_crowding, 2),
        "drawdown_from_recent_high_pct": round(drawdown, 4),
        "recent_local_high": high,
        "early_price_reversal": early_price_reversal,
        "strong_early_price_reversal": strong_early_price_reversal,
        "early_cvd_reversal": early_cvd_reversal,
        "early_oi_unwind": early_oi_unwind,
        "early_support_break": early_support_break,
        "evidence_count": evidence_count,
        "crowding_component": round(crowding_component, 2),
        "price_component": price_component,
        "cvd_component": cvd_component,
        "oi_component": oi_component,
        "structure_component": structure_component,
        "developing": developing,
        "developing_strong": developing_strong,
        "reason": reason,
        "note": "Early warning only. Not LONG TRAP CONFIRM. Not a trade entry. OI is not side-identified.",
    }


class ScoreEngine:
    def __init__(self):
        pass

    def compute(self, snap: dict) -> dict:
        long_setup = self._setup(snap, side="long")
        short_setup = self._setup(snap, side="short")
        long_conf = self._confirm(snap, side="long")
        short_conf = self._confirm(snap, side="short")
        cascade = self._cascade(snap)
        squeeze = self._squeeze(snap)
        developing = long_trap_developing(snap)
        return {
            "long_setup": long_setup.as_dict(),
            "short_setup": short_setup.as_dict(),
            "long_confirm": long_conf.as_dict(),
            "short_confirm": short_conf.as_dict(),
            "cascade_long": cascade["long"],
            "cascade_short": cascade["short"],
            "cascade_long_intensity": cascade["long"],
            "cascade_short_intensity": cascade["short"],
            "cascade_note": cascade.get("note", ""),
            "squeeze": squeeze,
            "long_trap_developing": developing,
        }

    def _setup(self, s: dict, side: str) -> ScoreCard:
        w = _weights("setup")
        refs = get("scoring_refs", {})
        comps: list[Component] = []

        # --- crowding (funding + LS_ACCOUNT + top_pos_ratio; NOT OI split) ---
        crowd = crowding_breakdown(s)
        fund_pct = crowd["fund_pct"]
        if side == "long":
            raw = crowd["long_100"] / 100.0
            reason = crowd["long_reason"]
        else:
            raw = crowd["short_100"] / 100.0
            reason = crowd["short_reason"]
        comps.append(_comp("crowding", raw, raw, w.get("crowding", 15), reason))

        # --- OI behavior ---
        oi15 = float(s.get("oi_chg_15m_pct") or 0.0)
        px15 = float(s.get("price_chg_15m_pct") or 0.0)
        expand_ref = float(refs.get("oi_expand_pct", 0.40))
        if side == "long":
            # longs more vulnerable if OI expands into a rally, especially if price stalls
            base = clip(oi15 / expand_ref) if oi15 > 0 and px15 > 0 else 0.0
            stall = clip((oi15 - px15) / float(refs.get("stall_gap_pct", 0.25))) if oi15 > px15 > 0 else 0.0
            if oi15 > 0 and px15 <= 0:
                base = 0.35 * clip(oi15 / expand_ref)
            raw = clip(0.6 * base + 0.4 * stall)
            reason = f"OI 15m {oi15:+.3f}% vs price 15m {px15:+.3f}% (OI up+price up = expanding book, not 'longs entered')"
        else:
            base = clip(oi15 / expand_ref) if oi15 > 0 and px15 < 0 else 0.0
            stall = clip((oi15 - abs(px15)) / float(refs.get("stall_gap_pct", 0.25))) if oi15 > abs(px15) and px15 < 0 else 0.0
            if oi15 > 0 and px15 >= 0:
                base = 0.35 * clip(oi15 / expand_ref)
            raw = clip(0.6 * base + 0.4 * stall)
            reason = f"OI 15m {oi15:+.3f}% vs price 15m {px15:+.3f}% (OI up+price down = expanding book into decline)"
        comps.append(_comp("oi_behavior", raw, raw, w.get("oi_behavior", 15), reason))

        # --- funding ---
        fund = float(s.get("funding") or 0.0)
        href = float(refs.get("funding_high", 0.0005))
        if side == "long":
            raw = clip(0.5 * clip(fund / href) + 0.5 * clip((fund_pct - 50) / 50))
            reason = (
                f"funding PROXY {fund:.6f} ({fund*100:.4f}%), percentile {fund_pct:.0f} "
                "— payment between longs/shorts, not proof of positioning"
            )
        else:
            raw = clip(0.5 * clip((-fund) / href) + 0.5 * clip((50 - fund_pct) / 50))
            reason = (
                f"funding PROXY {fund:.6f} (negative = short-side payment proxy), percentile {fund_pct:.0f} "
                "— not proof of short OI"
            )
        comps.append(_comp("funding", fund, raw, w.get("funding", 10), reason))

        # --- CVD divergence ---
        div = s.get("cvd_div") or {}
        if side == "long":
            raw = float(div.get("bearish_strength") or 0.0) if div.get("bearish") else 0.0
            reason = div.get("reason", "no bearish CVD divergence")
        else:
            raw = float(div.get("bullish_strength") or 0.0) if div.get("bullish") else 0.0
            reason = div.get("reason", "no bullish CVD divergence")
        comps.append(_comp("cvd_divergence", raw, raw, w.get("cvd_divergence", 20), reason))

        # --- absorption ---
        ab = s.get("absorption") or {}
        if side == "long":
            hit = bool(ab.get("buy_absorption"))
            raw = float(ab.get("strength") or 0.0) if hit else 0.0
            reason = ab.get("reason", "no buy absorption") if hit else "no buy absorption (aggressive buying stuck)"
        else:
            hit = bool(ab.get("sell_absorption"))
            raw = float(ab.get("strength") or 0.0) if hit else 0.0
            reason = ab.get("reason", "no sell absorption") if hit else "no sell absorption (aggressive selling stuck)"
        comps.append(_comp("absorption", raw, raw, w.get("absorption", 20), reason))

        # --- liquidation risk (OBSERVED only) ---
        liq_ref = float(refs.get("liq_notional_ref", 250000.0))
        if side == "long":
            notion = float((s.get("liq_15m") or {}).get("long_notional") or 0.0)
            raw = clip(notion / liq_ref)
            reason = (
                f"OBSERVED long-liquidation notional 15m ${notion:,.0f} "
                f"(incomplete force-order stream, not a hidden liq map)"
            )
        else:
            notion = float((s.get("liq_15m") or {}).get("short_notional") or 0.0)
            raw = clip(notion / liq_ref)
            reason = (
                f"OBSERVED short-liquidation notional 15m ${notion:,.0f} "
                f"(incomplete force-order stream)"
            )
        comps.append(_comp("liquidation_risk", notion, raw, w.get("liquidation_risk", 15), reason))

        # --- price structure ---
        st = s.get("structure") or {}
        if side == "long":
            bits = [st.get("near_high"), st.get("failed_breakout")]
            raw = 0.6 * float(bool(st.get("near_high"))) + 0.4 * float(bool(st.get("failed_breakout")))
            reason = st.get("reason", "") + " (long-trap structure = near highs / failed breakout)"
        else:
            raw = 0.6 * float(bool(st.get("near_low"))) + 0.4 * float(bool(st.get("failed_breakdown")))
            reason = st.get("reason", "") + " (short-trap structure = near lows / failed breakdown)"
        comps.append(_comp("price_structure", raw, raw, w.get("price_structure", 3), reason))

        # --- order book imbalance (resting liquidity, not executed volume) ---
        ob = s.get("orderbook") or {}
        ratio = float(ob.get("imbalance_ratio") or 0.0)
        if side == "long":
            # A long trap is not "book looks bullish" — it's book LOOKS bullish
            # (bid-heavy) while the ask side has almost nothing resting above
            # price, so a small push clears it and invites longs into a
            # vacuum with no real resistance-side absorption capacity behind
            # it. Positive imbalance alone (bid-heavy) is weak evidence on its
            # own; positive imbalance + a thin ask wall is the actual tell.
            base = clip(ratio) if ratio > 0 else 0.0
            thin = bool(ob.get("thin_ask_wall_above"))
            raw = base * (1.0 if thin else 0.4)
            reason = (
                (ob.get("reason") or "order book not available")
                + (" — bid-heavy book with thin resistance above = fragile-looking support, not proof of a trap"
                   if thin and base > 0 else "")
            )
        else:
            base = clip(-ratio) if ratio < 0 else 0.0
            thin = bool(ob.get("thin_bid_wall_below"))
            raw = base * (1.0 if thin else 0.4)
            reason = (
                (ob.get("reason") or "order book not available")
                + (" — ask-heavy book with thin support below = fragile-looking resistance, not proof of a trap"
                   if thin and base > 0 else "")
            )
        comps.append(_comp("book_imbalance", ratio, raw, w.get("book_imbalance", 10), reason))

        # --- taker buy/sell flow ratio (relative one-sidedness, not cumulative like CVD) ---
        t1 = s.get("taker_ratio_1m") or {}
        t5 = s.get("taker_ratio_5m") or {}
        r1 = float(t1.get("ratio") or 0.0)
        r5 = float(t5.get("ratio") or 0.0)
        if side == "long":
            # Heavy one-sided BUY flow is the demand-side crowding signal: if
            # nearly everyone aggressive lately has been buying, most of the
            # willing buyers may already be in — vulnerable to a flush once
            # that flow dries up. Weighted higher when the very recent (1m)
            # ratio is running hotter than the 5m ratio (flow still
            # intensifying, not already fading).
            base = clip(r5) if r5 > 0 else 0.0
            intensifying = r1 >= r5
            raw = base * (1.0 if intensifying else 0.5)
            reason = (
                (t5.get("reason") or "taker flow not available")
                + (f" | 1m ratio {r1:+.2f} {'confirms' if intensifying else 'is fading vs'} 5m"
                   if base > 0 else "")
            )
        else:
            base = clip(-r5) if r5 < 0 else 0.0
            intensifying = r1 <= r5
            raw = base * (1.0 if intensifying else 0.5)
            reason = (
                (t5.get("reason") or "taker flow not available")
                + (f" | 1m ratio {r1:+.2f} {'confirms' if intensifying else 'is fading vs'} 5m"
                   if base > 0 else "")
            )
        comps.append(_comp("taker_flow", r5, raw, w.get("taker_flow", 10), reason))

        total = round(sum(c.points for c in comps), 2)
        return ScoreCard(total, 100.0, comps)

    def _confirm(self, s: dict, side: str) -> ScoreCard:
        w = _weights("confirm")
        comps: list[Component] = []
        st = s.get("structure") or {}
        oi15 = float(s.get("oi_chg_15m_pct") or 0.0)
        px15 = float(s.get("price_chg_15m_pct") or 0.0)
        px5 = float(s.get("price_chg_5m_pct") or 0.0)
        px1 = float(s.get("price_chg_1m_pct") or 0.0)
        cvd5 = float(s.get("cvd_chg_5m") or 0.0)
        liq = s.get("liq_15m") or {}
        liq5 = s.get("liq_5m") or {}

        if side == "long":
            lost = bool(st.get("lost_support") or st.get("failed_breakout"))
            comps.append(
                _comp(
                    "support_break",
                    float(lost),
                    float(lost),
                    w.get("support_break", 25),
                    "failed breakout / lost support" if lost else "local support still holds (no failed breakout)",
                )
            )
            unwind = oi15 < 0 and px15 < 0
            uw_n = clip(min(abs(oi15), abs(px15)) / 0.25) if unwind else 0.0
            comps.append(
                _comp(
                    "oi_unwind",
                    oi15,
                    uw_n,
                    w.get("oi_unwind", 20),
                    f"OI {oi15:+.3f}% with price {px15:+.3f}% — "
                    + ("book shrinking into decline" if unwind else "no down-OI + down-price unwind"),
                )
            )
            long_liq = float(liq.get("long_notional") or 0.0)
            long_liq5 = float(liq5.get("long_notional") or 0.0)
            # Long-trap confirmation liq only counts into a decline (against longs).
            liq_n = 0.0
            liq_reason = f"OBSERVED long liq 15m ${long_liq:,.0f} / 5m ${long_liq5:,.0f}"
            if long_liq > 0 and px15 < 0:
                liq_n = clip(long_liq / float(get("scoring_refs.liq_notional_ref", 250000)))
                if long_liq5 > long_liq * 0.4 and long_liq5 > 0:
                    liq_n = clip(liq_n + 0.2)
            elif long_liq > 0:
                liq_reason += " — ignored: long liqs while price is not falling do not confirm a long trap"
            comps.append(
                _comp(
                    "liquidation_flow",
                    long_liq,
                    liq_n,
                    w.get("liquidation_flow", 25),
                    liq_reason,
                )
            )
            follow = cvd5 < 0 and px5 < 0
            comps.append(
                _comp(
                    "cvd_follow",
                    cvd5,
                    1.0 if follow else 0.0,
                    w.get("cvd_follow", 15),
                    f"CVD 5m {cvd5:+.4g}, price 5m {px5:+.3f}%",
                )
            )
            accel = px1 < 0 and px1 < px5 < 0
            comps.append(
                _comp(
                    "acceleration",
                    px1,
                    1.0 if accel else clip(abs(px1) / 0.15) if px1 < 0 else 0.0,
                    w.get("acceleration", 15),
                    f"1m {px1:+.3f}% vs 5m {px5:+.3f}%",
                )
            )
        else:
            lost = bool(st.get("lost_resistance") or st.get("failed_breakdown"))
            comps.append(
                _comp(
                    "resistance_break",
                    float(lost),
                    float(lost),
                    w.get("support_break", 25),
                    "failed breakdown / lost resistance" if lost else "local resistance still holds (no failed breakdown)",
                )
            )
            unwind = oi15 < 0 and px15 > 0
            uw_n = clip(min(abs(oi15), abs(px15)) / 0.25) if unwind else 0.0
            comps.append(
                _comp(
                    "oi_unwind",
                    oi15,
                    uw_n,
                    w.get("oi_unwind", 20),
                    f"OI {oi15:+.3f}% with price {px15:+.3f}% — "
                    + ("book shrinking into rally" if unwind else "no up-price + down-OI unwind"),
                )
            )
            short_liq = float(liq.get("short_notional") or 0.0)
            short_liq5 = float(liq5.get("short_notional") or 0.0)
            # Short-trap confirmation liq only counts into a rally (against shorts).
            liq_n = 0.0
            liq_reason = f"OBSERVED short liq 15m ${short_liq:,.0f} / 5m ${short_liq5:,.0f}"
            if short_liq > 0 and px15 > 0:
                liq_n = clip(short_liq / float(get("scoring_refs.liq_notional_ref", 250000)))
                if short_liq5 > short_liq * 0.4 and short_liq5 > 0:
                    liq_n = clip(liq_n + 0.2)
            elif short_liq > 0:
                liq_reason += " — ignored: short liqs while price is not rising do not confirm a short trap"
            comps.append(
                _comp(
                    "liquidation_flow",
                    short_liq,
                    liq_n,
                    w.get("liquidation_flow", 25),
                    liq_reason,
                )
            )
            follow = cvd5 > 0 and px5 > 0
            comps.append(
                _comp(
                    "cvd_follow",
                    cvd5,
                    1.0 if follow else 0.0,
                    w.get("cvd_follow", 15),
                    f"CVD 5m {cvd5:+.4g}, price 5m {px5:+.3f}%",
                )
            )
            accel = px1 > 0 and px1 > px5 > 0
            comps.append(
                _comp(
                    "acceleration",
                    px1,
                    1.0 if accel else clip(px1 / 0.15) if px1 > 0 else 0.0,
                    w.get("acceleration", 15),
                    f"1m {px1:+.3f}% vs 5m {px5:+.3f}%",
                )
            )

        return ScoreCard(round(sum(c.points for c in comps), 2), 100.0, comps)

    def _cascade(self, s: dict) -> dict:
        refs = get("cascade", {})
        oi15 = float(s.get("oi_chg_15m_pct") or 0.0)
        px5 = float(s.get("price_chg_5m_pct") or 0.0)
        cvd5 = float(s.get("cvd_chg_5m") or 0.0)
        oi_ref = float(refs.get("oi_drop_ref_pct", 0.35))

        def side_score(price_down: bool) -> float:
            # Intensity from price / OI / CVD only. Observed liq is a GATE, not this score.
            px_ok = (px5 < 0) if price_down else (px5 > 0)
            cvd_ok = (cvd5 < 0) if price_down else (cvd5 > 0)
            oi_ok = oi15 < 0
            parts = [
                0.40 * (1.0 if px_ok else 0.0),
                0.35 * clip(abs(oi15) / oi_ref) if oi_ok else 0.0,
                0.25 * (1.0 if cvd_ok else 0.0),
            ]
            return round(100.0 * sum(parts), 2)

        return {
            "long": side_score(price_down=True),
            "short": side_score(price_down=False),
            "note": "INTENSITY only (price/OI/CVD). Not an actual cascade. See gates.long_cascade / short_cascade.",
        }

    def _squeeze(self, s: dict) -> dict:
        """
        SHORT SQUEEZE = shorts being forced out:
            price rising strongly + CVD up + OI falling + meaningful short liq
        LONG SQUEEZE = longs being forced out:
            price falling strongly + CVD down + OI falling + meaningful long liq

        A liquidation print of $1 (or any notional > 0) is NOT a squeeze.
        OI down + price move can also be voluntary covering — require meaningful liq.
        """
        from src.liquidations import classify_liquidation, oi_usdt

        oi15 = float(s.get("oi_chg_15m_pct") or 0.0)
        px15 = float(s.get("price_chg_15m_pct") or 0.0)
        cvd15 = float(s.get("cvd_chg_15m") or 0.0)
        cvd5 = float(s.get("cvd_chg_5m") or 0.0)
        liq = s.get("liq_15m") or {}
        cfg = get("squeeze", {})
        gcfg = get("gates", {})
        need_oi = float(cfg.get("oi_drop_pct", 0.08))
        need_px = float(cfg.get("price_move_pct", 0.08))
        short_liq = float(liq.get("short_notional") or 0.0)
        long_liq = float(liq.get("long_notional") or 0.0)
        base = s.get("liq_baseline") or {}
        short_sig = classify_liquidation(
            short_liq,
            oi_usdt_value=oi_usdt(s),
            median=float((base.get("short") or {}).get("median") or 0.0),
            history=list((base.get("short") or {}).get("history") or []),
            cfg=gcfg,
        )
        long_sig = classify_liquidation(
            long_liq,
            oi_usdt_value=oi_usdt(s),
            median=float((base.get("long") or {}).get("median") or 0.0),
            history=list((base.get("long") or {}).get("history") or []),
            cfg=gcfg,
        )
        cvd_up = cvd15 > 0 or (cvd5 > 0 and cvd15 >= 0)
        cvd_down = cvd15 < 0 or (cvd5 < 0 and cvd15 <= 0)
        # Canonical names (fixed inversion): short_squeeze = shorts squeezed.
        short_sq = (
            oi15 <= -need_oi
            and px15 >= need_px
            and cvd_up
            and short_sig["is_meaningful"]
        )
        long_sq = (
            oi15 <= -need_oi
            and px15 <= -need_px
            and cvd_down
            and long_sig["is_meaningful"]
        )
        return {
            "long_squeeze": bool(long_sq),
            "short_squeeze": bool(short_sq),
            "short_liq_level": short_sig["level"],
            "long_liq_level": long_sig["level"],
            "reason": (
                f"OI {oi15:+.3f}% price {px15:+.3f}% CVD15 {cvd15:+.4g} CVD5 {cvd5:+.4g} "
                f"shortLiq ${short_liq:,.0f} ({short_sig['level']}) "
                f"longLiq ${long_liq:,.0f} ({long_sig['level']})"
            ),
        }

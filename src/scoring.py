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
        return {
            "long_setup": long_setup.as_dict(),
            "short_setup": short_setup.as_dict(),
            "long_confirm": long_conf.as_dict(),
            "short_confirm": short_conf.as_dict(),
            "cascade_long": cascade["long"],
            "cascade_short": cascade["short"],
            "squeeze": squeeze,
        }

    def _setup(self, s: dict, side: str) -> ScoreCard:
        w = _weights("setup")
        refs = get("scoring_refs", {})
        comps: list[Component] = []

        # --- crowding (funding + advertised LS ratio; NOT actual long/short OI) ---
        fund_pct = float(s.get("funding_pctile", 50.0))
        ls = float(s.get("ls_account_ratio") or 1.0)
        # ls>1 more accounts long
        if side == "long":
            fund_n = clip((fund_pct - 50.0) / 50.0)
            ls_n = clip((ls - 1.0) / 1.0)
            raw = 0.65 * fund_n + 0.35 * ls_n
            reason = (
                f"ESTIMATED crowding: funding percentile {fund_pct:.0f}, "
                f"global account LS ratio {ls:.3f} (not actual long OI)"
            )
        else:
            fund_n = clip((50.0 - fund_pct) / 50.0)
            ls_n = clip((1.0 - ls) / 1.0)
            raw = 0.65 * fund_n + 0.35 * ls_n
            reason = (
                f"ESTIMATED crowding: funding percentile {fund_pct:.0f} (low favors shorts), "
                f"global account LS ratio {ls:.3f}"
            )
        if s.get("ls_account_ratio") is None:
            raw *= 0.7
            reason += " — LS ratio unavailable, funding-only and capped 70%"
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
            reason = f"funding {fund:.6f} ({fund*100:.4f}%), percentile {fund_pct:.0f}"
        else:
            raw = clip(0.5 * clip((-fund) / href) + 0.5 * clip((50 - fund_pct) / 50))
            reason = f"funding {fund:.6f} (negative favors short crowding), percentile {fund_pct:.0f}"
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
        comps.append(_comp("price_structure", raw, raw, w.get("price_structure", 5), reason))

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
            lost = bool(st.get("lost_support"))
            comps.append(
                _comp(
                    "support_break",
                    float(lost),
                    float(lost),
                    w.get("support_break", 25),
                    "price lost local support" if lost else "local support still holds",
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
            liq_n = clip(long_liq / float(get("scoring_refs.liq_notional_ref", 250000)))
            if long_liq5 > long_liq * 0.4 and long_liq5 > 0:
                liq_n = clip(liq_n + 0.2)
            comps.append(
                _comp(
                    "liquidation_flow",
                    long_liq,
                    liq_n,
                    w.get("liquidation_flow", 25),
                    f"OBSERVED long liq 15m ${long_liq:,.0f} / 5m ${long_liq5:,.0f}",
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
            lost = bool(st.get("lost_resistance"))
            comps.append(
                _comp(
                    "resistance_break",
                    float(lost),
                    float(lost),
                    w.get("support_break", 25),
                    "price broke local resistance" if lost else "local resistance still holds",
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
            liq_n = clip(short_liq / float(get("scoring_refs.liq_notional_ref", 250000)))
            comps.append(
                _comp(
                    "liquidation_flow",
                    short_liq,
                    liq_n,
                    w.get("liquidation_flow", 25),
                    f"OBSERVED short liq 15m ${short_liq:,.0f} / 5m ${short_liq5:,.0f}",
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
        liq = s.get("liq_5m") or {}
        liq_ref = float(get("scoring_refs.liq_notional_ref", 250000))
        oi_ref = float(refs.get("oi_drop_ref_pct", 0.35))

        def side_score(price_down: bool, long_liq: bool) -> float:
            px_ok = (px5 < 0) if price_down else (px5 > 0)
            cvd_ok = (cvd5 < 0) if price_down else (cvd5 > 0)
            oi_ok = oi15 < 0
            notion = float(liq.get("long_notional" if long_liq else "short_notional") or 0.0)
            parts = [
                0.25 * (1.0 if px_ok else 0.0),
                0.25 * clip(abs(oi15) / oi_ref) if oi_ok else 0.0,
                0.30 * clip(notion / liq_ref),
                0.20 * (1.0 if cvd_ok else 0.0),
            ]
            return round(100.0 * sum(parts), 2)

        return {
            "long": side_score(price_down=True, long_liq=True),
            "short": side_score(price_down=False, long_liq=False),
        }

    def _squeeze(self, s: dict) -> dict:
        """
        Long squeeze / short covering: price↑ OI↓ CVD↑ short liqs↑
        Short squeeze / long covering: price↓ OI↓ CVD↓ long liqs↑
        OI down + price move can also be voluntary closing — require liq confirmation.
        """
        oi15 = float(s.get("oi_chg_15m_pct") or 0.0)
        px15 = float(s.get("price_chg_15m_pct") or 0.0)
        cvd15 = float(s.get("cvd_chg_15m") or 0.0)
        liq = s.get("liq_15m") or {}
        cfg = get("squeeze", {})
        need_oi = float(cfg.get("oi_drop_pct", 0.08))
        need_px = float(cfg.get("price_move_pct", 0.08))
        short_liq = float(liq.get("short_notional") or 0.0)
        long_liq = float(liq.get("long_notional") or 0.0)
        long_sq = oi15 <= -need_oi and px15 >= need_px and cvd15 > 0 and short_liq > 0
        short_sq = oi15 <= -need_oi and px15 <= -need_px and cvd15 < 0 and long_liq > 0
        return {
            "long_squeeze": bool(long_sq),  # shorts squeezed
            "short_squeeze": bool(short_sq),  # longs squeezed
            "reason": (
                f"OI {oi15:+.3f}% price {px15:+.3f}% CVD15 {cvd15:+.4g} "
                f"shortLiq ${short_liq:,.0f} longLiq ${long_liq:,.0f}"
            ),
        }

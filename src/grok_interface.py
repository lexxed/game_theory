"""
Optional local Grok CLI comment.

Python owns every number and the official STATE label.
Grok must apply THIS engine's interpretation rules — it does not narrate
and must not invent a competing taxonomy.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap

from config import get


ENGINE_RULES = """
# ENGINE INTERPRETATION RULES (authoritative)

You are writing a COMMENT on a live snapshot already scored by this engine.
Read these rules first. Use ONLY this vocabulary. Do not narrate, do not
re-score, do not place trades, do not claim certainty.

## Who owns the labels
- STATE is already assigned by the state machine. Repeat it verbatim.
- Do not rename states (e.g. do not say "bull trap" if STATE is POTENTIAL LONG TRAP).
- Do not upgrade POTENTIAL LONG TRAP to a cascade unless CASCADE_LONG already meets the cascade rule.
- Setup vs confirmation are separate. High setup + low confirm = vulnerable, NOT confirmed.
- Confirm SCORE never confirms a trap. Confirmed long trap = (failed_breakout OR lost_support) AND (neg CVD follow OR OI unwind OR observed long liq).
- Confirmed short trap = (failed_breakdown OR lost_resistance) AND (pos CVD follow OR OI unwind OR observed short liq).
- Cascade STATE requires observed liquidation notional above gates.cascade_min_observed_notional. Intensity is price/OI/CVD only.
- Funding percentile and account LS ratio are CROWDING PROXIES, not long/short OI.

## Allowed STATE values (exact strings)
- NEUTRAL
- LONG-SIDE CROWDING ESTIMATE
- SHORT-SIDE CROWDING ESTIMATE
- POTENTIAL LONG TRAP
- POTENTIAL SHORT TRAP
- BUY ABSORPTION
- SELL ABSORPTION
- FAILED BREAKOUT
- FAILED BREAKDOWN
- LONG LIQUIDATION CASCADE
- SHORT LIQUIDATION CASCADE
- SHORT SQUEEZE
- LONG SQUEEZE
- CASCADE EXHAUSTION

## Metric definitions (this engine)
- Price: last trade / ticker last. 24h change is the Binance 24h ticker percent.
- OI: REST open interest (no public OI websocket). OI up is "book expanding", NOT "longs entered". OI down is "book shrinking", NOT "longs closed".
- Funding: periodic long/short transfer. High positive funding is a crowding PROXY for longs, not proof of positioning.
- LS_ACCOUNT_RATIO: Binance advertised global account long/short ratio. ESTIMATED crowding only. Not an OI split.
- Aggressor: Binance aggTrade m=true => buyer is maker => SELL aggressor. m=false => BUY aggressor.
- buy_volume / sell_volume: aggressive market buy / sell quantity.
- delta = buy_volume - sell_volume (or ask - bid on footprint).
- CVD = running sum of delta. CVD is aggressive flow, not trader identity.
- Bearish CVD divergence (engine): price swing HIGH makes a higher high by min_price_hh_pct AND CVD swing high does not make a higher high.
- Bullish CVD divergence (engine): price swing LOW makes a lower low by min_price_ll_pct AND CVD swing low does not make a lower low.
- Do not call divergence because two lines "look different".
- Footprint ask = aggressive buy at that price; bid = aggressive sell.
- BUY ABSORPTION: large positive delta AND price progress <= max_progress_atr * ATR. Aggressive buying stuck.
- SELL ABSORPTION: large negative delta AND limited downside progress. Aggressive selling stuck.
- Liquidations: OBSERVED !forceOrder@arr only. SELL force-order = longs liquidated. BUY force-order = shorts liquidated. Sampled (~1 event/symbol/sec). Not a hidden liquidation map.
- Long trap SETUP: estimated long crowding + OI expanding into a rally/stall + high funding + bearish CVD div + buy absorption + observed long-liq risk + near highs / failed breakout.
- Short trap SETUP: mirror (low/negative funding, OI expanding into decline, bullish CVD div, sell absorption, short-liq risk, near lows / failed breakdown).
- Long trap CONFIRMATION: lost support + OI shrinking with price down + observed long liqs + CVD following down + downside acceleration. Requires adverse flow, not just high funding.
- Short trap CONFIRMATION: broken resistance + OI shrinking with price up + observed short liqs + CVD following up + upside acceleration.
- LONG SQUEEZE (engine name) = shorts squeezed / short covering candidate: price up, OI down, CVD up, observed short liqs. Could also be voluntary covering.
- SHORT SQUEEZE (engine name) = longs squeezed / long covering candidate: price down, OI down, CVD down, observed long liqs.
- LONG LIQUIDATION CASCADE: price down + OI shrinking + observed long-liq spike + sell/CVD down. Intensity 0-100; enter >= cascade.enter (default 70).
- SHORT LIQUIDATION CASCADE: mirror on the upside.
- Setup enter default 70. Confirm enter default 65. Absorption enter uses absorption.strength*100 >= 60.
- A high Long Trap Setup does NOT mean price must fall. A high Short Trap Setup does NOT mean price must rise.

## Forbidden
- Do not say the exchange is manipulating price.
- Do not invent liquidation clusters or "smart money".
- Do not contradict the engine STATE unless you are pointing out that CONFIRMATION is below threshold (then say "STATE is X but confirmation is only Y — treat as setup, not forced flow").
- If evidence is thin, say UNCLEAR.

## Output format (no other sections)
ENGINE_STATE: <verbatim STATE>
SETUP_VS_CONFIRM: long setup/confirm = A/B ; short setup/confirm = C/D
VULNERABLE_SIDE: LONGS | SHORTS | UNCLEAR
ABSORPTION: NONE | BUY ABSORPTION | SELL ABSORPTION | UNCLEAR  (use engine flags, not vibes)
FORCED_FLOW: NONE | OBSERVED LONG LIQS | OBSERVED SHORT LIQS | OI UNWIND | CASCADE | UNCLEAR
TRAP_STATUS: NO TRAP | LONG SETUP ONLY | LONG SETUP+CONFIRM | SHORT SETUP ONLY | SHORT SETUP+CONFIRM
INVALIDATION: <what would break THIS state's reading, in the engine's terms>
COMMENT: <6-10 sentences applying the rules to the snapshot. Label any extra inference as INTERPRETATION.>
"""


TASK = """
Comment on the SNAPSHOT using ENGINE INTERPRETATION RULES above.
Do not recalculate scores. Do not narrate. Do not invent data.
Use the engine's STATE string verbatim.
"""


def grok_available() -> tuple[bool, str]:
    exe = get("grok.exe", "grok")
    path = shutil.which(exe) or exe
    return True, path


def build_prompt(snapshot_text: str) -> str:
    return (
        textwrap.dedent(ENGINE_RULES).strip()
        + "\n\n# SNAPSHOT\n"
        + snapshot_text.strip()
        + "\n\n# TASK\n"
        + textwrap.dedent(TASK).strip()
    )


def ask_grok(snapshot_text: str) -> str:
    if not get("grok.enabled", True):
        return "Grok integration disabled in config.yaml (grok.enabled: false)."
    ok, exe = grok_available()
    if not ok:
        return "Grok CLI not found on PATH."
    prompt = build_prompt(snapshot_text)
    cmd = [
        exe,
        "-p",
        prompt,
        "--output-format",
        "plain",
        "--max-turns",
        str(int(get("grok.max_turns", 1))),
        "--disable-web-search",
        "--verbatim",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=float(get("grok.timeout_s", 90)),
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return "Grok timed out."
    except FileNotFoundError:
        return f"Could not execute {exe!r}. Is the Grok CLI installed?"
    except Exception as exc:
        return f"Grok error: {type(exc).__name__}: {exc}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 and not out:
        return f"Grok exited {proc.returncode}: {err[:800]}"
    return out or f"(empty stdout) {err[:400]}"


def _components(card: dict) -> list[dict]:
    rows = []
    for c in card.get("components") or []:
        rows.append(
            {
                "name": c.get("name"),
                "points": c.get("points"),
                "weight": c.get("weight"),
                "reason": c.get("reason"),
            }
        )
    return rows


def compact_snapshot(
    symbol: str,
    snap: dict,
    scores: dict,
    state: str,
    state_reason: str = "",
) -> str:
    oi = snap.get("oi") or {}
    liq15 = snap.get("liq_15m") or {}
    liq5 = snap.get("liq_5m") or {}
    ab = snap.get("absorption") or {}
    div = snap.get("cvd_div") or {}
    st = snap.get("structure") or {}
    payload = {
        "symbol": symbol,
        "STATE": state,
        "STATE_REASON": state_reason,
        "price": snap.get("price"),
        "change_24h_pct": snap.get("change_24h_pct"),
        "oi": oi.get("oi"),
        "oi_chg_1m_pct": oi.get("chg_1m_pct"),
        "oi_chg_5m_pct": oi.get("chg_5m_pct"),
        "oi_chg_15m_pct": oi.get("chg_15m_pct"),
        "oi_chg_1h_pct": oi.get("chg_1h_pct"),
        "funding": snap.get("funding"),
        "funding_percentile": snap.get("funding_pctile"),
        "ls_account_ratio": snap.get("ls_account_ratio"),
        "cvd": snap.get("cvd"),
        "cvd_chg_5m": snap.get("cvd_chg_5m"),
        "cvd_chg_15m": snap.get("cvd_chg_15m"),
        "delta_3m": snap.get("delta_3m"),
        "cvd_divergence": {
            "bearish": div.get("bearish"),
            "bullish": div.get("bullish"),
            "bearish_strength": div.get("bearish_strength"),
            "bullish_strength": div.get("bullish_strength"),
            "reason": div.get("reason"),
        },
        "absorption": {
            "buy_absorption": ab.get("buy_absorption"),
            "sell_absorption": ab.get("sell_absorption"),
            "strength": ab.get("strength"),
            "reason": ab.get("reason"),
        },
        "structure": {
            "near_high": st.get("near_high"),
            "near_low": st.get("near_low"),
            "lost_support": st.get("lost_support"),
            "lost_resistance": st.get("lost_resistance"),
            "failed_breakout": st.get("failed_breakout"),
            "failed_breakdown": st.get("failed_breakdown"),
            "swing_high": st.get("swing_high"),
            "swing_low": st.get("swing_low"),
            "reason": st.get("reason"),
        },
        "liq_5m_observed": liq5,
        "liq_15m_observed": liq15,
        "LONG_TRAP_SETUP": scores["long_setup"]["total"],
        "LONG_TRAP_CONFIRMATION": scores["long_confirm"]["total"],
        "SHORT_TRAP_SETUP": scores["short_setup"]["total"],
        "SHORT_TRAP_CONFIRMATION": scores["short_confirm"]["total"],
        "CASCADE_LONG": scores.get("cascade_long"),
        "CASCADE_SHORT": scores.get("cascade_short"),
        "SQUEEZE": scores.get("squeeze"),
        "long_setup_components": _components(scores.get("long_setup") or {}),
        "long_confirm_components": _components(scores.get("long_confirm") or {}),
        "short_setup_components": _components(scores.get("short_setup") or {}),
        "short_confirm_components": _components(scores.get("short_confirm") or {}),
        "LIMITS": [
            "OI is not side-identified",
            "Funding is not positioning",
            "CVD is aggressive flow, not identity",
            "Liquidations are OBSERVED force-orders only",
            "High setup is not a price forecast",
        ],
    }
    return json.dumps(payload, default=str, indent=2)

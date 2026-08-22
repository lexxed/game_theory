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
- Setup score = VULNERABILITY only. High setup is WATCH, not a confirmed trap.
- Confirm SCORE (0-100) is DIAGNOSTIC ONLY. Never say a trap is confirmed or denied because confirm is below/above 65.
- LONG_TRAP_CONFIRMATION_GATE is the only confirmation boolean.
  Long confirmed = (failed_breakout OR lost_support) AND (neg CVD follow OR OI unwind into decline OR meaningful long-liq flow with price down).
  Short confirmed = (failed_breakdown OR lost_resistance) AND (pos CVD follow OR OI unwind into rally OR meaningful short-liq flow with price up).
  A tiny liquidation print (liq>0) does NOT confirm a trap.
- TRADE_STATUS is already computed. Repeat it verbatim. Do not invent a different status.
- liquidation_event ≠ forced_flow ≠ trap ≠ squeeze ≠ cascade.
- SHORT FORCED FLOW requires meaningful short liq AND rising price AND rising CVD AND declining OI. A short print while price/CVD are falling is EVENT ONLY.
- LONG FORCED FLOW requires meaningful long liq AND falling price AND falling CVD AND declining OI.
- Cascade STATE requires the cascade GATE (intensity + meaningful same-side liq + direction). Intensity (CASCADE_LONG/SHORT) is price/OI/CVD only and is NOT a cascade by itself.
- Funding percentile and account LS ratio are CROWDING PROXIES, not long/short OI.
- LONG TRAP DEVELOPING in the SNAPSHOT is an engine boolean. Copy it. Do not recompute it.
  It is an early warning only. It is NOT confirmation and NOT a trade entry.
  LONG_TRAP_DEVELOPING=true AND LONG_TRAP_CONFIRMATION_GATE=false is VALID and common.
  Do NOT report developing as false because confirmation is false.
  Confirm remains LONG_TRAP_CONFIRMATION_GATE. Do not upgrade DEVELOPING to CONFIRMING.
  If ENGINE_STATE is LONG TRAP DEVELOPING or LONG TRAP DEVELOPING STRONG, developing is true.

## Allowed STATE values (exact strings)
- NEUTRAL
- LONG-SIDE CROWDING ESTIMATE
- SHORT-SIDE CROWDING ESTIMATE
- POTENTIAL LONG TRAP
- POTENTIAL SHORT TRAP
- LONG TRAP DEVELOPING
- LONG TRAP DEVELOPING STRONG
- BUY ABSORPTION
- SELL ABSORPTION
- FAILED BREAKOUT
- FAILED BREAKDOWN
- LONG FORCED FLOW
- SHORT FORCED FLOW
- LONG SQUEEZE
- SHORT SQUEEZE
- LONG LIQUIDATION CASCADE
- SHORT LIQUIDATION CASCADE
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
- Long trap CONFIRMATION GATE: (failed_breakout OR lost_support) AND (neg CVD follow OR OI unwind into decline OR meaningful long-liq with price down). Ignore confirm score for this.
- Short trap CONFIRMATION GATE: (failed_breakdown OR lost_resistance) AND (pos CVD follow OR OI unwind into rally OR meaningful short-liq with price up). Ignore confirm score for this.
- SHORT SQUEEZE = shorts forced out: price up, OI down, CVD up, meaningful short liqs. Could also be voluntary covering.
- LONG SQUEEZE = longs forced out: price down, OI down, CVD down, meaningful long liqs. Could also be voluntary covering.
- Do NOT invert squeeze names. SHORT SQUEEZE is bullish for price; LONG SQUEEZE is bearish.
- LONG LIQUIDATION CASCADE: intensity from price down + OI shrinking + CVD down, AND meaningful observed long-liq + direction. Intensity alone is not a cascade.
- SHORT LIQUIDATION CASCADE: mirror on the upside.
- SHORT_LIQ_EVENT true with SHORT_FORCED_FLOW false is a valid state (print only).
- Setup watch default 70. Absorption enter uses absorption.strength*100 >= 60.
- A high Long Trap Setup does NOT mean price must fall. A high Short Trap Setup does NOT mean price must rise.
- Do not mention "confirm enter 65". That threshold does not confirm traps.

## Forbidden
- Do not say the exchange is manipulating price.
- Do not invent liquidation clusters or "smart money".
- Do not contradict ENGINE_STATE or TRADE_STATUS.
- If evidence is thin, say UNCLEAR.

## Output format (no other sections)
ENGINE_STATE: <verbatim STATE>
TRADE_STATUS: <verbatim TRADE_STATUS>
SETUP_VS_CONFIRM: long setup/confirm-score = A/B ; short setup/confirm-score = C/D  (scores only; B and D are NOT gates)
CONFIRMATION_GATE: long=<true/false> short=<true/false>
LONG_TRAP_DEVELOPING: <verbatim true/false from SNAPSHOT>
LONG_TRAP_DEVELOPING_STRONG: <verbatim true/false from SNAPSHOT>
LONG_TRAP_DEVELOPING_SCORE: <verbatim number from SNAPSHOT>
VULNERABLE_SIDE: LONGS | SHORTS | UNCLEAR
ABSORPTION: NONE | BUY ABSORPTION | SELL ABSORPTION | UNCLEAR  (use engine flags, not vibes)
LIQ_EVENT: NONE | LONG | SHORT | BOTH
FORCED_FLOW: NONE | LONG FORCED FLOW | SHORT FORCED FLOW | CASCADE | UNCLEAR
TRAP_STATUS: NO TRAP | LONG SETUP ONLY | LONG TRAP DEVELOPING | LONG TRAP DEVELOPING STRONG | LONG SETUP+CONFIRM | SHORT SETUP ONLY | SHORT SETUP+CONFIRM
INVALIDATION: <what would break THIS state's reading, in the engine's terms>
COMMENT: <6-10 sentences applying the rules to the snapshot. Label any extra inference as INTERPRETATION.>
"""


TASK = """
Comment on the SNAPSHOT using ENGINE INTERPRETATION RULES above.
Do not recalculate scores. Do not narrate. Do not invent data.
Copy ENGINE_STATE, TRADE_STATUS, LONG_TRAP_DEVELOPING, and LONG_TRAP_CONFIRMATION_GATE
verbatim from VERBATIM ENGINE LABELS / JSON. Do not recompute developing from CVD or structure.
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
    g = scores.get("gates") or {}
    labels = {
        "ENGINE_STATE": state,
        "TRADE_STATUS": g.get("trade_status", "WAIT"),
        "LONG_TRAP_DEVELOPING": bool(g.get("long_trap_developing", False)),
        "LONG_TRAP_DEVELOPING_STRONG": bool(g.get("long_trap_developing_strong", False)),
        "LONG_TRAP_DEVELOPING_SCORE": g.get("long_trap_developing_score", 0),
        "LONG_TRAP_CONFIRMATION_GATE": bool(g.get("long_trap_confirmation", False)),
        "SHORT_TRAP_CONFIRMATION_GATE": bool(g.get("short_trap_confirmation", False)),
    }
    header = (
        "# VERBATIM ENGINE LABELS (copy these; do not recompute)\n"
        + "\n".join(f"{k}: {json.dumps(v) if isinstance(v, bool) else v}" for k, v in labels.items())
        + "\n\n# SNAPSHOT JSON\n"
    )
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
        "TRADE_STATUS": (scores.get("gates") or {}).get("trade_status", "WAIT"),
        "LONG_VULNERABILITY": (scores.get("gates") or {}).get("long_vulnerability", scores["long_setup"]["total"]),
        "SHORT_VULNERABILITY": (scores.get("gates") or {}).get("short_vulnerability", scores["short_setup"]["total"]),
        "LONG_TRAP_SETUP_SCORE": scores["long_setup"]["total"],
        "LONG_TRAP_CONFIRM_SCORE_DIAGNOSTIC_ONLY": scores["long_confirm"]["total"],
        "LONG_TRAP_CONFIRMATION_GATE": (scores.get("gates") or {}).get("long_trap_confirmation", False),
        "LONG_TRAP_DEVELOPING": (scores.get("gates") or {}).get("long_trap_developing", False),
        "LONG_TRAP_DEVELOPING_STRONG": (scores.get("gates") or {}).get("long_trap_developing_strong", False),
        "LONG_TRAP_DEVELOPING_SCORE": (scores.get("gates") or {}).get("long_trap_developing_score", 0),
        "LONG_TRAP_DEVELOPING_DETAIL": (scores.get("gates") or {}).get("long_trap_developing_detail")
        or scores.get("long_trap_developing")
        or {},
        "SHORT_TRAP_SETUP_SCORE": scores["short_setup"]["total"],
        "SHORT_TRAP_CONFIRM_SCORE_DIAGNOSTIC_ONLY": scores["short_confirm"]["total"],
        "SHORT_TRAP_CONFIRMATION_GATE": (scores.get("gates") or {}).get("short_trap_confirmation", False),
        "LONG_LIQ_EVENT": (scores.get("gates") or {}).get("long_liq_event", False),
        "SHORT_LIQ_EVENT": (scores.get("gates") or {}).get("short_liq_event", False),
        "LONG_LIQ_LEVEL": (scores.get("gates") or {}).get("long_liq_level", "none"),
        "SHORT_LIQ_LEVEL": (scores.get("gates") or {}).get("short_liq_level", "none"),
        "LONG_FORCED_FLOW": (scores.get("gates") or {}).get("long_forced_flow", False),
        "SHORT_FORCED_FLOW": (scores.get("gates") or {}).get("short_forced_flow", False),
        "LONG_SQUEEZE": (scores.get("gates") or {}).get("long_squeeze", False),
        "SHORT_SQUEEZE": (scores.get("gates") or {}).get("short_squeeze", False),
        "CASCADE_LONG_INTENSITY": scores.get("cascade_long"),
        "CASCADE_SHORT_INTENSITY": scores.get("cascade_short"),
        "LONG_CASCADE_GATE": (scores.get("gates") or {}).get("long_cascade", False),
        "SHORT_CASCADE_GATE": (scores.get("gates") or {}).get("short_cascade", False),
        "TRADE_STATUS_REASON": (scores.get("gates") or {}).get("trade_status_reason", ""),
        "EXPLANATION": (scores.get("gates") or {}).get("explanation_text", ""),
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
            "LONG_TRAP_DEVELOPING true with CONFIRM false is valid",
        ],
    }
    payload.update(labels)
    return header + json.dumps(payload, default=str, indent=2)

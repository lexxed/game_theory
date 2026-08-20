"""Deterministic narrative templates. No model, no certainty language."""


def interpret(symbol: str, snap: dict, scores: dict, state: str, reason: str) -> str:
    px = snap.get("price") or 0.0
    oi = snap.get("oi") or {}
    fund = snap.get("funding") or 0.0
    ls = scores["long_setup"]["total"]
    lc = scores["long_confirm"]["total"]
    ss = scores["short_setup"]["total"]
    sc = scores["short_confirm"]["total"]
    headline = _headline(state, ls, lc, ss, sc)

    facts = []
    facts.append(f"Price {float(px):.4f} | 24h {snap.get('change_24h_pct', 0):+.2f}%")
    facts.append(
        f"OI {oi.get('oi', 0):,.4g} | Δ1m {oi.get('chg_1m_pct', 0):+.3f}% "
        f"Δ15m {oi.get('chg_15m_pct', 0):+.3f}% Δ1h {oi.get('chg_1h_pct', 0):+.3f}%"
    )
    facts.append(
        f"Funding {fund:.6f} (pctile {snap.get('funding_pctile', 50):.0f}) — "
        "a transfer between longs and shorts, not proof of who is positioned."
    )
    facts.append(
        f"CVD {snap.get('cvd', 0):+.4g} | Δ15m {snap.get('cvd_chg_15m', 0):+.4g} | "
        f"delta 3m {snap.get('delta_3m', 0):+.4g}"
    )
    liq = snap.get("liq_15m") or {}
    facts.append(
        f"OBSERVED liq 15m: long ${liq.get('long_notional', 0):,.0f} "
        f"({liq.get('long_n', 0)} ev) / short ${liq.get('short_notional', 0):,.0f} "
        f"({liq.get('short_n', 0)} ev). Incomplete public force-order sample."
    )
    if snap.get("ls_account_ratio") is not None:
        facts.append(
            f"Global account long/short ratio {snap['ls_account_ratio']:.3f} "
            "(Binance advertised account ratio — not open-interest split)."
        )
    if snap.get("cvd_div", {}).get("reason"):
        facts.append("CVD: " + snap["cvd_div"]["reason"])
    if snap.get("absorption", {}).get("reason"):
        facts.append("Footprint: " + snap["absorption"]["reason"])
    if snap.get("structure", {}).get("reason"):
        facts.append("Structure: " + snap["structure"]["reason"])

    game = _game_block(state, snap, ls, lc, ss, sc)

    lines = [
        f"SYMBOL {symbol}   STATE: {state}",
        "",
        headline,
        "",
        "OBSERVED FACTS",
        *[f"  • {x}" for x in facts],
        "",
        "INTERPRETATION (not a forecast)",
        f"  Long trap setup {ls:.0f}/100  confirm {lc:.0f}/100",
        f"  Short trap setup {ss:.0f}/100  confirm {sc:.0f}/100",
        f"  Cascade intensity (NOT a cascade)  long {scores.get('cascade_long', 0):.0f}  short {scores.get('cascade_short', 0):.0f}",
        f"  State reason: {reason}",
        "",
        "CURRENT GAME",
        game,
        "",
        "WHAT WOULD INVALIDATE THIS",
        _invalidate(state, snap),
        "",
        "LIMITS: OI change is not side-identified. Funding is not positioning. "
        "CVD is aggressive flow, not trader identity. Liquidations are a sampled public stream. "
        "A high setup score does not mean price must move.",
    ]
    return "\n".join(lines)


def _headline(state: str, ls, lc, ss, sc) -> str:
    if "LONG LIQUIDATION CASCADE" in state:
        return "LONG CASCADE: meaningful long liquidations + shrinking OI + falling price + falling CVD."
    if "SHORT LIQUIDATION CASCADE" in state:
        return "SHORT CASCADE: meaningful short liquidations + shrinking OI + rising price + rising CVD."
    if state == "LONG SQUEEZE":
        return "LONGS LOOK SQUEEZED (price down, OI down, CVD down, meaningful long liqs) — could also be voluntary covering."
    if state == "SHORT SQUEEZE":
        return "SHORTS LOOK SQUEEZED (price up, OI down, CVD up, meaningful short liqs) — could also be voluntary covering."
    if state == "LONG FORCED FLOW":
        return "LONG FORCED FLOW: meaningful long liquidations with falling price, falling CVD, and declining OI. Not a trap by itself."
    if state == "SHORT FORCED FLOW":
        return "SHORT FORCED FLOW: meaningful short liquidations with rising price, rising CVD, and declining OI. Not a trap by itself."
    if state == "LONG TRAP DEVELOPING STRONG":
        return "LONG TRAP DEVELOPING STRONG — early warning (crowding + several short-term reversals). NOT confirmation."
    if state == "LONG TRAP DEVELOPING":
        return "LONG TRAP DEVELOPING — early warning. NOT confirmation. Existing confirm gate unchanged."
    if "LONG TRAP" in state:
        return "LONG-SIDE VULNERABILITY (setup). Confirmed only if structure+flow GATE is true — not via confirm score."
    if "SHORT TRAP" in state:
        return "SHORT-SIDE VULNERABILITY (setup). Confirmed only if structure+flow GATE is true — not via confirm score."
    if "BUY ABSORPTION" in state:
        return "AGGRESSIVE BUYING IS NOT MOVING PRICE MUCH — possible seller absorption."
    if "SELL ABSORPTION" in state:
        return "AGGRESSIVE SELLING IS NOT MOVING PRICE MUCH — possible buyer absorption."
    if "FAILED BREAKOUT" in state:
        return "BREAKOUT FAILED — range high was traded and rejected."
    if "FAILED BREAKDOWN" in state:
        return "BREAKDOWN FAILED — range low was traded and rejected."
    if state == "BREAKOUT":
        return "CLEAN BREAKOUT — close held above prior range high with OI expanding and CVD confirming."
    if state == "BREAKDOWN":
        return "CLEAN BREAKDOWN — close held below prior range low with OI expanding and CVD confirming."
    if "LONG-SIDE CROWDING" in state:
        return "LONG-SIDE CROWDING ESTIMATE — funding/account-ratio PROXY, not actual long OI."
    if "SHORT-SIDE CROWDING" in state:
        return "SHORT-SIDE CROWDING ESTIMATE — funding/account-ratio PROXY, not actual short OI."
    return "NO SIDE IS CLEARLY FORCED. Neutral / mixed evidence."


def _game_block(state: str, snap: dict, ls, lc, ss, sc) -> str:
    support = (snap.get("structure") or {}).get("swing_low")
    resist = (snap.get("structure") or {}).get("swing_high")
    return (
        f"  Longs: need price to hold above local support {support}.\n"
        f"  Shorts: benefit if support breaks; harmed if resistance {resist} is reclaimed.\n"
        "  Exchange: liquidates under-margined accounts under published risk rules; "
        "it earns fees and manages insurance-fund risk. That is incentive, not evidence of manipulation.\n"
        "  Potential feedback: trigger → forced close/liquidation → same-direction flow → "
        "further trigger — until the book is exhausted and a new equilibrium forms.\n"
        f"  Setup vs confirmation: long {ls:.0f}/{lc:.0f}   short {ss:.0f}/{sc:.0f} "
        "(high setup + low confirm = vulnerable but not yet forced)."
    )


def _invalidate(state: str, snap: dict) -> str:
    if "LONG TRAP" in state or "LONG LIQUIDATION" in state or state == "SHORT SQUEEZE":
        return (
            "  • Price reclaims and holds above the local high / last failed-breakout level.\n"
            "  • OI starts expanding again with a clean CVD higher-high.\n"
            "  • Observed long-liquidation flow dries up and funding mean-reverts without a break."
        )
    if "SHORT TRAP" in state or "SHORT LIQUIDATION" in state or state == "LONG SQUEEZE":
        return (
            "  • Price loses the reclaimed level and makes a clean lower low with CVD confirmation.\n"
            "  • OI expands into the decline again.\n"
            "  • Observed short-liquidation flow dries up."
        )
    if state == "BREAKOUT":
        return (
            "  • Price closes back below the broken range high (reverts to failed-breakout territory).\n"
            "  • OI stops expanding or CVD stops following — conviction was not real."
        )
    if state == "BREAKDOWN":
        return (
            "  • Price closes back above the broken range low (reverts to failed-breakdown territory).\n"
            "  • OI stops expanding or CVD stops following — conviction was not real."
        )
    return "  • A one-sided combination of OI + CVD + observed liquidations + structure break."

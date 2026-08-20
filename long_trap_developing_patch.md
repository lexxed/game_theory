# LONG TRAP DEVELOPING — GROK CLI PATCH SPECIFICATION

Apply this to the existing trading engine. Inspect the repository first and adapt
variable/function names to the existing architecture. Do not rewrite the system
unnecessarily.

GOAL
Add an early-warning state:

LONG TRAP DEVELOPING

between LONG TRAP SETUP and LONG TRAP CONFIRMING.

Do NOT weaken or replace the existing LONG TRAP CONFIRMING logic.

--------------------------------------------------
1. CONFIGURATION
--------------------------------------------------

Add these configurable constants:

EARLY_PRICE_REVERSAL_PCT = 0.20
STRONG_EARLY_PRICE_REVERSAL_PCT = 0.40
EARLY_OI_UNWIND_5M_PCT = -0.50

LONG_TRAP_DEVELOPING_THRESHOLD = 35.0
LONG_TRAP_DEVELOPING_STRONG_THRESHOLD = 50.0
LONG_CROWDING_EARLY_THRESHOLD = 50.0

Keep these in the existing config system if one exists.

--------------------------------------------------
2. EARLY PRICE REVERSAL
--------------------------------------------------

Use existing short-term swing/high logic where possible.

Calculate:

drawdown_from_recent_high_pct =
    (recent_local_high - current_price) / recent_local_high * 100.0

Then:

early_price_reversal =
    drawdown_from_recent_high_pct >= EARLY_PRICE_REVERSAL_PCT

strong_early_price_reversal =
    drawdown_from_recent_high_pct >= STRONG_EARLY_PRICE_REVERSAL_PCT

CRITICAL:
recent_local_high must only use information available at the current
timestamp. No future candles.

--------------------------------------------------
3. EARLY CVD REVERSAL
--------------------------------------------------

Reuse existing CVD calculations.

Prefer changes rather than absolute cumulative CVD:

cvd_reversal_1m = cvd_change_1m < 0
cvd_reversal_3m = cvd_change_3m < 0
cvd_reversal_5m = cvd_change_5m < 0

Prefer:

early_cvd_reversal = cvd_reversal_1m AND cvd_reversal_3m

If 3m data does not exist, use the closest available short timeframe.

Do not create a duplicate CVD calculation.

--------------------------------------------------
4. EARLY OI UNWIND
--------------------------------------------------

Use existing OI changes.

early_oi_unwind =
    oi_change_5m <= EARLY_OI_UNWIND_5M_PCT
    AND price_change_5m < 0

Optionally use OI 1m as supporting evidence.

Do NOT claim this proves longs closed. OI is not side-identified.

Use wording such as:
"OI shrinking while price declines."

--------------------------------------------------
5. SHORT-TIMEFRAME SUPPORT BREAK
--------------------------------------------------

Use existing swing/support logic where possible.

Add:

early_support_break

using 1m/3m/5m structure.

Conceptually:

early_support_break =
    short_term_support_exists
    AND current_price < short_term_support
    AND current_close < short_term_support

Do not replace the existing 15m lost_support logic.

--------------------------------------------------
6. DEVELOPING SCORE
--------------------------------------------------

Create:

long_trap_developing_score = 0.0

Components:

crowding/vulnerability       0-20
early price reversal         0-20
early CVD reversal            0-20
early OI unwind               0-20
short-term support break      0-20

Use the EXISTING LONG CROWDING score.

Normalize it:

crowding_component =
    min(max(long_crowding, 0.0), 100.0) * 0.20

Then:

price_component = 20.0 if early_price_reversal else 0.0
cvd_component = 20.0 if early_cvd_reversal else 0.0
oi_component = 20.0 if early_oi_unwind else 0.0
structure_component = 20.0 if early_support_break else 0.0

long_trap_developing_score =
    crowding_component
    + price_component
    + cvd_component
    + oi_component
    + structure_component

--------------------------------------------------
7. DEVELOPING GATE
--------------------------------------------------

Do not allow crowding alone to trigger it.

Calculate:

early_evidence_count = sum([
    early_price_reversal,
    early_cvd_reversal,
    early_oi_unwind,
    early_support_break,
])

Then:

long_trap_developing =
    long_crowding >= LONG_CROWDING_EARLY_THRESHOLD
    AND long_trap_developing_score >= LONG_TRAP_DEVELOPING_THRESHOLD
    AND early_evidence_count >= 2

--------------------------------------------------
8. STRONG DEVELOPING
--------------------------------------------------

Add:

long_trap_developing_strong =
    long_crowding >= LONG_CROWDING_EARLY_THRESHOLD
    AND long_trap_developing_score >= LONG_TRAP_DEVELOPING_STRONG_THRESHOLD
    AND early_evidence_count >= 3

This is still NOT full confirmation.

--------------------------------------------------
9. STATUS PRIORITY
--------------------------------------------------

Use:

if existing_long_trap_confirm:
    status = "LONG-TRAP CONFIRMING"
elif long_trap_developing_strong:
    status = "LONG TRAP DEVELOPING STRONG"
elif long_trap_developing:
    status = "LONG TRAP DEVELOPING"
else:
    preserve_existing_status_logic

Do NOT automatically make DEVELOPING a trade entry.

The existing trade gate remains authoritative.

--------------------------------------------------
10. DO NOT CHANGE EXISTING CONFIRMATION
--------------------------------------------------

Do not modify:

LONG TRAP CONFIRM
LONG confirmation score
LONG confirmation gate
forced-flow gate
cascade gate
trade gate

The new developing layer is additive.

--------------------------------------------------
11. SNAPSHOT OUTPUT
--------------------------------------------------

Add:

LONG TRAP DEVELOPING: YES/NO
LONG TRAP DEVELOPING SCORE: xx/100
LONG TRAP DEVELOPING STRONG: YES/NO

EARLY SIGNALS
  price_reversal: YES/NO
  cvd_reversal: YES/NO
  oi_unwind: YES/NO
  short_term_support_break: YES/NO
  evidence_count: x/4

Example:

LONG TRAP DEVELOPING: YES
LONG TRAP DEVELOPING SCORE: 47.5/100
LONG TRAP DEVELOPING STRONG: NO

EARLY SIGNALS
  price_reversal: YES
  cvd_reversal: YES
  oi_unwind: YES
  short_term_support_break: NO
  evidence_count: 3/4

LONG TRAP CONFIRM: NO
TRADE GATE: WAIT

--------------------------------------------------
12. REASON STRING
--------------------------------------------------

Generate a concise reason, for example:

"crowded long-side proxy + short-term price reversal +
negative CVD + declining OI"

Do not say "longs are closing" because OI is not side-identified.

--------------------------------------------------
13. NO LOOK-AHEAD BIAS
--------------------------------------------------

Mandatory.

Never use future:
- candle highs
- candle lows
- closes
- support/resistance
- CVD
- OI
- liquidations

The backtest must receive exactly the information that was available at
that timestamp.

--------------------------------------------------
14. BACKTEST COMPATIBILITY
--------------------------------------------------

All new calculations must work in historical/backtest mode.

Do not introduce live-only dependencies.

--------------------------------------------------
15. TESTING
--------------------------------------------------

After implementation:

1. Run existing tests.
2. Run lint/type checks if available.
3. Test a neutral snapshot.
4. Test a crowded long-side reversal.
5. Test a confirmed long trap.
6. Verify DEVELOPING can appear before CONFIRMING.
7. Verify existing CONFIRMING behavior is unchanged.
8. Verify no look-ahead bias.
9. Show files changed.
10. Show a concise diff summary.

IMPORTANT DESIGN:

LONG CROWDING
    ↓
LONG TRAP SETUP
    ↓
LONG TRAP DEVELOPING       <-- new early warning
    ↓
LONG TRAP CONFIRMING       <-- existing high-confidence confirmation
    ↓
TRADE GATE

Do not simply lower the existing confirmation threshold.

The goal is earlier detection, not weaker confirmation.

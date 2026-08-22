# Cross-exchange + aggregator signals, and per-signal attribution

Applied against `main` at the current HEAD. Verified: clean `git apply` on a
fresh clone, 91/91 tests pass (`test_live_connectivity.py` excluded — it
hits the real Binance REST endpoint and isn't relevant to this change).

## Apply it

```
cd game_theory
git apply cross_exchange_aggregator_signals.patch
pip install -r requirements.txt
python -m pytest tests -q -k "not live_connectivity"
```

## 1. New signals

**`src/external_venues.py`** — keyless public REST calls:
- OKX funding rate + last price (`/api/v5/public/funding-rate`, `/api/v5/market/ticker`)
- Bybit funding rate + last price (`/v5/market/tickers`, linear category)
- Crypto.com Exchange public ticker (`/exchange/v1/public/get-tickers`) for price + 24h volume
- CoinGecko `/api/v3/global` for BTC dominance and total market-cap 24h change
- CoinMarketCap global metrics as an optional fallback — **requires an API
  key**, unlike the others. Set `aggregators.coinmarketcap_api_key` in
  `config.yaml` or the `CMC_API_KEY` env var. Left unset, this source is
  simply skipped; CoinGecko is preferred whenever both are available.

Every function fails soft (returns `None` on any network/parse error) so a
slow or down third party can never block or crash the main Binance feed.

**`src/external_signals.py`** — `ExternalSignalTracker`, one per symbol,
same shape as `FundingTracker`/`OpenInterestTracker`: background thread,
own poll cadence (`external_signals.cross_venue_poll_s` = 30s for
funding/price, `external_signals.regime_poll_s` = 300s for the slower-moving
aggregator data — both configurable), thread-safe `.compute()` combining the
latest fetch with the live Binance funding/price passed in.

Only `BTCUSDT` and `ETHUSDT` have a venue map (`_SYMBOL_MAP`) right now —
add entries there for more symbols. On an unmapped symbol the tracker stays
idle and `.compute()` returns all-`None` values (handled explicitly by
scoring, not treated as neutral/zero).

## 2. Two new scoring components (setup layer)

Added to `weights_setup` in `config.yaml`, weights taken proportionally
from the existing nine components (the file has a running comment noting
this is the third such rebalance, after `book_imbalance` and `taker_flow`):

| component | weight | what it measures |
|---|---|---|
| `cross_funding_divergence` | 5 | Binance funding vs. OKX/Bybit average — is the crowding read Binance-specific leverage, or market-wide? |
| `market_regime` | 5 | BTC dominance momentum + total mcap 24h change — coarse risk-on/risk-off context |

Both score 0 (not a fabricated 50/neutral) when their data hasn't arrived
yet or the symbol has no venue map — see `test_external_signals_missing_scores_zero_not_crash`
in `tests/test_scoring.py`.

**Existing test assertions updated** to match: component count 9 → 11,
`test_weights_sum_displayed` now expects 100 (the new weights happen to sum
to exactly 100, replacing the old 98), and the crowding/divergence
threshold test's expected minimum score was lowered from 60 to 50 since the
same fixture inputs now score against a smaller weight pool.

## 3. Storage: full component breakdown, not just totals

`scores` table gets one new column: `components_json` — the full
`long_setup`/`short_setup` component breakdown (name, raw, normalized,
weight, points, reason) as a JSON string, for every flush. Existing
databases migrate automatically (`Storage._migrate()`, `ADD COLUMN IF NOT
EXISTS`, safe to run every startup).

Confirm-layer components are intentionally **not** included — they gate on
binary structure breaks that are better analyzed via the existing
state-transition backtest than via IC/correlation.

## 4. `attribution.py` — per-signal attribution

Separate from `backtest.py` (which reports forward returns after the
*total* score crosses a threshold). This instead asks, per component,
independently: did this component's raw value actually correlate with what
happened afterward?

```
python attribution.py --symbol BTCUSDT --side long
python attribution.py --symbol BTCUSDT --side short --top 15
```

For each component x horizon (5m/15m/30m/1h/4h): Pearson IC, Spearman IC,
and top-vs-bottom-quintile return spread (median split for low-cardinality
components, e.g. binary flags, where a real quintile split isn't
meaningful). Sign is flipped for the short side so a **positive number
always means "this component moved toward the adverse-for-that-side
direction before price did"** — i.e. it worked as intended — for both long
and short reports.

Explicit limits printed at the bottom of every report: correlational, not
causal, no transaction costs, no walk-forward split, small-n components are
exploratory only (`n_nonzero < 30`). This is a signal-quality triage tool,
not a validated trading edge.

## Suggested next steps (not in this patch)

- Extend `_SYMBOL_MAP` in `external_signals.py` for any symbols beyond BTC/ETH you trade
- Once `attribution.py` has enough data to be meaningful, use it to re-tune
  `weights_setup` — the two new components deliberately started small (5
  each) until they're validated
- `backtest.py` is still single-symbol / hardcoded-15m-candles / no
  transaction costs; `attribution.py` doesn't fix that, it answers a
  different question (component quality, not strategy PnL) — a realistic
  strategy simulator (entries, costs, drawdown) would be a separate patch

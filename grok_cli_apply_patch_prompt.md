# Task: Apply `cross_exchange_aggregator_signals.patch`

You are working in the `game_theory` repo (root of this checkout). A patch
file named `cross_exchange_aggregator_signals.patch` has been placed in the
repo root. Apply it, verify it, and report back — do not improvise around
it or reinterpret the design; the patch is the spec.

## What this patch does (context, so you can sanity-check as you go)

Adds cross-exchange and aggregator market signals to the scoring engine,
plus a new attribution tool to measure whether each scoring component
actually predicts forward returns:

1. **`src/external_venues.py`** (new) — keyless public REST calls to OKX,
   Bybit, and Crypto.com Exchange for funding rate / price, and to
   CoinGecko for BTC dominance + total market-cap change. CoinMarketCap is
   included as an optional fallback that requires an API key
   (`aggregators.coinmarketcap_api_key` in `config.yaml` or `CMC_API_KEY`
   env var) — leave it unset and it's simply skipped.
2. **`src/external_signals.py`** (new) — `ExternalSignalTracker`, a
   background poller per symbol (same pattern as `FundingTracker`), only
   mapped for `BTCUSDT`/`ETHUSDT` currently via `_SYMBOL_MAP`.
3. **`src/scoring.py`** — two new weighted setup components:
   `cross_funding_divergence` and `market_regime` (weight 5 each, taken
   proportionally from the existing nine components).
4. **`config.yaml`** — updated `weights_setup`, plus new
   `external_signals.*` poll-interval settings and `aggregators.*` key
   config.
5. **`src/market_data.py`** — wires the tracker into the
   start/stop/symbol-switch lifecycle and feeds its output into
   `_features()` under an `"external"` key.
6. **`src/storage.py`** — adds a `components_json` column to the `scores`
   table (full per-component breakdown, JSON-encoded) with an automatic
   migration for existing DB files.
7. **`attribution.py`** (new, repo root) — CLI tool:
   `python attribution.py --symbol BTCUSDT --side long`. Computes Pearson/
   Spearman correlation and quintile return spread per component per
   horizon, sign-adjusted so positive always means "worked as intended."
8. **Tests** — `tests/test_scoring.py` updated (component count, weight
   sum, one threshold assertion) and `tests/test_attribution.py` added.

## Steps

1. From the repo root, dry-run the patch first:
   ```
   git apply --check cross_exchange_aggregator_signals.patch
   ```
   If this fails, STOP and report the exact error — do not hand-patch
   around a conflict without showing me the diff first.

2. Apply it for real:
   ```
   git apply cross_exchange_aggregator_signals.patch
   ```

3. Install/confirm dependencies (the patch adds no new packages, but
   confirm `requests`, `pandas`, `numpy`, and `duckdb` are present):
   ```
   pip install -r requirements.txt
   ```

4. Run the full test suite, excluding the network-dependent live test:
   ```
   python -m pytest tests -q -k "not live_connectivity"
   ```
   Expected: all tests pass (91 at the time this patch was written — the
   exact count may drift if the repo has moved since, that's fine, just
   confirm zero failures).

5. Sanity-check the new CLI tool runs cleanly against the current (likely
   empty or stale-schema) DuckDB store:
   ```
   python attribution.py --symbol BTCUSDT --side long
   ```
   It should either print a report or a clear "no stored components yet"
   message — it must not raise a traceback either way.

6. Confirm the storage migration is non-destructive: if a `data/*.duckdb`
   file already exists in this checkout, start the dashboard briefly (or
   just import `src.storage.Storage()` and instantiate it) and confirm no
   exception is raised and existing rows in the `scores` table are still
   readable (`SELECT * FROM scores LIMIT 5`).

7. Report back with:
   - Confirmation the patch applied cleanly (or the exact conflict, if not)
   - Test suite pass/fail summary
   - Output of the `attribution.py` sanity run
   - Anything in the diff that looks like it conflicts with local changes
     already in this checkout that weren't present when the patch was
     generated (e.g. if `config.yaml`'s `weights_setup` or `scores` schema
     has since been hand-edited here)

## Do not

- Do not alter the scoring weights, thresholds, or component logic beyond
  what's in the patch — if something looks wrong, report it, don't fix it
  silently.
- Do not add the two new components to `gates.py` or `state_machine.py` —
  this patch intentionally only touches the setup-score layer, not
  squeeze/cascade/forced-flow gate logic.
- Do not commit the change automatically. Leave it staged/unstaged for
  review unless explicitly told to commit.

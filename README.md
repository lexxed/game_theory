# Binance USD-M Game Theory Dashboard

Live **analysis-only** Jupyter dashboard for USDⓈ-M perpetual futures.

- Public REST + WebSocket market data (no API key)
- Does **not** place orders
- Deterministic scores in Python
- Optional local `grok` CLI comment that must use this engine's state labels

## Install

```powershell
cd C:\Users\lexxt\backtest_workspace\binance_game_theory
python -m pip install -r requirements.txt
```

## Launch JupyterLab

`jupyter` may not be on PATH. Use the module form:

```powershell
cd C:\Users\lexxt\backtest_workspace
python -m jupyterlab
```

## Start the dashboard

In JupyterLab open:

`binance_game_theory/dashboard.ipynb`

Run the first code cell. Click **START**.

## Tests

```powershell
cd C:\Users\lexxt\backtest_workspace\binance_game_theory
python -m pytest tests -q
```

If pytest is not installed: `python -m pip install pytest` or run files directly:

```powershell
python tests/test_cvd.py
python tests/test_scoring.py
python tests/test_footprint.py
python tests/test_state_machine.py
python tests/test_trackers.py
python tests/test_live_connectivity.py
```

## Change score weights

Edit `config.yaml`:

```yaml
weights_setup:
  crowding: 15
  oi_behavior: 15
  funding: 10
  cvd_divergence: 20
  absorption: 20
  liquidation_risk: 15
  price_structure: 5
```

Then click STOP / START (or `app.engine.reload_config()`).

## Grok

Optional button **Grok comment** sends the snapshot plus this engine's
interpretation rules (state names, setup vs confirm, CVD/absorption/liq
definitions). Grok must comment in that vocabulary. It does not re-score.

`grok -p --output-format plain --max-turns 1 --disable-web-search --verbatim`

## Storage

`data/market.duckdb` plus optional parquet exports under `data/parquet/`.

## Backtest

```powershell
python backtest.py --symbol BTCUSDT --score long_setup --min 80
```

Empty until the dashboard has flushed snapshots. Not a performance claim.

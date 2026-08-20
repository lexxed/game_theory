import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.binance_client import BinanceRESTError, select_exchange_symbol
from src.utils import bucket_price, format_price_level


def test_select_exchange_symbol_does_not_use_first_entry():
    info = {
        "symbols": [
            {"symbol": "BTCUSDT", "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.10"}]},
            {"symbol": "REDUSDT", "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.0001"}]},
        ]
    }
    s = select_exchange_symbol(info, "REDUSDT")
    assert s["symbol"] == "REDUSDT"
    with pytest.raises(BinanceRESTError):
        select_exchange_symbol(info, "NOPEUSDT")


def test_wrong_btc_tick_collapses_red_prices():
    # What the bug did: BTC tick 0.10 applied to RED ~0.107
    assert abs(bucket_price(0.1071, 0.10) - 0.1) < 1e-12
    assert abs(bucket_price(0.1070, 0.10) - 0.1) < 1e-12
    assert format_price_level(0.1, 0.10, "0.10") == "0.1"


def test_red_tick_keeps_adjacent_trade_prices():
    tick = 0.0001
    a = bucket_price(0.1070, tick)
    b = bucket_price(0.1071, tick)
    assert abs(a - 0.1070) < 1e-12
    assert abs(b - 0.1071) < 1e-12
    assert format_price_level(a, tick, "0.0001") != format_price_level(b, tick, "0.0001")
    assert format_price_level(b, tick, "0.0001") == "0.1071"

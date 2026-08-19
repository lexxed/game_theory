"""Live public-endpoint smoke test. Requires network. Skip-safe if offline."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_rest_and_ws_public():
    import asyncio
    import time

    import requests
    import websockets

    r = requests.get("https://fapi.binance.com/fapi/v1/ping", timeout=10)
    assert r.status_code == 200
    oi = requests.get(
        "https://fapi.binance.com/fapi/v1/openInterest",
        params={"symbol": "BTCUSDT"},
        timeout=10,
    )
    assert oi.status_code == 200
    assert float(oi.json()["openInterest"]) > 0

    async def ws():
        url = "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"
        async with websockets.connect(url, open_timeout=10) as sock:
            raw = await asyncio.wait_for(sock.recv(), timeout=8)
            msg = json.loads(raw)
            assert msg["stream"] == "btcusdt@aggTrade"
            assert msg["data"]["e"] == "aggTrade"
            # m = isBuyerMaker
            assert "m" in msg["data"]

    asyncio.run(ws())
    # reconnect smoke: open, close, open again
    async def recon():
        url = "wss://fstream.binance.com/market/ws/btcusdt@markPrice@1s"
        async with websockets.connect(url, open_timeout=10) as s1:
            await asyncio.wait_for(s1.recv(), timeout=5)
        async with websockets.connect(url, open_timeout=10) as s2:
            msg = json.loads(await asyncio.wait_for(s2.recv(), timeout=5))
            assert msg["e"] == "markPriceUpdate"

    asyncio.run(recon())
    time.sleep(0.1)

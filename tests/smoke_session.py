"""Start a live session for ~12s and print a snapshot. Network required."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.market_data import MarketSession


def main():
    s = MarketSession()
    print("seeding BTCUSDT…")
    s.start("BTCUSDT", "15m")
    time.sleep(12)
    snap = s.snapshot()
    s.stop()
    f = snap["features"]
    print("symbol", snap["symbol"], "state", snap["state"])
    print("price", f.get("price"), "oi", (f.get("oi") or {}).get("oi"))
    print("funding", f.get("funding"), "cvd", f.get("cvd"))
    print("long_setup", snap["scores"]["long_setup"]["total"], "short_setup", snap["scores"]["short_setup"]["total"])
    print("health", {k: v["state"] for k, v in snap["health"].items()})
    print("klines 15m", len(snap["klines"]["15m"]), "footprint levels", len(snap["footprint"]))
    print("ws", snap["ws_url"])
    print("error", snap["error"] or "none")
    assert f.get("price"), "no live price"
    assert (f.get("oi") or {}).get("oi"), "no OI"
    assert snap["health"]["ws"]["state"] in ("LIVE", "CONNECTING")
    print("SMOKE OK")


if __name__ == "__main__":
    main()

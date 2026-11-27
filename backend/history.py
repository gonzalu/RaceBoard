"""Every Grand Prix winner since 1950, for the track and "on this day" pages.

One list of about 1,200 races from Jolpica, fetched in pages of 100 and kept
in data/history.json so a restart doesn't fetch it again. Refreshed weekly,
which picks up each new winner.
"""

import asyncio
import json
import logging
import time
from collections import Counter
from pathlib import Path

from . import config

log = logging.getLogger("raceboard.history")

CACHE = Path(__file__).resolve().parent.parent / "data" / "history.json"
MAX_AGE_S = 7 * 24 * 3600
PAGE = 100

_races = []


def _code(driver):
    return driver.get("code") or driver["familyName"][:3].upper()


def _load():
    global _races
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        _races = data["races"]
        return data.get("fetched", 0)
    except (OSError, ValueError, KeyError):
        return 0


async def refresh(c):
    """Fetch the whole winners list unless the cached copy is recent."""
    global _races
    fetched = _load()
    if _races and time.time() - fetched < MAX_AGE_S:
        return
    races, offset, total = {}, 0, 1
    while offset < total:
        r = await c.get(f"{config.JOLPICA}/results/1.json", params={"limit": PAGE, "offset": offset})
        r.raise_for_status()
        d = r.json()["MRData"]
        total = int(d["total"])
        for race in d["RaceTable"]["Races"]:
            key = (race["season"], race["round"])
            if key in races:   # a shared drive: two winners, keep the first
                continue
            w = race["Results"][0]
            races[key] = {
                "season": int(race["season"]), "round": int(race["round"]),
                "name": race["raceName"], "date": race["date"],
                "circuitId": race["Circuit"]["circuitId"],
                "locality": race["Circuit"]["Location"]["locality"],
                "code": _code(w["Driver"]),
                "driver": w["Driver"]["familyName"],
                "team": w["Constructor"]["constructorId"],
            }
        offset += PAGE
        await asyncio.sleep(1)   # Jolpica asks for a few requests a second at most
    _races = sorted(races.values(), key=lambda x: (x["season"], x["round"]))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"fetched": time.time(), "races": _races}), encoding="utf-8")
    log.info("history: %d races", len(_races))


def at_circuit(circuit_id):
    """What has happened at this track: first GP, how many, who wins here."""
    here = [r for r in _races if r["circuitId"] == circuit_id]
    if not here:
        return None
    wins = Counter(r["driver"] for r in here).most_common()
    top = wins[0] if wins and wins[0][1] > 1 else None
    return {
        "first": here[0]["season"],
        "count": len(here),
        "most_wins": {"driver": top[0].upper(), "wins": top[1]} if top else None,
        "recent": [{"season": r["season"], "code": r["code"], "team": r["team"]} for r in here[-5:][::-1]],
    }


def on_this_day(month, day):
    """Races held on this date in past years, newest first."""
    md = f"-{month:02d}-{day:02d}"
    hits = [r for r in _races if r["date"].endswith(md)]
    return [{"season": r["season"], "name": r["name"], "driver": r["driver"].upper(),
             "team": r["team"]} for r in hits[::-1]]

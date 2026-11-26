"""Between sessions: calendar, standings and the last result, from Jolpica.

Jolpica (api.jolpi.ca) carries on the old Ergast API: free, no key, updated a
few hours after each session. It isn't live, which is fine for these pages.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from . import config

log = logging.getLogger("raceboard.schedule")

REFRESH_S = 30 * 60

# Jolpica's constructorId -> livery colour. The live feed carries its own
# colours, but between weekends there is no feed to ask.
TEAM_COLOURS = {
    "mercedes": "00D7B6", "ferrari": "ED1131", "red_bull": "4781D7",
    "mclaren": "F47600", "rb": "6C98FF", "alpine": "00A1E8",
    "haas": "9C9FA2", "audi": "F50537", "sauber": "F50537",
    "williams": "1868DB", "aston_martin": "229971", "cadillac": "909090",
}

SESSION_KEYS = [
    ("FirstPractice", "FP1"), ("SecondPractice", "FP2"), ("ThirdPractice", "FP3"),
    ("SprintQualifying", "SQ"), ("Sprint", "SPRINT"), ("Qualifying", "QUALI"),
]

# Team names are long and the panel is not; these fit a narrow column.
TEAM_SHORT = {
    "mercedes": "MERCEDES", "ferrari": "FERRARI", "red_bull": "RED BULL",
    "mclaren": "McLAREN", "rb": "RACING BULLS", "alpine": "ALPINE",
    "haas": "HAAS", "audi": "AUDI", "sauber": "SAUBER", "williams": "WILLIAMS",
    "aston_martin": "ASTON MARTIN", "cadillac": "CADILLAC",
}

_data = {"next": None, "drivers": [], "teams": [], "last": None, "updated": 0.0, "error": None}


def snapshot():
    return _data


def _dt(block):
    if not block or not block.get("date"):
        return None
    t = block.get("time") or "00:00:00Z"
    return datetime.fromisoformat(f"{block['date']}T{t.rstrip('Z')}").replace(tzinfo=timezone.utc)


def _race_sessions(race):
    out = []
    for key, label in SESSION_KEYS:
        when = _dt(race.get(key))
        if when:
            out.append({"name": label, "utc": when.isoformat()})
    when = _dt(race)
    if when:
        out.append({"name": "RACE", "utc": when.isoformat()})
    out.sort(key=lambda s: s["utc"])
    return out


def _colour(cid):
    return TEAM_COLOURS.get(cid, "FFFFFF")


async def _get(c, path):
    r = await c.get(f"{config.JOLPICA}/{path}")
    r.raise_for_status()
    return r.json()["MRData"]


async def refresh(c):
    nxt = await _get(c, "current/next.json")
    races = nxt["RaceTable"]["Races"]
    if not races:
        # off-season: Jolpica has no "next" until the new calendar is loaded
        nxt = await _get(c, f"{datetime.now(timezone.utc).year + 1}.json")
        races = nxt["RaceTable"]["Races"][:1]
    if races:
        r = races[0]
        _data["next"] = {
            "season": r["season"], "round": r["round"], "name": r["raceName"],
            "circuit": r["Circuit"]["circuitName"],
            "locality": r["Circuit"]["Location"]["locality"],
            "country": r["Circuit"]["Location"]["country"],
            "sessions": _race_sessions(r),
        }
    else:
        _data["next"] = None

    ds = (await _get(c, "current/driverstandings.json"))["StandingsTable"]["StandingsLists"]
    if ds:
        _data["standings_round"] = ds[0]["round"]
        _data["drivers"] = [{
            "pos": int(d["position"]) if d.get("position", "").isdigit() else None,
            "code": d["Driver"].get("code") or d["Driver"]["familyName"][:3].upper(),
            "points": d["points"],
            "colour": _colour(d["Constructors"][-1]["constructorId"] if d["Constructors"] else ""),
        } for d in ds[0]["DriverStandings"]]

    cs = (await _get(c, "current/constructorstandings.json"))["StandingsTable"]["StandingsLists"]
    if cs:
        _data["teams"] = [{
            "pos": int(t["position"]) if t.get("position", "").isdigit() else None,
            "name": TEAM_SHORT.get(t["Constructor"]["constructorId"], t["Constructor"]["name"].upper()),
            "points": t["points"],
            "colour": _colour(t["Constructor"]["constructorId"]),
        } for t in cs[0]["ConstructorStandings"]]

    last = (await _get(c, "current/last/results.json"))["RaceTable"]["Races"]
    if last:
        r = last[0]
        _data["last"] = {
            "name": r["raceName"], "round": r["round"],
            "results": [{
                "pos": int(x["position"]),
                "code": x["Driver"].get("code") or x["Driver"]["familyName"][:3].upper(),
                "result": _result_text(x),
                "colour": _colour(x["Constructor"]["constructorId"]),
            } for x in r["Results"]],
        }
    _data["updated"] = time.time()
    _data["error"] = None


def _result_text(x):
    if x.get("position") == "1" and x.get("Time"):
        return "WINNER"
    if x.get("Time", {}).get("time"):
        return x["Time"]["time"]
    status = x.get("status", "")
    if status.startswith("+") or "Lap" in status:
        n = "".join(ch for ch in status if ch.isdigit()) or "1"
        return f"+{n} LAP" + ("S" if n != "1" else "")
    return {"Finished": "FIN", "Disqualified": "DSQ", "Did not start": "DNS"}.get(status, "DNF")


async def run():
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "RaceBoard"}) as c:
        while True:
            try:
                await refresh(c)
                await asyncio.sleep(REFRESH_S)
            except asyncio.CancelledError:
                raise
            except Exception as e:   # noqa: BLE001 - keep the last good data
                _data["error"] = f"{type(e).__name__}: {e}"
                log.warning("jolpica: %s", _data["error"])
                await asyncio.sleep(120)

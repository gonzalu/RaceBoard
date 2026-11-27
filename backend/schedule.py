"""Between sessions: calendar, standings, results and track history.

Jolpica (api.jolpi.ca) carries on the old Ergast API: free, no key, updated a
few hours after each session. It isn't live, which is fine for these pages.
The race weekend forecast comes from Open-Meteo, also free and keyless.
"""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone

import httpx

from . import config, history

log = logging.getLogger("raceboard.schedule")

REFRESH_S = 30 * 60
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 14   # Open-Meteo forecasts about two weeks ahead

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

_data = {"next": None, "drivers": [], "teams": [], "last": None, "quali": None,
         "forecast": None, "updated": 0.0, "error": None}


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
        loc = r["Circuit"]["Location"]
        _data["next"] = {
            "season": r["season"], "round": r["round"], "name": r["raceName"],
            "circuit": r["Circuit"]["circuitName"],
            "circuitId": r["Circuit"]["circuitId"],
            "locality": loc["locality"],
            "country": loc["country"],
            "date": r["date"],
            "lat": float(loc["lat"]), "long": float(loc["long"]),
            "sessions": _race_sessions(r),
        }
        _data["quali"] = await _quali(c, r["season"], r["round"])
        _data["forecast"] = await _forecast(c, loc, r["date"])
    else:
        _data["next"] = _data["quali"] = _data["forecast"] = None

    ds = (await _get(c, "current/driverstandings.json"))["StandingsTable"]["StandingsLists"]
    if ds:
        before = await _prior_positions(c, ds[0]["season"], int(ds[0]["round"]), "driver")
        _data["standings_round"] = ds[0]["round"]
        _data["drivers"] = [{
            "pos": _pos(d),
            "code": d["Driver"].get("code") or d["Driver"]["familyName"][:3].upper(),
            "points": d["points"],
            "colour": _colour(d["Constructors"][-1]["constructorId"] if d["Constructors"] else ""),
            "change": _change(before.get(d["Driver"]["driverId"]), _pos(d)),
        } for d in ds[0]["DriverStandings"]]

    cs = (await _get(c, "current/constructorstandings.json"))["StandingsTable"]["StandingsLists"]
    if cs:
        before = await _prior_positions(c, cs[0]["season"], int(cs[0]["round"]), "constructor")
        _data["teams"] = [{
            "pos": _pos(t),
            "name": TEAM_SHORT.get(t["Constructor"]["constructorId"], t["Constructor"]["name"].upper()),
            "points": t["points"],
            "colour": _colour(t["Constructor"]["constructorId"]),
            "change": _change(before.get(t["Constructor"]["constructorId"]), _pos(t)),
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


def _pos(x):
    return int(x["position"]) if x.get("position", "").isdigit() else None


def _change(before, now):
    """Places gained (+) or lost (-) since the previous round."""
    return before - now if before and now else 0


async def _prior_positions(c, season, rnd, kind):
    """Standings after the round before, keyed by driverId or constructorId."""
    if rnd <= 1:
        return {}
    d = await _get(c, f"{season}/{rnd - 1}/{kind}standings.json")
    lists = d["StandingsTable"]["StandingsLists"]
    if not lists:
        return {}
    if kind == "constructor":
        return {e["Constructor"]["constructorId"]: _pos(e) for e in lists[0]["ConstructorStandings"]}
    return {e["Driver"]["driverId"]: _pos(e) for e in lists[0]["DriverStandings"]}


async def _quali(c, season, rnd):
    """Qualifying for the coming race, once it has run. None before that."""
    races = (await _get(c, f"{season}/{rnd}/qualifying.json"))["RaceTable"]["Races"]
    if not races:
        return None
    return [{
        "pos": _pos(q),
        "code": q["Driver"].get("code") or q["Driver"]["familyName"][:3].upper(),
        "time": q.get("Q3") or q.get("Q2") or q.get("Q1") or "",
        "colour": _colour(q["Constructor"]["constructorId"]),
    } for q in races[0]["QualifyingResults"]]


async def _forecast(c, loc, race_day):
    """Daily high and chance of rain at the track, Friday to Sunday."""
    try:
        sunday = date.fromisoformat(race_day)
        today = date.today()
        if sunday < today or (sunday - today).days > FORECAST_DAYS:
            return None
        r = await c.get(OPEN_METEO, params={
            "latitude": loc["lat"], "longitude": loc["long"], "timezone": "auto",
            "daily": "temperature_2m_max,precipitation_probability_max",
            "start_date": max(sunday - timedelta(days=2), today).isoformat(),
            "end_date": sunday.isoformat(),
        })
        r.raise_for_status()
        d = r.json()["daily"]
        return [{
            "day": date.fromisoformat(t).strftime("%a").upper(),
            "high": round(hi) if hi is not None else None,
            "rain": rain,
        } for t, hi, rain in zip(d["time"], d["temperature_2m_max"], d["precipitation_probability_max"])]
    except Exception as e:   # noqa: BLE001 - the forecast is a nicety
        log.info("forecast: %s", e)
        return None


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
                try:
                    await history.refresh(c)
                except Exception as e:   # noqa: BLE001 - its pages just stay away
                    log.warning("history: %s: %s", type(e).__name__, e)
                await asyncio.sleep(REFRESH_S)
            except asyncio.CancelledError:
                raise
            except Exception as e:   # noqa: BLE001 - keep the last good data
                _data["error"] = f"{type(e).__name__}: {e}"
                log.warning("jolpica: %s", _data["error"])
                await asyncio.sleep(120)

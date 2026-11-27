"""The "on this day" pages: what happened in F1 on today's date.

Built from Jolpica, on top of the winners list in history.py:
- race wins, and a driver's first win;
- titles clinched: the race where the champion's lead became more than the
  points still on offer. From 1991, when every result started to count;
  before that, dropped scores make the decider unclear;
- drivers born on this day, champions and winners first.

The day's main story (the title decider, else the latest race, else the best
known driver born that day) comes with Wikipedia's summary of it.

Working out where titles were clinched takes a few hundred requests the first
time, paced well inside Jolpica's limits; everything is kept in
data/lore.json, so a finished season is only ever worked out once.
"""

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config, history

log = logging.getLogger("raceboard.lore")

CACHE = Path(__file__).resolve().parent.parent / "data" / "lore.json"
WIKI = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKI_HEADERS = {"User-Agent": "RaceBoard (https://github.com/gonzalu/RaceBoard)"}
FIRST_TITLE_SEASON = 1991
DRIVERS_MAX_AGE_S = 30 * 24 * 3600
PAUSE_S = 1.2      # between Jolpica requests: its limit is 4 a second, 500 an hour
STORY_CHARS = 520  # about twelve lines of the panel

_d = {"champions": {}, "titles": {}, "drivers": [], "drivers_fetched": 0, "sprints": {}, "wiki": {}}


def _load():
    try:
        _d.update(json.loads(CACHE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def _save():
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(_d), encoding="utf-8")


_load()


async def _get(c, path, **params):
    """One Jolpica request, waiting as long as it asks when it's busy."""
    for _ in range(8):
        await asyncio.sleep(PAUSE_S)
        r = await c.get(f"{config.JOLPICA}/{path}", params=params)
        if r.status_code != 429:
            break
        wait = min(600, int(r.headers.get("retry-after") or 60))
        log.info("lore: Jolpica is busy, waiting %d s", wait)
        await asyncio.sleep(wait)
    r.raise_for_status()
    return r.json()["MRData"]


async def _standings(c, season, rnd=None):
    """(round, leader driverId, leader points, lead over second) after a round."""
    path = f"{season}/{rnd}/driverStandings.json" if rnd else f"{season}/driverStandings.json"
    d = await _get(c, path, limit=2)
    lists = d["StandingsTable"]["StandingsLists"]
    if not lists:
        return None
    s = lists[0]["DriverStandings"]
    lead = float(s[0]["points"]) - (float(s[1]["points"]) if len(s) > 1 else 0)
    return int(lists[0]["round"]), s[0]["Driver"]["driverId"], lead


# ---- titles ----

async def _calendar(c, season):
    """Rounds in a season, and which of them have a sprint."""
    d = await _get(c, f"{season}.json", limit=100)
    races = d["RaceTable"]["Races"]
    return len(races), {int(r["round"]) for r in races if r.get("Sprint")}


def _race_max(season, rnd, won):
    """The most points one driver could take from a round's Grand Prix: what
    its winner scored (that era's points, double or half points included),
    plus the fastest-lap point of 2019-2024 if the winner didn't get it."""
    fl = 1 if 2019 <= season <= 2024 else 0
    if rnd in won:
        return won[rnd] + (fl if won[rnd] == 25 else 0)
    return 25 + fl      # a round still to run


async def _title(c, season, rounds_done, champion):
    """The round where `champion` clinched the title, or None if not yet."""
    total, sprint_rounds = await _calendar(c, season)
    won = {r["round"]: r["points"] for r in history.races() if r["season"] == season}
    sprint = {int(k.split("-")[1]): v for k, v in _d["sprints"].items() if k.startswith(f"{season}-")}

    def left_after(rnd):
        return sum(_race_max(season, i, won) + (sprint.get(i, 8) if i in sprint_rounds else 0)
                   for i in range(rnd + 1, total + 1))

    async def clinched(rnd):
        st = await _standings(c, season, rnd)
        return bool(st) and st[1] == champion and st[2] > left_after(rnd)

    if not await clinched(rounds_done):
        return None
    lo, hi = 1, rounds_done          # the first round from which it was clinched
    while lo < hi:
        mid = (lo + hi) // 2
        if await clinched(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


async def _titles(c):
    this_year = date.today().year
    if not _d["sprints"]:
        d = await _get(c, "sprint/1.json", limit=100)
        _d["sprints"] = {f'{r["season"]}-{r["round"]}': float(r["SprintResults"][0]["points"])
                         for r in d["RaceTable"]["Races"]}
    for season in range(1950, this_year + 1):
        key = str(season)
        done = max((r["round"] for r in history.races() if r["season"] == season), default=0)
        known = _d["champions"].get(key)
        if not done or (known and known["rounds"] == done and (season < this_year or known.get("checked"))):
            continue
        st = await _standings(c, season)
        if not st:
            continue
        rounds, leader, _ = st
        entry = {"rounds": done, "driverId": leader, "checked": True}
        if season >= FIRST_TITLE_SEASON:
            rnd = await _title(c, season, rounds, leader)
            if rnd:
                race = next((r for r in history.races() if r["season"] == season and r["round"] == rnd), None)
                if race:
                    _d["titles"][key] = {"round": rnd, "date": race["date"], "name": race["name"],
                                         "driverId": leader, "url": race.get("url")}
            entry["champion"] = bool(rnd) or season < this_year
        else:
            entry["champion"] = season < this_year
        _d["champions"][key] = entry
        _save()
        log.info("lore: %d checked", season)


# ---- drivers ----

async def _drivers(c):
    if _d["drivers"] and time.time() - _d["drivers_fetched"] < DRIVERS_MAX_AGE_S:
        return
    out, offset, total = [], 0, 1
    while offset < total:
        d = await _get(c, "drivers.json", limit=100, offset=offset)
        total = int(d["total"])
        for x in d["DriverTable"]["Drivers"]:
            if x.get("dateOfBirth"):
                out.append({"id": x["driverId"], "name": f'{x["givenName"]} {x["familyName"]}',
                            "family": x["familyName"], "born": x["dateOfBirth"], "url": x.get("url")})
        offset += 100
    _d["drivers"], _d["drivers_fetched"] = out, time.time()
    _save()
    log.info("lore: %d drivers", len(out))


# ---- Wikipedia ----

def _plain(text):
    """What the panel's font can draw: plain ASCII, one paragraph."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip()


async def _wiki(c, url):
    if not url or url in _d["wiki"]:
        return
    title = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    r = await c.get(WIKI + title.replace(" ", "_"), headers=WIKI_HEADERS, follow_redirects=True)
    text = _plain(r.json().get("extract") or "") if r.status_code == 200 else ""
    if len(text) > STORY_CHARS:      # whole sentences only
        cut = text[:STORY_CHARS]
        text = cut[:cut.rfind(". ") + 1] or cut
    _d["wiki"][url] = text
    _save()


# ---- the day ----

def _fame():
    races = history.races()
    wins = Counter(r["driverId"] for r in races)
    titles = Counter(v["driverId"] for v in _d["champions"].values() if v.get("champion"))
    return wins, titles


def _items(days):
    """Everything on these month-day strings ("11-27"), most important first."""
    races = history.races()
    wins, titles = _fame()
    first_win = {}
    for r in races:
        first_win.setdefault(r["driverId"], (r["season"], r["round"]))
    out = []
    for t in _d["titles"].values():
        if t["date"][5:] in days:
            name = next((r["driver"] for r in races if r["driverId"] == t["driverId"]), t["driverId"])
            out.append({"year": int(t["date"][:4]), "kind": "title", "who": name.upper(),
                        "what": t["name"].upper().replace(" GRAND PRIX", " GP"), "team": None, "url": t["url"], "rank": 0})
    for r in races:
        if r["date"][5:] in days:
            first = first_win.get(r["driverId"]) == (r["season"], r["round"])
            out.append({"year": r["season"], "kind": "first" if first else "win", "who": r["driver"].upper(),
                        "what": r["name"].upper().replace(" GRAND PRIX", " GP"), "team": r["team"],
                        "url": r.get("url"), "rank": 1})
    for d in _d["drivers"]:
        if d["born"][5:] in days:
            n_t, n_w = titles.get(d["id"], 0), wins.get(d["id"], 0)
            what = f"{n_t}X CHAMPION" if n_t > 1 else "CHAMPION" if n_t else f"{n_w} WINS" if n_w > 1 else "1 WIN" if n_w else ""
            out.append({"year": int(d["born"][:4]), "kind": "born", "who": d["name"].upper(), "what": what,
                        "team": None, "url": d.get("url"), "rank": 2, "fame": n_t * 100 + n_w})
    out.sort(key=lambda x: (x["rank"], -x.get("fame", 0), -x["year"]))
    return out


def _day(today):
    """The day's items, or the week around it on a quiet day."""
    items = _items({today.strftime("%m-%d")})
    if len(items) >= 3:
        return "ON THIS DAY", today.strftime("%d %b").upper(), items
    week = [today + timedelta(days=k) for k in range(-3, 4)]
    return ("THIS WEEK IN F1", f"{week[0].strftime('%d')}-{week[-1].strftime('%d %b')}".upper(),
            _items({d.strftime("%m-%d") for d in week}))


def on_this_day(today):
    """What the panel's "on this day" pages show for this date."""
    title, label, items = _day(today)
    story = None
    for it in items:
        text = _d["wiki"].get(it.get("url"))
        if text:
            head = f'{it["year"]} {it["what"]}' if it["kind"] != "born" else f'{it["who"]}, BORN {it["year"]}'
            story = {"head": _plain(head), "text": text}
            break
    rows = [{"year": it["year"], "kind": it["kind"], "who": _plain(it["who"]), "what": _plain(it["what"]),
             "team": it["team"]} for it in items]
    return {"title": title, "date": label, "items": rows, "story": story}


async def refresh(c):
    """Bring titles and drivers up to date, and fetch the story for today and the next two days."""
    await _drivers(c)
    await _titles(c)
    for k in range(3):
        _, _, items = _day(date.today() + timedelta(days=k))
        url = next((it["url"] for it in items if it.get("url")), None)
        try:
            await _wiki(c, url)
        except Exception as e:   # noqa: BLE001 - the page just has no story
            log.warning("lore: wikipedia: %s: %s", type(e).__name__, e)

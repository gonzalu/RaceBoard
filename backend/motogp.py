"""MotoGP: a timing tower during MotoGP sessions, and its pages between them.

Everything comes from MotoGP's own public API (api.motogp.pulselive.com),
the one motogp.com runs on. No key, no login:
- the calendar, with each session's start: /events?seasonYear=
- live timing: /timing-gateway/livetiming-lite, polled every few seconds
  while a MotoGP session is on. It carries positions, lap times, gaps, team
  colours and who's in the pits; no flags, race control or tyres;
- standings and results: /results/...

F1 comes first: the MotoGP tower only shows when no F1 session is on. The
feed switches between Moto3, Moto2 and MotoGP through the day; only MotoGP
is kept. The spoiler delay and recording work as they do for F1.
"""

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("raceboard.motogp")

API = "https://api.motogp.pulselive.com/motogp/v1"
HEADERS = {"User-Agent": "RaceBoard (https://github.com/gonzalu/RaceBoard)"}
DATA = Path(__file__).resolve().parent.parent / "data"
CACHE = DATA / "motogp.json"
RECORD_DIR = DATA / "recordings"

POLL_S = 4              # live timing, while a session is on
CALENDAR_S = 1800       # calendar and results
NEAR = timedelta(days=7)  # MotoGP's pages show this close to a round, either side

# Sessions as the calendar names them: label on the board, and kind.
SESSIONS = {
    "FP1": ("FP1", "Practice"), "PR": ("PRACTICE", "Practice"), "FP2": ("FP2", "Practice"),
    "Q1": ("Q1", "Qualifying"), "Q2": ("Q2", "Qualifying"), "SPR": ("SPRINT", "Race"),
    "WUP": ("WARM UP", "Practice"), "RAC": ("RACE", "Race"),
}
# The calendar gives a race no end time; about how long one runs, start to flag.
RACE_LEN = {"SPR": timedelta(minutes=30), "RAC": timedelta(minutes=55)}
OUT = {"OUTSTND", "NOTFINISHFIRST", "NOTSTARTED", "DSQ", "EXC", "NC", "DNF", "DNS"}

# Team colours, as the live feed gives them; teams it has sent since are added.
COLOURS = {
    "Ducati Lenovo Team": "ad0000", "Aprilia Racing": "5f259f",
    "SuperFile Trackhouse MotoGP Team": "0086d6", "Red Bull KTM Factory Racing": "ff7e27",
    "BK8 Gresini Racing MotoGP": "9baee4", "Red Bull KTM Tech3": "ff6600",
    "Pertamina Enduro VR46 Racing Team": "e1ff00", "Castrol Honda LCR": "fafafa",
    "Monster Energy Yamaha MotoGP Team": "0a2d82", "Prima Pramac Yamaha MotoGP": "0046d3",
    "Honda HRC Castrol": "e50000", "Pro Honda LCR": "fafafa",
}
MAKE_COLOURS = {"Ducati": "ad0000", "Aprilia": "5f259f", "KTM": "ff7e27", "Honda": "e50000", "Yamaha": "0a2d82"}

_d = {"calendar": [], "standings": [], "standings_round": None, "result": None,
      "colours": {}, "codes": {}, "season": None}
_live = {"buf": deque(), "latest": None, "rec": None, "rec_name": None, "last_line": None}


def _load():
    try:
        _d.update(json.loads(CACHE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def _save():
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(_d), encoding="utf-8")


_load()


def _iso(s):
    """The API's times, "2026-11-28T10:10:00+0100", as aware datetimes."""
    try:
        if len(s) > 5 and s[-5] in "+-" and s[-3] != ":":
            s = s[:-2] + ":" + s[-2:]
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _colour(team, make=None):
    return _d["colours"].get(team) or COLOURS.get(team) or MAKE_COLOURS.get(make or "", "ffffff")


# ---- rider codes ----
# Three letters like F1's, from the surname. Where riders share them (Marc and
# Alex Marquez, Martin, Marini) each gets an initial and two letters instead.

def _base(surname):
    return "".join(ch for ch in surname.upper() if ch.isalpha())[:3]


def _codes(riders):
    """{rider id: code} for (id, given name, surname) tuples."""
    count = {}
    for _, _, sur in riders:
        count[_base(sur)] = count.get(_base(sur), 0) + 1
    out = {}
    for rid, given, sur in riders:
        b = _base(sur)
        out[str(rid)] = b if count[b] == 1 else (given[:1] + _base(sur)[:2]).upper()
    return out


def _code(rid, given, surname):
    return _d["codes"].get(str(rid)) or _base(surname)


def _split(full):
    given, _, sur = (full or "").partition(" ")
    return given, sur or given


# ---- calendar ----

async def _calendar(c, year):
    r = await c.get(f"{API}/events", params={"seasonYear": year})
    r.raise_for_status()
    out = []
    for e in r.json():
        if e.get("kind") != "GP":
            continue
        sessions = []
        for b in e.get("broadcasts") or []:
            code = b.get("shortname")
            if (b.get("category") or {}).get("acronym") != "MGP" or code not in SESSIONS:
                continue
            start, end = _iso(b.get("date_start")), _iso(b.get("date_end"))
            if not start:
                continue
            if not end or end <= start:
                end = start + RACE_LEN.get(code, timedelta(hours=1))
            sessions.append({"code": code, "start": start.isoformat(), "end": end.isoformat()})
        if not sessions:
            continue
        sessions.sort(key=lambda s: s["start"])
        out.append({
            "name": (e.get("additional_name") or e.get("name") or "").strip().upper(),
            "circuit": ((e.get("circuit") or {}).get("name") or "").strip(),
            "flag": str(e.get("country") or "").lower() or None,   # "ES"
            "season": year, "sessions": sessions,
        })
    out.sort(key=lambda e: e["sessions"][0]["start"])
    for i, e in enumerate(out):
        e["round"] = i + 1
    return out


def _sessions():
    for e in _d["calendar"]:
        for s in e["sessions"]:
            yield e, s, datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])


def _hold(code):
    return timedelta(minutes=config.RESULT_HOLD_MIN if SESSIONS[code][1] == "Race" else config.PRACTICE_HOLD_MIN)


def _windows(now):
    """The scheduled sessions whose window (from just before each to its hold
    after) takes in now, with their events. Sessions are close together, so
    one's hold can run into the next one's start."""
    return [(e, s) for e, s, start, end in _sessions()
            if start - timedelta(minutes=config.PRE_SESSION_MIN) <= now <= end + _hold(s["code"])]


# ---- standings and the latest result ----

async def _results(c):
    seasons = (await c.get(f"{API}/results/seasons")).json()
    season = next((s for s in seasons if s.get("current")), None)
    if not season:
        return
    cats = (await c.get(f"{API}/results/categories", params={"seasonUuid": season["id"]})).json()
    cat = next((x["id"] for x in cats if x.get("legacy_id") == 3), None)
    if not cat:
        return
    events = (await c.get(f"{API}/results/events", params={"seasonUuid": season["id"]})).json()
    events = sorted((e for e in events if not e.get("test") and e.get("status") in ("FINISHED", "CURRENT")),
                    key=lambda e: e.get("date_start") or "", reverse=True)

    st = (await c.get(f"{API}/results/standings",
                      params={"seasonUuid": season["id"], "categoryUuid": cat})).json()
    st = st.get("classification") or []
    _d["codes"] = _codes([(x["rider"].get("legacy_id"), *_split(x["rider"].get("full_name"))) for x in st])

    # The latest round with a race or sprint run: its result, and what it
    # changed in the standings.
    result, gained, rounds = None, {}, 0
    for e in events:
        sess = (await c.get(f"{API}/results/sessions",
                            params={"eventUuid": e["id"], "categoryUuid": cat})).json()
        done = {s["type"]: s for s in sess if isinstance(s, dict) and s.get("status") == "FINISHED"}
        if not ("RAC" in done or "SPR" in done):
            continue
        rows_by_type = {}
        for typ in ("SPR", "RAC"):
            if typ in done:
                cl = (await c.get(f"{API}/results/session/{done[typ]['id']}/classification",
                                  params={"test": "false"})).json()
                rows_by_type[typ] = cl.get("classification") or []
        typ = "RAC" if "RAC" in rows_by_type else "SPR"
        name = (e.get("additional_name") or e.get("name") or "").strip().upper()
        result = {"name": f"{name} {'GP' if typ == 'RAC' else 'SPRINT'}", "rows": [_result_row(x) for x in rows_by_type[typ]]}
        round_pts = {}
        for rows in rows_by_type.values():
            for x in rows:
                rid = x["rider"].get("legacy_id")
                round_pts[rid] = round_pts.get(rid, 0) + (x.get("points") or 0)
        before = sorted(st, key=lambda x: -((x.get("points") or 0) - round_pts.get(x["rider"].get("legacy_id"), 0)))
        bpos = {x["rider"].get("legacy_id"): i + 1 for i, x in enumerate(before)}
        gained = {rid: bpos[rid] - i for rid, i in
                  ((x["rider"].get("legacy_id"), x.get("position") or 0) for x in st) if rid in bpos and i}
        break
    rounds = sum(1 for e in _d["calendar"] if datetime.fromisoformat(e["sessions"][-1]["end"]) < datetime.now(timezone.utc))

    _d["standings"] = [{
        "pos": x.get("position"),
        "code": _code(x["rider"].get("legacy_id"), *_split(x["rider"].get("full_name"))),
        "flag": ((x["rider"].get("country") or {}).get("iso") or "").lower() or None,
        "points": str(int(x.get("points") or 0)),
        "colour": _colour((x.get("team") or {}).get("name"), (x.get("constructor") or {}).get("name")),
        "change": gained.get(x["rider"].get("legacy_id"), 0),
    } for x in st if x.get("position")]
    _d["standings_round"] = rounds or None
    _d["result"] = result
    _save()


def _result_row(x):
    rid = x["rider"].get("legacy_id")
    pos, status, gap = x.get("position"), x.get("status") or "", x.get("gap") or {}
    if status in OUT or not pos:
        text = "DNS" if status in ("NOTSTARTED", "DNS") else "DNF"   # NOTFINISHFIRST: out on the first lap
    elif pos == 1:
        text = "WINNER"
    elif str(gap.get("lap") or "0") not in ("0", ""):
        n = int(gap["lap"])
        text = f"+{n} LAP" + ("S" if n > 1 else "")
    else:
        text = f"+{gap.get('first')}"
    return {
        "pos": str(pos) if pos else "-",
        "code": _code(rid, *_split(x["rider"].get("full_name"))),
        "flag": ((x["rider"].get("country") or {}).get("iso") or "").lower() or None,
        "result": text,
        "points": x.get("points") or 0,
        "colour": _colour((x.get("team") or {}).get("name"), (x.get("constructor") or {}).get("name")),
    }


# ---- live timing ----

def _record(snap):
    if not config.RECORD:
        return
    head = snap.get("head") or {}
    name = f"{datetime.now().strftime('%Y-%m-%d')}_MotoGP_{head.get('event_shortname')}_{head.get('session_shortname')}.jsonl"
    line = json.dumps(snap, separators=(",", ":"))
    if line == _live["last_line"]:
        return
    _live["last_line"] = line
    if _live["rec_name"] != name:
        if _live["rec"]:
            _live["rec"].close()
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        _live["rec"], _live["rec_name"] = open(RECORD_DIR / name, "a", encoding="utf-8"), name
        log.info("motogp: recording %s", name)
    _live["rec"].write(f'{{"t":{time.time():.3f},"d":{line}}}\n')
    _live["rec"].flush()


def _take(snap):
    """A new snapshot from the feed: kept (after the delay) if it's MotoGP."""
    if (snap.get("head") or {}).get("category") != "MotoGP" or not snap.get("rider"):
        return
    _live["buf"].append((time.time(), snap))
    _record(snap)


def _current():
    """The newest snapshot old enough to show, under the spoiler delay."""
    buf, cutoff = _live["buf"], time.time() - config.DELAY_S
    while len(buf) > 1 and buf[1][0] <= cutoff:
        buf.popleft()
    if buf and buf[0][0] <= cutoff:
        _live["latest"] = buf[0][1]
    return _live["latest"]


async def _replay():
    """Play a MotoGP recording into the board, at RACEBOARD_REPLAY_SPEED."""
    lines = [json.loads(x) for x in Path(config.MOTOGP_REPLAY).read_text(encoding="utf-8").splitlines() if x.strip()]
    if not lines:
        return
    log.info("motogp: replaying %s (%d snapshots)", config.MOTOGP_REPLAY, len(lines))
    t0, started = lines[0]["t"], time.monotonic()
    for m in lines:
        wait = (m["t"] - t0) / (config.REPLAY_SPEED or 1) - (time.monotonic() - started)
        if wait > 0:
            await asyncio.sleep(wait)
        _live["latest"] = m["d"]


async def run():
    if not config.MOTOGP:
        return
    if config.MOTOGP_REPLAY:
        asyncio.create_task(_replay())
    async with httpx.AsyncClient(timeout=20, headers=HEADERS) as c:
        next_slow = 0.0
        while True:
            try:
                if time.monotonic() >= next_slow:
                    year = datetime.now(timezone.utc).year
                    cal = await _calendar(c, year)
                    if not any(datetime.fromisoformat(s["end"]) > datetime.now(timezone.utc)
                               for e in cal for s in e["sessions"]):
                        cal += await _calendar(c, year + 1)   # the season's over: look ahead
                    _d["calendar"] = cal
                    await _results(c)
                    next_slow = time.monotonic() + CALENDAR_S
                wins = _windows(datetime.now(timezone.utc))
                if wins and not config.MOTOGP_REPLAY:
                    r = await c.get(f"{API}/timing-gateway/livetiming-lite")
                    if r.status_code == 200:
                        _take(r.json())
                        for x in (r.json().get("rider") or {}).values():
                            if x.get("team_name") and x.get("color") and _d["colours"].get(x["team_name"]) != x["color"]:
                                _d["colours"][x["team_name"]] = x["color"]
                    if any(s["code"] in ("SPR", "RAC") and datetime.fromisoformat(s["end"]) < datetime.now(timezone.utc)
                           for _, s in wins):
                        next_slow = min(next_slow, time.monotonic() + 300)   # the result lands soon after
                    await asyncio.sleep(POLL_S)
                else:
                    await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception as ex:   # noqa: BLE001 - keep the last good data
                log.warning("motogp: %s: %s", type(ex).__name__, ex)
                await asyncio.sleep(60)


# ---- what the board shows ----

def _time(v):
    """1'28.483 -> 1:28.483; 0.000 (no time yet) -> blank"""
    v = (v or "").replace("'", ":")
    return "" if v.strip("0.:") == "" else v


def _plus(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v or "").upper()
    return f"+{f:.3f}" if f > 0 else ""


def _ms(t):
    try:
        m, s = _time(t).split(":")
        return int(m) * 60 + float(s)
    except ValueError:
        return None


def timing(now=None):
    """The timing board, in the same shape as F1's, while a MotoGP session is
    on or just finished; otherwise None."""
    if not config.MOTOGP:
        return None
    snap = _current()
    if not snap:
        return None
    head = snap.get("head") or {}
    now = now or datetime.now(timezone.utc)
    # only a session on now, by its own timing: before one starts, the feed
    # still shows the one before
    e, s = next(((e, s) for e, s in _windows(now) if head.get("session_shortname") == s["code"]
                 and head.get("date") == datetime.fromisoformat(s["start"]).strftime("%d/%m/%Y")), (None, None))
    if not s and not config.MOTOGP_REPLAY:
        return None
    code = head.get("session_shortname") or ""
    label, kind = SESSIONS.get(code, (head.get("session_name", "").upper(), "Practice"))
    finished = head.get("session_status_id") == "F"
    waiting = head.get("session_status_id") == "N"   # not started: on the grid, or before the pit lane opens
    riders = sorted((snap.get("rider") or {}).values(), key=lambda r: r.get("order") or 99)
    codes = dict(_codes([(r.get("rider_id"), r.get("rider_name") or "", r.get("rider_surname") or "") for r in riders]))
    codes.update({k: v for k, v in _d["codes"].items() if k in codes})
    bests = [(_ms(r.get("lap_time")), r.get("rider_id")) for r in riders if _ms(r.get("lap_time"))]
    fastest = min(bests)[1] if bests else None
    is_race = kind == "Race"

    rows = []
    for i, r in enumerate(riders):
        pos = r.get("pos")
        # CL: classified, NS: not started yet. Anything else is a rider out of it.
        out = (r.get("status_name") or "CL") not in ("CL", "NS", "")
        row = {
            "num": str(r.get("rider_number") or ""),
            "number": str(r.get("rider_number") or ""),
            "pos": pos if isinstance(pos, int) and pos > 0 else (r.get("order") if waiting else None),
            "tla": codes.get(str(r.get("rider_id"))) or _base(r.get("rider_surname") or ""),
            "colour": r.get("color") or _colour(r.get("team_name"), r.get("bike_name")),
            "tyre": "", "tyre_laps": None, "pits": None,
            "in_pit": bool(r.get("on_pit")) and not finished,
            "pit_out": False, "retired": out, "stopped": False, "out": False,
            "last": _time(r.get("last_lap_time")),
            "best": _time(r.get("lap_time")),
            "fastest": r.get("rider_id") == fastest and not is_race,
            "laps_done": r.get("last_lap"),
            "gap": _plus(r.get("gap_first")),
            "interval": _plus(r.get("gap_prev")),
        }
        rows.append(row)

    remaining, laps, lap = None, None, None
    if is_race:
        laps = head.get("num_laps") or None
        done = max((r.get("last_lap") or 0 for r in riders), default=0)
        lap = laps if finished else min(laps or 99, done + 1)
    else:
        sched = (datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).total_seconds() if s else 0
        try:
            dur, rem = int(head.get("duration") or 0), int(head.get("remaining") or 0)
            # the feed's units aren't documented; scale them by the scheduled length
            remaining = int(rem * sched / dur) if dur and sched else None
        except ValueError:
            remaining = None
    meeting = ((snap.get("event") or {}).get("additional_name") or (e or {}).get("name") or "").upper()
    return {
        "series": "motogp",
        "meeting": f"MOTOGP {meeting}".strip(),
        "session": label,
        "type": kind,
        "status": "Finished" if finished else "Inactive" if waiting else "Started",
        "track": {"text": "CHEQUERED FLAG", "colour": "white", "code": ""} if finished
        else {"text": "NOT STARTED", "colour": "white", "code": ""} if waiting
        else {"text": "", "colour": "green", "code": "1"},
        "lap": lap, "laps": laps,
        "remaining_s": remaining,
        "rows": rows,
        "race_control": [],
        "weather": {"air": None, "track": None, "rain": False},
    }


def idle(now=None):
    """MotoGP's between-sessions pages, close to a round; None the rest of the year."""
    if not config.MOTOGP or not _d["calendar"]:
        return None
    now = now or datetime.now(timezone.utc)
    nxt = next((e for e in _d["calendar"] if datetime.fromisoformat(e["sessions"][-1]["end"]) > now), None)
    last = next((e for e in reversed(_d["calendar"]) if datetime.fromisoformat(e["sessions"][-1]["end"]) <= now), None)
    near_next = nxt and datetime.fromisoformat(nxt["sessions"][0]["start"]) - now <= NEAR
    near_last = last and now - datetime.fromisoformat(last["sessions"][-1]["end"]) <= NEAR
    if not (near_next or near_last):
        return None
    out = {"standings": _d["standings"], "standings_round": _d["standings_round"], "result": _d["result"], "next": None}
    if near_next:
        # Q1 and Q2 share a line, and the warm up is left out, so the weekend fits
        rows = []
        for s in nxt["sessions"]:
            if s["code"] == "WUP":
                continue
            if s["code"] == "Q2" and rows and rows[-1]["code"] == "Q1":
                rows[-1]["len_s"] = int((datetime.fromisoformat(s["end"]) -
                                         datetime.fromisoformat(rows[-1]["utc"])).total_seconds())
                continue
            label = "QUALIFYING" if s["code"] == "Q1" else SESSIONS[s["code"]][0]
            rows.append({"code": s["code"], "name": label, "utc": s["start"],
                         "len_s": int((datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).total_seconds())})
        out["next"] = {"series": "MOTOGP", "round": nxt["round"], "season": nxt["season"],
                       "name": f"{nxt['name']} GP", "circuit": nxt["circuit"], "flag": nxt["flag"], "sessions": rows}
    return out

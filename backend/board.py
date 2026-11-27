"""Turn the raw feed state into what the panel draws.

The feed's shapes differ by session type (a race has gaps and intervals,
practice has a fastest-lap table, qualifying has one of those per part), so
they are all folded into one row shape here and the panel never has to care.
"""

from datetime import datetime, timedelta, timezone

from . import config
from .livetiming import parse_utc

TRACK = {
    "1": ("GREEN", "green"), "2": ("YELLOW FLAG", "yellow"), "3": ("", "green"),
    "4": ("SAFETY CAR", "yellow"), "5": ("RED FLAG", "red"),
    "6": ("VIRTUAL SAFETY CAR", "yellow"), "7": ("VSC ENDING", "yellow"),
}
FINISHED = {"Finished", "Finalised", "Ends"}
RUNNING = {"Started", "Aborted"}

# A red flag runs from SessionStatus "Aborted" until the session is "Started"
# again, often through a minute of "Inactive" before the restart. TrackStatus
# goes back to all clear well before then, so it can't be trusted alone.
_last_active = {"status": None}


def _suspended(status):
    if status != "Inactive":
        _last_active["status"] = status
        return status == "Aborted"
    return _last_active["status"] == "Aborted"


def _remaining(clock, now):
    """The session clock now. F1 sends it only when it starts, stops or jumps
    (a session extended after a red flag, say) and says whether it's running;
    in between it is run on from the moment it was sent, whatever the status."""
    remaining = _hms(clock.get("Remaining"))
    at = parse_utc(clock.get("Utc"))
    if remaining is None or not clock.get("Extrapolating") or not at:
        return remaining
    return max(0, remaining - int((now - at).total_seconds()))


def _list(v):
    """Feed lists sometimes arrive as index-keyed dicts; read both the same way."""
    if isinstance(v, list):
        return [x for x in v if x is not None]
    if isinstance(v, dict):
        return [v[k] for k in sorted(v, key=lambda k: int(k) if k.isdigit() else 0)]
    return []


def _session_window(info):
    """Scheduled start and end in UTC. The feed gives local time plus an offset."""
    try:
        off = info.get("GmtOffset") or "00:00:00"
        neg = off.startswith("-")
        h, m, s = (int(x) for x in off.lstrip("+-").split(":"))
        delta = timedelta(hours=h, minutes=m, seconds=s) * (-1 if neg else 1)
        start = datetime.fromisoformat(info["StartDate"]).replace(tzinfo=timezone.utc) - delta
        end = datetime.fromisoformat(info["EndDate"]).replace(tzinfo=timezone.utc) - delta
        return start, end
    except (KeyError, ValueError, TypeError):
        return None, None


def _hms(s):
    try:
        h, m, sec = (int(float(x)) for x in s.split(":"))
        return h * 3600 + m * 60 + sec
    except (AttributeError, ValueError):
        return None


def _meeting_short(name):
    name = (name or "").upper()
    return name.replace(" GRAND PRIX", " GP")


def _session_label(info, part):
    name = (info.get("Name") or "").upper()
    if name.startswith("PRACTICE"):
        return "FP" + name.split()[-1]
    if name == "QUALIFYING":
        return f"Q{part}" if part else "QUALI"
    if name in ("SPRINT QUALIFYING", "SPRINT SHOOTOUT"):
        return f"SQ{part}" if part else "SPRINT QUALI"
    return name


def mode_for(feed):
    """'timing' while a session is on (or just finished), otherwise 'idle'."""
    st = feed.state
    info = st.get("SessionInfo") or {}
    lines = (st.get("TimingData") or {}).get("Lines")
    if not info or not lines:
        return "idle"
    if feed.replay:
        return "timing"
    status = (st.get("SessionStatus") or {}).get("Status") or info.get("SessionStatus")
    start, end = _session_window(info)
    now = feed.now()
    if status in RUNNING:
        return "timing"
    if not start:
        return "idle"
    if status not in FINISHED and start - timedelta(minutes=config.PRE_SESSION_MIN) <= now <= end:
        return "timing"
    hold = config.RESULT_HOLD_MIN if info.get("Type") == "Race" else config.PRACTICE_HOLD_MIN
    if status in FINISHED and now <= end + timedelta(minutes=hold):
        return "timing"
    return "idle"


def build(feed):
    st = feed.state
    info = st.get("SessionInfo") or {}
    td = st.get("TimingData") or {}
    status = (st.get("SessionStatus") or {}).get("Status") or info.get("SessionStatus")
    typ = info.get("Type") or ""
    is_race = typ == "Race"
    is_quali = typ == "Qualifying"
    part = td.get("SessionPart") if is_quali else None
    try:
        part = int(part) if part else None
    except (TypeError, ValueError):
        part = None

    drivers = st.get("DriverList") or {}
    app = (st.get("TimingAppData") or {}).get("Lines") or {}
    stats = (st.get("TimingStats") or {}).get("Lines") or {}

    rows = []
    for num, line in (td.get("Lines") or {}).items():
        if not isinstance(line, dict):
            continue
        d = drivers.get(num) or {}
        stints = _list((app.get(num) or {}).get("Stints"))
        stint = stints[-1] if stints else {}
        pos = line.get("Position")
        row = {
            "num": num,
            "pos": int(pos) if str(pos or "").isdigit() else None,
            "tla": d.get("Tla") or num,
            "colour": d.get("TeamColour") or "FFFFFF",
            "tyre": (stint.get("Compound") or "")[:1].upper() if stint.get("Compound") not in (None, "UNKNOWN") else "",
            "tyre_laps": stint.get("TotalLaps"),
            "pits": line.get("NumberOfPitStops"),
            "in_pit": bool(line.get("InPit")),
            "pit_out": bool(line.get("PitOut")),
            "retired": bool(line.get("Retired")),
            "stopped": bool(line.get("Stopped")),
            "out": bool(line.get("KnockedOut")),
            "last": (line.get("LastLapTime") or {}).get("Value") or "",
            "fastest": ((stats.get(num) or {}).get("PersonalBestLapTime") or {}).get("Position") == 1,
            "laps_done": line.get("NumberOfLaps"),
        }
        if is_race:
            # places made or lost since the start; 0 on the grid means a pit lane start
            grid = str((app.get(num) or {}).get("GridPos") or "")
            row["grid"] = int(grid) if grid.isdigit() and grid != "0" else None
            row["gained"] = row["grid"] - row["pos"] if row["grid"] and row["pos"] else None
        if is_race:
            row["gap"] = line.get("GapToLeader") or ""
            row["interval"] = (line.get("IntervalToPositionAhead") or {}).get("Value") or ""
            row["best"] = (line.get("BestLapTime") or {}).get("Value") or ""
        elif is_quali:
            bests = _list(line.get("BestLapTimes"))
            lap_stats = _list(line.get("Stats"))
            i = (part or 1) - 1
            row["best"] = (bests[i].get("Value") if i < len(bests) and bests[i] else "") or ""
            s = lap_stats[i] if i < len(lap_stats) and lap_stats[i] else {}
            row["gap"] = s.get("TimeDiffToFastest") or ""
            row["interval"] = s.get("TimeDiffToPositionAhead") or ""
        else:
            row["best"] = (line.get("BestLapTime") or {}).get("Value") or ""
            row["gap"] = line.get("TimeDiffToFastest") or ""
            row["interval"] = line.get("TimeDiffToPositionAhead") or ""
        rows.append(row)
    rows.sort(key=lambda r: (r["pos"] is None, r["pos"] or 99))

    ts = st.get("TrackStatus") or {}
    code = str(ts.get("Status") or "")
    track_text, track_colour = TRACK.get(code, ("", "green"))
    suspended = _suspended(status)
    if status in FINISHED:
        track_text, track_colour = "CHEQUERED FLAG", "white"
    elif suspended:
        track_text, track_colour = TRACK["5"]
        code = "5"
    elif status == "Inactive":
        track_text, track_colour = "NOT STARTED", "white"

    remaining = _remaining(st.get("ExtrapolatedClock") or {}, feed.now())

    msgs = _list((st.get("RaceControlMessages") or {}).get("Messages"))
    rc = [{"text": m.get("Message", ""), "flag": m.get("Flag"), "cat": m.get("Category"),
           "utc": m.get("Utc")} for m in msgs[-5:]]

    lap = st.get("LapCount") or {}
    w = st.get("WeatherData") or {}
    return {
        "meeting": _meeting_short((info.get("Meeting") or {}).get("Name")),
        "session": _session_label(info, part),
        "type": typ,
        "status": status,
        "track": {"text": track_text, "colour": track_colour, "code": code},
        "lap": lap.get("CurrentLap"), "laps": lap.get("TotalLaps"),
        "remaining_s": remaining,
        "rows": rows,
        "race_control": rc,
        "weather": {"air": w.get("AirTemp"), "track": w.get("TrackTemp"),
                    "rain": str(w.get("Rainfall")) == "1"},
    }

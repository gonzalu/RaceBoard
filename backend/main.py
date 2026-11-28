import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from . import board, circuits, config, history, lore, schedule
from .alerts import Alerts
from .livetiming import Feed

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("raceboard")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
feed = Feed()
alerts = Alerts()


def _tz():
    if config.TZ:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(config.TZ)
        except Exception:   # noqa: BLE001 - unknown zone or no tz database
            log.warning("unknown RACEBOARD_TZ %r, using server local time", config.TZ)
    return None


TZ = _tz()


def _local(iso):
    dt = datetime.fromisoformat(iso)
    dt = dt.astimezone(TZ) if TZ else dt.astimezone()
    return dt.strftime("%a %d %b %H:%M").upper()


def _build_id():
    """Fingerprint of the frontend, so a cast TV reloads itself after a deploy.

    A Chromecast loads the page once and keeps it for months; without this a
    change would never reach the screen.
    """
    h = hashlib.sha1()
    for p in sorted(FRONTEND_DIR.glob("*")):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


BUILD = _build_id()


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(feed.run()), asyncio.create_task(schedule.run())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.get("/api/board")
def api_board():
    mode = board.mode_for(feed)
    sched = schedule.snapshot()
    nxt = sched.get("next")
    if nxt:
        nxt = dict(nxt, sessions=[dict(s, local=_local(s["utc"])) for s in nxt["sessions"]])
    age = feed.age_s()
    timing = board.build(feed) if mode == "timing" else None
    alerts.update(timing)
    today = datetime.now(TZ).date() if TZ else date.today()
    return {
        "build": BUILD,
        "mode": mode,
        "feed": {
            "connected": feed.connected,
            "replay": feed.replay,
            "age_s": None if age is None else round(age, 1),
            "error": feed.last_error,
            "delay_s": config.DELAY_S,
        },
        "timing": timing,
        "alerts": alerts.recent(),
        "idle": {
            "next": nxt,
            "drivers": sched.get("drivers") or [],
            "teams": sched.get("teams") or [],
            "standings_round": sched.get("standings_round"),
            "last": sched.get("last"),
            "quali": sched.get("quali"),
            "sprint": sched.get("sprint"),
            "forecast": sched.get("forecast"),
            "map": circuits.outline(nxt["lat"], nxt["long"], nxt.get("circuitId")) if nxt and "lat" in nxt else None,
            "track": history.at_circuit(nxt["circuitId"]) if nxt and nxt.get("circuitId") else None,
            "today": lore.on_this_day(today),
            "error": sched.get("error"),
        },
    }


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

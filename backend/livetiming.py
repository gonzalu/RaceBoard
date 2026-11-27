"""F1 live timing: the same feed the timing page on formula1.com is drawn from.

It's a SignalR Core hub at livetiming.formula1.com/signalrcore. On subscribe the
hub answers with a full snapshot of every requested topic, then streams deltas
to merge into it. Without an F1 TV login it still serves the timing tower:
positions, gaps, laps, sectors, tyres, pit stops, flags, race control and
weather. Car telemetry (CarData.z) and GPS (Position.z) need a login, so they
aren't asked for.

The feed is unofficial and undocumented. Everything here assumes it can drop,
change shape or vanish, and the board falls back to the schedule pages when it
does.
"""

import asyncio
import collections
import copy
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import websockets

from . import config

log = logging.getLogger("raceboard.live")

RS = "\x1e"   # SignalR's JSON message separator
TOPICS = [
    "Heartbeat", "SessionInfo", "SessionStatus", "TrackStatus", "LapCount",
    "ExtrapolatedClock", "DriverList", "TimingData", "TimingAppData",
    "TimingStats", "RaceControlMessages", "WeatherData",
]
# A browser-ish agent; the hub has historically refused some library defaults.
HEADERS = {"User-Agent": "BestHTTP"}
RECORD_DIR = Path(__file__).resolve().parent.parent / "data" / "recordings"
_SECOND = timedelta(seconds=1)


def merge(base, delta):
    """Fold one F1 delta into the state it updates.

    Deltas are partial objects. The awkward case is lists: the snapshot sends a
    real list, but an update to it arrives as a dict keyed by index ("3": {...}),
    and an index one past the end means append (that's how new race control
    messages and new tyre stints show up).
    """
    if isinstance(delta, dict):
        if isinstance(base, list):
            for k, v in delta.items():
                try:
                    i = int(k)
                except ValueError:
                    continue
                while len(base) < i:
                    base.append(None)
                if i < len(base):
                    base[i] = merge(base[i], v)
                else:
                    base.append(merge(None, v))
            return base
        if not isinstance(base, dict):
            base = {}
        for k, v in delta.items():
            if k == "_deleted":
                continue
            base[k] = merge(base.get(k), v)
        return base
    if isinstance(delta, list):
        # never share a list between the delayed and undelayed state: an
        # append merged into one would otherwise land in both
        return copy.deepcopy(delta)
    return delta


def parse_utc(s):
    """F1 timestamps: ISO, sometimes with 7 fractional digits, sometimes no Z."""
    if not s:
        return None
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = head + "." + frac[:6]
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class Feed:
    def __init__(self):
        self.state = {}           # what the board shows, DELAY_S behind
        self.raw = {}             # undelayed, for recording and session changes
        self.pending = collections.deque()   # (monotonic, topic, data); topic None = snapshot
        self.connected = False
        self.last_rx = 0.0        # monotonic time of the last message of any kind
        self.last_error = None
        self.replay = bool(config.REPLAY)
        self._replay_clock = None   # (feed datetime, monotonic) anchor while replaying
        self._ws = None
        self._rec = None
        self._rec_name = None

    # ---- the board's view ----

    def now(self):
        """The moment the board is showing, in feed time.

        Live, that's wall clock minus the delay. In a replay it's the recording's
        own clock, so countdowns and the session clock run as they did that day.
        """
        if self.replay:
            if not self._replay_clock:
                hb = parse_utc((self.state.get("Heartbeat") or {}).get("Utc"))
                return hb or datetime.now(timezone.utc)
            t0, m0 = self._replay_clock
            return t0 + (time.monotonic() - m0) * config.REPLAY_SPEED * _SECOND
        return datetime.now(timezone.utc) - config.DELAY_S * _SECOND

    def age_s(self):
        return None if not self.last_rx else time.monotonic() - self.last_rx

    # ---- applying ----

    def _apply(self, topic, data):
        if topic is None:
            self.state = merge(None, data)
        else:
            self.state[topic] = merge(self.state.get(topic), data)

    def _queue(self, topic, data):
        if not self.state or config.DELAY_S <= 0:
            self._apply(topic, data)
        else:
            self.pending.append((time.monotonic(), topic, data))

    async def _drain(self):
        while True:
            cutoff = time.monotonic() - config.DELAY_S
            while self.pending and self.pending[0][0] <= cutoff:
                _, topic, data = self.pending.popleft()
                self._apply(topic, data)
            await asyncio.sleep(0.2)

    # ---- live connection ----

    async def run(self):
        drain = asyncio.create_task(self._drain())
        try:
            if self.replay:
                await self._run_replay()
            else:
                await self._run_live()
        finally:
            drain.cancel()

    async def _run_live(self):
        backoff = 2
        while True:
            try:
                await self._session()
                backoff = 2
            except asyncio.CancelledError:
                raise
            except Exception as e:   # noqa: BLE001 - any failure means reconnect
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("live timing: %s", self.last_error)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    async def _session(self):
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as c:
            r = await c.post(f"{config.LIVE_BASE}/signalrcore/negotiate?negotiateVersion=1")
            r.raise_for_status()
            token = r.json()["connectionToken"]
            # the load balancer pins the socket to the node that negotiated
            cookie = "; ".join(f"{k}={v}" for k, v in r.cookies.items())

        url = config.LIVE_BASE.replace("https://", "wss://") + "/signalrcore?id=" + token
        hdrs = dict(HEADERS)
        if cookie:
            hdrs["Cookie"] = cookie
        async with websockets.connect(url, additional_headers=hdrs, max_size=None,
                                      open_timeout=20) as ws:
            self._ws = ws
            await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
            await ws.send(json.dumps({"type": 1, "target": "Subscribe",
                                      "arguments": [TOPICS], "invocationId": "0"}) + RS)
            pinger = asyncio.create_task(self._ping(ws))
            try:
                async for raw in ws:
                    self.last_rx = time.monotonic()
                    for part in raw.split(RS):
                        if part:
                            self._on_message(json.loads(part))
            finally:
                pinger.cancel()
                self._ws = None

    async def _ping(self, ws):
        # SignalR's own keepalive; the hub drops clients it hasn't heard from
        while True:
            await asyncio.sleep(10)
            await ws.send(json.dumps({"type": 6}) + RS)

    def _on_message(self, m):
        kind = m.get("type")
        if kind == 3 and m.get("invocationId") == "0":
            if m.get("error"):
                raise RuntimeError("subscribe refused: " + str(m["error"]))
            snap = m.get("result") or {}
            self.connected = True
            self.last_error = None
            self.raw = merge(None, snap)
            self._queue(None, snap)
            log.info("live timing: subscribed, session %s",
                     (snap.get("SessionInfo") or {}).get("Path"))
        elif kind == 1 and m.get("target") == "feed":
            args = m.get("arguments") or []
            if len(args) < 2:
                return
            topic, data = args[0], args[1]
            new_session = (topic == "SessionInfo" and isinstance(data, dict) and data.get("Key")
                           and data["Key"] != (self.raw.get("SessionInfo") or {}).get("Key"))
            # record first: a new file opens with the snapshot as it stood
            # before this delta, or replaying would apply the delta twice
            self._record(topic, data)
            self.raw[topic] = merge(self.raw.get(topic), data)
            self._queue(topic, data)
            if new_session and self._ws:
                # a new session reuses topics the old one filled; resubscribe for
                # a clean snapshot instead of merging one session into another
                log.info("live timing: new session, resubscribing")
                asyncio.ensure_future(self._ws.close())
        elif kind == 7:
            raise ConnectionError("hub closed the connection: " + str(m.get("error")))

    # ---- recording ----

    def _record(self, topic, data):
        if not config.RECORD or topic == "Heartbeat":
            return
        path = (self.raw.get("SessionInfo") or {}).get("Path") or ""
        bits = [b for b in path.strip("/").split("/") if b]
        if len(bits) < 3:
            return
        name = f"{bits[2][:10]}_{bits[1][11:]}_{bits[2][11:]}.jsonl"
        try:
            if name != self._rec_name:
                if self._rec:
                    self._rec.close()
                RECORD_DIR.mkdir(parents=True, exist_ok=True)
                fresh = not (RECORD_DIR / name).exists()
                self._rec = open(RECORD_DIR / name, "a", encoding="utf-8")
                self._rec_name = name
                if fresh:
                    self._rec.write(json.dumps({"snapshot": self.raw}) + "\n")
            self._rec.write(json.dumps({"t": time.time(), "topic": topic, "data": data}) + "\n")
            self._rec.flush()
        except OSError as e:
            log.warning("recording off: %s", e)
            self._rec = None

    # ---- replay ----

    async def _run_replay(self):
        path = Path(config.REPLAY)
        log.info("replaying %s at %sx", path, config.REPLAY_SPEED)
        with open(path, encoding="utf-8-sig") as f:
            first = json.loads(f.readline())
            snap = first.get("snapshot", first)
            self._apply(None, snap)
            self.connected = True
            self.last_rx = time.monotonic()
            prev_t = None
            for line in f:
                if not line.strip():
                    continue
                m = json.loads(line)
                if prev_t is not None:
                    await asyncio.sleep(max(0.0, (m["t"] - prev_t) / config.REPLAY_SPEED))
                prev_t = m["t"]
                self._apply(m["topic"], m["data"])
                self.last_rx = time.monotonic()
                # every line carries the moment it arrived, so the replay's
                # clock follows the recording even with no heartbeats in it
                self._replay_clock = (datetime.fromtimestamp(m["t"], timezone.utc), time.monotonic())
        log.info("replay finished; holding the final state")
        while True:
            self.last_rx = time.monotonic()
            await asyncio.sleep(5)


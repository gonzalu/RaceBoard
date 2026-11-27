"""Moments worth the whole screen: overtakes, battles, flags, the chequered flag.

Each poll hands over the timing board it just built; comparing it with the
previous one finds what changed. The board is already spoiler-delayed, so the
alerts are too. Every event gets a rising id, and the panel shows each id once.

The feed has no "overtake" message, so passes are inferred from the order and
filtered hard: no alert for places changed in the pits, by a retirement, under
a safety car, on the opening lap, or in a burst (a reconnect, say) where many
places change at once. A missed pass is better than a false one.
"""

import threading
import time
from collections import deque

from . import config

KEEP_S = 120          # how long an event stays in /api/board for screens to pick up
REBASE_S = 15         # a gap this long since the last look: start fresh, alert nothing
PIT_GRACE_S = 90      # places changed this soon after a pit stop don't count
BURST = 3             # more passes than this in one look is a data jump, not racing
TOP = 10              # overtakes and battles only for these places
BATTLE_GAP = 1.0      # seconds
BATTLE_COOLDOWN = 10  # laps before the same pair is called again
CAUTION = {"4", "5", "6", "7"}
FLAGS = {"4": "sc", "5": "red", "6": "vsc"}


def _gap(v):
    try:
        return float(str(v).lstrip("+"))
    except ValueError:
        return None   # "1 L", "LAP 3" and the like


class Alerts:
    def __init__(self):
        self._lock = threading.Lock()
        self._events = deque(maxlen=30)
        self._id = 0
        self._reset(None)
        self._seen = 0.0

    def _reset(self, key):
        self._key = key
        self._rows = {}
        self._track = None
        self._status = None
        self._pitted = {}
        self._battle = {}
        self._called = {}

    def _emit(self, kind, **data):
        if kind not in config.ALERTS:
            return
        self._id += 1
        self._events.append(dict(data, id=self._id, kind=kind, at=time.time()))

    def recent(self):
        cut = time.time() - KEEP_S
        with self._lock:
            return [e for e in self._events if e["at"] >= cut]

    def update(self, t):
        if not t:
            return
        now = time.time()
        with self._lock:
            key = (t["meeting"], t["session"])
            fresh = key != self._key or now - self._seen > REBASE_S
            self._seen = now
            if fresh:
                self._reset(key)
            else:
                self._compare(t, now)
            self._rows = {r["num"]: r for r in t["rows"]}
            self._track = t["track"].get("code")
            self._status = t["status"]

    def _compare(self, t, now):
        rows = {r["num"]: r for r in t["rows"]}
        for r in rows.values():
            if r["in_pit"] or r["pit_out"]:
                self._pitted[r["num"]] = now

        code = t["track"].get("code")
        if code != self._track and code:
            if code in FLAGS:
                self._emit("flag", flag=FLAGS[code])
            elif code == "1" and self._track in CAUTION:
                self._emit("flag", flag="green")

        if t["status"] in ("Finished", "Finalised") and self._status in ("Started", "Aborted"):
            leader = t["rows"][0] if t["rows"] else None
            self._emit("chequered", race=t["type"] == "Race",
                       tla=leader["tla"] if leader else "", colour=leader["colour"] if leader else "")

        racing = (t["type"] == "Race" and t["status"] == "Started"
                  and code in ("1", "2") and (t.get("lap") or 0) >= 2)
        if racing:
            self._overtakes(t, rows, now)
            self._battles(t, rows, now)

    def _clean(self, r, now):
        return not (r["in_pit"] or r["pit_out"] or r["retired"] or r["stopped"]
                    or now - self._pitted.get(r["num"], 0) < PIT_GRACE_S)

    def _overtakes(self, t, rows, now):
        passes = []
        for a in rows.values():
            pa = self._rows.get(a["num"])
            if not a["pos"] or a["pos"] > TOP or not pa or not pa["pos"] or pa["pos"] <= a["pos"]:
                continue
            for b in rows.values():
                pb = self._rows.get(b["num"])
                if b is a or not b["pos"] or not pb or not pb["pos"]:
                    continue
                if pb["pos"] < pa["pos"] and b["pos"] > a["pos"]:
                    passes.append((a, b))
        if not passes or len(passes) > BURST:
            return
        for a, b in passes:
            if self._clean(a, now) and self._clean(b, now):
                self._emit("overtake", tla=a["tla"], colour=a["colour"], pos=a["pos"],
                           passed=b["tla"], passed_colour=b["colour"], lap=t.get("lap"))

    def _battles(self, t, rows, now):
        by_pos = {r["pos"]: r for r in rows.values() if r["pos"]}
        for r in rows.values():
            lap = r.get("laps_done")
            prev = self._rows.get(r["num"])
            if not r["pos"] or r["pos"] < 2 or r["pos"] > TOP or lap is None:
                continue
            if prev and prev.get("laps_done") == lap:
                continue   # judge once per lap, as the car crosses the line
            ahead = by_pos.get(r["pos"] - 1)
            gap = _gap(r.get("interval"))
            close = (ahead is not None and gap is not None and gap < BATTLE_GAP
                     and self._clean(r, now) and self._clean(ahead, now))
            last = self._battle.get(r["num"])
            if (close and last and last["close"] and last["ahead"] == ahead["num"]
                    and last["lap"] == lap - 1):
                pair = frozenset((r["num"], ahead["num"]))
                if lap - self._called.get(pair, -99) >= BATTLE_COOLDOWN:
                    self._called[pair] = lap
                    self._emit("battle", pos=ahead["pos"], ahead=ahead["tla"],
                               ahead_colour=ahead["colour"], behind=r["tla"],
                               behind_colour=r["colour"], gap=gap, lap=t.get("lap"))
            self._battle[r["num"]] = {"ahead": ahead["num"] if ahead else None,
                                      "lap": lap, "close": close}

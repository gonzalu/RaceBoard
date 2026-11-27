"""Settings, all from the environment (see raceboard.env.example)."""

import os


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


# Seconds to hold every live update before it reaches the board. The timing
# feed runs ahead of the TV picture, so with no delay the board calls overtakes
# before you see them. Tune it against your own stream.
DELAY_S = _float("RACEBOARD_DELAY_S", 0)

# How long after a session ends the board keeps its final classification up
# before going back to the between-sessions pages.
RESULT_HOLD_MIN = _float("RACEBOARD_RESULT_HOLD_MIN", 90)

# How early before a session's scheduled start the board switches to timing.
PRE_SESSION_MIN = _float("RACEBOARD_PRE_SESSION_MIN", 15)

# Play a recording instead of the live feed: a .jsonl written by the recorder,
# or a plain snapshot .json. For building and testing between race weekends.
REPLAY = os.environ.get("RACEBOARD_REPLAY", "").strip()
REPLAY_SPEED = _float("RACEBOARD_REPLAY_SPEED", 1)

# Write the raw feed of every live session to data/recordings/, so it can be
# replayed later. F1 no longer serves its session archive without a login.
RECORD = os.environ.get("RACEBOARD_RECORD", "1").strip() not in ("0", "false", "no", "")

# IANA zone for session times on the board, e.g. America/New_York. Empty means
# the server's own local time.
TZ = os.environ.get("RACEBOARD_TZ", "").strip()

LIVE_BASE = "https://livetiming.formula1.com"
JOLPICA = "https://api.jolpi.ca/ergast/f1"

# Full-screen alerts during a session, any of: overtake, battle, flag,
# chequered. "none" turns them all off.
_ALERT_KINDS = ("overtake", "battle", "flag", "chequered")
_alerts = os.environ.get("RACEBOARD_ALERTS", "all").strip().lower()
ALERTS = (set(_ALERT_KINDS) if _alerts in ("", "all") else
          set() if _alerts in ("none", "0", "off") else
          {a.strip() for a in _alerts.split(",") if a.strip() in _ALERT_KINDS})

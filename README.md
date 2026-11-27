<div align="center">

# 🏁 RaceBoard

**A dot-matrix LED Formula 1 timing board for your TV.**

</div>

RaceBoard turns any browser, and any TV with a Chromecast or a spare Raspberry
Pi, into a live F1 timing tower: the whole field, gaps and intervals, tyres,
pit stops, flags and race control messages, updating as it happens. Between
sessions it counts down to the next one and cycles through the standings and
the last result.

It isn't styled to look like an LED panel, it emulates one: a real
framebuffer, text drawn from the Adafruit GFX bitmap font that LED matrix
hardware uses, and every dot an individually lit LED.

**No subscription and no account.** RaceBoard reads the same public live timing
feed as the timing page on formula1.com, which needs no login for timing data.

| | |
|---|---|
| ![timing tower](img/timing.jpg) | ![next race](img/next.jpg) |
| **During a session:** all 22 cars at once | **Between sessions:** countdown and weekend schedule |
| ![driver standings](img/drivers.jpg) | ![last result](img/result.jpg) |
| **Driver standings** (constructors too) | **The last race's result** |

---

## What it shows

**During a session** (from 15 minutes before it starts until 90 minutes after
it ends):

- the running order in two columns of eleven, each car with its team colour
- a value column that cycles every 8 s: interval, gap to the leader, last
  lap and places gained or lost since the start in a race; best lap and gap
  in practice and qualifying
- the current tyre and its age, `IN PIT` / `PIT EXIT` / `STOPPED` / `OUT`
- fastest lap holder in purple; knocked-out qualifiers dimmed
- laps (race) or session clock (practice, qualifying)
- track status: green, yellow, safety car, VSC, red flag and chequered flag
  fill the whole band
- the latest race control message along the bottom, or the weather

**Full-screen alerts** take over for 6 s when something happens:

- **Overtake** for a place in the top 10
- **Battle** when two cars in the top 10 cross the line within a second of
  each other two laps running
- **Safety car**, **virtual safety car**, **red flag**, and **green flag**
  when the track clears
- **Chequered flag**, with the winner

The feed has no overtake message, so passes are worked out from the running
order and filtered hard. Places changed in the pits, by a retirement, under a
safety car or on the opening lap don't count. Neither does a burst of changes
at once, which is usually the feed catching up. A missed pass is better than
a false one.

**Between sessions**, 12 s each:

- the next Grand Prix, with a countdown to its next session and the weekend's
  schedule in your time zone
- the track: a map of the circuit and its length, when it first held a
  Grand Prix, who has won there most, the last winners, and the weekend
  forecast once it's within two weeks
- qualifying, from when it's run until the race
- driver and constructor standings, with places gained or lost at the last
  round
- the last race's result
- on this day: Grands Prix held on today's date in past years, and who won

## Where the data comes from

| What | Source | Notes |
|---|---|---|
| Live timing | `livetiming.formula1.com/signalrcore` | F1's own feed. Unofficial and undocumented; no login needed for timing. Car telemetry and GPS need an F1 TV login, so RaceBoard doesn't use them. |
| Schedule, standings, results, history | [Jolpica](https://github.com/jolpica/jolpica-f1) (`api.jolpi.ca`) | The successor to the Ergast API. Free, no key. Every winner since 1950 is fetched once and kept in `data/history.json`, refreshed weekly. |
| Race weekend forecast | [Open-Meteo](https://open-meteo.com/) | Free, no key. |
| Track maps and lengths | [bacinger/f1-circuits](https://github.com/bacinger/f1-circuits) | MIT licence. A copy is kept in `backend/circuits.geojson`, so no download is needed. A circuit missing from it gets the track page without a map. |

F1 can change the live feed whenever it likes. If the feed stops answering,
the board falls back to the schedule pages, and a feed that drops mid-session
shows `NO FEED` in the corner instead of freezing silently.

## The spoiler delay

The timing feed runs **ahead of every TV broadcast**, often by a lot on a
streaming service. With no delay, the board shows a pass before you see it.
Set `RACEBOARD_DELAY_S` in `raceboard.env` to hold every update that many
seconds. Start around 30 and adjust until the board moves just after the TV.

## Install

You need a Linux machine that stays on (a Raspberry Pi is plenty) with
Python 3.10 or newer and git. RaceBoard runs in its own virtualenv as one
systemd service.

```bash
git clone https://github.com/gonzalu/RaceBoard.git ~/RaceBoard
cd ~/RaceBoard
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cp raceboard.env.example raceboard.env      # then edit: delay, time zone
```

Try it:

```bash
set -a; . ./raceboard.env; set +a
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8093 --no-access-log
```

`--no-access-log` keeps uvicorn from logging every request. Each open screen
asks for an update once a second, which would otherwise bury everything else.

Open `http://<that machine's address>:8093/`.

Run it at boot:

```bash
sed "s#YOUR-USER#$USER#g" raceboard.service | sudo tee /etc/systemd/system/raceboard.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now raceboard
```

Check it's healthy:

```bash
systemctl status raceboard --no-pager
journalctl -u raceboard -n 20 --no-pager
```

You want a `live timing: subscribed` line and `200 OK` from `api.jolpi.ca`.

## Updating

```bash
cd ~/RaceBoard && git pull
.venv/bin/pip install -r backend/requirements.txt
sed "s#YOUR-USER#$USER#g" raceboard.service | sudo tee /etc/systemd/system/raceboard.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl restart raceboard
```

Your `raceboard.env` and recordings are untouched. Screens already showing the
board reload themselves.

## Getting it onto a TV

### Option A: Chromecast

The Chromecast loads the page and renders it itself, so no computer has to
stay awake driving the picture. [catt](https://github.com/skorokithakis/catt)
does the casting. Install it in RaceBoard's virtualenv and find your
Chromecast's exact name:

```bash
.venv/bin/pip install catt
.venv/bin/catt scan
```

Then cast the board to it, using the name exactly as `catt scan` printed it:

```bash
.venv/bin/catt -d "YOUR-CHROMECAST" cast_site "http://YOUR-SERVER-IP:8093/"
```

> **Use the IP address, not a hostname.** A Chromecast uses Google's DNS
> servers and ignores the one your router hands out, so a name that resolves
> everywhere else on your network fails on the stick. If the server's address
> can change, give it a DHCP reservation in your router.

> `catt` prints *Casting…* as soon as the Chromecast accepts the URL, before
> it has tried to load anything. Look at the TV to be sure.

The cast lasts until the Chromecast reboots or someone casts something else
to it. Run the same command again to put the board back.

`cast_site` uses the DashCast receiver, which isn't a documented Google
feature. It works on classic Chromecast sticks and the Chromecast Ultra, but
**not** on Chromecast with Google TV. Use Option B for those.

Classic Chromecasts run an old built-in browser (roughly Chrome 70s) that
never gets updated. RaceBoard's page is written to run on it. If you change
the frontend, avoid newer JavaScript such as `??` and `?.`: one unsupported
operator stops the whole script, and the TV shows an empty black panel.

### Option B: Raspberry Pi on HDMI

The most robust option, with no casting involved. Any spare Pi running
Raspberry Pi OS Lite, with Chromium in kiosk mode:

```bash
sudo apt install chromium-browser cage
cage -- chromium-browser --kiosk --noerrdialogs --disable-infobars \
     --check-for-update-interval=31536000 http://YOUR-SERVER-IP:8093/
```

Make that a systemd service to start it at boot. `cec-utils` can switch the TV
on and off on a schedule over HDMI-CEC.

### Option C: a browser

Open `http://YOUR-SERVER-IP:8093/` and press F11.

Whichever you use, the page reloads itself when RaceBoard is updated on the
server, so a TV picks up changes without being re-cast.

## Recording and replay

F1 no longer serves its session archive without a login, so RaceBoard keeps
its own. Every live session's raw feed is written to `data/recordings/`
(`RACEBOARD_RECORD=0` turns that off). Play one back to work on the board
between race weekends:

```bash
RACEBOARD_REPLAY=data/recordings/2026-11-29_Qatar_Grand_Prix_Race.jsonl \
RACEBOARD_REPLAY_SPEED=4 \
  .venv/bin/uvicorn backend.main:app --port 8093 --no-access-log
```

A plain snapshot (one JSON object, the hub's answer to a subscribe) also
works; it shows that moment frozen.

## Configuration

All in `raceboard.env`; see `raceboard.env.example` for the full list.

| Variable | Default | |
|---|---|---|
| `RACEBOARD_DELAY_S` | `0` | Spoiler delay, seconds |
| `RACEBOARD_TZ` | server's zone | Time zone for session times, e.g. `America/New_York` |
| `RACEBOARD_RESULT_HOLD_MIN` | `90` | Minutes a finished session's classification stays up |
| `RACEBOARD_PRE_SESSION_MIN` | `15` | Minutes before a session the tower appears |
| `RACEBOARD_ALERTS` | `all` | Full-screen alerts: any of `overtake,battle,flag,chequered`, or `none` |
| `RACEBOARD_RECORD` | `1` | Record live sessions to `data/recordings/` |
| `RACEBOARD_REPLAY` | | Replay a recording instead of the live feed |
| `RACEBOARD_REPLAY_SPEED` | `1` | Replay speed multiplier |

## License

Apache 2.0. The bitmap font is Adafruit's glcdfont (BSD). The circuit
outlines are Tomislav Bacinger's f1-circuits (MIT, `backend/circuits.LICENSE`). Formula 1, F1 and
team names are trademarks of their owners; RaceBoard is not affiliated with
or endorsed by Formula 1.

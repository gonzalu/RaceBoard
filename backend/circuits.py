"""Track outlines for the track page.

The outline and its orientation come from F1's own track positions, as
served by the MultiViewer API (api.multiviewer.app), the same data the F1
live timing apps draw their maps from, with the rotation F1 TV uses. The line
starts at the finish line and runs in the direction of travel. One file per
circuit is kept in data/circuits/ and refreshed monthly.

circuits.geojson is bacinger/f1-circuits (MIT, see circuits.LICENSE): each
circuit as a GPS line, with its length. It gives the length, and the outline
for any circuit MultiViewer doesn't have, matched by distance to the circuit
position Jolpica gives and turned to fill a landscape box.

Either way the panel gets points scaled to fit a 1x1 square.
"""

import json
import logging
import math
import time
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("raceboard.circuits")

SOURCE = Path(__file__).resolve().parent / "circuits.geojson"
CACHE = Path(__file__).resolve().parent.parent / "data" / "circuits"
MULTIVIEWER = "https://api.multiviewer.app/api/v1/circuits"
MAX_AGE_S = 30 * 24 * 3600
MATCH_KM = 10
BOX = (1.55, 1.0)   # width:height of the space the panel draws the map in

# Jolpica's circuit ids to F1's circuit keys, which MultiViewer is indexed by
F1_KEYS = {
    "albert_park": 10, "americas": 9, "bahrain": 63, "baku": 144, "catalunya": 15,
    "hungaroring": 4, "imola": 6, "interlagos": 14, "jeddah": 149, "losail": 150,
    "marina_bay": 61, "miami": 151, "monaco": 22, "monza": 39, "red_bull_ring": 19,
    "rodriguez": 65, "shanghai": 49, "silverstone": 2, "spa": 7, "suzuka": 46,
    "vegas": 152, "villeneuve": 23, "yas_marina": 70, "zandvoort": 55,
}


def _load():
    try:
        return json.loads(SOURCE.read_text(encoding="utf-8"))["features"]
    except (OSError, ValueError, KeyError):
        return []


_FEATURES = _load()


def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _centre(coords):
    return (sum(c[1] for c in coords) / len(coords), sum(c[0] for c in coords) / len(coords))


def _fit(coords):
    """Flat x/y (y down), turned to fill BOX, scaled into 0..1."""
    lat0 = _centre(coords)[0]
    k = math.cos(math.radians(lat0))
    pts = [(lon * k, -lat) for lon, lat in coords]
    best = None
    for deg in range(0, 180, 3):
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)
        rot = [(x * ca - y * sa, x * sa + y * ca) for x, y in pts]
        xs, ys = [p[0] for p in rot], [p[1] for p in rot]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        scale = min(BOX[0] / w, BOX[1] / h) if w and h else 0
        if not best or scale > best[0]:
            best = (scale, rot, min(xs), min(ys), w, h)
    _, rot, x0, y0, w, h = best
    span = max(w, h)
    return [[round((x - x0) / span, 4), round((y - y0) / span, 4)] for x, y in rot], round(w / span, 4), round(h / span, 4)


def _f1_file(circuit_id):
    key = F1_KEYS.get(circuit_id)
    return CACHE / f"{key}.json" if key else None


async def refresh(c, circuit_id):
    """Fetch F1's outline for this circuit unless the kept copy is recent."""
    path = _f1_file(circuit_id)
    if not path or (path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_S):
        return
    r = await c.get(f"{MULTIVIEWER}/{F1_KEYS[circuit_id]}/{time.gmtime().tm_year}")
    r.raise_for_status()
    d = r.json()
    if not d.get("x") or len(d["x"]) != len(d.get("y") or []):
        raise ValueError("no outline in the answer")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: d.get(k) for k in ("circuitName", "rotation", "x", "y")}),
                    encoding="utf-8")
    _f1_outline.cache_clear()
    log.info("circuits: %s outline fetched", circuit_id)


@lru_cache(maxsize=32)
def _f1_outline(circuit_id):
    """F1's outline, turned the way F1 TV shows it, or None if not kept."""
    path = _f1_file(circuit_id)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (AttributeError, OSError, ValueError):
        return None
    # As the F1 live timing maps draw it: rotate by the circuit's rotation
    # plus 90 degrees, with x and y swapped, on a screen whose y runs down.
    a = math.radians((d.get("rotation") or 0) + 90)
    ca, sa = math.cos(a), math.sin(a)
    pts = [(y * ca + x * sa, x * ca - y * sa) for x, y in zip(d["x"], d["y"])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0, max(ys) - y0
    span = max(w, h) or 1
    return ([[round((x - x0) / span, 4), round((y - y0) / span, 4)] for x, y in pts],
            round(w / span, 4), round(h / span, 4))


def outline(lat, lon, circuit_id=None):
    """The circuit's outline and length, or None if neither source has it."""
    g = _geo_outline(lat, lon)
    f1 = _f1_outline(circuit_id) if circuit_id else None
    if f1:
        points, w, h = f1
        return {"name": g["name"] if g else None, "length_m": g["length_m"] if g else None,
                "points": points, "w": w, "h": h}
    return g


@lru_cache(maxsize=64)
def _geo_outline(lat, lon):
    """The geojson outline nearest this position, or None if no circuit is close."""
    best = None
    for f in _FEATURES:
        coords = f["geometry"]["coordinates"]
        if not coords:
            continue
        d = _km(lat, lon, *_centre(coords))
        if d <= MATCH_KM and (not best or d < best[0]):
            best = (d, f)
    if not best:
        return None
    f = best[1]
    points, w, h = _fit(f["geometry"]["coordinates"])
    return {"name": f["properties"].get("Name"), "length_m": f["properties"].get("length"),
            "points": points, "w": w, "h": h}

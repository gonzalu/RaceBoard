"""Track outlines for the track page.

circuits.geojson is bacinger/f1-circuits (MIT, see circuits.LICENSE): each
circuit as a GPS line, with its length. Jolpica names circuits its own way,
so a race is matched to an outline by distance, using the circuit position
Jolpica gives.

An outline is turned to whichever angle fills a landscape box best, then
handed to the panel as points scaled to fit a 1x1 square.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "circuits.geojson"
MATCH_KM = 10
BOX = (1.55, 1.0)   # width:height of the space the panel draws the map in


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


@lru_cache(maxsize=64)
def outline(lat, lon):
    """The outline nearest this position, or None if no circuit is close."""
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

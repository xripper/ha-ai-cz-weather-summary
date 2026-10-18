"""Build simplified region polygons from CHMI regional forecast files.

Usage: python script/build_regions.py <dir with RPxx.json files>

Input files are the latest web_pCK0tx_<RPxx>_*.json from
https://opendata.chmi.cz/meteorology/weather/forecast/now/ saved as <RPxx>.json.
Writes custom_components/cz_weather_summary/regions.json.
"""

import json
import sys
from pathlib import Path

STEP = 8  # keep every Nth vertex
OUT = Path(__file__).parent.parent / "custom_components/cz_weather_summary/regions.json"


def main(src: str) -> None:
    regions = {}
    for path in sorted(Path(src).glob("RP*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        feature = data["data"]["features"][0]
        geom = feature["geometry"]
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        rings = []
        for poly in polys:
            ring = poly[0]
            simplified = ring[::STEP]
            rings.append([[round(x, 4), round(y, 4)] for x, y in simplified])
        regions[path.stem] = rings
    OUT.write_text(json.dumps(regions, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(regions)} regions)")


if __name__ == "__main__":
    main(sys.argv[1])

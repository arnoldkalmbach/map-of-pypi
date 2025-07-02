import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

import networkx as nx
from mock_data_export import export_mock_data_with_coords

ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_FILE = ROOT / 'public' / 'mock-data' / 'raw_data' / '100_sci_packages.json'
MOCK_V1_DIR = ROOT / 'public' / 'mock-data' / 'v1'
NAMES_DIR = MOCK_V1_DIR / 'names'
GRAPHS_DIR = MOCK_V1_DIR / 'graphs'

# Bounding box for generated coordinates (roughly around San Francisco)
MIN_LON, MAX_LON = -122.55, -122.25
MIN_LAT, MAX_LAT = 37.65, 37.95
GRID_COLS = 10  # we will generate a 10 x 10 grid => up to 100 packages

EDGE_PATTERN = re.compile(r'^[A-Za-z0-9_.-]+')

def load_raw_packages() -> List[Dict]:
    with open(RAW_DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def assign_coordinates(pkgs: List[Dict]) -> Dict[str, Tuple[float, float]]:
    coords = {}
    col_step = (MAX_LON - MIN_LON) / (GRID_COLS - 1)
    row_step = (MAX_LAT - MIN_LAT) / (GRID_COLS - 1)
    for idx, pkg in enumerate(pkgs):
        row, col = divmod(idx, GRID_COLS)
        lon = MIN_LON + col * col_step
        lat = MIN_LAT + row * row_step
        coords[pkg['name'].lower()] = (lon, lat)
    return coords


def extract_dep_name(raw: str) -> str:
    """Return the canonical dependency package name from requires_dist entry."""
    match = EDGE_PATTERN.match(raw)
    return match.group(0).lower() if match else ''


def build_graph(pkgs: List[Dict], coords: Dict[str, Tuple[float, float]]):
    names_set = {p['name'].lower() for p in pkgs}
    lines = ["digraph {"]
    # nodes
    for name in names_set:
        lon, lat = coords[name]
        lines.append(f'  "{name}" [l="{lon},{lat}"];')
    lines.append("")
    # edges
    for p in pkgs:
        src = p['name'].lower()
        for dep_raw in p.get('requires_dist', []):
            dep = extract_dep_name(dep_raw)
            if dep and dep in names_set:
                lines.append(f'  "{src}" -> "{dep}";')
    lines.append("} ")
    return "\n".join(lines)


def build_names(pkgs: List[Dict], coords: Dict[str, Tuple[float, float]]):
    letter_map: Dict[str, List] = {}
    for p in pkgs:
        name = p['name']
        lon, lat = coords[name.lower()]
        first = name[0].lower()
        letter_map.setdefault(first, []).append([name, lon, lat])
    return letter_map


def write_json(path: Path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def build_places(coords: Dict[str, Tuple[float, float]]):
    # simple centroid of all points
    lons = [lon for lon, _ in coords.values()]
    lats = [lat for _, lat in coords.values()]
    center = (sum(lons) / len(lons), sum(lats) / len(lats))
    feature = {
        "type": "Feature",
        "id": 0,
        "properties": {"name": "Science Pkgs"},
        "geometry": {"type": "Point", "coordinates": list(center)},
    }
    return {"type": "FeatureCollection", "features": [feature]}


def main():
    pkgs = load_raw_packages()
    coords = assign_coordinates(pkgs)

    # Build NetworkX graph (lowercase identifiers)
    G = nx.DiGraph()
    for pkg in pkgs:
        G.add_node(pkg['name'].lower())

    for pkg in pkgs:
        src = pkg['name'].lower()
        for dep_raw in pkg.get('requires_dist', []):
            dep = extract_dep_name(dep_raw)
            if dep and dep in coords:
                G.add_edge(src, dep)

    # Export using shared utility (writes graphs, names, places)
    export_mock_data_with_coords(
        G,
        coords,
        [p['name'] for p in pkgs],
        data_version='v1',
        root=ROOT,
    )

    # Borders remain unchanged – we reuse the existing single-rectangle file.

    print("Mock data generated successfully.")


if __name__ == '__main__':
    main() 
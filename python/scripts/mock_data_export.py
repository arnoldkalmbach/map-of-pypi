from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple, List
import json
import networkx as nx
from networkx.drawing.nx_pydot import write_dot

# Bounding box (roughly around San Francisco)
MIN_LON, MAX_LON = -122.55, -122.25
MIN_LAT, MAX_LAT = 37.65, 37.95

BBox = Tuple[float, float, float, float]


def _remap_positions_to_bbox(
    pos: Mapping[str, Tuple[float, float]],
    bbox: BBox = (MIN_LON, MAX_LON, MIN_LAT, MAX_LAT),
) -> Dict[str, Tuple[float, float]]:
    """Linearly rescale arbitrary 2-D layout `pos` into lon/lat bbox.

    Returns a mapping name -> (lon, lat).
    """
    min_lon, max_lon, min_lat, max_lat = bbox

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    # Avoid division by zero in pathological cases
    range_x = (max_x - min_x) or 1e-6
    range_y = (max_y - min_y) or 1e-6

    coords: Dict[str, Tuple[float, float]] = {}
    for name, (x, y) in pos.items():
        lon = min_lon + (x - min_x) / range_x * (max_lon - min_lon)
        lat = min_lat + (y - min_y) / range_y * (max_lat - min_lat)
        coords[name.lower()] = (lon, lat)
    return coords


def _export_graph(G: nx.DiGraph, coords: Mapping[str, Tuple[float, float]], out_path: Path) -> None:
    """Write `G` to `out_path` in DOT format using networkx → pydot.

    Node identifiers are lowered. Each node gets an attribute `l="lon,lat"` used
    by the front-end to position the node.
    """
    # Relabel nodes to lower-case identifiers (required by front-end search/index)
    G_lower = nx.relabel_nodes(G, lambda n: n.lower(), copy=True)

    # Attach coordinate attribute expected by front-end
    for node in G_lower.nodes:
        lon, lat = coords[node]
        G_lower.nodes[node]["l"] = f"{lon},{lat}"

    write_dot(G_lower, str(out_path))


def _export_names(
    package_names: Sequence[str],
    coords: Mapping[str, Tuple[float, float]],
    names_dir: Path,
) -> None:
    letter_map: Dict[str, List] = {}
    for name in package_names:
        lname = name.lower()
        lon, lat = coords.get(lname, (None, None))
        if lon is None:
            continue  # safety – skip names not found in coords
        first = lname[0]
        letter_map.setdefault(first, []).append([name, lon, lat])

    for letter, arr in letter_map.items():
        (names_dir / f"{letter}.json").write_text(json.dumps(arr, indent=2), encoding="utf-8")


def _export_places(coords: Mapping[str, Tuple[float, float]], out_path: Path) -> None:
    all_lons = [c[0] for c in coords.values()]
    all_lats = [c[1] for c in coords.values()]
    center = (sum(all_lons) / len(all_lons), sum(all_lats) / len(all_lats))
    places = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": 0,
                "properties": {"name": "Top Packages"},
                "geometry": {"type": "Point", "coordinates": list(center)},
            }
        ],
    }
    out_path.write_text(json.dumps(places, indent=2), encoding="utf-8")


def export_mock_data(
    G: nx.DiGraph,
    pos: Mapping[str, Tuple[float, float]],
    package_names: Sequence[str],
    *,
    data_version: str = "v2",
    bbox: BBox | None = None,
    root: Path | None = None,
) -> None:
    """Export graph + layout into mock-data file structure.

    Parameters
    ----------
    G : nx.DiGraph
        The graph of dependencies.
    pos : mapping name -> (x, y)
        Coordinates from whichever layout algorithm you used.
    package_names : sequence[str]
        The **original-case** package names in the dataset (used for search index).
    data_version : str, default "v2"
        Sub-directory under `public/mock-data/` to write files to.
    bbox : tuple[lon_min, lon_max, lat_min, lat_max] | None
        Override the default SF-ish bounding box.
    root : Path | None
        Repository root. If omitted, inferred three directories up from this file.
    """
    if root is None:
        root = Path(__file__).resolve().parents[2]  # .../map-of-pypi

    mock_dir = root / "public" / "mock-data" / data_version
    names_dir = mock_dir / "names"
    graphs_dir = mock_dir / "graphs"
    names_dir.mkdir(parents=True, exist_ok=True)
    graphs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Remap layout positions → geographic coords
    coords = _remap_positions_to_bbox(pos, bbox or (MIN_LON, MAX_LON, MIN_LAT, MAX_LAT))

    # 2. Graph
    _export_graph(G, coords, graphs_dir / "0.graph.dot")

    # 3. Names search index
    _export_names(package_names, coords, names_dir)

    # 4. Places (centroid)
    _export_places(coords, mock_dir / "places.geojson")

    relative = mock_dir.relative_to(root)
    print(f"Mock data exported to {relative} (graphs, names, places)")


# ---------------------------------------------------------------------------
# Convenience wrapper when you *already* have geographic coordinates and don't
# need automatic remapping (e.g. generate_mock_data's grid assignment).
# ---------------------------------------------------------------------------


def export_mock_data_with_coords(
    G: nx.DiGraph,
    coords: Mapping[str, Tuple[float, float]],
    package_names: Sequence[str],
    *,
    data_version: str = "v1",
    root: Path | None = None,
) -> None:
    """Same as `export_mock_data`, but skips coordinate remapping.

    Parameters
    ----------
    G : nx.DiGraph
        Graph whose nodes already correspond to *lowercase* package names.
    coords : mapping name -> (lon, lat)
        Pre-computed geographic coordinates.
    package_names : sequence[str]
        Original-case names for the search index.
    data_version : str
        Target sub-directory (e.g. "v1").
    root : Path | None
        Repository root. Inferred if omitted.
    """

    if root is None:
        root = Path(__file__).resolve().parents[2]

    mock_dir = root / "public" / "mock-data" / data_version
    names_dir = mock_dir / "names"
    graphs_dir = mock_dir / "graphs"
    names_dir.mkdir(parents=True, exist_ok=True)
    graphs_dir.mkdir(parents=True, exist_ok=True)

    # Graph (coords already final)
    _export_graph(G, coords, graphs_dir / "0.graph.dot")

    # Names search index
    _export_names(package_names, coords, names_dir)

    # Places geojson
    _export_places(coords, mock_dir / "places.geojson")

    print(f"Mock data exported to {mock_dir.relative_to(root)} (graphs, names, places)") 
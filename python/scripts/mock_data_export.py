from pathlib import Path
from typing import Sequence
import json
import math
import networkx as nx
from networkx.drawing.nx_pydot import write_dot
import subprocess
import tempfile
import os
import shutil
import geopandas as gpd
from shapely.geometry import Point, Polygon
import numpy as np

# Bounding box (roughly around San Francisco)
MIN_LON, MAX_LON = -122.55, -122.25
MIN_LAT, MAX_LAT = 37.65, 37.95

BBox = tuple[float, float, float, float]


def _remap_positions_to_bbox(
    pos: dict[str, tuple[float, float]],
    bbox: BBox = (MIN_LON, MAX_LON, MIN_LAT, MAX_LAT),
) -> dict[str, tuple[float, float]]:
    """Linearly rescale arbitrary 2-D layout `pos` into lon/lat bbox.

    Returns a mapping name -> (lon, lat).
    """
    min_lon, max_lon, min_lat, max_lat = bbox
    lon_range = max_lon - min_lon
    lat_range = max_lat - min_lat

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    # Avoid division by zero in pathological cases
    range_x = (max_x - min_x) or 1e-6
    range_y = (max_y - min_y) or 1e-6

    coords: dict[str, tuple[float, float]] = {}
    for name, (x, y) in pos.items():
        x_01 = (x - min_x) / range_x
        y_01 = (y - min_y) / range_y
        lon = min_lon + x_01 * lon_range
        lat = min_lat + y_01 * lat_range
        coords[name.lower()] = (lon, lat)
    return coords


def _export_graph(G: nx.DiGraph, coords: dict[str, tuple[float, float]], out_path: Path) -> None:
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
    coords: dict[str, tuple[float, float]],
    names_dir: Path,
) -> None:
    letter_map: dict[str, list] = {}
    for name in package_names:
        lname = name.lower()
        lon, lat = coords.get(lname, (None, None))
        if lon is None:
            # Skip names not found in coords
            continue
        first = lname[0]
        letter_map.setdefault(first, []).append([name, lon, lat])

    for letter, arr in letter_map.items():
        (names_dir / f"{letter}.json").write_text(json.dumps(arr, indent=2), encoding="utf-8")


def _create_packages_gdf(
    coords: dict[str, tuple[float, float]],
    package_names: Sequence[str],
    G: nx.DiGraph,
) -> gpd.GeoDataFrame:
    """Create a GeoDataFrame of package points for vector tile generation."""
    data = []
    
    for name in package_names:
        lname = name.lower()
        if lname not in coords:
            continue
            
        lon, lat = coords[lname]

        # Calculate size based on in-degree (number of packages that depend on this one)
        # Scale it to a reasonable range (1-20) for visualization
        in_degree = G.in_degree(lname)
        size = max(1, min(20, int(in_degree * 0.5 + 3)))
        
        data.append({
            'label': name,
            'size': size,
            'parent': 0,  # All packages in group 0 for now
            'geometry': Point(lon, lat)
        })
    
    return gpd.GeoDataFrame(data, crs='EPSG:4326')


def _calculate_zoom_levels(minx: float, miny: float, maxx: float, maxy: float, max_zoom: int = 19) -> tuple[int, int]:
    """
    Determine the minimum zoom level at which the bounding box fits entirely within a single map tile,
    and set a reasonable maximum zoom level for vector tiles.
    """
    
    def lonlat_to_tile(lon: float, lat: float, z: int):
        # Convert degrees to radians
        lat_rad = np.radians(lat)  
        # Number of tiles per side at zoom z
        n = 2 ** z
        # X tile index
        xtile = np.floor((lon + 180.0) / 360.0 * n).astype(int)  
        # Y tile index
        ytile = np.floor(
            (1.0 
             - np.log(np.tan(lat_rad) + 1.0/np.cos(lat_rad)) / np.pi
            ) / 2.0 * n
        ).astype(int)  
        return xtile, ytile

    # Search from highest zoom down to 0 to find min zoom
    min_zoom = 0
    for z in range(max_zoom, -1, -1):
        x0, y0 = lonlat_to_tile(minx, miny, z)
        x1, y1 = lonlat_to_tile(maxx, maxy, z)
        if x0 == x1 and y0 == y1:
            min_zoom = z
            break
    
    # Set max zoom to be high enough for detailed viewing
    # For package maps, zoom 16 should be more than enough
    max_zoom_final = min(12, max_zoom)
    
    # Ensure we have at least a few zoom levels
    if max_zoom_final <= min_zoom:
        max_zoom_final = min_zoom + 4
    
    return min_zoom, max_zoom_final


def _generate_vector_tiles(
    coords: dict[str, tuple[float, float]],
    package_names: Sequence[str],
    G: nx.DiGraph,
    output_dir: Path,
) -> None:
    """Generate vector tiles using tippecanoe."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create GeoDataFrame
    gdf = _create_packages_gdf(coords, package_names, G)
    
    print(f"Generated {len(gdf)} package points")
    if len(gdf) == 0:
        print("ERROR: No package points generated!")
        return
    
    # Calculate appropriate zoom levels
    min_zoom, max_zoom = _calculate_zoom_levels(*gdf.total_bounds)
    print(f"Coordinate bounds: {gdf.total_bounds}")
    print(f"Calculated zoom levels: {min_zoom}-{max_zoom}")
    
    # Export zoom level metadata for JavaScript to read
    metadata = {
        "minzoom": min_zoom,
        "maxzoom": max_zoom,
        "bounds": gdf.total_bounds.tolist(),
        "package_count": len(gdf)
    }
    
    metadata_path = output_dir / "metadata.json"
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    print(f"Exported metadata to {metadata_path}")
    
    # Write to temporary GeoJSON file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False) as f:
        temp_geojson = f.name
    
    # Export to GeoJSON
    gdf.to_file(temp_geojson, driver='GeoJSON')
    print(f"Exported GeoJSON to {temp_geojson}")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.mbtiles', delete=False) as f:
        temp_mbtiles = f.name
    
    try:        
        # Generate mbtiles using tippecanoe with options to handle sparse data
        cmd = [
            "tippecanoe",
            "-o", str(temp_mbtiles),
            "--layer=points",
            f"--minimum-zoom={min_zoom}",
            f"--maximum-zoom={max_zoom}",
            "--no-feature-limit",
            "--no-tile-size-limit",
            "--buffer=64",
            "--force",  # Overwrite existing files
            "--hilbert",  # Better spatial distribution
            "--drop-rate=0",  # Don't drop any features
            temp_geojson
        ]
        
        print(f"Running tippecanoe command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Tippecanoe error: {result.stderr}")
            print(f"Tippecanoe stdout: {result.stdout}")
            raise subprocess.CalledProcessError(result.returncode, cmd)
        
        print(f"Tippecanoe completed successfully")
        
        # Remove existing tile directory
        for item in output_dir.iterdir():
            if item.is_dir():
                print(f"Removing existing tile directory: {item}")
                shutil.rmtree(item)
        
        # Extract tiles to directory structure
        extract_cmd = [
            "tile-join",
            "--no-tile-compression",
            "--output-to-directory=" + str(output_dir),
            "--force",
            str(temp_mbtiles)
        ]
        
        print(f"Running tile-join command: {' '.join(extract_cmd)}")
        result = subprocess.run(extract_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Tile-join error: {result.stderr}")
            print(f"Tile-join stdout: {result.stdout}")
            raise subprocess.CalledProcessError(result.returncode, extract_cmd)
        
        print(f"Vector tiles generated successfully at {output_dir}")
        
    except subprocess.CalledProcessError as e:
        print(f"Error generating vector tiles: {e}")
        print("Make sure tippecanoe is installed and in PATH")
    except FileNotFoundError:
        print("tippecanoe not found. Install with: sudo apt install tippecanoe")
    finally:
        # Clean up temporary file
        if os.path.exists(temp_geojson):
            os.unlink(temp_geojson)
        if os.path.exists(temp_mbtiles):
            os.unlink(temp_mbtiles)


def _create_borders_gdf(coords: dict[str, tuple[float, float]]) -> gpd.GeoDataFrame:
    """Create borders GeoDataFrame that encompasses all package coordinates."""
    # Calculate bounding box from actual coordinates
    all_lons = [c[0] for c in coords.values()]
    all_lats = [c[1] for c in coords.values()]
    
    if not all_lons or not all_lats:
        # Fallback to default bbox
        min_lon, max_lon = MIN_LON, MAX_LON
        min_lat, max_lat = MIN_LAT, MAX_LAT
    else:
        min_lon, max_lon = min(all_lons), max(all_lons)
        min_lat, max_lat = min(all_lats), max(all_lats)
        
        # Add padding (5% of range)
        lon_range = max_lon - min_lon
        lat_range = max_lat - min_lat
        padding_lon = lon_range * 0.05
        padding_lat = lat_range * 0.05
        
        min_lon -= padding_lon
        max_lon += padding_lon
        min_lat -= padding_lat
        max_lat += padding_lat
    
    # Create polygon geometry
    polygon = Polygon([
        (min_lon, min_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
        (min_lon, max_lat),
        (min_lon, min_lat)
    ])
    
    data = [{
        'fill': '#516ebc',  # Color that matches the theme
        'geometry': polygon
    }]
    
    gdf = gpd.GeoDataFrame(data, crs='EPSG:4326')
    gdf.index = [0]  # Set id to 0
    return gdf


def _create_places_gdf(coords: dict[str, tuple[float, float]]) -> gpd.GeoDataFrame:
    """Create places GeoDataFrame with country labels."""
    all_lons = [c[0] for c in coords.values()]
    all_lats = [c[1] for c in coords.values()]
    center_lon = sum(all_lons) / len(all_lons)
    center_lat = sum(all_lats) / len(all_lats)
    
    data = [{
        'name': 'Python Package Land',
        'labelId': 'pypyland',
        'symbolzoom': 3,
        'geometry': Point(center_lon, center_lat)
    }]
    
    gdf = gpd.GeoDataFrame(data, crs='EPSG:4326')
    gdf.index = [0]  # Set id to 0
    return gdf


def _export_places(coords: dict[str, tuple[float, float]], out_path: Path) -> None:
    """Export places GeoDataFrame to GeoJSON."""
    gdf = _create_places_gdf(coords)
    gdf.to_file(out_path, driver='GeoJSON')


def _create_borders_geojson(coords: dict[str, tuple[float, float]], out_path: Path) -> None:
    """Create borders.geojson that encompasses all package coordinates."""
    gdf = _create_borders_gdf(coords)
    # Add explicit ID column that geopandas will use as feature ID
    gdf['id'] = gdf.index
    gdf.to_file(out_path, driver='GeoJSON')


def export_mock_data(
    G: nx.DiGraph,
    pos: dict[str, tuple[float, float]],
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

    # Take arbitrary XY coords and map them to possible lon/lat coords
    coords = _remap_positions_to_bbox(pos, bbox or (MIN_LON, MAX_LON, MIN_LAT, MAX_LAT))

    # Save the dot graph: Used for rendering edges
    _export_graph(G, coords, graphs_dir / "0.graph.dot")

    # Save the names json files: Used for searching
    _export_names(package_names, coords, names_dir)

    # Save the places geojson file: Identifies the centroid of each cluster ('country')
    # For now, we just have a single cluster in the map
    _export_places(coords, mock_dir / "places.geojson")

    # Save the borders geojson file: Used for rendering the boundaries of each cluster
    # For now we just have a single cluster, so this is the min, max, lon, lat extent
    _create_borders_geojson(coords, mock_dir / "borders.geojson")

    # Save the coordinates of each indivdual node as PBF features
    # Used for rendering the packages (when you zoom in enough) 
    _generate_vector_tiles(coords, package_names, G, mock_dir / "points")

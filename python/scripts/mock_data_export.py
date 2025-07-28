from pathlib import Path
from typing import Sequence
import json
import networkx as nx
from networkx.drawing.nx_pydot import write_dot
import subprocess
import tempfile
import os
import shutil
import geopandas as gpd
from pandas.core.internals.construction import convert_object_array
from shapely.geometry import Point, Polygon
import numpy as np
import polars as pl
from scipy.spatial import ConvexHull


# Bounding box (roughly around San Francisco)
MIN_LON, MAX_LON = -122.55, -122.25
MIN_LAT, MAX_LAT = 37.65, 37.95



def _export_graph(G: nx.DiGraph, coords: np.ndarray, out_path: Path) -> None:
    """Write `G` to `out_path` in DOT format using networkx → pydot.

    Node identifiers are lowered. Each node gets an attribute `l="lon,lat"` used
    by the front-end to position the node.
    """
    # Relabel nodes to lower-case identifiers (required by front-end search/index)
    G_lower = nx.relabel_nodes(G, lambda n: n.lower(), copy=True)

    # Attach coordinate attribute expected by front-end
    for node, (lon, lat) in zip(G_lower.nodes, coords):
        G_lower.nodes[node]["l"] = f"{lon},{lat}"

    write_dot(G_lower, str(out_path))


def _export_names(
    node_data: pl.DataFrame,
    names_dir: Path,
) -> None:
    letter_map: dict[str, list] = {}
    for name, (lon, lat) in node_data[['name', 'coords']].iter_rows(named=False):
        lname = name.lower()
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


def _create_borders_gdf(label_polygons: dict[int, np.ndarray]) -> gpd.GeoDataFrame:
    """Create borders GeoDataFrame with convex hull polygons for each cluster."""
    borders = []
    for cluster_label, points in label_polygons.items():

        poly = Polygon(points).buffer(0.01)
        borders.append({
            'fill': '#516ebc',  # Color that matches the theme
            'id': int(cluster_label),  # Use cluster_label as the border ID
            'geometry': poly
        })

    return gpd.GeoDataFrame(borders, crs='EPSG:4326', geometry='geometry')


def _create_places_gdf(polygons: dict[int, np.ndarray]) -> gpd.GeoDataFrame:
    """Create places GeoDataFrame with Point features at cluster centroids."""
    places = []
    for cluster_label, polygon in polygons.items():
        poly = Polygon(polygon)
        centroid = poly.centroid
        print(cluster_label, centroid)
            
        places.append({
            'name': f'Cluster {cluster_label}',
            'labelId': f'cluster_{cluster_label}',
            'symbolzoom': 8,  # Appropriate zoom level for labels
            'geometry': centroid
        })
    
    return gpd.GeoDataFrame(places, crs='EPSG:4326', geometry='geometry')


def _export_places(polygons: dict[int, np.ndarray], out_path: Path) -> None:
    """Export places GeoDataFrame to GeoJSON."""
    gdf = _create_places_gdf(polygons)
    gdf.to_file(out_path, driver='GeoJSON')


def _create_borders_geojson(label_polygons: dict[int, np.ndarray], out_path: Path) -> None:
    """Create borders.geojson with convex hulls for each cluster."""
    gdf = _create_borders_gdf(label_polygons)
    gdf.to_file(out_path, driver='GeoJSON')


def export_mock_data(
    G: nx.DiGraph,
    node_data: pl.DataFrame,
    label_polygons: dict[int, np.ndarray],
    data_version: str = "v2",
    root: Path | None = None,
) -> None:
    """Export graph + layout into mock-data file structure.

    Parameters
    ----------
    G : nx.DiGraph
        The graph of dependencies.
    node_data : pl.DataFrame
        The node data, including the coordinates and cluster labels.
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
    target_minx = MIN_LON
    target_miny = MIN_LAT
    range_x = (MAX_LON - MIN_LON)
    range_y = (MAX_LAT - MIN_LAT)

    data_minx = node_data['umap_embedding'].arr.get(0).min()
    data_miny = node_data['umap_embedding'].arr.get(1).min()
    data_maxx = node_data['umap_embedding'].arr.get(0).max()
    data_maxy = node_data['umap_embedding'].arr.get(1).max()
    data_range_x = data_maxx - data_minx
    data_range_y = data_maxy - data_miny

    def _xy_to_lonlat(xy: list[float]) -> tuple[float, float]:
        return (
            (xy[0] - data_minx) / data_range_x * range_x + target_minx,
            (xy[1] - data_miny) / data_range_y * range_y + target_miny
        )

    node_data = node_data.with_columns(
        pl.col('umap_embedding').map_elements(_xy_to_lonlat, return_dtype=pl.List(pl.Float64)).cast(pl.Array(pl.Float64, 2)).alias('coords')
    )

    # Save the dot graph: Used for rendering edges
    _export_graph(G, node_data['coords'].to_numpy(), graphs_dir / "0.graph.dot")

    # Save the names json files: Used for searching
    _export_names(node_data['name', 'coords'], names_dir)


    # Save the borders geojson file: Polygon boundaries for each cluster
    translated_polygons = {}
    for label, polygon in label_polygons.items():
        print(label)
        translated_polygons[label] = np.array([_xy_to_lonlat(pt) for pt in polygon])

    # Save the places geojson file: Point features at cluster centroids for labels
    _export_places(translated_polygons, mock_dir / "places.geojson")

    _create_borders_geojson(translated_polygons, mock_dir / "borders.geojson")

    # Save the coordinates of each individual node as PBF features
    # Used for rendering the packages (when you zoom in enough) 
    coords = {row[0].lower(): row[1] for row in node_data[['name', 'coords']].iter_rows()}
    package_names = node_data['name'].to_list()
    _generate_vector_tiles(coords, package_names, G, mock_dir / "points")

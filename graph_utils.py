"""
Utilities for building OSM road graphs and computing travel times
based on road types and speeds for fire/ARFFS vehicles.
"""

from typing import List, Tuple, Optional
import os
import hashlib
import warnings

try:
    import osmnx as ox
    import networkx as nx
except Exception as e:  # pragma: no cover
    ox = None
    nx = None

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
    QgsPointXY,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsGeometry,
    QgsPoint,
)
from shapely.geometry import box as shapely_box
from math import sqrt

from .physics_model import ARFFS_DEFAULT_SPEEDS_KMH


def find_nearest_node(graph: "nx.MultiDiGraph", lon: float, lat: float) -> Optional[int]:
    """
    Universal function for finding the nearest node in a graph.
    Works with both OSM-derived graphs and road-layer graphs.
    """
    if nx is None or len(graph.nodes()) == 0:
        return None

    min_dist = float('inf')
    nearest_node = None

    for node_id in graph.nodes():
        node_data = graph.nodes[node_id]
        node_lon = node_data.get('x')
        node_lat = node_data.get('y')

        if node_lon is None or node_lat is None:
            continue

        # Compute distance using the haversine formula
        from math import radians, cos, sin, asin

        lat1, lon1 = radians(lat), radians(lon)
        lat2, lon2 = radians(node_lat), radians(node_lon)

        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        R = 6371000  # Earth radius in metres
        dist = R * c

        if dist < min_dist:
            min_dist = dist
            nearest_node = node_id

    return nearest_node


def kmh_to_mm(kmh: float, precision: int = 2) -> float:
    """km/h -> m/min"""
    if not isinstance(kmh, (int, float)):
        raise TypeError("Argument 'kmh' must be of type int or float")
    return round(kmh * 1000 / 60, precision)


def set_graph_travel_times(
    G: "nx.MultiDiGraph",
    speeds: List[float],
    morph_function: Optional[callable] = None,
    travel_time_field: str = "travel_time",
    speed_field: str = "maxspeed",
    highway_field: str = "highway",
    length_field: str = "length",
):
    """
    Assigns speeds and travel times to graph edges based on road types.
    speeds: list of 5 speeds (see comments below), in km/h or m/min;
    if morph_function is provided, speeds will be converted (e.g. km/h -> m/min).
    """
    if nx is None:
        raise RuntimeError("OSMnx/NetworkX is unavailable. Install the 'osmnx' package.")
    if not isinstance(G, nx.MultiDiGraph):
        raise TypeError("Argument 'G' must be of type nx.MultiDiGraph")
    if not isinstance(speeds, list) or len(speeds) != 5:
        raise ValueError("Argument 'speeds' must be a list of exactly 5 elements")

    if G.graph.get("simplified", False):
        warnings.warn(
            "The graph has already been simplified. It is recommended to set speeds before simplification."
        )

    if morph_function is None:
        s1, s2, s3, s4, s5 = speeds
    else:
        s1, s2, s3, s4, s5 = [morph_function(kmh) for kmh in speeds]

    warnings.warn(
        "set_graph_travel_times() uses a simple time=distance/speed model. "
        "For ARFFS analysis, use physics_model.set_physics_travel_times() instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    # Speed map by OSM highway tags
    sp = {
        "trunk": s1,
        "trunk_link": s1,
        "motorway": s1,
        "motorway_link": s1,
        "primary": s2,
        "primary_link": s2,
        "secondary": s2,
        "secondary_link": s2,
        "unclassified": s2,
        "tertiary": s3,
        "tertiary_link": s3,
        "residential": s3,
        "living_street": s3,
        "road": s4,
        "service": s4,
        "track": s4,
        "footway": s5,
        "path": s5,
        "pedestrian": s5,
        "steps": s5,
        "cycleway": s5,
        "bridleway": s5,
        "corridor": s5,
        # ARFFS aerodrome road types
        "runway": s1,
        "taxiway": s2,
        "perimeter_road": s2,
        "apron": s4,
        "access_road": s3,
    }

    # Assign speed and travel time to each edge
    for u, v, k, data in G.edges(keys=True, data=True):
        road = data.get(highway_field, "other")
        length = data.get(length_field)
        if isinstance(road, list):
            road_speeds = [sp.get(rf, s5) for rf in road]
            speed = sum(road_speeds) / len(road_speeds)
        else:
            speed = sp.get(road, s5)

        data[speed_field] = speed
        # speed in m/min, length in metres → time in minutes
        data[travel_time_field] = (length / speed) if (speed and length) else None


def _get_cache_key(extent: QgsRectangle, buffer_m: float) -> str:
    """Generate a cache key based on extent and buffer"""
    key_str = f"{extent.xMinimum()}_{extent.yMinimum()}_{extent.xMaximum()}_{extent.yMaximum()}_{buffer_m}"
    return hashlib.sha256(key_str.encode()).hexdigest()


def _get_cache_path() -> str:
    """Return the path to the cache directory"""
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(plugin_dir, 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    try:
        os.chmod(cache_dir, 0o700)
    except OSError:
        pass  # Windows does not support chmod
    return cache_dir


def load_graph_from_cache(cache_key: str) -> Optional["nx.MultiDiGraph"]:
    """Load a graph from cache"""
    if nx is None:
        return None

    cache_dir = _get_cache_path()
    cache_file = os.path.join(cache_dir, f"graph_{cache_key}.graphml")

    if os.path.exists(cache_file):
        try:
            return ox.load_graphml(cache_file)
        except Exception:
            return None
    return None


def save_graph_to_cache(graph: "nx.MultiDiGraph", cache_key: str) -> bool:
    """Save a graph to cache"""
    if nx is None:
        return False

    cache_dir = _get_cache_path()
    cache_file = os.path.join(cache_dir, f"graph_{cache_key}.graphml")

    try:
        ox.save_graphml(graph, cache_file)
        return True
    except Exception:
        return False


def build_graph_from_road_layer(
    road_layer: QgsVectorLayer,
    objects_layer: QgsVectorLayer,
    stations_layer: QgsVectorLayer,
    buffer_m: float = 500.0,
) -> Tuple["nx.MultiDiGraph", QgsCoordinateTransform, QgsCoordinateTransform]:
    """
    Build a road graph from an existing vector road layer.
    Returns the graph and CRS transformations: to_wgs84, from_wgs84.
    """
    if nx is None:
        raise RuntimeError("NetworkX is unavailable. Install the 'networkx' package.")

    if road_layer.geometryType() != QgsWkbTypes.LineGeometry:
        raise ValueError("Road layer must be a line layer")

    # Coordinate transformations
    crs_src = objects_layer.sourceCrs() if objects_layer is not None else QgsProject.instance().crs()
    crs_wgs = QgsCoordinateReferenceSystem.fromEpsgId(4326)

    to_wgs = QgsCoordinateTransform(crs_src, crs_wgs, QgsProject.instance())
    from_wgs = QgsCoordinateTransform(crs_wgs, crs_src, QgsProject.instance())

    # Combined extent with buffer
    union_rect = QgsRectangle(objects_layer.extent())
    union_rect.combineExtentWith(stations_layer.extent())

    # Expand extent by buffer_m
    deg = buffer_m / 111000.0
    union_rect.grow(deg)

    # Create empty graph
    G = nx.MultiDiGraph()

    # Get road layer fields
    fields = road_layer.fields()
    highway_field_idx = None
    length_field_idx = None

    # Look for a highway or equivalent field
    for i, field in enumerate(fields):
        field_lower = field.name().lower()
        if field_lower in ['highway', 'road_type', 'type', 'highway_type']:
            highway_field_idx = i
        if field_lower in ['length']:
            length_field_idx = i

    # Process all lines in the road layer
    node_counter = 0
    node_coords = {}  # {(lon, lat): node_id}

    for feature in road_layer.getFeatures():
        geometry = feature.geometry()
        if geometry.isEmpty():
            continue

        # Check if the line intersects the extent
        if not geometry.boundingBox().intersects(union_rect):
            continue

        # Get road type
        highway_type = "other"
        if highway_field_idx is not None:
            highway_val = feature.attribute(highway_field_idx)
            if highway_val:
                highway_type = str(highway_val)

        # Get length
        length = geometry.length()
        if length_field_idx is not None:
            length_val = feature.attribute(length_field_idx)
            if length_val:
                length = float(length_val)

        # Convert geometry to WGS84
        geom_wgs = geometry
        if road_layer.sourceCrs() != crs_wgs:
            geom_wgs = QgsGeometry(geometry)
            geom_wgs.transform(to_wgs)

        # Get line points
        if geom_wgs.wkbType() == QgsWkbTypes.LineString:
            points = geom_wgs.asPolyline()
        elif geom_wgs.wkbType() == QgsWkbTypes.MultiLineString:
            points = []
            for line in geom_wgs.asMultiPolyline():
                points.extend(line)
        else:
            continue

        if len(points) < 2:
            continue

        # Create nodes and edges
        prev_node_id = None
        prev_point = None

        for i, point in enumerate(points):
            lon = point.x()
            lat = point.y()
            coord_key = (round(lon, 7), round(lat, 7))  # Round for node matching

            if coord_key not in node_coords:
                node_id = node_counter
                node_counter += 1
                node_coords[coord_key] = node_id
                G.add_node(node_id, x=lon, y=lat)
            else:
                node_id = node_coords[coord_key]

            if prev_node_id is not None and prev_point is not None:
                # Compute segment length in metres using the haversine formula
                from math import radians, cos, sin, asin, sqrt

                lat1, lon1 = radians(prev_point.y()), radians(prev_point.x())
                lat2, lon2 = radians(lat), radians(lon)

                dlat = lat2 - lat1
                dlon = lon2 - lon1
                a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
                c = 2 * asin(sqrt(a))
                R = 6371000  # Earth radius in metres
                segment_length = R * c

                # Store geometry coordinates for visualization and angle computation
                geom_coords = [
                    (prev_point.x(), prev_point.y()),
                    (lon, lat),
                ]

                # Add forward edge
                G.add_edge(
                    prev_node_id,
                    node_id,
                    highway=highway_type,
                    length=segment_length,
                    geometry_coords=geom_coords,
                )
                # Add reverse edge (bidirectional for ARFFS vehicles)
                G.add_edge(
                    node_id,
                    prev_node_id,
                    highway=highway_type,
                    length=segment_length,
                    geometry_coords=list(reversed(geom_coords)),
                )

            prev_node_id = node_id
            prev_point = point

    # Add graph metadata for osmnx compatibility
    G.graph['crs'] = 'epsg:4326'

    return G, to_wgs, from_wgs


def build_graph_for_layers(
    objects_layer: QgsVectorLayer,
    stations_layer: QgsVectorLayer,
    buffer_m: float = 500.0,
    road_layer: Optional[QgsVectorLayer] = None,
    use_cache: bool = True,
) -> Tuple["nx.MultiDiGraph", QgsCoordinateTransform, QgsCoordinateTransform]:
    """
    Build an OSM road graph for the combined extent of the input layers (with buffer).
    Can use an existing road layer or download from OSM.
    Supports graph caching.

    Parameters:
    - objects_layer: incident objects layer
    - stations_layer: fire stations layer
    - buffer_m: buffer around the extent in metres
    - road_layer: optional road layer (if None, OSM is used)
    - use_cache: whether to use graph caching

    Returns the graph and CRS transformations: to_wgs84, from_wgs84.
    """
    # Coordinate transformations
    crs_src = objects_layer.sourceCrs() if objects_layer is not None else QgsProject.instance().crs()
    crs_wgs = QgsCoordinateReferenceSystem.fromEpsgId(4326)

    to_wgs = QgsCoordinateTransform(crs_src, crs_wgs, QgsProject.instance())
    from_wgs = QgsCoordinateTransform(crs_wgs, crs_src, QgsProject.instance())

    # Combined extent
    union_rect = QgsRectangle(objects_layer.extent())
    union_rect.combineExtentWith(stations_layer.extent())

    # If a road layer is provided, use it
    if road_layer is not None:
        return build_graph_from_road_layer(road_layer, objects_layer, stations_layer, buffer_m)

    # Check cache
    if use_cache:
        cache_key = _get_cache_key(union_rect, buffer_m)
        cached_graph = load_graph_from_cache(cache_key)
        if cached_graph is not None:
            return cached_graph, to_wgs, from_wgs

    # Load from OSM
    if ox is None:
        raise RuntimeError("OSMnx is unavailable. Install the 'osmnx' package or provide a road layer.")

    # Expand extent by buffer_m; roughly convert metres to degrees
    # (adequate for small urban buffers; increase the buffer for larger areas)
    # 1 degree ≈ 111 km → 1 m ≈ 1/111000 degree
    deg = buffer_m / 111000.0

    # Extent corners in WGS84
    ll = to_wgs.transform(QgsPointXY(union_rect.xMinimum(), union_rect.yMinimum()))
    ur = to_wgs.transform(QgsPointXY(union_rect.xMaximum(), union_rect.yMaximum()))
    south = min(ll.y(), ur.y()) - deg
    north = max(ll.y(), ur.y()) + deg
    west = min(ll.x(), ur.x()) - deg
    east = max(ll.x(), ur.x()) + deg

    # Build graph for the drive network
    # Use polygon-based construction for compatibility across OSMnx versions
    polygon = shapely_box(west, south, east, north)
    G = ox.graph_from_polygon(polygon, network_type="drive")

    # Save to cache
    if use_cache:
        cache_key = _get_cache_key(union_rect, buffer_m)
        save_graph_to_cache(G, cache_key)

    return G, to_wgs, from_wgs


# Default speed values (km/h)
# 1 - Urban arterial / primary roads: 49
# 2 - Secondary / district roads: 37
# 3 - Local / residential streets: 26
# 4 - Service / access roads: 16
# 5 - Pedestrian paths / drivable non-roads: 5
DEFAULT_SPEEDS_KMH = [49.0, 37.0, 26.0, 16.0, 5.0]

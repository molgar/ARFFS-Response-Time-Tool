"""
Physics-based travel time model for ARFFS (Aerodrome Fire and Rescue Service) vehicles.

Implements trapezoidal velocity profiles accounting for:
- Activation time (alarm to vehicle departure)
- Acceleration from standstill or reduced speed
- Deceleration before curves and at destination
- Maximum speed limits based on road type and turn sharpness
"""

from math import sqrt, atan2, pi, radians, cos, sin, asin
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Vehicle presets
# ---------------------------------------------------------------------------

VEHICLE_PRESETS: Dict[str, Dict[str, float]] = {
    "Rosenbauer Panther 6x6": {
        "max_speed_kmh": 105.0,
        "acceleration_ms2": 2.0,
        "deceleration_ms2": 3.5,
        "activation_time_min": 1.0,
    },
    "Oshkosh Striker 3000": {
        "max_speed_kmh": 113.0,
        "acceleration_ms2": 1.8,
        "deceleration_ms2": 3.5,
        "activation_time_min": 1.0,
    },
    "Oshkosh Striker 6x6": {
        "max_speed_kmh": 113.0,
        "acceleration_ms2": 1.6,
        "deceleration_ms2": 3.0,
        "activation_time_min": 1.0,
    },
    "E-One Titan HPR": {
        "max_speed_kmh": 100.0,
        "acceleration_ms2": 1.7,
        "deceleration_ms2": 3.2,
        "activation_time_min": 1.0,
    },
}

# ---------------------------------------------------------------------------
# Default ARFFS road speeds (km/h) by road type
# ---------------------------------------------------------------------------

ARFFS_DEFAULT_SPEEDS_KMH: Dict[str, float] = {
    "runway": 80.0,
    "taxiway": 60.0,
    "perimeter_road": 70.0,
    "apron": 30.0,
    "service": 40.0,
    "access_road": 50.0,
    "other": 30.0,
}

# ---------------------------------------------------------------------------
# Curve speed mapping: deflection angle (degrees) -> speed factor
# ---------------------------------------------------------------------------

# Each entry is (max_deflection_degrees, speed_factor)
# Deflection: 0° = straight, 180° = U-turn
DEFAULT_CURVE_SPEED_TABLE: List[Tuple[float, float]] = [
    (15.0, 1.00),   # Straight
    (45.0, 0.80),   # Gentle curve
    (90.0, 0.50),   # Moderate turn
    (135.0, 0.30),  # Sharp turn
    (180.0, 0.15),  # U-turn / near-stop
]


def kmh_to_ms(speed_kmh: float) -> float:
    """Convert km/h to m/s."""
    return speed_kmh / 3.6


def ms_to_kmh(speed_ms: float) -> float:
    """Convert m/s to km/h."""
    return speed_ms * 3.6


# ---------------------------------------------------------------------------
# Trapezoidal velocity profile
# ---------------------------------------------------------------------------

def trapezoidal_travel_time(
    length_m: float,
    v_entry_ms: float,
    v_exit_ms: float,
    v_max_ms: float,
    accel_ms2: float,
    decel_ms2: float,
) -> float:
    """
    Compute travel time (seconds) for an edge using a trapezoidal velocity profile.

    The vehicle accelerates from v_entry to v_max, cruises, then decelerates
    to v_exit.  If the edge is too short to reach v_max, the vehicle
    accelerates to a peak velocity and immediately decelerates.

    Parameters
    ----------
    length_m : float
        Edge length in metres (must be > 0).
    v_entry_ms : float
        Entry speed in m/s (>= 0).
    v_exit_ms : float
        Required exit speed in m/s (>= 0).
    v_max_ms : float
        Maximum allowed speed on this edge in m/s (> 0).
    accel_ms2 : float
        Acceleration rate in m/s^2 (> 0).
    decel_ms2 : float
        Deceleration rate in m/s^2 (> 0).

    Returns
    -------
    float
        Travel time in seconds.  Returns 0.0 for zero-length edges.
    """
    if length_m <= 0:
        return 0.0

    # Clamp entry/exit to max speed
    v_entry_ms = min(v_entry_ms, v_max_ms)
    v_exit_ms = min(v_exit_ms, v_max_ms)

    # Distance to accelerate from v_entry to v_max
    d_accel = (v_max_ms ** 2 - v_entry_ms ** 2) / (2.0 * accel_ms2) if v_max_ms > v_entry_ms else 0.0
    # Distance to decelerate from v_max to v_exit
    d_decel = (v_max_ms ** 2 - v_exit_ms ** 2) / (2.0 * decel_ms2) if v_max_ms > v_exit_ms else 0.0

    if d_accel + d_decel <= length_m:
        # Case 1: vehicle reaches cruise speed
        d_cruise = length_m - d_accel - d_decel
        t_accel = (v_max_ms - v_entry_ms) / accel_ms2 if accel_ms2 > 0 else 0.0
        t_cruise = d_cruise / v_max_ms if v_max_ms > 0 else 0.0
        t_decel = (v_max_ms - v_exit_ms) / decel_ms2 if decel_ms2 > 0 else 0.0
        return t_accel + t_cruise + t_decel
    else:
        # Case 2: edge too short to reach v_max — compute peak velocity
        # v_peak^2 = (2*L*a*d + d*v_entry^2 + a*v_exit^2) / (a + d)
        numerator = (
            2.0 * length_m * accel_ms2 * decel_ms2
            + decel_ms2 * v_entry_ms ** 2
            + accel_ms2 * v_exit_ms ** 2
        )
        denominator = accel_ms2 + decel_ms2

        v_peak_sq = numerator / denominator
        if v_peak_sq < 0:
            # Degenerate case: edge too short even for deceleration only
            # Fall back to constant-speed estimate
            avg_speed = max((v_entry_ms + v_exit_ms) / 2.0, 0.1)
            return length_m / avg_speed

        v_peak = sqrt(v_peak_sq)

        # Ensure v_peak >= both entry and exit speeds
        v_peak = max(v_peak, v_entry_ms, v_exit_ms)

        t_accel = (v_peak - v_entry_ms) / accel_ms2 if v_peak > v_entry_ms and accel_ms2 > 0 else 0.0
        t_decel = (v_peak - v_exit_ms) / decel_ms2 if v_peak > v_exit_ms and decel_ms2 > 0 else 0.0

        return t_accel + t_decel


# ---------------------------------------------------------------------------
# Turn angle computation
# ---------------------------------------------------------------------------

def _compute_bearing(x1: float, y1: float, x2: float, y2: float) -> float:
    """
    Compute bearing in degrees [0, 360) from point (x1, y1) to (x2, y2).
    Uses projected (metric) coordinates.
    """
    dx = x2 - x1
    dy = y2 - y1
    angle = atan2(dx, dy) * 180.0 / pi
    return angle % 360.0


def _deflection_angle(bearing_in: float, bearing_out: float) -> float:
    """
    Compute the deflection angle between an incoming and outgoing bearing.

    Returns a value in [0, 180]:
    - 0° means straight ahead (no turn)
    - 180° means a U-turn

    The incoming bearing is reversed (+ 180°) to represent the direction of
    approach, then the absolute difference with the outgoing bearing gives
    the deflection.
    """
    # Reverse the incoming bearing to get approach direction
    approach = (bearing_in + 180.0) % 360.0
    diff = abs(bearing_out - approach)
    if diff > 180.0:
        diff = 360.0 - diff
    return diff


def _haversine_distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Haversine distance in metres between two WGS84 points."""
    lat1_r, lon1_r = radians(lat1), radians(lon1)
    lat2_r, lon2_r = radians(lat2), radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = sin(dlat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return 6371000.0 * c


def compute_turn_angles(G, use_projected: bool = True) -> None:
    """
    Compute the sharpest turn angle at each node in the graph.

    For each node, examines all pairs of (predecessor, successor) edges and
    computes the deflection angle.  Stores the sharpest (maximum) deflection
    as ``G.nodes[n]['max_deflection']`` in degrees.

    Nodes with degree <= 1 get deflection = 0 (no turn).

    Parameters
    ----------
    G : nx.MultiDiGraph
        Road network graph with node attributes 'x' (lon) and 'y' (lat).
    use_projected : bool
        If True, project WGS84 coords to a local metric approximation
        for accurate angle computation (recommended).
    """
    import networkx as nx

    # Compute approximate projected coordinates if needed
    # Use a simple equirectangular approximation centred on the graph
    if use_projected and len(G.nodes()) > 0:
        # Find centroid latitude for cosine correction
        lats = [G.nodes[n].get('y', 0) for n in G.nodes()]
        lat_center = sum(lats) / len(lats) if lats else 0
        cos_lat = cos(radians(lat_center))

        def project(lon, lat):
            """Equirectangular projection to approximate metres."""
            x_m = lon * cos_lat * 111320.0
            y_m = lat * 110540.0
            return x_m, y_m
    else:
        def project(lon, lat):
            return lon, lat

    for node in G.nodes():
        node_data = G.nodes[node]
        nx_lon = node_data.get('x')
        nx_lat = node_data.get('y')
        if nx_lon is None or nx_lat is None:
            G.nodes[node]['max_deflection'] = 0.0
            continue

        n_x, n_y = project(float(nx_lon), float(nx_lat))

        # Collect bearings of all edges connected to this node
        predecessor_bearings = []  # bearings FROM predecessor TO this node
        successor_bearings = []    # bearings FROM this node TO successor

        for pred in G.predecessors(node):
            pred_data = G.nodes[pred]
            px = pred_data.get('x')
            py = pred_data.get('y')
            if px is None or py is None:
                continue
            p_x, p_y = project(float(px), float(py))
            bearing = _compute_bearing(p_x, p_y, n_x, n_y)
            predecessor_bearings.append(bearing)

        for succ in G.successors(node):
            succ_data = G.nodes[succ]
            sx = succ_data.get('x')
            sy = succ_data.get('y')
            if sx is None or sy is None:
                continue
            s_x, s_y = project(float(sx), float(sy))
            bearing = _compute_bearing(n_x, n_y, s_x, s_y)
            successor_bearings.append(bearing)

        # Find the sharpest turn among all (incoming, outgoing) pairs
        max_deflection = 0.0
        if predecessor_bearings and successor_bearings:
            for b_in in predecessor_bearings:
                for b_out in successor_bearings:
                    defl = _deflection_angle(b_in, b_out)
                    if defl > max_deflection:
                        max_deflection = defl

        G.nodes[node]['max_deflection'] = max_deflection


# ---------------------------------------------------------------------------
# Curve speed limit
# ---------------------------------------------------------------------------

def turn_angle_to_speed_factor(
    deflection_deg: float,
    curve_table: Optional[List[Tuple[float, float]]] = None,
) -> float:
    """
    Map a deflection angle to a speed factor (0.0 - 1.0).

    Parameters
    ----------
    deflection_deg : float
        Deflection angle in degrees [0, 180].
    curve_table : list of (max_angle, factor), optional
        Custom curve speed table. Defaults to DEFAULT_CURVE_SPEED_TABLE.

    Returns
    -------
    float
        Fraction of road max speed allowed through this turn.
    """
    if curve_table is None:
        curve_table = DEFAULT_CURVE_SPEED_TABLE

    for max_angle, factor in curve_table:
        if deflection_deg <= max_angle:
            return factor

    # Beyond all thresholds — return the last (most restrictive) factor
    return curve_table[-1][1] if curve_table else 0.15


def get_node_turn_speed_ms(
    G,
    node,
    road_max_speed_ms: float,
    vehicle_max_speed_ms: float,
    curve_table: Optional[List[Tuple[float, float]]] = None,
) -> float:
    """
    Get the speed limit at a node based on its sharpest turn angle.

    Returns the minimum of road max speed, vehicle max speed, and curve-limited speed.
    """
    deflection = G.nodes[node].get('max_deflection', 0.0)
    factor = turn_angle_to_speed_factor(deflection, curve_table)
    curve_speed = road_max_speed_ms * factor
    return min(curve_speed, vehicle_max_speed_ms, road_max_speed_ms)


# ---------------------------------------------------------------------------
# Main entry point: assign physics-based travel times to graph edges
# ---------------------------------------------------------------------------

def get_road_speed_kmh(
    highway_type: str,
    road_speeds: Optional[Dict[str, float]] = None,
) -> float:
    """
    Look up the speed for a given road/highway type.

    Parameters
    ----------
    highway_type : str
        Road type string (e.g. 'taxiway', 'runway', 'service').
    road_speeds : dict, optional
        Custom speed lookup {type: km/h}. Defaults to ARFFS_DEFAULT_SPEEDS_KMH.
    """
    if road_speeds is None:
        road_speeds = ARFFS_DEFAULT_SPEEDS_KMH

    if isinstance(highway_type, list):
        speeds = [road_speeds.get(t, road_speeds.get('other', 30.0)) for t in highway_type]
        return sum(speeds) / len(speeds) if speeds else 30.0

    return road_speeds.get(highway_type, road_speeds.get('other', 30.0))


def set_physics_travel_times(
    G,
    vehicle_max_speed_kmh: float = 105.0,
    acceleration_ms2: float = 2.0,
    deceleration_ms2: float = 3.5,
    road_speeds_kmh: Optional[Dict[str, float]] = None,
    curve_table: Optional[List[Tuple[float, float]]] = None,
    highway_field: str = "highway",
    length_field: str = "length",
    travel_time_field: str = "travel_time",
    speed_field: str = "maxspeed",
) -> None:
    """
    Assign physics-based travel times to all edges in the graph.

    This replaces the simple ``time = length / speed`` model with a
    trapezoidal velocity profile that accounts for acceleration,
    deceleration, and curve-limited speeds at nodes.

    Travel times are stored in **minutes** for Dijkstra compatibility
    with the existing codebase.

    Parameters
    ----------
    G : nx.MultiDiGraph
        Road network graph.
    vehicle_max_speed_kmh : float
        Vehicle top speed in km/h.
    acceleration_ms2 : float
        Vehicle acceleration in m/s^2.
    deceleration_ms2 : float
        Vehicle deceleration in m/s^2.
    road_speeds_kmh : dict, optional
        Road type -> speed (km/h) mapping.
    curve_table : list of (angle, factor), optional
        Curve speed table.
    highway_field : str
        Edge attribute containing road type.
    length_field : str
        Edge attribute containing length in metres.
    travel_time_field : str
        Edge attribute to store computed travel time (minutes).
    speed_field : str
        Edge attribute to store assigned speed (m/min for backward compat).
    """
    # Step 1: Compute turn angles at all nodes
    compute_turn_angles(G)

    vehicle_max_ms = kmh_to_ms(vehicle_max_speed_kmh)

    # Step 2: For each edge, compute physics-based travel time
    for u, v, k, data in G.edges(keys=True, data=True):
        road_type = data.get(highway_field, "other")
        length = data.get(length_field)

        if length is None or length <= 0:
            data[travel_time_field] = 0.0
            data[speed_field] = 0.0
            continue

        length = float(length)

        # Road speed for this edge
        road_speed_kmh = get_road_speed_kmh(road_type, road_speeds_kmh)
        road_speed_ms = kmh_to_ms(road_speed_kmh)
        edge_max_ms = min(road_speed_ms, vehicle_max_ms)

        # Entry speed: limited by turn at start node
        v_entry = get_node_turn_speed_ms(G, u, road_speed_ms, vehicle_max_ms, curve_table)

        # Exit speed: limited by turn at end node
        v_exit = get_node_turn_speed_ms(G, v, road_speed_ms, vehicle_max_ms, curve_table)

        # Compute travel time via trapezoidal profile
        travel_time_s = trapezoidal_travel_time(
            length_m=length,
            v_entry_ms=v_entry,
            v_exit_ms=v_exit,
            v_max_ms=edge_max_ms,
            accel_ms2=acceleration_ms2,
            decel_ms2=deceleration_ms2,
        )

        # Store in minutes for Dijkstra compatibility
        data[travel_time_field] = travel_time_s / 60.0

        # Store speed in m/min for backward compatibility
        speed_m_min = road_speed_kmh * 1000.0 / 60.0
        data[speed_field] = speed_m_min


def set_standstill_start(
    G,
    station_node,
    acceleration_ms2: float = 2.0,
    deceleration_ms2: float = 3.5,
    highway_field: str = "highway",
    length_field: str = "length",
    travel_time_field: str = "travel_time",
    road_speeds_kmh: Optional[Dict[str, float]] = None,
    vehicle_max_speed_kmh: float = 105.0,
    curve_table: Optional[List[Tuple[float, float]]] = None,
) -> Dict:
    """
    Temporarily re-weight edges departing from a station node to model
    departure from standstill (v_entry = 0).

    Returns a dict of original edge weights that must be restored after
    Dijkstra computation using ``restore_edge_weights()``.

    Parameters
    ----------
    G : nx.MultiDiGraph
        Graph with physics travel times already computed.
    station_node : int
        Node ID of the station.

    Returns
    -------
    dict
        Mapping of (u, v, k) -> original_travel_time for restoration.
    """
    original_weights = {}
    vehicle_max_ms = kmh_to_ms(vehicle_max_speed_kmh)

    for u, v, k, data in G.out_edges(station_node, keys=True, data=True):
        original_weights[(u, v, k)] = data.get(travel_time_field, 0.0)

        length = data.get(length_field, 0.0)
        if length is None or length <= 0:
            continue

        length = float(length)
        road_type = data.get(highway_field, "other")
        road_speed_kmh = get_road_speed_kmh(road_type, road_speeds_kmh)
        road_speed_ms = kmh_to_ms(road_speed_kmh)
        edge_max_ms = min(road_speed_ms, vehicle_max_ms)

        # Exit speed: limited by turn at end node
        v_exit = get_node_turn_speed_ms(G, v, road_speed_ms, vehicle_max_ms, curve_table)

        # Recompute with v_entry = 0 (standstill)
        travel_time_s = trapezoidal_travel_time(
            length_m=length,
            v_entry_ms=0.0,
            v_exit_ms=v_exit,
            v_max_ms=edge_max_ms,
            accel_ms2=acceleration_ms2,
            decel_ms2=deceleration_ms2,
        )

        data[travel_time_field] = travel_time_s / 60.0

    return original_weights


def restore_edge_weights(
    G,
    original_weights: Dict,
    travel_time_field: str = "travel_time",
) -> None:
    """Restore original edge weights after standstill-start Dijkstra."""
    for (u, v, k), weight in original_weights.items():
        if G.has_edge(u, v, k):
            G.edges[u, v, k][travel_time_field] = weight

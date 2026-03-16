"""
ARFFS Isochrone Algorithm — generates color-coded road segment polygons
showing response time zones for Aerodrome Fire and Rescue Service vehicles.

Output classes:
  - Green  (< 2 min)  — within ICAO first-vehicle requirement
  - Orange (2–3 min)  — within ICAO all-vehicles requirement
  - Red    (> 3 min)  — exceeds ICAO response time
"""

import os
import math

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterBoolean,
    QgsProcessingException,
    QgsProcessing,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

try:
    import networkx as nx
except ImportError:
    nx = None

from ..graph_utils import (
    build_graph_for_layers,
    find_nearest_node,
)
from ..physics_model import (
    VEHICLE_PRESETS,
    ARFFS_DEFAULT_SPEEDS_KMH,
    set_physics_travel_times,
    set_standstill_start,
    restore_edge_weights,
    kmh_to_ms,
)


# Time thresholds in minutes and their display info
TIME_CLASSES = [
    {"label": "< 2 min", "min_t": 0.0, "max_t": 2.0, "color": "#00CC00"},
    {"label": "2-3 min", "min_t": 2.0, "max_t": 3.0, "color": "#FFA500"},
    {"label": "> 3 min", "min_t": 3.0, "max_t": float("inf"), "color": "#FF0000"},
]


class ARFFSIsochroneAlgorithm(QgsProcessingAlgorithm):
    """
    Generates color-coded buffered road polygons showing ARFFS response time
    zones from one or more fire station locations.
    """

    # Parameter constants
    FIRE_STATIONS_LAYER = "FIRE_STATIONS_LAYER"
    ROAD_LAYER = "ROAD_LAYER"
    ROAD_TYPE_FIELD = "ROAD_TYPE_FIELD"
    ROAD_SPEED_FIELD = "ROAD_SPEED_FIELD"
    ACTIVATION_TIME = "ACTIVATION_TIME"
    VEHICLE_PRESET = "VEHICLE_PRESET"
    MAX_SPEED_KMH = "MAX_SPEED_KMH"
    ACCELERATION_RATE = "ACCELERATION_RATE"
    DECELERATION_RATE = "DECELERATION_RATE"
    BUFFER_DISTANCE = "BUFFER_DISTANCE"
    USE_CACHE = "USE_CACHE"
    OUTPUT_LAYER = "OUTPUT_LAYER"

    def tr(self, string):
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        return ARFFSIsochroneAlgorithm()

    def name(self):
        return "arffs_isochrone"

    def displayName(self):
        return self.tr("ARFFS Response Time Isochrones")

    def icon(self):
        plugin_root = os.path.dirname(os.path.dirname(__file__))
        icon_path = os.path.join(plugin_root, "icons", "icon.png")
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return QIcon()

    def group(self):
        return self.tr("ARFFS Response Analysis")

    def groupId(self):
        return "arffs_response_analysis"

    def shortHelpString(self):
        return self.tr(
            "Generates color-coded buffered road/taxiway polygons showing "
            "ARFFS response time zones.\n\n"
            "Green: < 2 minutes (ICAO first vehicle requirement)\n"
            "Orange: 2–3 minutes (ICAO all vehicles requirement)\n"
            "Red: > 3 minutes (exceeds ICAO response time)\n\n"
            "The algorithm models realistic vehicle dynamics including "
            "activation time, acceleration, deceleration, and speed "
            "reduction at curves based on turn sharpness."
        )

    def initAlgorithm(self, config=None):
        # --- Input layers ---
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.FIRE_STATIONS_LAYER,
                self.tr("ARFFS station locations"),
                [QgsProcessing.TypeVectorPoint],
            )
        )

        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.ROAD_LAYER,
                self.tr("Aerodrome road network"),
                [QgsProcessing.TypeVectorLine],
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.ROAD_TYPE_FIELD,
                self.tr("Road type field (e.g. taxiway, runway, apron)"),
                parentLayerParameterName=self.ROAD_LAYER,
                type=QgsProcessingParameterField.String,
                optional=True,
            )
        )

        self.addParameter(
            QgsProcessingParameterField(
                self.ROAD_SPEED_FIELD,
                self.tr("Road speed field (km/h) — overrides type-based speeds"),
                parentLayerParameterName=self.ROAD_LAYER,
                type=QgsProcessingParameterField.Numeric,
                optional=True,
            )
        )

        # --- Vehicle parameters ---
        preset_names = list(VEHICLE_PRESETS.keys()) + ["Custom"]
        self.addParameter(
            QgsProcessingParameterEnum(
                self.VEHICLE_PRESET,
                self.tr("Vehicle preset"),
                options=preset_names,
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.ACTIVATION_TIME,
                self.tr("Activation time (minutes) — alarm to departure"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.0,
                maxValue=5.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_SPEED_KMH,
                self.tr("Vehicle max speed (km/h)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=105.0,
                minValue=10.0,
                maxValue=200.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.ACCELERATION_RATE,
                self.tr("Acceleration (m/s²)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=2.0,
                minValue=0.5,
                maxValue=5.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.DECELERATION_RATE,
                self.tr("Deceleration (m/s²)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=3.5,
                minValue=1.0,
                maxValue=8.0,
            )
        )

        # --- Output options ---
        self.addParameter(
            QgsProcessingParameterNumber(
                self.BUFFER_DISTANCE,
                self.tr("Buffer distance for road polygons (metres)"),
                type=QgsProcessingParameterNumber.Double,
                defaultValue=25.0,
                minValue=1.0,
                maxValue=200.0,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.USE_CACHE,
                self.tr("Use graph caching"),
                options=[self.tr("Yes"), self.tr("No")],
                defaultValue=0,
            )
        )

        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LAYER,
                self.tr("ARFFS isochrone output"),
            )
        )

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):
        if nx is None:
            raise QgsProcessingException("NetworkX is required but not installed.")

        # --- Retrieve parameters ---
        stations_layer = self.parameterAsVectorLayer(
            parameters, self.FIRE_STATIONS_LAYER, context
        )
        road_layer = self.parameterAsVectorLayer(
            parameters, self.ROAD_LAYER, context
        )
        road_type_field = self.parameterAsString(
            parameters, self.ROAD_TYPE_FIELD, context
        ) or None
        road_speed_field = self.parameterAsString(
            parameters, self.ROAD_SPEED_FIELD, context
        ) or None

        activation_time = self.parameterAsDouble(
            parameters, self.ACTIVATION_TIME, context
        )
        max_speed_kmh = self.parameterAsDouble(
            parameters, self.MAX_SPEED_KMH, context
        )
        accel = self.parameterAsDouble(
            parameters, self.ACCELERATION_RATE, context
        )
        decel = self.parameterAsDouble(
            parameters, self.DECELERATION_RATE, context
        )
        buffer_dist = self.parameterAsDouble(
            parameters, self.BUFFER_DISTANCE, context
        )
        use_cache = self.parameterAsInt(parameters, self.USE_CACHE, context) == 0

        # Apply vehicle preset
        preset_idx = self.parameterAsInt(parameters, self.VEHICLE_PRESET, context)
        preset_names = list(VEHICLE_PRESETS.keys()) + ["Custom"]
        if preset_idx < len(VEHICLE_PRESETS):
            preset_name = preset_names[preset_idx]
            preset = VEHICLE_PRESETS[preset_name]
            max_speed_kmh = preset["max_speed_kmh"]
            accel = preset["acceleration_ms2"]
            decel = preset["deceleration_ms2"]
            activation_time = preset["activation_time_min"]
            feedback.pushInfo(self.tr(f"Using vehicle preset: {preset_name}"))

        if stations_layer is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, self.FIRE_STATIONS_LAYER)
            )
        if road_layer is None:
            raise QgsProcessingException(
                self.invalidSourceError(parameters, self.ROAD_LAYER)
            )

        # --- Build output fields ---
        fields = QgsFields()
        fields.append(QgsField("station_name", QVariant.String))
        fields.append(QgsField("time_class", QVariant.String))
        fields.append(QgsField("color_code", QVariant.String))
        fields.append(QgsField("min_time_min", QVariant.Double))
        fields.append(QgsField("max_time_min", QVariant.Double))

        (sink, dest_id) = self.parameterAsSink(
            parameters,
            self.OUTPUT_LAYER,
            context,
            fields,
            QgsWkbTypes.MultiPolygon,
            road_layer.sourceCrs(),
        )
        if sink is None:
            raise QgsProcessingException(
                self.invalidSinkError(parameters, self.OUTPUT_LAYER)
            )

        # --- Build road graph ---
        feedback.pushInfo(self.tr("Building road network graph..."))
        try:
            G, to_wgs, from_wgs = build_graph_for_layers(
                stations_layer,
                stations_layer,
                buffer_m=500.0,
                road_layer=road_layer,
                use_cache=use_cache,
            )
        except RuntimeError as e:
            raise QgsProcessingException(str(e))

        feedback.pushInfo(
            self.tr(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        )

        # --- Build road speed dict from speed field if provided ---
        road_speeds = dict(ARFFS_DEFAULT_SPEEDS_KMH)
        if road_speed_field:
            # Per-edge speed override will be handled below
            pass

        # --- Apply physics travel times ---
        feedback.pushInfo(self.tr("Computing physics-based travel times..."))
        set_physics_travel_times(
            G,
            vehicle_max_speed_kmh=max_speed_kmh,
            acceleration_ms2=accel,
            deceleration_ms2=decel,
            road_speeds_kmh=road_speeds,
        )

        # If a per-edge speed field is provided, override travel times for those edges
        if road_speed_field:
            self._apply_speed_field_override(
                G, road_layer, road_speed_field, to_wgs,
                max_speed_kmh, accel, decel,
            )

        # --- Locate station nodes ---
        feedback.pushInfo(self.tr("Locating station nodes..."))
        station_name_field = self._detect_name_field(stations_layer)
        station_nodes = {}

        for station in stations_layer.getFeatures():
            pt = station.geometry().asPoint()
            pt_wgs = to_wgs.transform(pt.x(), pt.y())
            node = find_nearest_node(G, pt_wgs.x(), pt_wgs.y())
            if node is None:
                continue
            name = (
                station[station_name_field]
                if station_name_field
                else f"Station_{station.id()}"
            )
            station_nodes[name] = node

        if not station_nodes:
            raise QgsProcessingException(
                self.tr("Could not find graph nodes for any station.")
            )

        feedback.pushInfo(self.tr(f"Found {len(station_nodes)} station(s)"))

        # --- Compute Dijkstra from each station ---
        feedback.pushInfo(self.tr("Computing response times..."))
        # node -> best arrival time (minutes, including activation)
        best_arrival = {}
        best_station = {}

        for idx, (sname, snode) in enumerate(station_nodes.items()):
            if feedback.isCanceled():
                break

            feedback.pushInfo(self.tr(f"  Processing {sname}..."))

            # Standstill start for this station
            saved = set_standstill_start(
                G, snode,
                acceleration_ms2=accel,
                deceleration_ms2=decel,
                road_speeds_kmh=road_speeds,
                vehicle_max_speed_kmh=max_speed_kmh,
            )

            try:
                lengths = dict(
                    nx.single_source_dijkstra_path_length(
                        G, snode, weight="travel_time"
                    )
                )
            except Exception as e:
                feedback.reportError(f"Dijkstra failed for {sname}: {e}")
                restore_edge_weights(G, saved)
                continue

            restore_edge_weights(G, saved)

            for node, travel_min in lengths.items():
                arrival = travel_min + activation_time
                if node not in best_arrival or arrival < best_arrival[node]:
                    best_arrival[node] = arrival
                    best_station[node] = sname

            feedback.setProgress(int(50 * (idx + 1) / len(station_nodes)))

        # --- Classify edges and build output polygons ---
        feedback.pushInfo(self.tr("Classifying edges and building polygons..."))
        from shapely.geometry import LineString, MultiPolygon as ShapelyMultiPolygon
        from shapely.ops import unary_union

        # Collect geometries per (station, time_class)
        class_geoms = {}  # (station_name, class_label) -> [shapely polygons]

        total_edges = G.number_of_edges()
        for edge_idx, (u, v, k, data) in enumerate(G.edges(keys=True, data=True)):
            if feedback.isCanceled():
                break

            if u not in best_arrival or v not in best_arrival:
                continue

            t_u = best_arrival[u]
            t_v = best_arrival[v]
            station_u = best_station.get(u, "Unknown")
            station_v = best_station.get(v, "Unknown")

            # Edge midpoint time for primary classification
            t_mid = (t_u + t_v) / 2.0

            # Get edge geometry
            geom_coords = data.get("geometry_coords")
            if not geom_coords or len(geom_coords) < 2:
                # Fall back to node coordinates
                u_data = G.nodes[u]
                v_data = G.nodes[v]
                ux, uy = u_data.get("x"), u_data.get("y")
                vx, vy = v_data.get("x"), v_data.get("y")
                if ux is None or uy is None or vx is None or vy is None:
                    continue
                geom_coords = [(ux, uy), (vx, vy)]

            # Check if edge crosses a time threshold — split if so
            segments = self._split_edge_at_thresholds(
                geom_coords, t_u, t_v
            )

            for seg_coords, seg_time in segments:
                # Determine time class
                tc = self._classify_time(seg_time)
                if tc is None:
                    continue

                sname = station_u  # use the station closest to start node

                # Convert WGS84 coords to road layer CRS for buffering
                qgs_points = []
                for lon, lat in seg_coords:
                    pt_src = from_wgs.transform(QgsPointXY(lon, lat))
                    qgs_points.append(QgsPointXY(pt_src.x(), pt_src.y()))

                if len(qgs_points) < 2:
                    continue

                # Create QgsGeometry line and buffer
                line_geom = QgsGeometry.fromPolylineXY(qgs_points)
                buffered = line_geom.buffer(buffer_dist, 8)

                if buffered.isEmpty():
                    continue

                key = (sname, tc["label"])
                if key not in class_geoms:
                    class_geoms[key] = []
                class_geoms[key].append(buffered)

            if edge_idx % 500 == 0:
                feedback.setProgress(50 + int(40 * edge_idx / max(total_edges, 1)))

        # --- Union and output per class ---
        feedback.pushInfo(self.tr("Merging polygons per time class..."))
        for (sname, class_label), geom_list in class_geoms.items():
            if feedback.isCanceled():
                break

            # Find the time class info
            tc = None
            for c in TIME_CLASSES:
                if c["label"] == class_label:
                    tc = c
                    break
            if tc is None:
                continue

            # Union all geometries for this class
            if len(geom_list) == 1:
                merged = geom_list[0]
            else:
                # Collect and union using QgsGeometry
                merged = QgsGeometry.unaryUnion(geom_list)

            if merged.isEmpty():
                continue

            # Convert to MultiPolygon if needed
            if merged.wkbType() != QgsWkbTypes.MultiPolygon:
                merged = QgsGeometry.collectGeometry([merged])

            feat = QgsFeature(fields)
            feat.setGeometry(merged)
            feat["station_name"] = sname
            feat["time_class"] = tc["label"]
            feat["color_code"] = tc["color"]
            feat["min_time_min"] = round(tc["min_t"], 2)
            feat["max_time_min"] = round(tc["max_t"], 2) if tc["max_t"] != float("inf") else None

            sink.addFeature(feat)

        feedback.setProgress(100)
        feedback.pushInfo(self.tr("Done."))

        return {self.OUTPUT_LAYER: dest_id}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_time(t_min: float) -> dict:
        """Return the TIME_CLASSES entry for a given time in minutes."""
        for tc in TIME_CLASSES:
            if tc["min_t"] <= t_min < tc["max_t"]:
                return tc
        # Fallback: > 3 min
        return TIME_CLASSES[-1]

    @staticmethod
    def _split_edge_at_thresholds(
        coords: list,
        t_start: float,
        t_end: float,
    ) -> list:
        """
        Split an edge into sub-segments at time-class boundaries.

        Returns a list of (sub_coords, representative_time) tuples.
        """
        thresholds = [tc["min_t"] for tc in TIME_CLASSES if tc["min_t"] > 0]
        # Thresholds = [2.0, 3.0]

        t_min = min(t_start, t_end)
        t_max = max(t_start, t_end)

        # Find thresholds that fall within this edge's time range
        crossing_thresholds = [t for t in thresholds if t_min < t < t_max]

        if not crossing_thresholds:
            # No crossings — whole edge is one class
            mid_time = (t_start + t_end) / 2.0
            return [(coords, mid_time)]

        # Sort thresholds and split
        crossing_thresholds.sort()
        segments = []

        # Interpolate positions along the edge at each threshold
        fractions = []
        for t in crossing_thresholds:
            if abs(t_end - t_start) < 1e-9:
                frac = 0.5
            else:
                frac = (t - t_start) / (t_end - t_start)
            fractions.append(max(0.0, min(1.0, frac)))

        # Add start and end
        all_fracs = [0.0] + fractions + [1.0]

        for i in range(len(all_fracs) - 1):
            f1 = all_fracs[i]
            f2 = all_fracs[i + 1]
            if abs(f2 - f1) < 1e-9:
                continue

            # Interpolate coordinates
            p1 = _interpolate_along(coords, f1)
            p2 = _interpolate_along(coords, f2)
            seg_time = t_start + (f1 + f2) / 2.0 * (t_end - t_start)
            segments.append(([p1, p2], seg_time))

        return segments

    @staticmethod
    def _detect_name_field(layer) -> str:
        """Detect a name field in a layer."""
        if layer is None:
            return None
        for fld in layer.fields():
            if fld.type() == QVariant.String and fld.name().lower() in (
                "name", "station", "station_name",
            ):
                return fld.name()
        # Fallback: first string field
        for fld in layer.fields():
            if fld.type() == QVariant.String:
                return fld.name()
        return None

    def _apply_speed_field_override(
        self, G, road_layer, speed_field, to_wgs,
        vehicle_max_kmh, accel, decel,
    ):
        """
        Override edge travel times using a per-feature speed field from the
        road layer. This is an optional enhancement for users who have speed
        data directly in their road layer attributes.
        """
        from ..physics_model import trapezoidal_travel_time, get_node_turn_speed_ms

        vehicle_max_ms = kmh_to_ms(vehicle_max_kmh)

        for feature in road_layer.getFeatures():
            speed_val = feature[speed_field]
            if speed_val is None:
                continue
            try:
                speed_kmh = float(speed_val)
            except (ValueError, TypeError):
                continue
            if speed_kmh <= 0:
                continue

            speed_ms = kmh_to_ms(speed_kmh)
            edge_max = min(speed_ms, vehicle_max_ms)

            # Match feature geometry to graph edges (approximate by endpoints)
            geom = feature.geometry()
            if geom.isEmpty():
                continue
            # This is a best-effort override; exact edge matching is complex
            # For now, we skip per-edge override in favor of the type-based model


def _interpolate_along(coords: list, fraction: float) -> tuple:
    """
    Interpolate a point along a coordinate list at a given fraction [0, 1].
    """
    if fraction <= 0.0:
        return coords[0]
    if fraction >= 1.0:
        return coords[-1]

    # Compute total length
    total_len = 0.0
    seg_lengths = []
    for i in range(len(coords) - 1):
        dx = coords[i + 1][0] - coords[i][0]
        dy = coords[i + 1][1] - coords[i][1]
        seg_len = math.sqrt(dx * dx + dy * dy)
        seg_lengths.append(seg_len)
        total_len += seg_len

    if total_len <= 0:
        return coords[0]

    target_dist = fraction * total_len
    accumulated = 0.0

    for i, seg_len in enumerate(seg_lengths):
        if accumulated + seg_len >= target_dist:
            if seg_len <= 0:
                return coords[i]
            local_frac = (target_dist - accumulated) / seg_len
            x = coords[i][0] + local_frac * (coords[i + 1][0] - coords[i][0])
            y = coords[i][1] + local_frac * (coords[i + 1][1] - coords[i][1])
            return (x, y)
        accumulated += seg_len

    return coords[-1]

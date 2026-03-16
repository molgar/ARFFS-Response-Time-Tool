"""
Algorithm for creating a vector layer of response time routes
"""

from qgis.core import (QgsProcessingAlgorithm, QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterField, QgsProcessingParameterNumber,
                       QgsProcessingParameterFeatureSink, QgsProcessingParameterEnum,
                       QgsFeature, QgsGeometry, QgsPointXY,
                       QgsDistanceArea, QgsProject, QgsUnitTypes, QgsProcessingException,
                       QgsField, QgsFields, QgsWkbTypes, QgsProcessing)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon
import math
import importlib
import os

from ..graph_utils import (
    build_graph_for_layers,
    set_graph_travel_times,
    kmh_to_mm,
    DEFAULT_SPEEDS_KMH,
    find_nearest_node,
)


class ResponseTimeRoutesAlgorithm(QgsProcessingAlgorithm):
    """
    Algorithm for creating a vector layer of response time routes
    between incident objects and fire stations
    """

    # Parameter constants
    OBJECTS_LAYER = 'OBJECTS_LAYER'
    FIRE_STATIONS_LAYER = 'FIRE_STATIONS_LAYER'
    ROAD_LAYER = 'ROAD_LAYER'
    ROAD_SPEEDS_KMH = 'ROAD_SPEEDS_KMH'
    USE_CACHE = 'USE_CACHE'
    ROUTE_TYPE = 'ROUTE_TYPE'
    TIME_THRESHOLD = 'TIME_THRESHOLD'
    OUTPUT_LAYER = 'OUTPUT_LAYER'

    def tr(self, string):
        """Translate string"""
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        """Create algorithm instance"""
        return ResponseTimeRoutesAlgorithm()

    def name(self):
        """Algorithm name"""
        return 'response_time_routes'

    def displayName(self):
        """Algorithm display name"""
        return self.tr('Response Time Routes')

    def icon(self):
        """Algorithm icon for the Processing toolbar"""
        plugin_root = os.path.dirname(os.path.dirname(__file__))
        return QIcon(os.path.join(plugin_root, 'icons', 'response_time_routes_algorithm_icon.png'))

    def group(self):
        """Algorithm group"""
        return self.tr('Fire Response Analysis')

    def groupId(self):
        """Algorithm group ID"""
        return 'fire_response_analysis'

    def shortHelpString(self):
        """Short help text"""
        return self.tr(
            "This algorithm creates a vector layer of response time routes "
            "between incident objects and fire stations."
        )

    def initAlgorithm(self, config=None):
        """Initialize algorithm parameters"""

        # Input objects layer
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.OBJECTS_LAYER,
                self.tr('Objects layer'),
                [QgsProcessing.TypeVectorPoint, QgsProcessing.TypeVectorPolygon]
            )
        )

        # Input fire stations layer
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.FIRE_STATIONS_LAYER,
                self.tr('Fire stations layer'),
                [QgsProcessing.TypeVectorPoint]
            )
        )

        # Optional road network layer (if not provided, OSM will be used)
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.ROAD_LAYER,
                self.tr('Road network layer (optional)'),
                [QgsProcessing.TypeVectorLine],
                optional=True
            )
        )

        # Station name field is detected automatically during processing

        # Average travel speed (km/h)
        # Road-type speeds are passed from the dialog as a list of 5 values (km/h)

        # Use graph cache
        self.addParameter(
            QgsProcessingParameterEnum(
                self.USE_CACHE,
                self.tr('Use graph caching'),
                options=[self.tr('Yes'), self.tr('No')],
                defaultValue=0
            )
        )

        # Route type
        self.addParameter(
            QgsProcessingParameterEnum(
                self.ROUTE_TYPE,
                self.tr('Route type'),
                options=[self.tr('To nearest station only'),
                        self.tr('To all stations'),
                        self.tr('To all stations within time threshold')],
                defaultValue=0
            )
        )

        # Time threshold for filtering (minutes)
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TIME_THRESHOLD,
                self.tr('Response time threshold (minutes)'),
                type=QgsProcessingParameterNumber.Double,
                minValue=1.0,
                maxValue=300.0,
                defaultValue=30.0,
                optional=True
            )
        )

        # Output layer
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LAYER,
                self.tr('Output routes layer')
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        """Main processing logic"""

        # Retrieve parameters
        objects_layer = self.parameterAsVectorLayer(parameters, self.OBJECTS_LAYER, context)
        fire_stations_layer = self.parameterAsVectorLayer(parameters, self.FIRE_STATIONS_LAYER, context)
        road_layer = self.parameterAsVectorLayer(parameters, self.ROAD_LAYER, context)
        speeds_kmh = parameters.get(self.ROAD_SPEEDS_KMH, DEFAULT_SPEEDS_KMH)
        if not isinstance(speeds_kmh, list) or len(speeds_kmh) != 5:
            speeds_kmh = DEFAULT_SPEEDS_KMH
        elif not all(isinstance(s, (int, float)) and 0 < s <= 300 for s in speeds_kmh):
            speeds_kmh = DEFAULT_SPEEDS_KMH
        route_type = self.parameterAsInt(parameters, self.ROUTE_TYPE, context)
        time_threshold = self.parameterAsDouble(parameters, self.TIME_THRESHOLD, context)

        if objects_layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.OBJECTS_LAYER))

        if fire_stations_layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.FIRE_STATIONS_LAYER))

        # Create output layer fields
        fields = QgsFields()
        fields.append(QgsField('object_id', QVariant.Int))
        fields.append(QgsField('station_name', QVariant.String))
        fields.append(QgsField('distance_km', QVariant.Double))
        fields.append(QgsField('response_time_min', QVariant.Double))
        fields.append(QgsField('object_type', QVariant.String))
        fields.append(QgsField('route_type', QVariant.String))

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT_LAYER, context,
            fields, QgsWkbTypes.LineString, objects_layer.sourceCrs()
        )

        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT_LAYER))

        # Retrieve cache parameter
        use_cache = self.parameterAsInt(parameters, self.USE_CACHE, context) == 0

        # Build road graph
        if road_layer is not None:
            feedback.pushInfo(self.tr('Building graph from road layer...'))
        else:
            # Check for osmnx only if no road layer is provided
            try:
                importlib.import_module('osmnx')
            except Exception as e:
                # Show installation dialog
                import sys
                import os
                plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                if plugin_dir not in sys.path:
                    sys.path.insert(0, plugin_dir)

                try:
                    from osmnx_checker import check_osmnx_available

                    if not check_osmnx_available():
                        # Show message directing the user to the plugin menu
                        from qgis.PyQt.QtWidgets import QMessageBox

                        msg = QMessageBox()
                        msg.setWindowTitle(self.tr("OSMnx library not installed"))
                        msg.setIcon(QMessageBox.Warning)
                        msg.setText(
                            self.tr("The OSMnx library required by this algorithm is not installed.\n\n")
                            + self.tr("To install the library:\n")
                            + self.tr("1. Go to: Plugins → Fire Analysis → Install Libraries (OSMnx)\n")
                            + self.tr("2. Follow the instructions in the dialog\n\n")
                            + self.tr("Alternatively, specify a road network layer in the algorithm parameters.")
                        )
                        msg.setStandardButtons(QMessageBox.Ok)

                        try:
                            from qgis.utils import iface
                            if iface:
                                msg.setParent(iface.mainWindow())
                        except Exception:
                            pass

                        msg.exec_()

                        if not check_osmnx_available():
                            raise QgsProcessingException(
                                self.tr("OSMnx is unavailable and no road layer was provided. "
                                       "Install osmnx via Plugins → Fire Analysis → Install Libraries (OSMnx) "
                                       "or specify a road network layer.")
                            )
                except ImportError:
                    raise QgsProcessingException(
                        self.tr("OSMnx is unavailable and no road layer was provided. "
                               "Install osmnx (pip install osmnx) or specify a road network layer.")
                    )
            feedback.pushInfo(self.tr('Building OSM road graph...'))

        try:
            G, to_wgs, from_wgs = build_graph_for_layers(
                objects_layer,
                fire_stations_layer,
                buffer_m=500.0,
                road_layer=road_layer,
                use_cache=use_cache
            )
        except RuntimeError as e:
            raise QgsProcessingException(self.tr(str(e)))

        set_graph_travel_times(G, speeds_kmh, kmh_to_mm)

        # Prepare station data
        station_name_field = self._detect_station_name_field(fire_stations_layer)
        fire_stations = list(fire_stations_layer.getFeatures())

        # Process each incident object
        total_features = objects_layer.featureCount()
        feedback.pushInfo(self.tr(f'Processing {total_features} features...'))

        import networkx as nx

        def sum_route_time_and_length(graph, route_nodes):
            total_time = 0.0
            total_len = 0.0
            for u, v in zip(route_nodes[:-1], route_nodes[1:]):
                data = graph.get_edge_data(u, v)
                if not data:
                    continue
                best_edge = None
                best_time = float('inf')
                for _, ed in data.items():
                    t = ed.get('travel_time')
                    if t is None:
                        continue
                    if t < best_time:
                        best_time = t
                        best_edge = ed
                if best_edge is None:
                    ed = next(iter(data.values()))
                    t = ed.get('travel_time') or 0.0
                    l = ed.get('length') or 0.0
                else:
                    t = best_time
                    l = best_edge.get('length') or 0.0
                total_time += t
                total_len += l
            return total_time, total_len

        for i, obj_feature in enumerate(objects_layer.getFeatures()):
            if feedback.isCanceled():
                break

            # Get feature geometry
            obj_geometry = obj_feature.geometry()
            if obj_geometry.isEmpty():
                continue

            # Determine feature centroid
            if obj_geometry.type() == QgsWkbTypes.PointGeometry:
                obj_point = obj_geometry.asPoint()
            else:
                obj_point = obj_geometry.centroid().asPoint()

            obj_id = obj_feature.id()
            obj_type = QgsWkbTypes.displayString(obj_geometry.wkbType())

            # Find graph node for the incident object
            obj_wgs = to_wgs.transform(obj_point.x(), obj_point.y())
            try:
                obj_node = find_nearest_node(G, obj_wgs.x(), obj_wgs.y())
                if obj_node is None:
                    continue
            except Exception:
                continue

            # Function to compute route and travel time
            def compute_time_and_route(station_feature):
                st_pt = station_feature.geometry().asPoint()
                st_wgs = to_wgs.transform(st_pt.x(), st_pt.y())
                try:
                    st_node = find_nearest_node(G, st_wgs.x(), st_wgs.y())
                    if st_node is None:
                        return None, float('inf'), float('inf')
                    route_nodes = nx.shortest_path(G, obj_node, st_node, weight='travel_time')
                    t_min, total_len = sum_route_time_and_length(G, route_nodes)
                    return route_nodes, t_min, total_len
                except Exception:
                    return None, float('inf'), float('inf')

            routes_to_write = []  # (route_nodes, t_min, total_len, station)

            if route_type == 0:  # Nearest station only
                best = (None, float('inf'), float('inf'), None)
                for st in fire_stations:
                    route_nodes, t_min, total_len = compute_time_and_route(st)
                    if t_min < best[1]:
                        best = (route_nodes, t_min, total_len, st)
                if best[0] is not None:
                    routes_to_write.append(best)
            elif route_type == 1:  # All stations
                for st in fire_stations:
                    route_nodes, t_min, total_len = compute_time_and_route(st)
                    if route_nodes is not None:
                        routes_to_write.append((route_nodes, t_min, total_len, st))
            else:  # Within time threshold
                for st in fire_stations:
                    route_nodes, t_min, total_len = compute_time_and_route(st)
                    if route_nodes is not None and t_min <= time_threshold:
                        routes_to_write.append((route_nodes, t_min, total_len, st))

            # Write routes
            for route_nodes, t_min, total_len, station in routes_to_write:
                try:
                    st_name = station[station_name_field] if station_name_field else f"Station_{station.id()}"
                except Exception:
                    st_name = f"Station_{station.id()}"

                # Route geometry from graph nodes → project CRS
                path_pts = []
                for n in route_nodes:
                    lon = G.nodes[n].get('x')
                    lat = G.nodes[n].get('y')
                    pt_src = from_wgs.transform(lon, lat)
                    path_pts.append(QgsPointXY(pt_src))
                if len(path_pts) < 2:
                    continue
                line_geometry = QgsGeometry.fromPolylineXY(path_pts)

                route_feature = QgsFeature(fields)
                route_feature.setGeometry(line_geometry)
                route_feature['object_id'] = obj_id
                route_feature['station_name'] = st_name
                route_feature['distance_km'] = round(total_len / 1000.0, 2) if total_len != float('inf') else None
                route_feature['response_time_min'] = round(t_min, 2) if t_min != float('inf') else None
                route_feature['object_type'] = obj_type
                route_feature['route_type'] = ['nearest', 'all', 'within_threshold'][route_type]

                sink.addFeature(route_feature)

            # Update progress
            feedback.setProgress(int(i / total_features * 100))

        return {self.OUTPUT_LAYER: dest_id}

    def _detect_station_name_field(self, layer):
        """Detect the appropriate string field for the station name"""
        if layer is None:
            return None
        string_fields = []
        for fld in layer.fields():
            if fld.type() == QVariant.String:
                string_fields.append(fld.name())
        preferred = ['name', 'station', 'station_name']
        lower_map = {f.lower(): f for f in string_fields}
        for key in preferred:
            if key.lower() in lower_map:
                return lower_map[key.lower()]
        return string_fields[0] if string_fields else None

    def find_nearest_station(self, point, stations, distance_calc):
        """Find the nearest fire station"""
        min_distance = float('inf')
        nearest_station = None

        for station in stations:
            station_point = station.geometry().asPoint()
            distance = distance_calc.measureLine(point, station_point)

            if distance < min_distance:
                min_distance = distance
                nearest_station = station

        return nearest_station

    def tr(self, string):
        """Translate string"""
        return QCoreApplication.translate('Processing', string)

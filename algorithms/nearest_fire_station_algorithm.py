"""
Algorithm for identifying the nearest fire station
"""

from qgis.core import (QgsProcessingAlgorithm, QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterField, QgsProcessingParameterNumber,
                       QgsProcessingParameterFeatureSink, QgsProcessingParameterEnum,
                       QgsFeature, QgsGeometry, QgsPointXY, QgsSpatialIndex,
                       QgsDistanceArea, QgsProject, QgsUnitTypes, QgsProcessingException,
                       QgsField, QgsFields, QgsWkbTypes, QgsRectangle, QgsProcessing)
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon
from ..graph_utils import (
    build_graph_for_layers,
    set_graph_travel_times,
    kmh_to_mm,
    DEFAULT_SPEEDS_KMH,
    find_nearest_node,
)
import os
import importlib
import math


class NearestFireStationAlgorithm(QgsProcessingAlgorithm):
    """
    Algorithm for identifying the nearest fire station
    and calculating response time for each incident object
    """

    # Parameter constants
    OBJECTS_LAYER = 'OBJECTS_LAYER'
    FIRE_STATIONS_LAYER = 'FIRE_STATIONS_LAYER'
    ROAD_LAYER = 'ROAD_LAYER'
    RESPONSE_TIME_FIELD = 'RESPONSE_TIME_FIELD'
    ROAD_SPEEDS_KMH = 'ROAD_SPEEDS_KMH'
    USE_CACHE = 'USE_CACHE'
    OUTPUT_LAYER = 'OUTPUT_LAYER'

    def tr(self, string):
        """Translate string"""
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        """Create algorithm instance"""
        return NearestFireStationAlgorithm()

    def name(self):
        """Algorithm name"""
        return 'nearest_fire_station'

    def displayName(self):
        """Algorithm display name"""
        return self.tr('Nearest Fire Station')

    def icon(self):
        """Algorithm icon for the Processing toolbar"""
        # Icons are located in the plugin root alongside icon.png
        plugin_root = os.path.dirname(os.path.dirname(__file__))
        return QIcon(os.path.join(plugin_root, 'icons', 'nearest_fire_station_algorithm_icon.png'))

    def group(self):
        """Algorithm group"""
        return self.tr('Fire Response Analysis')

    def groupId(self):
        """Algorithm group ID"""
        return 'fire_response_analysis'

    def shortHelpString(self):
        """Short help text"""
        return self.tr(
            "This algorithm identifies the nearest fire station "
            "for each incident object and calculates the response time."
        )

    def initAlgorithm(self, config=None):
        """Initialize algorithm parameters"""

        # Input objects layer (buildings, point features)
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

        # Output layer (must be last)
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LAYER,
                self.tr('Output layer with response times')
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

        if objects_layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.OBJECTS_LAYER))

        if fire_stations_layer is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.FIRE_STATIONS_LAYER))

        # Create output layer
        fields = objects_layer.fields()
        fields.append(QgsField('nearest_station', QVariant.String))
        fields.append(QgsField('distance_km', QVariant.Double))
        fields.append(QgsField('response_time_min', QVariant.Double))
        fields.append(QgsField('station_x', QVariant.Double))
        fields.append(QgsField('station_y', QVariant.Double))

        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT_LAYER, context,
            fields, objects_layer.wkbType(), objects_layer.sourceCrs()
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
                        from qgis.PyQt.QtWidgets import QMessageBox, QApplication

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

                        # Try to find the main window to parent the message
                        try:
                            from qgis.utils import iface
                            if iface:
                                msg.setParent(iface.mainWindow())
                        except Exception:
                            pass

                        msg.exec_()

                        # Check again after the message
                        if not check_osmnx_available():
                            raise QgsProcessingException(
                                self.tr("OSMnx is unavailable and no road layer was provided. "
                                       "Install osmnx via Plugins → Fire Analysis → Install Libraries (OSMnx) "
                                       "or specify a road network layer.")
                            )
                except ImportError:
                    # If the checker could not be imported, show a standard message
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

        # Process each incident object
        total_features = objects_layer.featureCount()
        feedback.pushInfo(self.tr(f'Processing {total_features} features...'))

        # Helper function to sum travel time and length along a node route
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

            # Find nearest station by shortest road travel time
            nearest_station_id = None
            nearest_station_feature = None
            best_time_min = float('inf')
            best_distance_km = float('inf')

            # Graph node for the incident object
            obj_wgs = to_wgs.transform(obj_point.x(), obj_point.y())
            try:
                obj_node = find_nearest_node(G, obj_wgs.x(), obj_wgs.y())
                if obj_node is None:
                    continue
            except Exception:
                continue

            for station_feature in fire_stations_layer.getFeatures():
                station_point = station_feature.geometry().asPoint()
                st_wgs = to_wgs.transform(station_point.x(), station_point.y())
                try:
                    st_node = find_nearest_node(G, st_wgs.x(), st_wgs.y())
                    if st_node is None:
                        continue
                except Exception:
                    continue

                # Shortest path by travel time
                import networkx as nx
                try:
                    route = nx.shortest_path(G, obj_node, st_node, weight='travel_time')
                except Exception:
                    continue

                # Total time and length
                total_time_min, total_len_m = sum_route_time_and_length(G, route)
                total_dist_km = total_len_m / 1000.0 if total_len_m != float('inf') else float('inf')

                if total_time_min < best_time_min:
                    best_time_min = total_time_min
                    best_distance_km = total_dist_km
                    nearest_station_id = station_feature.id()
                    nearest_station_feature = station_feature

            # Calculate response time
            if nearest_station_feature is not None:
                station_name_field = self._detect_station_name_field(fire_stations_layer)
                station_name = nearest_station_feature[station_name_field] if station_name_field else f"Station_{nearest_station_id}"
                response_time_min = round(best_time_min, 2)
                station_point = nearest_station_feature.geometry().asPoint()

                # Create new feature
                new_feature = QgsFeature(fields)
                new_feature.setGeometry(obj_geometry)

                # Copy attributes from source object
                for field in objects_layer.fields():
                    new_feature[field.name()] = obj_feature[field.name()]

                # Add new attributes
                new_feature['nearest_station'] = station_name
                new_feature['distance_km'] = round(best_distance_km, 2)
                new_feature['response_time_min'] = response_time_min
                new_feature['station_x'] = round(station_point.x(), 6)
                new_feature['station_y'] = round(station_point.y(), 6)

                sink.addFeature(new_feature)
            else:
                feedback.reportError(self.tr(f'No nearest station found for feature {obj_feature.id()}'))

            # Update progress
            feedback.setProgress(int(i / total_features * 100))

        return {self.OUTPUT_LAYER: dest_id}

    def _detect_station_name_field(self, layer):
        """Select the most suitable string field for the station name"""
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

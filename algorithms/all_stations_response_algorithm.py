"""
Algorithm for creating a response time layer for all stations based on fire incident ranks
"""

from qgis.core import (QgsProcessingAlgorithm, QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterField, QgsProcessingParameterNumber,
                       QgsProcessingParameterFeatureSink, QgsProcessingParameterEnum,
                       QgsProcessingParameterString, QgsFeature, QgsGeometry,
                       QgsPointXY, QgsDistanceArea, QgsProject, QgsUnitTypes,
                       QgsProcessingException, QgsField, QgsFields, QgsWkbTypes, QgsProcessing)
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


class AllStationsResponseAlgorithm(QgsProcessingAlgorithm):
    """
    Algorithm for creating a response time layer for all stations
    based on various fire incident ranks and the number of responding units
    """

    # Parameter constants
    OBJECTS_LAYER = 'OBJECTS_LAYER'
    FIRE_STATIONS_LAYER = 'FIRE_STATIONS_LAYER'
    ROAD_LAYER = 'ROAD_LAYER'
    STATION_NAME_FIELD = 'STATION_NAME_FIELD'  # no longer a parameter; used as output key
    ROAD_SPEEDS_KMH = 'ROAD_SPEEDS_KMH'
    USE_CACHE = 'USE_CACHE'
    OUTPUT_LAYER = 'OUTPUT_LAYER'

    def tr(self, string):
        """Translate string"""
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        """Create algorithm instance"""
        return AllStationsResponseAlgorithm()

    def name(self):
        """Algorithm name"""
        return 'all_stations_response'

    def displayName(self):
        """Algorithm display name"""
        return self.tr('All Stations Response Analysis')

    def icon(self):
        """Algorithm icon for the Processing toolbar"""
        plugin_root = os.path.dirname(os.path.dirname(__file__))
        return QIcon(os.path.join(plugin_root, 'icons', 'all_stations_response_algorithm_icon.png'))

    def group(self):
        """Algorithm group"""
        return self.tr('Fire Response Analysis')

    def groupId(self):
        """Algorithm group ID"""
        return 'fire_response_analysis'

    def shortHelpString(self):
        """Short help text"""
        return self.tr(
            "This algorithm creates a layer with response time analysis for all stations "
            "across all fire ranks simultaneously. All ranks are calculated: "
            "rank 1 (1 unit), rank 1-bis (2 units), rank 2 (3 units), "
            "rank 3 (4 units), rank 4 (5 units), rank 5 (6 units)."
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

        # Station name is detected automatically during processing

        # Unit type is not used

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

        # Note: algorithm calculates all fire ranks simultaneously

        # Output layer
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT_LAYER,
                self.tr('Output stations analysis layer')
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        """Main processing logic using arrival time matrix for all ranks"""

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

        # Number of units per fire rank
        fire_ranks = {
            "rank_1": 1,      # 1 unit
            "rank_1bis": 2,   # 2 units
            "rank_2": 3,      # 3 units
            "rank_3": 4,      # 4 units
            "rank_4": 5,      # 5 units
            "rank_5": 6       # 6 units
        }

        # Create output layer fields for all ranks
        fields = QgsFields()
        fields.append(QgsField('object_id', QVariant.Int))

        # Add fields for each rank
        for rank_name in fire_ranks.keys():
            fields.append(QgsField(f'{rank_name}_min', QVariant.Double))  # Minimum arrival time
            fields.append(QgsField(f'{rank_name}_max', QVariant.Double))  # Maximum arrival time
            fields.append(QgsField(f'{rank_name}_avg', QVariant.Double))  # Average arrival time

        # Overall fields
        fields.append(QgsField('arrival_time_mean', QVariant.Double))  # Mean across all ranks
        fields.append(QgsField('arrival_time_max', QVariant.Double))   # Max across all ranks
        fields.append(QgsField('arrival_time_min', QVariant.Double))   # Min across all ranks
        fields.append(QgsField('evaluation', QVariant.String))         # Assessment based on mean arrival time

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

        station_name_field = self._detect_station_name_field(fire_stations_layer)
        fire_stations = list(fire_stations_layer.getFeatures())

        import networkx as nx

        # Step 1: Find graph nodes for all fire stations
        feedback.pushInfo(self.tr('Locating graph nodes for fire stations...'))
        station_nodes = {}

        for station in fire_stations:
            st_pt = station.geometry().asPoint()
            st_wgs = to_wgs.transform(st_pt.x(), st_pt.y())
            try:
                st_node = find_nearest_node(G, st_wgs.x(), st_wgs.y())
                if st_node is None:
                    feedback.reportError(self.tr(f'Could not find node for station {station.id()}: node not found'))
                    continue
                station_name = station[station_name_field] if station_name_field else f"Station_{station.id()}"
                station_nodes[station_name] = st_node
            except Exception as e:
                feedback.reportError(self.tr(f'Could not find node for station {station.id()}'))
                continue

        if len(station_nodes) == 0:
            raise QgsProcessingException(self.tr('Could not find graph nodes for any station'))

        # Step 2: Prepare object data and find their nodes
        feedback.pushInfo(self.tr('Preparing object data...'))
        objects_data = []
        objects_nodes_set = set()

        for obj_feature in objects_layer.getFeatures():
            obj_geometry = obj_feature.geometry()
            if obj_geometry.isEmpty():
                continue

            # Determine feature centroid
            if obj_geometry.type() == QgsWkbTypes.PointGeometry:
                obj_point = obj_geometry.asPoint()
            else:
                obj_point = obj_geometry.centroid().asPoint()

            obj_id = obj_feature.id()
            obj_wgs = to_wgs.transform(obj_point.x(), obj_point.y())

            try:
                obj_node = find_nearest_node(G, obj_wgs.x(), obj_wgs.y())
                if obj_node is not None:
                    objects_data.append({
                        'feature': obj_feature,
                        'geometry': obj_geometry,
                        'id': obj_id,
                        'node': obj_node
                    })
                    objects_nodes_set.add(obj_node)
            except Exception:
                continue

        total_features = len(objects_data)
        if total_features == 0:
            raise QgsProcessingException(self.tr('No objects found to process'))

        feedback.pushInfo(self.tr(f'Found {total_features} objects and {len(station_nodes)} stations'))

        # Step 3: Compute arrival time matrix
        feedback.pushInfo(self.tr('Computing arrival time matrix...'))
        arrival_times_matrix = {}  # {station_name: {node: time}}

        total_stations = len(station_nodes)
        for idx, (station_name, station_node) in enumerate(station_nodes.items()):
            if feedback.isCanceled():
                break

            progress_pct = round(100 * idx / total_stations, 1)
            feedback.pushInfo(self.tr(f'{progress_pct}% : {station_name}...'))

            try:
                # Compute shortest paths from station to all object nodes
                arrival_times = nx.shortest_path_length(
                    G,
                    source=station_node,
                    weight='travel_time'
                )
                # Filter to object nodes only
                arrival_times_filtered = {
                    k: v for k, v in arrival_times.items()
                    if k in objects_nodes_set
                }
                arrival_times_matrix[station_name] = arrival_times_filtered
                feedback.pushInfo(self.tr(f'{progress_pct}% : {station_name}... OK'))
            except Exception as e:
                feedback.reportError(self.tr(f'Error computing arrival times for station: {station_name}'))
                arrival_times_matrix[station_name] = {}

        # Step 4: Process objects using the matrix for all ranks
        feedback.pushInfo(self.tr('Processing objects using arrival time matrix for all ranks...'))

        for i, obj_data in enumerate(objects_data):
            if feedback.isCanceled():
                break

            obj_feature = obj_data['feature']
            obj_geometry = obj_data['geometry']
            obj_id = obj_data['id']
            obj_node = obj_data['node']

            # Get arrival times for this node from all stations
            station_times = []
            for station_name in station_nodes.keys():
                if obj_node in arrival_times_matrix.get(station_name, {}):
                    time_min = arrival_times_matrix[station_name][obj_node]
                    station_times.append({
                        'name': station_name,
                        'response_time_min': time_min
                    })

            if len(station_times) == 0:
                continue

            # Sort by arrival time
            station_times.sort(key=lambda x: x['response_time_min'])

            # Get all arrival times for calculations
            all_times = [s['response_time_min'] for s in station_times]

            # Calculate statistics for each rank
            rank_results = {}
            for rank_name, units_count in fire_ranks.items():
                if len(station_times) < units_count:
                    # Fewer stations available than required for this rank
                    selected_times = all_times
                else:
                    selected_times = all_times[:units_count]

                if len(selected_times) > 0:
                    rank_results[rank_name] = {
                        'min': min(selected_times),
                        'max': max(selected_times),
                        'avg': sum(selected_times) / len(selected_times)
                    }
                else:
                    rank_results[rank_name] = {
                        'min': float('inf'),
                        'max': float('inf'),
                        'avg': float('inf')
                    }

            # Calculate overall statistics (based on min time across all ranks)
            all_min_times = [r['min'] for r in rank_results.values() if r['min'] != float('inf')]
            all_max_times = [r['max'] for r in rank_results.values() if r['max'] != float('inf')]
            all_avg_times = [r['avg'] for r in rank_results.values() if r['avg'] != float('inf')]

            arrival_time_min = min(all_min_times) if all_min_times else float('inf')
            arrival_time_max = max(all_max_times) if all_max_times else float('inf')
            arrival_time_mean = sum(all_avg_times) / len(all_avg_times) if all_avg_times else float('inf')

            # Create new feature
            new_feature = QgsFeature(fields)
            new_feature.setGeometry(obj_geometry)
            new_feature['object_id'] = obj_id

            # Populate fields for each rank
            for rank_name in fire_ranks.keys():
                rank_data = rank_results[rank_name]
                new_feature[f'{rank_name}_min'] = round(rank_data['min'], 1) if rank_data['min'] != float('inf') else None
                new_feature[f'{rank_name}_max'] = round(rank_data['max'], 1) if rank_data['max'] != float('inf') else None
                new_feature[f'{rank_name}_avg'] = round(rank_data['avg'], 1) if rank_data['avg'] != float('inf') else None

            # Overall fields
            new_feature['arrival_time_min'] = round(arrival_time_min, 1) if arrival_time_min != float('inf') else None
            new_feature['arrival_time_max'] = round(arrival_time_max, 1) if arrival_time_max != float('inf') else None
            new_feature['arrival_time_mean'] = round(arrival_time_mean, 1) if arrival_time_mean != float('inf') else None

            # Assessment based on mean arrival time (compared against 10-minute threshold)
            if arrival_time_mean != float('inf') and arrival_time_mean is not None:
                if arrival_time_mean <= 10:
                    evaluation = "satisfactory"
                else:
                    evaluation = "unsatisfactory"
            else:
                evaluation = None

            new_feature['evaluation'] = evaluation

            sink.addFeature(new_feature)

            # Update progress
            feedback.setProgress(int(i / total_features * 100))

        return {self.OUTPUT_LAYER: dest_id}

    def _detect_station_name_field(self, layer):
        """Detect the string field for the station name"""
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

    def tr(self, string):
        """Translate string"""
        return QCoreApplication.translate('Processing', string)

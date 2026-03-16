"""
Arrival time matrix calculation algorithm for fire protection units.
"""

__author__    = 'Malyutin O.S.'
__date__      = '2025-11-22'
__copyright__ = '(C) 2025 by SPSA'

__revision__  = '$Format:%H$'


import inspect
import os

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsProcessingException,
    QgsProcessingAlgorithm,
    QgsProcessing,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFileDestination,
)

try:
    import osmnx as ox
    import geopandas as gpd
    import pandas as pd
    import networkx as nx
    from shapely.geometry import Point, Polygon, MultiPolygon
    from shapely.ops import unary_union
    from shapely import wkt
    import numpy as np
    ox.settings.log_console = False
    ox.settings.use_cache = True
    OSMNX_AVAILABLE = True
except ImportError:
    OSMNX_AVAILABLE = False

from .genesis.genesis.swiss_knife import DELAY_TIME
from ..graph_tools import get_graph_from_layer


class ATM_Algorithm(QgsProcessingAlgorithm):
    """
    Arrival time matrix calculation algorithm for fire protection units.

    The result is a new vector layer of buildings with columns
    corresponding to the arrival time of each fire protection unit.
    """

    # Input parameters
    ROAD_NETWORK = 'ROAD_NETWORK'
    WEIGHT_FIELD = 'WEIGHT_FIELD'
    FIRE_UNITS = 'FIRE_UNITS'
    UNITS_NAME_FIELD = 'UNITS_NAME_FIELD'
    BUILDINGS = 'BUILDINGS'


    # Output
    OUTPUT = 'OUTPUT'

    PRE_GDS_PATH  = '{}.ml'

    def tr(self, string):
        """
        Returns a translation for self.tr().
        """
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return ATM_Algorithm()

    def name(self):
        """
        Algorithm name
        """
        return 'arrival_time_matrix'

    def displayName(self):
        """
        Algorithm display name
        """
        return self.tr('Arrival Time Matrix for All Stations')

    def group(self):
        """
        Returns the name of the group this algorithm belongs to.
        """
        return self.tr('1. General Algorithms')

    def groupId(self):
        """
        Returns the unique identifier of the group this algorithm belongs to.
        """
        return 'COMMON'

    def shortHelpString(self):
        """
        Returns a short description of the algorithm
        """
        return self.tr("""
            Arrival time matrix calculation algorithm for fire protection units.

            The result is a new vector layer of buildings with columns
            corresponding to the arrival time of each fire protection unit.

            Parameters:
            - Road network layer (INPUT): Vector line layer of the road network
            - Travel time field for road segments (EDGES_WEIGHT_FIELD): Field in the road network layer containing travel time per segment
            - Fire units layer (FIRE_UNITS): Point layer with fire units
            - Buildings layer (BUILDINGS): Polygon layer with buildings. If not provided, road network graph nodes will be used as targets

            Output:
            - Buildings layer with arrival times for each fire protection unit
        """)

    def icon(self):
        """
        Returns the algorithm icon
        """
        cmd_folder = os.path.split(inspect.getfile(inspect.currentframe()))[0]
        icon_path  = os.path.join(cmd_folder, '..', 'icons/atm_calc.svg')
        icon_path  = os.path.normpath(icon_path)
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        else:
            return QIcon()

    def initAlgorithm(self, config=None):
        """
        Define algorithm settings — inputs and outputs.
        """

        # Road network layer
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.ROAD_NETWORK,
            self.tr('Road network layer'),
            [QgsProcessing.TypeVectorLine],
            defaultValue='Road Network',
            optional=False
        ))

        # Travel time field
        self.addParameter(QgsProcessingParameterField(
           self.WEIGHT_FIELD,
           self.tr('Travel time field for road network segments'),
           parentLayerParameterName=self.ROAD_NETWORK,
           type=QgsProcessingParameterField.Numeric,
           defaultValue='travel_time',
           optional=False
        ))

        # Fire units layer
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FIRE_UNITS,
            self.tr('Fire units layer'),
            [QgsProcessing.TypeVectorPoint],
            defaultValue='Units',
            optional=False
        ))

        # Unit name field
        self.addParameter(QgsProcessingParameterField(
            self.UNITS_NAME_FIELD,
            self.tr('Unit name field'),
            parentLayerParameterName=self.FIRE_UNITS,
            type=QgsProcessingParameterField.String,
            defaultValue='name',
            optional=False
        ))

        # Buildings layer
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.BUILDINGS,
            self.tr('Buildings layer'),
            [QgsProcessing.TypeVectorPolygon],
            optional=True
        ))

        # Output buildings layer
        self.addParameter(QgsProcessingParameterFileDestination(
                self.OUTPUT, self.tr('Arrival matrix (buildings with arrival times)'), 'Geopackage file (*.gpkg)',
            ))



    def processAlgorithm(self, parameters, context, feedback):
        """
        Main algorithm logic
        """

        DATA_NODE_FIELD   = 'node'


        # Check osmnx availability
        if not OSMNX_AVAILABLE:
            raise QgsProcessingException(
                self.tr('Required libraries must be installed: osmnx, geopandas, shapely')
            )

        # Retrieve input parameters
        road_network_source  = self.parameterAsVectorLayer(parameters, self.ROAD_NETWORK, context)
        weight_field         = self.parameterAsString(parameters, self.WEIGHT_FIELD, context)
        existed_units_layer  = self.parameterAsSource(parameters, self.FIRE_UNITS, context)
        units_name_field     = self.parameterAsString(parameters, self.UNITS_NAME_FIELD, context)
        target_layer         = self.parameterAsSource(parameters, self.BUILDINGS, context)

        target_file          = self.parameterAsFile(parameters, self.OUTPUT, context)


        # 1. Prepare source data
        # ================================================================================================
        feedback.setProgress(5)

        # 1.1. Build road network graph
        feedback.setProgressText('Building road network graph...')
        pre_gds_file = self.PRE_GDS_PATH.format(road_network_source.id())
        feedback.pushDebugInfo(f'Graph path: {pre_gds_file}')
        G = get_graph_from_layer(pre_gds_file, road_network_source, feedback)

        ## Project graph to local CRS
        G = ox.project_graph(G)

        ## Output
        estimated_utm_crs = G.graph['crs']
        feedback.pushDebugInfo(f'Road graph obtained with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges. CRS: {estimated_utm_crs}')
        feedback.setProgress(25)


        # 1.2. Prepare geodata
        feedback.setProgressText('Preparing data...')
        if target_layer is None:
            feedback.pushDebugInfo('No buildings layer provided; graph nodes will be used as arrival targets')
            target_layer_gdf = ox.graph_to_gdfs(G, edges=False)
            target_layer_gdf = target_layer_gdf.reset_index()
            target_layer_gdf.rename(columns={'osmid': DATA_NODE_FIELD}, inplace=True)
            target_layer_gdf = ox.projection.project_gdf(target_layer_gdf, to_crs=estimated_utm_crs)
        else:
            target_layer_gdf = gpd.GeoDataFrame.from_features(
                list(target_layer.getFeatures()),
                crs = target_layer.sourceCrs().authid()
                )
            # Project to local CRS and find nearest graph nodes
            target_layer_gdf                  = ox.projection.project_gdf(target_layer_gdf, to_crs=estimated_utm_crs)
            centroids                         = target_layer_gdf.geometry.centroid
            target_layer_gdf[DATA_NODE_FIELD] = ox.nearest_nodes(G, centroids.x, centroids.y)
            del centroids


        # 1.3. Prepare existing units layer
        existed_units_layer_gdf = gpd.GeoDataFrame.from_features(
            list(existed_units_layer.getFeatures()),
            crs = existed_units_layer.sourceCrs().authid()
            )
        existed_units_layer_gdf = ox.projection.project_gdf(existed_units_layer_gdf, to_crs=estimated_utm_crs)
        existed_units_layer_gdf[DATA_NODE_FIELD] = ox.nearest_nodes(G,
                                                            existed_units_layer_gdf.geometry.x,
                                                            existed_units_layer_gdf.geometry.y
                                                            )
        # If the unit name field is missing, create it
        if not units_name_field in existed_units_layer_gdf.columns:
            existed_units_layer_gdf[units_name_field] = pd.Series([f'#{i}' for i in range(len(existed_units_layer_gdf))])

        # Build units dictionary
        existed_units_dict = dict(zip(existed_units_layer_gdf[DATA_NODE_FIELD],
                                        existed_units_layer_gdf[units_name_field]))
        feedback.setProgress(35)



        # 2. Compute required number of fire vehicles
        # ================================================================================================
        feedback.setProgressText('Computing fire unit arrival times')

        # 2.1. Compute expected arrival times for each unit
        i = 0
        for node, unit_name in existed_units_dict.items():
            times = nx.single_source_dijkstra_path_length(G, node, weight=weight_field)
            times = pd.Series(times, name = unit_name)
            times = times+DELAY_TIME

            # Join buildings with arrival times
            target_layer_gdf = target_layer_gdf.merge(times,
                                                    left_on=DATA_NODE_FIELD,
                                                    right_index=True,
                                                    how='left')
            feedback.pushDebugInfo(f'Computed for {unit_name}')
            feedback.setProgress(40 + int(i * 55))
            i+=1

        # Drop node ID column
        target_layer_gdf = target_layer_gdf.drop(columns=[DATA_NODE_FIELD])


        # 5. Save results
        feedback.setProgressText('Saving results...')
        feedback.setProgress(95)

        # Reproject to source CRS
        if target_layer is not None:
            # source buildings CRS
            target_layer_gdf = ox.projection.project_gdf(target_layer_gdf, to_crs=target_layer.sourceCrs().authid())
        else:
            # source road CRS
            target_layer_gdf = ox.projection.project_gdf(target_layer_gdf, to_crs=road_network_source.sourceCrs().authid())


        # Save to output file
        target_layer_gdf.to_file(target_file)

        # Add resulting layer to the map
        vlayer = QgsVectorLayer(target_file, 'Arrival Matrix', 'ogr')
        QgsProject.instance().addMapLayer(vlayer)

        feedback.setProgress(100)
        feedback.setProgressText('OK')
        return {self.OUTPUT: target_file}

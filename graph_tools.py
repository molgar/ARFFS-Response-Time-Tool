'''
Additional utility functions for working with graphs
'''

import os

try:
    import geopandas as gpd
    GPD_AVAILABLE = True
except ImportError:
    GPD_AVAILABLE = False
    gpd = None

try:
    import osmnx as ox
    OSMNX_AVAILABLE = True
except ImportError:
    OSMNX_AVAILABLE = False
    ox = None

from .algorithms.genesis.graphs.algorithms import graph_rise_from_gpkg

from .algorithms.genesis.graphs.speeds import kmh_to_mm, set_graph_travel_times
from qgis.core import (QgsProcessingException)

def check_file_exists(file_path):
    return os.path.exists(file_path)


def get_graph_from_layer(pre_gds_file,
                         network,
                         feedback,
                         oneway_field_name: str = 'oneway',
                         # lanes_field_name: str = 'lanes',  # Not implemented yet
                         reversed_field_name: str = 'reversed',
                         ):
    """
    Build or load a road network graph.

    The function checks for a pre-saved graph file (GraphML).
    If the file exists, it is loaded. Otherwise, a new graph
    is constructed from the road network layer data.

    Parameters:
    ----------
    pre_gds_file : str
        Path to the GraphML file containing a pre-compiled graph.

    network : QgsVectorLayer
        Vector layer containing road network data.

    feedback : QgsProcessingFeedback
        Feedback object for displaying progress and messages.

    Returns:
    ----------
    G : networkx.MultiDiGraph
        Road network graph.

    Raises:
    -----------
    QgsProcessingException
        Raised if required attributes are missing from the road network layer.
    """
    if check_file_exists(pre_gds_file):
        # Load pre-compiled graph
        if not OSMNX_AVAILABLE:
            raise QgsProcessingException(
                'osmnx is required to load a pre-compiled graph. '
                'Install osmnx or use a road layer to build the graph.'
            )
        feedback.pushDebugInfo('Loading pre-compiled road graph')
        feedback.setProgressText('Loading road network graph')
        G = ox.load_graphml(pre_gds_file)
        roads_gdf = ox.graph_to_gdfs(G, nodes=False)
    else:
        # Build road network graph from layer
        if not GPD_AVAILABLE:
            raise QgsProcessingException(
                'geopandas is required to build a graph from a road layer. '
                'Install geopandas: pip install geopandas'
            )
        feedback.setProgressText('Building road network graph')

        # Load data from road layer
        roads_gdf = gpd.GeoDataFrame.from_features(list(network.getFeatures()), crs=network.sourceCrs().authid())

        # Normalise column names to lowercase
        columns_names_lower = {col: str.lower(col) for col in roads_gdf.columns}
        roads_gdf.rename(columns=columns_names_lower, inplace=True)

        ## Check for required fields
        if not oneway_field_name in roads_gdf.columns:
            feedback.pushWarning(f'Field "{oneway_field_name}" is missing from the road network layer!'
                ' The resulting graph will not account for one-way traffic directions!'
                                )
        if not reversed_field_name in roads_gdf.columns:
            feedback.pushWarning(f'Field "{reversed_field_name}" is missing from the road network layer!'
                ' The resulting graph may contain incorrect traffic direction errors!'
                                )
        ## Get all fields from the road layer, excluding 'geometry' and key fields
        ## IMPORTANT: also remove the 'length' field as it is recalculated in graph_rise_from_gpkg
        key_fields = ['u', 'v', 'key', 'osmid', 'length']
        columns_list = [col for col in roads_gdf.columns if col not in key_fields]

        ## Reconstruct graph
        G = graph_rise_from_gpkg(roads_gdf[columns_list],
                                oneway_field_name = oneway_field_name,
                                # lanes_field_name: str = 'lanes',  # Not implemented yet
                                reversed_field_name = reversed_field_name,
                                )

    return G

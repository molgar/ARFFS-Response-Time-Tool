"""
Additional utility functions for working with graphs.

This module wraps graph_utils for backward compatibility.
The genesis submodule dependency has been removed; all graph
building now goes through graph_utils.build_graph_from_road_layer().
"""

import os

try:
    import osmnx as ox
    OSMNX_AVAILABLE = True
except ImportError:
    OSMNX_AVAILABLE = False
    ox = None

try:
    import geopandas as gpd
    GPD_AVAILABLE = True
except ImportError:
    GPD_AVAILABLE = False
    gpd = None

from qgis.core import QgsProcessingException


def check_file_exists(file_path):
    return os.path.exists(file_path)


def get_graph_from_layer(pre_gds_file,
                         network,
                         feedback,
                         oneway_field_name: str = 'oneway',
                         reversed_field_name: str = 'reversed',
                         ):
    """
    Build or load a road network graph.

    If a pre-compiled GraphML file exists at ``pre_gds_file`` it is loaded.
    Otherwise a new graph is constructed from the road network layer using
    ``graph_utils.build_graph_from_road_layer()``.

    Parameters
    ----------
    pre_gds_file : str
        Path to GraphML cache file.
    network : QgsVectorLayer
        Road network vector layer.
    feedback : QgsProcessingFeedback
        Feedback object for progress display.

    Returns
    -------
    G : networkx.MultiDiGraph
    """
    if check_file_exists(pre_gds_file):
        if not OSMNX_AVAILABLE:
            raise QgsProcessingException(
                'osmnx is required to load a pre-compiled graph. '
                'Install osmnx or use a road layer to build the graph.'
            )
        feedback.pushDebugInfo('Loading pre-compiled road graph')
        feedback.setProgressText('Loading road network graph')
        G = ox.load_graphml(pre_gds_file)
        return G

    # Build graph from the road layer using graph_utils
    from .graph_utils import build_graph_from_road_layer
    feedback.setProgressText('Building road network graph')
    G, _to_wgs, _from_wgs = build_graph_from_road_layer(
        network, network, network
    )
    return G

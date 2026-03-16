"""
Processing algorithms provider for ARFFS (Aerodrome Fire and Rescue Service)
response time analysis.
"""

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
import os

from .algorithms.nearest_fire_station_algorithm import NearestFireStationAlgorithm
from .algorithms.response_time_routes_algorithm import ResponseTimeRoutesAlgorithm
from .algorithms.all_stations_response_algorithm import AllStationsResponseAlgorithm
from .algorithms.arrival_time_matrix import ATM_Algorithm
from .algorithms.first_arrival_unit import FirstArrivalUnitAlgorithm
from .algorithms.isochrone_algorithm import ARFFSIsochroneAlgorithm


class FireResponseAnalysisProvider(QgsProcessingProvider):
    """Processing algorithms provider for ARFFS response time analysis"""

    def __init__(self):
        super().__init__()

    def id(self):
        """Unique provider identifier"""
        return 'arffs_response_analysis'

    def name(self):
        """Provider name"""
        return 'ARFFS Response Time Analysis'

    def icon(self):
        """Provider icon"""
        return QIcon(os.path.join(os.path.dirname(__file__), 'icons', 'icon.png'))

    def longName(self):
        """Full provider name"""
        return 'ARFFS Response Time Analysis'

    def loadAlgorithms(self):
        """Load all algorithms"""
        self.addAlgorithm(ARFFSIsochroneAlgorithm())
        self.addAlgorithm(NearestFireStationAlgorithm())
        self.addAlgorithm(ResponseTimeRoutesAlgorithm())
        self.addAlgorithm(AllStationsResponseAlgorithm())
        self.addAlgorithm(ATM_Algorithm())
        self.addAlgorithm(FirstArrivalUnitAlgorithm())

    def supportedOutputTableExtensions(self):
        """Supported table output formats"""
        return ['csv', 'xlsx']

    def supportedOutputRasterLayerExtensions(self):
        """Supported raster layer output formats"""
        return ['tif', 'tiff']

    def supportedOutputVectorLayerExtensions(self):
        """Supported vector layer output formats"""
        return ['shp', 'gpkg', 'geojson', 'kml']

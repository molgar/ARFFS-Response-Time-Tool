"""
First arriving unit determination algorithm.
"""

__author__    = 'Malyutin O.S.'
__date__      = '2025-12-09'
__copyright__ = '(C) 2025 by SPSA'

__revision__  = '$Format:%H$'


import inspect
import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsProcessingException,
    QgsProcessingAlgorithm,
    QgsProcessing,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterField,
)

try:
    import geopandas as gpd
    import pandas as pd
    GPD_AVAILABLE = True
except ImportError:
    GPD_AVAILABLE = False


class FirstArrivalUnitAlgorithm(QgsProcessingAlgorithm):
    """
    First arriving unit determination algorithm.

    Accepts an arrival matrix layer and a units layer.
    Produces a new buildings layer in which, instead of columns
    with arrival times for all units, two columns are added:
    the name of the first arriving unit and its arrival time.
    """

    # Input parameters
    ARRIVAL_MATRIX   = 'ARRIVAL_MATRIX'        # Arrival matrix layer
    FIRE_UNITS       = 'FIRE_UNITS'            # Units layer
    UNITS_NAME_FIELD = 'UNITS_NAME_FIELD'      # Unit name field
    OUTPUT           = 'OUTPUT'                # Output layer

    def tr(self, string):
        """
        Returns a translation for self.tr().
        """
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return FirstArrivalUnitAlgorithm()

    def name(self):
        """
        Algorithm name
        """
        return 'first_arrival_unit'

    def displayName(self):
        """
        Algorithm display name
        """
        return self.tr('First Arrival Unit Response Time')

    def group(self):
        """
        Returns the name of the group this algorithm belongs to.
        """
        return self.tr('2. Arrival Analysis')

    def groupId(self):
        """
        Returns the unique identifier of the group this algorithm belongs to.
        """
        return 'ARRIVAL_ANALYSIS'

    def shortHelpString(self):
        """
        Returns a short description of the algorithm
        """
        return self.tr("""
            First arriving fire protection unit determination algorithm.

            Accepts:
            - Arrival matrix layer (produced by the "Arrival Time Matrix for All Stations" algorithm)
            - Fire units layer

            Output:
            - New vector buildings layer with two columns:
              * "first_unit" — name of the first arriving unit
              * "first_time" — arrival time of the first unit

            The algorithm iterates over all features in the arrival matrix layer and for each
            identifies the unit with the minimum arrival time, excluding NULL/NaN values.

            The output file is saved in Geopackage format (.gpkg) and automatically
            added to the map as a new layer.
        """)

    def icon(self):
        """
        Returns the algorithm icon
        """
        cmd_folder = os.path.split(inspect.getfile(inspect.currentframe()))[0]
        icon_path = os.path.join(cmd_folder, '..', 'icons', 'nearest_fire_station_algorithm_icon.png')
        icon_path = os.path.normpath(icon_path)
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        else:
            return QIcon()

    def initAlgorithm(self, config=None):
        """
        Define algorithm settings — inputs and outputs.
        """

        # Arrival matrix layer
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.ARRIVAL_MATRIX,
                self.tr('Arrival matrix layer'),
                [QgsProcessing.TypeVectorAnyGeometry],
                defaultValue='Arrival Matrix',
                optional=False
            )
        )

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

        # Output file
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT,
                self.tr('First arriving unit (new layer)'),
                'Geopackage file (*.gpkg)',
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        """
        Main algorithm logic
        """

        # Check geopandas availability
        if not GPD_AVAILABLE:
            raise QgsProcessingException(
                self.tr('Required libraries must be installed: geopandas, pandas')
            )

        # Retrieve input parameters
        arrival_matrix_source = self.parameterAsSource(parameters, self.ARRIVAL_MATRIX, context)
        fire_units_source     = self.parameterAsSource(parameters, self.FIRE_UNITS, context)
        units_name_field      = self.parameterAsString(parameters, self.UNITS_NAME_FIELD, context)
        output_file           = self.parameterAsFile(parameters, self.OUTPUT, context)

        # Begin processing
        feedback.setProgressText('Reading input data...')
        feedback.setProgress(5)

        # Build units GeoDataFrame
        units_gdf = gpd.GeoDataFrame.from_features(
            list(fire_units_source.getFeatures()),
            crs=fire_units_source.sourceCrs().authid()
        )

        # Load arrival matrix layer into GeoDataFrame
        arrival_gdf = gpd.GeoDataFrame.from_features(
            list(arrival_matrix_source.getFeatures()),
            crs=arrival_matrix_source.sourceCrs().authid()
        )

        feedback.setProgress(80)
        feedback.setProgressText('Processing arrival matrix...')

        # Identify columns containing arrival times
        units_names = list(units_gdf[units_name_field].unique())

        # Compute times and units
        first_time = arrival_gdf[units_names].min(axis=1)
        first_unit = arrival_gdf[units_names].idxmin(axis=1)

        # Assign results to new columns
        arrival_gdf['first_unit'] = first_unit
        arrival_gdf['first_time'] = first_time
        feedback.pushDebugInfo('Added field "first_unit" containing the name of the first arriving unit')
        feedback.pushDebugInfo('Added field "first_time" containing the arrival time of the first unit')
        feedback.pushDebugInfo('Unit arrival time columns removed.')

        # Drop arrival time columns
        arrival_gdf = arrival_gdf.drop(columns=units_names)
        feedback.setProgress(95)

        # Save result to output file
        feedback.setProgressText('Saving result...')
        arrival_gdf.to_file(output_file)

        # Add resulting layer to the map
        result_layer = QgsVectorLayer(output_file, 'First Arriving Unit', 'ogr')
        if result_layer.isValid():
            QgsProject.instance().addMapLayer(result_layer)

        feedback.setProgress(100)
        feedback.setProgressText('Done!')

        return {self.OUTPUT: output_file}

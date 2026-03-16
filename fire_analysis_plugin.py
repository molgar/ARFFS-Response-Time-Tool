"""
Fire Response Time Analysis Plugin
Main plugin class for fire department response time analysis
"""

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication
import os

from .fire_response_analysis_provider import FireResponseAnalysisProvider


class FireAnalysisPlugin:
    """Main plugin class"""

    def __init__(self, iface):
        """Initialize the plugin"""
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        # Initialize translator
        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(
            self.plugin_dir,
            'i18n',
            'FireAnalysisPlugin_{}.qm'.format(locale))

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        # Initialize processing provider
        self.provider = FireResponseAnalysisProvider()

        # Action for the plugin menu
        self.install_libs_action = None

    def initGui(self):
        """Create menu and toolbar"""
        # Add processing provider
        QgsApplication.processingRegistry().addProvider(self.provider)

        # Create action for library installation in the plugin menu
        self.install_libs_action = QAction(
            QIcon(os.path.join(self.plugin_dir, 'icons', 'icon.png')),
            u"Install Libraries (OSMnx)",
            self.iface.mainWindow())

        self.install_libs_action.triggered.connect(self.show_install_dialog)
        self.iface.addPluginToMenu(u"&Fire Analysis", self.install_libs_action)

    def unload(self):
        """Unload the plugin"""
        # Remove processing provider
        QgsApplication.processingRegistry().removeProvider(self.provider)

        # Remove action from menu
        if self.install_libs_action:
            self.iface.removePluginMenu(u"&Fire Analysis", self.install_libs_action)
            self.install_libs_action = None

    def show_install_dialog(self):
        """Show the library installation dialog"""
        from .osmnx_checker import show_osmnx_install_dialog
        show_osmnx_install_dialog(self.iface, self.iface.mainWindow())

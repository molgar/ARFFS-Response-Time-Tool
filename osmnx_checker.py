"""
Utility for checking whether the osmnx library is available and offering to install it
"""

import os
import subprocess
import sys
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                 QLabel, QMessageBox)
from qgis.PyQt.QtCore import Qt


def check_osmnx_available():
    """Check whether the osmnx library is installed"""
    try:
        import osmnx
        return True
    except ImportError:
        return False


def show_osmnx_install_dialog(iface=None, parent=None):
    """Show a dialog offering to install osmnx"""
    # Obtain parent window
    if parent is None:
        # Try to get via iface
        if iface is not None:
            try:
                parent = iface.mainWindow()
            except Exception:
                pass

        # If not obtained via iface, try via qgis.utils
        if parent is None:
            try:
                from qgis.utils import iface as qgis_iface
                if qgis_iface:
                    parent = qgis_iface.mainWindow()
            except Exception:
                pass

        # If still not obtained, try via QApplication
        if parent is None:
            try:
                from qgis.PyQt.QtWidgets import QApplication
                app = QApplication.instance()
                if app:
                    # Try to find the main QGIS window
                    for widget in app.topLevelWidgets():
                        widget_name = getattr(widget, 'objectName', lambda: '')()
                        if widget_name == 'QgisApp' or 'QGIS' in str(type(widget)):
                            parent = widget
                            break
                    # Fall back to active window
                    if parent is None:
                        parent = app.activeWindow()
            except Exception:
                pass

    # Create and show dialog
    try:
        dialog = OSMnxInstallDialog(iface, parent)
        # Ensure the dialog is visible
        dialog.raise_()
        dialog.activateWindow()
        return dialog.exec_()
    except Exception as e:
        # If the dialog could not be shown, display a simple message
        try:
            from qgis.PyQt.QtWidgets import QMessageBox, QApplication
            app = QApplication.instance()
            if app:
                msg = QMessageBox(parent)
                msg.setWindowTitle("Install OSMnx Library")
                msg.setText(
                    "The OSMnx library is required by this algorithm.\n\n"
                    "Install it via OSGeo4W Shell:\n"
                    "python -m pip install \"osmnx>=1.4,<2.0\" \"networkx>=2.6,<3.0\""
                )
                msg.exec_()
        except Exception:
            pass
        return 0


class OSMnxInstallDialog(QDialog):
    """Dialog for offering to install osmnx"""

    def __init__(self, iface=None, parent=None):
        super(OSMnxInstallDialog, self).__init__(parent)
        self.iface = iface
        self.setWindowTitle("Install OSMnx Library")
        self.setModal(True)
        self.resize(400, 150)

        # Set window flags for correct display
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)

        self.setup_ui()

    def setup_ui(self):
        """Set up the user interface"""
        layout = QVBoxLayout()

        # Message text
        message_label = QLabel(
            "The OSMnx library required by this algorithm is not installed.\n\n"
            "Would you like to install it?"
        )
        message_label.setWordWrap(True)
        layout.addWidget(message_label)

        # Buttons
        button_layout = QHBoxLayout()

        self.yes_button = QPushButton("Yes")
        self.yes_button.clicked.connect(self.install_osmnx)
        button_layout.addWidget(self.yes_button)

        self.no_button = QPushButton("No")
        self.no_button.clicked.connect(self.reject)
        button_layout.addWidget(self.no_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def install_osmnx(self):
        """Launch osmnx installation via the bat file"""
        # Get the plugin directory path (where the bat file is located)
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        bat_file = os.path.join(plugin_dir, 'install_osmnx.bat')

        if not os.path.exists(bat_file):
            QMessageBox.warning(
                self,
                "Error",
                "Installation file (install_osmnx.bat) not found in the plugin directory.\n\n"
                "Please install osmnx manually."
            )
            return

        try:
            # Launch the bat file in a new command prompt window
            # Use cmd /c to run in a separate window
            subprocess.Popen(
                ['cmd', '/c', 'start', 'cmd', '/k', bat_file],
                shell=False
            )
            QMessageBox.information(
                self,
                "Installation Started",
                "OSMnx installation has been launched in a separate command prompt window.\n\n"
                "Restart QGIS after the installation completes."
            )
            self.accept()
        except Exception:
            QMessageBox.critical(
                self,
                "Error",
                "Could not launch installation.\n\n"
                "Try running the bat file manually from OSGeo4W Shell."
            )

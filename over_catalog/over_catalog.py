# -*- coding: utf-8 -*-
"""
Main plugin class. Registers the Browser tree provider (layer 2), the
Locator search filter (layer 3), and the settings dialog entry (layer 4).
"""

from qgis.core import QgsApplication
from qgis.gui import QgsGui

# QAction moved from QtWidgets (Qt5) to QtGui (Qt6); QGIS ships both bindings.
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:  # pragma: no cover - Qt5 fallback
    from qgis.PyQt.QtWidgets import QAction

MENU_NAME = "&OVER Catalog"


class OverCatalogPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.locator_filter = None
        self.data_provider = None
        self.gui_provider = None
        self.settings_action = None

    def initGui(self):
        # Browser tree provider (registry lives on QgsApplication).
        from .data_items import OverDataItemProvider, OverDataItemGuiProvider
        self.data_provider = OverDataItemProvider()
        QgsApplication.dataItemProviderRegistry().addProvider(
            self.data_provider
        )

        # GUI provider: routes double-click on the Route B leaf to a load
        # (the plain QgsDataItem.handleDoubleClick is not consulted for layer
        # items). Registry lives on QgsGui.
        self.gui_provider = OverDataItemGuiProvider()
        QgsGui.dataItemGuiProviderRegistry().addProvider(self.gui_provider)

        # Global search-bar filter (registry lives on the iface, not
        # QgsApplication).
        from .locator import OverLocatorFilter
        self.locator_filter = OverLocatorFilter()
        self.iface.registerLocatorFilter(self.locator_filter)

        # Settings dialog entry, under Plugins -> OVER Catalog.
        self.settings_action = QAction(
            "הגדרות...", self.iface.mainWindow())
        self.settings_action.triggered.connect(self._open_settings)
        self.iface.addPluginToMenu(MENU_NAME, self.settings_action)

    def _open_settings(self):
        from .settings_dialog import SettingsDialog, SETTINGS_SPATIAL_ONLY
        from qgis.PyQt.QtCore import QSettings
        before = QSettings().value(SETTINGS_SPATIAL_ONLY, False, type=bool)
        dlg = SettingsDialog(self.iface.mainWindow())
        if dlg.exec():
            after = QSettings().value(SETTINGS_SPATIAL_ONLY, False, type=bool)
            if after != before:
                # The tree filter only takes effect when a node re-runs
                # createChildren, so refresh the OVER root to apply it now.
                self._refresh_tree()

    def _refresh_tree(self):
        # A plain item.refresh() reconciles by path and keeps the unchanged
        # source_type/org nodes (and their already-filtered children), so the
        # spatial_only change would not show. Re-registering the data provider
        # drops the OVER root and rebuilds it from scratch — the filter is then
        # re-applied everywhere (the tree collapses to the top; expected).
        from .data_items import OverDataItemProvider
        try:
            reg = QgsApplication.dataItemProviderRegistry()
            if self.data_provider is not None:
                reg.removeProvider(self.data_provider)
            self.data_provider = OverDataItemProvider()
            reg.addProvider(self.data_provider)
        except Exception:
            pass

    def unload(self):
        if self.locator_filter is not None:
            self.iface.deregisterLocatorFilter(self.locator_filter)
            self.locator_filter = None

        if self.gui_provider is not None:
            QgsGui.dataItemGuiProviderRegistry().removeProvider(
                self.gui_provider
            )
            # removeProvider takes ownership/deletes; drop our reference.
            self.gui_provider = None

        if self.data_provider is not None:
            QgsApplication.dataItemProviderRegistry().removeProvider(
                self.data_provider
            )
            self.data_provider = None

        if self.settings_action is not None:
            self.iface.removePluginMenu(MENU_NAME, self.settings_action)
            self.settings_action = None

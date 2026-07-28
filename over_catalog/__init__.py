# -*- coding: utf-8 -*-
#
# OVER Catalog — a QGIS plugin for the גרסאות לעם (over.org.il) catalog.
# Copyright (C) 2026 Reuven Kost, Guy Zomer
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See the LICENSE file for the full text.
#
"""OVER Catalog — QGIS plugin entry point."""


def classFactory(iface):
    """Called by QGIS when the plugin is loaded."""
    from .over_catalog import OverCatalogPlugin
    return OverCatalogPlugin(iface)

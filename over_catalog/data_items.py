# -*- coding: utf-8 -*-
"""
Layer 2 — the Browser panel tree (QgsDataItemProvider + data items).

The tree is built entirely from `catalog_cache` (one `/api/tables` call,
aggregated by dataset), so every node comes from local data — no per-level
REST calls with the wrong public-API params. Only the dataset leaf level
still hits the network lazily: `/versions/latest` for the file resources
(Route A) and `/versions` for history.

Hierarchy (every level lazy-loaded via createChildren, which QGIS runs on a
background thread, so blocking API calls here are fine):

    OVER
    └── <source_type>            govmap / ckan / scraper / ...
        └── <organization>
            └── <dataset (N)>
                ├── 🗺 … תצוגה נוכחית      Route B leaf (spatial only)
                ├── <file resource>         Route A leaf(s), latest version
                └── גרסאות קודמות           lazy history (Route A)

Loading:
  * Route B (datastore_search_sql) is the default for spatial datasets:
    double-click = load per settings (bbox = current canvas by default);
    right-click = choose current-view / whole-layer.
  * Route A (whole file via /vsicurl/) is the file resource leaves and the
    only route for previous versions.
"""

from qgis.core import (
    QgsDataItem,
    QgsDataItemProvider,
    QgsDataProvider,
    QgsDataCollectionItem,
    QgsLayerItem,
    QgsErrorItem,
    QgsApplication,
    Qgis,
    QgsMessageLog,
)
from qgis.gui import QgsDataItemGuiProvider
from qgis.PyQt.QtCore import QSettings

# QAction moved from QtWidgets (Qt5) to QtGui (Qt6); QGIS ships both bindings.
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:  # pragma: no cover - Qt5 fallback
    from qgis.PyQt.QtWidgets import QAction

from . import api
from . import catalog_cache
from . import datastore

PROVIDER_KEY = "OVER"
ROOT_PATH = "over:"

# Friendly labels for the raw source_type buckets; unknown values pass through.
SOURCE_TYPE_LABELS = {
    "govmap": "govmap",
    "ckan": "data.gov.il (ckan)",
    "scraper": "scraper",
    "odata": "OData",
}

# Soft cap on datasets listed under one organization node. Must stay above the
# largest real org (govmap.gov.il ≈ 795) so no dataset is hidden — otherwise
# a dataset past the cap has no tree node and the Locator cannot reveal it.
# Items are cheap (no network until expanded), so a high cap is fine.
PAGE_CAP = 2000

SETTINGS_SPATIAL_ONLY = "over_catalog/spatial_only"       # tree filter


def _log(msg, level=Qgis.MessageLevel.Warning):
    QgsMessageLog.logMessage(msg, "OVER", level)


def dataset_browser_path(rec):
    """
    The Browser-tree path string for a dataset record — must stay identical to
    the paths the tree items build (root/source_type/org/dataset_id), so the
    Locator can resolve a search result back to its node via
    QgsBrowserModel.findPath. The "—" fallbacks match catalog_cache's
    source_types()/organizations() and OrganizationItem.
    """
    source_type = rec.get("source_type") or "—"
    org = rec.get("organization") or "—"
    return f"{ROOT_PATH}/{source_type}/{org}/{rec['dataset_id']}"


# Loaded-layer name suffix for the Route B "current view" (bbox) load, so the
# user can tell a viewport-filtered layer apart from other data.
CURRENT_VIEW_SUFFIX = "תצוגה נוכחית"


def dataset_file_label(title, item):
    """
    Tree-leaf label AND loaded-layer name for a Route A file resource.

    The primary GeoJSON (spatial) resource is named with the bare dataset
    title — no ".geojson" — so double-clicking it drops a clean layer name
    into the Layers panel. Non-geojson resources (CSV, etc.) keep their
    extension so they stay distinguishable from the GeoJSON in the tree.
    """
    if item.get("kind") == "vector":
        return title
    fmt = (item.get("fmt") or "").lower()
    if not fmt:
        fmt = "csv" if item.get("kind") == "table" else "dat"
    return f"{title}.{fmt}"


def current_view_name(title):
    """Loaded-layer name for a Route B current-view (bbox) load."""
    return f"{title} — {CURRENT_VIEW_SUFFIX}"


def _notify(msg, level=Qgis.MessageLevel.Info):
    """User-visible message bar feedback (best effort — iface may be absent)."""
    try:
        from qgis.utils import iface
        iface.messageBar().pushMessage("OVER", msg, level=level)
    except Exception:
        _log(msg, level)


def _notify_loaded(layer, truncated):
    """
    Report a successful Route B load, flagging it clearly when row_cap was
    hit — the layer holds fewer features than the table actually has.
    """
    msg = f"נטען: {layer.name()} ({layer.featureCount()} פיצ'רים)"
    if truncated:
        _notify(msg + " — הגיע למגבלת השליפה; ייתכנו עוד רשומות",
               Qgis.MessageLevel.Warning)
    else:
        _notify(msg)


def _layer_type(kind):
    if kind == "vector":
        return Qgis.BrowserLayerType.Vector
    return Qgis.BrowserLayerType.TableLayer   # NoGeometry attribute table


def _geom_icon(geom_type):
    """QGIS theme icon matching the real geometry type (point/line/polygon);
    a generic geometry icon when the type is unknown."""
    t = (geom_type or "").upper()
    if "POINT" in t:
        name = "/mIconPointLayer.svg"
    elif "LINE" in t or "CURVE" in t:
        name = "/mIconLineLayer.svg"
    elif "POLYGON" in t or "SURFACE" in t:
        name = "/mIconPolygonLayer.svg"
    else:
        name = "/mIconGeometryCollectionLayer.svg"
    return QgsApplication.getThemeIcon(name)


# --------------------------------------------------------------------------
# Root
# --------------------------------------------------------------------------

class OverRootItem(QgsDataCollectionItem):

    def __init__(self, parent=None):
        super().__init__(parent, "OVER", ROOT_PATH, PROVIDER_KEY)
        self.setToolTip("over.org.il — version-tracked public datasets")

    def over_query_scope(self):
        # Presence of this method marks the node as supporting the free-form
        # "שאילתה מתקדמת..." (see OverDataItemGuiProvider.populateContextMenu).
        return "OVER — כל מסד הנתונים"

    def createChildren(self):
        try:
            source_types = catalog_cache.source_types()
        except api.OverApiError as exc:
            return [QgsErrorItem(self, f"⚠ {exc}", ROOT_PATH + "/error")]
        children = []
        for st in source_types:
            label = SOURCE_TYPE_LABELS.get(st, st)
            children.append(
                SourceTypeItem(self, label, f"{ROOT_PATH}/{st}", st)
            )
        return children


# --------------------------------------------------------------------------
# source_type level
# --------------------------------------------------------------------------

class SourceTypeItem(QgsDataCollectionItem):

    def __init__(self, parent, label, path, source_type):
        super().__init__(parent, label, path, PROVIDER_KEY)
        self.source_type = source_type

    def over_query_scope(self):
        return self.name()

    def createChildren(self):
        try:
            orgs = catalog_cache.organizations(self.source_type)
        except api.OverApiError as exc:
            return [QgsErrorItem(self, f"⚠ {exc}", self.path() + "/error")]
        children = []
        for org in orgs:
            path = f"{self.path()}/{org}"
            children.append(
                OrganizationItem(self, org, path, self.source_type, org)
            )
        return children


# --------------------------------------------------------------------------
# organization level
# --------------------------------------------------------------------------

class OrganizationItem(QgsDataCollectionItem):

    def __init__(self, parent, title, path, source_type, org_name):
        super().__init__(parent, title, path, PROVIDER_KEY)
        self.source_type = source_type
        self.org_name = org_name

    def over_query_scope(self):
        return self.name()

    def createChildren(self):
        # Default: show only spatial datasets (this is a GIS tool). Users can
        # turn this off in the settings dialog to also see tabular datasets.
        spatial_only = QSettings().value(
            SETTINGS_SPATIAL_ONLY, True, type=bool)
        try:
            recs = catalog_cache.datasets(
                source_type=self.source_type,
                organization=self.org_name,
                spatial_only=spatial_only,
            )
        except api.OverApiError as exc:
            return [QgsErrorItem(self, f"⚠ {exc}", self.path() + "/error")]

        children = [DatasetItem(self, rec) for rec in recs[:PAGE_CAP]]
        if len(recs) > PAGE_CAP:
            children.append(QgsErrorItem(
                self,
                f"… {len(recs) - PAGE_CAP}+ more — use the search bar",
                self.path() + "/more",
            ))
        return children


# --------------------------------------------------------------------------
# dataset level
# --------------------------------------------------------------------------

class DatasetItem(QgsDataCollectionItem):

    def __init__(self, parent, rec):
        title = rec.get("title") or "—"
        est = rec.get("est_rows")
        label = f"{title} ({est})" if est else title
        path = f"{parent.path()}/{rec['dataset_id']}"
        super().__init__(parent, label, path, PROVIDER_KEY)
        self.rec = rec
        self.dataset_id = rec["dataset_id"]

    def createChildren(self):
        children = []

        # Route B leaf first: for spatial datasets this is the default load.
        if self.rec.get("is_spatial") and self.rec.get("spatial_table"):
            # Learn the real geometry type once (datastore columns are generic
            # `geometry`), cache it on the record, and use it for the leaf icon.
            if "spatial_geom_type" not in self.rec:
                self.rec["spatial_geom_type"] = datastore.geometry_type(
                    self.dataset_id, self.rec["spatial_table"],
                    schema=self.rec.get("spatial_schema"),
                    source_type=self.rec.get("source_type"))
            children.append(DatastoreLayerItem(
                self, self.rec, self.rec.get("spatial_geom_type")))

        # Route A: file resources from the latest version (+ history node).
        try:
            version = api.get_latest_version(self.dataset_id)
        except api.OverApiError as exc:
            if not children:
                children.append(
                    QgsErrorItem(self, f"⚠ {exc}", self.path() + "/error"))
            return children

        items = api.resources_from_version(version)
        loadable = [it for it in items if it["kind"] in ("vector", "table")]
        for it in loadable:
            children.append(self._file_item(it))

        if version.get("version_number", 1) > 1:
            children.append(PreviousVersionsItem(
                self, "גרסאות קודמות",
                self.path() + "/history", self.dataset_id,
                self.rec.get("title") or "—"))

        if not children:
            children.append(QgsErrorItem(
                self, "⚠ אין קבצים טעינים בגרסה זו",
                self.path() + "/unavailable"))
        return children

    def _file_item(self, item):
        """Route A leaf — a whole file loaded natively via its /vsicurl/ URI."""
        label = dataset_file_label(self.rec.get("title") or "—", item)
        return QgsLayerItem(
            self,
            label,
            self.path() + "/" + item["name"],
            item["uri"],
            _layer_type(item["kind"]),
            "ogr",
        )

    # The dataset node has no load action of its own — Route A files load by
    # double-clicking their leaves, and Route B (incl. the advanced query) is
    # on the "תצוגה נוכחית" leaf below.


# --------------------------------------------------------------------------
# Route B leaf (datastore_search_sql)
# --------------------------------------------------------------------------

class DatastoreLayerItem(QgsDataItem):
    """
    A spatial dataset loaded via Route B (datastore_search_sql). It is a
    Custom-type data item, NOT a QgsLayerItem: the browser routes a
    double-click on a *layer* item straight to "add layer" (which for our
    runtime-loaded data would be an empty memory layer) without ever
    consulting QgsDataItemGuiProvider. A Custom item has no layer to add, so
    the browser instead lets the GUI provider handle the double-click — which
    calls handleDoubleClick() here. It is a leaf (state forced to Populated).

    Loads are blocking (they call the API on the GUI thread, like the
    Locator), which is acceptable for the small, bbox-filtered result sets.
    """

    # Stable duck-typing marker: the GUI provider matches on this attribute
    # rather than isinstance, so it keeps working across plugin reloads (where
    # a browser-held item and the freshly-imported class differ by module).
    OVER_ROUTE_B = True

    def __init__(self, parent, rec, geom_type=None):
        title = rec.get("title") or rec["dataset_id"]
        name = f"🗺 {title} — תצוגה נוכחית"
        path = parent.path() + "/datastore"
        super().__init__(Qgis.BrowserItemType.Custom, parent, name, path,
                         PROVIDER_KEY)
        # Leaf: no children, so the browser shows no (pointless) expand arrow.
        self.setState(Qgis.BrowserItemState.Populated)
        self.setIcon(_geom_icon(geom_type))
        self.dataset_id = rec["dataset_id"]
        self.spatial_table = rec["spatial_table"]
        self.spatial_schema = rec.get("spatial_schema")
        self.spatial_columns = rec.get("spatial_columns") or []
        self.source_type = rec.get("source_type")
        self.layer_name = title
        self.setToolTip(
            "datastore_search_sql — נטען לזיכרון, ניתן לסינון לפי תצוגה")

    # -- interaction -------------------------------------------------------

    def sortKey(self):
        # The browser proxy sorts a dataset's children by sortKey (defaults to
        # the name). Return a low-codepoint key so this leaf sits at the TOP —
        # above the file leaves and the "גרסאות קודמות" node — instead of
        # being sorted to the bottom by its 🗺 emoji.
        return "\t"

    def handleDoubleClick(self):
        # This leaf IS the "current view" entry, so it always filters by the
        # canvas bbox. Loading without a bbox here would run a whole-layer
        # ST_AsGeoJSON, which for complex-polygon datasets is slow enough to
        # trip the server gateway.
        self._load(bbox=True)
        return True

    def actions(self, parent):
        a_view = QAction("טען — תצוגה נוכחית (bbox)", parent)
        a_view.triggered.connect(lambda: self._load(bbox=True))
        a_all = QAction("טען — כל השכבה", parent)
        a_all.triggered.connect(lambda: self._load(bbox=False))
        a_adv = QAction("שאילתה מתקדמת...", parent)
        a_adv.triggered.connect(self._open_advanced_query)
        return [a_view, a_all, a_adv]

    def _open_advanced_query(self):
        from .load_dialog import AdvancedQueryDialog
        from qgis.utils import iface
        dlg = AdvancedQueryDialog(self.layer_name, columns=self.spatial_columns,
                                  parent=iface.mainWindow())
        if dlg.exec():
            sel = dlg.selection()
            self._load(bbox=sel["bbox"], where=sel["where"])

    # -- load --------------------------------------------------------------

    def _load(self, bbox, where=None):
        try:
            from qgis.utils import iface
            bb = datastore.canvas_bbox_4326(iface) if bbox else None
            # Tag a viewport-filtered load so it is distinguishable in the
            # Layers panel from other data of the same dataset.
            name = (current_view_name(self.layer_name) if bbox
                    else self.layer_name)
            layer, truncated = datastore.load_layer(
                self.dataset_id, self.spatial_table, name,
                schema=self.spatial_schema, bbox=bb, where=where,
                source_type=self.source_type,
            )
            _notify_loaded(layer, truncated)
        except datastore.DatastoreError as exc:
            _notify(str(exc), Qgis.MessageLevel.Warning)
        except api.OverApiError as exc:
            _notify(f"שגיאת רשת: {exc}", Qgis.MessageLevel.Critical)


# --------------------------------------------------------------------------
# previous versions (lazy, Route A)
# --------------------------------------------------------------------------

class PreviousVersionsItem(QgsDataCollectionItem):

    def __init__(self, parent, name, path, dataset_id, title):
        super().__init__(parent, name, path, PROVIDER_KEY)
        self.dataset_id = dataset_id
        self.title = title

    def createChildren(self):
        try:
            payload = api.get_all_versions(self.dataset_id)
        except api.OverApiError as exc:
            return [QgsErrorItem(self, f"⚠ {exc}", self.path() + "/error")]

        versions = api.normalize_versions(payload)
        # Newest-first; skip the latest (already shown under the dataset).
        versions = sorted(
            versions,
            key=lambda v: v.get("version_number", 0),
            reverse=True,
        )[1:]

        children = []
        for ver in versions:
            num = ver.get("version_number")
            when = (ver.get("detected_at") or "")[:10]
            vpath = f"{self.path()}/v{num}"
            children.append(
                VersionItem(self, f"v{num} · {when}", vpath, ver, self.title)
            )
        return children


class VersionItem(QgsDataCollectionItem):
    """A single historical version -> its own resources as loadable leaves."""

    def __init__(self, parent, name, path, version, title):
        super().__init__(parent, name, path, PROVIDER_KEY)
        self.version = version
        self.title = title

    def createChildren(self):
        items = api.resources_from_version(self.version)
        loadable = [it for it in items if it["kind"] in ("vector", "table")]
        if not loadable:
            return [QgsErrorItem(self, "⚠ אין קבצים טעינים",
                                 self.path() + "/unavailable")]
        return [
            QgsLayerItem(self, dataset_file_label(self.title, it),
                         self.path() + "/" + it["name"],
                         it["uri"], _layer_type(it["kind"]), "ogr")
            for it in loadable
        ]


# --------------------------------------------------------------------------
# Provider registration entry point
# --------------------------------------------------------------------------

class OverDataItemProvider(QgsDataItemProvider):

    def name(self):
        return PROVIDER_KEY

    def capabilities(self):
        return QgsDataProvider.Net

    def createDataItem(self, path, parent_item):
        # Top-level call from QGIS uses an empty path.
        if not path:
            return OverRootItem(parent_item)
        return None


# --------------------------------------------------------------------------
# GUI provider — double-click handling (QgsDataItem.handleDoubleClick is not
# consulted by the browser for QgsLayerItems: the default action adds the
# item's URI as a layer, which for our runtime-loaded Route B leaf is an empty
# memory layer. The modern QgsDataItemGuiProvider.handleDoubleClick IS called
# first, so we route the double-click to the same _load as the right-click
# action and return True to suppress the empty-layer add.)
# --------------------------------------------------------------------------

class OverDataItemGuiProvider(QgsDataItemGuiProvider):

    def name(self):
        return PROVIDER_KEY

    def handleDoubleClick(self, item, context):
        # Duck-type on the marker attribute, not isinstance — see OVER_ROUTE_B.
        if getattr(item, "OVER_ROUTE_B", False):
            item.handleDoubleClick()   # current-view load
            return True                # suppress the default add
        return False

    def populateContextMenu(self, item, menu, selectedItems, context):
        # Collection nodes (root / source_type / organization) expose a
        # free-form SQL query over the whole database. Duck-typed via
        # over_query_scope so it survives plugin reloads.
        scope = getattr(item, "over_query_scope", None)
        if callable(scope):
            act = QAction("שאילתה מתקדמת...", menu)
            label = scope()
            act.triggered.connect(
                lambda checked=False, s=label: _open_free_query(s))
            menu.addAction(act)


def _open_free_query(scope_label):
    """Open the free-SQL dialog and load its result as a layer."""
    from .load_dialog import FreeQueryDialog
    from qgis.utils import iface
    dlg = FreeQueryDialog(scope_label, parent=iface.mainWindow())
    if not dlg.exec():
        return
    sql, name = dlg.values()
    if not sql:
        return
    try:
        layer, truncated = datastore.load_free_query(sql, name)
        _notify_loaded(layer, truncated)
    except datastore.DatastoreError as exc:
        _notify(str(exc), Qgis.MessageLevel.Warning)
    except api.OverApiError as exc:
        _notify(f"שגיאת רשת: {exc}", Qgis.MessageLevel.Critical)

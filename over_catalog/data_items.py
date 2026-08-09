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

# Tree display mode (replaces the old spatial_only / show_all_files toggles):
#   1 = spatial only     — only datasets/files that are spatial layers.
#   2 = all openable      — everything QGIS/GDAL can open (spatial + CSV/XLS/…).
#   3 = all files         — every file; non-openable ones get a warning marker.
SETTINGS_DISPLAY_MODE = "over_catalog/display_mode"
DISPLAY_MODE_DEFAULT = 2


def display_mode():
    """Current tree display mode (1/2/3); defaults to 2 (all openable)."""
    try:
        return int(QSettings().value(
            SETTINGS_DISPLAY_MODE, DISPLAY_MODE_DEFAULT))
    except (TypeError, ValueError):
        return DISPLAY_MODE_DEFAULT


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

    Every file keeps its extension (`.geojson`, `.csv`, `.gpkg`, ...) so its
    type is visible in the tree and the loaded layer stays distinguishable
    from same-dataset siblings.
    """
    ext = (item.get("ext") or item.get("gdal_format")
           or item.get("fmt") or "")
    ext = str(ext).lower().lstrip(".")
    if not ext:
        ext = "csv" if item.get("kind") == "table" else "dat"
    return f"{title}.{ext}"


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


def _make_child(parent, item, title, warn=False):
    """
    Build the tree child for one resource:
      * container  -> expandable node listing its sublayers on demand;
      * vector / raster / table -> a native, loadable QgsLayerItem;
      * style/definition files, and anything QGIS can't open -> a download
        link (the non-openable ones flagged with a warning in "all files"
        mode).
    """
    if item.get("container"):
        return ContainerFileItem(parent, item, title)

    layer_kind = item.get("layer_kind")
    if layer_kind in ("vector", "raster", "table"):
        label = dataset_file_label(title, item)
        path = parent.path() + "/" + str(item["name"])
        url = item.get("download_url")
        if layer_kind == "raster":
            return OverFileLayerItem(parent, label, path, item["uri"],
                                     Qgis.BrowserLayerType.Raster, "gdal", url)
        btype = (Qgis.BrowserLayerType.Vector if layer_kind == "vector"
                 else Qgis.BrowserLayerType.TableLayer)
        return OverFileLayerItem(parent, label, path, item["uri"], btype,
                                 "ogr", url)

    # Style/definition files (SLD/QML/...) and any non-openable resource.
    return OtherFileItem(parent, item, warn=warn)


def _file_children(parent, items, title, mode):
    """
    File-leaf children for a dataset or version, filtered by display `mode`:
      1 spatial only -> only spatial-category resources;
      2 all openable -> spatial + data (hide non-openable "other");
      3 all files    -> everything, non-openable flagged with a warning.
    """
    children = []
    for it in items:
        if not it.get("download_url"):
            continue  # odata / nothing to fetch
        category = it.get("category", "other")
        if mode <= 1 and category != "spatial":
            continue
        if mode == 2 and category == "other":
            continue
        warn = (mode >= 3 and category == "other")
        child = _make_child(parent, it, title, warn=warn)
        if child is not None:
            children.append(child)
    return children


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


class OverFileLayerItem(QgsLayerItem):
    """
    A Route A file leaf: loaded on double-click like any QgsLayerItem, but also
    offering a right-click "copy server location" — the file's raw download URL
    — so the same file can be opened in other tools.
    """

    def __init__(self, parent, name, path, uri, layer_type, provider, url):
        super().__init__(parent, name, path, uri, layer_type, provider)
        self.url = url or ""
        if self.url:
            self.setToolTip(self.url)

    def actions(self, parent):
        if not self.url:
            return []
        act = QAction("העתק מיקום הקובץ בשרת", parent)
        act.triggered.connect(self._copy_url)
        return [act]

    def _copy_url(self):
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.clipboard().setText(self.url)
        _notify("מיקום הקובץ הועתק ללוח")


class ContainerFileItem(QgsDataCollectionItem):
    """
    A file that may hold several sublayers (GPKG / GeoParquet / FlatGeobuf /
    GML / KML / ZIP). Shown as an expandable node; on expand it lists the
    container's sublayers via QgsProviderRegistry.querySublayers WITHOUT
    resolving geometry types (that would scan features — slow/hanging over
    /vsicurl/ for big files). Each sublayer is a native, loadable QgsLayerItem
    (uri = .../file|layername=<name>). For a zipped shapefile the archive's
    single vector layer is listed (the .dbf/.shx/.prj sidecars fold into it).
    Driver selection relies on the file extension.
    """

    def __init__(self, parent, item, title):
        label = dataset_file_label(title, item)
        super().__init__(parent, label,
                         parent.path() + "/" + str(item["name"]), PROVIDER_KEY)
        self.file_uri = item["uri"]
        self.download_url = item.get("download_url") or ""
        self.setToolTip(self.download_url)

    def actions(self, parent):
        if not self.download_url:
            return []
        act = QAction("העתק מיקום הקובץ בשרת", parent)
        act.triggered.connect(self._copy_url)
        return [act]

    def _copy_url(self):
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.clipboard().setText(self.download_url)
        _notify("מיקום הקובץ הועתק ללוח")

    def createChildren(self):
        from qgis.core import QgsProviderRegistry, QgsWkbTypes
        try:
            subs = QgsProviderRegistry.instance().querySublayers(self.file_uri)
        except Exception as exc:  # network / driver failure
            return [QgsErrorItem(self, f"⚠ {exc}", self.path() + "/error")]
        if not subs:
            return [QgsErrorItem(self, "⚠ לא נמצאו שכבות בקובץ",
                                 self.path() + "/empty")]
        children = []
        for s in subs:
            name = s.name() or "layer"
            # Sublayer leaf: its "server location" is the container's file URL.
            leaf = OverFileLayerItem(self, name, self.path() + "/" + name,
                                     s.uri(), Qgis.BrowserLayerType.Vector,
                                     s.providerKey() or "ogr",
                                     self.download_url)
            leaf.setIcon(_geom_icon(QgsWkbTypes.displayString(s.wkbType())))
            children.append(leaf)
        return children


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
        # Display mode (settings dialog) decides which datasets show here:
        #   1 spatial only -> only spatial datasets;
        #   2 all openable -> datasets with any QGIS-openable content;
        #   3 all files    -> every dataset, even ones with no openable file.
        # A non-spatial dataset's table-fallback context-menu action (see
        # DatasetItem.over_table_fallback) is therefore only reachable in
        # modes 2/3 — in mode 1 the dataset never becomes a DatasetItem here.
        mode = display_mode()
        try:
            recs = catalog_cache.datasets(
                source_type=self.source_type,
                organization=self.org_name,
                mode=mode,
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
        mode = display_mode()
        title = self.rec.get("title") or "—"

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
        version, version_error = None, None
        try:
            version = api.get_latest_version(self.dataset_id)
        except api.OverApiError as exc:
            version_error = exc

        if version is not None:
            items = api.resources_from_version(version)
            children.extend(_file_children(self, items, title, mode))
            if version.get("version_number", 1) > 1:
                children.append(PreviousVersionsItem(
                    self, "גרסאות קודמות",
                    self.path() + "/history", self.dataset_id, title))

        if not children:
            # No spatial leaf and no visible files. If this is a non-spatial
            # datastore-only dataset, surface its info-table fallback (also on
            # the right-click menu — see over_table_fallback); otherwise show
            # the appropriate error.
            if self.over_table_fallback() is not None:
                children.append(TableFallbackItem(
                    self, self.path() + "/table", self.rec))
            elif version_error is not None:
                children.append(QgsErrorItem(
                    self, f"⚠ {version_error}", self.path() + "/error"))
            else:
                children.append(QgsErrorItem(
                    self, "⚠ אין קבצים טעינים בגרסה זו",
                    self.path() + "/unavailable"))
        return children

    # The dataset node has no load action of its own — Route A files load by
    # double-clicking their leaves, and Route B (incl. the advanced query) is
    # on the "תצוגה נוכחית" leaf below. The exception is over_table_fallback:
    # a dataset with no geometry column (bare lat/lon or X/Y coordinate
    # fields, not real PostGIS geometry — see catalog_cache._table_is_spatial)
    # has no leaf offering its raw datastore table, so that load action lives
    # on the context menu (OverDataItemGuiProvider.populateContextMenu) and,
    # when the dataset has no other children, on the TableFallbackItem leaf.

    def over_table_fallback(self):
        """
        This dataset's catalog record, if it has a datastore table but no
        geometry column — used by OverDataItemGuiProvider.populateContextMenu
        to add a "load as info table" action. None otherwise.
        """
        if self.rec.get("is_spatial") or not self.rec.get("primary_table"):
            return None
        return self.rec


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

    # Stable duck-typing markers for the GUI provider (match on attribute, not
    # isinstance, so they survive plugin reloads). OVER_DBLCLICK -> the GUI
    # provider routes a double-click to handleDoubleClick().
    OVER_ROUTE_B = True
    OVER_DBLCLICK = True

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
# Non-spatial "info table" fallback leaf — shown for a datastore-only dataset
# that has no geometry column and no file resources (so it gets neither a
# Route A nor a Route B leaf). It loads the raw datastore table as an
# attribute-only layer; the same action is also on the dataset's right-click
# menu (see over_table_fallback / populateContextMenu).
# --------------------------------------------------------------------------

class TableFallbackItem(QgsDataItem):

    OVER_DBLCLICK = True

    def __init__(self, parent, path, rec):
        super().__init__(Qgis.BrowserItemType.Custom, parent,
                         "📋 טען כטבלת מידע (מאגר נתונים ללא שכבה מרחבית)",
                         path, PROVIDER_KEY)
        self.setState(Qgis.BrowserItemState.Populated)
        self.setIcon(QgsApplication.getThemeIcon("/mIconTableLayer.svg"))
        self.rec = rec
        self.setToolTip("לחיצה כפולה טוענת את טבלת ה-datastore כטבלת מאפיינים")

    def handleDoubleClick(self):
        _load_table_fallback(self.rec)
        return True

    def actions(self, parent):
        a_load = QAction("טען כטבלת מידע", parent)
        a_load.triggered.connect(lambda: _load_table_fallback(self.rec))
        a_query = QAction("שאילתה מתקדמת (טבלת מידע)...", parent)
        a_query.triggered.connect(
            lambda: _open_table_fallback_query(self.rec))
        return [a_load, a_query]


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
        children = _file_children(self, items, self.title, display_mode())
        if not children:
            return [QgsErrorItem(self, "⚠ אין קבצים טעינים",
                                 self.path() + "/unavailable")]
        return children


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
        # Duck-typed: any OVER Custom leaf that wants to handle its own
        # double-click sets OVER_DBLCLICK (Route B leaf loads; OtherFileItem
        # opens the download URL). The browser does not consult a plain
        # QgsDataItem.handleDoubleClick for us, so we route it here.
        if getattr(item, "OVER_DBLCLICK", False):
            return bool(item.handleDoubleClick())
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

        # Dataset nodes with a datastore table but no geometry column (see
        # DatasetItem.over_table_fallback) — load that raw table without
        # depending on a lazily-populated tree leaf.
        table_fallback = getattr(item, "over_table_fallback", None)
        if callable(table_fallback):
            rec = table_fallback()
            if rec:
                a_load = QAction(
                    "טען כטבלת מידע (מאגר נתונים בלבד, ללא שכבה מרחבית)", menu)
                a_load.triggered.connect(
                    lambda checked=False, r=rec: _load_table_fallback(r))
                menu.addAction(a_load)

                a_query = QAction("שאילתה מתקדמת (טבלת מידע)...", menu)
                a_query.triggered.connect(
                    lambda checked=False, r=rec: _open_table_fallback_query(r))
                menu.addAction(a_query)


def _load_table_fallback(rec, where=None):
    """Load a non-spatial dataset's raw datastore table as an attribute-only layer."""
    title = rec.get("title") or rec["dataset_id"]
    try:
        layer, truncated = datastore.load_table(
            rec["dataset_id"], rec["primary_table"], title,
            schema=rec.get("primary_schema"), where=where,
            source_type=rec.get("source_type"),
        )
        _notify_loaded(layer, truncated)
    except datastore.DatastoreError as exc:
        _notify(str(exc), Qgis.MessageLevel.Warning)
    except api.OverApiError as exc:
        _notify(f"שגיאת רשת: {exc}", Qgis.MessageLevel.Critical)


def _open_table_fallback_query(rec):
    """WHERE-only advanced query (no bbox — no geometry column to filter by)."""
    from .load_dialog import AdvancedQueryDialog
    from qgis.utils import iface
    title = rec.get("title") or rec["dataset_id"]
    dlg = AdvancedQueryDialog(title, columns=rec.get("primary_columns") or [],
                              parent=iface.mainWindow(), show_bbox=False)
    if dlg.exec():
        _load_table_fallback(rec, where=dlg.selection()["where"])


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


# --------------------------------------------------------------------------
# "Other" file leaf — a resource QGIS can't open as a layer (symbology zip,
# PDF, XML, ...). Shown only when the "show all files" setting is on; it is a
# download link, not a layer: double-click / right-click opens or copies the
# URL. Custom item so the browser doesn't try to add it as a layer.
# --------------------------------------------------------------------------

class OtherFileItem(QgsDataItem):

    OVER_DBLCLICK = True

    def __init__(self, parent, item, warn=False):
        fmt = (item.get("fmt") or item.get("gdal_format")
               or item.get("ext") or "file").upper()
        name = f"{'⚠ ' if warn else ''}{item['name']} · {fmt}"
        path = parent.path() + "/other/" + str(item["name"])
        super().__init__(Qgis.BrowserItemType.Custom, parent, name, path,
                         PROVIDER_KEY)
        self.setState(Qgis.BrowserItemState.Populated)
        icon = "/mIconWarning.svg" if warn else "/mIconFile.svg"
        self.setIcon(QgsApplication.getThemeIcon(icon))
        self.url = item["download_url"]
        tip = f"{self.url}\n(לא נפתח כשכבה ב-QGIS — הורדה/פתיחה בדפדפן)"
        if warn:
            tip = "⚠ QGIS לא יודע לפתוח קובץ מסוג זה כשכבה.\n" + tip
        self.setToolTip(tip)

    def handleDoubleClick(self):
        self._open()
        return True

    def actions(self, parent):
        a_open = QAction("פתח / הורד בדפדפן", parent)
        a_open.triggered.connect(self._open)
        a_copy = QAction("העתק מיקום הקובץ בשרת", parent)
        a_copy.triggered.connect(self._copy)
        return [a_open, a_copy]

    def _open(self):
        from qgis.PyQt.QtGui import QDesktopServices
        from qgis.PyQt.QtCore import QUrl
        QDesktopServices.openUrl(QUrl(self.url))

    def _copy(self):
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.clipboard().setText(self.url)
        _notify("מיקום הקובץ הועתק ללוח")

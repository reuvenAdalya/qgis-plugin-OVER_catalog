# -*- coding: utf-8 -*-
"""
Layer 3 — global search bar integration (QgsLocatorFilter).

The Locator is a pure NAVIGATION tool: typing in the QGIS search bar searches
the cached catalog, and selecting a result REVEALS that dataset in the Browser
tree (expanding + selecting its node) rather than loading anything. All actual
loading happens from the tree (double-click / right-click "טען...") so the
user can pick Route A/B, bbox, versions, etc. there.
"""

from qgis.core import (
    QgsLocatorFilter,
    QgsLocatorResult,
    QgsBrowserModel,
    QgsMessageLog,
    Qgis,
)
import time

from qgis.PyQt.QtCore import Qt, QItemSelectionModel, QModelIndex
from qgis.PyQt.QtWidgets import (
    QDockWidget, QTreeView, QApplication, QAbstractItemView,
)

from . import api
from . import catalog_cache
from . import data_items


class OverLocatorFilter(QgsLocatorFilter):
    """Type in the QGIS search bar -> reveal over.org.il datasets in the tree."""

    # QGIS runs fetchResults on a clone in a worker thread and later calls
    # triggerResult on that SAME clone. If the clone's Python wrapper is
    # garbage-collected in between, our overridden triggerResult is never
    # dispatched (the C++ base runs instead) and selecting a result does
    # nothing. Keep every clone referenced so its Python object stays alive.
    _clones = []

    def name(self):
        return "over"

    def displayName(self):
        return self.tr("OVER — over.org.il datasets")

    def prefix(self):
        # Typing "over <text>" restricts the search bar to this filter.
        return "over"

    def clone(self):
        f = OverLocatorFilter()
        OverLocatorFilter._clones.append(f)
        # Bound the retained clones so a long session doesn't grow unbounded.
        del OverLocatorFilter._clones[:-8]
        return f

    def priority(self):
        return QgsLocatorFilter.Priority.Medium

    # -- search ------------------------------------------------------------

    def fetchResults(self, query, context, feedback):
        """
        Called by QGIS on each keystroke. UUID input resolves to that one
        dataset (if it is in the catalog); free text is a local substring
        search over cached titles (the REST endpoint has no text filter).
        """
        text = (query or "").strip()
        if len(text) < 2:
            return

        try:
            if api.looks_like_uuid(text):
                rec = catalog_cache.get(text)
                if rec:
                    self._emit_result(rec, feedback)
                return

            for rec in catalog_cache.search(text, limit=25):
                if feedback.isCanceled():
                    return
                self._emit_result(rec, feedback)

        except api.OverApiError as exc:
            QgsMessageLog.logMessage(
                f"OVER search failed: {exc}", "OVER", Qgis.MessageLevel.Warning
            )

    def _emit_result(self, rec, feedback):
        if feedback.isCanceled():
            return
        result = QgsLocatorResult()
        result.filter = self
        result.displayString = rec.get("title") or rec["dataset_id"]
        # Carry the Browser-tree path so triggerResult can reveal the node.
        result.userData = data_items.dataset_browser_path(rec)
        self.resultFetched.emit(result)

    # -- reveal on select --------------------------------------------------

    def triggerResult(self, result):
        """Selecting a result: expand + select the dataset node in the Browser."""
        path = result.userData
        if not self._reveal_in_browser(path):
            self._notify(
                self.tr("Could not locate the dataset in the Browser panel. "
                        "Open the OVER tree once, then try again."),
                Qgis.MessageLevel.Warning,
            )

    def _reveal_in_browser(self, path):
        """
        Resolve `path` to its Browser index and expand/select/scroll to it.
        Returns False if the Browser panel or the node could not be found.

        The tree is lazily populated, so a cold `findPath` on the full path
        fails — findPath only matches items already fetched. We descend the
        ancestor path prefixes, fetching each level (via the model, NOT
        QgsDataItem.populate) and pumping the event loop so the next prefix
        becomes findable. The intermediate levels (source_type /
        organization / dataset list) are served from the in-memory
        catalog_cache, so this is quick and hits no network.

        Crucially, the model used is the VIEW's own source model — each
        browser model instance builds its own OverRootItem, so descending
        `iface.browserModel()` would populate a tree the visible view never
        shows, and the indexes would not map to it.
        """
        from qgis.utils import iface

        view = self._browser_view(iface)
        if view is None:
            return False

        proxy = view.model()
        # The dock proxy's source model IS iface.browserModel() (verified: same
        # C++ object). We must fetch/findPath via iface.browserModel() though —
        # proxy.sourceModel() comes back typed as a plain QAbstractItemModel,
        # which the static QgsBrowserModel.findPath rejects.
        src = iface.browserModel()

        # Ancestor path prefixes: "over:", "over:/<st>", ".../<org>", full.
        segments = path.split("/")
        prefixes = []
        acc = segments[0]
        prefixes.append(acc)
        for seg in segments[1:]:
            acc = f"{acc}/{seg}"
            prefixes.append(acc)

        index = QModelIndex()
        for prefix in prefixes:
            index = QgsBrowserModel.findPath(
                src, prefix, Qt.MatchFlag.MatchExactly)
            if not index or not index.isValid():
                return False
            # Fetch every level's children, INCLUDING the target dataset's —
            # so the node opens with its resources already populated. The
            # target's fetch is the one that hits /versions/latest (network).
            self._fetch_children(src, index)

        target = (proxy.mapFromSource(index)
                  if hasattr(proxy, "mapFromSource") else index)
        if not target.isValid():
            return False

        # Expand every ancestor so the node is visible, expand the dataset
        # itself so its resources show, then select + scroll to it.
        parent = target.parent()
        while parent.isValid():
            view.setExpanded(parent, True)
            parent = parent.parent()
        view.setExpanded(target, True)
        # Center the node in the viewport rather than the default "just make
        # it visible", which leaves it stuck at the bottom edge.
        view.scrollTo(target,
                      QAbstractItemView.ScrollHint.PositionAtCenter)
        view.selectionModel().setCurrentIndex(
            target,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        return True

    @staticmethod
    def _fetch_children(model, index, timeout_s=8.0):
        """
        Populate `index`'s children via the model and wait — with a real
        wall-clock budget, not just a spin — for the background populate to
        deliver them. The browser fetches QgsDataItem children on a worker
        thread, so a tight processEvents spin returns before the rows land;
        the short sleeps give the thread time while processEvents delivers
        its completion signal. Our levels are catalog_cache-backed, so this
        normally resolves in well under a second.
        """
        if not model.canFetchMore(index):
            return
        model.fetchMore(index)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            QApplication.processEvents()
            if model.rowCount(index) > 0:
                return
            time.sleep(0.02)

    @staticmethod
    def _browser_view(iface):
        """Find the Browser dock's tree view (first dock named 'Browser')."""
        win = iface.mainWindow()
        for dock in win.findChildren(QDockWidget):
            name = (dock.objectName() or "").lower()
            title = (dock.windowTitle() or "").lower()
            if "browser" in name or "browser" in title or "דפדפן" in title:
                view = dock.findChild(QTreeView)
                if view is not None:
                    return view
        return None

    # -- helpers -----------------------------------------------------------

    def _notify(self, message, level):
        QgsMessageLog.logMessage(message, "OVER", level)
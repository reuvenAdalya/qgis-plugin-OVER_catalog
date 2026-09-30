# -*- coding: utf-8 -*-
"""
Layer 6 — bringing a project's OVER layers up to the latest version.

over.org.il keeps every version of a file in the same bucket folder and changes
only the file NAME, which carries a content hash:

    .../datasets/<dataset id>/v1/8cef55fe_geojson.gz     (version 1)
    .../datasets/<dataset id>/v1/8ef2188b_geojson.gz     (version 3)

A saved project therefore stays on whatever version it was saved with. It does
not break — superseded files keep being served — it simply goes stale in
silence, which is the worse failure: on a real 12-layer project four layers
turned out to be versions behind. The newest name can only be learnt from the
API: no endpoint serves "the latest file" (they are all keyed by version id)
and the public bucket cannot be listed.

This module is the single engine behind both front ends — the plugin's dialog
and the Processing algorithm. It only reads the project; nothing here changes a
layer until apply_update() is called.

Two things the layer rewrite has to get right, both found by testing rather
than assumption:

  * A LAYER FILTER IS LOST. setDataSource rebuilds the provider, which clears
    subsetString. Measured: a filter of `"objectId" < 50` (49 features) came
    back empty (138 features) after the swap. It is captured before and
    re-applied after — silently losing a filter would quietly change what the
    map shows. Everything else survives: style, labels, opacity, blend mode,
    scale visibility, custom properties, layer variables, and the layer ID,
    so joins and project references keep working.

  * FIELD NAMES CAN CHANGE BETWEEN VERSIONS. The same dataset shipped
    ['objectId', 'התרעה', 'מרחב'] in v1/v2 and ['objectId', 'hatraha',
    'merchav'] in v3. A style or filter built on the old names survives the
    swap syntactically and then matches nothing. So the new file's schema is
    compared with the layer's before anything is applied, and the difference is
    reported. It is only ever a warning — replacing the symbology stays the
    user's explicit choice.
"""

import re

from qgis.core import Qgis, QgsMessageLog, QgsProject, QgsVectorLayer

from . import api

# The bucket host and layout are stable; only the file name carries the
# version. Matched by PATTERN rather than the current bucket id, so moving to
# another r2.dev bucket or a custom domain does not silently stop matching.
BUCKET_RE = re.compile(
    r"https://pub-[0-9a-z]+\.r2\.dev/datasets/"
    r"([0-9a-fA-F-]{36})/v\d+/([^/?|]+)"
)

# A stored file name is "<content hash>_<resource suffix>", e.g.
# "8cef55fe_geojson.gz". The suffix identifies the resource across versions;
# the hash is what changes.
_NAME_RE = re.compile(r"^([0-9a-fA-F]+)_(.+)$")

# Row states.
UPDATE = "update"      # a newer version exists
CURRENT = "current"    # already on the latest
MISSING = "missing"    # the resource is gone from the latest version
ERROR = "error"        # the dataset could not be resolved


def _log(msg, level=Qgis.MessageLevel.Warning):
    QgsMessageLog.logMessage(msg, "OVER", level)


def resource_suffix(file_name):
    """'8cef55fe_geojson.gz' -> 'geojson.gz'; an unhashed name passes through."""
    match = _NAME_RE.match(file_name or "")
    return match.group(2) if match else (file_name or "")


def layer_path(layer, project=None):
    """
    'group / subgroup / layer name' — the layer as the user sees it in the
    tree. Two layers of one dataset are distinguishable only by where they sit,
    so the group matters as much as the name.
    """
    project = project or QgsProject.instance()
    node = project.layerTreeRoot().findLayer(layer.id())
    groups = []
    parent = node.parent() if node else None
    while parent is not None and parent.parent() is not None:
        groups.append(parent.name())
        parent = parent.parent()
    groups.reverse()          # innermost was collected first
    return " / ".join(groups + [layer.name()])


class LayerUpdate:
    """One row of the scan: a layer, and what the server has for it."""

    def __init__(self, layer, dataset_id, old_file):
        self.layer = layer
        self.layer_id = layer.id()
        self.path = layer_path(layer)
        self.dataset_id = dataset_id
        self.old_file = old_file
        self.new_file = None
        self.old_version = None
        self.new_version = None
        self.detected_at = ""        # when the LATEST version was detected
        self.old_detected = ""       # when the version in use was detected
        self.old_rows = None
        self.new_rows = None
        self.status = ERROR
        self.message = ""
        self.missing_fields = []     # fields the new file no longer publishes
        self.sym_url = None          # symbology bundle of the latest version

    @property
    def selectable(self):
        return self.status == UPDATE

    def new_source(self):
        """
        The layer's URI with only the file name swapped.

        A filtered layer carries its filter inside source() as
        `|subset=<expr>`. That is dropped here and re-applied afterwards by
        apply_update: baking a filter into the URI makes the layer fail to
        open outright when the new version renamed the field it references,
        whereas re-applying it separately lets the layer load and the failure
        be reported.
        """
        if not self.new_file:
            return None
        source = (self.layer.source() or "").replace(self.old_file,
                                                     self.new_file)
        return re.sub(r"\|subset=.*$", "", source, flags=re.S)

    def summary(self):
        if self.status == CURRENT:
            return f"v{self.old_version}" if self.old_version else "current"
        if self.status == UPDATE:
            return f"v{self.old_version} → v{self.new_version}"
        return self.message


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def over_layers(project=None):
    """[(layer, dataset_id, file name)] for every layer served from the bucket."""
    project = project or QgsProject.instance()
    found = []
    for layer in project.mapLayers().values():
        match = BUCKET_RE.search(layer.source() or "")
        if match:
            found.append((layer, match.group(1), match.group(2)))
    return found


def _versions(dataset_id, cache):
    """The dataset's version list, newest first; [] if it cannot be read."""
    if dataset_id in cache:
        return cache[dataset_id]
    url = f"{api.API_BASE}/datasets/{dataset_id}/versions"
    try:
        versions = api.normalize_versions(api.fetch_json(url))
    except api.OverApiError as exc:
        _log(f"refresh: {dataset_id}: {exc}")
        versions = []
    versions.sort(key=lambda v: v.get("version_number") or 0, reverse=True)
    cache[dataset_id] = versions
    return versions


def _files_of(version):
    """{resource suffix: (file name, download_url)} for one version."""
    out = {}
    for res in (version.get("resources") or []):
        url = res.get("download_url") or ""
        match = BUCKET_RE.search(url)
        if match:
            out[resource_suffix(match.group(2))] = (match.group(2), url)
    return out


def _rows_of(version):
    return (version.get("change_summary") or {}).get("total_rows")


def scan(project=None, check_schema=True, progress=None):
    """
    Look at every OVER layer in the project and report what the server has.

    check_schema also opens each candidate's NEW file to compare field names —
    the only way to warn that a style or filter is about to stop matching. It
    costs one download per updatable layer, so it is optional; the version
    comparison itself is metadata-only.

    `progress` is called as progress(done, total, text).
    """
    project = project or QgsProject.instance()
    targets = over_layers(project)
    cache = {}
    results = []

    for index, (layer, dataset_id, old_file) in enumerate(targets):
        row = LayerUpdate(layer, dataset_id, old_file)
        if progress:
            progress(index, len(targets), row.path)

        versions = _versions(dataset_id, cache)
        if not versions:
            row.status = ERROR
            row.message = "לא ניתן לקרוא את גרסאות המאגר"
            results.append(row)
            continue

        suffix = resource_suffix(old_file)
        latest = versions[0]
        row.new_version = latest.get("version_number")
        row.detected_at = (latest.get("detected_at") or "")[:10]
        row.new_rows = _rows_of(latest)
        row.sym_url = _symbology_url(latest)

        # Which version is this layer actually on? The one whose resources
        # include the file the layer points at.
        for version in versions:
            if any(name == old_file for name, _ in _files_of(version).values()):
                row.old_version = version.get("version_number")
                row.old_rows = _rows_of(version)
                row.old_detected = (version.get("detected_at") or "")[:10]
                break

        newest = _files_of(latest).get(suffix)
        if not newest:
            row.status = MISSING
            row.message = "המשאב אינו קיים בגרסה האחרונה"
            results.append(row)
            continue

        row.new_file = newest[0]
        if row.new_file == old_file:
            row.status = CURRENT
            results.append(row)
            continue

        row.status = UPDATE
        if check_schema:
            row.missing_fields = _schema_drift(layer, row.new_source())
        results.append(row)

    if progress:
        progress(len(targets), len(targets), "")
    return results


def _symbology_url(version):
    try:
        from . import symbology
        bundle = symbology.bundle_from_items(api.resources_from_version(version))
        return bundle.get("download_url") if bundle else None
    except Exception as exc:          # symbology is a bonus, never a blocker
        _log(f"refresh: could not resolve symbology: {exc}",
             Qgis.MessageLevel.Info)
        return None


def _schema_drift(layer, new_source):
    """Field names the layer has today that the NEW file no longer publishes."""
    if not new_source:
        return []
    try:
        probe = QgsVectorLayer(new_source, "probe", layer.providerType() or "ogr")
        if not probe.isValid():
            return []
        old = [f.name() for f in layer.fields()]
        new = {f.name() for f in probe.fields()}
        return [name for name in old if name not in new]
    except Exception as exc:
        _log(f"refresh: schema check failed: {exc}", Qgis.MessageLevel.Info)
        return []


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------

def apply_update(row, with_symbology=False):
    """
    Repoint one layer at its latest file. Returns (ok, message).

    Only the data source changes. The layer filter is carried over by hand
    (setDataSource clears it); the style is replaced only when the caller asks.
    A new file that fails to open is rolled back rather than left broken.
    """
    if row.status != UPDATE or not row.new_file:
        return False, "אין עדכון זמין"

    layer = row.layer
    old_source = layer.source()
    new_source = row.new_source()
    subset = layer.subsetString()          # cleared by setDataSource

    layer.setDataSource(new_source, layer.name(), layer.providerType() or "ogr")
    if not layer.isValid():
        layer.setDataSource(old_source, layer.name(),
                            layer.providerType() or "ogr")
        if subset:
            layer.setSubsetString(subset)
        return False, "הקובץ החדש לא נפתח — הוחזר למקור הקודם"

    note = ""
    if subset and not layer.setSubsetString(subset):
        note = " (הסינון לא הוחל מחדש — ייתכן ששם השדה השתנה)"

    if with_symbology and row.sym_url:
        try:
            from . import symbology
            outcome = symbology.apply_to_layer(layer, row.sym_url)
            if outcome not in (symbology.STYLED, symbology.STYLED_LYR,
                               symbology.ALIASES):
                note += " (הסימבולוגיה לא הוחלה)"
        except Exception as exc:
            _log(f"refresh: symbology failed for {layer.name()}: {exc}")
            note += " (הסימבולוגיה נכשלה)"

    layer.triggerRepaint()
    return True, f"v{row.old_version} → v{row.new_version}{note}"

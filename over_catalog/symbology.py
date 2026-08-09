# -*- coding: utf-8 -*-
"""
Layer 5 — the GovMap symbology bundle shipped with most datasets.

Nearly every over.org.il dataset carries a `_symbology` ZIP next to its data
files:

    _symbology.zip
    ├── symbology_index.csv    sld_file, rule_count, icon_count, style_name ...
    ├── <layer>_<id>.sld       the style itself
    ├── icons/*.svg            SVG markers, referenced RELATIVELY by the SLD
    ├── <layer>_fields.csv     machine_name / hebrew_alias / is_served
    └── README.txt

Applying it is mostly trivial: the SLD's <PropertyName> references are the same
machine names the data files use as column headers, so QGIS's own
loadSldStyle() resolves them directly — no field mapping needed. (Verified
against a live sample: 17 of 18 bundles applied cleanly, on both the file
route and the datastore route.)

Two things do need handling, and they are what this module exists for:

  * ICONS. The SLD points at `icons/x.svg` relative to itself. QGIS stores the
    path verbatim and never resolves it, so the marker silently fails to draw.
    The bundle is therefore extracted to a cache directory under the user's
    QGIS profile and the hrefs are rewritten to absolute paths. The cache is
    persistent on purpose: those absolute paths get written into a saved
    project, so a temp directory would leave the project broken later.

  * A STYLE THAT MATCHES NOTHING. Some GovMap SLDs filter on a field that is
    not actually published — e.g. a numeric code field where only its text
    twin ships. Such a style loads "successfully" but every rule matches zero
    features, so the whole layer renders unstyled: worse than leaving it
    alone. So the style's field references are checked against the layer
    before it is kept.

    Deliberately NO fuzzy / prefix field matching. A near-miss name
    (`stat1tendr` vs the published `stat1tendrtext`) is a DIFFERENT field, and
    guessing produced exactly the empty style described above.
"""

import csv
import hashlib
import io
import os
import re

from qgis.core import QgsApplication, QgsMessageLog, Qgis

# Fields an SLD may reference that are GovMap service internals rather than
# published attributes; their absence is not a reason to reject a style.
GEOM_PSEUDO_FIELDS = {
    "shape", "shape_area", "shape_length", "geometry", "the_geom",
}

_PROPERTY_RE = re.compile(r"<(?:ogc:)?PropertyName>([^<]+)</(?:ogc:)?PropertyName>")
_HREF_RE = re.compile(r'xlink:href="([^"]+)"')
_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:|/|https?://|file:)")


def _log(msg, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(msg, "OVER", level)


# --------------------------------------------------------------------------
# Locating the bundle
# --------------------------------------------------------------------------

def bundle_from_items(items):
    """
    The symbology resource among a version's resources, or None. Matched on the
    `_symbology` resource name (see api.classify_ext, which already routes
    these ZIPs to a download link rather than a data container).
    """
    for it in items or []:
        if it.get("ext") == "zip" and "symbology" in (it.get("name") or "").lower():
            if it.get("download_url"):
                return it
    return None


# --------------------------------------------------------------------------
# Fetching + caching the bundle
# --------------------------------------------------------------------------

def _cache_root():
    """Persistent cache dir under the active QGIS profile (see module docstring)."""
    return os.path.join(
        QgsApplication.qgisSettingsDirPath(), "over_catalog", "symbology")


def _cache_dir(download_url):
    """One directory per bundle URL (the R2 filename carries a content hash)."""
    key = hashlib.sha1(  # nosec B324 - cache key only, not security
        (download_url or "").encode("utf-8")).hexdigest()[:16]
    return os.path.join(_cache_root(), key)


def _vsi_read(path):
    """Read a whole file from a /vsizip//vsicurl/ path, or None."""
    from osgeo import gdal
    stat = gdal.VSIStatL(path)
    if not stat or not stat.size:
        return None
    handle = gdal.VSIFOpenL(path, "rb")
    if handle is None:
        return None
    try:
        return gdal.VSIFReadL(1, stat.size, handle)
    finally:
        gdal.VSIFCloseL(handle)


def _absolutize(sld_text, cache_dir):
    """Rewrite the SLD's relative icon hrefs to absolute paths in `cache_dir`."""
    def repl(match):
        href = match.group(1)
        if _ABSOLUTE_RE.match(href):
            return match.group(0)
        target = os.path.join(cache_dir, href.replace("/", os.sep))
        return match.group(0).replace(href, target.replace("\\", "/"))
    return _HREF_RE.sub(repl, sld_text)


def ensure_bundle(download_url):
    """
    Download + extract the bundle once, returning
    {"sld": <path>, "fields": {machine_name: hebrew_alias}} or None.

    Subsequent calls reuse the extracted cache directory, so applying a style
    to a second layer of the same dataset costs nothing.
    """
    from osgeo import gdal

    cache_dir = _cache_dir(download_url)
    sld_path = os.path.join(cache_dir, "style.sld")
    fields_path = os.path.join(cache_dir, "fields.csv")
    if os.path.exists(sld_path):
        return {"sld": sld_path, "fields": _read_field_aliases(fields_path)}

    base = "/vsizip//vsicurl/" + download_url
    try:
        names = gdal.ReadDir(base) or []
    except RuntimeError as exc:
        _log(f"symbology: cannot open bundle: {exc}", Qgis.MessageLevel.Warning)
        return None

    sld_name = next((n for n in names if n.lower().endswith(".sld")), None)
    if not sld_name:
        return None
    raw = _vsi_read(f"{base}/{sld_name}")
    if not raw:
        return None

    os.makedirs(cache_dir, exist_ok=True)

    # Icons must land next to the style, since the hrefs are rewritten to
    # point at them by absolute path.
    if "icons" in names:
        icon_dir = os.path.join(cache_dir, "icons")
        os.makedirs(icon_dir, exist_ok=True)
        for icon in (gdal.ReadDir(f"{base}/icons") or []):
            data = _vsi_read(f"{base}/icons/{icon}")
            if data:
                with open(os.path.join(icon_dir, icon), "wb") as fh:
                    fh.write(data)

    sld_text = _absolutize(raw.decode("utf-8", "replace"), cache_dir)
    with open(sld_path, "w", encoding="utf-8") as fh:
        fh.write(sld_text)

    fields_name = next((n for n in names if n.endswith("_fields.csv")), None)
    if fields_name:
        data = _vsi_read(f"{base}/{fields_name}")
        if data:
            with open(fields_path, "wb") as fh:
                fh.write(data)

    return {"sld": sld_path, "fields": _read_field_aliases(fields_path)}


def _read_field_aliases(fields_path):
    """{machine_name: hebrew_alias} from the bundle's fields CSV ({} if absent)."""
    if not fields_path or not os.path.exists(fields_path):
        return {}
    try:
        with open(fields_path, "rb") as fh:
            text = fh.read().decode("utf-8-sig", "replace")
        out = {}
        for row in csv.DictReader(io.StringIO(text)):
            name = (row.get("machine_name") or "").strip()
            alias = (row.get("hebrew_alias") or "").strip()
            if name and alias:
                out[name] = alias
        return out
    except (OSError, ValueError, csv.Error) as exc:
        _log(f"symbology: unreadable fields CSV: {exc}", Qgis.MessageLevel.Warning)
        return {}


# --------------------------------------------------------------------------
# Applying to a layer
# --------------------------------------------------------------------------

def _referenced_fields(sld_path):
    """Real (non-pseudo) attribute names the SLD filters on."""
    try:
        with open(sld_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        _log(f"symbology: unreadable SLD: {exc}", Qgis.MessageLevel.Warning)
        return set()
    return {p for p in _PROPERTY_RE.findall(text)
            if p.lower() not in GEOM_PSEUDO_FIELDS}


def apply_to_layer(layer, download_url, set_aliases=True):
    """
    Apply the dataset's official symbology to `layer`.

    Returns True when the style was applied. Returns False — leaving the layer
    untouched — when there is no bundle, or when the style references an
    attribute the layer does not publish (which would render every feature
    unstyled; see the module docstring).
    """
    if layer is None or not layer.isValid():
        return False
    if not hasattr(layer, "loadSldStyle"):
        return False

    bundle = ensure_bundle(download_url)
    if not bundle:
        return False

    field_names = {f.name() for f in layer.fields()}
    missing = _referenced_fields(bundle["sld"]) - field_names
    if missing:
        _log(f"symbology: skipped for '{layer.name()}' — the style filters on "
             f"{sorted(missing)}, which the data does not publish",
             Qgis.MessageLevel.Warning)
        return False

    message, ok = layer.loadSldStyle(bundle["sld"])
    if not ok:
        _log(f"symbology: QGIS rejected the SLD for '{layer.name()}': {message}",
             Qgis.MessageLevel.Warning)
        return False

    if set_aliases:
        _apply_aliases(layer, bundle["fields"])

    layer.triggerRepaint()
    return True


def _apply_aliases(layer, aliases):
    """Show GovMap's Hebrew field captions in the attribute table."""
    if not aliases:
        return
    fields = layer.fields()
    for name, alias in aliases.items():
        idx = fields.indexOf(name)
        if idx >= 0:
            layer.setFieldAlias(idx, alias)

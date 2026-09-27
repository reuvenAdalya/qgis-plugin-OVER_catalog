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

ESRI LYR styles
---------------
Some datasets ship no SLD at all and instead zip an ArcGIS `.lyr` next to the
shapefile it styles (e.g. BUS_TERMINAL_STRAT.zip = the .shp parts plus
BUS_TERMINAL_STRAT.lyr). QGIS cannot read a .lyr, but the SLYR plugin can, so
when SLYR is installed the .lyr is converted once to a QML and cached like any
other style.

The conversion target matters: LYR -> QLR would carry the layer's ORIGINAL data
source, whose path frequently does not point at the shapefile shipped beside it.
LYR -> QML sidesteps that entirely, because a QML is style-only — it is applied
to the layer we already opened from the archive, so there is no source to
correct. As a bonus the QML carries the LYR's labelling, which the SLD bundles
do not.

A LYR authored against a geodatabase often spells fields in a different case
than the shapefile does (`TERM_NAME` in the style vs `term_name` in the data),
which would leave labels silently blank. Field references are therefore mapped
onto the layer's own spelling, case-insensitively — an exact match ignoring
case, never the fuzzy guessing rejected above. The mapping is done per layer at
apply time, since the same dataset's shapefile and datastore table can spell
the same field differently.

An SLD wins over a LYR when a dataset somehow has both: it needs no third-party
plugin, so it works for every user.
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
    The resource to take this dataset's style from, or None.

    A `_symbology` bundle wins — it holds an SLD, which needs no third-party
    plugin. Failing that, any ZIP is offered as a CANDIDATE, because a data ZIP
    may carry an ESRI .lyr beside its shapefile. Whether it actually does is
    decided lazily in ensure_bundle, so building the tree never opens an
    archive.
    """
    zips = [it for it in (items or [])
            if it.get("ext") == "zip" and it.get("download_url")]
    for it in zips:
        if "symbology" in (it.get("name") or "").lower():
            return it
    return zips[0] if zips else None


def slyr_available():
    """True if the SLYR plugin can convert an ESRI .lyr for us."""
    try:
        from qgis.core import QgsApplication
        return QgsApplication.processingRegistry().algorithmById(
            "slyr:lyrtoqml") is not None
    except Exception:
        return False


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
    {"sld": <path or None>, "fields": {machine_name: hebrew_alias}}, or None
    when the bundle holds neither.

    Note that a bundle may legitimately carry ONLY the field dictionary:
    GovMap has no style for every layer, and those bundles are still worth
    fetching for the Hebrew field captions. `sld` is None in that case.

    Subsequent calls reuse the extracted cache directory, so applying a style
    to a second layer of the same dataset costs nothing.
    """
    from osgeo import gdal

    cache_dir = _cache_dir(download_url)
    sld_path = os.path.join(cache_dir, "style.sld")
    qml_path = os.path.join(cache_dir, "style.qml")
    fields_path = os.path.join(cache_dir, "fields.csv")
    stamp = os.path.join(cache_dir, ".extracted")
    lyr_marker = os.path.join(cache_dir, ".lyr-pending")
    if os.path.exists(stamp):
        cached = {
            "sld": sld_path if os.path.exists(sld_path) else None,
            "qml": qml_path if os.path.exists(qml_path) else None,
            "fields": _read_field_aliases(fields_path),
            "needs_slyr": False,
        }
        # A .lyr was found on an earlier pass but SLYR was not installed then;
        # retry the conversion now in case it has been installed since.
        if not cached["sld"] and not cached["qml"] and os.path.exists(lyr_marker):
            src = open(lyr_marker, encoding="utf-8").read().strip()
            if slyr_available() and _convert_lyr(src, qml_path):
                cached["qml"] = qml_path
            else:
                cached["needs_slyr"] = True
        return cached

    base = "/vsizip//vsicurl/" + download_url
    try:
        names = gdal.ReadDir(base) or []
    except RuntimeError as exc:
        _log(f"symbology: cannot open bundle: {exc}", Qgis.MessageLevel.Warning)
        return None

    sld_name = next((n for n in names if n.lower().endswith(".sld")), None)
    fields_name = next((n for n in names if n.endswith("_fields.csv")), None)
    # An SLD wins; only look for an ESRI style when there is no SLD.
    lyr_name = (None if sld_name else
                next((n for n in names
                      if n.lower().endswith((".lyr", ".lyrx"))), None))
    if not sld_name and not fields_name and not lyr_name:
        return None

    os.makedirs(cache_dir, exist_ok=True)

    needs_slyr = False
    if lyr_name:
        local_lyr = os.path.join(cache_dir, lyr_name)
        data = _vsi_read(f"{base}/{lyr_name}")
        if data:
            with open(local_lyr, "wb") as fh:
                fh.write(data)
            # Remember the source even if we cannot convert it yet, so the
            # answer survives a restart and the retry above can pick it up.
            with open(lyr_marker, "w", encoding="utf-8") as fh:
                fh.write(local_lyr)
            if slyr_available():
                if not _convert_lyr(local_lyr, qml_path):
                    needs_slyr = False   # SLYR is there; the LYR just failed
            else:
                needs_slyr = True

    if sld_name:
        raw = _vsi_read(f"{base}/{sld_name}")
        if raw:
            # Icons must land next to the style, since the hrefs are rewritten
            # to point at them by absolute path.
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

    if fields_name:
        data = _vsi_read(f"{base}/{fields_name}")
        if data:
            with open(fields_path, "wb") as fh:
                fh.write(data)

    with open(stamp, "w", encoding="utf-8") as fh:
        fh.write(download_url)

    return {
        "sld": sld_path if os.path.exists(sld_path) else None,
        "qml": qml_path if os.path.exists(qml_path) else None,
        "fields": _read_field_aliases(fields_path),
        "needs_slyr": needs_slyr,
    }


def _convert_lyr(lyr_path, qml_path):
    """
    Convert an ESRI .lyr/.lyrx to a QML through SLYR. True on success.

    SLYR's Community Edition does not support every ArcGIS symbol, and reports
    that through CONVERTED/ERROR rather than by raising, so a partial failure is
    logged and treated as "no style" instead of applying something broken.
    """
    try:
        import processing
        alg = ("slyr:lyrxtoqml" if lyr_path.lower().endswith(".lyrx")
               else "slyr:lyrtoqml")
        res = processing.run(alg, {"INPUT": lyr_path, "OUTPUT": qml_path}) or {}
    except Exception as exc:
        _log(f"symbology: SLYR could not convert {os.path.basename(lyr_path)}: "
             f"{exc}", Qgis.MessageLevel.Warning)
        return False
    if res.get("ERROR"):
        _log(f"symbology: SLYR reported an error converting "
             f"{os.path.basename(lyr_path)}: {res['ERROR']}",
             Qgis.MessageLevel.Warning)
    if os.path.exists(qml_path) and os.path.getsize(qml_path) > 0:
        return True
    return False


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


# apply_to_layer outcomes.
STYLED = "styled"          # the SLD was applied
STYLED_LYR = "styled_lyr"  # an ESRI LYR was applied, via SLYR
ALIASES = "aliases"        # no style in the bundle; Hebrew captions applied
NO_BUNDLE = "none"         # nothing to apply — not an error
SKIPPED = "skipped"        # a style exists but would blank the layer
NEEDS_SLYR = "needs_slyr"  # an ESRI LYR is available but SLYR is not installed

# QML attribute references we remap onto the layer's own field spelling.
_QML_FIELD_RE = re.compile(r'((?:attr|fieldName)=")([^"]+)(")')


def apply_to_layer(layer, download_url, set_aliases=True):
    """
    Apply the dataset's official symbology (and Hebrew field captions) to
    `layer`. Returns one of STYLED / ALIASES / NO_BUNDLE / SKIPPED.

    SKIPPED is the only outcome worth telling the user about: a style exists
    but references an attribute the layer does not publish, so applying it
    would leave every feature unstyled (see the module docstring). NO_BUNDLE
    simply means GovMap has no style for this layer, which is common and
    unremarkable.
    """
    if layer is None or not layer.isValid():
        return NO_BUNDLE
    if not hasattr(layer, "loadSldStyle"):
        return NO_BUNDLE

    bundle = ensure_bundle(download_url)
    if not bundle:
        return NO_BUNDLE

    # Captions are independent of the style and are worth applying either way.
    if set_aliases:
        _apply_aliases(layer, bundle["fields"])

    if not bundle["sld"]:
        if bundle.get("qml"):
            return _apply_qml(layer, bundle["qml"])
        if bundle.get("needs_slyr"):
            return NEEDS_SLYR
        return ALIASES if bundle["fields"] else NO_BUNDLE

    field_names = {f.name() for f in layer.fields()}
    missing = _referenced_fields(bundle["sld"]) - field_names
    if missing:
        _log(f"symbology: skipped for '{layer.name()}' — the style filters on "
             f"{sorted(missing)}, which the data does not publish",
             Qgis.MessageLevel.Warning)
        return SKIPPED

    message, ok = layer.loadSldStyle(bundle["sld"])
    if not ok:
        _log(f"symbology: QGIS rejected the SLD for '{layer.name()}': {message}",
             Qgis.MessageLevel.Warning)
        return SKIPPED

    layer.triggerRepaint()
    return STYLED


def _apply_qml(layer, qml_path):
    """
    Apply a SLYR-converted QML, mapping its field references onto this layer's
    own spelling first (see the module docstring on ArcGIS field case).

    The rewritten QML is handed to importNamedStyle as a document, so nothing
    per-layer is written to the cache — two layers of the same dataset can need
    different spellings of the same field.
    """
    try:
        with open(qml_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        _log(f"symbology: unreadable QML: {exc}", Qgis.MessageLevel.Warning)
        return NO_BUNDLE

    actual = {f.name().lower(): f.name() for f in layer.fields()}
    names = {f.name() for f in layer.fields()}

    def remap(match):
        ref = match.group(2)
        if ref in names or ref.lower() not in actual:
            return match.group(0)
        return match.group(1) + actual[ref.lower()] + match.group(3)

    text = _QML_FIELD_RE.sub(remap, text)

    referenced = {m.group(2) for m in _QML_FIELD_RE.finditer(text)}
    missing = {r for r in referenced
               if r and r not in names and r.lower() not in GEOM_PSEUDO_FIELDS}
    if missing:
        _log(f"symbology: skipped the ESRI style for '{layer.name()}' — it "
             f"refers to {sorted(missing)}, which the data does not publish",
             Qgis.MessageLevel.Warning)
        return SKIPPED

    from qgis.PyQt.QtXml import QDomDocument
    doc = QDomDocument()
    if not doc.setContent(text):
        _log(f"symbology: SLYR produced a QML QGIS could not parse for "
             f"'{layer.name()}'", Qgis.MessageLevel.Warning)
        return SKIPPED
    result = layer.importNamedStyle(doc)
    ok = result[0] if isinstance(result, tuple) else result
    if not ok:
        detail = result[1] if isinstance(result, tuple) and len(result) > 1 else ""
        _log(f"symbology: QGIS rejected the converted style for "
             f"'{layer.name()}': {detail}", Qgis.MessageLevel.Warning)
        return SKIPPED

    layer.triggerRepaint()
    return STYLED_LYR


def _apply_aliases(layer, aliases):
    """Show GovMap's Hebrew field captions in the attribute table."""
    if not aliases:
        return
    fields = layer.fields()
    for name, alias in aliases.items():
        idx = fields.indexOf(name)
        if idx >= 0:
            layer.setFieldAlias(idx, alias)

# -*- coding: utf-8 -*-
"""
Layer 1 — access to the over.org.il API and construction of QGIS-ready URIs.

All plugin HTTP calls go through QgsNetworkAccessManager (per the QGIS plugin
publishing guidelines) so that QGIS proxy settings are respected. The actual
layer files (GeoJSON/CSV) are NOT fetched here — they are handed to GDAL via
/vsicurl/ URIs; this module only builds those URIs.
"""

import json
import re

from qgis.core import QgsNetworkAccessManager
from qgis.PyQt.QtCore import QUrl, QEventLoop
from qgis.PyQt.QtNetwork import QNetworkRequest

API_BASE = "https://www.over.org.il/api/v1"
USER_AGENT = "over-qgis-catalog/0.1"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Network (QgsNetworkAccessManager)
# --------------------------------------------------------------------------

class OverApiError(Exception):
    """
    Raised on any network or decoding failure when talking to the API.

    `status` carries the HTTP status code when the failure was an HTTP error
    response (e.g. 409), or None for transport/decoding errors. Callers use it
    to distinguish a datastore 409 (needs a gateway retry) from other failures.
    """

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _follow_redirects(req):
    """
    Ask the request to follow redirects, across the Qt5/Qt6 split.

    Qt6 removed QNetworkRequest.Attribute.FollowRedirectsAttribute (the bool
    flag used on Qt5); redirects are now opted into via RedirectPolicyAttribute
    with a QNetworkRequest.RedirectPolicy value. NoLessSafeRedirectPolicy
    matches the old FollowRedirectsAttribute behaviour (follow same-or-safer
    redirects, e.g. https -> https on another host, which is what over.org.il
    -> Cloudflare R2 needs) and is also Qt6's default, so this is only
    necessary for the Qt5 builds where the default is still "don't follow".
    """
    try:
        req.setAttribute(
            QNetworkRequest.Attribute.FollowRedirectsAttribute, True)
    except AttributeError:
        req.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)


def fetch_json(url, timeout_ms=30000):
    """
    Blocking GET that returns decoded JSON.

    Uses a local QEventLoop so it can be called from synchronous code (e.g.
    tree node expansion during development) without freezing the main event
    loop. Follows redirects, which is required because /latest and the file
    download endpoints redirect from over.org.il to Cloudflare R2.
    """
    req = QNetworkRequest(QUrl(url))
    req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, USER_AGENT)
    _follow_redirects(req)

    reply = QgsNetworkAccessManager.instance().get(req)

    loop = QEventLoop()
    reply.finished.connect(loop.quit)
    loop.exec()

    try:
        status = reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if reply.error() != reply.NetworkError.NoError:
            raise OverApiError(reply.errorString(), status=status)
        raw = bytes(reply.readAll())
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise OverApiError(f"invalid JSON from {url}: {exc}")
    finally:
        reply.deleteLater()


def _pct(value):
    """Percent-encode a query value as UTF-8 (needed for Hebrew queries)."""
    return QUrl.toPercentEncoding(str(value)).data().decode("ascii")


# --------------------------------------------------------------------------
# API calls
# --------------------------------------------------------------------------

def list_datasets(limit=50, offset=0, source_type=None,
                  organization=None, tag=None, query=None):
    """
    List tracked datasets with server-side paging and filtering.
    Returns the raw payload: {"total", "limit", "offset", "items": [...]}.
    """
    params = [f"limit={int(limit)}", f"offset={int(offset)}"]
    if source_type:
        params.append(f"source_type={_pct(source_type)}")
    if organization:
        params.append(f"organization={_pct(organization)}")
    if tag:
        params.append(f"tag={_pct(tag)}")
    if query:
        params.append(f"query={_pct(query)}")
    return fetch_json(f"{API_BASE}/datasets?" + "&".join(params))


def get_dataset(dataset_id):
    """Single dataset: metadata + recent_versions summary."""
    return fetch_json(f"{API_BASE}/datasets/{dataset_id}")


def get_latest_version(dataset_id):
    """
    Latest version (highest version_number), including a `resources` array
    with a download_url per file. Raises OverApiError (404) if no versions
    exist yet.
    """
    return fetch_json(f"{API_BASE}/datasets/{dataset_id}/versions/latest")


def get_all_versions(dataset_id):
    """
    Full version history, newest first — used for the lazy-loaded
    "previous versions" node. NOTE: payload shape (bare list vs wrapped
    object) is not yet confirmed; normalize_versions() handles both.
    """
    return fetch_json(f"{API_BASE}/datasets/{dataset_id}/versions")


def normalize_versions(payload):
    """Return a list of version objects regardless of wrapper shape."""
    if isinstance(payload, list):
        return payload
    for key in ("versions", "items", "results"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def looks_like_uuid(text):
    """True if `text` is exactly a UUID (used to route Locator input)."""
    return bool(_UUID_RE.match(text.strip()))


# --------------------------------------------------------------------------
# Turning a version into loadable resources
# --------------------------------------------------------------------------

def resources_from_version(version):
    """
    Convert a version object (from /latest or the /versions list) into a list
    of items describing every resource. Each item:

        {name, fmt, ext, category, layer_kind, container, kind, gdal_format,
         uri, download_url, rows, label, odata_url?}

    category   : 'spatial' | 'data' | 'other' — drives the tree display mode
                 (spatial-only / all-openable / all-files; see data_items).
    layer_kind : 'vector' | 'raster' | 'table' | None — how (if at all) the
                 resource loads as a QGIS map layer. None = not a map layer
                 (style/definition files, and anything QGIS can't open).
    container  : True for a file that expands to sublayers in the tree
                 (GPKG / GeoParquet / FlatGeobuf / GML / KML / zip / ...).
    kind       : legacy coarse bucket ('vector' | 'table' | 'other' | 'odata')
                 kept for pick_default and back-compat.
    """
    change = version.get("change_summary") or {}
    rows_by_res = {
        r.get("name"): r.get("rows")
        for r in change.get("resources", [])
    }
    total_rows = change.get("total_rows")

    items = []
    for res in version.get("resources", []):
        name = res.get("name") or "resource"
        fmt = (res.get("format") or "").lower()
        download_url = res.get("download_url")
        rows = rows_by_res.get(name)
        if rows is None:
            rows = total_rows  # fall back to the version-wide count

        if not download_url:
            # Non-r2 storage (odata): different load path.
            items.append({
                "kind": "odata",
                "name": name,
                "fmt": fmt,
                "ext": "",
                "category": "other",
                "layer_kind": None,
                "container": False,
                "gdal_format": None,
                "uri": None,
                "download_url": None,
                "odata_url": res.get("odata_resource_url"),
                "rows": rows,
                "label": display_label(name, rows, fmt),
            })
            continue

        ext = resource_ext(fmt, name, download_url)
        category, layer_kind, container = classify_ext(ext)
        if ext == "zip" and _is_symbology(name, download_url):
            # over.org.il's ZIPs are symbology bundles (QML/SLD styles), not
            # zipped shapefiles: show them as a download link (spatial group,
            # no warning) like the other style files, not as a data container.
            layer_kind, container = None, False
        items.append({
            "name": name,
            "fmt": fmt,
            "ext": ext,                # canonical lowercase extension
            "category": category,      # spatial | data | other
            "layer_kind": layer_kind,  # vector | raster | table | None
            "container": container,    # holds sublayers -> expandable node
            "kind": _legacy_kind(category, layer_kind),
            "gdal_format": ext,        # drives build_uri
            "uri": build_uri(download_url, ext),
            "download_url": download_url,
            "rows": rows,
            "label": display_label(name, rows, fmt),
        })
    return items


# --------------------------------------------------------------------------
# File-type taxonomy
#
# Each resource is placed in one of three display categories, matching the
# three tree modes exposed in the settings dialog:
#   spatial : GIS layers/rasters/styles — shown in every mode (incl. "spatial
#             only"). ZIP is assumed to hold a shapefile and treated as a
#             spatial container.
#   data    : non-spatial data QGIS/GDAL can still open as a table (CSV, XLS,
#             TXT, ...) — shown in "all openable" and "all files".
#   other   : anything QGIS can't open as a layer (PDF, DOC, XML, ...) — shown
#             only in "all files", with a warning marker.
#
# Driver selection relies on the file EXTENSION (over.org.il stores each file
# with its proper extension) — no content sniffing / forced drivers.
# --------------------------------------------------------------------------

# Containers expand to sublayers in the tree (queried on demand). ZIP is here
# too: a zipped shapefile is read via /vsizip/ and its single vector sublayer
# is listed (the .dbf/.shx/.prj sidecars are folded into it, not shown).
CONTAINER_FORMATS = {"gpkg", "parquet", "fgb", "gml", "kml", "kmz", "zip"}
RASTER_FORMATS = {"tif", "tiff", "geotiff", "png", "jpg", "jpeg"}
VECTOR_FORMATS = {"geojson", "shp"}
# Style / layer-definition files: GIS-related (shown in "spatial only") but not
# loadable as a map layer -> offered as a download/open link, no warning.
STYLE_FORMATS = {"sld", "qml", "qlr", "lyr", "lyrx"}
# Non-spatial tabular data QGIS/GDAL can open.
TABLE_FORMATS = {"csv", "tsv", "txt", "xls", "xlsx", "ods"}

SPATIAL_FORMATS = (CONTAINER_FORMATS | RASTER_FORMATS
                   | VECTOR_FORMATS | STYLE_FORMATS)
# Everything the software can open as a layer OR a table (spatial + data),
# minus the style/definition files that aren't openable data. Used by
# catalog_cache to decide dataset visibility in the "all openable" mode.
OPENABLE_FORMATS = ((SPATIAL_FORMATS - STYLE_FORMATS) | TABLE_FORMATS)

# Normalize format/extension synonyms to the canonical token above.
_EXT_ALIASES = {
    "geoparquet": "parquet",
    "flatgeobuf": "fgb",
    "json": "geojson",   # over.org.il's spatial snapshots are GeoJSON
}


def resource_ext(fmt, name, download_url):
    """
    Canonical lowercase extension for a resource: the declared `format` when
    present, else parsed from the download URL's filename. Synonyms are
    normalized (geoparquet -> parquet, flatgeobuf -> fgb).
    """
    f = (fmt or "").lower().strip().lstrip(".")
    if not f:
        f = _ext_from_url(download_url)
    return _EXT_ALIASES.get(f, f)


def _ext_from_url(download_url):
    """Extension parsed from the URL's last path segment (dot- or _-joined)."""
    low = (download_url or "").lower().split("?")[0].split("#")[0]
    seg = low.rstrip("/").rsplit("/", 1)[-1]
    if seg.endswith(".gz"):
        seg = seg[:-3]  # look through the gzip wrapper (e.g. .geojson.gz)
    for sep in (".", "_"):
        if sep in seg:
            cand = seg.rsplit(sep, 1)[-1]
            if cand.isalnum() and 2 <= len(cand) <= 8:
                return cand
    return ""


def _is_symbology(name, download_url):
    """True for a symbology/style bundle (over.org.il names these `_symbology`)."""
    hay = f"{name or ''} {download_url or ''}".lower()
    return "symbology" in hay


def classify_ext(ext):
    """
    Map a canonical extension to (category, layer_kind, container):
      category   : 'spatial' | 'data' | 'other'
      layer_kind : 'vector' | 'raster' | 'table' | None
      container  : bool
    """
    if ext in CONTAINER_FORMATS:
        return "spatial", "vector", True
    if ext in RASTER_FORMATS:
        return "spatial", "raster", False
    if ext in VECTOR_FORMATS:
        return "spatial", "vector", False
    if ext in STYLE_FORMATS:
        return "spatial", None, False       # shown, but not a loadable layer
    if ext in TABLE_FORMATS:
        return "data", "table", False
    return "other", None, False


def _legacy_kind(category, layer_kind):
    """Coarse legacy bucket used by pick_default / historical callers."""
    if layer_kind in ("vector", "raster"):
        return "vector"
    if layer_kind == "table":
        return "table"
    return "other"


def classify_resource(fmt, name, download_url):
    """
    Back-compat shim: (kind, gdal_format) for callers predating the richer
    per-resource metadata built in resources_from_version.
    """
    ext = resource_ext(fmt, name, download_url)
    category, layer_kind, _ = classify_ext(ext)
    return _legacy_kind(category, layer_kind), ext


# Internal resource names -> friendlier Hebrew labels. NOTE: the Browser tree
# now labels file leaves as "<dataset title>.<ext>" (see
# data_items.dataset_file_label); display_label is kept for any caller that
# wants the resource-centric label and for the feature count.
_FRIENDLY_NAMES = {
    "_geojson": "GeoJSON",
    "נתוני הסורק": "טבלת נתונים",
}


def display_label(name, rows, fmt=None):
    """
    Resource-centric label: a friendly resource name, its format when
    informative, and the feature count in parentheses when known — e.g.
    "GeoJSON (14)" or "טבלת נתונים · CSV (2261)".
    """
    base = _FRIENDLY_NAMES.get(name, name)
    fmt = (fmt or "").upper()
    if fmt and fmt not in base.upper():
        base = f"{base} · {fmt}"
    return f"{base} ({rows})" if rows is not None else base


# --------------------------------------------------------------------------
# GDAL /vsicurl/ URI construction (with gzip detection)
# --------------------------------------------------------------------------

def _is_gzip(download_url):
    low = download_url.lower()
    return low.endswith(".gz") or ".geojson.gz" in low


def build_uri(download_url, gdal_format, force_gz=None):
    """
    Build the best-guess GDAL /vsicurl/ URI for a resource of `gdal_format`
    (from classify_resource). The R2 filenames often lack a real extension, so
    the driver is chosen from the known format rather than the URL suffix.
    """
    vsicurl = f"/vsicurl/{download_url}"
    gz = _is_gzip(download_url) if force_gz is None else force_gz
    if gdal_format == "geojson":
        return f"/vsigzip/{vsicurl}" if gz else vsicurl
    if gdal_format == "csv":
        return f"CSV:{vsicurl}"
    if gdal_format == "zip":
        # Read inside the archive (e.g. a zipped shapefile) via /vsizip/;
        # querySublayers on this lists the archive's vector sublayer(s).
        return f"/vsizip/{vsicurl}"
    # gpkg / kml / kmz / gml / raster / other: GDAL identifies these by content.
    return vsicurl


def uri_candidates(download_url, gdal_format):
    """
    Ordered URI forms to try at load time (best guess first) — the loader
    falls through to the next when a layer comes back invalid. Covers the
    gzip/plain ambiguity for GeoJSON and the driver-prefix ambiguity for
    GPKG/CSV when the R2 filename has no usable extension.
    """
    vsicurl = f"/vsicurl/{download_url}"
    if gdal_format == "geojson":
        prim = _is_gzip(download_url)
        cands = [(f"/vsigzip/{vsicurl}" if g else vsicurl) for g in (prim, not prim)]
    elif gdal_format == "gpkg":
        cands = [vsicurl, f"GPKG:{vsicurl}"]
    elif gdal_format == "csv":
        cands = [f"CSV:{vsicurl}", vsicurl]
    elif gdal_format in ("kml", "kmz"):
        cands = [vsicurl, f"KML:{vsicurl}"]
    else:
        cands = [vsicurl]
    out = []
    for uri in cands:
        if uri not in out:
            out.append(uri)
    return out


# --------------------------------------------------------------------------
# Default-resource selection (interpretation A: default = double-click action)
# --------------------------------------------------------------------------

def pick_default(items, prefer_geojson=True):
    """
    The resource loaded on double-click / Locator selection. All resources
    remain individually available in the tree; this only picks the default.
    """
    if prefer_geojson:
        for item in items:
            if item["kind"] == "vector":
                return item
    return items[0] if items else None

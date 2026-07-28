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
    req.setAttribute(QNetworkRequest.Attribute.FollowRedirectsAttribute, True)

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
    of loadable items. Each item:

        {kind, name, fmt, uri, download_url, rows, label, odata_url?}

    kind: 'vector' (spatial) | 'table' (attribute-only) | 'odata'
          ('odata' has no download_url -> loaded via query_dataset_rows,
           handled in the load layer, not here).
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
                "uri": None,
                "download_url": None,
                "odata_url": res.get("odata_resource_url"),
                "rows": rows,
                "label": display_label(name, rows, fmt),
            })
            continue

        is_spatial = (fmt == "geojson") or (".geojson" in download_url.lower())
        kind = "vector" if is_spatial else "table"
        items.append({
            "kind": kind,
            "name": name,
            "fmt": fmt,
            "uri": build_uri(download_url, kind),
            "download_url": download_url,
            "rows": rows,
            "label": display_label(name, rows, fmt),
        })
    return items


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


def build_uri(download_url, kind, force_gz=None):
    """
    Build a /vsicurl/ path, wrapping in /vsigzip/ when the file is gzip'd.

    force_gz: None -> auto-detect from the URL; True/False -> force (used by
    uri_candidates() for load-time fallback when the filename is misleading).
    """
    gz = _is_gzip(download_url) if force_gz is None else force_gz
    core = (f"/vsigzip//vsicurl/{download_url}" if gz
            else f"/vsicurl/{download_url}")
    # CSV/tables have no extension in the URL, so force the OGR driver.
    return f"CSV:{core}" if kind == "table" else core


def uri_candidates(download_url, kind):
    """
    Both URI forms (gzip / plain), best guess first — for load-time fallback:
    try the first; if the resulting layer isn't valid, try the second.
    """
    primary = _is_gzip(download_url)
    out = []
    for gz in (primary, not primary):
        uri = build_uri(download_url, kind, force_gz=gz)
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

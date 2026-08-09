# -*- coding: utf-8 -*-
"""
Client-side catalog cache, sourced from /api/tables.

/api/tables returns every datastore table (≈1050) in a single call, each row
carrying: table, dataset_id, ckan_id, title, organization, source_type,
est_rows, kind/schema, and a `columns` array with per-column {name, type}.

That gives us — cheaply, in one request — everything the /data spatial filter
uses, plus more:
  * "has data"  -> the dataset appears in /api/tables at all.
  * "is spatial" -> at least one of its tables has a column of type
    "geometry" (this is exactly how over.org.il's own filter works).

Datasets are the unit shown in the tree, so we aggregate tables by dataset_id.
Free-text search is done locally against titles (the REST list endpoints have
no server-side text filter).

NOTE: a dataset with only a file snapshot (and no datastore table) will NOT
appear here. Spatial/tabular loading still goes through /api/v1/datasets +
/versions/latest as before; this cache is for browsing, filtering and search.
"""

import time

from . import api

TABLES_URL = "https://www.over.org.il/api/tables"
DATASETS_URL = "https://www.over.org.il/api/v1/datasets"

# For file-only datasets (no datastore table), spatiality is inferred cheaply
# from the /api/v1 list — no per-dataset /versions fetch: a scraped source
# (govmap/scraper) is treated as spatial (this is where the heavy 100k+ row
# layers without an idx table live), and a ckan dataset is spatial when the
# source offers a spatial-format resource.
_SPATIAL_SOURCE_TYPES = {"govmap", "scraper"}
_SPATIAL_SOURCE_FORMATS = {"geojson", "kml", "kmz", "gpkg", "gml", "zip", "shp"}
# Formats QGIS/GDAL can open as a layer or a table — used to decide whether a
# file-only dataset has any openable content (the "all openable" tree mode).
_OPENABLE_SOURCE_FORMATS = _SPATIAL_SOURCE_FORMATS | api.OPENABLE_FORMATS

# {"ts": epoch, "datasets": {dataset_id: {...}}, "order": [...], "gateway": id}
_CACHE = {"ts": 0.0, "datasets": None, "order": None, "gateway": None}

# Schema PostGIS/geometry tables live in when /api/tables omits the field.
DEFAULT_SPATIAL_SCHEMA = "idx"

TTL_SECONDS = 30 * 60  # in-memory refresh interval


def _fetch_tables():
    """Return the raw list of table rows from /api/tables (single call)."""
    payload = api.fetch_json(TABLES_URL)
    if isinstance(payload, list):
        return payload
    return payload.get("tables", []) or []


def _table_is_spatial(table_row):
    """True if any column is of type 'geometry'."""
    for col in table_row.get("columns", []) or []:
        if (col.get("type") or "").lower() == "geometry":
            return True
    return False


def _fetch_all_datasets():
    """Every dataset from /api/v1/datasets (paged). Used to add file-only
    datasets (no datastore table) to the catalog."""
    out = []
    offset = 0
    while True:
        payload = api.fetch_json(f"{DATASETS_URL}?limit=500&offset={offset}")
        items = (payload.get("items") if isinstance(payload, dict)
                 else payload) or []
        if not items:
            break
        out.extend(items)
        if len(items) < 500:
            break
        offset += 500
    return out


def _file_only_is_spatial(item):
    """Cheap spatiality guess for a file-only dataset (see the constants)."""
    if item.get("source_type") in _SPATIAL_SOURCE_TYPES:
        return True
    for res in item.get("new_resources_at_source") or []:
        if (res.get("format") or "").lower() in _SPATIAL_SOURCE_FORMATS:
            return True
    return False


def _file_only_has_openable(item):
    """True if a file-only dataset has any resource QGIS/GDAL can open."""
    if _file_only_is_spatial(item):
        return True
    for res in item.get("new_resources_at_source") or []:
        if (res.get("format") or "").lower() in _OPENABLE_SOURCE_FORMATS:
            return True
    return False


def _build():
    """
    Fetch /api/tables and aggregate into one record per dataset_id:
        {dataset_id, title, organization, source_type, ckan_id,
         is_spatial, est_rows, table_count, versions_url}
    """
    rows = _fetch_tables()
    datasets = {}
    order = []
    gateway = None
    for t in rows:
        ds_id = t.get("dataset_id")
        if not ds_id:
            continue
        rec = datasets.get(ds_id)
        if rec is None:
            rec = {
                "dataset_id": ds_id,
                "title": t.get("title") or "",
                "organization": t.get("organization"),
                "source_type": t.get("source_type"),
                "ckan_id": t.get("ckan_id"),
                "is_spatial": False,
                "spatial_table": None,
                "spatial_schema": None,
                "spatial_columns": None,
                "primary_table": None,
                "primary_schema": None,
                "primary_columns": None,
                # A datastore table is always openable (spatially via Route B,
                # or as an info table via the non-spatial fallback), so any
                # table-based dataset counts as "has openable content".
                "has_openable": True,
                "est_rows": 0,
                "table_count": 0,
                "versions_url": t.get("versions_url"),
                "file_only": False,
            }
            datasets[ds_id] = rec
            order.append(ds_id)
        rec["table_count"] += 1
        rec["est_rows"] += int(t.get("est_rows") or 0)
        # Remember the first table seen for this dataset regardless of
        # spatiality, so a dataset whose only datastore table has no geometry
        # column (bare lat/lon or X/Y, not a real PostGIS geometry) still has
        # something queryable — see DatasetItem.over_table_fallback.
        if not rec.get("primary_table"):
            rec["primary_table"] = t.get("table")
            rec["primary_schema"] = t.get("schema") or DEFAULT_SPATIAL_SCHEMA
            rec["primary_columns"] = t.get("columns") or []
        # First ckan dataset seen becomes the Route B gateway (ckan datasets
        # have their own NEON append DB, so their /api/append id is accepted;
        # from there SQL can reach every schema, incl. idx spatial tables).
        if gateway is None and (t.get("source_type") == "ckan"):
            gateway = ds_id
        if _table_is_spatial(t):
            rec["is_spatial"] = True
            # Remember the specific table that carries geometry — that's the
            # one datastore_search_sql must query (FROM <schema>.<table>).
            if not rec.get("spatial_table"):
                rec["spatial_table"] = t.get("table")
                rec["spatial_schema"] = (
                    t.get("schema") or DEFAULT_SPATIAL_SCHEMA)
                # Keep the column list (name/type) for the advanced-query
                # dialog's field picker — avoids hand-typing Hebrew names.
                rec["spatial_columns"] = t.get("columns") or []

    # Merge in file-only datasets: those in /api/v1 with no datastore table
    # (absent from /api/tables). These are often the heavy layers (100k+ rows,
    # e.g. GPKG) that are stored as files only, without an idx table. They
    # load via Route A (file) only — no Route B / datastore.
    for item in _fetch_all_datasets():
        ds_id = item.get("id")
        if not ds_id or ds_id in datasets:
            continue
        org = item.get("organization")
        org_name = org.get("name") if isinstance(org, dict) else org
        datasets[ds_id] = {
            "dataset_id": ds_id,
            "title": item.get("title") or "",
            "organization": org_name,
            "source_type": item.get("source_type"),
            "ckan_id": item.get("ckan_id"),
            "is_spatial": _file_only_is_spatial(item),
            "spatial_table": None,
            "spatial_schema": None,
            "spatial_columns": None,
            "primary_table": None,
            "primary_schema": None,
            "primary_columns": None,
            "has_openable": _file_only_has_openable(item),
            "est_rows": 0,
            "table_count": 0,
            "versions_url": item.get("versions_url"),
            "file_only": True,
        }
        order.append(ds_id)

    return datasets, order, gateway


def _ensure(force=False):
    now = time.time()
    if (force or _CACHE["datasets"] is None
            or now - _CACHE["ts"] > TTL_SECONDS):
        datasets, order, gateway = _build()
        _CACHE["datasets"] = datasets
        _CACHE["order"] = order
        _CACHE["gateway"] = gateway
        _CACHE["ts"] = now


def get_all(force=False):
    """All dataset records, in the order tables were first seen."""
    _ensure(force)
    return [_CACHE["datasets"][i] for i in _CACHE["order"]]


def get(dataset_id):
    """One dataset record (or None if it has no datastore table)."""
    _ensure()
    return _CACHE["datasets"].get(dataset_id)


def pick_gateway():
    """
    A dataset_id usable as the Route B "gateway" — a ckan dataset, whose
    /api/append endpoint is NEON-backed and thus accepts datastore_search_sql.
    govmap/scraper datasets 409 on their own endpoint; queries for their
    (idx-schema) tables are routed through this gateway instead. None if the
    catalog somehow contains no ckan dataset.
    """
    _ensure()
    return _CACHE["gateway"]


def source_types():
    """Distinct source_type values present in the catalog, sorted."""
    _ensure()
    seen = []
    for rec in get_all():
        st = rec.get("source_type") or "—"
        if st not in seen:
            seen.append(st)
    return sorted(seen)


def organizations(source_type):
    """Distinct organization names within one source_type, sorted."""
    _ensure()
    seen = set()
    for rec in get_all():
        if rec.get("source_type") == source_type:
            seen.add(rec.get("organization") or "—")
    return sorted(seen)


def visible_in_mode(rec, mode):
    """
    Whether a dataset should appear in the tree for the given display mode:
      1 (spatial only)  -> has spatial content (geometry table / spatial file)
      2 (all openable)  -> has any content QGIS/GDAL can open (spatial or data)
      3 (all files)     -> always (even datasets with no openable content)
    """
    if mode <= 1:
        return bool(rec.get("is_spatial"))
    if mode == 2:
        return bool(rec.get("has_openable"))
    return True


def datasets(source_type=None, organization=None, mode=3):
    """
    Records matching the given source_type / organization filters and display
    `mode` (see visible_in_mode), sorted by title. `organization` matches the
    same "—" fallback used for the tree.
    """
    _ensure()
    out = []
    for rec in get_all():
        if source_type is not None and rec.get("source_type") != source_type:
            continue
        if organization is not None \
                and (rec.get("organization") or "—") != organization:
            continue
        if not visible_in_mode(rec, mode):
            continue
        out.append(rec)
    out.sort(key=lambda r: (r.get("title") or "").lower())
    return out


def search(text, limit=25, spatial_only=False, data_only=True):
    """
    Case-insensitive, token-AND title search over the cached catalog: every
    whitespace-separated token must appear somewhere in the title, in any
    order. This matches "אנדרטאות רמת הנגב" against "אנדרטאות מ.א. רמת הנגב",
    which a single contiguous-substring match would miss.

    spatial_only: keep only datasets with a geometry column.
    data_only:    since the source is /api/tables, every record already has a
                  table; kept as an explicit flag for clarity/future use.
    """
    tokens = (text or "").strip().lower().split()
    if not tokens:
        return []
    out = []
    for rec in get_all():
        if spatial_only and not rec["is_spatial"]:
            continue
        title = rec["title"].lower()
        if all(tok in title for tok in tokens):
            out.append(rec)
            if len(out) >= limit:
                break
    return out


def invalidate():
    _CACHE["datasets"] = None
    _CACHE["order"] = None
    _CACHE["gateway"] = None
    _CACHE["ts"] = 0.0

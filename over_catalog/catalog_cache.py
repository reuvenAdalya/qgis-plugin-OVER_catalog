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

TABLES_URL = f"https://www.over.org.il/api/tables"

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
                "est_rows": 0,
                "table_count": 0,
                "versions_url": t.get("versions_url"),
            }
            datasets[ds_id] = rec
            order.append(ds_id)
        rec["table_count"] += 1
        rec["est_rows"] += int(t.get("est_rows") or 0)
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


def datasets(source_type=None, organization=None, spatial_only=False):
    """
    Records matching the given source_type / organization filters, sorted by
    title. `organization` matches the same "—" fallback used for the tree.
    """
    _ensure()
    out = []
    for rec in get_all():
        if source_type is not None and rec.get("source_type") != source_type:
            continue
        if organization is not None \
                and (rec.get("organization") or "—") != organization:
            continue
        if spatial_only and not rec["is_spatial"]:
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

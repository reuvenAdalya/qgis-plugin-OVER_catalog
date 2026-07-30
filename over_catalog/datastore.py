# -*- coding: utf-8 -*-
"""
Route B — load a layer from the NEON datastore via datastore_search_sql.

This uses the (undocumented but sanctioned) internal endpoint
    GET /api/append/{dataset_id}/datastore_search_sql?sql=<SELECT ...>
which runs a READ-ONLY SELECT/WITH against the dataset's Postgres table.
PostGIS is available, so we ask for geometry as GeoJSON and can filter
server-side by the current map extent with ST_Intersects.

Geometry is WGS84 (EPSG:4326), so the memory layer CRS is fixed to 4326.

Route A (whole-file GeoJSON via /versions/latest) stays the default-quick
path; Route B is the spatially-filterable path and the configured default.
"""

import json
import time

from qgis.core import (
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsFields,
    QgsProject, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsRectangle, QgsMessageLog, Qgis,
)
from qgis.PyQt.QtCore import QUrl, QMetaType

from . import api
from . import catalog_cache

APPEND_BASE = "https://www.over.org.il/api/append"

# Soft cap on features pulled into a memory layer. Raised from an earlier
# 5000 once paging (below) made higher caps possible: live inventory of the
# 892 spatial (idx-schema) tables tops out at 82,419 rows, and 50,000 covers
# all but one of them. A load that still hits the cap is reported to the
# caller as truncated (see _fetch_paged) rather than silently cut.
DEFAULT_ROW_CAP = 50000

# Source types confirmed (live) to have their own NEON append DB, so their
# own dataset_id works directly against datastore_search_sql. Everything
# else — govmap and scraper, ~98% of the catalog — 409s on its own id and
# needs the gateway. Skipping the doomed direct attempt for those matters
# because it is paid on EVERY page of a paged fetch, not just once. If this
# heuristic is ever wrong for some dataset, run_sql's 409 handling below
# still catches it.
NEON_SOURCE_TYPES = {"ckan", "cbs"}

# A paged fetch fires many requests in quick succession, which can trip the
# server's rate limiting (HTTP 429) partway through — observed around page
# ~19-20. REQUEST_DELAY_S paces consecutive page requests to make that less
# likely; MAX_RETRIES/RETRY_BACKOFF_S ride out a transient failure anyway.
REQUEST_DELAY_S = 0.3
MAX_RETRIES = 2
RETRY_BACKOFF_S = 2.0

# Transient HTTP statuses worth retrying: rate limiting (429) and gateway/
# upstream hiccups (502/503/504). The latter show up on slow queries —
# e.g. whole-layer ST_AsGeoJSON on complex polygons takes several seconds and
# the proxy in front of the datastore sometimes times out with 502 or drops
# the connection. A transport-level drop ("Connection closed") arrives with
# status None, so that is treated as transient too.
_TRANSIENT_STATUSES = {429, 502, 503, 504}


def _is_transient(exc):
    status = getattr(exc, "status", None)
    return status in _TRANSIENT_STATUSES or status is None

# The datastore_search_sql endpoint silently truncates any single query to
# this many rows, REGARDLESS of the LIMIT clause in the SQL (confirmed live:
# a 4M-row table queried with LIMIT 5000 came back with 1000 rows and
# result.truncated=True; LIMIT 1000 came back untruncated). LIMIT/OFFSET
# paging within one query's row window is not affected by the cap (LIMIT
# 1000 OFFSET 1000 correctly returns the next 1000), so DEFAULT_ROW_CAP is
# reached by issuing multiple page-sized queries — see _fetch_paged.
SERVER_PAGE_CAP = 1000

# Column that holds geometry in the datastore tables.
GEOM_COL = "geom"
GEOJSON_ALIAS = "_geojson"

# PostGIS functions live in the `extensions` schema, which is NOT on the
# datastore endpoint's search_path — every ST_* call must be schema-qualified
# or the endpoint replies 400.
PG = "extensions"


class DatastoreError(Exception):
    pass


# --------------------------------------------------------------------------
# SQL execution
# --------------------------------------------------------------------------

def run_sql(dataset_id, sql, gateway_id=None, source_type=None):
    """
    Execute a read-only SELECT/WITH via datastore_search_sql, retrying a
    small number of times (with backoff) on a transient failure — see
    _is_transient (429 / 502 / 503 / 504 / dropped connection). A failure that
    persists past the retries is raised; _fetch_paged decides whether to
    salvage a partial page sequence from it.

    Returns the decoded JSON payload (CKAN `result` envelope unwrapped).
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _run_sql_routed(dataset_id, sql, gateway_id, source_type)
        except api.OverApiError as exc:
            if _is_transient(exc) and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_S * (attempt + 1))
                continue
            raise


def _run_sql_routed(dataset_id, sql, gateway_id, source_type):
    """
    One attempt, with gateway routing but no 429 retry (see run_sql).

    When `source_type` is known (from a catalog_cache record) and is NOT in
    NEON_SOURCE_TYPES, the direct attempt on `dataset_id` is skipped — it is
    guaranteed to 409 — and the call goes straight to the gateway. Otherwise
    (source_type unknown, or it IS a NEON type) the dataset's own endpoint is
    tried first, falling back to `gateway_id` — any ckan dataset, whose
    endpoint IS NEON-backed — on HTTP 409. A gateway's _sql can read the
    whole shared database (all schemas, including the idx spatial tables), so
    the target table named in `sql` is reached regardless of which id fronts
    the request. `gateway_id` defaults to catalog_cache.pick_gateway().
    """
    gateway = gateway_id or catalog_cache.pick_gateway()
    if source_type is not None and source_type not in NEON_SOURCE_TYPES \
            and gateway:
        return _run_sql_on(gateway, sql)
    try:
        return _run_sql_on(dataset_id, sql)
    except api.OverApiError as exc:
        if getattr(exc, "status", None) == 409 and gateway \
                and gateway != dataset_id:
            return _run_sql_on(gateway, sql)
        raise


def _run_sql_on(id_, sql):
    """Single datastore_search_sql call fronted by `id_`; no fallback."""
    url = f"{APPEND_BASE}/{id_}/datastore_search_sql?sql=" \
          + QUrl.toPercentEncoding(sql).data().decode("ascii")
    payload = api.fetch_json(url)
    # CKAN-style envelope: {success, result:{records, fields, ...}} — but be
    # lenient about shape.
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


# --------------------------------------------------------------------------
# SQL builders (bbox values are validated floats, never raw strings)
# --------------------------------------------------------------------------

def _quote_ident(name):
    """Minimal identifier quoting for one identifier from /api/tables."""
    return '"' + str(name).replace('"', '""') + '"'


def _qualified_table(table, schema=None):
    """`"schema"."table"` when a schema is given, else just `"table"`."""
    tbl = _quote_ident(table)
    return f"{_quote_ident(schema)}.{tbl}" if schema else tbl


def build_sql(table, schema=None, bbox=None, where=None,
              limit=SERVER_PAGE_CAP, offset=0):
    """
    SELECT every column plus geometry as GeoJSON, optionally filtered by a
    bbox (ST_Intersects) and/or a raw WHERE fragment (advanced use).

    schema: DB schema the table lives in (spatial tables are in `idx`); None
            leaves the table unqualified.
    bbox:   (xmin, ymin, xmax, ymax) in EPSG:4326, or None.
    limit/offset: one page's window. `limit` beyond SERVER_PAGE_CAP has no
                  effect on a single call — page via _fetch_paged instead.

    All PostGIS calls are `extensions.`-qualified (see PG) — the endpoint's
    search_path does not include the extensions schema.
    """
    # Bandit B608: this builds SQL by string formatting, but safely — table
    # and schema identifiers are quoted (_quote_ident), bbox values are
    # float-coerced and limit/offset int-coerced. `where` is a deliberate
    # user-supplied read-only SQL filter (the "advanced query" feature),
    # executed against the read-only datastore_search_sql endpoint which takes
    # a full SQL string and cannot be parameterized.
    tbl = _qualified_table(table, schema)
    select = (f'SELECT *, {PG}.ST_AsGeoJSON({GEOM_COL}) AS {GEOJSON_ALIAS} '  # nosec B608
              f'FROM {tbl}')

    clauses = []
    if bbox is not None:
        xmin, ymin, xmax, ymax = (float(v) for v in bbox)  # validate -> float
        clauses.append(
            f"{PG}.ST_Intersects({GEOM_COL}, "
            f"{PG}.ST_MakeEnvelope({xmin},{ymin},{xmax},{ymax}, 4326))"
        )
    if where:
        clauses.append(f"({where})")

    if clauses:
        select += " WHERE " + " AND ".join(clauses)
    select += f" LIMIT {int(limit)}"
    if offset:
        select += f" OFFSET {int(offset)}"
    return select


def _fetch_paged(dataset_id, table, schema=None, bbox=None, where=None,
                 row_cap=DEFAULT_ROW_CAP, gateway_id=None, source_type=None):
    """
    Accumulate up to `row_cap` records by issuing repeated SERVER_PAGE_CAP-
    sized SELECT ... LIMIT/OFFSET queries (the endpoint's per-query cap, see
    SERVER_PAGE_CAP). Stops once a page comes back shorter than requested
    (genuinely no more rows) or row_cap is reached.

    Relies on stable physical row order across calls to the same read-only
    snapshot table — reasonable here since datastore tables are versioned
    append-only snapshots, not concurrently modified.

    Returns (records, truncated): `truncated` is True when fewer rows were
    returned than the table may actually hold — either row_cap was hit while
    a full page was still coming back, or the server kept failing transiently
    (429/502/503/504/dropped) after run_sql's own retries and we stopped
    rather than losing the pages already fetched. Never True just because the
    table ran out.
    """
    records = []
    offset = 0
    truncated = False
    first_page = True
    while len(records) < row_cap:
        if not first_page:
            time.sleep(REQUEST_DELAY_S)  # pace requests, see REQUEST_DELAY_S
        first_page = False

        page_size = min(SERVER_PAGE_CAP, row_cap - len(records))
        sql = build_sql(table, schema=schema, bbox=bbox, where=where,
                        limit=page_size, offset=offset)
        try:
            result = run_sql(dataset_id, sql, gateway_id=gateway_id,
                             source_type=source_type)
        except api.OverApiError as exc:
            if _is_transient(exc) and records:
                # Keep what we already fetched rather than discarding it.
                truncated = True
                break
            raise
        page = result.get("records") if isinstance(result, dict) else result
        if not page:
            break
        records.extend(page)
        offset += len(page)
        if len(page) < page_size:
            break  # short page -> genuinely no more rows
        if len(records) >= row_cap:
            truncated = True  # stopped by the cap, not by data running out
    return records, truncated


# --------------------------------------------------------------------------
# Building the memory layer
# --------------------------------------------------------------------------

_META_FIELD_TYPES = {
    bool: QMetaType.Type.Bool,
    int: QMetaType.Type.LongLong,
    float: QMetaType.Type.Double,
}


def _qgs_field(key, val):
    """
    Build a QgsField for `val`'s Python type.

    QgsField's QVariant.Type constructor is deprecated as of QGIS 3.38 and
    QVariant.Bool/.LongLong/.Double/.String don't exist under Qt6 (QGIS 4),
    so QMetaType.Type is used. The QMetaType overload itself only exists on
    QGIS >= 3.38 though, so 3.34-3.37 builds (this plugin's stated minimum)
    fall back to the old QVariant.Type constructor.
    """
    meta_type = _META_FIELD_TYPES.get(type(val), QMetaType.Type.QString)
    try:
        return QgsField(key, meta_type)
    except TypeError:
        from qgis.PyQt.QtCore import QVariant
        variant_types = {
            bool: QVariant.Bool,
            int: QVariant.LongLong,
            float: QVariant.Double,
        }
        return QgsField(key, variant_types.get(type(val), QVariant.String))


def _fields_from_record(record):
    """Infer QgsFields from one record, skipping the geometry columns."""
    fields = QgsFields()
    for key, val in record.items():
        if key in (GEOM_COL, GEOJSON_ALIAS, "geometry_wkt"):
            continue
        fields.append(_qgs_field(key, val))
    return fields


def layer_from_records(records, name):
    """Build an in-memory QgsVectorLayer (EPSG:4326) from datastore records."""
    if not records:
        raise DatastoreError("no rows returned")

    fields = _fields_from_record(records[0])
    attr_names = [f.name() for f in fields]

    geom_type = _detect_geom_type(records) or "MultiPolygon"
    layer = QgsVectorLayer(f"{geom_type}?crs=EPSG:4326", name, "memory")

    pr = layer.dataProvider()
    pr.addAttributes(fields)
    layer.updateFields()

    feats = []
    for rec in records:
        gj = rec.get(GEOJSON_ALIAS)
        if not gj:
            continue
        geom = _geojson_to_geometry(gj)
        f = QgsFeature(layer.fields())
        f.setGeometry(geom)
        f.setAttributes([rec.get(n) for n in attr_names])
        feats.append(f)

    pr.addFeatures(feats)
    layer.updateExtents()
    return layer


def _detect_geom_type(records):
    """Peek at the first GeoJSON geometry to choose the memory layer type."""
    for rec in records:
        gj = rec.get(GEOJSON_ALIAS)
        if not gj:
            continue
        try:
            t = json.loads(gj).get("type", "")
        except (ValueError, TypeError):
            return None
        return {
            "Point": "Point", "MultiPoint": "MultiPoint",
            "LineString": "LineString", "MultiLineString": "MultiLineString",
            "Polygon": "Polygon", "MultiPolygon": "MultiPolygon",
        }.get(t)
    return None


def _geojson_to_geometry(geojson_str):
    """
    Convert a GeoJSON geometry string to a QgsGeometry via OGR (always
    available in QGIS, robust for all geometry types).
    """
    from osgeo import ogr
    g = ogr.CreateGeometryFromJson(geojson_str)
    if g is None:
        return QgsGeometry()
    return QgsGeometry.fromWkt(g.ExportToWkt())


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def load_layer(dataset_id, spatial_table, name, schema=None,
               bbox=None, where=None, row_cap=DEFAULT_ROW_CAP,
               gateway_id=None, source_type=None):
    """
    Load a datastore layer. If bbox is given, filter server-side by extent.
    `schema` is the table's DB schema (`idx` for spatial tables); `gateway_id`
    fronts the request when `dataset_id`'s own endpoint 409s, and
    `source_type` (from the catalog_cache record) lets that 409 be skipped
    entirely for known non-NEON sources — see run_sql.

    Returns (layer, truncated): `truncated` is True if row_cap was hit with
    more data still available — the caller should tell the user, since more
    rows exist than were loaded.
    """
    if not spatial_table:
        raise DatastoreError("dataset has no spatial table")

    records, truncated = _fetch_paged(
        dataset_id, spatial_table, schema=schema, bbox=bbox, where=where,
        row_cap=row_cap, gateway_id=gateway_id, source_type=source_type)
    if not records:
        raise DatastoreError("no features in the requested area")

    layer = layer_from_records(records, name)
    QgsProject.instance().addMapLayer(layer)
    return layer, truncated


# --------------------------------------------------------------------------
# Free-form SQL (advanced query over the whole shared database)
# --------------------------------------------------------------------------

def _table_layer_from_records(records, name):
    """A NoGeometry (attribute-only) memory layer for a non-spatial result."""
    fields = _fields_from_record(records[0])
    attr_names = [f.name() for f in fields]
    layer = QgsVectorLayer("None", name, "memory")
    pr = layer.dataProvider()
    pr.addAttributes(fields)
    layer.updateFields()
    feats = []
    for rec in records:
        f = QgsFeature(layer.fields())
        f.setAttributes([rec.get(n) for n in attr_names])
        feats.append(f)
    pr.addFeatures(feats)
    return layer


def build_result_layer(records, name):
    """
    Build a layer from arbitrary query records: spatial (EPSG:4326) when the
    result carries the `_geojson` alias, otherwise an attribute-only table.
    """
    if not records:
        raise DatastoreError("no rows returned")
    if any(r.get(GEOJSON_ALIAS) for r in records):
        return layer_from_records(records, name)
    return _table_layer_from_records(records, name)


def load_free_query(sql, name):
    """
    Run a full user-written SELECT/WITH against the shared database (via the
    ckan gateway, which can read every schema) and load the result as a layer.
    Include `extensions.ST_AsGeoJSON(geom) AS _geojson` in the SELECT to get a
    spatial layer; otherwise the result loads as an attribute table. Subject to
    the server's 1000-row-per-query cap (returned as `truncated`).

    Returns (layer, truncated).
    """
    gateway = catalog_cache.pick_gateway()
    if not gateway:
        raise DatastoreError("no gateway dataset available for the query")
    result = run_sql(gateway, sql, gateway_id=gateway, source_type="ckan")
    if isinstance(result, dict):
        records = result.get("records") or []
        truncated = bool(result.get("truncated"))
    else:
        records, truncated = result or [], False
    if not records:
        raise DatastoreError("no rows returned")

    layer = build_result_layer(records, name)
    QgsProject.instance().addMapLayer(layer)
    return layer, truncated


def geometry_type(dataset_id, table, schema=None, source_type=None,
                  gateway_id=None):
    """
    Return the table's real geometry type (e.g. 'MULTIPOLYGON', 'POINT') by
    sampling one non-null row, or None on failure. The datastore columns are
    declared as generic `geometry`, so this is the cheap way to learn the
    actual shape for the tree icon — one small LIMIT 1 query.
    """
    tbl = _qualified_table(table, schema)
    sql = (f'SELECT {PG}.GeometryType({GEOM_COL}) AS t '  # nosec B608
           f'FROM {tbl} WHERE {GEOM_COL} IS NOT NULL LIMIT 1')
    try:
        result = run_sql(dataset_id, sql, gateway_id=gateway_id,
                         source_type=source_type)
    except api.OverApiError:
        return None
    records = result.get("records") if isinstance(result, dict) else result
    if records:
        return records[0].get("t")
    return None


def canvas_bbox_4326(iface):
    """Current map extent as (xmin,ymin,xmax,ymax) in EPSG:4326."""
    canvas = iface.mapCanvas()
    extent = canvas.extent()
    src = canvas.mapSettings().destinationCrs()
    dst = QgsCoordinateReferenceSystem("EPSG:4326")
    if src != dst:
        tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
        extent = tr.transformBoundingBox(extent)
    return (extent.xMinimum(), extent.yMinimum(),
            extent.xMaximum(), extent.yMaximum())

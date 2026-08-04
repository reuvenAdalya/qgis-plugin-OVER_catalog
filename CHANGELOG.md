# Changelog

## 1.1.0

- **File-only datasets** — the catalog now also includes datasets that exist
  in `/api/v1` but have no datastore table (absent from `/api/tables`). These
  are often the heavy layers (100k+ rows, e.g. GPKG) stored as files only.
  They load via Route A (file). Spatiality is inferred cheaply from the source
  type / source file formats so the spatial-only filter still applies.
- **More file formats** — Route A now opens GPKG, KML and GML (not just
  GeoJSON) with the correct GDAL driver, chosen by format rather than the
  extension-less R2 filename.
- **"Show all files" setting** — optionally list every resource in a dataset,
  including ones QGIS can't open as a layer (symbology zips, PDF, XML, ...),
  as download / open-in-browser links.

## 1.0.2

- The "current view" datastore item in the Browser now shows the correct
  geometry-type icon (point / line / polygon) instead of always a point. The
  geometry type is sampled once per dataset (one small query) and cached.

## 1.0.1

- Resolve QGIS plugin-repository Bandit security-scan findings: annotate the
  safe read-only SQL construction (`# nosec B608` — identifiers are quoted,
  bbox/limit values are numeric-coerced, and the WHERE fragment is a
  deliberate read-only advanced-query feature), and log instead of silently
  passing in two exception handlers (B110). No functional change.

## 1.0.0

- First public release.
- Browser tree: source type → organization → dataset.
- Locator search reveals a dataset in the tree.
- Load a whole GeoJSON file or CSV table.
- Live "current view" datastore layer (Route B), filtered to the map extent,
  auto-paged up to 50,000 rows.
- Advanced per-layer query (bbox + WHERE, with a field picker) and database-
  wide free-form SQL query.
- Access to previous versions.
- Spatial-only tree filter (default on) and settings/help dialog.

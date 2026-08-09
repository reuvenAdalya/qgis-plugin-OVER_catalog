# Changelog

## 1.3.0

- **Three tree display modes** (Settings, replacing the old "spatial only" /
  "show all files" toggles):
  1. *Spatial only* — only spatial datasets/files (layers with geometry):
     SHP, ZIP, GeoJSON, FGB, GPKG, GeoParquet, GML, KML, TIFF, PNG, plus
     style/definition files (SLD, QML, QLR, LYR).
  2. *All openable* (default) — the above plus non-spatial data QGIS/GDAL can
     open as a table (CSV, XLS/XLSX, TXT, ...).
  3. *All files* — every resource; ones QGIS can't open as a layer (PDF, XML,
     ...) are shown as download links marked with a ⚠ warning.
  A dataset is hidden in modes 1/2 when it has no content for that mode; mode
  3 shows every dataset.
- **Extensions on every file leaf** — file nodes (and their loaded layer
  names) now always carry their extension, including `.geojson`.
- **Copy server location** — right-clicking any file (leaf, container or
  download link) offers to copy its raw server URL, for opening in other tools.
- **ZIP handling** — over.org.il's ZIPs are symbology bundles (`_symbology`,
  holding QML/SLD styles); these are shown as download links in the spatial
  group. A non-symbology ZIP (a zipped shapefile) instead expands in the tree
  and lists its vector sublayer(s) (the `.dbf`/`.shx`/`.prj` sidecars fold
  into the single `.shp` entry).
- **Raster and style/definition resources** — TIFF/PNG load as raster layers;
  SLD/QML/QLR/LYR are shown (in spatial mode too) as download links.
- **Info-table fallback** — datasets whose datastore table has no geometry
  column and no file resources (bare lat/lon or X/Y fields) are now reachable:
  a "load as info table" / advanced-query action on the dataset's right-click
  menu (and, when the dataset has no other children, a leaf) loads the raw
  table as an attribute-only layer. Hidden by the "spatial only" mode.
- **QGIS 4 / Qt6 compatibility** — datastore fields are built via
  `QMetaType.Type` (falling back to `QVariant.Type` on QGIS 3.34–3.37), and
  the bulk `/api/tables` call follows redirects via `RedirectPolicyAttribute`
  where `FollowRedirectsAttribute` was removed. Declared
  `qgisMaximumVersion=4.99`. (Qt6 fixes contributed by Shai Sussman.)

## 1.2.0

- **Container files** — GPKG, GeoParquet and FlatGeobuf resources are now
  shown as expandable nodes in the Browser tree. Expanding one lists its
  sublayers (via a fast metadata query, no feature scan), and each sublayer
  loads on its own. GeoParquet and FlatGeobuf are now recognized as vector
  formats. Driver selection relies on the file extension.

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

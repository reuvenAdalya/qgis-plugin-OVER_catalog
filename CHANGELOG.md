# Changelog

## 1.0.3

- QGIS 4 / Qt6 compatibility: the datastore layer builder used the
  `QVariant.Type`-based `QgsField` constructor; `QVariant.Bool` /
  `.LongLong` / `.Double` / `.String` no longer exist under Qt6, which broke
  building the "current view" layer. Fields are now built via
  `QMetaType.Type`, falling back to `QVariant.Type` on QGIS 3.34-3.37 (where
  the `QMetaType` overload of `QgsField` does not yet exist).
- Declared `qgisMaximumVersion=4.99` in `metadata.txt` so the plugin is
  listed for QGIS 4, per the
  [official migration guide](https://plugins.qgis.org/docs/migrate-qgis4).
- Datasets whose datastore table has no PostGIS geometry column and no file
  resources (e.g. bare `lat`/`lon` or `X_Coordinate`/`Y_Coordinate` fields —
  over.org.il doesn't expose these as a real `geometry` column) previously
  fell through to a bare "no loadable files" error, with no way to reach
  their data at all. A "load as info table" / "advanced query" action on the
  dataset's right-click context menu now loads that raw table directly (no
  bbox, since there's no geometry to filter by extent). Like all non-spatial
  datasets, it's hidden by the "spatial only" tree filter, which defaults on
  — turn it off in Settings to see these datasets in the tree at all.

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

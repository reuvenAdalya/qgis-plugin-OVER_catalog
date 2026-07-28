# Changelog

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

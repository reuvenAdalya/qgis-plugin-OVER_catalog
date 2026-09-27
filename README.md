# OVER Catalog — QGIS plugin

Browse and load the **גרסאות לעם** (OVER, [over.org.il](https://www.over.org.il/))
catalog of Israeli public datasets directly into QGIS — spatial layers,
attribute tables, and live datastore queries.

דפדוף וטעינה של מאגרי פרויקט **גרסאות לעם** ([over.org.il](https://www.over.org.il/))
ישירות ל-QGIS — שכבות מרחביות, טבלאות, ושאילתות חיות למסד הנתונים.

> "גרסאות לעם" היא מערכת שעוקבת אחרי שינויים במאגרי מידע ממשלתיים וציבוריים
> בישראל. המערכת החלה במעקב אחרי המאגרים הפתוחים באתר data.gov.il, וכיום היא
> מנטרת גם מקורות נוספים: מסד הנתונים של הכנסת, אתר הלשכה המרכזית לסטטיסטיקה
> (הלמ"ס), שכבות המידע המרחבי של GovMap, פרוטוקולי ועדות, מכרזים, החלטות ממשלה
> ועוד.

---

## Features

- **Browser tree** — an `OVER` node in the QGIS Browser panel:
  source type (govmap / data.gov.il / scraper) → organization → dataset.

- **Original symbology, applied on load** — most datasets ship GovMap's own
  style (SLD + SVG icons); it is applied as the layer loads, along with the
  Hebrew field captions. On by default, and the right-click menu always offers
  the opposite.

- **Three display modes** — spatial layers only / everything QGIS can open
  (default) / every file, with the non-openable ones marked `⚠`.

- **Container files** — GPKG, GeoParquet, etc  expand in the tree and
  list their sublayers; each loads on its own.

- **Search** — type a dataset name in the QGIS Locator (the search bar at the
  bottom of the window) to reveal it in the tree.

- **Advanced SQL queries** — per-layer (WHERE + field picker) or database-wide
  (free-form SQL).

- **Copy server location** — right-click any file to copy its raw URL for use
  in other tools.

- **Load** — read-only, on demand.

- **Settings & help** — Plugins → OVER Catalog → הגדרות...

## Data structure — the items under each dataset

A dataset may hold several files of different types. Every file leaf shows its
extension, and right-clicking one copies its location on the server.

| Item | What it is |
| --- | --- |
| **Spatial files** (`.geojson`, `.gpkg`, `.parquet`, `.fgb`, `.kml`, `.gml`, …) | The stored spatial snapshot. Double-click loads the layer; containers expand to their sublayers. |
| **Tables** (`.csv`, `.xlsx`, `.txt`) | Attributes only, no geometry. |
| **Symbology bundle** (`.zip`) | GovMap's original style (SLD + icons) and the field dictionary — applied automatically, not loaded as a layer. |
| **🗺 תצוגה נוכחית** (current view) | A live datastore query, filtered to the current map extent. |
| **גרסאות קודמות** (previous versions) | Historical snapshots; each version with its own files. |
| **טען כטבלת מידע** (load as info table) | For datasets with a datastore table but no geometry column — right-click the dataset to load the raw table. |

### Symbology

The style is applied when the layer is **added**, so it works for a
double-click, a drag onto the canvas, and "Add Selected Layers" alike. Not
every layer has one: some bundles carry only the field dictionary, in which
case just the Hebrew captions are applied. If a style exists but filters on a
field the published data does not contain, it is skipped rather than applied —
applying it would match zero features and leave the layer blank. Reopening a
saved project keeps the styling saved in that project.

## Query options and how they differ

| Action | Result |
| --- | --- |
| Double-click a **file** | The whole file from the stored snapshot, no filter, styled with the dataset's symbology. |
| Double-click **🗺 תצוגה נוכחית** | Live load filtered to the current map view. |
| Right-click a **file** | Load with/without symbology (whichever is not the current default), and copy the file's server location. |
| Right-click **🗺** | Current view / whole layer / **advanced query** (bbox + WHERE, with a field picker). Auto-pages up to 50,000 rows. |
| Right-click **OVER / source type / organization** | **Advanced query**: free-form SQL over the whole database. Limited to 1000 rows per query. |

SQL is PostgreSQL / PostGIS. In the WHERE / SQL editors, quote column names
with double quotes (`"שם תחנה"`) and string values with single quotes; for
geometry include `extensions.ST_AsGeoJSON(geom) AS _geojson`.

## Install

- **Recommended:** QGIS → Plugins → Manage and Install Plugins → search
  "OVER Catalog".
- **Manual:** copy the `over_catalog/` folder into your QGIS profile's
  `python/plugins/` directory, then enable it in the Plugins manager.

Requires **QGIS 3.34** or newer, including QGIS 4 (Qt6).

## Data source & attribution

Data is served by the **גרסאות לעם** project ([over.org.il](https://www.over.org.il/)),
which version-tracks Israeli public datasets from data.gov.il, the Knesset
database, the Central Bureau of Statistics (CBS), GovMap spatial layers, and
more. The plugin uses the project's public `/api/v1` endpoints, the
`/api/tables` catalog, `/api/tables/{table}/features` (GeoJSON by bbox, for
the live current-view layer) and `/api/append/{id}/datastore_search`. Only the
advanced WHERE query and the free-SQL dialog use
`/api/append/.../datastore_search_sql`, which the server may restrict to
authenticated callers. Use of these endpoints was confirmed as permitted by
the over.org.il maintainer. All network requests go through `QgsNetworkAccessManager` (QGIS
proxy settings are respected). Data is loaded read-only.

## Credits / based on

OVER Catalog is a QGIS client for the **גרסאות לעם** service
([over.org.il](https://www.over.org.il/)), which is powered by the open-source
**ckan-version-tracker** project by Guy Zomer:
<https://github.com/zomer-g/ckan-version-tracker> (MIT). This plugin is a
separate project that consumes that service's public API — it is not a fork of
ckan-version-tracker.

## Authors

Reuven Kost, Guy Zomer.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).

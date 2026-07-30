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
  By default only spatial datasets are shown (toggle in the settings).
- **Search** — type a dataset name in the QGIS Locator (the search bar at the
  bottom of the window) to reveal it in the tree.
- **Load** — read-only, on demand.
- **Advanced SQL queries** — per-layer (WHERE + field picker) or database-wide
  (free-form SQL).
- **Settings & help** — Plugins → OVER Catalog → הגדרות...

## Data structure — the items under each dataset

| Item | What it is |
| --- | --- |
| **GeoJSON** | The full spatial file as stored. Double-click loads the whole layer. |
| **CSV** | The data table (attributes only, no geometry). |
| **🗺 תצוגה נוכחית** (current view) | A live datastore query, filtered to the current map extent. |
| **גרסאות קודמות** (previous versions) | Historical snapshots; each version with its own files. |

## Query options and how they differ

| Action | Result |
| --- | --- |
| Double-click **GeoJSON** | The whole file from the stored snapshot, no filter. |
| Double-click **🗺 תצוגה נוכחית** | Live load filtered to the current map view. |
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

Requires **QGIS 3.34** or newer.

## Data source & attribution

Data is served by the **גרסאות לעם** project ([over.org.il](https://www.over.org.il/)),
which version-tracks Israeli public datasets from data.gov.il, the Knesset
database, the Central Bureau of Statistics (CBS), GovMap spatial layers, and
more. The plugin uses the project's public `/api/v1` endpoints as well as the
internal `/api/tables` and `/api/append/.../datastore_search_sql` endpoints;
use of the internal endpoints was confirmed as permitted by the over.org.il
maintainer. All network requests go through `QgsNetworkAccessManager` (QGIS
proxy settings are respected). Data is loaded read-only.

## Credits / based on

OVER Catalog is a QGIS client for the **גרסאות לעם** service
([over.org.il](https://www.over.org.il/)), which is powered by the open-source
**ckan-version-tracker** project by Guy Zomer:
<https://github.com/zomer-g/ckan-version-tracker> (MIT). This plugin is a
separate project that consumes that service's public API — it is not a fork of
ckan-version-tracker.

## Authors

Reuven Kost, Guy Zomer, Shai Sussman.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).

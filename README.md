# OVER Catalog — QGIS plugin

Browse and load the **גרסאות לעם** (OVER, [over.org.il](https://www.over.org.il/))
catalog of Israeli public datasets directly into QGIS — spatial layers,
attribute tables, and live datastore queries.

דפדוף וטעינה של מאגרי פרויקט **גרסאות לעם** ([over.org.il](https://www.over.org.il/))
ישירות ל-QGIS — שכבות מרחביות, טבלאות, ושאילתות חיות למסד הנתונים.

---

## What it does / מה הכלי מאפשר

- **Browser tree** — an `OVER` node in the QGIS Browser panel:
  source type (govmap / data.gov.il / scraper) → organization → dataset.
- **Search** — type a dataset name in the QGIS Locator (search bar) to reveal
  it in the tree.
- **Load** — each dataset exposes:
  - **GeoJSON** — the full spatial file (double-click loads the whole layer).
  - **CSV** — the attribute table.
  - **🗺 תצוגה נוכחית (current view)** — a live datastore query filtered to the
    current map extent (auto-pages up to 50,000 rows).
  - **גרסאות קודמות (previous versions)** — historical snapshots.
- **Advanced queries** — right-click the current-view leaf for a WHERE/SQL
  query builder (with a field picker), or right-click a folder node for a
  full free-form SQL query over the whole database.

## Install

- **Recommended:** QGIS → Plugins → Manage and Install Plugins → search
  "OVER Catalog".
- **Manual:** copy the `over_catalog/` folder into your QGIS profile
  `python/plugins/` directory, then enable it in the Plugins manager.

Requires QGIS 3.34 or newer.

## Data source & attribution

Data is served by the **גרסאות לעם** project (over.org.il), which version-
tracks Israeli public datasets from data.gov.il, the Knesset database, the
Central Bureau of Statistics (CBS), GovMap spatial layers, and more. The
plugin uses the project's public `/api/v1` endpoints plus internal
`/api/tables` and `/api/append/.../datastore_search_sql` endpoints; use of the
internal endpoints was confirmed as permitted by the over.org.il maintainer.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).

# Changelog

## 1.6.5

- **The version column is one line, each version carrying its own date:**
  `v2: 2026-07-11 → v3: 2026-07-31`. 1.6.4 stacked bare dates under bare
  versions, which left the reader pairing them by position; this way each date
  is attached to the version it belongs to, and the rows stay one line tall.

## 1.6.4

- **Both version dates are shown in full**, on the same from→to shape as the
  versions above them: `v2 → v3` over `2026-07-11 → 2026-07-31`. How stale the
  data in use is — the gap between the two — is the point of the column, and
  1.6.3 only showed the date being moved to, leaving that gap in a tooltip. The
  dialog opens wider to fit them without squeezing the layer column.

## 1.6.3

- **The version column carries its date.** It now reads `v2 → v3` over
  `2026-07-31` — the same "version · date" shape the Browser tree already uses
  for historical versions, so the same fact reads the same way in both places.
  A row that is up to date shows the date of the version it is on.
- Hovering the cell gives both sides in full ("in use: version 2 · 2026-07-11 /
  latest: version 3 · 2026-07-31"), which is what tells you how old the data
  actually is — the column alone only says what it would move to.
- The Processing report splits `detected` into `from_detected` and
  `to_detected` to match.

## 1.6.2

- The update tool is now called "עדכון גירסאות למאגרי OVER", in its window, its
  progress dialog and the Plugins menu, and opens with a short line saying what
  it is for: it finds the gap between the versions a project holds and the
  latest ones, and updates whichever layers you choose.

## 1.6.1

- **Fixed: the row counts read backwards.** In the right-to-left dialog,
  `69 → 78` displayed as `78 → 69`. The version cell was fine because it opens
  with a strong left-to-right letter (`v1`), which pins the run's direction,
  while a cell opening with a digit inherits the dialog's direction and swaps
  its two halves. Both cells are now wrapped in a directional isolate.
- **The dialog says when the run has finished.** The per-row ticks reported
  each layer, but nothing stated that the operation as a whole was done. A
  status line now appears on completion — green when everything applied, amber
  when some layers failed, red when none did — and it repeats the one thing
  still outstanding: saving the project. The pre-flight warning about versions
  being unrecoverable is hidden once the run is over, and comes back on rescan.

## 1.6.0

- **Update a project's OVER layers to the latest version.** over.org.il keeps
  every version of a file in one bucket folder and changes only the file name,
  so a saved project stays on the version it was saved with — it keeps working
  and quietly goes stale. On a real 12-layer project, four layers turned out to
  be versions behind. Plugins → OVER Catalog → "עדכון שכבות OVER בפרויקט"
  scans the project and shows, per layer, the version it is on, the latest one,
  the row counts on either side, and whether the new version renamed any
  fields. Nothing changes until the user ticks rows and confirms.
- **The same engine as a Processing algorithm** ("Refresh OVER layers to their
  latest version", in `scripts/`), so it works in Model Builder, batch mode,
  `processing.run()` and scheduled triggers. Its report is a table output
  rather than log text, so a model can act on it. Dry run is on by default.
- **Only the data source changes.** Style, labels, opacity, blend mode, scale
  visibility, custom properties, layer variables and the layer id are all kept
  — the id in particular, so joins and project references keep working. The one
  exception found by testing: QGIS clears a layer's filter on a source change,
  so the filter is captured beforehand and re-applied, and reported if it no
  longer applies.
- **Renamed fields are flagged, not acted on.** The same dataset shipped
  `התרעה`/`מרחב` in one version and `hatraha`/`merchav` in the next; a style
  built on the old names survives the swap and then matches nothing. Rows whose
  fields disappeared say so. Replacing the symbology stays a separate, per-row,
  off-by-default choice — it overwrites manual styling.
- Layers are listed with their full group path, and two layers of one dataset
  appear as two rows, because they can be updated independently.

## 1.5.3

- **Auxiliary files now follow the layers instead of leading them.** A symbology
  bundle sorted on the name `_symbology`, and an underscore sorts below Hebrew,
  so it appeared *above* the layers it belongs to. Files QGIS cannot open as a
  layer — symbology bundles, PDFs, XML — now come after the loadable ones and
  still before the history.
- A dataset's children are ordered by five documented groups rather than by
  whatever their names happened to be: the version marker, the live
  "תצוגה נוכחית" leaf, the loadable files, the auxiliary files, and the history.
  Each group still reads alphabetically inside itself, and the scheme replaces
  the control-character sort keys 1.5.2 relied on.

## 1.5.2

- **Fixed: "גרסאות קודמות" was only at the end of the list by luck.** The node
  had no explicit sort key, so the browser sorted it on its own name — it landed
  last only for datasets whose title sorts before it (א, ב, ג) and jumped to the
  *top* of the file list for every title from ד onwards, which is most of them.
  It is now always last.

## 1.5.1

- **Each dataset now says which version it is serving and when.** The first item
  under a dataset reads `🕒 גרסה 3 · 2026-07-31`, naming the active version and
  the date it was detected; the tooltip adds the full timestamp and the row
  count. Historical versions were already labelled with their dates under
  "גרסאות קודמות", while the version actually in use was not, so there was no
  way to tell how current the data was without opening the history.
- The date comes from the same `/versions/latest` response the file leaves are
  already built from, so it costs no extra request.

## 1.5.0

- **ESRI `.lyr` styles are now applied, via SLYR.** Some datasets ship no SLD
  and instead zip an ArcGIS `.lyr` beside the shapefile it styles (for example
  `BUS_TERMINAL_STRAT.zip`, which holds the `.shp` parts plus
  `BUS_TERMINAL_STRAT.lyr`). When the **SLYR (Community Edition)** plugin is
  installed, the `.lyr` is converted once, cached, and applied to every layer of
  that dataset — the shapefile from the archive, the GeoJSON and the live
  datastore layer alike. It also brings the **labelling**, which the SLD bundles
  do not carry.
- The conversion targets QML rather than QLR on purpose. A QLR carries the
  layer's original data source, whose path routinely does not point at the
  shapefile shipped beside it; a QML is style-only, so it is applied to the
  layer already opened from the archive and there is no source to correct.
- **Field-name case is reconciled.** A `.lyr` authored against a geodatabase
  spells fields differently from the shapefile (`TERM_NAME` vs `term_name`),
  which would have left the labels silently blank. References are mapped onto
  each layer's own spelling, case-insensitively — and because two
  representations of one dataset can spell a field differently, the mapping is
  done per layer rather than baked into the cache.
- **Without SLYR nothing breaks**: the layer loads unstyled and a one-time
  message recommends installing SLYR. If SLYR is installed later, the cached
  `.lyr` is converted on the next load without re-downloading it.
- An SLD still wins when a dataset somehow has both, since it needs no
  third-party plugin and therefore works for everyone.
- A converted style that refers to a field the data does not publish is skipped
  rather than applied — the same guard the SLD path already used.

## 1.4.4

- **Fixed: "OVER: שגיאת רשת: Host requires authentication" on the current-view
  layer.** over.org.il can require authentication on its per-dataset free-SQL
  endpoint (`/api/append/{id}/datastore_search_sql`), and every datastore load
  went through it — so with that flag on, no datastore layer loads at all. The
  common paths no longer depend on that endpoint.
- The current-view (bbox) load, the whole-layer load and the geometry-type
  probe behind the tree icons now use the public GeoJSON endpoint
  `/api/tables/{table}/features?bbox=…`. It is filtered with the spatial index,
  pages 5000 features at a time instead of 1000, and needs no authentication.
- The info-table fallback of a ckan/cbs dataset uses the public
  `datastore_search` endpoint.
- The advanced WHERE query and the free-SQL dialog still need SQL. When the
  server refuses them, the message now says that authentication is required,
  instead of reporting a network error.

## 1.4.3

- Documentation refresh. The in-plugin help (Settings → הגדרות) and the README
  had both been left describing version 1.0 behaviour. They now cover the three
  display modes, container files and their sublayers, the copy-server-location
  action, the info-table fallback, and how symbology actually behaves —
  including that not every layer has a style, and that a style which would
  blank the layer is skipped.

## 1.4.2

- **Fixed: symbology was never applied to raw file layers.** Only the live
  "current view" layer got styled. The cause was not the data — QGIS's own
  `layer_item` browser provider is registered before any plugin's and handles a
  double-click on a *layer* item itself, adding the layer and stopping the
  chain, so the plugin was never asked. (The datastore leaf is a Custom item,
  which that provider ignores — hence the one route that did work.)
- Styling now happens when the layer is **added** rather than when it is
  clicked, so it no longer depends on winning the double-click. This also
  covers drag-and-drop onto the canvas and "Add Selected Layers", neither of
  which the click path would ever have caught.
- Reopening a saved project keeps the styling saved in it: the hook stands down
  while a project's layers are being restored, and while the plugin is loading
  a layer itself (so "load without symbology" stays without).

## 1.4.1

- **Field captions no longer depend on there being a style.** Not every layer
  has a GovMap style: some `_symbology` bundles carry only the field
  dictionary. Such a bundle was previously discarded whole, so those datasets
  lost their Hebrew field captions too. The dictionary is now applied on its
  own — the layer simply keeps its default rendering.
- The "loaded without symbology" note is limited to the case that warrants it
  (a style exists but references a field the data doesn't publish). A dataset
  GovMap has no style for is ordinary and now passes without a message.

## 1.4.0

- **Original symbology applied on load.** Most datasets ship a `_symbology`
  bundle (GovMap's own SLD, plus SVG icons and a field dictionary). It is now
  fetched and applied as the layer loads, so a layer arrives looking the way it
  does on GovMap — categories, colours and Hebrew legend labels included. Works
  for file layers, container sublayers and the live datastore layer alike (they
  all carry the same attribute names).
- **Hebrew field captions.** The bundle's field dictionary is attached as QGIS
  field aliases, so the attribute table reads `התרעה` rather than `hatraha`.
- **Icons actually render.** The SLD references its markers as `icons/x.svg`,
  relative to itself; QGIS stores that path verbatim and never resolves it, so
  the marker silently failed to draw. The bundle is now extracted to a cache
  under the user's QGIS profile and the paths rewritten to absolute — profile
  rather than temp, because those paths get saved into the project file.
- **A style that would blank the layer is skipped.** A few GovMap styles filter
  on a field that is not actually published (a numeric code whose text twin is
  what ships). Applying one yields rules matching zero features — every feature
  renders unstyled. The style's field references are now checked against the
  layer first, and the layer is left alone (with a note) when they don't match.
  Deliberately no fuzzy name matching: a near-miss name is a different field.
- **Setting + inverse action.** "Apply original symbology" is on by default and
  can be turned off; the right-click menu always offers the opposite of the
  current setting, so both behaviours are one click away either way.
- Applying a cached bundle costs ~0 ms; a first fetch measured 0.03–0.05 s.

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

# Changelog

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

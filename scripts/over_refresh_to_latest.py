# -*- coding: utf-8 -*-
"""
Refresh OVER layers to their latest version — a Processing algorithm.

The non-interactive face of over_catalog.refresh, so the same behaviour is
available to Model Builder, batch mode, processing.run() and anything that
wants to schedule it. The plugin's dialog is the interactive face; both call
the one engine, so they cannot drift apart.

Add it with Processing Toolbox -> Scripts -> Add Script to Toolbox. It needs
the OVER Catalog plugin installed, since that is where the engine lives.

Design note: the report is a TABLE output, not log text, so a model can act on
it — filter to datasets that grew, export it, feed it onward. Dry run is on by
default, because a data-source change cannot be undone.
"""

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingOutputNumber,
    QgsProject,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QMetaType


def _string_field(name):
    """QgsField across the QGIS 3/4 split (see over_catalog.datastore)."""
    try:
        return QgsField(name, QMetaType.Type.QString)
    except TypeError:
        from qgis.PyQt.QtCore import QVariant
        return QgsField(name, QVariant.String)


class OverRefreshToLatest(QgsProcessingAlgorithm):

    LAYERS = "LAYERS"
    DRY_RUN = "DRY_RUN"
    SYMBOLOGY = "SYMBOLOGY"
    CHECK_SCHEMA = "CHECK_SCHEMA"
    OUTPUT = "OUTPUT"
    UPDATED = "UPDATED"
    CURRENT = "CURRENT"
    SKIPPED = "SKIPPED"

    # Repointing project layers has to happen on the main thread.
    def flags(self):
        base = super().flags()
        for holder, attr in ((Qgis.ProcessingAlgorithmFlag, "NoThreading"),
                             (QgsProcessingAlgorithm, "FlagNoThreading")):
            flag = getattr(holder, attr, None)
            if flag is not None:
                return base | flag
        return base

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.LAYERS, self.tr("Layers (leave empty for all)"),
            layerType=QgsProcessing.SourceType.TypeVectorAnyGeometry,
            optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DRY_RUN, self.tr("Report only, change nothing"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SYMBOLOGY,
            self.tr("Also replace symbology from the server "
                    "(overwrites manual styling)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.CHECK_SCHEMA,
            self.tr("Check for renamed fields (downloads each new file)"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Report"),
            type=QgsProcessing.SourceType.TypeVector, optional=True,
            createByDefault=True))
        for name, label in ((self.UPDATED, "Layers updated"),
                            (self.CURRENT, "Layers already current"),
                            (self.SKIPPED, "Layers skipped")):
            self.addOutput(QgsProcessingOutputNumber(name, self.tr(label)))

    def processAlgorithm(self, parameters, context, feedback):
        try:
            from over_catalog import refresh
        except ImportError as exc:
            raise QgsProcessingException(
                "This tool needs the OVER Catalog plugin installed: "
                f"{exc}")

        dry_run = self.parameterAsBool(parameters, self.DRY_RUN, context)
        want_sym = self.parameterAsBool(parameters, self.SYMBOLOGY, context)
        check_schema = self.parameterAsBool(
            parameters, self.CHECK_SCHEMA, context)
        chosen = self.parameterAsLayerList(parameters, self.LAYERS, context)
        chosen_ids = {lyr.id() for lyr in chosen} if chosen else None

        fields = QgsFields()
        for name in ("layer", "dataset", "status", "from_version",
                     "from_detected", "to_version", "to_detected",
                     "rows_before", "rows_after", "renamed_fields", "result"):
            fields.append(_string_field(name))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.NoGeometry)

        def progress(done, total, text):
            feedback.setProgress(int(90 * done / max(total, 1)))
            if text:
                feedback.pushInfo(f"  {text}")
            if feedback.isCanceled():
                raise QgsProcessingException("canceled")

        feedback.pushInfo("Scanning the project for OVER layers…")
        rows = refresh.scan(QgsProject.instance(),
                            check_schema=check_schema, progress=progress)
        if chosen_ids is not None:
            rows = [r for r in rows if r.layer_id in chosen_ids]

        updated = current = skipped = 0
        for row in rows:
            result = ""
            if row.status == refresh.CURRENT:
                current += 1
            elif row.status != refresh.UPDATE:
                skipped += 1
                result = row.message
            elif dry_run:
                updated += 1
                result = f"would update: {row.old_file} -> {row.new_file}"
                feedback.pushInfo(f"→ {row.path}: {result}")
            else:
                ok, message = refresh.apply_update(
                    row, with_symbology=want_sym)
                result = message
                if ok:
                    updated += 1
                    feedback.pushInfo(f"✓ {row.path}: {message}")
                else:
                    skipped += 1
                    feedback.reportError(f"✗ {row.path}: {message}",
                                         fatalError=False)

            if sink:
                feat = QgsFeature(fields)
                feat.setAttributes([
                    row.path, row.dataset_id, row.status,
                    str(row.old_version or ""), row.old_detected,
                    str(row.new_version or ""), row.detected_at,
                    str(row.old_rows if row.old_rows is not None else ""),
                    str(row.new_rows if row.new_rows is not None else ""),
                    ", ".join(row.missing_fields), result,
                ])
                sink.addFeature(feat)

        feedback.setProgress(100)
        verb = "would be updated" if dry_run else "updated"
        feedback.pushInfo(
            f"\n{updated} {verb}, {current} already current, {skipped} skipped.")
        if updated and not dry_run:
            feedback.pushInfo("Save the project to keep the new sources.")

        out = {self.UPDATED: updated, self.CURRENT: current,
               self.SKIPPED: skipped}
        if sink:
            out[self.OUTPUT] = dest
        return out

    # -- registration ------------------------------------------------------

    def name(self):
        return "overrefreshtolatest"

    def displayName(self):
        return self.tr("Refresh OVER layers to their latest version")

    def group(self):
        return self.tr("OVER")

    def groupId(self):
        return "over"

    def shortHelpString(self):
        return self.tr(
            "Repoints project layers loaded from the over.org.il bucket at the "
            "newest version of the same file.\n\n"
            "over.org.il keeps every version in one folder and changes only "
            "the file name, so a saved project stays on the version it was "
            "saved with — it keeps working, it just goes stale silently.\n\n"
            "Only the data source changes: style, labels, layer id and the "
            "rest are kept, and the layer filter is re-applied by hand because "
            "QGIS clears it on a source change. Replacing the symbology is "
            "off by default — it overwrites manual styling.\n\n"
            "Dry run is on by default: a data-source change cannot be undone. "
            "Save the project afterwards to keep the result.")

    def tr(self, string):
        return QCoreApplication.translate("OverRefreshToLatest", string)

    def createInstance(self):
        return OverRefreshToLatest()

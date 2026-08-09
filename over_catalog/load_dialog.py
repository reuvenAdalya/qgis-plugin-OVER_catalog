# -*- coding: utf-8 -*-
"""
Advanced-query dialog for a Route B (datastore) layer.

Reached via right-click "שאילתה מתקדמת..." on the "תצוגה נוכחית" leaf (it is
specific to that spatial datastore layer, not the whole dataset). It offers:
a bbox toggle, a list of the table's fields (double-click inserts the exact,
correctly-quoted column name — Hebrew names are easy to mistype), and a large
multi-line SQL (WHERE fragment) editor. The caller reads `selection()` after
the dialog is accepted and runs the load.
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel, QPlainTextEdit,
    QLineEdit, QListWidget, QListWidgetItem, QDialogButtonBox,
)
from qgis.PyQt.QtGui import QFontDatabase
from qgis.core import Qgis, QgsMessageLog


def _mono(widget):
    """Apply the system fixed-width font, ignoring binding differences."""
    try:
        widget.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    except Exception as exc:  # cosmetic only — log and keep the default font
        QgsMessageLog.logMessage(
            f"OVER: monospace font unavailable: {exc}", "OVER",
            Qgis.MessageLevel.Info)

# Geometry / helper columns that make no sense in a WHERE field picker.
_HIDDEN_COLUMNS = {"geom", "geometry_wkt"}


class AdvancedQueryDialog(QDialog):
    """bbox toggle + field picker + a large SQL (WHERE) editor; see selection()."""

    def __init__(self, title, columns=None, parent=None, show_bbox=True):
        """
        columns: list of {"name", "type"} for the table (from catalog_cache
        `spatial_columns`), or None/empty to hide the field picker.
        show_bbox: False for a table with no geometry column (the table
        fallback context-menu action in data_items.py) — there is nothing to
        filter by extent, so the toggle is hidden and selection()["bbox"] is
        always False.
        """
        super().__init__(parent)
        self.setWindowTitle(f"שאילתה מתקדמת — {title}")
        self._columns = columns or []
        self._show_bbox = show_bbox
        self._build_ui()

    # -- UI ------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        if self._show_bbox:
            self.chk_bbox = QCheckBox("רק בתחום התצוגה הנוכחית (bbox)")
            self.chk_bbox.setChecked(True)
            layout.addWidget(self.chk_bbox)
        else:
            self.chk_bbox = None

        fields = [c for c in self._columns
                  if (c.get("name") not in _HIDDEN_COLUMNS
                      and (c.get("type") or "").lower() != "geometry")]
        if fields:
            layout.addWidget(QLabel("שדות זמינים (לחיצה כפולה מוסיפה):"))
            self.field_list = QListWidget()
            for c in fields:
                name, ctype = c.get("name", ""), c.get("type", "")
                item = QListWidgetItem(f"{name}   ·   {ctype}")
                item.setData(0x0100, name)  # Qt.UserRole -> the raw name
                self.field_list.addItem(item)
            self.field_list.itemDoubleClicked.connect(self._insert_field)
            self.field_list.setMaximumHeight(140)
            layout.addWidget(self.field_list)

        layout.addWidget(QLabel("סינון SQL (WHERE):"))
        self.sql_edit = QPlainTextEdit()
        self.sql_edit.setPlaceholderText('לדוגמה: "שם תחנה" = \'תחנת חברון\'')
        _mono(self.sql_edit)
        self.sql_edit.setMinimumSize(520, 200)
        layout.addWidget(self.sql_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(600, 460)

    def _insert_field(self, item):
        """Double-click a field -> insert its double-quoted name at the cursor."""
        name = item.data(0x0100)
        self.sql_edit.insertPlainText(f'"{name}"')
        self.sql_edit.setFocus()

    # -- result --------------------------------------------------------

    def selection(self):
        """{'bbox': bool, 'where': str|None} — where is None when left blank."""
        where = self.sql_edit.toPlainText().strip() or None
        bbox = self.chk_bbox.isChecked() if self.chk_bbox is not None else False
        return {"bbox": bbox, "where": where}


class FreeQueryDialog(QDialog):
    """
    Full free-form SQL over the whole shared database, offered at the OVER
    root / source_type / organization nodes (the gateway can read every
    schema). The user writes the complete SELECT; `values()` returns
    (sql, layer_name).
    """

    DEFAULT_SQL = (
        'SELECT *, extensions.ST_AsGeoJSON(geom) AS _geojson\n'
        'FROM "idx"."<table>"\n'
        'LIMIT 1000'
    )

    def __init__(self, scope_label, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"שאילתה מתקדמת — {scope_label}")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        warning = QLabel("⚠ השאילתה מוגבלת ל־1000 שורות")
        warning.setStyleSheet("color:#b00020; font-weight:bold;")
        layout.addWidget(warning)

        layout.addWidget(QLabel(
            "שאילתת SQL מלאה (PostgreSQL / PostGIS) על כל מסד הנתונים.\n"
            "לשכבה מרחבית — כלול extensions.ST_AsGeoJSON(geom) AS _geojson."))

        self.sql_edit = QPlainTextEdit()
        self.sql_edit.setPlainText(self.DEFAULT_SQL)
        _mono(self.sql_edit)
        self.sql_edit.setMinimumSize(560, 260)
        layout.addWidget(self.sql_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("שם השכבה:"))
        self.name_edit = QLineEdit("שאילתה")
        row.addWidget(self.name_edit)
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(660, 440)

    def values(self):
        """(sql, layer_name) — sql stripped; name falls back to 'שאילתה'."""
        return (self.sql_edit.toPlainText().strip(),
                self.name_edit.text().strip() or "שאילתה")

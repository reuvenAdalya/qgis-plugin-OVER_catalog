# -*- coding: utf-8 -*-
"""
The "update OVER layers" dialog — the interactive face of refresh.py.

A review table, not a confirm box: repointing a layer cannot be undone (QGIS
has no undo for a data-source change), so the user sees exactly what would
change, per layer, and ticks what they want. The Processing algorithm is the
other face of the same engine, for scripts and models.

Every layer gets its own row, including two layers of the same dataset — they
can be updated independently, and the group path is shown because that is
often the only thing telling them apart.

Replacing the symbology is a separate tick per row, off by default: it
overwrites hand-made styling. When the new version renames fields, the row says
so — but the tick still stays off. Warning, not decision.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QHeaderView, QLabel, QProgressDialog, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from . import refresh

COL_PICK, COL_LAYER, COL_VERSION, COL_ROWS, COL_SYM = range(5)
HEADERS = ["", "שכבה", "גרסה", "שורות", "עדכון סימבולוגיה"]


# Written as escapes, never as the characters themselves: a source file
# holding raw bidirectional controls is the Trojan Source pattern, and
# security scanners rightly reject it (bandit B613).
_LRI = "\u2066"       # left-to-right isolate
_PDI = "\u2069"       # pop directional isolate


def _ltr(text):
    """
    Pin a value to left-to-right inside this RTL dialog.

    "v1 → v3" happens to survive because it opens with a strong LTR letter, but
    "69 → 78" opens with a digit — a weak character — so the paragraph's RTL
    direction wins and the two numbers swap places on screen. Wrapping the run
    in an isolate fixes the order without changing the text.
    """
    return f"{_LRI}{text}{_PDI}"


def _fmt_rows(row):
    if row.old_rows is None and row.new_rows is None:
        return ""
    if row.status != refresh.UPDATE:
        return _ltr(f"{row.new_rows:,}") if row.new_rows is not None else ""
    old = f"{row.old_rows:,}" if row.old_rows is not None else "?"
    new = f"{row.new_rows:,}" if row.new_rows is not None else "?"
    return _ltr(f"{old} → {new}")


class RefreshDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("עדכון שכבות OVER")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.rows = []
        self._build_ui()
        self.resize(820, 520)
        self.scan()

    # -- ui ----------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        head = QHBoxLayout()
        self.summary = QLabel("")
        head.addWidget(self.summary)
        head.addStretch()
        rescan = QPushButton("סרוק מחדש")
        rescan.clicked.connect(self.scan)
        head.addWidget(rescan)
        layout.addLayout(head)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_LAYER, QHeaderView.ResizeMode.Stretch)
        for col in (COL_PICK, COL_VERSION, COL_ROWS, COL_SYM):
            header.setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        bulk = QHBoxLayout()
        self.chk_all = QCheckBox("בחר הכל")
        self.chk_all.stateChanged.connect(self._toggle_all)
        bulk.addWidget(self.chk_all)
        self.chk_all_sym = QCheckBox("עדכון סימבולוגיה לכולן")
        self.chk_all_sym.stateChanged.connect(self._toggle_all_sym)
        bulk.addWidget(self.chk_all_sym)
        note = QLabel("⚠ עדכון סימבולוגיה מוחק עיצוב ידני")
        note.setStyleSheet("color:#8a6d00;")
        bulk.addWidget(note)
        bulk.addStretch()
        layout.addLayout(bulk)

        self.warn = QLabel("⚠ לא ניתן לשחזר את הגרסאות הקודמות. "
                           "שמור עותק של הפרויקט לפני העדכון.")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color:#b00020;")
        layout.addWidget(self.warn)

        # Shown only once the run finishes — the per-row ticks say what
        # happened to each layer, this says the operation as a whole is done
        # and what still needs doing (saving the project).
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        layout.addWidget(self.status)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close)
        self.btn_apply = self.buttons.addButton(
            "עדכן נבחרות", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_apply.clicked.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    # -- scanning ----------------------------------------------------------

    def scan(self):
        # A rescan starts a fresh run: drop the previous result and bring the
        # pre-flight warning back.
        self.status.setVisible(False)
        self.warn.setVisible(True)

        bar = QProgressDialog("סורק שכבות...", "בטל", 0, 100, self)
        bar.setWindowTitle("עדכון שכבות OVER")
        bar.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        bar.setMinimumDuration(0)
        bar.setValue(0)

        def progress(done, total, text):
            bar.setMaximum(max(total, 1))
            bar.setValue(done)
            if text:
                bar.setLabelText(text)
            QApplication.processEvents()

        try:
            self.rows = refresh.scan(check_schema=True, progress=progress)
        finally:
            bar.close()

        self._fill()

    def _fill(self):
        self.table.setRowCount(len(self.rows))
        updatable = 0
        for index, row in enumerate(self.rows):
            pick = QTableWidgetItem()
            pick.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                          | (Qt.ItemFlag.ItemIsEnabled if row.selectable
                             else Qt.ItemFlag.NoItemFlags))
            pick.setCheckState(Qt.CheckState.Checked if row.selectable
                               else Qt.CheckState.Unchecked)
            self.table.setItem(index, COL_PICK, pick)

            label = row.path
            if row.missing_fields:
                label += "\n⚠ שמות השדות השתנו - העיצוב והסינון צפויים להפגע"
            elif row.status == refresh.MISSING:
                label += f"\n{row.message}"
            elif row.status == refresh.ERROR:
                label += f"\n{row.message}"
            name = QTableWidgetItem(label)
            name.setToolTip(row.layer.source())
            self.table.setItem(index, COL_LAYER, name)

            self.table.setItem(index, COL_VERSION,
                               QTableWidgetItem(_ltr(row.summary())))
            self.table.setItem(index, COL_ROWS, QTableWidgetItem(_fmt_rows(row)))

            sym = QTableWidgetItem()
            can_sym = row.selectable and bool(row.sym_url)
            sym.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                         | (Qt.ItemFlag.ItemIsEnabled if can_sym
                            else Qt.ItemFlag.NoItemFlags))
            sym.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(index, COL_SYM, sym)

            if row.selectable:
                updatable += 1
        self.table.resizeRowsToContents()

        total = len(self.rows)
        self.summary.setText(
            f"{total} שכבות מ-OVER · {updatable} עם גרסה חדשה")
        self._refresh_button()

    # -- interaction -------------------------------------------------------

    def _toggle_all(self, state):
        want = Qt.CheckState(state) == Qt.CheckState.Checked
        for index, row in enumerate(self.rows):
            if row.selectable:
                self.table.item(index, COL_PICK).setCheckState(
                    Qt.CheckState.Checked if want else Qt.CheckState.Unchecked)
        self._refresh_button()

    def _toggle_all_sym(self, state):
        want = Qt.CheckState(state) == Qt.CheckState.Checked
        for index, row in enumerate(self.rows):
            if row.selectable and row.sym_url:
                self.table.item(index, COL_SYM).setCheckState(
                    Qt.CheckState.Checked if want else Qt.CheckState.Unchecked)

    def _picked(self):
        out = []
        for index, row in enumerate(self.rows):
            item = self.table.item(index, COL_PICK)
            if row.selectable and item.checkState() == Qt.CheckState.Checked:
                sym = self.table.item(index, COL_SYM)
                out.append((row, sym.checkState() == Qt.CheckState.Checked))
        return out

    def _refresh_button(self):
        count = len(self._picked())
        self.btn_apply.setText(f"עדכן {count} נבחרות" if count
                               else "עדכן נבחרות")
        self.btn_apply.setEnabled(bool(count))

    # -- applying ----------------------------------------------------------

    def _apply(self):
        picked = self._picked()
        if not picked:
            return
        done = failed = 0
        for row, with_sym in picked:
            ok, message = refresh.apply_update(row, with_symbology=with_sym)
            index = self.rows.index(row)
            self.table.setItem(
                index, COL_VERSION,
                QTableWidgetItem(("✓ " if ok else "✗ ") + _ltr(message)))
            self.table.item(index, COL_PICK).setCheckState(
                Qt.CheckState.Unchecked)
            self.table.item(index, COL_PICK).setFlags(Qt.ItemFlag.NoItemFlags)
            row.status = refresh.CURRENT if ok else refresh.ERROR
            done += 1 if ok else 0
            failed += 0 if ok else 1
        self.table.resizeRowsToContents()
        self.summary.setText(f"{len(self.rows)} שכבות מ-OVER")

        if failed and not done:
            text = f"✗ העדכון נכשל · {failed} שכבות לא עודכנו"
            style = "color:#b00020; font-weight:bold;"
        elif failed:
            text = (f"✓ הסתיים · {done} שכבות עודכנו, {failed} נכשלו — "
                    f"שמור את הפרויקט כדי לשמר את השינוי")
            style = "color:#8a6d00; font-weight:bold;"
        else:
            text = (f"✓ הסתיים בהצלחה · {done} שכבות עודכנו — "
                    f"שמור את הפרויקט כדי לשמר את השינוי")
            style = "color:#0a7a3d; font-weight:bold;"
        self.status.setText(text)
        self.status.setStyleSheet(style)
        self.status.setVisible(True)
        # The pre-flight warning has done its job once the run is over.
        self.warn.setVisible(False)
        self._refresh_button()

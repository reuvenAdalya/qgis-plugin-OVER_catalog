# -*- coding: utf-8 -*-
"""
Layer 4 — plugin settings dialog.

Exposes the one QSettings key the plugin still acts on:
    over_catalog/spatial_only   tree filter (data_items.OrganizationItem)

plus an "about / help" panel describing the גרסאות לעם project and the
plugin's functions. Opened from Plugins → OVER Catalog → הגדרות...
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QGroupBox, QCheckBox, QLabel, QTextBrowser,
    QDialogButtonBox,
)
from qgis.PyQt.QtCore import QSettings, Qt

SETTINGS_SPATIAL_ONLY = "over_catalog/spatial_only"

# RTL rich-text help shown in the dialog. The over.org.il link opens in the
# system browser (setOpenExternalLinks below).
ABOUT_HTML = """
<div dir="rtl" style="text-align:right; direction:rtl">
  <p><b><a href="https://www.over.org.il/">גישה למאגרי פרויקט גרסאות לעם</a></b><br>
  <a href="https://www.over.org.il/">https://www.over.org.il/</a></p>

  <p>"גרסאות לעם" היא מערכת שעוקבת אחרי שינויים במאגרי מידע ממשלתיים וציבוריים
  בישראל. המערכת החלה במעקב אחרי המאגרים הפתוחים באתר data.gov.il, וכיום היא
  מנטרת גם מקורות נוספים: מסד הנתונים של הכנסת, אתר הלשכה המרכזית לסטטיסטיקה
  (הלמ"ס), שכבות המידע המרחבי של GovMap, פרוטוקולי ועדות, מכרזים, החלטות ממשלה
  ועוד.</p>

  <p><b>אפשרויות הכלי</b></p>
  <p>א. עיון בעץ במאגרים: OVER ← סוג מקור (govmap / data.gov.il / scraper) ←
  ארגון ← מאגר.</p>
  <p>ב. חיפוש מאגרים (טבלאות/שכבות): הקלדת שם מאגר בשורת החיפוש הכללית של
  התכנה שבתחתית המסך (Locator) תקפיץ את המאגר בעץ.</p>
  <p>ג. טעינת הנתונים לקריאה בלבד.</p>

  <p><b>מבנה הנתונים — הפריטים העיקריים בכל מאגר</b></p>
  <ul>
    <li><b>GeoJSON</b> — הקובץ המרחבי המלא כפי שנשמר. לחיצה כפולה טוענת את כל
    השכבה.</li>
    <li><b>CSV</b> — טבלת הנתונים (מאפיינים בלבד, ללא גאומטריה).</li>
    <li><b>🗺 תצוגה נוכחית</b> — שאילתה חיה למסד הנתונים (datastore). לחיצה
    כפולה טוענת רק את מה שנמצא בתחום התצוגה הנוכחית של המפה.</li>
    <li><b>גרסאות קודמות</b> — עותקים היסטוריים של המאגר; כל גרסה עם הקבצים
    שלה.</li>
  </ul>

  <p><b>שאילתות SQL</b></p>
  <p>קליק ימני על 🗺 — תצוגה נוכחית / כל השכבה / שאילתה מתקדמת (bbox + סינון
  WHERE, עם בורר שדות). מדפדף אוטומטית עד 50,000 שורות לשאילתה בודדת.</p>
  <p>קליק ימני על OVER / סוג מקור / ארגון — שאילתה מתקדמת: SQL חופשי ומלא על
  כל מסד הנתונים. מוגבל ל-1000 שורות לשאילתה בודדת.</p>
</div>
"""


class SettingsDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("הגדרות OVER Catalog")
        self._build_ui()
        self._load_current()

    def _build_ui(self):
        # Right-align the whole dialog (Hebrew UI).
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        layout = QVBoxLayout(self)

        tree_box = QGroupBox("עץ הדפדפן")
        tree_layout = QVBoxLayout(tree_box)
        self.chk_spatial_only = QCheckBox("הצג רק מאגרים מרחביים")
        tree_layout.addWidget(self.chk_spatial_only)
        note = QLabel("⚠ שינוי ההגדרה יטען מחדש את עץ OVER "
                      "(הענף ייסגר וייבנה מחדש)")
        note.setWordWrap(True)
        note.setStyleSheet("color:#b00020;")
        tree_layout.addWidget(note)
        layout.addWidget(tree_box)

        about_box = QGroupBox("אודות")
        about_layout = QVBoxLayout(about_box)
        self.about = QTextBrowser()
        self.about.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.about.setHtml(ABOUT_HTML)
        self.about.setOpenExternalLinks(True)
        self.about.setMinimumHeight(320)
        about_layout.addWidget(self.about)
        layout.addWidget(about_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(560, 620)

    def _load_current(self):
        self.chk_spatial_only.setChecked(
            QSettings().value(SETTINGS_SPATIAL_ONLY, True, type=bool))

    def accept(self):
        QSettings().setValue(
            SETTINGS_SPATIAL_ONLY, self.chk_spatial_only.isChecked())
        super().accept()

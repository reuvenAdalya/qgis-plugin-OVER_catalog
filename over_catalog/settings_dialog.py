# -*- coding: utf-8 -*-
"""
Layer 4 — plugin settings dialog.

Exposes the one QSettings key the plugin acts on:
    over_catalog/display_mode   tree display mode (data_items.display_mode)

plus an "about / help" panel describing the גרסאות לעם project and the
plugin's functions. Opened from Plugins → OVER Catalog → הגדרות...
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QGroupBox, QRadioButton, QLabel, QTextBrowser,
    QDialogButtonBox,
)
from qgis.PyQt.QtCore import QSettings, Qt

# Tree display mode (replaces the old spatial_only / show_all_files toggles):
#   1 spatial only  2 all openable (default)  3 all files
SETTINGS_DISPLAY_MODE = "over_catalog/display_mode"
DISPLAY_MODE_DEFAULT = 2

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

  <p><b>מצבי תצוגת העץ (בהגדרות)</b></p>
  <ul>
    <li><b>הצג רק שכבות מרחביות</b> — רק מאגרים וקבצים מרחביים.</li>
    <li><b>הצג את כל הנתונים שהתכנה יודעת לפתוח</b> (ברירת מחדל) — כולל גם
    טבלאות (CSV, XLS, TXT ועוד).</li>
    <li><b>הצג את כל הקבצים</b> — גם קבצים ש-QGIS לא פותח כשכבה (מסומנים ב-⚠).</li>
  </ul>

  <p><b>שאילתות SQL</b></p>
  <p>קליק ימני על 🗺 — תצוגה נוכחית / כל השכבה / שאילתה מתקדמת (bbox + סינון
  WHERE, עם בורר שדות). מדפדף אוטומטית עד 50,000 שורות לשאילתה בודדת.</p>
  <p>קליק ימני על OVER / סוג מקור / ארגון — שאילתה מתקדמת: SQL חופשי ומלא על
  כל מסד הנתונים. מוגבל ל-1000 שורות לשאילתה בודדת.</p>

  <p><b>מבנה הנתונים — הפריטים בכל מאגר</b></p>
  <p>מאגר יכול לכלול כמה קבצים מסוגים שונים. כל קובץ מוצג עם סיומתו, וקליק
  ימני עליו מאפשר להעתיק את מיקומו בשרת (לפתיחה בכלים אחרים).</p>
  <ul>
    <li><b>קובצי שכבה מרחבית</b> (GeoJSON, GPKG, GeoParquet, FGB, KML/GML,
    שכבת SHP בתוך ZIP ועוד) — לחיצה כפולה טוענת את השכבה. קונטיינרים
    (GPKG / GeoParquet) ניתנים להרחבה בעץ, וכל תת-שכבה נטענת בנפרד.</li>
    <li><b>טבלאות</b> (CSV, XLS/XLSX, TXT) — מאפיינים בלבד, ללא גאומטריה.</li>
    <li><b>חבילת סימבולוגיה</b> (ZIP) — קובצי עיצוב (QML/SLD); קישור להורדה.</li>
    <li><b>🗺 תצוגה נוכחית</b> — שאילתה חיה למסד הנתונים (datastore). לחיצה
    כפולה טוענת רק את מה שנמצא בתחום התצוגה הנוכחית של המפה.</li>
    <li><b>גרסאות קודמות</b> — עותקים היסטוריים של המאגר; כל גרסה עם הקבצים
    שלה.</li>
    <li><b>"טען כטבלת מידע"</b> — למאגרים שאין להם עמודת גאומטריה (למשל
    קואורדינטות כ-lat/lon או X/Y ולא כטיפוס geometry אמיתי), קליק ימני על
    המאגר עצמו טוען את טבלת ה-datastore הגולמית כטבלת מאפיינים בלבד.</li>
  </ul>

  <p>לכל הצעה או שאלה נשמח לקבל מייל:
  <a href="mailto:reuvenkost@gmail.com">reuvenkost@gmail.com</a></p>
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

        tree_box = QGroupBox("תצוגת עץ הדפדפן")
        tree_layout = QVBoxLayout(tree_box)

        self.rb_mode1 = QRadioButton("הצג רק שכבות מרחביות")
        self.rb_mode1.setToolTip(
            "רק מאגרים וקבצים מרחביים (שכבות עם גאומטריה): "
            "SHP, ZIP, GeoJSON, FGB, GPKG, GeoParquet, GML, KML, TIFF, PNG, "
            "וקובצי סגנון/הגדרה (SLD, QML, QLR, LYR).")
        tree_layout.addWidget(self.rb_mode1)

        self.rb_mode2 = QRadioButton(
            "הצג את כל הנתונים שהתכנה יודעת לפתוח (ברירת מחדל)")
        self.rb_mode2.setToolTip(
            "כל מה שב'מרחביים בלבד', ובנוסף נתונים טבלאיים ש-QGIS/GDAL "
            "יודעת לפתוח: CSV, XLS/XLSX, TXT ועוד.")
        tree_layout.addWidget(self.rb_mode2)

        self.rb_mode3 = QRadioButton("הצג את כל הקבצים")
        self.rb_mode3.setToolTip(
            "כל הקבצים, כולל כאלה ש-QGIS לא יודעת לפתוח כשכבה "
            "(PDF, XML ועוד) — מסומנים בסימן אזהרה ⚠.")
        tree_layout.addWidget(self.rb_mode3)

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
        s = QSettings()
        try:
            mode = int(s.value(SETTINGS_DISPLAY_MODE, DISPLAY_MODE_DEFAULT))
        except (TypeError, ValueError):
            mode = DISPLAY_MODE_DEFAULT
        {1: self.rb_mode1, 2: self.rb_mode2, 3: self.rb_mode3}.get(
            mode, self.rb_mode2).setChecked(True)

    def accept(self):
        if self.rb_mode1.isChecked():
            mode = 1
        elif self.rb_mode3.isChecked():
            mode = 3
        else:
            mode = 2
        QSettings().setValue(SETTINGS_DISPLAY_MODE, mode)
        super().accept()

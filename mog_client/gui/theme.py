"""The stylesheet every window of the client shares."""

from __future__ import annotations

from mog_client.gui.widgets import ACCENT_HOVER_STOPS, accent_qss, scrollbar_qss

STYLE = """
* { font-size: 18px; }
QMainWindow { background: #14171c; color: #e8eaed; }
QLabel { color: #e8eaed; }
QLineEdit, QComboBox, QPlainTextEdit { background: #1e232b; color: #e8eaed; border: 2px solid #2c333d; border-radius: 6px; padding: 8px; }
QPushButton { background: {accent}; color: white; border: 2px solid transparent; border-radius: 8px; padding: 12px 22px; max-width: 460px; }
QPushButton:hover { background: {accent_hover}; }
QPushButton:focus { border-color: white; }
QPushButton#key { padding: 2px; font-size: 17px; border-radius: 6px; max-width: 16777215px; }
QPushButton[danger="true"] { background: #e2574c; }
QPushButton[danger="true"]:hover { background: #c94439; }
QComboBox QAbstractItemView { background: #1e232b; color: #e8eaed; border: 2px solid #2c333d; outline: none; selection-background-color: #1e2733; selection-color: white; }
QComboBox QAbstractItemView::item { color: #e8eaed; padding: 6px 8px; min-height: 28px; }
QComboBox QAbstractItemView::item:selected { color: white; background: #1e2733; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { color: #e8eaed; border: 3px solid transparent; border-radius: 10px; padding: 6px; }
QListWidget::item:selected { border-color: #4c8dff; background: #1e2733; }
*:focus { border-color: #4c8dff; }
QTabBar::tab { background: #1e232b; color: #9aa3b0; padding: 10px 24px; margin: 8px 6px 0 0; border-top-left-radius: 10px; border-top-right-radius: 10px; border-bottom-left-radius: 0; border-bottom-right-radius: 0; }
QTabBar::tab:hover { color: #e8eaed; }
QTabBar::tab:selected { background: {accent}; color: white; margin-top: 0; }
QPushButton[filter="true"] { background: #2c333d; color: #9aa3b0; }
QPushButton[filter="true"]:checked { background: {accent}; color: white; }
QTextEdit { background: #1e232b; color: #e8eaed; border: 2px solid #2c333d; border-radius: 6px; padding: 8px; }
#menuView, #menuView > QWidget > QWidget { background: transparent; }
#menuSection { color: #9aa3b0; font-size: 14px; font-weight: bold; padding: 16px 4px 2px 4px; }
#optionRow { background: #1b2028; border: 2px solid transparent; border-radius: 10px; }
#optionRow[active="true"] { background: #1e2733; border-color: #4c8dff; }
#optionTitle { font-size: 19px; font-weight: 600; }
#optionDescription { color: #9aa3b0; font-size: 15px; }
#optionStatus { color: #6fb1ff; font-size: 15px; }
#overlay { background: rgba(0, 0, 0, 175); }
#overlayCard { background: #1e232b; border: 2px solid #4c8dff; border-radius: 12px; padding: 18px; }
#modalHost { background: rgba(0, 0, 0, 150); }
#modalCard { background: #1a1f27; border: 2px solid #4c8dff; border-radius: 12px; }
#modalTitle { font-size: 20px; font-weight: bold; padding-bottom: 4px; }
#overlayCard[level="error"] { border-color: #e2574c; }
#overlayCard[level="warning"] { border-color: #e8c547; }
#overlayTitle { font-size: 22px; font-weight: bold; }
#userButton { padding: 8px 18px 8px 10px; margin: 0 8px 0 0; }  /* room on the right for the unread dot */
QMenu { background: #1e2733; color: #e8eaed; border: 1px solid #343c49; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 12px 34px 12px 18px; border-radius: 8px; font-size: 18px; }
QMenu::item:selected { background: {accent}; color: white; }
QMenu::separator { height: 1px; background: #343c49; margin: 6px 10px; }
#metaKey { color: #8b94a3; font-size: 12px; font-weight: bold; padding-top: 3px; }
#metaValue { color: #ffffff; font-size: 17px; }
#summary { color: #c3c8d2; }
#hltbBox { background: rgba(21, 25, 32, 205); border: 1px solid #343c49; border-radius: 10px; }
#hltbTitle { color: #8b94a3; font-size: 12px; font-weight: bold; }
#hltbLabel { color: #c3c8d2; font-size: 14px; }
#hltbTime { color: #ffffff; font-size: 19px; font-weight: bold; }
QProgressBar { background: #1e232b; border: none; border-radius: 6px; height: 18px; text-align: center; color: white; }
QProgressBar::chunk { background: {accent}; border-radius: 6px; }
""".replace("{accent_hover}", accent_qss(ACCENT_HOVER_STOPS)).replace("{accent}", accent_qss()) + scrollbar_qss()

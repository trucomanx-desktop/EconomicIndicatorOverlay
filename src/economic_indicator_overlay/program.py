import sys, os, json, signal
from PyQt5.QtCore import Qt, QTimer, QPoint, QRectF
from PyQt5.QtWidgets import (QApplication, QLabel, QWidget, QSystemTrayIcon,
                             QMenu, QInputDialog)
from PyQt5.QtGui import QFont, QPainterPath, QRegion, QIcon, QFontMetrics

import economic_indicator_overlay.about as about
from economic_indicator_overlay.modules.configure import load_config, save_config
from economic_indicator_overlay.modules.resources import resource_path
from economic_indicator_overlay.modules.wabout  import show_about_window
from economic_indicator_overlay.desktop import create_desktop_file
from economic_indicator_overlay.desktop import create_desktop_directory
from economic_indicator_overlay.desktop import create_desktop_menu
import brazilian_economic_data.economic_indicators as ecin

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".config", about.__package__, "config.json")
DEFAULT_OPTIONS = {
    "show_cdi": True,
    "show_selic": True,
    "show_ipca_12m": True,
    "show_focus": True,
    "show_errors": True,
    "show_consultation": False,
}
DEFAULT_CONFIG = {
    "x": 200,
    "y": 200,
    "refresh_hours": 6,
    "font_family": "DejaVu Sans Mono",
    "font_size": 14,
    "display_template": "{indicators}",
    "error_template": "Error retrieving economic indicators:\n{error}",
    "indicators": DEFAULT_OPTIONS.copy(),
    "ui_text": {
        "indicators_menu": "Indicators",
        "cdi": "CDI",
        "selic": "Selic",
        "ipca_12m": "IPCA (12 months)",
        "focus": "Focus report",
        "errors": "Errors",
        "consultation": "Consultation date",
        "refresh_interval": "Set refresh interval...",
        "refresh_prompt_title": "Refresh interval",
        "refresh_prompt_label": "Hours between updates:",
        "refresh_now": "Refresh now",
        "font_size": "Set font size...",
        "font_family": "Set font family...",
        "font_size_title": "Font size",
        "font_size_prompt": "Enter font size:",
        "font_family_title": "Font family",
        "font_family_prompt": "Enter font family:",
        "about": "About",
        "exit": "Exit",
    },
}


class StickyIndicators(QWidget):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.height = 120
        self.color = "white"
        self.radius = 20
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(800, self.height)
        self.move(config.get("x", 200), config.get("y", 200))

        self.label = QLabel(self)
        self.label.setStyleSheet(f"color: {self.color};")
        self.label.setFont(QFont(config["font_family"], config["font_size"], QFont.Bold))
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.label.setWordWrap(False)
        self.label.setMargin(10)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_indicators)
        self.set_refresh_interval(config.get("refresh_hours", 6))
        self.refresh_indicators()
        self.old_pos = None
        self.set_rounded_corners(self.radius)

    def set_rounded_corners(self, radius):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), radius, radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def set_refresh_interval(self, hours):
        self.config["refresh_hours"] = int(hours)
        self.timer.start(int(hours) * 60 * 60 * 1000)

    def refresh_indicators(self):
        try:
            data = ecin.get_indicators()
            text = ecin.indicators_to_string(data, **self.config["indicators"])
        except Exception as error:
            text = self.config["error_template"].format(error=error)
        text = self.config["display_template"].format(indicators=text)
        self.label.setText(text)
        metrics = QFontMetrics(self.label.font())
        lines = text.splitlines() or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines) + 40
        height = max(self.height, metrics.lineSpacing() * len(lines) + 30)
        self.resize(width, height)
        self.label.setGeometry(0, 0, width, height)
        self.set_rounded_corners(self.radius)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPos()

    def mouseMoveEvent(self, event):
        if self.old_pos is not None:
            delta = QPoint(event.globalPos() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPos()

    def mouseReleaseEvent(self, event):
        self.old_pos = None
        if hasattr(self, "tray_ref"):
            self.tray_ref.save_config()


class EconomicIndicatorsTray(QSystemTrayIcon):
    def __init__(self, icon, parent=None):
        super().__init__(icon, parent)
        saved = load_config(CONFIG_PATH)
        self.config = {**DEFAULT_CONFIG, "indicators": DEFAULT_OPTIONS.copy(),
                       "ui_text": DEFAULT_CONFIG["ui_text"].copy()}
        if isinstance(saved, dict):
            self.config.update({key: saved[key] for key in DEFAULT_CONFIG if key in saved})
            self.config["indicators"] = {**DEFAULT_OPTIONS, **saved.get("indicators", {})}
            self.config["ui_text"] = {**DEFAULT_CONFIG["ui_text"], **saved.get("ui_text", {})}
        self.window = StickyIndicators(self.config)
        self.window.tray_ref = self
        self.window.show()

        menu = QMenu(parent)
        ui = self.config["ui_text"]
        indicators_menu = menu.addMenu(ui["indicators_menu"])
        self.indicator_actions = {}
        labels = {
            "show_cdi": ui["cdi"], "show_selic": ui["selic"], "show_ipca_12m": ui["ipca_12m"],
            "show_focus": ui["focus"], "show_errors": ui["errors"], "show_consultation": ui["consultation"],
        }
        for key, label in labels.items():
            action = indicators_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.config["indicators"].get(key, True))
            action.toggled.connect(lambda checked, option=key: self.set_indicator(option, checked))
            self.indicator_actions[key] = action

        interval_action = menu.addAction(ui["refresh_interval"])
        interval_action.triggered.connect(self.set_refresh_interval)
        size_action = menu.addAction(ui["font_size"])
        size_action.triggered.connect(self.set_font_size)
        family_action = menu.addAction(ui["font_family"])
        family_action.triggered.connect(self.set_font_family)
        refresh_action = menu.addAction(ui["refresh_now"])
        refresh_action.triggered.connect(self.window.refresh_indicators)
        menu.addSeparator()
        about_action = menu.addAction(f"ℹ️ {ui['about']}")
        about_action.triggered.connect(self.show_about)
        exit_action = menu.addAction(f"❌ {ui['exit']}")
        exit_action.triggered.connect(self.exit_app)
        self.setContextMenu(menu)
        self.show()

    def set_indicator(self, name, enabled):
        self.config["indicators"][name] = enabled
        self.save_config()
        self.window.refresh_indicators()

    def set_refresh_interval(self):
        ui = self.config["ui_text"]
        hours, ok = QInputDialog.getInt(None, ui["refresh_prompt_title"],
                                        ui["refresh_prompt_label"], self.config["refresh_hours"], 1, 8760, 1)
        if ok:
            self.window.set_refresh_interval(hours)
            self.save_config()

    def set_font_size(self):
        ui = self.config["ui_text"]
        size, ok = QInputDialog.getInt(None, ui["font_size_title"], ui["font_size_prompt"],
                                       self.config["font_size"], 6, 96, 1)
        if ok:
            self.config["font_size"] = size
            self.window.label.setFont(QFont(self.config["font_family"], size, QFont.Bold))
            self.window.refresh_indicators()
            self.save_config()

    def set_font_family(self):
        ui = self.config["ui_text"]
        family, ok = QInputDialog.getText(None, ui["font_family_title"], ui["font_family_prompt"],
                                          text=self.config["font_family"])
        if ok and family.strip():
            self.config["font_family"] = family.strip()
            self.window.label.setFont(QFont(self.config["font_family"], self.config["font_size"], QFont.Bold))
            self.window.refresh_indicators()
            self.save_config()

    def save_config(self):
        self.config["x"], self.config["y"] = self.window.x(), self.window.y()
        save_config(CONFIG_PATH, self.config)

    def show_about(self):
        data = {
            "version": about.__version__, "package": about.__package__,
            "program_name": about.__program_name__, "author": about.__author__,
            "email": about.__email__, "description": about.__description__,
            "url_source": about.__url_source__, "url_doc": about.__url_doc__,
            "url_funding": about.__url_funding__, "url_bugs": about.__url_bugs__
        }
        show_about_window(data, resource_path("icons", "logo.png"))

    def exit_app(self):
        self.window.close()
        QApplication.quit()


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    create_desktop_directory()
    create_desktop_menu()
    create_desktop_file("~/.local/share/applications")
    for arg in sys.argv[1:]:
        if arg == "--autostart":
            create_desktop_directory(overwrite=True)
            create_desktop_menu(overwrite=True)
            create_desktop_file("~/.config/autostart", overwrite=True)
            return
        if arg == "--applications":
            create_desktop_directory(overwrite=True)
            create_desktop_menu(overwrite=True)
            create_desktop_file("~/.local/share/applications", overwrite=True)
            return
    app = QApplication(sys.argv)
    app.setApplicationName(about.__package__)
    app.setQuitOnLastWindowClosed(False)
    icon = QIcon(resource_path("icons", "logo.png"))
    tray = EconomicIndicatorsTray(icon)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

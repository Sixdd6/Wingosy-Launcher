import sys
import os
import re
import webbrowser
import zipfile
import shutil
import subprocess
import logging
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, 
                             QLabel, QLineEdit, QPushButton, QDialogButtonBox, 
                             QMessageBox, QProgressBar, QComboBox, QFileDialog, 
                             QSizePolicy, QApplication, QWidget, QSpinBox, QScrollArea,
                             QCheckBox, QListWidget, QListWidgetItem)
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QEventLoop
from PySide6.QtGui import QPixmap, QDesktopServices, QFont, QFontMetrics

from src.ui.threads import (UpdaterThread, SelfUpdateThread,
                             ConnectionTestThread, RomDownloader, CoreDownloadThread, 
                             ImageFetcher, ConflictResolveThread, GameDescriptionFetcher, 
                             ExtractionThread, WikiFetcherThread)
from src.ui.widgets import format_speed, format_size, get_resource_path
from src.platforms import RETROARCH_PLATFORMS, RETROARCH_CORES, platform_matches
from src import emulators, windows_saves
from src.utils import read_retroarch_cfg, write_retroarch_cfg_values, zip_path, extract_strip_root

_retroarch_autosave_checked = False
_ppsspp_assets_checked = False

WINDOWS_PLATFORM_SLUGS = ["windows", "win", "pc", "pc-windows", "windows-games", "win95", "win98"]
EXCLUDED_EXES = [
    "unins000.exe", "uninstall.exe", "setup.exe",
    "vcredist", "directx", "dxsetup.exe",
    "vc_redist", "crashpad_handler.exe",
    "notification_helper.exe", "UnityCrashHandler",
    "dotnet", "netfx", "oalinst.exe",
    "DXSETUP.exe", "installscript",
    "dx_setup", "redist"
]

def check_retroarch_autosave(ra_exe_path, platform_slug, parent, config=None):
    global _retroarch_autosave_checked
    if _retroarch_autosave_checked:
        return
    _retroarch_autosave_checked = True
    
    if platform_slug in ("psp", "playstation-portable"):
        return
        
    save_mode = config.get("retroarch_save_mode", "srm") if config else "srm"
    if save_mode == "srm":
        return
        
    cfg_path = Path(ra_exe_path).parent / "retroarch.cfg"
    if not cfg_path.exists():
        return
        
    cfg = read_retroarch_cfg(str(cfg_path))
    auto_save = cfg.get("savestate_auto_save", "false")
    auto_load = cfg.get("savestate_auto_load", "false")
    
    if auto_save == "true" and auto_load == "true":
        return
        
    missing = []
    if auto_save != "true": missing.append("savestate_auto_save")
    if auto_load != "true": missing.append("savestate_auto_load")
    
    result = QMessageBox.question(
        parent, 
        "RetroArch Auto-Save States", 
        f"Enable auto save/load states in retroarch.cfg?\n\nMissing: {', '.join(missing)}", 
        QMessageBox.Yes | QMessageBox.No
    )
    
    if result == QMessageBox.Yes:
        write_retroarch_cfg_values(str(cfg_path), {"savestate_auto_save": "true", "savestate_auto_load": "true"})
        QMessageBox.information(parent, "RetroArch Auto-Save States", "✅ Auto save/load states enabled.")

def check_ppsspp_assets(ra_exe_path, parent):
    global _ppsspp_assets_checked
    if _ppsspp_assets_checked:
        return
    _ppsspp_assets_checked = True
    
    system_ppsspp = Path(ra_exe_path).parent / "system" / "PPSSPP"
    if (system_ppsspp / "ppge_atlas.zim").exists():
        return
        
    result = QMessageBox.question(
        parent, 
        "PPSSPP Assets Missing", 
        "Download missing PPSSPP assets now?", 
        QMessageBox.Yes | QMessageBox.No
    )
    
    if result != QMessageBox.Yes:
        return
        
    progress = QMessageBox(parent)
    progress.setWindowTitle("Downloading PPSSPP Assets")
    progress.setText("Downloading...")
    progress.show()
    QApplication.processEvents()
    
    try:
        import urllib.request, tempfile
        url = "https://buildbot.libretro.com/assets/system/PPSSPP.zip"
        system_ppsspp.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
        
        urllib.request.urlretrieve(url, tmp_path)
        with zipfile.ZipFile(tmp_path, 'r') as z:
            for member in z.namelist():
                rel = member[len("PPSSPP/"):] if member.startswith("PPSSPP/") else member
                if not rel: continue
                target = system_ppsspp / rel
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as src, open(target, 'wb') as dst:
                        dst.write(src.read())
        
        Path(tmp_path).unlink(missing_ok=True)
        progress.close()
        QMessageBox.information(parent, "PPSSPP Assets Ready", "✅ Done.")
    except Exception as e:
        progress.close()
        QMessageBox.warning(parent, "Download Failed", str(e))

class WelcomeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Wingosy Launcher")
        self.resize(500, 350)
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("<h1>Welcome to Wingosy!</h1>"))
        info = QLabel("<p style='font-size: 12pt;'>Your setup is almost complete. Follow the tabs to get started.</p>")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch()
        
        btn = QPushButton("Get Started")
        btn.setStyleSheet("background: #1e88e5; color: white; padding: 10px;")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

class ConflictDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Save Conflict: {title}")
        self.resize(450, 200)
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel(f"Conflict found for <b>{title}</b>. Which save would you like to use?"))
        layout.addStretch()
        
        btn_layout = QHBoxLayout()
        self.result_mode = None
        
        for mode, text in [("cloud", "☁️ Use Cloud"), ("local", "💾 Keep Local"), ("both", "📁 Keep Both")]:
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked, m=mode: self.finish(m))
            btn_layout.addWidget(btn)
            
        layout.addLayout(btn_layout)
        
    def finish(self, mode):
        self.result_mode = mode
        self.accept()

class SetupDialog(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wingosy Setup")
        self.config = config_manager
        self.resize(400, 200)
        layout = QFormLayout(self)
        
        self.host_input = QLineEdit(self.config.get("host"))
        self.user_input = QLineEdit(self.config.get("username"))
        self.pass_input = QLineEdit("")
        self.pass_input.setEchoMode(QLineEdit.Password)
        
        layout.addRow("RomM Host:", self.host_input)
        layout.addRow("Username:", self.user_input)
        layout.addRow("Password:", self.pass_input)
        
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        btns.accepted.connect(self.validate_and_accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)
        
    def validate_and_accept(self):
        if not re.match(r'^https?://.+', self.host_input.text().strip()):
            QMessageBox.warning(self, "Invalid Host", "Enter a valid URL.")
            return
        self.accept()
        
    def get_data(self):
        return {
            "host": self.host_input.text().strip().rstrip('/'),
            "username": self.user_input.text().strip(),
            "password": self.pass_input.text()
        }

class ExePickerDialog(QDialog):
    def __init__(self, exes, game_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Choose Executable — {game_name}")
        self.setMinimumSize(600, 450)
        self.selected_exe = None
        self.setStyleSheet("QDialog { background-color: #1e1e1e; color: #ffffff; }")
        
        layout = QVBoxLayout(self)
        header = QLabel("Multiple executables found. Select one to launch:")
        header.setStyleSheet("font-size: 12pt; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)
        
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget { background-color: #2b2b2b; color: #ffffff; border: 1px solid #555; font-size: 10pt; }
            QListWidget::item { padding: 12px; border-bottom: 1px solid #3a3a3a; }
            QListWidget::item:selected { background-color: #0d6efd; color: #ffffff; }
            QListWidget::item:hover { background-color: #3a3a3a; }
        """)
        
        for path in exes:
            try:
                size_str = format_size(os.path.getsize(path))
            except:
                size_str = "Unknown"
            item = QListWidgetItem(f"{os.path.basename(path)}\n({size_str}) — {path}")
            item.setData(Qt.UserRole, path)
            self.list_widget.addItem(item)
            
        layout.addWidget(self.list_widget)
        
        btns = QHBoxLayout()
        launch_btn = QPushButton("▶ Launch Selected")
        launch_btn.setStyleSheet("background: #2e7d32; color: white; font-weight: bold; padding: 10px; font-size: 11pt;")
        launch_btn.clicked.connect(self.accept_selection)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("background: #444; color: #eee; padding: 10px;")
        cancel_btn.clicked.connect(self.reject)
        
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(launch_btn)
        layout.addLayout(btns)
        
    def accept_selection(self):
        if self.list_widget.currentItem():
            self.selected_exe = self.list_widget.currentItem().data(Qt.UserRole)
            self.accept()
        else:
            QMessageBox.warning(self, "No Selection", "Please select an executable.")

class WikiSuggestionsDialog(QDialog):
    def __init__(self, suggestions, game_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Save Location Suggestions — {game_name}")
        self.setFixedSize(680, 350)
        self.selected_path = None
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        layout.addWidget(QLabel(f"<b>Found {len(suggestions)} possible save locations from PCGamingWiki:</b>"))
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: #1a1a1a; border: 1px solid #333;")
        
        container = QWidget()
        list_layout = QVBoxLayout(container)
        list_layout.setContentsMargins(2, 2, 2, 2)
        list_layout.setSpacing(2)
        list_layout.setAlignment(Qt.AlignTop)
        
        metrics = QFontMetrics(self.font())
        
        for item in suggestions:
            row = QWidget()
            row.setFixedHeight(36)
            row.setStyleSheet("background: #252525; border-radius: 3px;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(4, 0, 4, 0)
            rl.setSpacing(4)
            
            badge = QLabel(item["path_type"])
            color = "#2e7d32" if item["exists"] else "#555"
            badge.setFixedWidth(130)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(f"background: {color}; color: white; border-radius: 2px; font-size: 10px; font-weight: bold; padding: 2px;")
            rl.addWidget(badge)
            
            p_val = item['expanded_path']
            elided = metrics.elidedText(p_val, Qt.ElideMiddle, 380)
            lbl = QLabel(elided)
            lbl.setToolTip(p_val)
            lbl.setStyleSheet("font-size: 10px; color: #ddd;")
            rl.addWidget(lbl, 1)
            
            btn = QPushButton("📁 Browse Here")
            btn.setFixedWidth(100)
            btn.setStyleSheet("font-size: 10px; padding: 4px 8px; background: #444;")
            btn.clicked.connect(lambda checked, p=p_val: self.browse_and_confirm(p))
            rl.addWidget(btn)
            
            list_layout.addWidget(row)
            
        scroll.setWidget(container)
        layout.addWidget(scroll)
        
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet("padding: 6px;")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        
    def browse_and_confirm(self, start_path):
        p = Path(start_path)
        while not p.exists() and p.parent != p:
            p = p.parent
        directory = QFileDialog.getExistingDirectory(self, "Select Save Folder", str(p))
        if directory:
            if QMessageBox.question(self, "Confirm", f"Use this folder?\n{directory}") == QMessageBox.Yes:
                self.selected_path = directory
                self.accept()

class WikiFetchWorker(QThread):
    results_ready = Signal(list)
    failed = Signal()
    
    def __init__(self, game_title, windows_games_dir):
        super().__init__()
        self.game_title = game_title
        self.windows_games_dir = windows_games_dir
        
    def run(self):
        try:
            from src.pcgamingwiki import fetch_save_locations
            self.results_ready.emit(fetch_save_locations(self.game_title, self.windows_games_dir))
        except Exception:
            self.failed.emit()

class SaveSyncSetupDialog(QDialog):
    def __init__(self, game_name, config, main_window, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Up Save Sync")
        self.game_name = game_name
        self.config = config
        self.main_window = main_window
        self.selected_path = None
        self.setFixedSize(450, 250)
        
        layout = QVBoxLayout(self)
        msg = QLabel(f"Where does <b>{game_name}</b> save its files?<br><br>Setting this up enables automatic cloud backup.")
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(msg)
        layout.addStretch()
        
        self.btn_wiki = QPushButton("🌐 Get PCGamingWiki Suggestions")
        self.btn_wiki.setStyleSheet("padding: 10px; background: #1565c0; color: white; font-weight: bold;")
        self.btn_wiki.setVisible(self.config.get("pcgamingwiki_enabled", True))
        self.btn_wiki.clicked.connect(self.get_suggestions)
        layout.addWidget(self.btn_wiki)
        
        btn_man = QPushButton("📁 Browse Manually")
        btn_man.clicked.connect(self.browse_manually)
        layout.addWidget(btn_man)
        
        btn_skip = QPushButton("▶ Skip for Now")
        btn_skip.clicked.connect(self.reject)
        layout.addWidget(btn_skip)
        
    def get_suggestions(self):
        self.loading_dlg = QMessageBox(self)
        self.loading_dlg.setWindowTitle("Fetching")
        self.loading_dlg.setText("Querying PCGamingWiki...")
        self.loading_dlg.show()
        
        self.btn_wiki.setEnabled(False)
        self.wiki_worker = WikiFetchWorker(self.game_name, self.config.get("windows_games_dir", ""))
        self.wiki_worker.results_ready.connect(self.on_wiki_results)
        self.wiki_worker.failed.connect(self.on_wiki_failed)
        
        self.wiki_timeout = QTimer()
        self.wiki_timeout.setSingleShot(True)
        self.wiki_timeout.timeout.connect(self.on_wiki_timeout)
        self.wiki_timeout.start(3000)
        self.wiki_worker.start()
        
    def on_wiki_timeout(self):
        if self.wiki_worker and self.wiki_worker.isRunning():
            self.wiki_worker.terminate()
            self.on_wiki_failed()
            
    def on_wiki_results(self, res):
        if self.wiki_timeout: self.wiki_timeout.stop()
        self.loading_dlg.close()
        self.btn_wiki.setEnabled(True)
        
        if not res:
            QMessageBox.information(self, "No Suggestions", "None found. Browse manually.")
            self.browse_manually()
            return
            
        QTimer.singleShot(100, lambda: self._show_suggestions(res))
        
    def _show_suggestions(self, res):
        d = WikiSuggestionsDialog(res, self.game_name, self)
        if d.exec() == QDialog.Accepted:
            self.selected_path = d.selected_path
            self.accept()
            
    def on_wiki_failed(self):
        if self.wiki_timeout: self.wiki_timeout.stop()
        self.loading_dlg.close()
        self.btn_wiki.setEnabled(True)
        QMessageBox.warning(self, "Error", "Failed to reach wiki.")
        
    def browse_manually(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Save Folder")
        if directory:
            self.selected_path = directory
            self.accept()

class WindowsGameSettingsDialog(QDialog):
    def __init__(self, game, config, main_window, parent=None):
        super().__init__(parent)
        self.game = game
        self.config = config
        self.main_window = main_window
        self.setWindowTitle(f"Game Settings — {game.get('name')}")
        self.resize(550, 500)
        
        saved = windows_saves.get_windows_save(game['id']) or {"name": game.get('name')}
        self.default_exe = saved.get("default_exe")
        self.save_dir = saved.get("save_dir")
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h3>Default Executable</h3><p>Choose which .exe to launch by default.</p>"))
        
        self.exe_status = QLabel()
        self.exe_status.setStyleSheet("color: #aaa;")
        layout.addWidget(self.exe_status)
        
        eb = QHBoxLayout()
        ab = QPushButton("🔍 Auto-detect")
        ab.clicked.connect(self.auto_detect_exe)
        eb.addWidget(ab)
        bb = QPushButton("📁 Browse")
        bb.clicked.connect(self.browse_exe)
        eb.addWidget(bb)
        layout.addLayout(eb)
        layout.addSpacing(20)
        
        layout.addWidget(QLabel("<h3>Save Directory</h3><p>Where does this game store its saves?</p>"))
        self.save_status = QLabel()
        self.save_status.setStyleSheet("color: #aaa;")
        layout.addWidget(self.save_status)
        
        sb = QHBoxLayout()
        wb = QPushButton("🌐 PCGamingWiki Suggestions")
        wb.setVisible(self.config.get("pcgamingwiki_enabled", True))
        wb.clicked.connect(self.get_wiki_suggestions)
        sb.addWidget(wb)
        mb = QPushButton("📁 Browse Manually")
        mb.clicked.connect(self.browse_save_dir)
        sb.addWidget(mb)
        layout.addLayout(sb)
        
        self.sync_status = QLabel()
        self.sync_status.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.sync_status)
        layout.addStretch()
        
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        btns.accepted.connect(self.save_and_close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        
        self.update_ui()
        
    def update_ui(self):
        if self.default_exe:
            self.exe_status.setText(f"<b>{os.path.basename(self.default_exe)}</b><br><small>{self.default_exe}</small>")
        else:
            self.exe_status.setText("No default set")
            
        self.save_status.setText(self.save_dir or "Not configured")
        
        if self.save_dir and os.path.exists(self.save_dir):
            self.sync_status.setText("<span style='color: #4caf50;'>✅ Cloud sync active</span>")
        elif self.save_dir:
            self.sync_status.setText("<span style='color: #ff5252;'>⚠️ Folder does not exist</span>")
        else:
            self.sync_status.setText("")
            
    def auto_detect_exe(self):
        rom = self.game.get('fs_name')
        win_dir = self.config.get("windows_games_dir")
        if not rom or not win_dir:
            return
            
        folder = Path(win_dir) / Path(rom).stem
        if not folder.exists():
            return
            
        exes = [str(p) for p in folder.rglob("*.exe") if not any(ex.lower() in str(p).lower() for ex in EXCLUDED_EXES)]
        if not exes:
            QMessageBox.information(self, "No EXEs", "None found.")
            return
            
        if len(exes) == 1:
            self.default_exe = exes[0]
            self.update_ui()
        else:
            p = ExePickerDialog(exes, self.game.get("name"), self)
            if p.exec() == QDialog.Accepted:
                self.default_exe = p.selected_exe
                self.update_ui()
                
    def browse_exe(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select Executable", "", "Executables (*.exe)")
        if p:
            self.default_exe = p
            self.update_ui()
            
    def get_wiki_suggestions(self):
        self.loading_dlg = QMessageBox(self)
        self.loading_dlg.setWindowTitle("Fetching")
        self.loading_dlg.setText("Querying PCGamingWiki...")
        self.loading_dlg.show()
        
        self.wiki_worker = WikiFetchWorker(self.game.get("name"), self.config.get("windows_games_dir", ""))
        self.wiki_worker.results_ready.connect(self.on_wiki_results)
        self.wiki_worker.failed.connect(lambda: (self.loading_dlg.close(), QMessageBox.warning(self, "Error", "Failed.")))
        
        self.wiki_timeout = QTimer()
        self.wiki_timeout.setSingleShot(True)
        self.wiki_timeout.timeout.connect(lambda: (self.wiki_worker.terminate(), self.loading_dlg.close()))
        self.wiki_timeout.start(3000)
        self.wiki_worker.start()
        
    def on_wiki_results(self, res):
        if self.wiki_timeout: self.wiki_timeout.stop()
        self.loading_dlg.close()
        
        if not res:
            QMessageBox.information(self, "No Suggestions", "None found.")
            return
            
        d = WikiSuggestionsDialog(res, self.game.get("name"), self)
        if d.exec() == QDialog.Accepted:
            self.save_dir = d.selected_path
            self.update_ui()
            
    def browse_save_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Save Folder")
        if directory:
            self.save_dir = directory
            self.update_ui()
            
    def save_and_close(self):
        windows_saves.set_windows_save(self.game['id'], self.game['name'], self.save_dir, self.default_exe)
        self.accept()

class SettingsDialog(QDialog):
    def __init__(self, config_manager, main_window, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.config = config_manager
        self.main_window = main_window
        self.resize(400, 600)
        layout = QVBoxLayout(self)
        
        hl = QHBoxLayout()
        hl.addWidget(QLabel("Server Host:"))
        self.host_input = QLineEdit()
        self.host_input.setText(self.config.get("host", ""))
        hl.addWidget(self.host_input)
        
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._test_host_connection)
        hl.addWidget(self.test_btn)
        
        self.re_btn = QPushButton("✅ Apply & Re-connect")
        self.re_btn.setVisible(False)
        self.re_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.re_btn.clicked.connect(self._apply_and_restart)
        hl.addWidget(self.re_btn)
        layout.addLayout(hl)
        
        layout.addWidget(QLabel(f"<b>User:</b> {self.config.get('username')}  |  <b>Version:</b> {self.main_window.version}"))
        
        self.ap_btn = QPushButton("Auto Pull Saves: ON" if self.config.get("auto_pull_saves", True) else "Auto Pull Saves: OFF")
        self.ap_btn.setCheckable(True)
        self.ap_btn.setChecked(self.config.get("auto_pull_saves", True))
        self.ap_btn.toggled.connect(self.toggle_auto_pull)
        layout.addWidget(self.ap_btn)
        
        cl = QHBoxLayout()
        cl.addWidget(QLabel("Cards per row:"))
        self.row_spin = QSpinBox()
        self.row_spin.setRange(1, 12)
        self.row_spin.setValue(self.config.get("cards_per_row", 6))
        self.row_spin.valueChanged.connect(self.set_cards_per_row)
        cl.addWidget(self.row_spin)
        cl.addStretch()
        layout.addLayout(cl)
        
        layout.addWidget(QLabel("<b>RetroArch Save Mode:</b>"))
        self.ra_combo = QComboBox()
        self.ra_combo.addItems(["SRM only", "States only", "Both"])
        ra_mode = self.config.get("retroarch_save_mode", "srm")
        self.ra_combo.setCurrentText({"srm": "SRM only", "state": "States only", "both": "Both"}.get(ra_mode))
        self.ra_combo.currentTextChanged.connect(self.set_ra_save_mode)
        layout.addWidget(self.ra_combo)
        
        layout.addWidget(QLabel("<b>Windows Games Folder:</b>"))
        wl = QHBoxLayout()
        self.win_input = QLineEdit(self.config.get("windows_games_dir", ""))
        wl.addWidget(self.win_input)
        wb = QPushButton("Browse")
        wb.clicked.connect(self.browse_win)
        wl.addWidget(wb)
        layout.addLayout(wl)
        
        self.wiki_check = QCheckBox("PCGamingWiki Save Suggestions")
        self.wiki_check.setChecked(self.config.get("pcgamingwiki_enabled", True))
        self.wiki_check.stateChanged.connect(lambda s: self.config.set("pcgamingwiki_enabled", s == Qt.Checked.value))
        layout.addWidget(self.wiki_check)
        
        ll = QHBoxLayout()
        ll.addWidget(QLabel("<b>Log Level:</b>"))
        self.log_combo = QComboBox()
        self.log_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.log_combo.setCurrentText(self.config.get("log_level", "INFO").upper())
        self.log_combo.currentTextChanged.connect(self.set_log_level)
        ll.addWidget(self.log_combo)
        ll.addStretch()
        layout.addLayout(ll)
        
        layout.addSpacing(10)
        ub = QPushButton("Check for Updates")
        ub.clicked.connect(self.check_updates)
        layout.addWidget(ub)
        
        self.up_btn = QPushButton("Upgrade Available!")
        self.up_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.up_btn.setVisible(False)
        layout.addWidget(self.up_btn)
        
        self.pbar = QProgressBar()
        self.pbar.setVisible(False)
        layout.addWidget(self.pbar)
        layout.addStretch()
        
        lb = QPushButton("Log Out")
        lb.setStyleSheet("background: #c62828; color: white;")
        lb.clicked.connect(self.do_logout)
        layout.addWidget(lb)
        
        bb = QDialogButtonBox(QDialogButtonBox.Close, self)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)
        
    def browse_win(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Folder")
        if directory:
            self.win_input.setText(directory)
            self.config.set("windows_games_dir", directory)
            
    def _test_host_connection(self):
        host = self.host_input.text().strip()
        if not host: return
        self.test_btn.setText("Testing...")
        self.test_btn.setEnabled(False)
        ok, msg = self.main_window.client.test_connection(
            host_override=host, 
            retry_callback=lambda: self.test_btn.setText("Retrying...")
        )
        self.test_btn.setText("Test Connection")
        self.test_btn.setEnabled(True)
        if ok:
            QMessageBox.information(self, "Success", f"{msg} Click Apply.")
            self.re_btn.setVisible(True)
        else:
            QMessageBox.warning(self, "Failed", msg)
            self.re_btn.setVisible(False)
            
    def _apply_and_restart(self):
        self.config.set("host", self.host_input.text().strip())
        QMessageBox.information(self, "Restarting", "App will restart.")
        self._do_restart()
        
    def _do_restart(self):
        if sys.platform == "win32":
            subprocess.Popen([sys.executable], close_fds=True, creationflags=(0x00000008 | 0x00000200), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen([sys.executable], close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sys.exit(0)
        
    def toggle_auto_pull(self, checked):
        self.config.set("auto_pull_saves", checked)
        self.ap_btn.setText("Auto Pull Saves: ON" if checked else "Auto Pull Saves: OFF")
        
    def set_cards_per_row(self, val):
        self.config.set("cards_per_row", val)
        self.main_window.library_tab._resize_all_cards()
        
    def set_log_level(self, text):
        self.config.set("log_level", text)
        logging.getLogger().setLevel(getattr(logging, text.upper(), logging.INFO))
        
    def set_ra_save_mode(self, text):
        mode = {"SRM only": "srm", "States only": "state", "Both": "both"}.get(text, "srm")
        self.config.set("retroarch_save_mode", mode)
        
    def check_updates(self):
        self.updater = UpdaterThread(self.main_window.version)
        self.updater.finished.connect(self.on_update_result)
        self.updater.start()
        
    def on_update_result(self, available, version, url):
        if available:
            self.latest_url = url
            self.up_btn.setText(f"Upgrade to v{version}")
            self.up_btn.setVisible(True)
            if getattr(sys, 'frozen', False):
                self.up_btn.clicked.connect(self.start_self_update)
            else:
                self.up_btn.clicked.connect(lambda: webbrowser.open(url))
        else:
            QMessageBox.information(self, "No Updates", "You are on the latest version.")
            
    def start_self_update(self):
        self.up_btn.setEnabled(False)
        self.pbar.setVisible(True)
        self.t = SelfUpdateThread(self.latest_url, Path(sys.executable).resolve())
        self.t.progress.connect(self.pbar.setValue)
        self.t.finished.connect(self.on_self_update_finished)
        self.t.start()
        
    def on_self_update_finished(self, success, msg):
        if success:
            QMessageBox.information(self, "Done", "Update installed. Restarting...")
            subprocess.Popen(['cmd.exe', '/c', f'timeout /t 2 >NUL & start "" "{sys.executable}"'], creationflags=subprocess.CREATE_NO_WINDOW)
            sys.exit(0)
        else:
            QMessageBox.critical(self, "Failed", msg)
            
    def do_logout(self):
        if QMessageBox.question(self, "Log Out", "Are you sure you want to log out?") == QMessageBox.Yes:
            self.main_window.client.logout()
            self.config.set("password", None)
            sys.exit(0)

class GameDetailDialog(QDialog):
    def __init__(self, game, client, config, main_window, parent=None):
        super().__init__(parent)
        self.game = game
        self.client = client
        self.config = config
        self.main_window = main_window
        self.setWindowTitle(game.get("name"))
        self.setFixedSize(800, 550)
        self.dl_thread = None
        self.extract_thread = None
        self._conflict_shown = False
        self._is_windows = game.get("platform_slug") in WINDOWS_PLATFORM_SLUGS
        self._local_rom_path = self._get_local_rom_path()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        
        title_label = QLabel(game.get('name'))
        title_label.setStyleSheet("font-size: 20pt; font-weight: bold; color: #1e88e5;")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        
        content_layout = QHBoxLayout()
        content_layout.setSpacing(25)
        
        self.img_label = QLabel()
        self.img_label.setFixedWidth(300)
        self.img_label.setStyleSheet("background: #1a1a1a; border-radius: 6px;")
        content_layout.addWidget(self.img_label)
        
        right_column = QVBoxLayout()
        right_column.setSpacing(0)
        
        right_column.addWidget(QLabel(f"<b>Platform:</b> {game.get('platform_display_name')}", styleSheet="font-size: 12pt; margin-bottom: 2px;"))
        
        total_bytes = sum(f.get('file_size_bytes', 0) for f in game.get('files', []))
        right_column.addWidget(QLabel(f"<b>Size:</b> {format_size(total_bytes)}", styleSheet="font-size: 12pt; margin-bottom: 8px;"))
        
        self.desc_scroll = QScrollArea()
        self.desc_scroll.setWidgetResizable(True)
        self.desc_scroll.setStyleSheet("background: transparent; border: none;")
        
        self.desc_label = QLabel("Loading description...")
        self.desc_label.setWordWrap(True)
        self.desc_label.setAlignment(Qt.AlignTop)
        self.desc_label.setStyleSheet("color: #ccc; font-size: 11pt; line-height: 1.4;")
        self.desc_scroll.setWidget(self.desc_label)
        right_column.addWidget(self.desc_scroll, 1)
        
        self.pbar = QProgressBar()
        self.pbar.setVisible(False)
        right_column.addWidget(self.pbar)
        
        self.speed_label = QLabel()
        self.speed_label.setAlignment(Qt.AlignCenter)
        right_column.addWidget(self.speed_label)
        
        actions_layout = QVBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)
        
        self.play_btn = QPushButton("▶ PLAY")
        self.play_btn.setStyleSheet("background: #2e7d32; color: white; font-weight: bold; padding: 10px; font-size: 13pt;")
        self.play_btn.clicked.connect(self.play_game)
        
        self.gs_btn = QPushButton("⚙ Game Settings")
        self.gs_btn.setStyleSheet("background: #455a64; color: white; padding: 8px; font-size: 11pt;")
        self.gs_btn.clicked.connect(self.open_game_settings)
        
        self.un_btn = QPushButton("🗑 Uninstall")
        self.un_btn.setStyleSheet("background: #8e0000; color: white; padding: 6px; font-size: 11pt;")
        self.un_btn.clicked.connect(self.uninstall_game)
        
        self.dl_btn = QPushButton("⬇ DOWNLOAD")
        self.dl_btn.setStyleSheet("background: #1565c0; color: white; font-weight: bold; padding: 10px; font-size: 13pt;")
        self.dl_btn.clicked.connect(self._on_download_clicked)
        
        self.can_btn = QPushButton("Cancel Download")
        self.can_btn.setStyleSheet("background: #c62828; color: white;")
        self.can_btn.setVisible(False)
        self.can_btn.clicked.connect(self.cancel_dl)
        
        actions_layout.addWidget(self.play_btn)
        actions_layout.addWidget(self.gs_btn)
        actions_layout.addWidget(self.un_btn)
        actions_layout.addWidget(self.dl_btn)
        actions_layout.addWidget(self.can_btn)
        
        right_column.addLayout(actions_layout)
        content_layout.addLayout(right_column, 1)
        layout.addLayout(content_layout)
        
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("background: #333; color: #ccc; padding: 8px; font-size: 14pt;")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)
        
        self._update_button_states()
        self._start_image_fetch()
        self._start_desc_fetch()
        
    def _get_local_rom_path(self):
        if self._is_windows:
            wd = self.config.get("windows_games_dir")
            fn = self.game.get('fs_name')
            return Path(wd) / Path(fn).stem if wd and fn else None
        
        br = self.config.get("base_rom_path")
        fn = self.game.get('fs_name')
        return Path(br) / self.game.get('platform_slug') / fn if br and fn else None
        
    def _update_button_states(self):
        if self._is_windows and self._local_rom_path and self._local_rom_path.is_dir():
            exists = any(self._local_rom_path.rglob("*.exe"))
        else:
            exists = self._local_rom_path and self._local_rom_path.exists()
            
        if not exists and not self._is_windows:
            br = self.config.get("base_rom_path")
            fn = self.game.get('fs_name')
            rp = Path(br) / fn if br and fn else None
            if rp and rp.exists():
                self._local_rom_path = rp
                exists = True
                
        self.play_btn.setVisible(exists)
        self.gs_btn.setVisible(exists and self._is_windows)
        self.un_btn.setVisible(exists)
        self.dl_btn.setVisible(not exists)
        
    def open_game_settings(self):
        if WindowsGameSettingsDialog(self.game, self.config, self.main_window, self).exec() == QDialog.Accepted:
            self._update_button_states()
            
    def _start_image_fetch(self):
        url = self.client.get_cover_url(self.game)
        if url:
            self.it = ImageFetcher(self.game['id'], url)
            self.it.finished.connect(lambda g, p: self.img_label.setPixmap(p.scaled(300, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            self.it.finished.connect(lambda t=self.it: self.main_window.active_threads.remove(t) if t in self.main_window.active_threads else None)
            self.main_window.active_threads.append(self.it)
            self.it.start()
            
    def _start_desc_fetch(self):
        self.dt = GameDescriptionFetcher(self.client, self.game['id'])
        self.dt.finished.connect(self.desc_label.setText)
        self.dt.finished.connect(lambda t=self.dt: self.main_window.active_threads.remove(t) if t in self.main_window.active_threads else None)
        self.main_window.active_threads.append(self.dt)
        self.dt.start()
        
    def uninstall_game(self):
        msg = f"Are you sure you want to delete {self.game.get('name')}?"
        if self._is_windows:
            msg = f"Permanently delete ALL files in:\n{self._local_rom_path}?"
            
        if QMessageBox.question(self, "Uninstall", msg, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            try:
                p = self._local_rom_path
                if p.exists():
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        os.remove(p)
                    self.main_window.log(f"🗑 {self.game.get('name')} uninstalled")
                    self._update_button_states()
                    self.main_window.library_tab.apply_filters()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
                
    def _on_download_clicked(self):
        if self._is_windows and not self.config.get("windows_games_dir"):
            directory = QFileDialog.getExistingDirectory(self, "Select Windows Games Folder")
            if directory:
                self.config.set("windows_games_dir", directory)
            else:
                return
        
        files = self.game.get('files', [])
        if files:
            self.download_rom(files[0])
            
    def _do_blocking_pull(self, save_info, is_ra):
        watcher = self.main_window.watcher
        rom_id = self.game['id']
        title = self.game['name']
        
        if self._is_windows:
            save_dir = windows_saves.get_save_dir(rom_id)
            if save_dir:
                latest = watcher.client.get_latest_save(rom_id)
                if latest:
                    return self._apply_save_blocking(rom_id, title, latest, save_dir, "save", True) is not False
            return True
            
        # Standard emulator logic
        if save_info:
            if is_ra:
                if save_info.get('srm'):
                    latest = watcher.client.get_latest_save(rom_id)
                    if latest:
                        self._apply_save_blocking(rom_id, title, latest, save_info['srm'], "save")
                if save_info.get('state'):
                    latest = watcher.client.get_latest_state(rom_id)
                    if latest:
                        self._apply_save_blocking(rom_id, title, latest, save_info['state'], "state")
            else:
                # Direct emulator
                latest = watcher.client.get_latest_save(rom_id)
                if latest:
                    # Resolve if it's a folder or file
                    is_folder = os.path.isdir(save_info) if os.path.exists(save_info) else False
                    self._apply_save_blocking(rom_id, title, latest, save_info, "save", is_folder)
        return True
        
    def _apply_save_blocking(self, rom_id, title, obj, local_path, file_type, is_folder=False):
        import tempfile
        watcher = self.main_window.watcher
        server_updated_at = obj.get('updated_at', '')
        local_exists = os.path.isdir(local_path) if is_folder else os.path.exists(local_path)
        
        cached_entry = watcher.sync_cache.get(str(rom_id), {})
        if isinstance(cached_entry, dict):
            cached_ts = cached_entry.get(f'{file_type}_updated_at', '')
        else:
            cached_ts = cached_entry if file_type == 'save' else ''
            
        if cached_ts == server_updated_at and local_exists:
            return True
            
        tmp = tempfile.mktemp(suffix=f".{file_type}")
        success = watcher.client.download_state(obj, tmp) if file_type == "state" else watcher.client.download_save(obj, tmp)
        if not success:
            return True
            
        if local_exists and str(rom_id) in watcher.sync_cache and not self._conflict_shown:
            behavior = self.config.get("conflict_behavior", "ask")
            if behavior == "prefer_local":
                if os.path.exists(tmp): os.remove(tmp)
                return True
            elif behavior == "ask":
                self._conflict_shown = True
                msg = QMessageBox(self)
                msg.setWindowTitle(f"Conflict — {title}")
                msg.setText(f"Local {file_type} differs from cloud.")
                keep_local = msg.addButton("Keep Local", QMessageBox.RejectRole)
                use_cloud = msg.addButton("Use Cloud", QMessageBox.AcceptRole)
                msg.exec()
                if msg.clickedButton() == keep_local:
                    if os.path.exists(tmp): os.remove(tmp)
                    return True
                    
        dest = Path(local_path)
        if is_folder:
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            
        if dest.exists():
            bak = Path(str(dest) + ".bak")
            try:
                if is_folder:
                    shutil.copytree(str(dest), str(bak), dirs_exist_ok=True)
                else:
                    shutil.copy2(str(dest), str(bak))
            except:
                pass
                
        try:
            if is_folder or (zipfile.is_zipfile(tmp) and not local_path.endswith(('.srm', '.state'))):
                extract_strip_root(tmp, local_path)
            else:
                shutil.copy2(tmp, str(dest))
                if file_type == "state" and dest.suffix == '.state' and not dest.name.endswith('.state.auto'):
                    auto_path = dest.with_name(dest.name + '.auto')
                    if auto_path.exists():
                        if auto_path.is_dir(): shutil.rmtree(auto_path)
                        else: auto_path.unlink()
                    dest.rename(auto_path)
                    
            if not isinstance(watcher.sync_cache.get(str(rom_id)), dict):
                watcher.sync_cache[str(rom_id)] = {}
            watcher.sync_cache[str(rom_id)][f'{file_type}_updated_at'] = server_updated_at
            watcher.save_cache()
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return True
        
    def play_game(self):
        if self._is_windows:
            folder = self._local_rom_path
            if not folder or not folder.exists():
                QMessageBox.warning(self, "Error", "Game folder not found.")
                self._update_button_states()
                return
                
            saved = windows_saves.get_windows_save(self.game['id'])
            default_exe = saved.get("default_exe") if saved else None
            
            if default_exe and os.path.exists(default_exe):
                exe_to_launch = default_exe
            else:
                exes = [str(p) for p in folder.rglob("*.exe") if not any(e.lower() in str(p).lower() for e in EXCLUDED_EXES)]
                if not exes:
                    QMessageBox.warning(self, "Error", "No game executables found.")
                    return
                if len(exes) == 1:
                    exe_to_launch = exes[0]
                else:
                    picker = ExePickerDialog(exes, self.game.get("name"), self)
                    if picker.exec() == QDialog.Accepted:
                        exe_to_launch = picker.selected_exe
                    else:
                        return
                        
            if exe_to_launch:
                if self.config.get("auto_pull_saves", True) and not self._do_blocking_pull(None, False):
                    return
                try:
                    self.main_window.log(f"🚀 Launching Windows Game: {os.path.basename(exe_to_launch)}")
                    proc = subprocess.Popen([exe_to_launch], cwd=os.path.dirname(exe_to_launch))
                    save_dir = windows_saves.get_save_dir(self.game['id'])
                    if self.main_window.watcher:
                        QTimer.singleShot(0, lambda: self.main_window.watcher.track_session(
                            proc, "Windows (Native)", self.game, exe_to_launch, exe_to_launch, 
                            skip_pull=True, windows_save_dir=save_dir
                        ))
                    self.accept()
                except Exception as e:
                    QMessageBox.critical(self, "Error", str(e))
            return
            
        local_rom = self._local_rom_path
        if not local_rom or not local_rom.exists():
            QMessageBox.warning(self, "Error", "Download the game first.")
            return
            
        emu_data, emu_name, platform = None, None, self.game.get('platform_slug')
        all_emus = emulators.load_emulators()
        assigned_id = self.config.get("platform_assignments", {}).get(platform)
        
        if assigned_id:
            emu_data = next((e for e in all_emus if e["id"] == assigned_id), None)
            if emu_data and emu_data.get("executable_path") and os.path.exists(emu_data["executable_path"]):
                emu_name = emu_data["name"]
            else:
                emu_data = None
                
        if not emu_data:
            emu_data = emulators.get_emulator_for_platform(platform)
            if emu_data and emu_data.get("executable_path") and os.path.exists(emu_data["executable_path"]):
                emu_name = emu_data["name"]
            else:
                emu_data = None
                
        if not emu_data:
            emu_data = next((e for e in all_emus if e["id"] == "retroarch"), None)
            if emu_data and emu_data.get("executable_path") and os.path.exists(emu_data["executable_path"]):
                emu_name = emu_data["name"]
            else:
                emu_data = None
                
        if not emu_data:
            QMessageBox.warning(self, "Error", "No valid emulator configured.")
            return
            
        self.main_window.log(f"🎮 Preparing {self.game.get('name')}...")
        self.main_window.ensure_watcher_running()
        
        try:
            exe_path = emu_data["executable_path"]
            is_ra = emu_data["id"] == "retroarch"
            watcher = self.main_window.watcher
            
            if is_ra:
                check_retroarch_autosave(exe_path, platform, self, self.config)
                core_name = RETROARCH_CORES.get(platform)
                if platform == "psp" or core_name == "ppsspp_libretro.dll":
                    check_ppsspp_assets(exe_path, self)
                if core_name:
                    core_path = Path(exe_path).parent / "cores" / core_name
                    if core_path.exists():
                        args = [exe_path, "-L", str(core_path), str(local_rom)]
                    else:
                        if QMessageBox.question(self, "Error", f"Core {core_name} missing. Download?") == QMessageBox.Yes:
                            self.start_core_download(core_name, Path(exe_path).parent, platform)
                        return
                else:
                    args = [exe_path, str(local_rom)]
            else:
                raw_args = emu_data.get("launch_args", ["{rom_path}"])
                args = [exe_path]
                for a in raw_args:
                    if a.replace("{rom_path}", str(local_rom)) != exe_path:
                        args.append(a.replace("{rom_path}", str(local_rom)))
                        
            if self.config.get("auto_pull_saves", True):
                if is_ra:
                    save_info = watcher.get_retroarch_save_path(self.game, {"path": exe_path})
                else:
                    res = watcher.resolve_save_path(emu_name, self.game['name'], f"\"{exe_path}\" \"{local_rom}\"", exe_path, platform)
                    save_info = res[0] if res else None
                if not self._do_blocking_pull(save_info, is_ra):
                    return
                    
            clean_env = os.environ.copy()
            for k in ["QT_QPA_PLATFORM_PLUGIN_PATH", "QT_PLUGIN_PATH", "QT_QPA_FONTDIR", "QT_QPA_PLATFORM", "QT_STYLE_OVERRIDE"]:
                clean_env.pop(k, None)
                
            proc = subprocess.Popen(args, env=clean_env, cwd=str(Path(exe_path).parent))
            self.main_window.log(f"🚀 Launched {emu_name} (PID: {proc.pid})")
            if self.main_window.watcher:
                QTimer.singleShot(0, lambda: self.main_window.watcher.track_session(proc, emu_name, self.game, str(local_rom), exe_path, skip_pull=True))
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            
    def download_rom(self, file_data):
        if self._is_windows:
            target_path = Path(self.config.get("windows_games_dir")) / file_data['file_name']
        else:
            base = self.config.get("base_rom_path")
            os.makedirs(Path(base) / self.game.get('platform_slug', 'unknown'), exist_ok=True)
            target_path, _ = QFileDialog.getSaveFileName(self, "Save ROM", str(Path(base) / self.game.get('platform_slug', 'unknown') / file_data['file_name']))
            if not target_path:
                return
            target_path = Path(target_path)
            
        self.dl_btn.setVisible(False)
        self.can_btn.setVisible(True)
        self.pbar.setVisible(True)
        self.speed_label.setText("Downloading...")
        
        t = RomDownloader(self.client, self.game['id'], file_data['file_name'], str(target_path))
        self.main_window.active_threads.append(t)
        self.main_window.download_queue.add_download(self.game.get('name'), t)
        
        t.progress.connect(lambda p, s: (self.pbar.setValue(p), self.speed_label.setText(f"Speed: {format_speed(s)}")))
        t.finished.connect(self.on_download_complete)
        t.finished.connect(lambda: self.main_window.download_queue.remove_download(t))
        t.finished.connect(lambda thread=t: self.main_window.active_threads.remove(thread) if thread in self.main_window.active_threads else None)
        self.dl_thread = t
        t.start()
        
    def cancel_dl(self):
        if self.dl_thread:
            self.dl_thread.requestInterruption()
        self.on_download_complete(False, "Cancelled")
        
    def on_download_complete(self, ok, path):
        if not ok:
            self.can_btn.setVisible(False)
            self.pbar.setVisible(False)
            self.speed_label.setText("")
            self._update_button_states()
            if path != "Cancelled":
                QMessageBox.critical(self, "Error", f"Failed: {path}")
            return
            
        if self._is_windows:
            win_dir = self.config.get("windows_games_dir")
            if not win_dir:
                QMessageBox.warning(self, "Error", "Set folder in Settings.")
                self.can_btn.setVisible(False)
                self.pbar.setVisible(False)
                self.speed_label.setText("")
                return
                
            self.speed_label.setText("Extracting...")
            fs_name = self.game.get('fs_name')
            final_target = Path(win_dir) / Path(fs_name).stem if fs_name else None
            self._local_rom_path = final_target
            
            self.et = ExtractionThread(path, str(final_target))
            self.main_window.active_threads.append(self.et)
            self.et.progress.connect(self.pbar.setValue)
            self.et.finished.connect(self.on_extraction_done)
            self.et.error.connect(lambda m: QMessageBox.warning(self, "Error", m))
            self.et.start()
        else:
            self._local_rom_path = Path(path)
            self.can_btn.setVisible(False)
            self.pbar.setVisible(False)
            self.speed_label.setText("")
            self._update_button_states()
            self.main_window.fetch_library_and_populate()
            
    def on_extraction_done(self, path):
        self.pbar.setVisible(False)
        self.speed_label.setText("✅ Ready to play!")
        self._update_button_states()
        self.main_window.fetch_library_and_populate()
        
    def start_core_download(self, core_name, emu_dir, platform):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Downloading {core_name}")
        dlg.setFixedSize(350, 100)
        l = QVBoxLayout(dlg)
        status = QLabel(f"Downloading for {platform}...")
        pb = QProgressBar()
        l.addWidget(status)
        l.addWidget(pb)
        dlg.setWindowModality(Qt.ApplicationModal)
        
        t = CoreDownloadThread(core_name, emu_dir / "cores")
        t.progress.connect(lambda v, s: (pb.setValue(v), status.setText(f"Speed: {format_speed(s)}")))
        t.finished.connect(lambda success, msg: (dlg.close(), self.play_game() if success else QMessageBox.critical(self, "Error", msg)))
        t.start()
        dlg.exec()

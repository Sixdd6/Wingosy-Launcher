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
from src import emulators, windows_saves, download_registry
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

        self.right_column = QVBoxLayout()
        self.right_column.setSpacing(0)

        self.right_column.addWidget(QLabel(f"<b>Platform:</b> {game.get('platform_display_name')}", styleSheet="font-size: 12pt; margin-bottom: 2px;"))

        total_bytes = sum(f.get('file_size_bytes', 0) for f in game.get('files', []))
        self.right_column.addWidget(QLabel(f"<b>Size:</b> {format_size(total_bytes)}", styleSheet="font-size: 12pt; margin-bottom: 8px;"))

        self.desc_scroll = QScrollArea()
        self.desc_scroll.setWidgetResizable(True)
        self.desc_scroll.setStyleSheet("background: transparent; border: none;")

        self.desc_label = QLabel("Loading description...")
        self.desc_label.setWordWrap(True)
        self.desc_label.setAlignment(Qt.AlignTop)
        self.desc_label.setStyleSheet("color: #ccc; font-size: 11pt; line-height: 1.4;")
        self.desc_scroll.setWidget(self.desc_label)
        self.right_column.addWidget(self.desc_scroll, 1)

        self.pbar = QProgressBar()
        self.pbar.setVisible(False)
        self.pbar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 3px;
                background: #2d2d2d;
                height: 8px;
            }
            QProgressBar::chunk {
                border-radius: 3px;
                background: #0d6efd;
            }
        """)
        self.right_column.addWidget(self.pbar)

        self.speed_label = QLabel()
        self.speed_label.setAlignment(Qt.AlignCenter)
        self.right_column.addWidget(self.speed_label)

        self.actions_layout = QVBoxLayout()
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(4)

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

        self.actions_layout.addWidget(self.play_btn)
        self.actions_layout.addWidget(self.gs_btn)
        self.actions_layout.addWidget(self.un_btn)
        self.actions_layout.addWidget(self.dl_btn)
        self.actions_layout.addWidget(self.can_btn)

        self.right_column.addLayout(self.actions_layout)
        content_layout.addLayout(self.right_column, 1)
        layout.addLayout(content_layout)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("background: #333; color: #ccc; padding: 8px; font-size: 14pt;")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

        # After building the UI, check registry
        self._reconnect_active_download()
            
        self._start_image_fetch()
        self._start_desc_fetch()

    def _reconnect_active_download(self):
        rom_id = str(self.game["id"])
        entry = download_registry.get(rom_id)
        
        if not entry:
            self._update_button_states()
            return
        
        # Active download or extraction found!
        row_type = entry["type"]
        current, total = entry["progress"]
        
        self.play_btn.hide()
        self.dl_btn.hide()
        self.un_btn.hide()
        
        self.pbar.setVisible(True)
        if total > 0:
            self.pbar.setRange(0, 100)
            self.pbar.setValue(int(current / total * 100))
        else:
            self.pbar.setRange(0, 0)
        
        if row_type == "download":
            self.speed_label.setText("Downloading...")
        else:
            self.speed_label.setText("Extracting...")
        
        self.can_btn.show()
        
        self._progress_listener = self._on_registry_progress
        download_registry.add_listener(rom_id, self._progress_listener)

    def _on_registry_progress(self, rom_id, rtype, current, total, speed=0):
        if rtype == "done" or rtype == "cancelled":
            download_registry.remove_listener(rom_id, self._progress_listener)
            self.pbar.setVisible(False)
            self.can_btn.hide()
            self.speed_label.setText("")
            self._update_button_states()
            return
        
        if total > 0:
            self.pbar.setRange(0, 100)
            self.pbar.setValue(int(current / total * 100))
        else:
            self.pbar.setRange(0, 0)
        
        if rtype == "download":
            self.speed_label.setText(f"Downloading... {format_size(current)} / {format_size(total)}")
        elif rtype == "extraction":
            if total > 0:
                self.speed_label.setText(f"Extracting... {current}/{total} files")
            else:
                self.speed_label.setText("Extracting...")

    def download_rom(self, file_obj):
        if not file_obj: return
        
        # Determine target path
        if self._is_windows:
            target_dir = Path(self.config.get("windows_games_dir"))
            target_path = target_dir / file_obj['file_name']
        else:
            target_dir = Path(self.config.get("base_rom_path")) / self.game.get('platform_slug')
            target_path = target_dir / file_obj['file_name']
            
        os.makedirs(target_dir, exist_ok=True)
        
        self.dl_thread = RomDownloader(self.client, self.game['id'], file_obj['file_name'], str(target_path))
        download_registry.register_download(self.game['id'], self.game['name'], self.dl_thread)
        
        self.dl_thread.progress.connect(lambda d, t, s: download_registry.update_progress(self.game['id'], d, t, s))
        self.dl_thread.finished.connect(lambda ok, p: self._on_download_finished(ok, p))
        
        self.main_window.download_queue.add_download(self.game['name'], self.dl_thread, "download", self.game['id'])
        self.dl_thread.start()
        self._reconnect_active_download()

    def _on_download_finished(self, ok, path):
        if not ok:
            download_registry.unregister(self.game['id'])
            return
            
        # If it's an archive and we are on Windows, or just need extraction
        if path.endswith(('.zip', '.7z', '.iso')):
            # Pre-fetch 7z.exe in background so extraction starts immediately
            from src.sevenzip import get_7zip_exe
            
            class SevenZipFetcher(QThread):
                ready = Signal(str)
                def run(self):
                    exe = get_7zip_exe()
                    self.ready.emit(exe or "")
            
            self.speed_label.setText("Preparing extractor...")
            self._sz_fetcher = SevenZipFetcher()
            self._sz_fetcher.ready.connect(lambda exe: self._start_extraction(path))
            self._sz_fetcher.start()
        else:
            download_registry.unregister(self.game['id'])
            self._update_button_states()

    def _on_extraction_finished(self, path):
        download_registry.unregister(self.game['id'])
        self._update_button_states()
        self.main_window.fetch_library_and_populate()

    def cancel_dl(self):
        rom_id = str(self.game["id"])
        entry = download_registry.get(rom_id)
        if not entry or not entry.get("thread"):
            return

        rom_name = self.game.get('name', 'this game')
        if entry["type"] == "extraction":
            reply = QMessageBox.question(
                self, "Cancel Extraction",
                f"Cancel extracting {rom_name}?\n\nWhat should happen to the files extracted so far?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel
            )
            if reply == QMessageBox.Cancel: return
            
            entry["thread"].cancel()
            if reply == QMessageBox.Discard:
                def on_cancelled(path):
                    import shutil
                    shutil.rmtree(path, ignore_errors=True)
                entry["thread"].cancelled.connect(on_cancelled)
        else:
            reply = QMessageBox.question(
                self, "Cancel Download",
                f"Cancel downloading {rom_name}?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel
            )
            if reply == QMessageBox.Cancel: return
            
            entry["thread"].cancel()
            if reply == QMessageBox.Discard:
                def on_cancelled_dl():
                    p = getattr(entry["thread"], 'file_path', None)
                    if p and os.path.exists(p):
                        try: os.remove(p)
                        except: pass
                entry["thread"].cancelled.connect(on_cancelled_dl)

        download_registry.update_status(rom_id, "cancelled")
        QTimer.singleShot(1000, lambda: download_registry.unregister(rom_id))
        self.can_btn.hide()
        self.pbar.hide()
        self._update_button_states()

    def closeEvent(self, event):
        rom_id = str(self.game["id"])
        if hasattr(self, '_progress_listener'):
            download_registry.remove_listener(rom_id, self._progress_listener)
        super().closeEvent(event)

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
        self.dl_btn.setText("⬇ DOWNLOAD")
        self.dl_btn.setStyleSheet("background: #1565c0; color: white; font-weight: bold; padding: 10px; font-size: 13pt;")
        
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
        windows_dir = self.config.get("windows_games_dir", "")
        if self._is_windows and not windows_dir:
            directory = QFileDialog.getExistingDirectory(self, "Select Windows Games Folder")
            if directory:
                self.config.set("windows_games_dir", directory)
                windows_dir = directory
            else:
                return

        files = self.game.get('files', [])
        if not files:
            return

        file_obj = files[0]
        rom_name = file_obj.get("file_name", "")

        # Windows-specific pre-download checks
        if self._is_windows and windows_dir:
            archive_path = Path(windows_dir) / rom_name
            extracted_dir = Path(windows_dir) / Path(rom_name).stem

            # 1. Check if already installed
            if extracted_dir.exists() and any(extracted_dir.rglob("*.exe")):
                QMessageBox.information(
                    self, "Already Installed",
                    f"{self.game['name']} appears to already be installed at:\n{extracted_dir}\n\nUse the Play button to launch it."
                )
                self._update_button_states()
                return

            # 2. Check if archive exists
            if archive_path.exists():
                reply = QMessageBox.question(
                    self, "Archive Already Downloaded",
                    f"{rom_name} already exists in your Windows Games folder.\n\nWould you like to extract it now instead of downloading again?",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Cancel:
                    return
                if reply == QMessageBox.Yes:
                    self._start_extraction(str(archive_path))
                    return

        self.download_rom(file_obj)

    def _start_extraction(self, path):
        target_dir = Path(path).parent
        if self._is_windows:
            target_dir = target_dir / Path(path).stem

        self.extract_thread = ExtractionThread(path, str(target_dir))
        download_registry.register_extraction(self.game['id'], self.game['name'], self.extract_thread)

        self.extract_thread.progress.connect(lambda d, t: download_registry.update_progress(self.game['id'], d, t))
        self.extract_thread.finished.connect(self._on_extraction_finished)

        self.main_window.download_queue.add_download(self.game['name'], self.extract_thread, "extraction", self.game['id'])
        self.extract_thread.start()
        self._reconnect_active_download()

    def download_rom(self, file_obj):

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
            # Resolve conflict behavior
            behavior = "ask"
            if self._is_windows:
                behavior = self.config.get("windows_conflict_behavior", "ask")
            else:
                # Try to find the emulator that would be used
                all_emus = emulators.load_emulators()
                assigned_id = self.config.get("platform_assignments", {}).get(self.game.get('platform_slug'))
                emu = None
                if assigned_id:
                    emu = next((e for e in all_emus if e["id"] == assigned_id), None)
                if not emu:
                    emu = emulators.get_emulator_for_platform(self.game.get('platform_slug'))
                if not emu:
                    emu = next((e for e in all_emus if e["id"] == "retroarch"), None)
                
                if emu:
                    behavior = emu.get("conflict_behavior", "ask")

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
        
    def _pull_windows_save(self, client, rom_id, save_dir):
        """Helper to download and extract latest Windows save zip."""
        try:
            latest = client.get_latest_save(rom_id)
            if not latest: return False
            
            import tempfile
            tmp = tempfile.mktemp(suffix=".zip")
            if client.download_save(latest, tmp):
                os.makedirs(save_dir, exist_ok=True)
                extract_strip_root(tmp, save_dir)
                if os.path.exists(tmp): os.remove(tmp)
                return True
        except Exception as e:
            logging.error(f"Failed to pull Windows save: {e}")
        return False

    def play_game(self):
        if self._is_windows:
            folder = self._local_rom_path
            if not folder or not folder.exists():
                QMessageBox.warning(self, "Error", "Game folder not found.")
                self._update_button_states()
                return
                
            # SMART PULL for Windows
            save_dir = windows_saves.get_save_dir(self.game['id'])
            if save_dir and self.config.get("windows_sync_enabled", True):
                should_pull = False
                reason = ""
                p = Path(save_dir)
                
                # Check 1: Missing or empty
                if not p.exists() or not any(p.iterdir()):
                    should_pull, reason = True, "local save folder is empty"
                else:
                    # Check 2: Remote is newer
                    remote = self.client.get_latest_save(self.game['id'])
                    if remote:
                        remote_ts = remote.get("updated_at", "")
                        local_mtime = max((f.stat().st_mtime for f in p.rglob("*") if f.is_file()), default=0)
                        from datetime import datetime
                        try:
                            remote_dt = datetime.fromisoformat(remote_ts.replace("Z", "+00:00"))
                            if remote_dt.timestamp() > local_mtime:
                                should_pull, reason = True, "cloud save is newer"
                        except Exception:
                            should_pull, reason = True, "could not compare timestamps"
                
                if should_pull:
                    behavior = self.config.get("windows_conflict_behavior", "ask")
                    if behavior == "prefer_local":
                        should_pull = False
                    elif behavior == "prefer_cloud":
                        if self._pull_windows_save(self.client, self.game['id'], save_dir):
                            self.main_window.log(f"☁️ Pulled save ({reason})")
                    else:
                        # "ask"
                        res = QMessageBox.question(self, "Cloud Save Found", 
                            f"A newer cloud save was found ({reason}).\n\nWould you like to download it now?",
                            QMessageBox.Yes | QMessageBox.No)
                        if res == QMessageBox.Yes:
                            if self._pull_windows_save(self.client, self.game['id'], save_dir):
                                self.main_window.log(f"☁️ Pulled save ({reason})")

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

import os
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QScrollArea, QFormLayout, 
                             QLineEdit, QFileDialog, QMessageBox)
from PySide6.QtCore import Qt

from src.ui.threads import (DirectDownloader, DolphinDownloader,
                             GithubDownloader, BiosDownloader, CoreDownloadThread)
from src.ui.widgets import format_speed, get_resource_path
from src import emulators

class EmulatorsTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.config = main_window.config
        
        layout = QVBoxLayout(self)
        
        paths_widget = QWidget()
        form_layout = QFormLayout(paths_widget)
        
        rom_path_layout = QHBoxLayout()
        self.rom_path_input = QLineEdit(self.config.get("base_rom_path"))
        rom_path_layout.addWidget(self.rom_path_input)
        browse_rom_btn = QPushButton("Browse")
        browse_rom_btn.clicked.connect(lambda: self.browse_directory("base_rom_path", self.rom_path_input))
        rom_path_layout.addWidget(browse_rom_btn)
        form_layout.addRow("ROM Path:", rom_path_layout)
        
        emu_path_layout = QHBoxLayout()
        self.emu_path_input = QLineEdit(self.config.get("base_emu_path"))
        emu_path_layout.addWidget(self.emu_path_input)
        browse_emu_btn = QPushButton("Browse")
        browse_emu_btn.clicked.connect(lambda: self.browse_directory("base_emu_path", self.emu_path_input))
        emu_path_layout.addWidget(browse_emu_btn)
        form_layout.addRow("Emu Path:", emu_path_layout)
        
        save_paths_btn = QPushButton("Save Paths")
        save_paths_btn.clicked.connect(self.save_paths)
        form_layout.addRow(save_paths_btn)
        layout.addWidget(paths_widget)
        
        self.emu_list_layout = QVBoxLayout()
        self.emu_list_layout.setAlignment(Qt.AlignTop)
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        emulator_container = QWidget()
        emulator_container.setLayout(self.emu_list_layout)
        scroll_area.setWidget(emulator_container)
        layout.addWidget(scroll_area)
        
        self.populate_emus()

    def browse_directory(self, key, line_edit):
        directory = QFileDialog.getExistingDirectory(self, "Select Folder")
        if directory:
            line_edit.setText(directory)
            self.config.set(key, directory)

    def save_paths(self):
        self.config.set("base_rom_path", self.rom_path_input.text())
        self.config.set("base_emu_path", self.emu_path_input.text())
        self.main_window.log("✅ Paths saved.")
        self.populate_emus()
        self.main_window.library_tab.apply_filters()

    def populate_emus(self):
        for i in reversed(range(self.emu_list_layout.count())):
            item = self.emu_list_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)
        
        all_emus = emulators.load_emulators()
        for emu_data in all_emus:
            emu_id = emu_data.get("id")
            name = emu_data.get("name")
            
            row = QWidget()
            row.setStyleSheet("background: #252525; border-radius: 5px; margin: 2px;")
            row_layout = QHBoxLayout(row)
            
            # Health Indicator
            path = emu_data.get("executable_path", "")
            indicator = "✅" if path and os.path.exists(path) else ""
            
            indicator_label = QLabel(indicator)
            indicator_label.setFixedWidth(24)
            row_layout.addWidget(indicator_label)
            
            name_label = QLabel(f"<b>{name}</b>")
            name_label.setFixedWidth(180)
            row_layout.addWidget(name_label)
            
            path_label = QLabel(path or "Not Set")
            path_label.setStyleSheet("color: #888;")
            row_layout.addWidget(path_label, 1)
            
            # Action Buttons - Note: These now need to handle the new ID-based system
            btn_latest = QPushButton("⬇️ Latest")
            btn_latest.clicked.connect(lambda checked, n=name: self.main_window.dl_emu(n))
            row_layout.addWidget(btn_latest)
            
            btn_fw = QPushButton("📂 Firmware")
            btn_fw.clicked.connect(lambda checked, n=name: self.main_window.open_fw(n))
            row_layout.addWidget(btn_fw)
            
            btn_path = QPushButton("Path")
            btn_path.clicked.connect(lambda checked, eid=emu_id: self.edit_emulator_path(eid))
            row_layout.addWidget(btn_path)
            
            btn_export = QPushButton("📤 Export")
            btn_export.clicked.connect(lambda checked, n=name: self.main_window.sy_ec(n, "export"))
            row_layout.addWidget(btn_export)
            
            btn_import = QPushButton("📥 Import")
            btn_import.clicked.connect(lambda checked, n=name: self.main_window.sy_ec(n, "import"))
            row_layout.addWidget(btn_import)
            
            self.emu_list_layout.addWidget(row)

    def edit_emulator_path(self, emu_id):
        all_emus = emulators.load_emulators()
        emu = next((e for e in all_emus if e["id"] == emu_id), None)
        if not emu: return

        # Try to find existing path
        start_dir = os.path.dirname(emu.get("executable_path")) if emu.get("executable_path") else ""
        if not start_dir or not os.path.exists(start_dir):
            start_dir = self.config.get("base_emu_path")

        file_path, _ = QFileDialog.getOpenFileName(self, f"Select {emu['name']} Executable", start_dir, "Executables (*.exe)")
        if file_path:
            emu["executable_path"] = file_path
            emulators.save_emulators(all_emus)
            self.main_window.log(f"✅ {emu['name']} path updated.")
            self.populate_emus()
            self.main_window.library_tab.apply_filters()

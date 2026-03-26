import os
import shlex
import subprocess
import logging
from functools import partial
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,   
                             QPushButton, QScrollArea, QFormLayout,
                             QLineEdit, QFileDialog,
                             QDialog, QComboBox, QDialogButtonBox, QTabWidget,
                             QCheckBox)
from PySide6.QtCore import Qt

from src import emulators
from src.ui.dialogs.styled_messagebox import StyledMessageBox


def _normalize_launch_args_for_display(raw_args, default="{rom_path}"):
    if isinstance(raw_args, list):
        return [str(a) for a in raw_args if a is not None]
    if isinstance(raw_args, str):
        return [raw_args]
    return [default]


def _normalize_platform_slugs(raw_slugs):
    if isinstance(raw_slugs, list):
        return [str(s).strip() for s in raw_slugs if str(s).strip()]
    if isinstance(raw_slugs, str):
        slug = raw_slugs.strip()
        return [slug] if slug else []
    return []


def _to_text(value, default=""):
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


class EmulatorSettingsDialog(QDialog):
    def __init__(self, main_window, emulator_id, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.config = main_window.config
        self.emulator_id = emulator_id
        self._is_saving = False
        self.setWindowTitle("Emulator Settings")
        self.setMinimumWidth(600)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.cloud_sync_check = QCheckBox("Enable cloud sync")
        self.cloud_sync_check.setChecked(self.config.get("auto_pull_saves", True))
        form.addRow("Cloud Sync:", self.cloud_sync_check)

        layout.addWidget(QLabel("<h3>Selected Emulator</h3>"))
        selected_emu = self._selected_emulator()
        selected_name = _to_text(selected_emu.get("name"), "Unknown Emulator") if selected_emu else "Unknown Emulator"
        self.emulator_name_label = QLabel(selected_name)
        self.emulator_name_label.setStyleSheet("color: #ddd; font-size: 13px;")
        layout.addWidget(self.emulator_name_label)

        exe_row = QHBoxLayout()
        selected_path = _to_text(selected_emu.get("executable_path"), "") if selected_emu else ""
        self.executable_input = QLineEdit(selected_path)
        self.executable_input.setPlaceholderText("C:/Path/to/emulator.exe")
        exe_row.addWidget(self.executable_input, 1)
        self.browse_emulator_btn = QPushButton("Browse")
        self.browse_emulator_btn.clicked.connect(self.browse_selected_emulator)
        exe_row.addWidget(self.browse_emulator_btn)
        form.addRow("Executable Path:", exe_row)

        launch_args = " ".join(_normalize_launch_args_for_display(selected_emu.get("launch_args"))) if selected_emu else "{rom_path}"
        self.args_input = QLineEdit(launch_args)
        self.args_input.setPlaceholderText("{rom_path}")
        form.addRow("Launch Arguments:", self.args_input)
        args_helper = QLabel("<small style='color:#888;'>Use {rom_path} for the game file. Example: --fullscreen {rom_path}</small>")
        form.addRow("", args_helper)

        layout.addLayout(form)

        actions_row = QHBoxLayout()
        self.check_updates_btn = QPushButton("Check for Updates")
        self.check_updates_btn.clicked.connect(self.check_selected_emulator_updates)
        actions_row.addWidget(self.check_updates_btn)
        self.firmware_btn = QPushButton("Firmware from Server")
        self.firmware_btn.clicked.connect(self.open_selected_emulator_firmware)
        actions_row.addWidget(self.firmware_btn)
        layout.addLayout(actions_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _selected_emulator(self):
        all_emus = [
            e for e in emulators.load_emulators()
            if isinstance(e, dict) and e.get("id") != "windows_native"
        ]
        return next((e for e in all_emus if e.get("id") == self.emulator_id), None)

    def browse_selected_emulator(self):
        emu = self._selected_emulator()
        if not emu:
            StyledMessageBox.warning(self, "No Emulator", "No emulator is available to configure.")
            return

        selected_path = _to_text(emu.get("executable_path"), "")
        start_dir = os.path.dirname(self.executable_input.text().strip() or selected_path)
        if not start_dir or not os.path.exists(start_dir):
            start_dir = os.path.dirname(selected_path)

        file_path, _ = QFileDialog.getOpenFileName(self, f"Select {emu.get('name', 'Emulator')} Executable", start_dir, "Executables (*.exe)")
        if not file_path:
            return

        self.executable_input.setText(file_path)

    def check_selected_emulator_updates(self):
        emu = self._selected_emulator()
        if not emu:
            StyledMessageBox.warning(self, "No Emulator", "No emulator is available to update.")
            return
        self.main_window.dl_emu(emu.get("id"))

    def open_selected_emulator_firmware(self):
        emu = self._selected_emulator()
        if not emu:
            StyledMessageBox.warning(self, "No Emulator", "No emulator is available.")
            return
        self.main_window.open_fw(emu.get("name", ""))

    def save_and_close(self):
        if self._is_saving:
            return
        self._is_saving = True
        try:
            all_emus = emulators.load_emulators()
            target = next((e for e in all_emus if e.get("id") == self.emulator_id), None)
            if target:
                target["executable_path"] = self.executable_input.text().strip()

                args_text = self.args_input.text().strip()
                if args_text:
                    try:
                        parsed_args = shlex.split(args_text, posix=False)
                    except ValueError:
                        parsed_args = args_text.split()
                    target["launch_args"] = parsed_args or ["{rom_path}"]
                else:
                    target["launch_args"] = ["{rom_path}"]

                emulators.save_emulators(all_emus)

            self.config.set("auto_pull_saves", self.cloud_sync_check.isChecked())
            self.main_window.log("✅ Emulator settings saved.")
            self.accept()
        except Exception as e:
            self._is_saving = False
            StyledMessageBox.warning(self, "Save Failed", f"Could not save emulator settings:\n{e}")

class EmulatorEditDialog(QDialog):
    def __init__(self, emu_data=None, parent=None):
        super().__init__(parent)
        self.emu_data = emu_data
        self.setWindowTitle("Edit Emulator" if emu_data else "Add Custom Emulator")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. DuckStation")
        if emu_data: self.name_input.setText(emu_data.get("name", ""))      
        form.addRow("Emulator Name:", self.name_input)

        path_layout = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("C:/Path/to/emulator.exe")       
        if emu_data: self.path_input.setText(emu_data.get("executable_path", ""))
        path_layout.addWidget(self.path_input)
        self.browse_exe_btn = QPushButton("Browse")
        self.browse_exe_btn.clicked.connect(self.browse_exe)
        path_layout.addWidget(self.browse_exe_btn)
        form.addRow("Executable Path:", path_layout)

        self.slugs_input = QLineEdit()
        self.slugs_input.setPlaceholderText("e.g. psx, ps1, playstation")   
        if emu_data: self.slugs_input.setText(", ".join(emu_data.get("platform_slugs", [])))
        form.addRow("Platform Slugs:", self.slugs_input)

        helper = QLabel("<small style='color:#888;'>Common: psx, ps2, ps3, switch, wiiu, 3ds, gc, wii, xbox, xbox360, nds, gba, snes, n64</small>")     
        form.addRow("", helper)

        self.args_input = QLineEdit()
        self.args_input.setPlaceholderText("{rom_path}")
        if emu_data: self.args_input.setText(" ".join(_normalize_launch_args_for_display(emu_data.get("launch_args"))))
        form.addRow("Launch Arguments:", self.args_input)

        args_helper = QLabel("<small style='color:#888;'>Use {rom_path} for the game file. Example: --fullscreen {rom_path}</small>")
        form.addRow("", args_helper)

        self.command_preview = QLabel("")
        self.command_preview.setWordWrap(True)
        self.command_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.command_preview.setStyleSheet("color: #bbb; font-family: Consolas, 'Courier New', monospace;")
        form.addRow("Command Preview:", self.command_preview)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Direct File (.sav/.mcr etc)", "direct_file")
        self.mode_combo.addItem("Folder Sync (zip entire folder)", "folder")
        self.mode_combo.addItem("No Save Sync", "none")

        save_res = emu_data.get("save_resolution") if emu_data else {}
        if not isinstance(save_res, dict):
            save_res = {}
        mode = save_res.get("mode", "none")
        idx = self.mode_combo.findData(mode)
        if idx >= 0: self.mode_combo.setCurrentIndex(idx)
        form.addRow("Save Sync Mode:", self.mode_combo)

        save_path_layout = QHBoxLayout()
        self.save_path_input = QLineEdit()
        if emu_data: self.save_path_input.setText(save_res.get("path", "") or save_res.get("srm_dir", ""))
        save_path_layout.addWidget(self.save_path_input)
        self.browse_save_btn = QPushButton("Browse")
        self.browse_save_btn.clicked.connect(self.browse_dir)
        self.save_path_label = QLabel("Save Directory:")
        form.addRow(self.save_path_label, save_path_layout)

        self.ext_input = QLineEdit()
        self.ext_input.setPlaceholderText(".mcd")
        if emu_data: self.ext_input.setText(save_res.get("extension", ""))  
        self.ext_label = QLabel("Save File Extension:")
        form.addRow(self.ext_label, self.ext_input)

        self.mode_combo.currentIndexChanged.connect(self.update_visibility) 
        self.update_visibility()

        self.path_input.textChanged.connect(self.update_command_preview)
        self.args_input.textChanged.connect(self.update_command_preview)
        self.update_command_preview()

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Emulator Executable", "", "Executables (*.exe)")
        if path: self.path_input.setText(path)

    def browse_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Save Directory")
        if path: self.save_path_input.setText(path)

    def update_visibility(self):
        mode = self.mode_combo.currentData()
        is_none = (mode == "none")
        is_direct = (mode == "direct_file")

        self.save_path_input.setEnabled(not is_none)
        self.browse_save_btn.setEnabled(not is_none)

        self.ext_input.setVisible(is_direct)
        self.ext_label.setVisible(is_direct)

    def _build_command_preview_args(self):
        exe_path = os.path.normpath(self.path_input.text().strip())
        try:
            raw_args = shlex.split(self.args_input.text(), posix=False)
        except ValueError:
            raw_args = self.args_input.text().split()

        if not exe_path:
            return []

        rom_example = os.path.normpath(r"C:\Path\To\Game.rom")
        args = [exe_path]
        for a in raw_args:
            expanded = a.replace("{rom_path}", rom_example)
            if expanded != exe_path:
                args.append(expanded)
        return args

    def update_command_preview(self):
        args = self._build_command_preview_args()
        if not args:
            self.command_preview.setText("(set an executable path to see a preview)")
            return

        try:
            self.command_preview.setText(subprocess.list2cmdline(args))
        except Exception:
            self.command_preview.setText(" ".join(args))

    def validate_and_save(self):
        name = self.name_input.text().strip()
        exe = self.path_input.text().strip()
        slugs = [s.strip() for s in self.slugs_input.text().split(",") if s.strip()]

        if not name or not exe or not slugs:
            StyledMessageBox.warning(self, "Error", "Name, path, and at least one slug are required.")
            return

        emu_id = name.lower().replace(" ", "_")

        if not self.emu_data:
            all_emus = emulators.load_emulators()
            existing = next((e for e in all_emus if e.get("id") == emu_id), None)
            if existing and not existing.get("user_defined"):
                StyledMessageBox.warning(self, "Error", "Cannot overwrite a built-in emulator. Choose a different name.")
                return

        mode = self.mode_combo.currentData()
        save_res = {"mode": mode}
        if mode != "none":
            save_res["path"] = self.save_path_input.text().strip()
            if mode == "direct_file":
                save_res["extension"] = self.ext_input.text().strip()       

        new_data = {
            "id": emu_id,
            "name": name,
            "executable_path": exe,
            "launch_args": [],
            "platform_slugs": slugs,
            "save_resolution": save_res,
            "user_defined": True,
            "sync_enabled": True,
            "conflict_behavior": "ask"
        }
        args_text = self.args_input.text().strip()
        if args_text:
            try:
                new_data["launch_args"] = shlex.split(args_text, posix=False)
            except ValueError:
                new_data["launch_args"] = args_text.split()

        self.result_data = new_data
        self.accept()

class EmuListWidget(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.config = main_window.config
        self._row_button_style = (
            "QPushButton {"
            " background: #2f2f2f;"
            " color: #e6e6e6;"
            " border: 1px solid #3f3f3f;"
            " padding: 5px 10px;"
            " border-radius: 4px;"
            " font-size: 12px;"
            " }"
            "QPushButton:hover { background: #383838; border-color: #565656; }"
            "QPushButton:pressed { background: #262626; border-color: #1f1f1f; }"
            "QPushButton:disabled { background: #2a2a2a; color: #888; border-color: #2f2f2f; }"
        )
        self._row_button_danger_style = (
            "QPushButton {"
            " background: rgba(255, 82, 82, 0.12);"
            " color: #ff8a8a;"
            " border: 1px solid rgba(255, 82, 82, 0.35);"
            " padding: 5px 10px;"
            " border-radius: 4px;"
            " font-size: 12px;"
            " }"
            "QPushButton:hover { background: rgba(255, 82, 82, 0.18); border-color: rgba(255, 82, 82, 0.55); }"
            "QPushButton:pressed { background: rgba(255, 82, 82, 0.10); border-color: rgba(255, 82, 82, 0.70); }"
            "QPushButton:disabled { background: rgba(255, 82, 82, 0.06); color: rgba(255, 138, 138, 0.6); border-color: rgba(255, 82, 82, 0.20); }"
        )
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Emulator List
        self.emu_list_layout = QVBoxLayout()
        self.emu_list_layout.setAlignment(Qt.AlignTop)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        emulator_container = QWidget()
        emulator_container.setLayout(self.emu_list_layout)
        scroll_area.setWidget(emulator_container)
        layout.addWidget(scroll_area)

        # Add Custom Emu Button
        add_emu_btn = QPushButton("＋ Add Custom Emulator")
        add_emu_btn.setStyleSheet("background: #1565c0; color: white; padding: 10px; font-weight: bold;")
        add_emu_btn.clicked.connect(self.add_custom_emulator)
        layout.addWidget(add_emu_btn)

        self.populate_emus()

    def open_settings_dialog(self, emu_id):
        try:
            dialog = EmulatorSettingsDialog(self.main_window, emu_id, self)
            if dialog.exec() == QDialog.Accepted:
                try:
                    self.main_window.emulators_tab.refresh_all()
                except Exception as e:
                    logging.exception("Failed to refresh UI after emulator settings save")
                    StyledMessageBox.warning(self, "Refresh Failed", f"Settings were saved, but the UI refresh encountered an error:\n{e}")
        except Exception as e:
            logging.exception("Failed to open emulator settings dialog")
            StyledMessageBox.warning(self, "Open Settings Failed", f"Could not open emulator settings:\n{e}")

    def populate_emus(self):
        for i in reversed(range(self.emu_list_layout.count())):
            item = self.emu_list_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)

        all_emus = emulators.load_emulators()
        for emu_data in all_emus:
            if not isinstance(emu_data, dict):
                continue

            emu_id = emu_data.get("id")
            if emu_id == "windows_native":
                continue
            name = emu_data.get("name") or emu_id or "Unknown Emulator"
            is_user = emu_data.get("user_defined", False)

            row = QWidget()
            row.setStyleSheet("background: #252525; border-radius: 5px; margin: 2px;")
            row_layout = QHBoxLayout(row)

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

            btn_settings = QPushButton("⚙ Settings")
            btn_settings.setStyleSheet(self._row_button_style)
            btn_settings.clicked.connect(lambda checked, eid=emu_id: self.open_settings_dialog(eid))
            row_layout.addWidget(btn_settings)

            if is_user:
                btn_edit = QPushButton("� Edit")
                btn_edit.setStyleSheet(self._row_button_style)
                btn_edit.clicked.connect(lambda checked, eid=emu_id: self.edit_custom_emulator(eid))
                row_layout.addWidget(btn_edit)
                btn_del = QPushButton("🗑 Remove")
                btn_del.setStyleSheet(self._row_button_danger_style)
                btn_del.clicked.connect(lambda checked, eid=emu_id: self.remove_emulator(eid))
                row_layout.addWidget(btn_del)

            self.emu_list_layout.addWidget(row)

    def add_custom_emulator(self):
        dialog = EmulatorEditDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            try:
                all_emus = emulators.load_emulators()
                all_emus.append(dialog.result_data)
                emulators.save_emulators(all_emus)
                self.main_window.log(f"✅ Added emulator: {dialog.result_data['name']}")
                self.populate_emus()
                self.main_window.emulators_tab.refresh_all()
            except Exception as e:
                logging.exception("Failed to add custom emulator")
                StyledMessageBox.warning(self, "Add Emulator Failed", f"Could not add custom emulator:\n{e}")

    def edit_custom_emulator(self, emu_id):
        try:
            all_emus = emulators.load_emulators()
            emu_idx = next((i for i, e in enumerate(all_emus) if e.get("id") == emu_id), -1)
            if emu_idx == -1:
                return
            dialog = EmulatorEditDialog(emu_data=all_emus[emu_idx], parent=self)
            if dialog.exec() == QDialog.Accepted:
                all_emus[emu_idx] = dialog.result_data
                emulators.save_emulators(all_emus)
                self.main_window.log(f"✅ Updated emulator: {dialog.result_data['name']}")
                self.populate_emus()
                self.main_window.emulators_tab.refresh_all()
        except Exception as e:
            logging.exception("Failed to edit custom emulator")
            StyledMessageBox.warning(self, "Update Emulator Failed", f"Could not update custom emulator:\n{e}")

    def remove_emulator(self, emu_id):
        try:
            all_emus = emulators.load_emulators()
            emu = next((e for e in all_emus if e.get("id") == emu_id), None)
            if not emu:
                return
            reply = StyledMessageBox.question(self, "Remove Emulator", f"Are you sure you want to remove {emu['name']}?", StyledMessageBox.Yes | StyledMessageBox.No)
            if reply == StyledMessageBox.Yes:
                all_emus = [e for e in all_emus if e.get("id") != emu_id]
                emulators.save_emulators(all_emus)
                self.main_window.log(f"🗑 Removed emulator: {emu['name']}")
                self.populate_emus()
                self.main_window.emulators_tab.refresh_all()
        except Exception as e:
            logging.exception("Failed to remove custom emulator")
            StyledMessageBox.warning(self, "Remove Emulator Failed", f"Could not remove emulator:\n{e}")

class PlatformAssignWidget(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.config = main_window.config
        self._rebuilding_assignments = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.addWidget(QLabel("<h2>Platform Assignments</h2>"))
        layout.addWidget(QLabel("<p style='color:#888;'>Assign which emulator to use for each platform.</p>"))
        self.assign_layout = QFormLayout()
        container = QWidget()
        container.setLayout(self.assign_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        layout.addWidget(scroll)
        self.populate_assignments()

    def populate_assignments(self):
        self._rebuilding_assignments = True
        for i in reversed(range(self.assign_layout.count())):
            item = self.assign_layout.itemAt(i)
            if item and item.widget(): item.widget().setParent(None)        
        all_games = getattr(self.main_window, "all_games", [])
        platforms = sorted(list(set(g.get("platform_slug") for g in all_games if g.get("platform_slug"))))

        if not platforms:
            platforms = sorted([
                "psx", "ps2", "ps3", "gc", "wii", "wiiu", "n64", "gba",
                "nds", "snes", "nes", "switch", "nintendo-switch",
                "3ds", "n3ds", "psp", "dreamcast", "saturn", "xbox", "xbox360"
            ])

        all_emus = emulators.load_emulators()
        assignments = self.config.get("platform_assignments", {})
        if not isinstance(assignments, dict):
            assignments = {}
        for slug in platforms:
            combo = QComboBox()
            matching_emus = [
                e for e in all_emus
                if isinstance(e, dict) and slug in _normalize_platform_slugs(e.get("platform_slugs"))
            ]
            for emu in matching_emus:
                emu_id = emu.get("id")
                if not emu_id:
                    continue
                emu_name = emu.get("name") or emu_id
                combo.addItem(emu_name, emu_id)
            assigned_id = assignments.get(slug)
            if assigned_id:
                idx = combo.findData(assigned_id)
                if idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)
            combo.currentIndexChanged.connect(partial(self._on_assignment_changed, slug))
            self.assign_layout.addRow(QLabel(f"<b>{slug.upper()}</b>"), combo)
        self._rebuilding_assignments = False

    def _on_assignment_changed(self, platform_slug, index):
        if self._rebuilding_assignments or index < 0:
            return
        combo = self.sender()
        if not isinstance(combo, QComboBox):
            return
        emu_id = combo.itemData(index)
        if emu_id is None:
            return
        self.save_assignment(platform_slug, emu_id)

    def save_assignment(self, platform_slug, emu_id):
        all_emus = emulators.load_emulators()
        emu = next((e for e in all_emus if e.get("id") == emu_id), None)
        assignments = self.config.get("platform_assignments", {})
        if not isinstance(assignments, dict):
            assignments = {}
        assignments[platform_slug] = emu_id
        self.config.set("platform_assignments", assignments)
        self.main_window.log(f"🕹 {platform_slug.upper()} assigned to {emu['name'] if emu else emu_id}")

class EmulatorsTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.sub_tabs = QTabWidget()
        self.sub_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background: #1a1a1a;
            }
            QTabWidget > QTabBar {
                background: #1a1a1a;
                border-bottom: 1px solid #2d2d2d;
            }
            QTabBar::tab {
                background: transparent;
                color: #aaaaaa;
                font-size: 11px;
                padding: 8px 20px;
                border: none;
                border-bottom: 2px solid transparent;
                min-width: 80px;
            }
            QTabBar::tab:selected {
                color: #ffffff;
                border-bottom: 2px solid #0d6efd;
                background: transparent;
            }
            QTabBar::tab:hover {
                color: #dddddd;
                background: rgba(255,255,255,0.04);
            }
            QTabBar::scroller {
                width: 0px;
            }
        """)
        self.emu_list = EmuListWidget(main_window)
        self.platform_assign = PlatformAssignWidget(main_window)
        self.sub_tabs.addTab(self.emu_list, "Emulators")
        self.sub_tabs.addTab(self.platform_assign, "Platforms")
        self.sub_tabs.currentChanged.connect(self._on_tab_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.sub_tabs)

    def _on_tab_changed(self, index):
        widget = self.sub_tabs.widget(index)
        if hasattr(widget, 'populate_assignments'):
            widget.populate_assignments()
        elif hasattr(widget, 'populate_emus'):
            widget.populate_emus()

    def populate_emus(self): self.emu_list.populate_emus()
    def refresh_all(self):
        self.emu_list.populate_emus()
        self.platform_assign.populate_assignments()

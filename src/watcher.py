import time
import psutil
import os
import re
import shutil
import zipfile
import json
import hashlib
import logging
import traceback
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QTimer
from src.utils import calculate_folder_hash, calculate_file_hash, calculate_zip_content_hash, zip_path, extract_strip_root
from src import emulators

class WingosyWatcher(QThread):
    log_signal = Signal(str)
    path_detected_signal = Signal(str, str) # emu_display_name, path
    conflict_signal = Signal(str, str, str, str) # title, local_path, temp_dl, rom_id
    notify_signal = Signal(str, str) # title, msg

    def __init__(self, client, config):
        super().__init__()
        self.client = client
        self.config = config
        self.running = True
        self.active_sessions = {}
        self.session_errors = {} # rom_id -> consecutive error count
        self.skip_next_pull_rom_id = None # Flag to prevent double-pull when launching from app
        
        self.tmp_dir = Path.home() / ".wingosy" / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        
        self.cache_path = Path.home() / ".wingosy" / "sync_cache.json"
        self.sync_cache = {}
        if self.cache_path.exists():
            try:
                with open(self.cache_path, 'r') as f:
                    self.sync_cache = json.load(f)
            except Exception as e:
                logging.error(f"[Watcher] Cache load error: {e}")

    def save_cache(self):
        try:
            with open(self.cache_path, 'w') as f:
                json.dump(self.sync_cache, f)
        except Exception as e:
            logging.error(f"[Watcher] Cache save error: {e}")

    def run(self):
        logging.info("🚀 Watcher Active (Process-Specific Mode).")
        while self.running:
            # Only poll processes we are explicitly tracking
            for pid, data in list(self.active_sessions.items()):
                try:
                    # Check if process is still running
                    if not psutil.pid_exists(pid):
                        try:
                            self.handle_exit(data)
                        except Exception as e:
                            logging.error(f"[Watcher] Error in handle_exit for {data.get('title')}:\n{traceback.format_exc()}")
                        del self.active_sessions[pid]
                    else:
                        # Mid-session sync
                        now = time.time()
                        interval = self.config.get("sync_interval_seconds", 120)
                        if now - data.get("last_sync_time", 0) >= interval:
                            try:
                                self._do_mid_session_sync(data)
                            except Exception as e:
                                logging.error(f"[Watcher] Error in mid-session sync for {data.get('title')}: {e}")
                            data["last_sync_time"] = now
                except Exception as e:
                    logging.error(f"❌ Error monitoring PID {pid}: {e}")
                    del self.active_sessions[pid]
            
            time.sleep(2)

    def _do_mid_session_sync(self, data):
        """Perform a sync upload while the game is still running, if changes detected."""
        # Only sync if enabled for this emulator/Windows
        if data.get('is_windows_native'):
            if not self.config.get("windows_sync_enabled", True): return
        else:
            all_emus = emulators.load_emulators()
            emu = next((e for e in all_emus if e["id"] == data.get("emulator_id")), None)
            if emu and not emu.get("sync_enabled", True): return

        # Check for changes
        save_path = data['save_path']
        if not os.path.exists(save_path): return
        
        if data.get('is_folder'):
            new_h = self._safe_folder_hash(save_path)
        else:
            new_h = calculate_file_hash(str(save_path))
            
        if new_h and new_h != data.get('last_mid_sync_hash'):
            logging.info(f"🔄 Mid-session changes detected for {data['title']}. Syncing...")
            success = self._perform_sync_upload(data)
            if success:
                data['last_mid_sync_hash'] = new_h

    def _perform_sync_upload(self, data):
        """Helper to zip and upload saves. Returns True on success."""
        rom_id = data['rom_id']
        title = data['title']
        is_windows_native = data.get('is_windows_native', False)
        windows_save_dir = data.get('windows_save_dir')
        
        try:
            if is_windows_native and windows_save_dir:
                temp_zip = str(self.tmp_dir / f"sync_{rom_id}.zip")
                try:
                    zip_path(str(windows_save_dir), temp_zip)
                    success, msg = self.client.upload_save(rom_id, "Windows (Native)", temp_zip)
                    return success
                finally:
                    if os.path.exists(temp_zip): os.remove(temp_zip)
            
            save_path = data['save_path']
            is_folder = data.get('is_folder')
            temp_zip = str(self.tmp_dir / f"sync_{rom_id}.zip")
            try:
                if is_folder: zip_path(str(save_path), temp_zip)
                else: 
                    with zipfile.ZipFile(temp_zip, 'w') as zf:
                        zf.write(str(save_path), os.path.basename(save_path))
                
                # Determine slot
                is_retroarch = data.get('emu') == "Multi-Console (RetroArch)"
                save_mode = self.config.get("retroarch_save_mode", "srm")
                slot = "wingosy-state" if (is_retroarch and save_mode == "state") else "wingosy-srm" if is_retroarch else "wingosy-windows"
                
                success, msg = self.client.upload_save(rom_id, data['emu'], temp_zip, slot=slot)
                return success
            finally:
                if os.path.exists(temp_zip): os.remove(temp_zip)
        except Exception:
            return False

    def _hash_retroarch_game(self, srm_path, state_path=None, is_folder=False):
        try:
            if is_folder:
                from src.utils import calculate_folder_hash
                return calculate_folder_hash(str(srm_path))
            
            h = hashlib.md5()
            found = False
            for p in [srm_path, state_path]:
                if p and Path(p).exists():
                    found = True
                    with open(p, 'rb') as f:
                        h.update(f.read())
            return h.hexdigest() if found else None
        except Exception as e:
            logging.error(f"[Watcher] Error hashing RetroArch files: {e}")
            return None

    def _get_folder_mtime(self, path):
        try:
            if not path or not os.path.exists(path):
                return 0
            if os.path.isfile(path):
                return os.path.getmtime(path)
            newest = 0
            for root, dirs, files in os.walk(path):
                for f in files:
                    try:
                        t = os.path.getmtime(os.path.join(root, f))
                        if t > newest:
                            newest = t
                    except Exception:
                        pass
            return newest
        except Exception:
            return 0

    def _safe_folder_hash(self, folder_path, retries=3, delay=3):
        from src.utils import calculate_folder_hash
        for i in range(retries):
            try:
                if not os.path.exists(folder_path):
                    return None
                return calculate_folder_hash(str(folder_path))
            except (PermissionError, OSError) as e:
                if i < retries - 1:
                    time.sleep(delay)
                else:
                    logging.error(f"[Watcher] Could not hash folder after {retries} attempts: {e}")
                    return None
            except Exception as e:
                logging.error(f"[Watcher] Unexpected error in _safe_folder_hash: {e}")
                return None

    def track_session(self, proc, emu_display_name, game_data, local_rom_path, emu_path, skip_pull=False, windows_save_dir=None):
        try:
            pid = proc.pid
            full_cmd = f"\"{emu_path}\" \"{local_rom_path}\""
            rom_id = game_data['id']
            title = game_data['name']
            platform = game_data.get('platform_slug')
            
            all_emus = emulators.load_emulators()
            this_emu = next((e for e in all_emus if e["name"] == emu_display_name or e["id"] == emu_display_name), None)
            emu_id = this_emu["id"] if this_emu else None

            if windows_save_dir:
                h = self._safe_folder_hash(windows_save_dir) if os.path.exists(windows_save_dir) else None
                init_mtime = self._get_folder_mtime(windows_save_dir) if os.path.exists(windows_save_dir) else time.time()
                
                session_data = {
                    'emu': "Windows (Native)",
                    'rom_id': rom_id,
                    'save_path': windows_save_dir,
                    'title': title,
                    'initial_hash': h,
                    'initial_mtime': init_mtime,
                    'is_folder': True,
                    'start_time': time.time(),
                    'last_sync_time': time.time(),
                    'windows_save_dir': windows_save_dir,
                    'is_windows_native': True,
                    'emulator_id': 'windows_native'
                }
                self.active_sessions[pid] = session_data
                self.log_signal.emit(f"🎮 Tracking Windows Game: {title} (PID: {pid})")
                return

            res = None
            try:
                res = self.resolve_save_path(emu_display_name, title, full_cmd, emu_path, platform, proc=psutil.Process(pid))
            except Exception as e:
                logging.error(f"[Watcher] resolve_save_path failed for {title}:\n{traceback.format_exc()}")
            
            if res:
                save_path, is_folder = res
                save_path = str(Path(save_path).resolve())
                
                should_pull = (self.config.get("auto_pull_saves", True) and not skip_pull)
                if self.skip_next_pull_rom_id == str(rom_id):
                    should_pull = False
                    self.skip_next_pull_rom_id = None

                is_retroarch_game = ("RetroArch" in emu_display_name or platform == "multi" or platform in ["nes", "snes", "n64", "gb", "gbc", "gba", "genesis", "mastersystem", "segacd", "gamegear", "atari2600", "psx", "psp"])
                
                save_info = None
                if emu_display_name == "Multi-Console (RetroArch)":
                    ra_emu_data = {"path": emu_path}
                    try:
                        save_info = self.get_retroarch_save_path(game_data, ra_emu_data)
                    except Exception as e:
                        logging.error(f"[Watcher] get_retroarch_save_path failed for {title}: {e}")

                    if save_info is None:
                        self.log_signal.emit(f"⚠️ Could not resolve RetroArch paths for {title}")
                        return
                    
                    save_path   = save_info.get('srm') or ""
                    state_path  = save_info.get('state') or ""
                    is_folder   = False
                    if should_pull:
                        self.pull_server_save(rom_id, title, save_info, is_folder, emu_id=emu_id)
                else:
                    if should_pull:
                        self.pull_server_save(rom_id, title, save_path, is_folder, emu_id=emu_id)
                
                game_item = next((g for g in self.client.user_games if g['name'] == title), None)
                if not game_item: game_item = {"id": rom_id, "name": title, "platform_slug": platform, "fs_name": Path(local_rom_path).name}
                
                both_mode = False
                state_save_path = None
                psp_folder = None
                initial_state_mtime = 0
                
                if is_retroarch_game:
                    try:
                        ra_res = self.get_retroarch_save_path(game_item, {"path": emu_path})
                        if isinstance(ra_res, dict):
                            save_path = str(Path(ra_res['srm']).resolve())
                            state_save_path = str(Path(ra_res['state']).resolve()) if ra_res.get('state') else None
                            both_mode = (self.config.get("retroarch_save_mode") == "both")
                            is_folder = ra_res.get('is_folder', False)
                            psp_folder = ra_res.get('psp_folder')
                    except Exception as e:
                        logging.error(f"[Watcher] Secondary path resolution failed for {title}: {e}")
                
                is_gc_card = (is_folder == "gc_card")
                gc_card_dir = save_path if is_gc_card else None
                
                if is_gc_card:
                    h = None
                    init_mtime = time.time()
                elif psp_folder:
                    h = self._safe_folder_hash(psp_folder) if os.path.exists(psp_folder) else None
                    init_mtime = self._get_folder_mtime(psp_folder) if os.path.exists(psp_folder) else time.time()
                    initial_state_mtime = os.path.getmtime(state_save_path) if state_save_path and os.path.exists(state_save_path) else 0
                elif is_retroarch_game:
                    h = self._hash_retroarch_game(save_path, state_save_path, is_folder)
                    init_mtime = max(self._get_folder_mtime(save_path), self._get_folder_mtime(state_save_path))
                else:
                    h = (calculate_folder_hash(save_path) if is_folder 
                          else calculate_file_hash(save_path) 
                          if os.path.exists(save_path) else None)
                    init_mtime = self._get_folder_mtime(save_path)

                srm_p = save_path if str(save_path).endswith('.srm') else (state_save_path if state_save_path and str(state_save_path).endswith('.srm') else None)
                state_p = state_save_path if state_save_path and '.state' in str(state_save_path) else (save_path if '.state' in str(save_path) else None)

                session_data = {
                    'emu': emu_display_name, 
                    'rom_id': rom_id, 
                    'save_path': save_path,
                    'title': title,
                    'initial_hash': h,
                    'initial_mtime': init_mtime,
                    'is_folder': is_folder,
                    'start_time': time.time(),
                    'last_sync_time': time.time(),
                    'emu_path': emu_path,
                    'gc_card_dir': gc_card_dir,
                    'both_mode': both_mode,
                    'state_save_path': state_save_path,
                    'srm_path': srm_p,
                    'state_path': state_p,
                    'psp_folder': psp_folder,
                    'initial_state_mtime': initial_state_mtime,
                    'emulator_id': emu_id
                }
                self.active_sessions[pid] = session_data
                self.log_signal.emit(f"🎮 Tracking {title} on {emu_display_name} (PID: {pid})")
            else:
                self.log_signal.emit(f"⚠️ Identified {title} but could not resolve local save path.")
                
        except Exception as e:
            logging.error(f"❌ Error setting up tracking: {e}\n{traceback.format_exc()}")

    def resolve_save_path(self, emu_display_name, title, full_cmd, emu_path, platform=None, proc=None):
        try:
            emu_dir = Path(emu_path).parent
            all_emus = emulators.load_emulators()
            this_emu = next((e for e in all_emus if e["name"] == emu_display_name or e["id"] == emu_display_name), None)
            
            if (this_emu and this_emu["id"] == "eden") or "Switch" in emu_display_name or platform == "switch":
                import sqlite3
                title_id = None
                rom_path = None
                m_path = re.search(r'("[^"]+\.(?:xci|nsp|nsz)")', full_cmd)
                if not m_path: m_path = re.search(r'(\S+\.(?:xci|nsp|nsz))', full_cmd)
                if m_path: rom_path = Path(m_path.group(1).strip('"'))
                search_roots = [emu_dir / "user", emu_dir / "data", Path(os.path.expandvars(r'%APPDATA%\eden')), Path(os.path.expandvars(r'%APPDATA%\yuzu')), Path(os.path.expandvars(r'%APPDATA%\sudachi')), Path(os.path.expandvars(r'%APPDATA%\torzu')), Path(os.path.expandvars(r'%LOCALAPPDATA%\yuzu'))]
                emu_lower = emu_display_name.lower()
                prioritized = []
                for root in search_roots:
                    if any(k in root.as_posix().lower() for k in ["eden", "yuzu", "sudachi", "torzu"]) and any(k in emu_lower for k in ["eden", "yuzu", "sudachi", "torzu"]):
                        for k in ["eden", "yuzu", "sudachi", "torzu"]:
                            if k in root.as_posix().lower() and k in emu_lower: prioritized.append(root); break
                search_roots = prioritized + [r for r in search_roots if r not in prioritized]
                if not title_id:
                    for root in search_roots:
                        db_path = root / "cache/game_list/game_list.db"
                        if db_path.exists():
                            try:
                                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                                cursor = conn.cursor()
                                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                                tables = [row[0] for row in cursor.fetchall()]
                                for table in tables:
                                    cursor.execute(f"PRAGMA table_info({table})")
                                    cols = [c[1].lower() for c in cursor.fetchall()]
                                    id_col, name_col = next((c for c in cols if c in ['title_id', 'program_id', 'id'] or ('id' in c and 'title' in c)), None), next((c for c in cols if c in ['name', 'title', 'game_name'] or 'name' in c or 'title' in c), None)
                                    if id_col and name_col:
                                        cursor.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} LIKE ? LIMIT 1", (f"%{title}%",))
                                        row = cursor.fetchone()
                                        if row:
                                            val = row[0]
                                            title_id = hex(val)[2:].upper().zfill(16) if isinstance(val, int) else str(val).upper().replace('0X', '')
                                            if re.match(r'^[0-9A-F]{16}$', title_id): break
                                            else: title_id = None
                                conn.close()
                                if title_id: break
                            except Exception: pass
                if not title_id and rom_path and rom_path.exists() and rom_path.suffix.lower() == ".xci":
                    try:
                        with open(rom_path, "rb") as f:
                            f.seek(0x108)
                            title_id = f.read(8)[::-1].hex().upper()
                            if not re.match(r'^01[0-9A-F]{14}$', title_id): title_id = None
                    except Exception: pass
                if not title_id:
                    recent_tid, max_mtime = None, 0
                    for root in search_roots:
                        save_base = root / "nand/user/save/0000000000000000"
                        if not save_base.exists(): continue
                        for profile_dir in save_base.iterdir():
                            if not profile_dir.is_dir(): continue
                            for tid_dir in profile_dir.iterdir():
                                if tid_dir.is_dir() and re.match(r'^01[0-9A-F]{14}$', tid_dir.name):
                                    if tid_dir.stat().st_mtime > max_mtime: max_mtime, recent_tid = tid_dir.stat().st_mtime, tid_dir.name
                    if recent_tid: title_id = recent_tid
                if title_id:
                    for root in search_roots:
                        save_base = root / "nand/user/save/0000000000000000"
                        if not save_base.exists(): continue
                        for profile_dir in save_base.iterdir():
                            if not profile_dir.is_dir(): continue
                            candidate = profile_dir / title_id
                            if candidate.exists(): return str(candidate), True
                        profiles = [d for d in save_base.iterdir() if d.is_dir()]
                        if profiles: return str(profiles[0] / title_id), True
                return None
            elif (this_emu and this_emu["id"] == "dolphin") or "Dolphin" in emu_display_name or platform in ["gc", "ngc", "wii", "gamecube", "nintendo-gamecube", "nintendo-wii", "wii-u-vc"]:
                emu_dir = Path(emu_path).parent
                portable_gc, documents_gc = emu_dir / "User" / "GC", Path.home() / "Documents" / "Dolphin Emulator" / "GC"
                gc_base = portable_gc if portable_gc.exists() else documents_gc
                rom_upper = full_cmd.upper()
                region = "EUR" if any(r in rom_upper for r in ["EUR", "PAL", "EUROPE"]) else "JAP" if any(r in rom_upper for r in ["JAP", "JPN", "JAPAN"]) else "USA"
                card_dir = gc_base / region / "Card A"
                return str(card_dir), "gc_card"
            elif (this_emu and this_emu["id"] == "pcsx2") or "PlayStation 2" in emu_display_name or platform == "ps2":
                search_paths = [emu_dir / "memcards" / "Mcd001.ps2", Path(os.path.expandvars(r'%APPDATA%\PCSX2\memcards\Mcd001.ps2')), Path.home() / "Documents" / "PCSX2" / "memcards" / "Mcd001.ps2"]
                for p in search_paths:
                    if p.exists(): return str(p), False
                return str(search_paths[0]), False
            elif (this_emu and this_emu["id"] == "rpcs3") or "PlayStation 3" in emu_display_name or platform == "ps3":
                save_base = emu_dir / "dev_hdd0/home/00000001/savedata"
                if not save_base.exists(): save_base = Path(os.path.expandvars(r'%APPDATA%\RPCS3\dev_hdd0\home\00000001\savedata'))
                if save_base.exists():
                    tid_match = re.search(r'([A-Z]{4}\d{5})', full_cmd)
                    if tid_match: return str(save_base / tid_match.group(1).upper()), True
                    subdirs = sorted([d for d in save_base.iterdir() if d.is_dir() and (d / "PARAM.SFO").exists()], key=lambda x: x.stat().st_mtime, reverse=True)
                    if subdirs: return str(subdirs[0]), True
                return str(save_base), True
            elif (this_emu and this_emu["id"] == "cemu") or "Cemu" in emu_display_name or platform == "wiiu":
                mlc_path = emu_dir / "mlc01"
                if not mlc_path.exists():
                    settings_xml = emu_dir / "settings.xml"
                    if settings_xml.exists():
                        try:
                            import xml.etree.ElementTree as ET
                            mlc_node = ET.parse(settings_xml).getroot().find('.//mlc_path')
                            if mlc_node is not None and mlc_node.text: mlc_path = Path(mlc_node.text)
                        except Exception: pass
                if not mlc_path or not mlc_path.exists(): mlc_path = Path(os.path.expandvars(r'%APPDATA%\Cemu\mlc01'))
                save_base = mlc_path / "usr/save/00050000"
                if save_base.exists():
                    title_dirs = sorted([d for d in save_base.iterdir() if d.is_dir()], key=lambda x: x.stat().st_mtime, reverse=True)
                    for title_dir in title_dirs:
                        candidate = title_dir / "user" / "80000001"
                        if candidate.exists() and any(candidate.iterdir()): return str(candidate), True
                    for title_dir in title_dirs:
                        if (title_dir / "user").exists(): return str(title_dir / "user" / "80000001"), True
                return None
            elif (this_emu and this_emu["id"] == "azahar") or any(x in emu_display_name for x in ["Citra", "Azahar", "3DS"]) or platform in ["3ds", "n3ds"]:
                citra_base = Path(os.path.expandvars(r'%APPDATA%\Citra\sdmc\Nintendo 3DS'))
                if citra_base.exists():
                    best, max_mt = None, 0
                    for id1 in citra_base.iterdir():
                        for id2 in id1.iterdir():
                            title_base = id2 / "title/00040000"
                            if not title_base.exists(): continue
                            for tid in title_base.iterdir():
                                candidate = tid / "data/00000001"
                                if candidate.exists() and candidate.stat().st_mtime > max_mt: max_mt, best = candidate.stat().st_mtime, candidate
                    if best: return str(best), True
                return str(Path(os.path.expandvars(r'%APPDATA%\Citra\sdmc'))), True
            elif (this_emu and this_emu["id"] == "retroarch") or "RetroArch" in emu_display_name or platform == "multi" or platform in ["nes", "snes", "n64", "gb", "gbc", "gba", "genesis", "mastersystem", "segacd", "gamegear", "atari2600", "psx", "psp"]:
                game_item = next((g for g in self.client.user_games if g['name'] == title), None)
                if not game_item: game_item = {"id": title, "name": title, "platform_slug": platform, "fs_name": title + ".rom"}
                res = self.get_retroarch_save_path(game_item, {"path": emu_path})
                if res['is_folder']: return res['srm'], True
                save_mode = self.config.get("retroarch_save_mode", "srm")
                path = res['state'] if save_mode == "state" else res['srm']
                return path, False
        except Exception as e: logging.error(f"⚠️ Error resolving save path: {e}")
        return None

    def get_retroarch_save_path(self, game, emu_data):
        try:
            from src.platforms import RETROARCH_CORES, RETROARCH_CORE_SAVE_FOLDERS
            ra_exe = emu_data.get("path") or emu_data.get("executable_path", "")
            if not ra_exe: return None
            ra_dir, platform_slug = Path(ra_exe).parent, game.get("platform_slug", "")
            if platform_slug in ("psp", "playstation-portable"):
                psp_saves = ra_dir / "saves" / "PPSSPP" / "PSP" / "SAVEDATA"
                rom_name = game.get("fs_name", game.get("name", ""))
                base_name, state_path = Path(rom_name).stem, ra_dir / "states" / "PPSSPP" / f"{Path(rom_name).stem}.state.auto"
                return {"srm": str(psp_saves), "state": str(state_path), "is_folder": True, "psp_folder": str(psp_saves)}
            core_dll = RETROARCH_CORES.get(platform_slug, "")
            if not core_dll: return None
            core_name = (core_dll.replace(".dll", "").replace(".so", "").replace("_libretro", ""))
            save_folder_name = RETROARCH_CORE_SAVE_FOLDERS.get(core_name, core_name)
            rom_name = game.get("fs_name", game.get("name", ""))
            base_name = Path(rom_name).stem
            srm_path, state_path = ra_dir / "saves" / save_folder_name / f"{base_name}.srm", ra_dir / "states" / save_folder_name / f"{base_name}.state.auto"
            return {"srm": str(srm_path), "state": str(state_path), "is_folder": False, "psp_folder": None}
        except Exception as e: logging.error(f"[Watcher] get_retroarch_save_path error: {e}"); return None

    def handle_exit(self, data):
        rom_id = data.get('rom_id')
        title = data.get('title')
        is_windows_native = data.get('is_windows_native', False)
        
        if is_windows_native:
            if not self.config.get("windows_sync_enabled", True):
                logging.info(f"[Watcher] Sync disabled for Windows Native, skipping upload for {title}")
                return
        else:
            all_emus = emulators.load_emulators()
            emu = next((e for e in all_emus if e["id"] == data.get("emulator_id")), None)
            if emu and not emu.get("sync_enabled", True):
                logging.info(f"[Watcher] Sync disabled for {emu['name']}, skipping upload for {title}")
                return

        if rom_id and self.session_errors.get(str(rom_id), 0) >= 5:
            logging.warning(f"[Watcher] Giving up on save sync for {title} after 5 consecutive errors")
            return

        try:
            self.log_signal.emit(f"🛑 Session Ended: {title}")
            gc_card_dir = data.get('gc_card_dir')
            if gc_card_dir:
                try:
                    session_start, card_path = data.get('initial_mtime', 0), Path(gc_card_dir)
                    changed_gcis = []
                    if card_path.exists():
                        for gci in card_path.glob("*.gci"):
                            try:
                                if gci.stat().st_mtime > session_start: changed_gcis.append(gci)
                            except Exception: pass
                    if changed_gcis:
                        self.log_signal.emit(f"📝 {len(changed_gcis)} GCI file(s) changed. Syncing...")
                        temp_zip = str(self.tmp_dir / f"sync_{data['rom_id']}.zip")
                        try:
                            with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
                                for gci in changed_gcis: zf.write(str(gci), gci.name)
                            success, msg = self.client.upload_save(data['rom_id'], data['emu'], temp_zip)
                            if success: self.log_signal.emit("✨ Sync Complete!"); self.sync_cache.pop(str(data['rom_id']), None); self.save_cache(); self.session_errors[str(rom_id)] = 0
                            else: self.log_signal.emit(f"❌ Sync Failed: {msg}"); self.session_errors[str(rom_id)] = self.session_errors.get(str(rom_id), 0) + 1
                        finally:
                            if os.path.exists(temp_zip): os.remove(temp_zip)
                    else: self.log_signal.emit(f"⏭️ No changes in {data['title']}. Skipping sync.")
                except Exception as e: logging.error(f"[Watcher] Dolphin GC sync failed: {e}"); self.session_errors[str(rom_id)] = self.session_errors.get(str(rom_id), 0) + 1
                self._update_playtime(data); return

            save_path, is_retroarch = Path(data['save_path']), data.get('emu') == "Multi-Console (RetroArch)"
            if not save_path.exists() and not is_retroarch: self.log_signal.emit(f"⚠️ Save file missing on exit: {save_path}. Skipping sync."); return
            
            try:
                if is_retroarch and data.get('is_folder'): h_at_exit = self._safe_folder_hash(data['save_path'])
                elif is_retroarch: h_at_exit = self._hash_retroarch_game(str(save_path), data['is_folder'])
                elif is_windows_native: h_at_exit = self._safe_folder_hash(data['save_path'])
                else: h_at_exit = calculate_folder_hash(str(save_path)) if data['is_folder'] else calculate_file_hash(str(save_path))
            except Exception as e: logging.error(f"[Watcher] Failed to capture hash at exit for {title}: {e}"); h_at_exit = None
            
            time.sleep(3)
            
            try:
                if is_retroarch and data.get('is_folder'): new_h = self._safe_folder_hash(data['save_path'])
                elif is_retroarch: new_h = self._hash_retroarch_game(str(save_path), data['is_folder'])
                elif is_windows_native: new_h = self._safe_folder_hash(data['save_path'])
                else: new_h = calculate_folder_hash(str(save_path)) if data['is_folder'] else calculate_file_hash(str(save_path))
            except Exception as e: logging.error(f"[Watcher] Failed to capture new hash for {title}: {e}"); new_h = None
            
            initial_h, post_mtime, initial_mtime = data.get('initial_hash'), self._get_folder_mtime(str(save_path)), data.get('initial_mtime', 0)
            mtime_changed, has_hash_changed = post_mtime > initial_mtime, (new_h != initial_h or (h_at_exit is not None and new_h != h_at_exit))
            if initial_h is None and new_h is not None: has_hash_changed = True
            
            psp_folder, state_p, state_mtime_changed = data.get('psp_folder'), data.get('state_path'), False
            if psp_folder and state_p and os.path.exists(state_p): state_mtime_changed = (os.path.getmtime(state_p) > data.get('initial_state_mtime', 0))
            
            if not has_hash_changed and not mtime_changed and not state_mtime_changed: self.log_signal.emit(f"⏭️ No changes in {data['title']}. Skipping sync."); return
            
            self.log_signal.emit(f"📝 Changes detected! Syncing...")
            success_overall = self._perform_sync_upload(data)
            if success_overall: self.session_errors[str(rom_id)] = 0
            else: self.session_errors[str(rom_id)] = self.session_errors.get(str(rom_id), 0) + 1
            self._update_playtime(data)
        except Exception as e: logging.error(f"❌ Error during sync: {e}\n{traceback.format_exc()}"); self.session_errors[str(rom_id)] = self.session_errors.get(str(rom_id), 0) + 1

    def pull_server_save(self, rom_id, title, save_info_or_path, is_folder, force=False, emu_id=None):
        behavior = "ask"
        if emu_id == "windows_native":
            behavior = self.config.get("windows_conflict_behavior", "ask")
        else:
            all_emus = emulators.load_emulators()
            emu = next((e for e in all_emus if e["id"] == emu_id), None)
            if emu:
                behavior = emu.get("conflict_behavior", "ask")

        if behavior == "prefer_local" and not force:
            logging.info(f"[Watcher] Conflict behavior is prefer_local, skipping pull for {title}")
            return

        is_ra_dict = isinstance(save_info_or_path, dict)
        srm_path, state_path = (save_info_or_path.get('srm'), save_info_or_path.get('state')) if is_ra_dict else (save_info_or_path, None)
        
        self.log_signal.emit(f"☁️ Checking cloud for {title}...")
        try:
            latest_save = self.client.get_latest_save(rom_id)
            if latest_save:
                self._apply_cloud_file(rom_id, title, latest_save, srm_path, is_folder, force, file_type="save", behavior=behavior)
        except Exception as e:
            logging.error(f"[Watcher] pull_server_save failed for {title} save: {e}")
            
        if state_path:
            try:
                latest_state = self.client.get_latest_state(rom_id)
                if latest_state:
                    self._apply_cloud_file(rom_id, title, latest_state, state_path, False, force, file_type="state", behavior=behavior)
            except Exception as e:
                logging.error(f"[Watcher] pull_server_save failed for {title} state: {e}")

    def _apply_cloud_file(self, rom_id, title, cloud_obj, local_path, is_folder, force, file_type="save", behavior="ask"):
        try:
            server_updated_at = cloud_obj.get('updated_at', '')
            cached_val = self.sync_cache.get(str(rom_id), {})
            cached_updated_at = cached_val.get(f'{file_type}_updated_at', '') if isinstance(cached_val, dict) else (cached_val if file_type == 'save' else '')
            
            local_exists = os.path.exists(local_path)
            if not force and cached_updated_at == server_updated_at and local_exists:
                self.log_signal.emit(f"☁️ {'SAVE' if file_type=='save' else 'STATE'} already up to date.")
                return
            
            if not force and local_exists:
                if behavior == "ask":
                    # Let the conflict_signal be emitted later or handled by UI
                    pass
                elif behavior == "prefer_cloud":
                    # Silent pull
                    pass
                elif behavior == "prefer_local":
                    return

            temp_dl = str(self.tmp_dir / f"cloud_check_{rom_id}_{file_type}")
            success = self.client.download_state(cloud_obj, temp_dl) if file_type == "state" else self.client.download_save(cloud_obj, temp_dl)
            
            if success:
                orig_filename = cloud_obj.get('file_name', '') or cloud_obj.get('name', '')
                filename = self._clean_romm_filename(orig_filename)
                is_raw_retroarch = filename.lower().endswith(('.srm', '.state'))
                is_zip = zipfile.is_zipfile(temp_dl) if not is_raw_retroarch else False
                
                self.log_signal.emit(f"📥 Cloud {file_type} is different. Updating...")
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                if os.path.exists(local_path):
                    bak_path = str(local_path) + ".bak"
                    try:
                        if is_folder: shutil.copytree(local_path, bak_path, dirs_exist_ok=True)
                        else: shutil.copy2(local_path, bak_path)
                    except Exception: pass
                
                try:
                    if is_zip:
                        extract_strip_root(temp_dl, local_path)
                    elif is_raw_retroarch:
                        dest = Path(local_path)
                        if dest.is_dir(): dest = dest / filename
                        shutil.copy2(temp_dl, str(dest))
                        if (dest.suffix == '.state' and not dest.name.endswith('.state.auto')):
                            auto_path = dest.with_name(dest.name + '.auto')
                            if auto_path.exists():
                                if auto_path.is_dir(): shutil.rmtree(auto_path)
                                else: auto_path.unlink()
                            dest.rename(auto_path)
                    else:
                        dest = Path(local_path)
                        if dest.is_dir(): dest = dest / filename; shutil.copy2(temp_dl, str(dest))
                        else:
                            if os.path.isdir(local_path): shutil.rmtree(local_path, ignore_errors=True)
                            shutil.copy2(temp_dl, local_path)
                            
                    current_entry = self.sync_cache.get(str(rom_id))
                    if not isinstance(current_entry, dict): current_entry = {}
                    current_entry[f'{file_type}_updated_at'] = server_updated_at
                    self.sync_cache[str(rom_id)] = current_entry; self.save_cache(); self.log_signal.emit(f"✨ Cloud {file_type} applied!")
                except Exception as e:
                    self.log_signal.emit(f"❌ Failed to apply {file_type}: {e}")
                if os.path.exists(temp_dl): os.remove(temp_dl)
        except Exception as e:
            logging.error(f"[Watcher] Error in _apply_cloud_file for {title}: {e}")

    def _clean_romm_filename(self, filename: str) -> str:
        return filename.split('/')[-1].split('\\')[-1]

    def _update_playtime(self, data):
        """Update session and total playtime."""
        try:
            session_minutes = (time.time() - data['start_time']) / 60
            playtime_path = Path.home() / ".wingosy" / "playtime.json"
            playtime_data = {}
            if playtime_path.exists():
                try:
                    with open(playtime_path, 'r') as f: playtime_data = json.load(f)
                except: pass
            rid_str = str(data['rom_id'])
            new_total = playtime_data.get(rid_str, 0) + session_minutes
            playtime_data[rid_str] = new_total
            with open(playtime_path, 'w') as f: json.dump(playtime_data, f)
            self.log_signal.emit(f"🕐 Session: {session_minutes:.1f} min | Total: {new_total:.1f} min")
        except Exception as e: logging.error(f"[Watcher] Playtime error: {e}")

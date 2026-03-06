import time
import psutil
import os
import re
import shutil
import zipfile
import json
import hashlib
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from src.utils import calculate_folder_hash, calculate_file_hash, calculate_zip_content_hash, zip_path
from src.platforms import RETROARCH_PLATFORMS, platform_matches

class WingosyWatcher(QThread):
    log_signal = Signal(str)
    path_detected_signal = Signal(str, str) # emu_display_name, path
    conflict_signal = Signal(str, str, str, str) # title, local_path, temp_dl, rom_id
    notify_signal = Signal(str, str) # title, msg

    def __init__(self, client, config_manager):
        super().__init__()
        self.client = client
        self.config = config_manager
        self.running = True
        self.active_sessions = {}
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
                print(f"[Watcher] Cache load error: {e}")

    def save_cache(self):
        try:
            with open(self.cache_path, 'w') as f:
                json.dump(self.sync_cache, f)
        except Exception as e:
            print(f"[Watcher] Cache save error: {e}")

    def run(self):
        self.log_signal.emit("🚀 Watcher Active (Process-Specific Mode).")
        while self.running:
            # Only poll processes we are explicitly tracking
            for pid, data in list(self.active_sessions.items()):
                try:
                    # Check if process is still running
                    if not psutil.pid_exists(pid):
                        self.handle_exit(data)
                        del self.active_sessions[pid]
                    else:
                        # Optional: periodically verify it's still the SAME process (unlikely to collide in short term)
                        pass
                except Exception as e:
                    self.log_signal.emit(f"❌ Error monitoring PID {pid}: {e}")
                    del self.active_sessions[pid]
            
            time.sleep(2)

    def _hash_retroarch_game(self, save_path, is_folder=False):
        """
        Hash the SRM + all state files for a RetroArch game.
        For folder-based cores (PSP), hashes the entire folder tree.
        """
        if is_folder:
            from src.utils import calculate_folder_hash
            try:
                return calculate_folder_hash(str(save_path))
            except Exception:
                return None

        srm = Path(save_path)
        core_dir = srm.parent
        stem = srm.stem  # e.g. "Super Punch-Out!! (USA)"
        
        # Collect: the .srm + any .stateN / .state.auto files
        files = []
        if srm.exists():
            files.append(srm)
        for f in sorted(core_dir.glob(f"{stem}.state*")):
            files.append(f)
        
        if not files:
            return None
        
        # Hash all files together
        h = hashlib.sha256()
        for f in files:
            try:
                h.update(f.read_bytes())
            except Exception:
                pass
        return h.hexdigest()

    def _get_folder_mtime(self, path):
        """Return the newest mtime of any file in a folder tree."""
        if not os.path.exists(path):
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

    def track_session(self, proc, emu_display_name, game_data, local_rom_path, emu_path):
        """
        Explicitly track a process launched by the UI.
        """
        try:
            pid = proc.pid
            full_cmd = f"\"{emu_path}\" \"{local_rom_path}\""
            rom_id = game_data['id']
            title = game_data['name']
            platform = game_data.get('platform_slug')

            # 1. Resolve Save Path
            res = self.resolve_save_path(emu_display_name, title, full_cmd, emu_path, platform, proc=psutil.Process(pid))
            
            if res:
                save_path, is_folder = res
                save_path = str(Path(save_path).resolve())
                
                # Double-Pull Protection
                should_pull = self.config.get("auto_pull_saves", True)
                if self.skip_next_pull_rom_id == str(rom_id):
                    should_pull = False
                    self.skip_next_pull_rom_id = None

                # Special marker for Dolphin GC card sync
                is_gc_card = (is_folder == "gc_card")
                gc_card_dir = save_path if is_gc_card else None
                
                if is_gc_card:
                    is_folder = False # We upload a zip of files later

                # Tracking both mode for RetroArch
                is_retroarch_pull = (emu_display_name == "Multi-Console (RetroArch)")
                self._pull_is_retroarch = is_retroarch_pull

                if should_pull:
                    self.pull_server_save(rom_id, title, save_path, is_folder)
                
                self._pull_is_retroarch = False

                # Resolve paths again if both mode might have returned multiple
                game_item = next((g for g in self.client.user_games if g['name'] == title), None)
                if not game_item: game_item = {"id": rom_id, "name": title, "platform_slug": platform, "fs_name": Path(local_rom_path).name}
                
                both_mode = False
                state_save_path = None
                
                if is_retroarch_pull:
                    ra_res = self.get_retroarch_save_path(game_item, {"path": emu_path})
                    if len(ra_res) == 3:
                        srm_p, state_p, is_f = ra_res
                        save_path = str(Path(srm_p).resolve())
                        state_save_path = str(Path(state_p).resolve())
                        both_mode = True
                
                # Track the session with initial state
                if is_gc_card:
                    h = None
                    init_mtime = time.time() # Key timestamp for GCI detection
                elif is_retroarch_pull:
                    h = self._hash_retroarch_game(save_path, is_folder)
                    init_mtime = self._get_folder_mtime(save_path)
                else:
                    h = (calculate_folder_hash(save_path) if is_folder 
                          else calculate_file_hash(save_path) 
                          if os.path.exists(save_path) else None)
                    init_mtime = self._get_folder_mtime(save_path)

                self.active_sessions[pid] = {
                    'emu': emu_display_name, 
                    'rom_id': rom_id, 
                    'save_path': save_path,
                    'title': title,
                    'initial_hash': h,
                    'initial_mtime': init_mtime,
                    'is_folder': is_folder,
                    'start_time': time.time(),
                    'emu_path': emu_path,
                    'gc_card_dir': gc_card_dir,
                    'both_mode': both_mode,
                    'state_save_path': state_save_path
                }
                self.log_signal.emit(f"🎮 Tracking {title} on {emu_display_name} (PID: {pid})")
            else:
                self.log_signal.emit(f"⚠️ Identified {title} but could not resolve local save path.")
                
        except Exception as e:
            self.log_signal.emit(f"❌ Error setting up tracking: {e}")

    def pull_server_save(self, rom_id, title, local_path, is_folder, force=False):
        self.log_signal.emit(f"☁️ Checking cloud for {title}...")
        
        save_mode = self.config.get("retroarch_save_mode", "srm")
        is_retroarch_pull = getattr(self, '_pull_is_retroarch', False)
        
        if is_retroarch_pull and save_mode in ("srm", "state", "both"):
            slot = "wingosy-state" if save_mode == "state" else "wingosy-srm"
            latest_save = self.client.get_save_by_slot(rom_id, slot)
            if latest_save is None:
                latest_save = self.client.get_latest_save(rom_id)
        else:
            latest_save = self.client.get_latest_save(rom_id)

        if not latest_save: 
            self.log_signal.emit("☁️ No cloud saves found.")
            return

        server_updated_at = latest_save.get('updated_at', '')
        
        # Determine cached updated_at
        rid_str = str(rom_id)
        cached_val = self.sync_cache.get(rid_str)
        cached_updated_at = cached_val.get('updated_at', '') if isinstance(cached_val, dict) else cached_val
        
        # Only skip if timestamp matches AND the local file actually exists
        if not force and cached_updated_at == server_updated_at and os.path.exists(local_path):
            self.log_signal.emit("☁️ Cloud save already up to date.")
            return

        temp_dl = str(self.tmp_dir / f"cloud_check_{rom_id}")
        if self.client.download_save(latest_save, temp_dl):
            is_zip = zipfile.is_zipfile(temp_dl)
                
            if not force and os.path.exists(local_path) and rid_str in self.sync_cache:
                # Save Conflict Detected
                self.log_signal.emit(f"⚠️ Save conflict detected for {title}!")
                self.conflict_signal.emit(title, local_path, temp_dl, rid_str)
                return # Stop here, wait for UI resolution

            self.log_signal.emit(f"📥 Cloud save is different. Updating...")
            
            # Ensure parent dir exists
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            # Backup
            if os.path.exists(local_path):
                bak_path = str(local_path) + ".bak"
                if os.path.exists(bak_path):
                    if os.path.isdir(bak_path):
                        shutil.rmtree(bak_path, ignore_errors=True)
                    else:
                        try: os.remove(bak_path)
                        except Exception: pass
                
                try:
                    if is_folder:
                        shutil.copytree(local_path, bak_path)
                    else:
                        shutil.copy2(local_path, bak_path)
                except Exception as e:
                    self.log_signal.emit(f"⚠️ Backup failed: {e}")

            # Apply
            try:
                if is_zip:
                    if is_folder:
                        extract_parent = str(Path(local_path).parent)
                        folder_name = Path(local_path).name
                        if os.path.exists(local_path):
                            shutil.rmtree(local_path, ignore_errors=True)
                        os.makedirs(extract_parent, exist_ok=True)
                        with zipfile.ZipFile(temp_dl, 'r') as z:
                            names = z.namelist()
                            has_root = any(n.startswith(folder_name + '/') or 
                                          n.startswith(folder_name + '\\') for n in names)
                            if has_root:
                                z.extractall(extract_parent)
                            else:
                                os.makedirs(local_path, exist_ok=True)
                                z.extractall(local_path)
                    else:
                        # Special Case: GC bundle (zipped GCIs going into Card A directory)
                        with zipfile.ZipFile(temp_dl, 'r') as z:
                            names = z.namelist()
                            is_gc_bundle = any(n.endswith('.gci') for n in names)
                            
                            if is_gc_bundle and os.path.isdir(local_path):
                                self.log_signal.emit(f"📥 Extracting GC bundle into {Path(local_path).name}...")
                                z.extractall(local_path)
                            else:
                                # Standard single-file extract
                                target_member = None
                                for name in names:
                                    if name.endswith(('.ps2', '.srm', '.sav', '.dat', '.sv', '.raw', '.gci')):
                                        target_member = name
                                        break
                                
                                if target_member:
                                    with z.open(target_member) as source, open(local_path, 'wb') as target:
                                        shutil.copyfileobj(source, target)
                                elif names:
                                    with z.open(names[0]) as source, open(local_path, 'wb') as target:
                                        shutil.copyfileobj(source, target)
                else:
                    if os.path.isdir(local_path):
                        shutil.rmtree(local_path, ignore_errors=True)
                    shutil.copy2(temp_dl, local_path)
                
                self.sync_cache[rid_str] = server_updated_at
                self.save_cache()
                self.log_signal.emit("✨ Cloud save applied!")
                self.notify_signal.emit(title, "☁️ Cloud save applied")
            except Exception as e:
                self.log_signal.emit(f"❌ Failed to apply save: {e}")
            
            if os.path.exists(temp_dl):
                os.remove(temp_dl)

    def resolve_save_path(self, emu_display_name, title, full_cmd, emu_path, platform=None, proc=None):
        try:
            emu_dir = Path(emu_path).parent
            
            # 1. NINTENDO SWITCH
            if "Switch" in emu_display_name or platform == "switch":
                import sqlite3
                title_id = None
                rom_path = None
                m_path = re.search(r'("[^"]+\.(?:xci|nsp|nsz)")', full_cmd)
                if not m_path: m_path = re.search(r'(\S+\.(?:xci|nsp|nsz))', full_cmd)
                if m_path:
                    rom_path = Path(m_path.group(1).strip('"'))

                search_roots = [
                    emu_dir / "user", emu_dir / "data", 
                    Path(os.path.expandvars(r'%APPDATA%\eden')), 
                    Path(os.path.expandvars(r'%APPDATA%\yuzu')), 
                    Path(os.path.expandvars(r'%APPDATA%\sudachi')),
                    Path(os.path.expandvars(r'%APPDATA%\torzu')),
                    Path(os.path.expandvars(r'%LOCALAPPDATA%\yuzu'))
                ]

                emu_lower = emu_display_name.lower()
                prioritized = []
                for root in search_roots:
                    if any(k in root.as_posix().lower() for k in ["eden", "yuzu", "sudachi", "torzu"]) and \
                       any(k in emu_lower for k in ["eden", "yuzu", "sudachi", "torzu"]):
                        for k in ["eden", "yuzu", "sudachi", "torzu"]:
                            if k in root.as_posix().lower() and k in emu_lower:
                                prioritized.append(root); break
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
                                    id_col = next((c for c in cols if c in ['title_id', 'program_id', 'id'] or ('id' in c and 'title' in c)), None)
                                    name_col = next((c for c in cols if c in ['name', 'title', 'game_name'] or 'name' in c or 'title' in c), None)
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
                                    if tid_dir.stat().st_mtime > max_mtime:
                                        max_mtime = tid_dir.stat().st_mtime
                                        recent_tid = tid_dir.name
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

            # 2. GAMECUBE / WII / NGC (DOLPHIN)
            elif "Dolphin" in emu_display_name or platform in [
                    "gc", "ngc", "wii", "gamecube", "nintendo-gamecube",
                    "nintendo-wii", "wii-u-vc"]:

                emu_dir = Path(emu_path).parent

                # Detect portable vs appdata mode
                portable_gc = emu_dir / "User" / "GC"
                documents_gc = (Path.home() / "Documents" 
                                / "Dolphin Emulator" / "GC")
                gc_base = (portable_gc if portable_gc.exists() 
                           else documents_gc)

                # Detect region from ROM name
                rom_upper = full_cmd.upper()
                if any(r in rom_upper for r in ["EUR", "PAL", "EUROPE"]):
                    region = "EUR"
                elif any(r in rom_upper for r in ["JAP", "JPN", "JAPAN"]):
                    region = "JAP"
                else:
                    region = "USA"

                card_dir = gc_base / region / "Card A"
                print(f"[Dolphin] Card dir: {card_dir} (exists={card_dir.exists()})")
                
                # Use special marker for mtime-based detection
                return str(card_dir), "gc_card"

            # 3. PLAYSTATION 2
            elif "PlayStation 2" in emu_display_name or platform == "ps2":
                search_paths = [emu_dir / "memcards" / "Mcd001.ps2", Path(os.path.expandvars(r'%APPDATA%\PCSX2\memcards\Mcd001.ps2')), Path.home() / "Documents" / "PCSX2" / "memcards" / "Mcd001.ps2"]
                for p in search_paths:
                    if p.exists(): return str(p), False
                return str(search_paths[0]), False

            # 3.5 PLAYSTATION 3 (RPCS3)
            elif "PlayStation 3" in emu_display_name or platform == "ps3":
                save_base = emu_dir / "dev_hdd0/home/00000001/savedata"
                if not save_base.exists():
                    save_base = Path(os.path.expandvars(r'%APPDATA%\RPCS3\dev_hdd0\home\00000001\savedata'))
                
                if save_base.exists():
                    tid_match = re.search(r'([A-Z]{4}\d{5})', full_cmd)
                    if tid_match: return str(save_base / tid_match.group(1).upper()), True
                    subdirs = sorted([d for d in save_base.iterdir() if d.is_dir() and (d / "PARAM.SFO").exists()], key=lambda x: x.stat().st_mtime, reverse=True)
                    if subdirs: return str(subdirs[0]), True
                return str(save_base), True

            # 5. WII U (CEMU)
            elif "Cemu" in emu_display_name or platform == "wiiu":
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

            # 6. NINTENDO 3DS (CITRA)
            elif any(x in emu_display_name for x in ["Citra", "Azahar", "3DS"]) or platform in ["3ds", "n3ds"]:
                citra_base = Path(os.path.expandvars(r'%APPDATA%\Citra\sdmc\Nintendo 3DS'))
                if citra_base.exists():
                    best, max_mt = None, 0
                    for id1 in citra_base.iterdir():
                        for id2 in id1.iterdir():
                            title_base = id2 / "title/00040000"
                            if not title_base.exists(): continue
                            for tid in title_base.iterdir():
                                candidate = tid / "data/00000001"
                                if candidate.exists() and candidate.stat().st_mtime > max_mt:
                                    max_mt, best = candidate.stat().st_mtime, candidate
                    if best: return str(best), True
                return str(Path(os.path.expandvars(r'%APPDATA%\Citra\sdmc'))), True

            # 4. RETROARCH
            elif "RetroArch" in emu_display_name or platform == "multi" or platform in RETROARCH_PLATFORMS:
                game_item = next((g for g in self.client.user_games if g['name'] == title), None)
                if not game_item: game_item = {"id": title, "name": title, "platform_slug": platform, "fs_name": title + ".rom"}
                
                res = self.get_retroarch_save_path(game_item, {"path": emu_path})
                if len(res) == 3:
                    path, state_p, is_f = res
                else:
                    path, is_f = res
                
                if path: return str(path), is_f
        except Exception as e:
            self.log_signal.emit(f"⚠️ Error resolving save path: {e}")
        return None

    def get_retroarch_save_path(self, game, emu_data):
        """
        Returns (path_str, is_folder) for the RetroArch save to sync.
        Respects the retroarch_save_mode config setting:
          srm   → .srm file in saves/<CoreFolder>/
          state → .state.auto file in states/<CoreFolder>/
          both  → returns the path that was modified most recently
        PSP is always SAVEDATA folder regardless of mode.
        """
        from src.platforms import RETROARCH_CORES, RETROARCH_CORE_SAVE_FOLDERS
        ra_exe = emu_data.get("path", "")
        if not ra_exe:
            return None, False

        ra_dir = Path(ra_exe).parent
        platform_slug = game.get("platform_slug", "")

        # PSP: always folder-based SAVEDATA, never affected by save mode
        if platform_slug in ("psp", "playstation-portable"):
            psp_saves = ra_dir / "saves" / "PPSSPP" / "PSP" / "SAVEDATA"
            return str(psp_saves), True

        core_dll = RETROARCH_CORES.get(platform_slug, "")
        if not core_dll:
            return None, False

        core_name = (core_dll.replace(".dll", "").replace(".so", "")
                              .replace("_libretro", ""))
        save_folder_name = RETROARCH_CORE_SAVE_FOLDERS.get(core_name, core_name)

        rom_name = game.get("fs_name", game.get("name", ""))
        base_name = Path(rom_name).stem

        srm_path = ra_dir / "saves" / save_folder_name / f"{base_name}.srm"
        state_path = ra_dir / "states" / save_folder_name / f"{base_name}.state.auto"

        save_mode = self.config.get("retroarch_save_mode", "srm")

        if save_mode == "srm":
            return str(srm_path), False
        elif save_mode == "state":
            return str(state_path), False
        elif save_mode == "both":
            # Return 3-tuple: (srm_path, state_path, is_folder=False)
            return str(srm_path), str(state_path), False

        return str(srm_path), False

    def handle_exit(self, data):
        try:
            self.log_signal.emit(f"🛑 Session Ended: {data['title']}")
            
            # If mode is "both", also push the secondary save file
            save_mode = self.config.get("retroarch_save_mode", "srm")
            emu_display_name = data.get('emu')
            if save_mode == "both" and emu_display_name and "RetroArch" in emu_display_name:
                # The primary was already pushed above; now push the secondary if it exists
                # secondary = the one NOT chosen as primary in get_retroarch_save_path
                # We just log — RomM only stores one save slot per game, so we push
                # whichever was newer (already done). No double-push needed.
                pass  # Already handled by get_retroarch_save_path choosing the newer file

            # Special case for Dolphin GC card sync (mtime-based GCI detection)
            gc_card_dir = data.get('gc_card_dir')
            if gc_card_dir:
                session_start = data.get('initial_mtime', 0)
                card_path = Path(gc_card_dir)
                
                # Find .gci files modified AFTER session started
                changed_gcis = []
                if card_path.exists():
                    for gci in card_path.glob("*.gci"):
                        try:
                            if gci.stat().st_mtime > session_start:
                                changed_gcis.append(gci)
                        except Exception:
                            pass
                
                print(f"[Dolphin] Session start: {session_start}")
                print(f"[Dolphin] Changed GCIs: {[f.name for f in changed_gcis]}")
                
                if changed_gcis:
                    self.log_signal.emit(f"📝 {len(changed_gcis)} GCI file(s) changed. Syncing...")
                    temp_zip = str(self.tmp_dir / f"sync_{data['rom_id']}.zip")
                    try:
                        with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
                            for gci in changed_gcis:
                                zf.write(str(gci), gci.name)
                        success, msg = self.client.upload_save(data['rom_id'], data['emu'], temp_zip)
                        if success:
                            self.log_signal.emit("✨ Sync Complete!")
                            if str(data['rom_id']) in self.sync_cache:
                                del self.sync_cache[str(data['rom_id'])]
                                self.save_cache()
                        else:
                            self.log_signal.emit(f"❌ Sync Failed: {msg}")
                    finally:
                        if os.path.exists(temp_zip):
                            try: os.remove(temp_zip)
                            except: pass
                else:
                    self.log_signal.emit(f"⏭️ No changes in {data['title']}. Skipping sync.")
                
                self._update_playtime(data)
                return

            save_path = Path(data['save_path'])
            is_retroarch = data.get('emu') == "Multi-Console (RetroArch)"
            both_mode = data.get('both_mode', False)
            state_save_path = data.get('state_save_path')
            
            if not save_path.exists() and not is_retroarch:
                self.log_signal.emit(f"⚠️ Save file missing on exit: {save_path}. Skipping sync.")
                return
            
            # Capture hash IMMEDIATELY at process exit
            if is_retroarch:
                h_at_exit = self._hash_retroarch_game(str(save_path), data['is_folder'])
            else:
                h_at_exit = calculate_folder_hash(str(save_path)) if data['is_folder'] else calculate_file_hash(str(save_path))
            
            # Give emulator a moment to finish writing buffered files to disk
            time.sleep(3)
            
            if is_retroarch:
                new_h = self._hash_retroarch_game(str(save_path), data['is_folder'])
            else:
                new_h = calculate_folder_hash(str(save_path)) if data['is_folder'] else calculate_file_hash(str(save_path))
            
            initial_h = data.get('initial_hash')
            
            post_mtime = self._get_folder_mtime(str(save_path))
            initial_mtime = data.get('initial_mtime', 0)
            mtime_changed = post_mtime > initial_mtime
            
            # Change detected if final hash differs from initial (during play) 
            # OR final hash differs from at_exit (post-exit flush)
            has_hash_changed = (new_h != initial_h or (h_at_exit is not None and new_h != h_at_exit))

            print(f"[DEBUG] {data['title']} exit: is_folder={data['is_folder']} save_path={save_path} exists={os.path.exists(save_path)} mtime_changed={mtime_changed} hash_changed={has_hash_changed}")

            if not has_hash_changed and not mtime_changed:
                self.log_signal.emit(f"⏭️ No changes in {data['title']}. Skipping sync.")
                return

            self.log_signal.emit(f"📝 Changes detected! Syncing...")
            
            if both_mode and is_retroarch and state_save_path:
                # === BOTH MODE: upload SRM and STATE as separate slots ===
                rom_id = data['rom_id']
                emu = data['emu']
                srm_p = Path(data['save_path'])
                state_p = Path(state_save_path)

                # Upload SRM slot
                if srm_p.exists():
                    temp_srm = str(self.tmp_dir / f"sync_{rom_id}_srm.zip")
                    try:
                        with zipfile.ZipFile(temp_srm, 'w', zipfile.ZIP_DEFLATED) as zf:
                            zf.write(srm_p, srm_p.name)
                        ok, msg = self.client.upload_save(rom_id, emu, temp_srm, slot="wingosy-srm")
                        if ok:
                            self.log_signal.emit("✨ SRM slot synced!")
                            self.sync_cache.pop(f"{rom_id}:srm", None)
                        else: self.log_signal.emit(f"❌ SRM sync failed: {msg}")
                    finally:
                        if os.path.exists(temp_srm):
                            try: os.remove(temp_srm)
                            except: pass

                # Upload State slot
                if state_p.exists():
                    temp_state = str(self.tmp_dir / f"sync_{rom_id}_state.zip")
                    try:
                        with zipfile.ZipFile(temp_state, 'w', zipfile.ZIP_DEFLATED) as zf:
                            zf.write(state_p, state_p.name)
                            # Also include any numbered states alongside .auto
                            for f in state_p.parent.glob(f"{state_p.stem.replace('.auto','')}*"):
                                if f != state_p: zf.write(f, f.name)
                        ok, msg = self.client.upload_save(rom_id, emu, temp_state, slot="wingosy-state")
                        if ok:
                            self.log_signal.emit("✨ State slot synced!")
                            self.sync_cache.pop(f"{rom_id}:state", None)
                        else: self.log_signal.emit(f"❌ State sync failed: {msg}")
                    finally:
                        if os.path.exists(temp_state):
                            try: os.remove(temp_state)
                            except: pass
            else:
                # === SINGLE MODE (srm or state): original single-slot upload ===
                save_mode = self.config.get("retroarch_save_mode", "srm")
                slot = "wingosy-state" if (is_retroarch and save_mode == "state") else "wingosy-srm" if is_retroarch else "wingosy-windows"

                temp_zip = str(self.tmp_dir / f"sync_{data['rom_id']}.zip")
                try:
                    # For RetroArch, we need to zip ALL matching files (SRM + States) or the whole folder
                    if is_retroarch:
                        if data['is_folder']:
                            zip_path(str(save_path), temp_zip)
                        else:
                            save_p = Path(data['save_path'])
                            with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
                                if save_p.exists(): zf.write(save_p, save_p.name)
                                for f in save_p.parent.glob(f"{save_p.stem}.state*"):
                                    zf.write(f, f.name)
                    else:
                        zip_path(str(save_path), temp_zip)
                    
                    success, msg = self.client.upload_save(data['rom_id'], data['emu'], temp_zip, slot=slot)
                    if success:
                        self.log_signal.emit("✨ Sync Complete!")
                        self.sync_cache.pop(str(data['rom_id']), None)
                        self.save_cache()
                    else: self.log_signal.emit(f"❌ Sync Failed: {msg}")
                finally:
                    if os.path.exists(temp_zip):
                        try: os.remove(temp_zip)
                        except: pass
            
            # Playtime tracking
            self._update_playtime(data)
        except Exception as e: self.log_signal.emit(f"❌ Error during sync: {e}")

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
        except Exception as e: print(f"[Watcher] Playtime error: {e}")

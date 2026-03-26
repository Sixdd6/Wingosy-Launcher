import os
import time
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
import logging
from src.app_paths import primary_app_dir, preferred_existing_app_dir

try:
    import keyring
except ImportError:
    keyring = None

PRIMARY_KEYRING_SERVICE = "rommate"
LEGACY_KEYRING_SERVICES = ("wingosy",)

def _get_certifi_path():
    """Get certifi CA bundle path, handling PyInstaller."""
    # Check env var first (set by main.py before imports)
    env_path = os.environ.get('REQUESTS_CA_BUNDLE')
    if env_path and os.path.exists(env_path):
        return env_path
    try:
        import certifi
        path = certifi.where()
        os.environ['REQUESTS_CA_BUNDLE'] = path
        os.environ['SSL_CERT_FILE'] = path
        return path
    except Exception:
        return True  # Let requests find it automatically

CERTIFI_PATH = _get_certifi_path()
REQUEST_TIMEOUT = (10, 30) # (connect, read)

class RomMClient:
    def __init__(self, host, config=None):
        self.host = host.rstrip('/')
        self.config = config
        self.token = self._load_token()
        self.user_games = []
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        self.app_dir = primary_app_dir()
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_app_dir = preferred_existing_app_dir()
        self.library_cache_path = self.app_dir / "library_cache.json"
        self.metadata_dir_path = self.app_dir / "metadata"
        self._legacy_metadata_dir_path = (
            self.legacy_app_dir / "metadata"
            if self.legacy_app_dir != self.app_dir
            else None
        )

    def _load_token(self):
        """Retrieve token via config manager (keyring with encrypted fallback)."""
        if self.config:
            return self.config.load_token()
        
        # Fallback for when config is not available (rare)
        if keyring:
            try:
                services = (PRIMARY_KEYRING_SERVICE, *LEGACY_KEYRING_SERVICES)
                for service in services:
                    token = keyring.get_password(service, "auth_token")
                    if token:
                        return token
            except Exception as e:
                logging.warning(f"Keyring retrieval error: {e}")
        return None

    def logout(self):
        """Clear the auth token from memory and secure storage."""
        self.token = None
        if self.config:
            self.config.delete_token()
        elif keyring:
            try:
                services = (PRIMARY_KEYRING_SERVICE, *LEGACY_KEYRING_SERVICES)
                for service in services:
                    try:
                        keyring.delete_password(service, "auth_token")
                    except Exception:
                        pass
                logging.info("Logged out: removed token from keyring")
            except Exception as e:
                logging.warning(f"Failed to remove token from keyring: {e}")

    def save_library_cache(self, games):
        """Save fetched library to disk for instant startup next time."""
        try:
            self.library_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.library_cache_path, 'w', encoding='utf-8') as f:
                json.dump(games, f)
        except Exception as e:
            print(f"[Cache] Save error: {e}")

    def load_library_cache(self):
        """Load cached library. Returns (games, age_seconds) or (None, 0)."""
        try:
            candidates = [self.library_cache_path]
            if self.legacy_app_dir != self.app_dir:
                candidates.append(self.legacy_app_dir / "library_cache.json")

            games = None
            for candidate in candidates:
                if not candidate.exists():
                    continue
                with open(candidate, 'r', encoding='utf-8') as f:
                    games = json.load(f)
                break
            if games is None:
                return None, 0
            # We no longer track age in the simplified list format, return 0
            return games, 0
        except Exception:
            return None, 0

    def test_connection(self, host_override=None, retry_callback=None):
        host = (host_override or self.host).rstrip('/')
        try:
            # Try heartbeat first, then roms list as a connectivity test
            for endpoint in ["/api/heartbeat", "/api/roms?limit=1&offset=0"]:
                try:
                    # Stage 1: Fast attempt
                    try:
                        r = requests.get(f"{host}{endpoint}", 
                                         headers=self.get_auth_headers(),
                                         timeout=REQUEST_TIMEOUT, 
                                         verify=CERTIFI_PATH)
                    except requests.exceptions.Timeout:
                        # Stage 2: Slow attempt for cold starts
                        if retry_callback:
                            retry_callback()
                        r = requests.get(f"{host}{endpoint}", 
                                         headers=self.get_auth_headers(),
                                         timeout=(300, 300), 
                                         verify=CERTIFI_PATH)

                    if r.status_code == 200:
                        return True, "Connected successfully."
                    if r.status_code in [401, 403]:
                        return False, "Connected but authentication failed. Check credentials."
                except (requests.exceptions.ConnectTimeout,
                        requests.exceptions.ConnectionError):
                    return False, "Could not reach host. Check URL and port."
                except requests.exceptions.ReadTimeout:
                    return False, "Server took too long to respond. It might be overloaded."
                except Exception:
                    continue
            return False, "Could not reach RomM API. Check your URL."
        except Exception as e:
            return False, str(e)

    def login(self, username, password):
        try:
            url = f"{self.host}/api/token"
            if self.host.startswith("http://"):
                print("[API] Warning: Credentials being sent over unencrypted HTTP connection.")
            
            data = {
                "grant_type": "password",
                "username": username,
                "password": password
            }
            try:
                # Login usually shouldn't be cold-started but we'll use standard timeout
                r = requests.post(url, data=data, headers=self.headers, 
                                  timeout=REQUEST_TIMEOUT, verify=CERTIFI_PATH)
            except requests.exceptions.Timeout:
                # If login hangs, retry once with longer timeout
                r = requests.post(url, data=data, headers=self.headers, 
                                  timeout=(60, 60), verify=CERTIFI_PATH)
            except (requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.RequestException) as e:
                print(f"[API] Network error in login: {e}")
                return False, f"Could not reach server: {e}"

            if r.status_code == 200:
                self.token = r.json()["access_token"]
                
                # Save via config manager (keyring with encrypted fallback)
                if self.config:
                    self.config.save_token(self.token)
                elif keyring:
                    try:
                        keyring.set_password(PRIMARY_KEYRING_SERVICE, "auth_token", self.token)
                    except Exception as e:
                        logging.warning(f"Failed to save token to keyring: {e}")
                
                return True, self.token
            return False, r.json().get("detail", "Login failed")
        except Exception as e:
            return False, str(e)

    def get_auth_headers(self):
        h = self.headers.copy()
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _normalize_upload_emulator(self, emulator):
        """
        Normalize emulator IDs for upload endpoints where some RomM deployments
        still validate against legacy Switch IDs.
        """
        emu = str(emulator or "").strip().lower()
        if emu in ("eden", "suyu", "switch"):
            return "yuzu"
        return emulator

    def fetch_library(self, retry_callback=None, page_callback=None):
        """
        Fetch all games from RomM in parallel for speed.
        Emits pages progressively via page_callback if provided.
        """
        import concurrent.futures
        url = f"{self.host}/api/roms"
        limit = 100 
        all_items = []
        
        # Use a session for connection pooling
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        def _fetch_page(offset, retry=True):
            params = {"limit": limit, "offset": offset}
            try:
                try:
                    # Stage 1: Fast attempt
                    r = session.get(url, headers=self.get_auth_headers(),
                                    params=params, timeout=REQUEST_TIMEOUT, verify=CERTIFI_PATH)
                except requests.exceptions.Timeout:
                    # Stage 2: Slow attempt
                    if retry_callback:
                        retry_callback()
                    r = session.get(url, headers=self.get_auth_headers(),
                                    params=params, timeout=(300, 300), verify=CERTIFI_PATH)
                
                if r.status_code == 401:
                    return "REAUTH_REQUIRED"
                if r.status_code != 200:
                    return None
                
                data = r.json()
                items = (data.get("items", []) if isinstance(data, dict)
                         else data if isinstance(data, list) else [])
                total = (data.get("total") or data.get("count") or 0
                         if isinstance(data, dict) else len(items))
                return {"items": items, "total": total}
            except Exception as e:
                if retry:
                    print(f"[API] Retry page offset {offset} due to error: {e}")
                    return _fetch_page(offset, retry=False)
                print(f"[API] Network error at offset {offset}: {e}")
                return None

        first_page = _fetch_page(0)
        if first_page is None:
            return None
        if first_page == "REAUTH_REQUIRED":
            return "REAUTH_REQUIRED"
        
        items = first_page["items"]
        total = first_page["total"]
        all_items.extend(items)
        if page_callback:
            page_callback(items, total)

        if total > limit:
            remaining_offsets = list(range(limit, total, limit))
            print(f"[Library] Parallel fetch started for {len(remaining_offsets)} remaining pages...")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                future_to_offset = {executor.submit(_fetch_page, offset): offset for offset in remaining_offsets}
                for future in concurrent.futures.as_completed(future_to_offset):
                    page_res = future.result()
                    if page_res and isinstance(page_res, dict):
                        page_items = page_res["items"]
                        all_items.extend(page_items)
                        if page_callback:
                            page_callback(page_items, total)

        # Aggregate and cache
        self.user_games = all_items
        self.save_library_cache(all_items)
        
        # We no longer save cached_library to config.json explicitly here
        # self.config.set("cached_library", all_items) is removed to avoid UI stutter
        
        print(f"[Library] Parallel fetch complete: {len(all_items)} games.")
        return all_items

    def get_rom_details(self, rom_id):
        """Fetch detailed information for a single ROM."""
        url = f"{self.host}/api/roms/{rom_id}"
        try:
            r = requests.get(url, headers=self.get_auth_headers(), 
                             timeout=REQUEST_TIMEOUT, verify=CERTIFI_PATH)
            if r.status_code == 200:
                rom_data = r.json()
                try:
                    local_meta = self._read_local_wingosy_metadata(rom_id)

                    if local_meta is not None and isinstance(rom_data, dict):
                        rom_data["rommate_metadata"] = local_meta
                        rom_data["wingosy_metadata"] = local_meta

                        local_playtime = local_meta.get("playtimeSeconds")
                        if local_playtime is not None:
                            try:
                                local_playtime_int = max(0, int(local_playtime))
                            except Exception:
                                local_playtime_int = None
                            if local_playtime_int is not None:
                                rom_data["playtimeSeconds"] = local_playtime_int

                        local_last_played = local_meta.get("lastPlayed")
                        if isinstance(local_last_played, str) and local_last_played.strip():
                            rom_data["lastPlayed"] = local_last_played.strip()
                except Exception:
                    pass
                logging.debug(f"ROM detail raw for {rom_id}: {json.dumps(rom_data, indent=2)}")
                return rom_data
            return None
        except Exception as e:
            print(f"[API] Error fetching ROM details for {rom_id}: {e}")
            return None

    def _extract_note_text(self, note_obj):
        if not isinstance(note_obj, dict):
            return None
        for key in ("note", "content", "text", "body", "message"):
            value = note_obj.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _extract_note_id(self, note_obj):
        if not isinstance(note_obj, dict):
            return None
        for key in ("id", "note_id"):
            value = note_obj.get(key)
            if value not in (None, ""):
                return value
        return None

    def _parse_wingosy_metadata_note(self, note_text):
        if not isinstance(note_text, str) or not note_text.strip():
            return None
        try:
            payload = json.loads(note_text)
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None

        meta = payload.get("rommate_metadata") if isinstance(payload.get("rommate_metadata"), dict) else payload.get("wingosy_metadata")
        if not isinstance(meta, dict):
            return None

        playtime_value = meta.get("playtimeSeconds", 0)
        try:
            playtime_seconds = max(0, int(playtime_value or 0))
        except Exception:
            playtime_seconds = 0

        last_played = meta.get("lastPlayed")
        if not isinstance(last_played, str):
            last_played = ""

        return {
            "playtimeSeconds": playtime_seconds,
            "lastPlayed": last_played,
        }

    def _build_wingosy_metadata_note(self, playtime_seconds, last_played_iso):
        try:
            playtime_total = max(0, int(playtime_seconds or 0))
        except Exception:
            playtime_total = 0

        metadata = {
            "playtimeSeconds": playtime_total,
            "lastPlayed": str(last_played_iso or ""),
        }

        return {
            "rommate_metadata": metadata,
            "wingosy_metadata": metadata,
        }

    def _metadata_file_path(self, rom_id):
        rid = str(rom_id or "").strip()
        if not rid:
            return None
        return self.metadata_dir_path / f"{rid}.json"

    def _read_local_wingosy_metadata(self, rom_id):
        file_path = self._metadata_file_path(rom_id)
        candidates = []
        if file_path is not None:
            candidates.append(file_path)
        if self._legacy_metadata_dir_path is not None and file_path is not None:
            candidates.append(self._legacy_metadata_dir_path / file_path.name)

        payload = None
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                break
            except Exception:
                continue

        if payload is None:
            return None

        if not isinstance(payload, dict):
            return None

        if isinstance(payload.get("rommate_metadata"), dict):
            meta = payload.get("rommate_metadata")
        elif isinstance(payload.get("wingosy_metadata"), dict):
            meta = payload.get("wingosy_metadata")
        else:
            meta = payload
        if not isinstance(meta, dict):
            return None

        playtime_value = meta.get("playtimeSeconds", 0)
        try:
            playtime_seconds = max(0, int(playtime_value or 0))
        except Exception:
            playtime_seconds = 0

        last_played = meta.get("lastPlayed")
        if not isinstance(last_played, str):
            last_played = ""

        return {
            "playtimeSeconds": playtime_seconds,
            "lastPlayed": last_played,
        }

    def _write_local_wingosy_metadata(self, rom_id, playtime_seconds, last_played_iso):
        file_path = self._metadata_file_path(rom_id)
        if file_path is None:
            return False

        payload = self._build_wingosy_metadata_note(playtime_seconds, last_played_iso)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
            return True
        except Exception as e:
            logging.warning(f"[API] Failed writing local metadata for rom_id={rom_id}: {e}")
            return False

    def list_notes(self, rom_id):
        try:
            r = requests.get(
                f"{self.host}/api/roms/{rom_id}/notes",
                headers=self.get_auth_headers(),
                timeout=REQUEST_TIMEOUT,
                verify=CERTIFI_PATH,
            )
            if r.status_code != 200:
                return []

            payload = r.json()
            if isinstance(payload, list):
                return payload

            if isinstance(payload, dict):
                notes = payload.get("notes")
                if isinstance(notes, list):
                    return notes
                if any(k in payload for k in ("note", "content", "text", "body", "message")):
                    return [payload]
            return []
        except Exception:
            return []

    def _upsert_wingosy_metadata_note(self, rom_id, playtime_seconds, last_played_iso):
        rid = str(rom_id)
        note_payload_obj = self._build_wingosy_metadata_note(playtime_seconds, last_played_iso)
        note_text = json.dumps(note_payload_obj, separators=(",", ":"))
        headers = self.get_auth_headers()

        existing_note = None
        for note_obj in self.list_notes(rid):
            parsed = self._parse_wingosy_metadata_note(self._extract_note_text(note_obj))
            if parsed is not None:
                existing_note = note_obj
                break

        payload = {
            "title": "Rom Mate Metadata",
            "content": note_text,
            "is_public": False,
            "tags": ["rommate", "metadata"],
        }

        note_id = self._extract_note_id(existing_note)
        if note_id is not None:
            note_url = f"{self.host}/api/roms/{rid}/notes/{note_id}"
            try:
                r = requests.put(
                    note_url,
                    headers=headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                    verify=CERTIFI_PATH,
                )
                if r.status_code in (200, 201, 204):
                    return True
                logging.warning(f"[API] Notes update failed ({r.status_code}) for rom_id={rid}: {r.text[:200]}")
                return False
            except Exception:
                return False

        create_url = f"{self.host}/api/roms/{rid}/notes"
        try:
            r = requests.post(
                create_url,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                verify=CERTIFI_PATH,
            )
            if r.status_code in (200, 201, 204):
                return True
            logging.warning(f"[API] Notes create failed ({r.status_code}) for rom_id={rid}: {r.text[:200]}")
            return False
        except Exception:
            return False

    def update_playtime(self, rom_id, seconds, total_playtime_seconds=None, last_played_iso=None):
        try:
            secs = int(seconds)
        except Exception:
            return False
        if secs <= 0:
            return False

        rid = str(rom_id)

        try:
            if last_played_iso:
                parsed_last_played = str(last_played_iso)
            else:
                parsed_last_played = datetime.now(timezone.utc).isoformat()
        except Exception:
            parsed_last_played = datetime.now(timezone.utc).isoformat()

        if total_playtime_seconds is None:
            total_playtime = secs
            try:
                local_meta = self._read_local_wingosy_metadata(rid)
                if isinstance(local_meta, dict):
                    total_playtime = max(0, int(local_meta.get("playtimeSeconds") or 0)) + secs
            except Exception:
                pass
        else:
            try:
                total_playtime = max(0, int(total_playtime_seconds))
            except Exception:
                total_playtime = secs

        return self._write_local_wingosy_metadata(rid, total_playtime, parsed_last_played)

    def get_cover_url(self, game):
        path = game.get('path_cover_large') or game.get('path_cover_small') 
        if path:
            return path if path.startswith('http') else f"{self.host}{path}"
        url = game.get('url_cover')
        if url:
            if url.startswith('//'):
                return f"https:{url}"
            return url
        return None

    def download_rom(self, rom_id, file_name, target_path, progress_cb=None, thread=None):
        try:
            encoded_name = quote(file_name)
            url = f"{self.host}/api/roms/{rom_id}/content/{encoded_name}"
            
            try:
                r = requests.get(url, headers=self.get_auth_headers(), stream=True, 
                                 timeout=REQUEST_TIMEOUT, verify=CERTIFI_PATH)
            except (requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.RequestException) as e:
                print(f"[API] Network error in download_rom: {e}")
                return False

            if r.status_code != 200:
                return False

            total = int(r.headers.get('content-length', 0))
            downloaded = 0
            start = time.time()
            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(1024*1024):
                    if thread and thread.isInterruptionRequested():
                        f.close()
                        os.remove(target_path)
                        return False
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total > 0:
                            elapsed = time.time() - start
                            speed = downloaded / elapsed if elapsed > 0 else 0
                            progress_cb(downloaded, total, speed)
            return True
        except Exception as e:
            print(f"[API] ROM download error: {e}")
            return False

    def get_latest_save(self, rom_id):
        items = self.list_all_saves(rom_id)
        if not items: return None
        return sorted(items, key=self._item_updated_key, reverse=True)[0]

    def _normalize_collection_items(self, payload, preferred_keys=None):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []

        keys = list(preferred_keys or []) + ["items", "results", "data", "saves", "states"]
        seen = set()
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return []

    def _extract_paginated_items(self, payload, preferred_keys=None):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []

        keys = list(preferred_keys or []) + ["items", "results", "data"]
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return []

    def _item_updated_key(self, item):
        if not isinstance(item, dict):
            return ""
        return (
            item.get("updated_at")
            or item.get("modified_at")
            or item.get("created_at")
            or item.get("date")
            or item.get("timestamp")
            or ""
        )

    def list_all_saves(self, rom_id):
        try:
            r = requests.get(
                f"{self.host}/api/saves",
                params={"rom_id": rom_id},
                headers=self.get_auth_headers(),
                timeout=REQUEST_TIMEOUT,
                verify=CERTIFI_PATH
            )
            if r.status_code != 200: return []
            return self._normalize_collection_items(r.json(), preferred_keys=["saves"])
        except Exception as e:
            print(f"[API] list_all_saves error: {e}")
            return []

    def delete_save(self, save_id):
        try:
            # RomM OpenAPI: POST /api/saves/delete with {"saves": [id]}
            r = requests.post(
                f"{self.host}/api/saves/delete",
                headers=self.get_auth_headers(),
                json={"saves": [int(save_id)]},
                timeout=REQUEST_TIMEOUT,
                verify=CERTIFI_PATH,
            )
            return r.status_code in [200, 204]
        except Exception as e:
            print(f"[API] delete_save error: {e}")
            return False

    def get_latest_state(self, rom_id):
        items = self.list_all_states(rom_id)
        if not items: return None
        return sorted(items, key=self._item_updated_key, reverse=True)[0]

    def list_all_states(self, rom_id):
        try:
            r = requests.get(
                f"{self.host}/api/states",
                params={"rom_id": rom_id},
                headers=self.get_auth_headers(),
                timeout=REQUEST_TIMEOUT,
                verify=CERTIFI_PATH
            )
            if r.status_code != 200: return []
            return self._normalize_collection_items(r.json(), preferred_keys=["states"])
        except Exception as e:
            print(f"[API] list_all_states error: {e}")
            return []

    def delete_state(self, state_id):
        try:
            # RomM OpenAPI: POST /api/states/delete with {"states": [id]}
            r = requests.post(
                f"{self.host}/api/states/delete",
                headers=self.get_auth_headers(),
                json={"states": [int(state_id)]},
                timeout=REQUEST_TIMEOUT,
                verify=CERTIFI_PATH,
            )
            return r.status_code in [200, 204]
        except Exception as e:
            print(f"[API] delete_state error: {e}")
            return False

    def download_save(self, save_item, target_path, thread=None):
        try:
            path = save_item.get('download_path') or save_item.get('path')
            if isinstance(path, str) and path:
                url = path if path.startswith('http') else f"{self.host}{path}"
            else:
                save_id = save_item.get('id')
                if save_id is None:
                    return False
                # RomM OpenAPI: GET /api/saves/{id}/content
                url = f"{self.host}/api/saves/{save_id}/content"
            try:
                r = requests.get(url, headers=self.get_auth_headers(), stream=True, 
                                 timeout=REQUEST_TIMEOUT, verify=CERTIFI_PATH)
            except (requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.RequestException) as e:
                print(f"[API] Network error in download_save: {e}")
                return False

            if r.status_code == 200:
                with open(target_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if thread and thread.isInterruptionRequested():
                            f.close()
                            os.remove(target_path)
                            return False
                        if chunk:
                            f.write(chunk)
                return True
            return False
        except Exception as e:
            print(f"[API] Error downloading save: {e}")
            return False

    def download_state(self, state_obj, dest_path):
        try:
            dl_path = state_obj.get('download_path') or state_obj.get('file_path')
            if not dl_path:
                state_id = state_obj.get('id')
                if state_id is None:
                    return False
                r = requests.get(
                    f"{self.host}/api/states/{state_id}",
                    headers=self.get_auth_headers(),
                    timeout=REQUEST_TIMEOUT,
                    verify=CERTIFI_PATH,
                )
                if r.status_code != 200:
                    return False
                state_payload = r.json() if isinstance(r.json(), dict) else {}
                dl_path = state_payload.get('download_path') or state_payload.get('file_path')
                if not dl_path:
                    return False
            url = dl_path if dl_path.startswith('http') \
                  else f"{self.host}{dl_path}"
            r = requests.get(url, headers=self.get_auth_headers(),
                           stream=True, timeout=60, verify=CERTIFI_PATH)
            if r.status_code != 200:
                return False
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(65536):
                    if chunk: f.write(chunk)
            return True
        except Exception as e:
            print(f"[API] download_state error: {e}")
            return False

    def upload_save(self, rom_id, emulator, file_obj, slot="rommate-windows", raw=False, filename_override=None):
        try:
            url = f"{self.host}/api/saves"
            params = {
                "rom_id": rom_id,
                "emulator": self._normalize_upload_emulator(emulator),
                "slot": slot
            }
            
            # file_obj can be a path string or a file-like object
            if isinstance(file_obj, str):
                f = open(file_obj, 'rb')
                close_after = True
                filename = filename_override or os.path.basename(file_obj)
            else:
                f = file_obj
                close_after = False
                filename = filename_override or "save.zip"
            
            # Strip .auto suffix 
            if filename.endswith('.auto'):
                filename = filename[:-5]
            
            try:
                files = {'saveFile': (filename, f, 'application/octet-stream')}
                r = requests.post(url, params=params, headers=self.get_auth_headers(),
                                  files=files, timeout=(10, 120), verify=CERTIFI_PATH)
                print(f"[API] upload_save -> {r.status_code}: {r.text[:200]}")
                return r.status_code in [200, 201], r.text
            finally:
                if close_after: f.close()
        except Exception as e:
            print(f"[API] upload_save error: {e}")
            return False, str(e)

    def upload_state(self, rom_id, emulator, file_obj, slot="rommate-state", filename_override=None):
        try:
            from pathlib import Path
            
            if isinstance(file_obj, str):
                f = open(file_obj, 'rb')
                close_after = True
                filename = filename_override or Path(file_obj).name
            else:
                f = file_obj
                close_after = False
                filename = filename_override or "state.state"
            
            # Strip .auto suffix 
            if filename.endswith('.auto'):
                filename = filename[:-5]
            
            # Strip RomM timestamp brackets 
            import re
            filename = re.sub(
                r'\s*\[[^\]]*\d{4}-\d{2}-\d{2}[^\]]*\]', '', filename)
            
            url = f"{self.host}/api/states"
            params = {
                "rom_id": rom_id,
                "emulator": self._normalize_upload_emulator(emulator),
                "slot": slot
            }
            
            try:
                files = {'stateFile': (filename, f, 'application/octet-stream')}
                r = requests.post(url, params=params, headers=self.get_auth_headers(),
                                  files=files, timeout=(10, 120), verify=CERTIFI_PATH)
                print(f"[API] upload_state -> {r.status_code}: {r.text[:300]}")
                return r.status_code in [200, 201], r.text
            finally:
                if close_after: f.close()
        except Exception as e:
            print(f"[API] upload_state error: {e}")
            return False, str(e)

    def get_firmware(self, platform_id=None):
        """
        Fetch BIOS/firmware files from RomM's dedicated firmware endpoint.
        """
        try:
            url = f"{self.host}/api/firmware"
            params = {"limit": 100, "offset": 0}
            if platform_id is not None:
                params["platform_id"] = int(platform_id)
            firmware_list = []

            while True:
                r = requests.get(
                    url,
                    params=params,
                    headers=self.get_auth_headers(),
                    timeout=REQUEST_TIMEOUT,
                    verify=CERTIFI_PATH,
                )
                if r.status_code != 200:
                    return firmware_list

                payload = r.json()
                items = self._extract_paginated_items(payload, preferred_keys=["firmware"])
                if not items and isinstance(payload, list):
                    items = payload

                if not items:
                    break

                firmware_list.extend(items)

                if not isinstance(payload, dict):
                    break

                total = payload.get("total") or payload.get("count")
                if isinstance(total, int) and total > 0:
                    if len(firmware_list) >= total:
                        break

                if len(items) < params["limit"]:
                    break

                params["offset"] += params["limit"]

            return firmware_list
        except Exception as e:
            print(f"[API] Error getting firmware: {e}")
            return []

    def get_bios_files(self, platform_id=None):
        """
        Alias for get_firmware using the correct RomM dedicated endpoint.
        """
        return self.get_firmware(platform_id=platform_id)

    def download_firmware(self, fw_item, target_path, progress_cb=None, thread=None):
        try:
            path = fw_item.get('download_path')
            if isinstance(path, str) and path:
                url = path if path.startswith('http') else f"{self.host}{path}"
            else:
                fw_id = fw_item.get('id')
                file_name = fw_item.get('file_name') or fw_item.get('name')
                if fw_id is None or not file_name:
                    slug = fw_item.get('platform_slug', 'unknown')
                    file_name = file_name or ""
                    url = f"{self.host}/api/raw/assets/firmware/{slug}/{file_name}"
                else:
                    encoded_name = quote(str(file_name))
                    # RomM OpenAPI: GET /api/firmware/{id}/content/{file_name}
                    url = f"{self.host}/api/firmware/{fw_id}/content/{encoded_name}"
            try:
                r = requests.get(url, headers=self.get_auth_headers(), stream=True, 
                                 timeout=60, verify=CERTIFI_PATH)
            except (requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.RequestException) as e:
                print(f"[API] Network error in download_firmware: {e}")
                return False

            total = int(r.headers.get('content-length', 0))
            downloaded = 0
            start = time.time()
            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(1024*1024):
                    if thread and thread.isInterruptionRequested():
                        f.close()
                        os.remove(target_path)
                        return False
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            elapsed = time.time() - start
                            speed = downloaded / elapsed if elapsed > 0 else 0
                            progress_cb(downloaded, total, speed)
            return True
        except Exception as e:
            print(f"[API] Error downloading firmware: {e}")
            return False

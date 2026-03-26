import json
import os
import logging
from pathlib import Path
from src.app_paths import primary_app_dir, preferred_existing_app_dir

from src.platforms import RETROARCH_PLATFORMS


def _unique_list(items):
    return list(dict.fromkeys(items))


def _normalize_platform_slugs(raw_slugs):
    if isinstance(raw_slugs, list):
        return [str(s).strip() for s in raw_slugs if str(s).strip()]
    if isinstance(raw_slugs, str):
        slug = raw_slugs.strip()
        return [slug] if slug else []
    return []


def _normalize_launch_args(raw_args, emulator_id):
    if isinstance(raw_args, list):
        values = [str(a) for a in raw_args if a is not None]
    elif isinstance(raw_args, str):
        values = [raw_args]
    else:
        values = []

    if values:
        return values

    if emulator_id == "windows_native":
        return []
    return ["{rom_path}"]


def _coerce_bool(value, default):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _sanitize_emulator_entry(entry):
    if not isinstance(entry, dict):
        return None, True

    changed = False
    sanitized = dict(entry)

    emu_id = sanitized.get("id")
    if not isinstance(emu_id, str) or not emu_id.strip():
        name = sanitized.get("name")
        if isinstance(name, str) and name.strip():
            sanitized["id"] = name.strip().lower().replace(" ", "_")
            changed = True
        else:
            return None, True
    else:
        trimmed_id = emu_id.strip()
        if trimmed_id != emu_id:
            sanitized["id"] = trimmed_id
            changed = True

    emu_name = sanitized.get("name")
    if not isinstance(emu_name, str) or not emu_name.strip():
        sanitized["name"] = sanitized["id"]
        changed = True
    elif emu_name != emu_name.strip():
        sanitized["name"] = emu_name.strip()
        changed = True

    exe_path = sanitized.get("executable_path")
    normalized_path = exe_path.strip() if isinstance(exe_path, str) else ""
    if normalized_path != exe_path:
        sanitized["executable_path"] = normalized_path
        changed = True

    normalized_args = _normalize_launch_args(sanitized.get("launch_args"), sanitized["id"])
    if sanitized.get("launch_args") != normalized_args:
        sanitized["launch_args"] = normalized_args
        changed = True

    platform_slugs = _normalize_platform_slugs(sanitized.get("platform_slugs"))
    if not platform_slugs:
        single_slug = sanitized.get("platform_slug")
        if isinstance(single_slug, str) and single_slug.strip():
            platform_slugs = [single_slug.strip()]
    platform_slugs = _unique_list(platform_slugs)
    if sanitized.get("platform_slugs") != platform_slugs:
        sanitized["platform_slugs"] = platform_slugs
        changed = True

    save_resolution = sanitized.get("save_resolution")
    if not isinstance(save_resolution, dict):
        sanitized["save_resolution"] = {"mode": "none"}
        changed = True

    user_defined = _coerce_bool(sanitized.get("user_defined", False), False)
    if sanitized.get("user_defined") is not user_defined:
        sanitized["user_defined"] = user_defined
        changed = True

    sync_enabled = _coerce_bool(sanitized.get("sync_enabled", True), True)
    if sanitized.get("sync_enabled") is not sync_enabled:
        sanitized["sync_enabled"] = sync_enabled
        changed = True

    conflict_behavior = sanitized.get("conflict_behavior")
    if not isinstance(conflict_behavior, str) or not conflict_behavior.strip():
        sanitized["conflict_behavior"] = "ask"
        changed = True

    return sanitized, changed


def _sanitize_emulators_payload(data):
    if not isinstance(data, dict):
        data = {}

    changed = False
    migration_done = data.get("migration_done", False)
    if not isinstance(migration_done, bool):
        migration_done = bool(migration_done)
        changed = True
    if "migration_done" not in data:
        changed = True
    data["migration_done"] = migration_done

    emulators_value = data.get("emulators", [])
    if not isinstance(emulators_value, list):
        emulators_value = []
        changed = True

    sanitized_emulators = []
    seen_ids = set()
    for entry in emulators_value:
        sanitized_entry, entry_changed = _sanitize_emulator_entry(entry)
        if sanitized_entry is None:
            changed = True
            continue

        emu_id = sanitized_entry["id"]
        if emu_id in seen_ids:
            logging.warning(f"Dropping duplicate emulator id during sanitize: {emu_id}")
            changed = True
            continue

        seen_ids.add(emu_id)
        sanitized_emulators.append(sanitized_entry)
        if entry_changed:
            changed = True

    if data.get("emulators") != sanitized_emulators:
        data["emulators"] = sanitized_emulators
        changed = True

    return data, changed

DEFAULT_EMULATORS = [
    {
        "id": "retroarch",
        "name": "Multi-Console (RetroArch)",
        "executable_path": "",
        "launch_args": ["-L", "{core_path}", "{rom_path}"],
        "platform_slugs": ["dc", "dreamcast", "segacd", "sega-cd","megacd", "sega-megacd", "32x", "sega-32x",
                         "saturn", "sega-saturn", "psx", "ps1", "playstation", "ps2", "playstation2",
                         "psp", "playstation-portable", "snes", "super-nintendo", "sega-genesis", "sega-megadrive",
                         "n64", "nintendo-64", "nds", "nintendo-ds", "gba", "gameboy-advance", "gb", "gameboy",
                         "ngp", "neo-geo-pocket", "ngpc", "neo-geo-pocket-color",
                         "pce", "pcengine", "tg16", "pc-engine-turboGrafx-16", "pcenginecd", "arcade", "mame",
                         "dos", "pc-dos", "gamecube", "nintendo-gamecube", "nes", "famicom", "gbc", "gameboy-color",
                         "sms", "sega-master-system", "gg", "game-gear", "virtualboy", "vboy", "wii", "nintendo-wii"],
        "save_resolution": {
            "mode": "retroarch",
            "srm_dir": "",
            "state_dir": ""
        },
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "eden",
        "name": "Switch (Eden)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "platform_slugs": ["switch", "nintendo-switch"],
        "save_resolution": {
            "mode": "switch",
            "path": ""
        },
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "rpcs3",
        "name": "PlayStation 3 (RPCS3)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "platform_slugs": ["ps3", "playstation-3", "playstation3"],
        "save_resolution": {
            "mode": "ps3",
            "path": ""
        },
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "dolphin",
        "name": "GameCube / Wii (Dolphin)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "platform_slugs": ["gc", "ngc", "gamecube", "nintendo-gamecube", "wii", "nintendo-wii"],
        "save_resolution": {
            "mode": "dolphin",
            "path": ""
        },
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "pcsx2",
        "name": "PlayStation 2 (PCSX2)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "platform_slugs": ["ps2", "playstation-2", "playstation2"],
        "save_resolution": {
            "mode": "folder",
            "path": ""
        },
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "cemu",
        "name": "Wii U (Cemu)",
        "executable_path": "",
        "launch_args": ["-g", "{rom_path}"],
        "platform_slugs": ["wiiu", "wii-u", "nintendo-wii-u", "nintendo-wiiu"],
        "save_resolution": {
            "mode": "cemu",
            "path": ""
        },
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "azahar",
        "name": "Nintendo 3DS (Azahar)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "platform_slugs": ["n3ds", "3ds", "nintendo-3ds", "nintendo3ds"],
        "save_resolution": {
            "mode": "folder",
            "path": ""
        },
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "xemu",
        "name": "Xbox (Xemu)",
        "executable_path": "",
        "launch_args": ["-dvd_path", "{rom_path}"],
        "github": "xemu-project/xemu",
        "platform_slugs": ["xbox"],
        "save_resolution": {
            "mode": "folder",
            "path": ""
        },
        "folder": "xemu",
        "user_defined": False,
        "sync_enabled": False,
        "conflict_behavior": "ask"
    },
    {
        "id": "xenia_canary",
        "name": "Xbox 360 (Xenia Canary)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "platform_slugs": ["xbox360", "xbla"],
        "save_resolution": {
            "mode": "folder",
            "path": ""
        },
        "folder": "xenia_canary",
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "xenia",
        "name": "Xbox 360 (Xenia)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "url": "https://github.com/xenia-project/release-builds-windows/releases/latest/download/xenia_master.zip",
        "platform_slugs": ["xbox360", "xbla"],
        "save_resolution": {
            "mode": "folder",
            "path": ""
        },
        "folder": "xenia",
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "duckstation",
        "name": "Playstation (DuckStation)",
        "executable_path": "",
        "launch_args": ["-batch", "{rom_path}"],
        "github": "stenzek/duckstation",
        "platform_slugs": ["ps1", "psx", "playstation"],
        "save_resolution": {
            "mode": "folder",
            "path": ""
        },
        "folder": "duckstation",
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "ppsspp",
        "name": "PSP (PPSSPP)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "platform_slugs": ["psp", "playstation-portable"],
        "save_resolution": {
            "mode": "folder",
            "path": ""
        },
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "melonds",
        "name": "Nintendo DS (MelonDS)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "github": "melonDS-emu/melonDS",
        "platform_slugs": ["nds", "nintendo-ds", "nintendods"],
        "save_resolution": {
            "mode": "file",
            "path": ""
        },
        "folder": "melonds",
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "redream",
        "name": "Sega Dreamcast (reDream)",
        "executable_path": "",
        "launch_args": ["{rom_path}"],
        "platform_slugs": ["dreamcast", "sega-dreamcast", "dc"],
        "save_resolution": {
            "mode": "folder",
            "path": ""
        },
        "folder": "redream",
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    },
    {
        "id": "windows_native",
        "name": "Windows (Native)",
        "executable_path": "",
        "launch_args": [],
        "platform_slugs": ["windows", "win"],
        "is_native": True,
        "save_resolution": {
            "mode": "windows"
        },
        "user_defined": False,
        "sync_enabled": True,
        "conflict_behavior": "ask"
    }
]

EMULATORS_FILE = primary_app_dir() / "emulators.json"

def load_emulators_raw():
    """Load the full emulators.json content."""
    if not EMULATORS_FILE.exists():
        legacy_file = preferred_existing_app_dir() / "emulators.json"
        if legacy_file.exists() and legacy_file != EMULATORS_FILE:
            try:
                EMULATORS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(legacy_file, 'r', encoding='utf-8') as f:
                    legacy_data = json.load(f)
                with open(EMULATORS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(legacy_data, f, indent=4)
            except Exception:
                pass

    if not EMULATORS_FILE.exists():
        data = {"migration_done": False, "emulators": DEFAULT_EMULATORS}
        save_emulators_raw(data)
        return data
    
    try:
        with open(EMULATORS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            data, changed = _sanitize_emulators_payload(data)
            
            # Filter out deprecated emulators (Yuzu)
            emus = data.get("emulators", [])
            initial_count = len(emus)
            data["emulators"] = [
                e for e in emus
                if not (
                    e.get("id", "").lower() == "yuzu" or 
                    "yuzu" in e.get("name", "").lower()
                )
            ]
            
            changed = changed or (len(data["emulators"]) < initial_count)
            if changed:
                logging.info("Removed deprecated entries from emulators")
            
            # Ensure sync_enabled and conflict_behavior exists for all
            for e in data["emulators"]:
                if "sync_enabled" not in e:
                    e["sync_enabled"] = True
                    changed = True
                if "conflict_behavior" not in e:
                    e["conflict_behavior"] = "ask"
                    changed = True

                # Merge newly-supported RetroArch platforms (e.g., Virtual Boy)
                if e.get("id") == "retroarch":
                    desired = set(_unique_list(["multi"] + RETROARCH_PLATFORMS))
                    current = set(e.get("platform_slugs", []))
                    if not desired.issubset(current):
                        e["platform_slugs"] = _unique_list(list(current) + list(desired))
                        changed = True
                        logging.info("Migrated RetroArch supported platforms")
                
                # Migrate DuckStation to folder mode (v0.6.1)
                if e.get("id") == "duckstation":
                    res = e.get("save_resolution", {})
                    if res.get("mode") == "file":
                        res["mode"] = "folder"
                        e["save_resolution"] = res
                        changed = True
                        logging.info("Migrated DuckStation to folder save mode")
                
                # Migrate Xenia to "Xenia Stable (Xbox 360)" (v0.6.1)
                if e.get("id") == "xenia" and e.get("name") == "Xenia":
                    e["name"] = "Xenia Stable (Xbox 360)"
                    changed = True
                    logging.info("Migrated Xenia to 'Xenia Stable (Xbox 360)'")

            # Merge any new defaults
            existing_ids = {e.get("id") for e in data["emulators"] if e.get("id")}
            for default_emu in DEFAULT_EMULATORS:
                if default_emu["id"] not in existing_ids:
                    data["emulators"].append(default_emu)
                    changed = True
                    logging.info(f"Added new default emulator: {default_emu['id']}")
            
            if changed:
                save_emulators_raw(data)
                
            return data
    except Exception as e:
        logging.error(f"Failed to load emulators.json: {e}")
    
    return {"migration_done": False, "emulators": DEFAULT_EMULATORS}

def load_emulators():
    """Return only the list of emulator dicts."""
    return load_emulators_raw().get("emulators", DEFAULT_EMULATORS)

def save_emulators_raw(data):
    """Save full content to emulators.json."""
    EMULATORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(EMULATORS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save emulators.json: {e}")

def save_emulators(emulators_list):
    """Update only the emulators list in the JSON file."""
    data = load_emulators_raw()
    data["emulators"] = emulators_list
    save_emulators_raw(data)

def migrate_old_config(config_manager):
    """Migrate emulator paths from config.json to emulators.json once."""
    data = load_emulators_raw()
    if data.get("migration_done"):
        return

    logging.info("Starting emulator path migration from old config...")
    old_emus = config_manager.get("emulators", {})
    changed = False
    
    # Map old config names/ids to new schema IDs
    id_map = {
        "Multi-Console (RetroArch)": "retroarch",
        "Switch (Eden)": "eden",
        "PlayStation 3": "rpcs3",
        "GameCube / Wii": "dolphin",
        "PlayStation 2": "pcsx2",
        "Wii U (Cemu)": "cemu",
        "Nintendo 3DS (Azahar)": "azahar"
    }

    for old_name, old_data in old_emus.items():
        new_id = id_map.get(old_name)
        path = old_data.get("path")
        if new_id and path:
            for emu in data["emulators"]:
                if emu["id"] == new_id and not emu["executable_path"]:
                    emu["executable_path"] = path
                    logging.info(f"Migrated {new_id} path from old config: {path}")
                    changed = True
                    break
    
    data["migration_done"] = True
    save_emulators_raw(data)
    if changed:
        logging.info("Emulator path migration complete.")

def get_emulator_for_platform(slug):
    """Return the first emulator that supports the given platform slug."""
    all_emus = load_emulators()
    for emu in all_emus:
        if slug in emu.get("platform_slugs", []):
            return emu
    return None

def get_all_emulators():
    """Return the full list of emulators."""
    return load_emulators()

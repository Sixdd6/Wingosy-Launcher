from pathlib import Path
import shutil

PRIMARY_APP_DIR_NAME = ".rommate"
LEGACY_APP_DIR_NAMES = (".wingosy", ".argosy")


def primary_app_dir() -> Path:
    return Path.home() / PRIMARY_APP_DIR_NAME


def legacy_app_dirs() -> list[Path]:
    return [Path.home() / name for name in LEGACY_APP_DIR_NAMES]


def preferred_existing_app_dir() -> Path:
    primary = primary_app_dir()
    if primary.exists():
        return primary
    for legacy in legacy_app_dirs():
        if legacy.exists():
            return legacy
    return primary


def migrate_legacy_to_primary() -> Path:
    primary = primary_app_dir()
    if primary.exists():
        return primary

    for legacy in legacy_app_dirs():
        if not legacy.exists():
            continue
        try:
            shutil.copytree(legacy, primary)
            return primary
        except Exception:
            break

    primary.mkdir(parents=True, exist_ok=True)
    return primary

# Wingosy Launcher

![Wingosy Example](Wingosy_example.png)

A Windows port of the original [Argosy Launcher for Android](https://github.com/rommapp/argosy-launcher).

**Wingosy** is a lightweight, portable Windows game launcher designed to bridge the gap between your local emulators and a **RomM** server. It features automated cloud save syncing, portable emulator management, and a unified library interface.

## Key Features

- **Cloud Save Syncing**: Automatically pulls your latest saves from RomM before you play and pushes changes back to the cloud as soon as you close the emulator.
- **Universal PLAY Button**: One-click to sync, launch, and track your games across PCSX2, Dolphin, Cemu, RPCS3, Yuzu/Eden, RetroArch, and more.
- **Playtime Tracking**: Automatically track how long you play each game and see your total playtime in the library.
- **Portable Emulator Management**: Download and manage the latest versions of your favorite emulators directly through the app. Supports "Portable Mode" automatically.
- **BIOS / Firmware Rescue**: Search and download required BIOS files directly from your RomM library or firmware index.
- **Library Search & Filtering**: Instantly find games by name or console platform.

## Getting Started

1.  **Download**: Grab the latest `Wingosy.exe` from the [Releases](https://github.com/abduznik/Wingosy-Launcher/releases) page.
2.  **Setup**: On the first run, enter your RomM host URL and credentials.
3.  **Configure Paths**:
    -   Go to the **Emulators** tab.
    -   Set your **ROM Path** (where your games are stored).
    -   Set your **Emu Path** (where you want emulators to be installed).
4.  **Sync & Play**: Click on any game in your library and hit **▶ PLAY**. Wingosy v0.5.0 handles the rest!

## Supported Emulators

- **PlayStation 3**: RPCS3 - **Stable** (Portable & AppData)
- **Wii U**: Cemu - **Stable** (Portable & AppData)
- **PlayStation 2**: PCSX2 (Qt) - **Stable**
- **Nintendo Switch**: Yuzu / Eden / Ryujinx - **Stable**
- **Nintendo 3DS**: Azahar / Citra - **Stable**
- **GameCube / Wii**: Dolphin - **Stable**
- **Multi-system**: RetroArch - **Stable**
- **And more...** (easily extensible via `config.json`)

## Roadmap

### Current Status (v0.5.0)
- ✅ **New**: Asynchronous Library Loading (No UI freezing)
- ✅ **New**: Playtime tracking per game
- ✅ **New**: Support for RPCS3, Cemu, and Azahar/Citra
- ✅ **New**: Portable Mode detection for all major emulators
- ✅ **New**: Smarter GitHub asset selection with keyword filtering
- ✅ Auto-updating: downloads and replaces Wingosy.exe in place via batch intermediary
- ✅ Save conflict resolution: choose between cloud, local, or keep both
- ✅ RetroArch core auto-download for missing cores
- ✅ System tray notifications for sync events
- ✅ Game state indicators on library cards (local ROM, cloud save)
- ✅ Emulator health indicators (green/red/grey dot per emulator)
- ✅ Keyboard shortcuts (Ctrl+F to search, F5 to refresh)

### Planned for future releases
- Custom emulator profile management via UI
- Xenia (Xbox 360) and DuckStation (PS1) save path resolution
- Detailed game view with screenshots and metadata from RomM
- Game metadata editing (Title, Platform, etc.) directly in Wingosy
- Improved save slot management (rolling backups)
- Dark/Light mode theme switching

## Building from Source

If you want to run or build Wingosy manually:

```powershell
# Install dependencies
pip install PySide6 psutil requests py7zr Pillow

# Run the app
python main.py

# Build .exe with icon
pip install pyinstaller
pyinstaller --noconsole --onefile --name Wingosy --icon "icon.png" --add-data "icon.png;." --hidden-import sqlite3 --hidden-import src.ui --hidden-import src.ui.main_window --hidden-import src.ui.dialogs --hidden-import src.ui.threads --hidden-import src.ui.widgets --hidden-import src.ui.tabs --hidden-import src.ui.tabs.library --hidden-import src.ui.tabs.emulators main.py
```

## Changelog

### v0.5.0
- **Asynchronous Library Loading**: Refactored library fetching to a background thread to prevent UI freezing on startup and refresh.
- **Expanded Emulator Support**: Added official save path resolution and launch logic for **Wii U (Cemu)**, **PlayStation 3 (RPCS3)**, and **Nintendo 3DS (Azahar/Citra)**.
- **Playtime Tracking**: Sessions are now timed, and total playtime is stored in `playtime.json` and displayed in game details.
- **Portable Mode Awareness**: Smarter resolution of save folders for emulators using portable mode (checking for `User`, `mlc01`, or `dev_hdd0` next to the EXE).
- **TitleID Awareness**: RPCS3 and Cemu now correctly resolve specific game subfolders via command-line TitleID extraction.
- **Improved Sync Logic**: Pulling from cloud now correctly triggers if local files are missing, even if timestamps match the cache.
- **Self-Update Fix**: Implemented a batch-file restart pattern to ensure PyInstaller temporary directories are fully released on update.
- **Qt Compatibility**: Cleans environment variables before launching emulators to prevent crashes in apps with their own bundled Qt runtime (e.g. PCSX2).
- **Advanced Asset Downloader**: Smarter GitHub release picking using required/excluded keywords defined in config.
- **UI Enhancements**: Added cloud save indicator dot to game cards (Blue dot).
- Fixed: ROM path casing preserved on Windows to allow header reading.
- Fixed: Signal disconnection `RuntimeError` during game launch.

### v0.4.4
- Initial implementation of playtime tracking and cloud indicators.
- Self-update restart logic improvements.
- Dolphin/GameCube path resolution fixes.

### v0.4.0
- Auto-updating exe with in-place replacement and restart prompt
- Save conflict resolution dialog (Use Cloud / Keep Local / Keep Both)
- RetroArch core auto-download from libretro buildbot
- Game state indicators: green dot for local ROM, blue dot for cloud save
- UI refactored into maintainable package structure

## License

GNU General Public License v3.0. See `LICENSE` for details.

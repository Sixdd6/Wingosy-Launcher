"""
Emulator download source definitions.
Edit this file to update download sources without touching application logic.
"""

EMULATOR_SOURCES = {
    "retroarch": {
        "type": "direct",
        "label": "RetroArch (Stable)",
        "url": "https://buildbot.libretro.com/stable",
        "exe_hint": "retroarch.exe"
    },
    "eden": {
        "type": "direct",
        "label": "Eden",
        "url": "https://git.eden-emu.dev/eden-emu/eden/releases",
        "exe_hint": "eden.exe"
    },
    "rpcs3": {
        "type": "direct",
        "label": "RPCS3",
        "url": "https://rpcs3.net/download",
        "exe_hint": "rpcs3.exe"
    },
    "dolphin": {
        "type": "direct",
        "label": "Dolphin",
        "url": "https://dolphin-emu.org/download",
        "exe_hint": "Dolphin.exe"
    },
    "pcsx2": {
        "type": "direct",
        "label": "PCSX2",
        "url": "https://pcsx2.net/downloads",
        "exe_hint": "pcsx2-qt.exe"
    },
    "cemu": {
        "type": "direct",
        "label": "Cemu",
        "url": "https://cemu.info/#download",
        "exe_hint": "Cemu.exe"
    },
    "azahar": {
        "type": "direct",
        "label": "Azahar",
        "url": "https://github.com/azahar-emu/azahar/releases/latest",
        "exe_hint": "azahar.exe"
    },
    "xemu": {
        "type": "direct",
        "label": "xemu",
        "url": "https://github.com/xemu-project/xemu/releases/latest",
        "exe_hint": "xemu.exe"
    },
    "xenia_canary": {
        "type": "direct",
        "label": "xenia-canary",
        "url": "https://github.com/xenia-canary/xenia-canary-releases/releases/canary_experimental",
        "exe_hint": "xenia_canary.exe"
    },
    "duckstation": {
        "type": "direct",
        "label": "Duckstation",
        "url": "https://github.com/stenzek/duckstation/releases/latest",
        "exe_hint": "duckstation-qt-x64-ReleaseLTCG.exe"
    },
    "melonds": {
        "type": "direct",
        "label": "melonDS",
        "url": "https://github.com/melonDS-emu/melonDS/releases/latest",
        "exe_hint": "melonDS.exe"
    },
    "shadps4": {
        "type": "direct",
        "label": "ShadPS4",
        "url": "https://github.com/shadps4-emu/shadPS4/releases/latest",
        "exe_hint": "shadps4.exe"
    },
    "redream": {
        "type": "direct",
        "label": "ReDream",
        "url": "https://redream.io/download",
        "exe_hint": "redream.exe"
    }
}

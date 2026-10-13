# ARK: Survival Ascended Dedicated Server Manager (Windows)
from __future__ import annotations

import base64
import collections
import contextlib
import ctypes
import copy
import hashlib
import json
import logging
import logging.handlers
import os
import queue
import re
import shlex
import shutil
import socket
import ssl
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from ctypes import wintypes
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import tkinter as tk
import tkinter.messagebox as _tk_messagebox
from tkinter import filedialog, simpledialog
from tkinter import font as tkfont
from tkinter import ttk
import uuid

from data.ark_creature_data import (_CREATURE_DATA, _CREATURE_NAMES_SORTED,
                                    _ENTITY_ID_TO_NAME, _NAMETAG_TO_NAMES)
from data.ark_item_data import (_ITEM_DATA, _ITEM_NAMES_SORTED, _CLASS_TO_ITEM_NAME)
from data.ark_harvest_data import (_HARVEST_RESOURCES, _HARVEST_NAMES_SORTED,
                                    _HARVEST_CLASS_TO_NAME)
from data.ini_settings import (
    _STAT_NAMES, _SS, _GM,
    INI_SETTINGS_GENERAL, INI_SETTINGS_CROPS,
    INI_SETTINGS_DINOS, INI_SETTINGS_DINO_STATS,
    INI_SETTINGS_PLAYERS, INI_SETTINGS_PLAYER_STATS,
    INI_SETTINGS_MISC,
    INI_SETTINGS_ENVIRONMENT, INI_SETTINGS_DINOS_FULL,
    INI_SETTINGS_STRUCTURES, INI_SETTINGS_STACK_SIZE,
    INI_SETTINGS_SERVER_OPTIONS, INI_SETTINGS_CLUSTER_TRANSFERS, _SPAWN_ENTRY_KEYS,
    _DINO_STAT_DEFAULTS,
)

try:
    import winreg  # type: ignore
except Exception:
    winreg = None

# Called before every message box with its kind ("showinfo", "askyesno", ...), title and
# message; returning False keeps that box from showing (only ever an info box).
_BEFORE_DIALOG: Optional[Callable[[str, str, str], bool]] = None


class _MessageBoxes:
    """tkinter.messagebox, except that a manager running in the background shows its window
    first: a question asked into a hidden window would never be seen, and a worker waiting on
    the answer would hold its server's task for good."""

    def __getattr__(self, name: str) -> Any:
        fn = getattr(_tk_messagebox, name)
        if not (callable(fn) and name.startswith(("ask", "show"))):
            return fn

        def call(*args: Any, **kwargs: Any) -> Any:
            hook = _BEFORE_DIALOG
            if hook is not None:
                title = str(args[0] if args else kwargs.get("title", ""))
                message = str(args[1] if len(args) > 1 else kwargs.get("message", ""))
                try:
                    if hook(name, title, message) is False and name == "showinfo":
                        return "ok"
                except Exception:
                    pass
            return fn(*args, **kwargs)

        return call


messagebox: Any = _MessageBoxes()


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# =============================================================================
# GLOBALS
# =============================================================================

APP_NAME = "ARK: Survival Ascended Server Manager"
APP_USERMODEL_ID = "Ch4r0ne.ARKASAManager"

ARK_ASA_APP_ID = 2430930  # ASA Dedicated Server AppID

STEAMCMD_ZIP_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
VC_REDIST_X64_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
DXWEBSETUP_URL = "https://download.microsoft.com/download/1/7/1/1718CCC4-6315-4D8E-9543-8E28A4E18C4C/dxwebsetup.exe"

AMAZON_ROOT_CA1_URL = "https://www.amazontrust.com/repository/AmazonRootCA1.cer"
AMAZON_R2M02_URL = "https://crt.r2m02.amazontrust.com/r2m02.cer"

DEFAULT_STEAMCMD_DIR = r"C:\GameServer\SteamCMD"
DEFAULT_SERVER_DIR = r"C:\GameServer\ARK-Survival-Ascended-Server"

DEFAULT_MAP = "TheIsland_WP"
DEFAULT_SERVER_NAME = "default"
DEFAULT_PORT = 7777
DEFAULT_QUERY_PORT = 27015
DEFAULT_MAX_PLAYERS = 70

DEFAULT_RCON_HOST = "127.0.0.1"
DEFAULT_RCON_PORT = 27020

DOWNLOAD_TIMEOUT_SEC = 180
AUTOSAVE_DEBOUNCE_MS = 700
INI_APPLY_DEBOUNCE_MS = 500
INI_VISUAL_DEBOUNCE_MS = 400
DEFAULT_SCHEDULE_TIME = "03:00"

MAP_PRESETS = [
    "TheIsland_WP",
    "ScorchedEarth_WP",
    "TheCenter_WP",
    "Aberration_WP",
    "Extinction_WP",
    "Ragnarok_WP",
    "Valguero_WP",
    "LostColony_WP",
]
MAP_CUSTOM_SENTINEL = "Custom..."

APPDATA_DIR_NAME = "ARK-Ascended-Server-Manager"
LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "app.log"
SERVER_LOG_FILE_NAME = "server.log"  # per server, under servers/<id>/logs
LOG_ROTATE_BYTES = 20 * 1024 * 1024
LOG_ROTATE_KEEP = 3
# How long an Update & Restart waits, with its server still up, for another server's
# SteamCMD run to finish before it gives up for this time.
UPDATE_SLOT_WAIT_SEC = 3 * 60 * 60
# How long a cluster-wide start waits for one map to load (RCON answers) before the next.
READY_WAIT_SEC = 15 * 60
# How long a stop waits for the server to save and exit before ending the process.
STOP_WAIT_SEC = 120
# A planned restart or update found more than this late (the PC slept, the clock jumped)
# is skipped until its next time rather than kicking players at a random hour.
SCHEDULE_GRACE = timedelta(hours=2)
STAGING_DIR_NAME = "staging"
BASELINE_DIR_NAME = "baseline"
BACKUP_DIR_NAME = "backups"
# Written into every zip the manager makes, so the backup screen can tell which server
# a zip belongs to when several profiles share one backup folder.
BACKUP_MANIFEST_NAME = "asa_manager_backup.json"
BACKUP_KIND_FULL = "full"
BACKUP_KIND_PRE_IMPORT = "pre-import"
BACKUP_KIND_PRE_RESTORE = "pre-restore"
BACKUP_KIND_AUTO_SAVE = "auto-save"
BACKUP_FULL_PREFIX = "ASA_Backup_"
AUTO_SAVE_PREFIX = "ASA_AutoSave_"
# Auto-saves only run while the server is up, since a stopped world does not change.
# They are zipped under their own prefix and pruned against their own count, so a busy
# timer can never push manual or on-stop backups out of retention.
AUTO_SAVE_DEFAULT_INTERVAL_MIN = 60
AUTO_SAVE_MIN_INTERVAL_MIN = 5
AUTO_SAVE_MAX_INTERVAL_MIN = 7 * 24 * 60
AUTO_SAVE_TICK_SEC = 15.0
AUTO_SAVE_DEFAULT_KEEP = 12
AUTO_SAVE_MAX_KEEP = 500
# Breathing room after SaveWorld returns before the save files are zipped.
AUTO_SAVE_SETTLE_SEC = 5.0
BACKUP_ZIP_PREFIXES = (
    ("ASA_Backup_", BACKUP_KIND_FULL),
    ("ASA_PreImport_", BACKUP_KIND_PRE_IMPORT),
    ("ASA_PreRestore_", BACKUP_KIND_PRE_RESTORE),
    ("ASA_AutoSave_", BACKUP_KIND_AUTO_SAVE),
)
SERVERS_DIR_NAME = "servers"
# Cluster transfer data (uploaded survivors, dinos and items). Without -ClusterDirOverride
# each install keeps it in its own Saved\clusters, so two installs with the same cluster ID
# still never see each other's uploads. The manager gives every cluster one shared folder.
CLUSTERS_DIR_NAME = "clusters"
SERVER_CLUSTERS_REL = Path(r"ShooterGame\Saved\clusters")
# Cluster data rides along in backups under this prefix. Restores never extract it: the
# folder is shared by every map in the cluster, so rolling it back would hit them all.
CLUSTER_ZIP_PREFIX = "ClusterData/"
# The ID goes on the command line unquoted, so keep it to characters that cannot split it.
CLUSTER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LOCKS_DIR_NAME = "locks"
GLOBAL_CONFIG_NAME = "global.json"
LEGACY_CONFIG_NAME = "config.json"
SERVER_CONFIG_NAME = "server.json"
DISCORD_STATE_FILE_NAME = "discord_state.json"

LOG_LEVEL = logging.INFO

AUTO_RESTART_EXIT_CODES = {3}
AUTO_RESTART_DELAY_SEC = 5.0

DIRECTX_LEGACY_DLLS = [
    "d3dx9_43.dll",
    "d3dx10_43.dll",
    "d3dx11_43.dll",
    "d3dcompiler_43.dll",
    "xinput1_3.dll",
]

GAMEUSERSETTINGS_REL = Path(r"ShooterGame\Saved\Config\WindowsServer\GameUserSettings.ini")
GAME_INI_REL = Path(r"ShooterGame\Saved\Config\WindowsServer\Game.ini")

# --- Single player (client) layout -------------------------------------------
# ASA keeps local/single player worlds in SavedArksLocal; dedicated servers use SavedArks.
SP_SAVED_ARKS_DIR_NAMES = ("SavedArksLocal", "SavedArks")
SERVER_SAVED_ARKS_DIR_NAME = "SavedArks"
SP_CONFIG_DIR_NAME = "Config"
SP_CONFIG_PLATFORM_DIRS = ("Windows", "WindowsNoEditor", "WindowsClient", "WindowsServer")
SP_CONFIG_SEARCH_DEPTH = 5
# The server rebuilds player data for its own accounts on its first save, so a single
# player survivor never comes across with the world; it has to travel through an obelisk.
# The tribe does come across, and its id is the .arktribe file name, which is exactly
# what TakeTribe needs to hand the old base and tames to the new survivor.
SP_TRIBE_ID_RE = re.compile(r"^\d{5,}$")

# ARK writes float32 spew: 0.00999999978 for 0.01, and its sliders emit 0.999989986 for 1.
# The client records its enabled CurseForge mods here; a server takes the same ids via -mods=.
SP_ACTIVE_MODS_KEY = "ActiveMods"
# These must never reach a server GameUserSettings.ini. ActiveMods is the old ASE
# mod mechanism, and an ASA server that sees it alongside -mods= deadlocks during
# world load: it reaches "Number of cores" in ~6s instead of ~36s and then spins
# forever on one thread without ever completing startup. The mod list belongs in
# the manager's Mods field, which is what -mods= is built from.
SP_MOD_KEYS_LOWER = {"activemods", "activemapmod"}

# Single player only. On a dedicated server this silently re-applies the hidden
# single player multipliers on top of the rates being imported.
SP_NEVER_IMPORT_KEYS = {"busesingleplayersettings"}

SP_FLOAT_SNAP_DECIMALS = 4
SP_FLOAT_SNAP_TOLERANCE = 2e-5
SP_FLOAT_SNAP_MIN_MAGNITUDE = 0.001
SP_FLOAT_MAX_EXACT = 1e7

# ASA installed through Steam keeps its saves inside the game folder, not LocalAppData.
ASA_STEAM_APP_DIR = "ARK Survival Ascended"

SP_WORLD_SUFFIXES = (".ark",)
SP_PROFILE_SUFFIXES = (".arkprofile", ".arkprofilebak", ".profilebak")
SP_TRIBE_SUFFIXES = (".arktribe", ".arktribebak", ".tribebak", ".arktributetribe")

# Client-only sections of a single player GameUserSettings.ini; never useful on a server.
SP_GUS_CLIENT_ONLY_SECTIONS = {
    "scalabilitygroups",
    "/script/engine.gameusersettings",
    "/script/engine.inputsettings",
    "/script/shootergame.shootergameusersettings",
    "/script/shootergame.shootergameinstance",
    "/script/shootergame.primalgameinstance",
    "clientsettings",
    "audiosettings",
    "windowssettings",
    "player.info",
    "startup",
    "marketing",
}

# Client-only sections of a single player Game.ini.
SP_GAME_CLIENT_ONLY_SECTIONS = {
    "/script/shootergame.shooterplayercontroller_menu",
    "/script/shootergame.shootergameusersettings",
}

# Keys this manager owns. An imported single player INI must never clobber them,
# or the server loses its name, passwords, RCON access or player slots.
SP_PROTECTED_INI_KEYS = {
    "serversettings": {
        "serveradminpassword",
        "serverpassword",
        "spectatorpassword",
        "rconenabled",
        "rconport",
    },
    "sessionsettings": {
        "sessionname",
        "port",
        "queryport",
        "multihome",
    },
    "/script/engine.gamesession": {
        "sessionname",
        "maxplayers",
    },
}

THEME_COLORS = {
    "bg": "#f5f7fb",
    "surface": "#ffffff",
    "border": "#d7deea",
    "text": "#1f2937",
    "muted": "#6b7280",
    "accent": "#2563eb",
    "accent_dark": "#1d4ed8",
    "accent_light": "#e6eefc",
    "warning": "#b45309",
    "console_bg": "#0b1220",
    "console_fg": "#e2e8f0",
    "console_select": "#1e293b",
}

# =============================================================================
# PATH / ICON HELPERS
# =============================================================================


@dataclass(frozen=True)
class RuntimeCliOptions:
    no_admin_prompt: bool = False


def parse_runtime_cli_options(argv: List[str]) -> RuntimeCliOptions:
    """
    Parses lightweight runtime flags before GUI/bootstrap starts.

    Supported flags:
      --no-admin-prompt
      -no-admin
    """
    no_admin_prompt = False
    unknown_flags: List[str] = []

    for raw in argv:
        arg = (raw or "").strip().lower()
        if arg in {"--no-admin-prompt", "-no-admin"}:
            no_admin_prompt = True
            continue
        if arg.startswith("-"):
            unknown_flags.append(raw)

    if unknown_flags:
        logging.getLogger(APP_NAME).warning(
            "Ignoring unknown CLI flags: %s",
            ", ".join(unknown_flags),
        )

    return RuntimeCliOptions(no_admin_prompt=no_admin_prompt)

def resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", None)  # type: ignore[name-defined]
    if base:
        return str(Path(base) / relative)
    return str(Path(__file__).resolve().parent / relative)


def parse_time_hhmm(value: str) -> Optional[dt_time]:
    if not value:
        return None
    clean = value.strip()
    if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", clean):
        return None
    hour_str, minute_str = clean.split(":")
    return dt_time(hour=int(hour_str), minute=int(minute_str))


def normalize_time_hhmm(value: str, fallback: str = DEFAULT_SCHEDULE_TIME) -> str:
    parsed = parse_time_hhmm(value)
    if not parsed:
        return fallback
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def parse_warning_minutes(raw: str) -> List[int]:
    """'15, 5, 1' -> [15, 5, 1]: whole minutes, largest first, each at most two hours."""
    out: Set[int] = set()
    for part in re.split(r"[,; ]+", raw or ""):
        if part.strip().isdigit():
            minutes = int(part)
            if 0 < minutes <= 120:
                out.add(minutes)
    return sorted(out, reverse=True)


def next_scheduled_datetime(now: datetime, schedule_time: dt_time) -> datetime:
    candidate = now.replace(
        hour=schedule_time.hour,
        minute=schedule_time.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def set_windows_appusermodel_id(app_id: str) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _set_tk_window_icon_from_ico(root: tk.Tk, ico_path: str) -> None:
    """
    Ensures icon is applied for the actual window (WM_SETICON), not only Tk metadata.
    This fixes cases where taskbar/window still shows generic icon.
    """
    if os.name != "nt":
        return

    try:
        hwnd = root.winfo_id()
        user32 = ctypes.windll.user32

        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040

        hicon_small = user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        hicon_big = user32.LoadImageW(None, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE | LR_DEFAULTSIZE)

        if hicon_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
        if hicon_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
    except Exception:
        pass


def _set_tk_window_icon_from_exe(root: tk.Tk) -> None:
    """
    Fallback: Extract icon from the running EXE (PyInstaller onefile) and apply via WM_SETICON.
    """
    if os.name != "nt":
        return
    try:
        hwnd = root.winfo_id()
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32

        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1

        exe_path = os.path.abspath(sys.executable)  # type: ignore[name-defined]

        large = (ctypes.c_void_p * 1)()
        small = (ctypes.c_void_p * 1)()

        n = shell32.ExtractIconExW(exe_path, 0, large, small, 1)
        if n <= 0:
            return

        if small[0]:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small[0])
        if large[0]:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, large[0])
    except Exception:
        pass


def apply_window_icon(root: tk.Tk) -> None:
    """
    Priority:
      1) assets/app.ico (when running from sources or bundled as data)
      2) icon extracted from sys.executable (PyInstaller --icon)
    """
    ico = resource_path(r"assets\app.ico")
    try:
        if Path(ico).exists():
            root.iconbitmap(ico)
            _set_tk_window_icon_from_ico(root, ico)
        else:
            _set_tk_window_icon_from_exe(root)
    except Exception:
        _set_tk_window_icon_from_exe(root)


def configure_modern_theme(root: tk.Tk) -> None:
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")

    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family="Segoe UI", size=10)
    tkfont.nametofont("TkTextFont").configure(family="Segoe UI", size=10)
    tkfont.nametofont("TkHeadingFont").configure(family="Segoe UI Semibold", size=10)

    theme = THEME_COLORS
    root.configure(bg=theme["bg"])

    style.configure(
        ".",
        background=theme["bg"],
        foreground=theme["text"],
        bordercolor=theme["border"],
        lightcolor=theme["border"],
        darkcolor=theme["border"],
        troughcolor=theme["bg"],
        focuscolor=theme["accent"],
        borderwidth=1,
    )

    style.configure("TFrame", background=theme["bg"])
    style.configure("TLabel", background=theme["bg"], foreground=theme["text"])
    style.configure("TSeparator", background=theme["border"])

    style.configure("TLabelframe", background=theme["bg"], bordercolor=theme["border"])
    style.configure(
        "TLabelframe.Label",
        background=theme["bg"],
        foreground=theme["text"],
        font=("Segoe UI Semibold", 10),
    )

    style.configure(
        "TButton",
        background=theme["surface"],
        foreground=theme["text"],
        padding=(12, 6),
        relief="flat",
        borderwidth=1,
    )
    style.map(
        "TButton",
        background=[
            ("active", theme["accent_light"]),
            ("pressed", theme["accent_light"]),
            ("disabled", theme["bg"]),
        ],
        foreground=[("disabled", theme["muted"])],
    )

    style.configure(
        "TEntry",
        fieldbackground=theme["surface"],
        foreground=theme["text"],
        padding=4,
    )
    style.configure(
        "TCombobox",
        fieldbackground=theme["surface"],
        background=theme["surface"],
        foreground=theme["text"],
        padding=4,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", theme["surface"])],
        foreground=[("readonly", theme["text"])],
        selectbackground=[("readonly", theme["accent_light"])],
    )

    style.configure("TCheckbutton", background=theme["bg"], foreground=theme["text"])
    style.configure("TRadiobutton", background=theme["bg"], foreground=theme["text"])
    style.configure("TScale", background=theme["bg"], troughcolor=theme["border"])

    style.configure("TNotebook", background=theme["bg"], borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=theme["bg"],
        foreground=theme["muted"],
        padding=(14, 8),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", theme["surface"])],
        foreground=[("selected", theme["text"])],
    )

    style.configure(
        "Treeview",
        background=theme["surface"],
        fieldbackground=theme["surface"],
        foreground=theme["text"],
        rowheight=24,
        bordercolor=theme["border"],
    )
    style.map(
        "Treeview",
        background=[("selected", theme["accent"])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Treeview.Heading",
        background=theme["bg"],
        foreground=theme["text"],
        relief="flat",
        padding=(8, 6),
    )

    style.configure(
        "Vertical.TScrollbar",
        background=theme["border"],
        troughcolor=theme["bg"],
        arrowcolor=theme["text"],
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=theme["border"],
        troughcolor=theme["bg"],
        arrowcolor=theme["text"],
    )


def configure_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def apply_tk_scaling(root: tk.Tk) -> None:
    try:
        dpi = root.winfo_fpixels("1i")
        scaling = max(1.0, dpi / 72.0)
        root.tk.call("tk", "scaling", scaling)
    except Exception:
        pass


def _scaled_dimension(root: tk.Tk, size: int) -> int:
    try:
        scaling = float(root.tk.call("tk", "scaling"))
    except Exception:
        scaling = 1.0
    return max(1, int(size * scaling))


class HoverTooltip:
    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: Optional[str] = None
        self._tip_window: Optional[tk.Toplevel] = None

        self.widget.bind("<Enter>", self._schedule, add="+")
        self.widget.bind("<Leave>", self._hide, add="+")
        self.widget.bind("<ButtonPress>", self._hide, add="+")
        self.widget.bind("<FocusOut>", self._hide, add="+")

    def _schedule(self, _event: tk.Event) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip_window or not self.text:
            return
        theme = THEME_COLORS
        self._tip_window = tk.Toplevel(self.widget)
        self._tip_window.overrideredirect(True)
        try:
            self._tip_window.attributes("-topmost", True)
        except Exception:
            pass

        frame = tk.Frame(
            self._tip_window,
            background=theme["surface"],
            borderwidth=1,
            relief="solid",
        )
        label = tk.Label(
            frame,
            text=self.text,
            background=theme["surface"],
            foreground=theme["text"],
            padx=8,
            pady=4,
        )
        label.pack()
        frame.pack()

        self._tip_window.update_idletasks()
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip_window.geometry(f"+{x}+{y}")

    def _hide(self, _event: tk.Event) -> None:
        self._cancel()
        if self._tip_window:
            try:
                self._tip_window.destroy()
            except Exception:
                pass
            self._tip_window = None


def apply_min_window_size(root: tk.Tk, width: int, height: int) -> None:
    scaled_w = _scaled_dimension(root, width)
    scaled_h = _scaled_dimension(root, height)
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    root.minsize(min(scaled_w, screen_w), min(scaled_h, screen_h))


def apply_initial_window_geometry(root: tk.Tk, width: int, height: int) -> None:
    scaled_w = _scaled_dimension(root, width)
    scaled_h = _scaled_dimension(root, height)
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    target_w = min(scaled_w, int(screen_w * 0.95))
    target_h = min(scaled_h, int(screen_h * 0.9))
    root.geometry(f"{target_w}x{target_h}")

# =============================================================================
# LOW-LEVEL UTILITIES
# =============================================================================

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    if os.name != "nt":
        return False
    if is_admin():
        return True
    try:
        exe = sys.executable  # type: ignore[name-defined]
        script = os.path.abspath(sys.argv[0])  # type: ignore[name-defined]
        params = f'"{script}" ' + " ".join([f'"{a}"' for a in sys.argv[1:]])  # type: ignore[name-defined]
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return int(rc) > 32
    except Exception:
        return False


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def now_ts() -> str:
    return time.strftime("%Y-%m-%d_%H-%M-%S")


def reserve_zip_path(folder: Path, stem: str) -> Path:
    """A new zip path in folder that no other writer can take: created here, exclusively.

    Several servers can back up in the same second into one folder; the timestamp alone
    would hand them the same name, and two writers on one zip silently lose one backup.
    """
    ensure_dir(folder)
    for n in range(1000):
        path = folder / (f"{stem}.zip" if n == 0 else f"{stem}-{n}.zip")
        try:
            with open(path, "xb"):
                return path
        except FileExistsError:
            continue
    raise RuntimeError(f"No free backup file name for {stem} in {folder}")


def format_duration(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds))
    mins, sec = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    days, hrs = divmod(hrs, 24)
    if days > 0:
        return f"{days}d {hrs}h {mins}m"
    if hrs > 0:
        return f"{hrs}h {mins}m"
    return f"{mins}m {sec}s"


def safe_int(s: Any, default: int) -> int:
    try:
        return int(str(s).strip())
    except Exception:
        return default


def safe_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("1", "true", "yes", "y", "on"):
            return True
        if t in ("0", "false", "no", "n", "off"):
            return False
    return default


def file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(str(tmp), str(path))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(str(tmp), str(path))


def open_folder(path: Path) -> None:
    ensure_dir(path)
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass


def open_in_explorer(path: Path, select_file: bool = True) -> None:
    try:
        if os.name == "nt":
            if select_file and path.exists() and path.is_file():
                subprocess.Popen(["explorer.exe", "/select,", str(path)], creationflags=CREATE_NO_WINDOW)
                return
            target = path if path.is_dir() else path.parent
            ensure_dir(target)
            subprocess.Popen(["explorer.exe", str(target)], creationflags=CREATE_NO_WINDOW)
            return
    except Exception:
        pass


def _find_powershell() -> Optional[str]:
    if os.name != "nt":
        return None
    candidates = ["powershell.exe", "powershell", "pwsh.exe", "pwsh"]
    for c in candidates:
        try:
            p = subprocess.run(["where", c], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
            if p.returncode == 0:
                return c
        except Exception:
            continue
    return "powershell.exe"


def _is_writable_dir(p: Path) -> bool:
    try:
        ensure_dir(p)
        t = p / f".write_test_{os.getpid()}_{int(time.time())}"
        t.write_text("ok", encoding="utf-8")
        t.unlink()
        return True
    except Exception:
        return False


def _portable_base_dir() -> Path:
    try:
        if getattr(sys, "frozen", False):  # type: ignore[name-defined]
            return Path(sys.executable).resolve().parent  # type: ignore[name-defined]
        return Path(__file__).resolve().parent
    except Exception:
        return Path.cwd()


def _programdata_base() -> Optional[Path]:
    if os.name != "nt":
        return None
    base = os.getenv("PROGRAMDATA") or r"C:\ProgramData"
    return Path(base) / APPDATA_DIR_NAME


def _appdata_base() -> Path:
    base = os.getenv("APPDATA") or str(Path.home())
    return Path(base) / APPDATA_DIR_NAME


def resolve_storage_root() -> Path:
    if os.name != "nt":
        user = _appdata_base()
        if _is_writable_dir(user):
            return user
        portable = _portable_base_dir() / APPDATA_DIR_NAME
        ensure_dir(portable)
        return portable

    shared = _programdata_base()
    if not shared:
        user = _appdata_base()
        if _is_writable_dir(user):
            return user
        portable = _portable_base_dir() / APPDATA_DIR_NAME
        ensure_dir(portable)
        return portable

    try:
        ensure_dir(shared)
    except Exception:
        user = _appdata_base()
        if _is_writable_dir(user):
            return user
        portable = _portable_base_dir() / APPDATA_DIR_NAME
        ensure_dir(portable)
        return portable

    if _is_writable_dir(shared):
        return shared

    if is_admin():
        ensure_programdata_acl(shared)
        if _is_writable_dir(shared):
            return shared

    raise PermissionError(
        f"Shared storage at {shared} is not writable. Admin rights are required to initialize shared storage."
    )


def ensure_programdata_acl(shared_root: Path, logger: Optional[logging.Logger] = None) -> None:
    if os.name != "nt" or not is_admin():
        return
    try:
        ensure_dir(shared_root)
        cmd = ["icacls", str(shared_root), "/grant", "Users:(OI)(CI)M", "/T", "/C"]
        subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if logger:
            logger.info(f"Shared storage ACL ensured: {shared_root}")
    except Exception as e:
        if logger:
            logger.info(f"Shared storage ACL could not be ensured: {e}")


def run_quiet(cmd: List[str], cwd: Optional[Path] = None) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return p.returncode, out.strip()
    except Exception as e:
        return 1, str(e)


def stream_process_output(
    cmd: List[str],
    logger: logging.Logger,
    cwd: Optional[Path] = None,
    timeout_s: int = 0,
    log_prefix: str = "",
) -> Tuple[int, str]:
    """
    Streams stdout/stderr live into logger (GUI + file) AND returns full combined output.
    Handles SteamCMD progress that uses carriage returns (\r).
    """
    logger.info(" ".join(cmd))
    p = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert p.stdout is not None

    start = time.time()
    buf = b""
    collected: List[str] = []
    last_line = ""
    last_emit_t = 0.0

    def emit(line: str) -> None:
        nonlocal last_line, last_emit_t
        s = line.strip("\r\n")
        if not s:
            return
        # light throttle for rapidly updating progress lines
        now = time.time()
        if s == last_line and (now - last_emit_t) < 0.25:
            return
        last_line = s
        last_emit_t = now
        msg = f"{log_prefix}{s}" if log_prefix else s
        collected.append(msg)
        logger.info(msg)

    while True:
        if timeout_s > 0 and (time.time() - start) > timeout_s:
            try:
                p.kill()
            except Exception:
                pass
            emit("Process timeout -> killed.")
            break

        chunk = p.stdout.read(4096)
        if not chunk:
            break

        buf += chunk
        # split on \n or \r to capture progress updates
        while True:
            m = re.search(br"[\r\n]", buf)
            if not m:
                break
            idx = m.start()
            line = buf[:idx]
            sep = buf[idx:idx+1]
            buf = buf[idx+1:]
            try:
                emit(line.decode("utf-8", errors="replace"))
            except Exception:
                pass
            if sep == b"\r":
                continue

    if buf:
        try:
            emit(buf.decode("utf-8", errors="replace"))
        except Exception:
            pass

    code = p.wait()
    return code, "\n".join(collected)


def server_root(app_base: Path, server_id: str) -> Path:
    return app_base / SERVERS_DIR_NAME / server_id


def server_config_path(app_base: Path, server_id: str) -> Path:
    return server_root(app_base, server_id) / SERVER_CONFIG_NAME


def server_lock_path(app_base: Path, server_id: str) -> Path:
    return server_root(app_base, server_id) / LOCKS_DIR_NAME / "server.lock"


def global_lock_path(app_base: Path) -> Path:
    return app_base / LOCKS_DIR_NAME / "global.lock"


def server_discord_state_path(app_base: Path, server_id: str) -> Path:
    return server_root(app_base, server_id) / DISCORD_STATE_FILE_NAME

# =============================================================================
# DOWNLOAD (urllib only)
# =============================================================================

def download_file(url: str, dest: Path, logger: logging.Logger, timeout: int = DOWNLOAD_TIMEOUT_SEC) -> None:
    ensure_dir(dest.parent)
    logger.info(f"Downloading: {url}")

    ctx = ssl.create_default_context()
    try:
        import certifi  # type: ignore
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass

    req = Request(url, headers={"User-Agent": "ARK-ASA-Manager/1.0"})
    with urlopen(req, timeout=timeout, context=ctx) as r:
        data = r.read()

    if not data:
        raise RuntimeError("Empty download response")

    atomic_write_bytes(dest, data)
    logger.info(f"Saved: {dest}")

# =============================================================================
# DPAPI (Discord secrets)
# =============================================================================

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(data: bytes) -> DATA_BLOB:
    buf = (ctypes.c_byte * len(data)).from_buffer_copy(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))


def dpapi_encrypt(plain: str) -> str:
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    data_in = _blob_from_bytes(plain.encode("utf-8"))
    data_out = DATA_BLOB()
    flags = 0x4  # CRYPTPROTECT_LOCAL_MACHINE

    if not crypt32.CryptProtectData(ctypes.byref(data_in), None, None, None, None, flags, ctypes.byref(data_out)):
        raise RuntimeError("DPAPI encrypt failed.")

    try:
        out = ctypes.string_at(data_out.pbData, data_out.cbData)
        return base64.b64encode(out).decode("ascii")
    finally:
        kernel32.LocalFree(data_out.pbData)


def dpapi_decrypt(enc_b64: str) -> str:
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    raw = base64.b64decode(enc_b64.encode("ascii"))
    data_in = _blob_from_bytes(raw)
    data_out = DATA_BLOB()

    if not crypt32.CryptUnprotectData(ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out)):
        raise RuntimeError("DPAPI decrypt failed.")

    try:
        out = ctypes.string_at(data_out.pbData, data_out.cbData)
        return out.decode("utf-8", errors="replace")
    finally:
        kernel32.LocalFree(data_out.pbData)

# =============================================================================
# CONFIG
# =============================================================================

@dataclass
class ServerRef:
    id: str
    display_name: str
    server_dir: str


@dataclass
class GlobalConfig:
    schema_version: int = 1
    steamcmd_dir: str = DEFAULT_STEAMCMD_DIR
    last_selected_server_id: str = ""
    start_on_startup: bool = False
    servers: List[ServerRef] = field(default_factory=list)
    # Per cluster ID: {"name": display name, "start_stagger_sec": seconds between starts}.
    # Which profiles belong to a cluster is not stored here: each profile's own cluster
    # settings say so, and this only adds what a cluster has beyond its members.
    clusters: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class AppConfig:
    schema_version: int = 13
    server_dir: str = DEFAULT_SERVER_DIR

    map_name: str = DEFAULT_MAP
    server_name: str = DEFAULT_SERVER_NAME

    port: int = DEFAULT_PORT
    query_port: int = DEFAULT_QUERY_PORT
    max_players: int = DEFAULT_MAX_PLAYERS

    server_platform: str = ""  # e.g. "PC+XSX+WINGDK" (moved to Advanced tab)

    join_password: str = ""
    admin_password: str = "AdminPassword"

    enable_battleye: bool = False

    enable_rcon: bool = True
    rcon_host: str = DEFAULT_RCON_HOST
    rcon_port: int = DEFAULT_RCON_PORT

    automanaged_mods: bool = True
    mods: str = ""  # stored as CSV

    validate_on_update: bool = False

    backup_on_stop: bool = True
    backup_dir: str = ""
    backup_retention: int = 20
    auto_save_enabled: bool = False
    auto_save_interval_minutes: int = AUTO_SAVE_DEFAULT_INTERVAL_MIN
    auto_save_retention: int = AUTO_SAVE_DEFAULT_KEEP
    backup_include_configs: bool = True

    auto_update_restart: bool = False
    auto_update_time: str = DEFAULT_SCHEDULE_TIME
    # A daily restart of a running server, and in-game warnings before planned restarts
    # (that one and the nightly update) at these minutes before.
    scheduled_restart_enabled: bool = False
    scheduled_restart_time: str = "06:00"
    restart_warnings_enabled: bool = True
    restart_warning_minutes: str = "15,5,1"
    update_on_startup: bool = False

    install_optional_certificates: bool = True  # enforced True

    # Console noise filter (GUI only)
    hide_gameanalytics_console_logs: bool = True

    rcon_saved_commands: List[str] = field(default_factory=lambda: [
        "SaveWorld",
        "DoExit",
        "ListPlayers",
        "DestroyWildDinos",
    ])

    # Advanced Start Args
    cluster_enable: bool = False
    cluster_id: str = ""
    cluster_custom_path_enable: bool = False
    cluster_dir_override: str = ""
    no_transfer_from_filtering: bool = False
    alt_save_directory_name: str = ""

    dino_mode: str = ""

    log_servergamelog: bool = False
    log_servergamelogincludetribelogs: bool = False
    log_serverrconoutputtribelogs: bool = False

    mech_disablecustomcosmetics: bool = False
    mech_autodestroystructures: bool = False
    mech_forcerespawndinos: bool = False
    mech_nowildbabies: bool = False
    mech_forceallowcaveflyers: bool = False
    mech_disabledinonetrangescaling: bool = False
    mech_unstasisdinoobstructioncheck: bool = False
    mech_alwaystickdedicatedskeletalmeshes: bool = False
    mech_disablecharactertracker: bool = False
    mech_useservernetspeedcheck: bool = False
    mech_stasiskeepcontrollers: bool = False
    mech_ignoredupeditems: bool = False

    custom_start_args: str = ""

    discord_enable: bool = False
    discord_webhook_url: str = ""
    discord_webhook_url_enc: str = ""
    discord_poll_interval_sec: int = 300
    discord_notify_start: bool = True
    discord_notify_stop: bool = True
    discord_notify_join: bool = True
    discord_notify_leave: bool = False
    discord_notify_crash: bool = True
    discord_include_player_id: bool = False
    discord_mention_mode: str = "name"
    discord_mention_map_json: str = ""

    def normalized_mods(self) -> str:
        raw = (self.mods or "").strip()
        if not raw:
            return ""
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return ",".join(parts)


@dataclass
class ConfigLoadResult:
    cfg: AppConfig
    migrated: bool
    warnings: List[str]

@dataclass
class GlobalConfigLoadResult:
    cfg: GlobalConfig
    migrated: bool
    warnings: List[str]


class ServerConfigStore:
    def __init__(self, path: Path, lock_path: Path):
        self.path = path
        self.lock_path = lock_path
        self._extra: Dict[str, Any] = {}

    def load(self) -> ConfigLoadResult:
        cfg = AppConfig()
        migrated = False
        warnings: List[str] = []

        if not self.path.exists():
            migrated = True
            return ConfigLoadResult(cfg=cfg, migrated=migrated, warnings=warnings)

        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Config root is not a JSON object")
        except Exception:
            try:
                bad = self.path.with_name(f"config.corrupt.{now_ts()}.json")
                shutil.copy2(self.path, bad)
                warnings.append(f"Config was corrupt -> copied to: {bad}")
            except Exception:
                warnings.append("Config was corrupt -> could not copy quarantine file")
            migrated = True
            return ConfigLoadResult(cfg=cfg, migrated=migrated, warnings=warnings)

        known = {f.name: f for f in fields(AppConfig)}
        self._extra = {k: v for k, v in data.items() if k not in known}
        for deprecated in ("auto_start_schedule", "auto_start_time"):
            self._extra.pop(deprecated, None)

        for name, fdef in known.items():
            if name not in data:
                continue
            v = data.get(name)
            try:
                cur = getattr(cfg, name)
                if isinstance(cur, bool):
                    setattr(cfg, name, safe_bool(v, cur))
                elif isinstance(cur, int):
                    setattr(cfg, name, safe_int(v, cur))
                elif isinstance(cur, list):
                    setattr(cfg, name, v if isinstance(v, list) else cur)
                else:
                    setattr(cfg, name, v if isinstance(v, type(cur)) else str(v))
            except Exception:
                pass

        cfg.install_optional_certificates = True

        enc_webhook = (cfg.discord_webhook_url_enc or "").strip()
        if enc_webhook:
            try:
                cfg.discord_webhook_url = dpapi_decrypt(enc_webhook)
            except Exception:
                cfg.discord_webhook_url = ""
                warnings.append("Discord webhook could not be decrypted. Please re-enter the URL.")

        if not isinstance(cfg.rcon_saved_commands, list):
            cfg.rcon_saved_commands = ["SaveWorld", "DoExit", "ListPlayers", "DestroyWildDinos"]

        current_schema = AppConfig().schema_version
        if safe_int(getattr(cfg, "schema_version", 0), 0) != current_schema:
            cfg.schema_version = current_schema
            migrated = True

        for name in known.keys():
            if name not in data:
                migrated = True
                break

        return ConfigLoadResult(cfg=cfg, migrated=migrated, warnings=warnings)

    @staticmethod
    def _serialize(cfg: AppConfig) -> Dict[str, Any]:
        """The profile as written to disk: the Discord webhook only ever encrypted."""
        base = asdict(cfg)
        webhook_raw = (cfg.discord_webhook_url or "").strip()
        if webhook_raw:
            try:
                base["discord_webhook_url_enc"] = dpapi_encrypt(webhook_raw)
            except Exception as exc:
                raise RuntimeError(f"Failed to encrypt Discord webhook URL: {exc}") from exc
        else:
            base["discord_webhook_url_enc"] = ""
        base["discord_webhook_url"] = ""
        return base

    def save(self, cfg: AppConfig) -> None:
        with exclusive_file_lock(self.lock_path):
            ensure_dir(self.path.parent)
            base = self._serialize(cfg)
            for k, v in self._extra.items():
                if k not in base:
                    base[k] = v
            if self.path.exists():
                try:
                    bak = self.path.with_name(f"server.bak.{now_ts()}.json")
                    shutil.copy2(self.path, bak)
                except Exception:
                    pass
            atomic_write_text(self.path, json.dumps(base, indent=2), encoding="utf-8")

    def save_new(self, cfg: AppConfig) -> None:
        with exclusive_file_lock(self.lock_path):
            if self.path.exists():
                raise FileExistsError(str(self.path))
            ensure_dir(self.path.parent)
            # New profiles are often copies of one with a webhook; it must not land in clear text.
            base = self._serialize(cfg)
            atomic_write_text(self.path, json.dumps(base, indent=2), encoding="utf-8")


class GlobalConfigStore:
    def __init__(self, path: Path, lock_path: Path):
        self.path = path
        self.lock_path = lock_path
        self._extra: Dict[str, Any] = {}

    def load(self) -> GlobalConfigLoadResult:
        cfg = GlobalConfig()
        migrated = False
        warnings: List[str] = []

        if not self.path.exists():
            migrated = True
            return GlobalConfigLoadResult(cfg=cfg, migrated=migrated, warnings=warnings)

        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Global config root is not a JSON object")
        except Exception:
            try:
                bad = self.path.with_name(f"global.corrupt.{now_ts()}.json")
                shutil.copy2(self.path, bad)
                warnings.append(f"Global config was corrupt -> copied to: {bad}")
            except Exception:
                warnings.append("Global config was corrupt -> could not copy quarantine file")
            migrated = True
            return GlobalConfigLoadResult(cfg=cfg, migrated=migrated, warnings=warnings)

        known = {"schema_version", "steamcmd_dir", "last_selected_server_id", "start_on_startup", "servers", "clusters"}
        self._extra = {k: v for k, v in data.items() if k not in known}

        cfg.schema_version = safe_int(data.get("schema_version", cfg.schema_version), cfg.schema_version)
        cfg.steamcmd_dir = str(data.get("steamcmd_dir") or cfg.steamcmd_dir)
        cfg.last_selected_server_id = str(data.get("last_selected_server_id") or "")
        cfg.start_on_startup = safe_bool(data.get("start_on_startup"), cfg.start_on_startup)
        clusters_raw = data.get("clusters")
        if isinstance(clusters_raw, dict):
            cfg.clusters = {str(k): dict(v) for k, v in clusters_raw.items() if isinstance(v, dict)}

        servers_raw = data.get("servers")
        if isinstance(servers_raw, list):
            for item in servers_raw:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("id") or "").strip()
                sdir = str(item.get("server_dir") or "").strip()
                if not sid or not sdir:
                    continue
                name = str(item.get("display_name") or sid).strip()
                cfg.servers.append(ServerRef(id=sid, display_name=name, server_dir=sdir))
        else:
            migrated = True

        if cfg.schema_version != GlobalConfig().schema_version:
            cfg.schema_version = GlobalConfig().schema_version
            migrated = True

        return GlobalConfigLoadResult(cfg=cfg, migrated=migrated, warnings=warnings)

    def save(self, cfg: GlobalConfig) -> None:
        with exclusive_file_lock(self.lock_path):
            ensure_dir(self.path.parent)
            base = asdict(cfg)
            for k, v in self._extra.items():
                if k not in base:
                    base[k] = v
            if self.path.exists():
                try:
                    bak = self.path.with_name(f"global.bak.{now_ts()}.json")
                    shutil.copy2(self.path, bak)
                except Exception:
                    pass
            atomic_write_text(self.path, json.dumps(base, indent=2), encoding="utf-8")

# =============================================================================
# DEPENDENCIES
# =============================================================================

def reg_open_key_64(root: Any, subkey: str) -> Any:
    if winreg is None:
        raise FileNotFoundError("winreg unavailable")
    access = winreg.KEY_READ
    try:
        access |= winreg.KEY_WOW64_64KEY  # type: ignore[attr-defined]
    except Exception:
        pass
    return winreg.OpenKey(root, subkey, 0, access)


def vc14_x64_version() -> Optional[str]:
    if winreg is None:
        return None
    try:
        key_path = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
        with reg_open_key_64(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            installed, _ = winreg.QueryValueEx(key, "Installed")
            version, _ = winreg.QueryValueEx(key, "Version")
            if int(installed) == 1:
                return str(version)
            return None
    except Exception:
        return None


def directx_registry_present() -> bool:
    if winreg is None:
        return False
    try:
        with reg_open_key_64(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\DirectX"):
            return True
    except Exception:
        return False


def has_directx_legacy(min_hits: int = 1) -> bool:
    if os.name != "nt":
        return False
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [Path(windir) / "System32", Path(windir) / "SysWOW64"]
    hits = 0
    for folder in candidates:
        for dll in DIRECTX_LEGACY_DLLS:
            if (folder / dll).exists():
                hits += 1
    return hits >= min_hits


def install_vcredist(logger: logging.Logger) -> None:
    temp = Path(os.environ.get("TEMP", str(Path.home() / "AppData/Local/Temp")))
    exe = temp / "vc_redist.x64.exe"
    download_file(VC_REDIST_X64_URL, exe, logger)
    code, _ = stream_process_output([str(exe), "/install", "/passive", "/norestart"], logger)
    logger.info(f"VC++ installer exit code: {code}")


def install_directx_web(logger: logging.Logger) -> None:
    temp = Path(os.environ.get("TEMP", str(Path.home() / "AppData/Local/Temp")))
    exe = temp / "dxwebsetup.exe"
    download_file(DXWEBSETUP_URL, exe, logger)
    code, _ = stream_process_output([str(exe), "/Q"], logger)
    logger.info(f"DirectX web installer exit code: {code}")


def install_asa_certificates(logger: logging.Logger) -> None:
    """
    Mandatory:
    - Downloads AmazonRootCA1 + r2m02 and imports into Windows cert store.
    - PowerShell Import-Certificate (Defender friendlier than certutil).
    """
    if os.name != "nt":
        logger.info("Certificate install skipped: only supported on Windows.")
        return

    ps_exe = _find_powershell()
    if not ps_exe:
        raise RuntimeError("PowerShell not found; cannot import certificates.")

    temp = Path(os.environ.get("TEMP", str(Path.home() / "AppData/Local/Temp")))
    root_path = temp / f"AmazonRootCA1_{os.getpid()}.cer"
    r2m02_path = temp / f"r2m02_{os.getpid()}.cer"

    download_file(AMAZON_ROOT_CA1_URL, root_path, logger)
    download_file(AMAZON_R2M02_URL, r2m02_path, logger)

    if is_admin():
        root_store = r"Cert:\LocalMachine\Root"
        ca_store = r"Cert:\LocalMachine\CA"
        logger.info("Certificate import: LocalMachine store.")
    else:
        root_store = r"Cert:\CurrentUser\Root"
        ca_store = r"Cert:\CurrentUser\CA"
        logger.info("Certificate import: CurrentUser store (non-admin).")

    def import_cert(cer_path: Path, store: str) -> None:
        ps = f"Import-Certificate -FilePath '{str(cer_path)}' -CertStoreLocation '{store}' | Out-Null"
        code, out = run_quiet([ps_exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps])
        if code != 0:
            msg = out.splitlines()[-1].strip() if out else "unknown Import-Certificate error"
            raise RuntimeError(f"Import-Certificate failed for {cer_path.name} -> {store}: {msg}")

    logger.info("Installing certificates: AmazonRootCA1 -> Root, r2m02 -> CA")
    import_cert(root_path, root_store)
    import_cert(r2m02_path, ca_store)
    logger.info("Certificates installed/updated successfully.")

    try:
        root_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        r2m02_path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass

# =============================================================================
# SteamCMD canonical path + lock + runscript
# =============================================================================

@contextlib.contextmanager
def exclusive_file_lock(lock_path: Path, timeout_s: int = 900, poll_s: float = 0.25):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+", encoding="utf-8")
    start = time.time()

    if os.name == "nt":
        import msvcrt
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.time() - start > timeout_s:
                    raise TimeoutError(f"Timeout waiting for lock: {lock_path}")
                time.sleep(poll_s)
        try:
            yield
        finally:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                fh.close()
    else:
        try:
            yield
        finally:
            fh.close()


def resolve_steamcmd_exe(steamcmd_root: str) -> Path:
    root = Path(steamcmd_root)
    candidates = [
        root / "steamcmd.exe",
        root / "SteamCMD" / "steamcmd.exe",
        root / "steamcmd" / "steamcmd.exe",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_real = dest_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        for zi in zf.infolist():
            if zi.is_dir():
                continue
            target = (dest_dir / zi.filename).resolve()
            if not str(target).startswith(str(dest_real)):
                raise RuntimeError(f"Blocked unsafe zip path: {zi.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(zi, "r") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def ensure_steamcmd(steamcmd_root: str, logger: logging.Logger) -> Path:
    exe = resolve_steamcmd_exe(steamcmd_root)
    root = exe.parent
    ensure_dir(root)

    if exe.is_file():
        # verify it boots at least once
        code, _ = stream_process_output([str(exe), "+quit"], logger, cwd=root, log_prefix="[SteamCMD] ")
        if code in (0, 7, 8):
            # SteamCMD can return 7/8 during first bootstrap/self-update; treat as OK.
            if code != 0:
                logger.warning("[SteamCMD] boot returned exit=%s (bootstrap/self-update).", code)
            return exe
        logger.warning("[SteamCMD] steamcmd.exe exists but boot returned code=%s -> reinstalling", code)

    zip_path = root / "steamcmd.zip"
    try:
        if zip_path.exists():
            zip_path.unlink()
    except Exception:
        pass

    download_file(STEAMCMD_ZIP_URL, zip_path, logger)
    safe_extract_zip(zip_path, root)
    try:
        zip_path.unlink()
    except Exception:
        pass

    exe = resolve_steamcmd_exe(str(root))
    if not exe.is_file():
        raise FileNotFoundError(f"SteamCMD extraction failed: steamcmd.exe not found in {root}")

    # final boot (SteamCMD may self-update/bootstrap on first run and return non-zero transiently)
    code, _ = stream_process_output([str(exe), "+quit"], logger, cwd=root, log_prefix="[SteamCMD] ")
    if code != 0:
        # IMPORTANT: do NOT hard-fail here. Update/Validate succeeds moments later because SteamCMD stabilizes.
        # Also use f-string to avoid any logger %-formatting argument mismatch.
        logger.warning(f"[SteamCMD] boot returned exit={code} after fresh install (bootstrap/self-update). Continuing...")

    return exe


def unstick_install_dir(install_dir: Path, app_id: int, logger: logging.Logger) -> bool:
    steamapps = install_dir / "steamapps"
    manifest = steamapps / f"appmanifest_{app_id}.acf"
    downloading = steamapps / "downloading" / str(app_id)
    tempdir = steamapps / "temp"

    changed = False
    if manifest.exists():
        backup_dir = steamapps / "_repair_backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"appmanifest_{app_id}.acf.{ts}.bak"
        try:
            os.chmod(manifest, stat.S_IWRITE)
        except Exception:
            pass
        shutil.move(str(manifest), str(target))
        logger.warning("[SteamCMD] Moved stale manifest -> %s", target)
        changed = True

    for p in [downloading, tempdir]:
        if p.exists():
            try:
                shutil.rmtree(p, ignore_errors=True)
                logger.warning("[SteamCMD] Removed stale folder -> %s", p)
                changed = True
            except Exception:
                pass

    return changed

def steamcmd_output_has_fatal(out: str) -> bool:
    """
    SteamCMD sometimes exits 0 but prints error text (rare).
    We treat the run as failed if strong fatal markers are present.
    """
    if not out:
        return False

    fatal_patterns = [
        r"\bERROR!\b",
        r"\bFAILED\b",
        r"Failed to install app",
        r"Invalid Password",
        r"No subscription",
        r"Login Failure",
        r"Timed out",
        r"Disk write failure",
        r"Missing file privileges",
    ]
    for pat in fatal_patterns:
        if re.search(pat, out, re.IGNORECASE):
            return True
    return False


def steamcmd_verify_install(install_dir: Path, app_id: int) -> bool:
    """
    Minimal verification that an update actually produced artifacts.
    """
    manifest = install_dir / "steamapps" / f"appmanifest_{app_id}.acf"
    exe = install_dir / "ShooterGame" / "Binaries" / "Win64" / "ArkAscendedServer.exe"
    return manifest.exists() or exe.exists()

def copy_file_atomic(src: Path, dst: Path) -> bool:
    """Copy one file through a temp name, never over an existing file.

    A copy cut short leaves no truncated file that a later "existing files win" rule would
    keep for good, and os.rename refuses to overwrite on Windows, so a file someone else
    wrote meanwhile wins. False when dst already existed.
    """
    if dst.exists():
        return False
    ensure_dir(dst.parent)
    part = dst.with_name(dst.name + ".part")
    if part.exists():  # left over from a copy that was killed; it may carry a read-only bit
        with contextlib.suppress(OSError):
            os.chmod(part, stat.S_IWRITE)
        with contextlib.suppress(OSError):
            part.unlink()
    try:
        shutil.copy2(src, part)
        os.rename(part, dst)
    except FileExistsError:
        with contextlib.suppress(OSError):
            part.unlink()
        return False
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise
    return True


# Only the game's own folders at the top of an install are copied: anything else there (a
# backup folder, notes, other tools) belongs to that server or its owner.
INSTALL_COPY_TOP_DIRS = {"engine", "shootergame", "steamapps"}
# Inside those, parts that belong to that server, not to the game: never copied to a new map.
SERVER_STATE_DIRS = (
    ("ShooterGame", "Saved"),  # worlds, configs, logs, cluster data
    ("ShooterGame", ".sentry-native"),  # crash reporter sessions
    ("ShooterGame", "Binaries", "Win64", "ArkApi"),  # ServerAPI plugins and their data
    ("ShooterGame", "Binaries", "Win64", "ShooterGame", "ModsUserData"),  # mods' per-server data
    ("ShooterGame", "Binaries", "Win64", "logs"),  # Steam client logs
    ("ShooterGame", "Binaries", "Win64", "config"),  # Steam client state
    ("ShooterGame", "Binaries", "Win64", "appcache"),
    ("steamapps", "downloading"),  # SteamCMD leftovers, can be gigabytes
    ("steamapps", "temp"),
    ("steamapps", "_repair_backup"),
)
COPY_MARKER_NAME = ".asa_manager_copying"  # present until a copied install has been validated


def server_installed(server_dir: Path) -> bool:
    """The server is installed and complete: its executable is there and no copy into the
    folder is still unfinished or unvalidated."""
    return ark_server_exe(server_dir).exists() and not (server_dir / COPY_MARKER_NAME).exists()


def copy_marker_source(server_dir: Path) -> Optional[Path]:
    """Where an unfinished copy into this folder was copying from, if it says."""
    try:
        text = (server_dir / COPY_MARKER_NAME).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.match(r"copying from (.+)", text.strip())
    return Path(m.group(1).strip()) if m else None


def copy_server_install(
    src: Path, dst: Path, logger: logging.Logger, also_skip: Optional[List[Path]] = None
) -> int:
    """Copy an ASA server install's program files to a new install folder, so SteamCMD only
    has to check and finish it instead of downloading the whole server.

    Only the game's folders are copied (INSTALL_COPY_TOP_DIRS), minus what belongs to that
    server (SERVER_STATE_DIRS) and any folder in also_skip (its backup or cluster folder,
    when they live in the install): the new server starts clean. Files already in the
    target are kept. The marker file written here stays until the caller has had SteamCMD
    validate the copy; while it is there the install does not count as complete, and an
    interrupted copy is resumed. Returns the number of files copied.
    """
    if path_contains(src, dst):
        raise ValueError(f"The new install folder {dst} lies inside the install it would copy ({src}).")
    skip = {tuple(part.lower() for part in rel) for rel in SERVER_STATE_DIRS}
    extra_skip = [p for p in (also_skip or []) if path_contains(src, p)]
    ensure_dir(dst)
    marker = dst / COPY_MARKER_NAME
    atomic_write_text(marker, f"copying from {src}\n", encoding="utf-8")

    def fail(err: OSError) -> None:
        raise err  # a folder that cannot be read must stop the copy, not leave holes in it

    copied = 0
    size = 0
    next_report = 2 * 1024 ** 3
    seen: Set[str] = set()  # real folders already walked: a linked folder loop is walked once
    for root, dirs, files in os.walk(src, onerror=fail, followlinks=True):
        real = os.path.normcase(os.path.realpath(root))
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        rel = Path(root).relative_to(src)
        rel_parts = tuple(part.lower() for part in rel.parts)
        if not rel_parts:
            dirs[:] = [d for d in dirs if d.lower() in INSTALL_COPY_TOP_DIRS]
        dirs[:] = [
            d for d in dirs
            if rel_parts + (d.lower(),) not in skip and not any(same_path(Path(root) / d, x) for x in extra_skip)
        ]
        for name in files:
            if copy_file_atomic(Path(root) / name, dst / rel / name):
                copied += 1
                with contextlib.suppress(OSError):
                    size += (dst / rel / name).stat().st_size
                if size >= next_report:
                    logger.info(f"Copying server files: {size / 1024 ** 3:.0f} GB so far...")
                    next_report += 2 * 1024 ** 3
    logger.info(f"Copied {copied} server file(s) ({size / 1024 ** 3:.1f} GB) from {src} to {dst}.")
    return copied


def steamcmd_app_update(
    logger: logging.Logger,
    steamcmd_exe: Path,
    install_dir: Path,
    app_id: int,
    validate: bool,
    lock_root: Path,
    retries: int = 2,
) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / "locks" / "steamcmd.lock"

    with exclusive_file_lock(lock_path):
        # Warm-up (updates steamcmd itself / creates initial files)
        stream_process_output(
            [str(steamcmd_exe), "+quit"],
            logger,
            cwd=steamcmd_exe.parent,
            log_prefix="[SteamCMD] ",
        )

        for attempt in range(1, retries + 2):
            script_lines = [
                "@ShutdownOnFailedCommand 1",
                "@NoPromptForPassword 1",
                f'force_install_dir "{install_dir}"',
                "login anonymous",
                f"app_update {app_id}" + (" validate" if validate else ""),
                "quit",
                "",
            ]
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8") as tf:
                tf.write("\n".join(script_lines))
                script_path = Path(tf.name)

            try:
                code, out = stream_process_output(
                    [str(steamcmd_exe), "+runscript", str(script_path)],
                    logger,
                    cwd=steamcmd_exe.parent,
                    log_prefix="[SteamCMD] ",
                )
            finally:
                try:
                    script_path.unlink()
                except Exception:
                    pass

            # --------- NEW SUCCESS LOGIC ----------
            # ExitCode 0 is the primary success signal.
            # We only treat it as failure if strong fatal markers appear AND no install artifacts exist.
            if code == 0:
                if steamcmd_output_has_fatal(out) and not steamcmd_verify_install(install_dir, app_id):
                    logger.warning("[SteamCMD] exit=0 but fatal markers found and install not verified -> retrying.")
                else:
                    # Optional verification (keeps first install stable)
                    if steamcmd_verify_install(install_dir, app_id):
                        logger.info("[SteamCMD] app_update successful (verified).")
                        return

                    # On some systems, manifest/exe might appear slightly delayed -> short retry window
                    logger.warning("[SteamCMD] exit=0 but install not yet verified -> short wait and re-check.")
                    time.sleep(2.0)
                    if steamcmd_verify_install(install_dir, app_id):
                        logger.info("[SteamCMD] app_update successful (verified after delay).")
                        return

                    # Still not verified -> do one more attempt if available
                    if attempt < (retries + 1):
                        logger.warning("[SteamCMD] not verified yet -> retrying (attempt %s/%s).", attempt, retries + 1)
                        time.sleep(2.0 * attempt)
                        continue

                    raise RuntimeError("SteamCMD exit=0 but install could not be verified (no manifest/exe).")

            # --------- RETRYABLE FAILURES ----------
            retryable = False
            if code in (7, 8) or re.search(r"state is 0x6 after update job", out or "", re.IGNORECASE):
                retryable = True
                changed = unstick_install_dir(install_dir, app_id, logger)
                logger.warning(
                    "[SteamCMD] Retryable failure (exit=%s), self-heal changed=%s, attempt %s/%s",
                    code, changed, attempt, retries + 1
                )

            if code == 3221225477:
                retryable = True
                logger.warning("[SteamCMD] Crash (3221225477) -> reinstall SteamCMD + retry.")
                _ = ensure_steamcmd(str(steamcmd_exe.parent), logger)

            if retryable and attempt <= retries:
                time.sleep(3.0 * attempt)
                continue

            # --------- HARD FAIL ----------
            tail = "\n".join((out or "").splitlines()[-35:])
            raise RuntimeError(f"SteamCMD failed (exit={code}). Last output:\n{tail}")

# =============================================================================
# INI Visual Editor
# =============================================================================

class _SpawnListData:
    """Stores structured spawn entries for a spawn customization key."""
    def __init__(self, listbox: tk.Listbox):
        self.listbox = listbox
        self.entries: list[str] = []  # raw INI value strings

    def clear(self) -> None:
        self.listbox.delete(0, "end")
        self.entries.clear()

    def add(self, raw: str, display: str) -> None:
        self.entries.append(raw)
        self.listbox.insert("end", display)

    def remove_selected(self) -> None:
        sel = self.listbox.curselection()
        for i in reversed(sel):
            self.listbox.delete(i)
            del self.entries[i]


# =============================================================================
# INI (order-preserving, duplicate-key aware)
# =============================================================================

@dataclass
class IniLine:
    kind: str  # "section" | "kv" | "other"
    raw: str
    section: str = ""
    key: str = ""
    value: str = ""


class IniDocument:
    def __init__(self, lines: List[IniLine]):
        self.lines = lines

    @staticmethod
    def parse(text: str) -> "IniDocument":
        cur_section = ""
        out: List[IniLine] = []
        for raw in text.splitlines(True):
            s = raw.strip()
            if s.startswith("[") and s.endswith("]"):
                cur_section = s[1:-1].strip()
                out.append(IniLine(kind="section", raw=raw, section=cur_section))
                continue
            if not s or s.startswith(";") or s.startswith("#"):
                out.append(IniLine(kind="other", raw=raw, section=cur_section))
                continue
            if "=" in raw:
                k, v = raw.split("=", 1)
                key = k.strip()
                val = v.strip().rstrip("\r\n")
                out.append(IniLine(kind="kv", raw=raw, section=cur_section, key=key, value=val))
                continue
            out.append(IniLine(kind="other", raw=raw, section=cur_section))
        return IniDocument(out)

    def is_effectively_empty(self) -> bool:
        for line in self.lines:
            if line.kind == "kv":
                return False
            if line.kind == "section":
                continue
        return True

    def kv_entries_by_section(self) -> Dict[str, List[Tuple[int, str, str]]]:
        out: Dict[str, List[Tuple[int, str, str]]] = {}
        for idx, line in enumerate(self.lines):
            if line.kind == "kv":
                out.setdefault(line.section, []).append((idx, line.key, line.value))
        return out

    def get_last_value_map(self) -> Dict[str, Dict[str, str]]:
        data: Dict[str, Dict[str, str]] = {}
        for line in self.lines:
            if line.kind == "kv":
                data.setdefault(line.section, {})[line.key] = line.value
        return data

    def get_all_values(self, section: str, key: str) -> List[str]:
        """Return all values for a key in a section (handles duplicate keys)."""
        key_lower = key.lower()
        return [
            line.value for line in self.lines
            if line.kind == "kv" and line.section == section and line.key.lower() == key_lower
        ]

    def remove_all_kv(self, section: str, key: str) -> int:
        """Remove all kv entries matching section+key. Returns count removed."""
        key_lower = key.lower()
        before = len(self.lines)
        self.lines = [
            line for line in self.lines
            if not (line.kind == "kv" and line.section == section and line.key.lower() == key_lower)
        ]
        return before - len(self.lines)

    def ensure_section(self, section: str) -> None:
        section = section.strip()
        if not section:
            return
        if any(line.kind == "section" and line.section == section for line in self.lines):
            return

        if self.lines and not self.lines[-1].raw.endswith("\n"):
            self.lines[-1].raw += "\n"
        self.lines.append(IniLine(kind="other", raw="\n"))
        self.lines.append(IniLine(kind="section", raw=f"[{section}]\n", section=section))

    def set(self, section: str, key: str, value: str) -> None:
        section = section.strip()
        key = key.strip()
        value = str(value)
        if not section or not key:
            return
        self.ensure_section(section)

        for line in self.lines:
            if line.kind == "kv" and line.section == section and line.key.lower() == key.lower():
                nl = "\r\n" if line.raw.endswith("\r\n") else ("\n" if line.raw.endswith("\n") else "\n")
                line.key = key
                line.value = value
                line.raw = f"{key}={value}{nl}"
                return

        self.append_kv(section, key, value)

    def append_kv(self, section: str, key: str, value: str) -> int:
        section = section.strip()
        key = key.strip()
        value = str(value)
        if not section or not key:
            return -1

        self.ensure_section(section)

        insert_at = None
        for i, line in enumerate(self.lines):
            if line.kind == "section" and line.section == section:
                insert_at = i + 1
                for j in range(i + 1, len(self.lines)):
                    if self.lines[j].kind == "section":
                        insert_at = j
                        break
                    insert_at = j + 1
                break

        if insert_at is None:
            insert_at = len(self.lines)

        self.lines.insert(insert_at, IniLine(kind="kv", raw=f"{key}={value}\n", section=section, key=key, value=value))
        return insert_at

    def update_value_at(self, line_index: int, value: str) -> None:
        if not (0 <= line_index < len(self.lines)):
            return
        line = self.lines[line_index]
        if line.kind != "kv":
            return
        nl = "\r\n" if line.raw.endswith("\r\n") else ("\n" if line.raw.endswith("\n") else "\n")
        line.value = str(value)
        line.raw = f"{line.key}={line.value}{nl}"

    def delete_at(self, line_index: int) -> None:
        if not (0 <= line_index < len(self.lines)):
            return
        if self.lines[line_index].kind != "kv":
            return
        self.lines.pop(line_index)

    def to_text(self) -> str:
        return "".join(line.raw for line in self.lines)


def read_ini(path: Path) -> IniDocument:
    if not path.exists():
        return IniDocument.parse("")
    return IniDocument.parse(path.read_text(encoding="utf-8", errors="ignore"))


def write_ini(path: Path, doc: IniDocument) -> None:
    atomic_write_text(path, doc.to_text(), encoding="utf-8")


def runtime_state_path_for_ini(path: Path) -> Path:
    return path.with_name(f"{path.name}.runtime.json")


def read_server_ini_runtime_state_for_stage(path: Path) -> bool:
    state_path = runtime_state_path_for_ini(path)
    if not state_path.exists():
        return False
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        value = payload.get("live_is_runtime") if isinstance(payload, dict) else None
        return bool(value) if isinstance(value, bool) else False
    except Exception:
        return False


def write_server_ini_runtime_state_for_stage(path: Path, live_is_runtime: Optional[bool]) -> None:
    state_path = runtime_state_path_for_ini(path)
    if live_is_runtime is None:
        try:
            if state_path.exists():
                state_path.unlink()
        except Exception:
            pass
        return

    payload = {
        "live_is_runtime": bool(live_is_runtime),
        "updated": now_ts(),
    }
    try:
        atomic_write_text(state_path, json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def three_way_merge_ini(base: IniDocument, staged: IniDocument, upstream: IniDocument) -> IniDocument:
    base_map = base.get_last_value_map()
    staged_map = staged.get_last_value_map()
    up_doc = upstream

    base_lower: Dict[str, Dict[str, str]] = {}
    for sec, kvs in base_map.items():
        base_lower[sec] = {key.lower(): val for key, val in kvs.items()}

    for sec, kvs in staged_map.items():
        sec_base = base_lower.get(sec, {})
        for key, staged_val in kvs.items():
            base_val = sec_base.get(key.lower())
            if base_val is None or staged_val != base_val:
                up_doc.set(sec, key, staged_val)

    return up_doc


@dataclass
class IniStagePaths:
    target_name: str  # "gus" | "game"
    live: Path
    baseline: Path
    stage: Path
    stage_base: Path
    stage_meta: Path


def staging_paths(app_base: Path, server_id: str) -> Tuple[Path, Path]:
    root = server_root(app_base, server_id) / STAGING_DIR_NAME
    ensure_dir(root)
    return root / "GameUserSettings.ini", root / "Game.ini"


def ini_stage_paths(app_base: Path, server_id: str, server_dir: Path, target: str) -> IniStagePaths:
    live_gus = server_dir / GAMEUSERSETTINGS_REL
    live_game = server_dir / GAME_INI_REL

    baseline_root = server_root(app_base, server_id) / BASELINE_DIR_NAME
    baseline_gus = baseline_root / "GameUserSettings.ini"
    baseline_game = baseline_root / "Game.ini"

    stage_gus, stage_game = staging_paths(app_base, server_id)

    if target == "gus":
        stage = stage_gus
        live = live_gus
        baseline = baseline_gus
        stage_base = stage_gus.with_suffix(".base.ini")
        stage_meta = stage_gus.with_suffix(".meta.json")
    else:
        stage = stage_game
        live = live_game
        baseline = baseline_game
        stage_base = stage_game.with_suffix(".base.ini")
        stage_meta = stage_game.with_suffix(".meta.json")

    return IniStagePaths(
        target_name=target,
        live=live,
        baseline=baseline,
        stage=stage,
        stage_base=stage_base,
        stage_meta=stage_meta,
    )


def ensure_ini_staging_synced(paths: IniStagePaths, server_running: bool, logger: Optional[logging.Logger] = None) -> None:
    ensure_dir(paths.stage.parent)

    def log(msg: str) -> None:
        if logger:
            logger.info(msg)

    runtime_live_state = (
        read_server_ini_runtime_state_for_stage(paths.live)
        or read_server_ini_runtime_state_for_stage(paths.stage)
    )

    # Runtime-generated live INIs after shutdown should never be treated as authoritative user input.
    if server_running or runtime_live_state:
        upstream = paths.baseline if paths.baseline.exists() else paths.live
    else:
        upstream = paths.live if paths.live.exists() else paths.baseline

    upstream_exists = upstream.exists() if upstream is not None else False

    if not paths.stage.exists():
        if upstream_exists:
            shutil.copy2(upstream, paths.stage)
            shutil.copy2(upstream, paths.stage_base)
            log(f"INI staging created from upstream: {paths.stage.name}")
        else:
            write_ini(paths.stage, IniDocument.parse(""))
            write_ini(paths.stage_base, IniDocument.parse(""))
            log(f"INI staging created empty: {paths.stage.name}")

        meta = {
            "created": now_ts(),
            "upstream_path": str(upstream) if upstream else "",
            "upstream_hash": file_sha256(upstream) if upstream else "",
        }
        try:
            atomic_write_text(paths.stage_meta, json.dumps(meta, indent=2), encoding="utf-8")
        except Exception:
            pass
        return

    try:
        stage_doc = read_ini(paths.stage)
        if paths.stage.stat().st_size == 0 or stage_doc.is_effectively_empty():
            if upstream_exists:
                shutil.copy2(upstream, paths.stage)
                shutil.copy2(upstream, paths.stage_base)
                log(f"INI staging repaired from upstream: {paths.stage.name}")
            else:
                write_ini(paths.stage, IniDocument.parse(""))
                write_ini(paths.stage_base, IniDocument.parse(""))
                log(f"INI staging repaired as empty: {paths.stage.name}")
            return
    except Exception:
        if upstream_exists:
            shutil.copy2(upstream, paths.stage)
            shutil.copy2(upstream, paths.stage_base)
            log(f"INI staging force-repaired from upstream: {paths.stage.name}")
        return

    if not paths.stage_base.exists():
        if upstream_exists:
            shutil.copy2(upstream, paths.stage_base)
            log(f"INI stage base created from upstream: {paths.stage_base.name}")
        else:
            shutil.copy2(paths.stage, paths.stage_base)
            log(f"INI stage base created from stage: {paths.stage_base.name}")

    if upstream_exists:
        upstream_hash = file_sha256(upstream)
        base_hash = file_sha256(paths.stage_base)
        if upstream_hash and base_hash and upstream_hash != base_hash:
            base_doc = read_ini(paths.stage_base)
            staged_doc = read_ini(paths.stage)
            upstream_doc = read_ini(upstream)
            merged = three_way_merge_ini(base_doc, staged_doc, upstream_doc)
            write_ini(paths.stage, merged)
            shutil.copy2(upstream, paths.stage_base)

            meta = {
                "last_merge": now_ts(),
                "upstream_path": str(upstream),
                "upstream_hash": upstream_hash,
                "base_hash_before": base_hash,
            }
            try:
                atomic_write_text(paths.stage_meta, json.dumps(meta, indent=2), encoding="utf-8")
            except Exception:
                pass

            log(f"INI staging merged with upstream changes: {paths.stage.name}")

# =============================================================================
# RCON
# =============================================================================

class RCONError(Exception):
    ...


class RCONAuthError(RCONError):
    ...


class RCONConnectionError(RCONError):
    ...


class RCONProtocolError(RCONError):
    ...


class RconClient:
    SERVERDATA_RESPONSE_VALUE = 0
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_AUTH = 3
    SERVERDATA_AUTH_RESPONSE = 2

    def __init__(self, host: str, port: int, password: str, timeout: float = 4.0):
        self.host = host
        self.port = int(port)
        self.password = password
        self.timeout = float(timeout)
        self._sock: Optional[socket.socket] = None
        self._req_id = 100

    def __enter__(self) -> "RconClient":
        self.connect()
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.host, self.port))
            self._sock = s
        except Exception as e:
            raise RCONConnectionError(str(e))

    def close(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        finally:
            self._sock = None

    @staticmethod
    def _pack(req_id: int, ptype: int, payload: str) -> bytes:
        body = struct.pack("<ii", req_id, ptype) + payload.encode("utf-8") + b"\x00\x00"
        return struct.pack("<i", len(body)) + body

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise RCONConnectionError("Connection closed by remote host")
            buf += chunk
        return buf

    def _recv_packet(self) -> Tuple[int, int, str]:
        assert self._sock is not None
        length = struct.unpack("<i", self._recv_exact(4))[0]
        data = self._recv_exact(length)
        if len(data) < 10:
            raise RCONProtocolError("Malformed packet")
        req_id, ptype = struct.unpack("<ii", data[:8])
        payload = data[8:-2].decode("utf-8", errors="replace")
        return req_id, ptype, payload

    def login(self) -> None:
        assert self._sock is not None
        self._req_id += 1
        self._sock.sendall(self._pack(self._req_id, self.SERVERDATA_AUTH, self.password))

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                rid, ptype, _ = self._recv_packet()
            except socket.timeout:
                break
            if ptype == self.SERVERDATA_AUTH_RESPONSE:
                if rid == -1:
                    raise RCONAuthError("Authentication failed")
                if rid == self._req_id:
                    return

        raise RCONAuthError("Authentication timeout (no AUTH_RESPONSE)")

    def command(self, cmd: str) -> str:
        assert self._sock is not None
        self._req_id += 1
        self._sock.sendall(self._pack(self._req_id, self.SERVERDATA_EXECCOMMAND, cmd))

        chunks: List[str] = []
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                rid, ptype, payload = self._recv_packet()
            except socket.timeout:
                break
            if rid == -1:
                raise RCONAuthError("Authentication failed")
            if rid != self._req_id:
                continue
            if ptype == self.SERVERDATA_RESPONSE_VALUE:
                chunks.append(payload)
        return "".join(chunks).strip()


def create_rcon_client(host: str, port: int, password: str, timeout: float = 4.0) -> RconClient:
    """
    Primary RCON implementation.
    Built-in (no third-party dependency) to ensure deterministic runtime and packaging.
    """
    return RconClient(host, port, password, timeout)

# =============================================================================
# DISCORD NOTIFICATIONS
# =============================================================================

def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def validate_discord_webhook_url(url: str) -> None:
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("Discord webhook URL is empty.")

    parsed = urlparse(cleaned)
    if parsed.scheme.lower() != "https":
        raise ValueError("Discord webhook must use https://")

    host = (parsed.hostname or "").lower()
    allowed_hosts = {"discord.com", "ptb.discord.com", "canary.discord.com", "discordapp.com"}
    if host not in allowed_hosts:
        raise ValueError("Webhook host must be a Discord domain.")

    path = parsed.path or ""
    if not re.match(r"^/api/webhooks/\d+/.+", path):
        raise ValueError("Webhook path must look like /api/webhooks/<id>/<token>.")


def redact_webhook(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "discord.com"
        match = re.match(r"^/api/webhooks/(\d+)/", parsed.path or "")
        webhook_id = match.group(1) if match else "unknown"
        return f"https://{host}/api/webhooks/{webhook_id}/[REDACTED]"
    except Exception:
        return "[REDACTED]"


class DiscordWebhookClient:
    # Messages being sent right now, by any client: quitting waits for them (a crash report
    # sent from a watcher thread would otherwise die with the process).
    in_flight = 0
    _flight_lock = threading.Lock()

    def __init__(self, webhook_url: str, logger: logging.Logger, timeout: float = 8.0):
        self.webhook_url = webhook_url.strip()
        validate_discord_webhook_url(self.webhook_url)
        self.logger = logger
        self.timeout = timeout

    @classmethod
    def sending(cls) -> bool:
        with cls._flight_lock:
            return cls.in_flight > 0

    def post(self, payload: Dict[str, Any]) -> None:
        with DiscordWebhookClient._flight_lock:
            DiscordWebhookClient.in_flight += 1
        try:
            self._send(payload)
        finally:
            with DiscordWebhookClient._flight_lock:
                DiscordWebhookClient.in_flight -= 1

    def _send(self, payload: Dict[str, Any]) -> None:
        if not self.webhook_url:
            self.logger.info("[Discord] Webhook URL missing; send skipped.")
            return

        ctx = ssl.create_default_context()
        try:
            import certifi  # type: ignore
            ctx.load_verify_locations(cafile=certifi.where())
        except Exception:
            pass

        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "ARK-ASA-Manager/1.0"}

        def send_once() -> int:
            req = Request(self.webhook_url, data=data, headers=headers, method="POST")
            with urlopen(req, timeout=self.timeout, context=ctx) as resp:
                return int(getattr(resp, "status", resp.getcode()))

        try:
            status = send_once()
            if status in (200, 204):
                return
            self.logger.info(f"[Discord] Webhook returned status {status}")
        except HTTPError as e:
            if int(getattr(e, "code", 0)) == 429:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                try:
                    delay = float(retry_after) if retry_after else 0.0
                except Exception:
                    delay = 0.0
                delay = min(delay, 10.0)  # Discord can ask for far longer; one retry is enough
                if delay > 0:
                    self.logger.info(f"[Discord] Rate limited. Retrying in {delay:.1f}s.")
                    time.sleep(delay)
                try:
                    status = send_once()
                    if status not in (200, 204):
                        self.logger.info(f"[Discord] Webhook returned status {status} after retry.")
                except Exception as retry_err:
                    self.logger.info(
                        f"[Discord] Webhook retry failed ({redact_webhook(self.webhook_url)}): "
                        f"{type(retry_err).__name__}"
                    )
            else:
                self.logger.info(f"[Discord] Webhook error ({redact_webhook(self.webhook_url)}): HTTP {e.code}")
        except Exception as e:
            self.logger.info(
                f"[Discord] Webhook error ({redact_webhook(self.webhook_url)}): {type(e).__name__}"
            )


class PlayerListProvider:
    def __init__(self, rcon_fn: Callable[[str, float], str], logger: logging.Logger):
        self.rcon_fn = rcon_fn
        self.logger = logger
        self._patterns = [
            re.compile(r"^\s*\d+\.\s*(?P<name>.+?)[,;]\s*(?P<id>\d{6,20})\s*$"),
            re.compile(r"^\s*(?P<id>\d{6,20})\s*[,;]\s*(?P<name>.+?)\s*$"),
            re.compile(r"^\s*(?P<name>.+?)\s*[,;]\s*(?P<id>\d{6,20})\b.*$"),
        ]

    def get_players(self) -> Dict[str, Dict[str, str]]:
        raw = self.rcon_fn("ListPlayers", 6.0)
        if not raw:
            return {}

        players: Dict[str, Dict[str, str]] = {}
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        for line in lines:
            if line.lower().startswith("player") and "count" in line.lower():
                continue
            if "no players" in line.lower():
                continue

            cleaned = re.sub(r"^\s*\d+\.\s*", "", line).strip()
            name = ""
            player_id = ""
            for pat in self._patterns:
                m = pat.match(cleaned)
                if m:
                    name = m.group("name").strip()
                    player_id = m.group("id").strip()
                    break

            if not player_id:
                m = re.search(r"\d{6,20}", cleaned)
                if m:
                    player_id = m.group(0)
                    name = cleaned.replace(player_id, "").strip(" ,;-")

            if not name:
                name = cleaned

            if player_id:
                key = player_id
            else:
                key = hashlib.sha1(name.encode("utf-8", errors="ignore")).hexdigest()

            players[key] = {"name": name, "id": player_id, "raw": line}

        return players


class DiscordStateStore:
    def __init__(self, state_path: Path, logger: logging.Logger):
        self.path = state_path
        self.logger = logger

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            self.logger.info(f"[Discord] Failed to read state: {e}")
            return {}

    def save(self, state: Dict[str, Any]) -> None:
        try:
            ensure_dir(self.path.parent)
            atomic_write_text(self.path, json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            self.logger.info(f"[Discord] Failed to save state: {e}")


DISCORD_SHARED_FIELDS = (
    "discord_enable",
    "discord_webhook_url",
    "discord_poll_interval_sec",
    "discord_notify_start",
    "discord_notify_stop",
    "discord_notify_join",
    "discord_notify_leave",
    "discord_notify_crash",
    "discord_include_player_id",
    "discord_mention_mode",
    "discord_mention_map_json",
)


class DiscordNotificationCoordinator:
    ERROR_THROTTLE_SEC = 3600

    def __init__(self, state_path: Path, server_id: str, logger: logging.Logger, rcon_fn: Callable[[str, float], str]):
        self.server_id = server_id
        self.logger = logger
        self.state_store = DiscordStateStore(state_path, logger)
        self.player_provider = PlayerListProvider(rcon_fn, logger)
        self._cfg: Optional[AppConfig] = None
        self._webhook: Optional[DiscordWebhookClient] = None
        self._state_lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._server_instance_id: Optional[str] = None
        self._stop_requested = False
        # Which server a message is from ("PvE Land · The Island"), shown at the top of each
        # one: several maps of a cluster often post to the same channel. Asked for on every
        # message, so a renamed cluster or a changed map shows at once.
        self.label_fn: Optional[Callable[[], str]] = None
        self.instance_pid: Optional[int] = None  # the server run start_instance was told about

    def source_label(self) -> str:
        fn = self.label_fn
        if fn is None:
            return ""
        try:
            return (fn() or "").strip()
        except Exception as e:
            self.logger.info(f"[Discord] Could not name the server for the message: {type(e).__name__}: {e}")
            return ""

    def end_instance(self, pid: Optional[int]) -> None:
        """The run is over and reported: forget it, unless a newer run (another PID) took over."""
        if pid is None or self.instance_pid in (None, pid):
            self._server_instance_id = None
            self.instance_pid = None

    def polling(self) -> bool:
        return bool(self._poll_thread and self._poll_thread.is_alive() and not self._stop_event.is_set())

    def _post(self, payload: Dict[str, Any]) -> None:
        webhook = self._webhook  # the settings can change meanwhile; send with the one checked
        if not webhook:
            return
        label = self.source_label()
        if label:
            for embed in payload.get("embeds") or []:
                embed.setdefault("author", {"name": label[:256]})
        webhook.post(payload)

    def update_config(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        if cfg.discord_enable and cfg.discord_webhook_url.strip():
            try:
                self._webhook = DiscordWebhookClient(cfg.discord_webhook_url, self.logger, timeout=8.0)
            except ValueError as exc:
                self._webhook = None
                self.logger.info(f"[Discord] Webhook URL invalid: {exc}")
        else:
            self._webhook = None
        if not (cfg.discord_notify_join or cfg.discord_notify_leave):
            self.stop_polling()

    @property
    def state_dir(self) -> Path:
        return self.state_store.path.parent

    def request_stop(self) -> None:
        self._stop_requested = True

    def start_instance(self, cfg: AppConfig, pid: int) -> None:
        self.update_config(cfg)
        instance_id = f"{utc_now_iso()}_pid{pid}"
        self._server_instance_id = instance_id
        self.instance_pid = pid
        self._stop_requested = False
        with self._state_lock:
            state = self.state_store.load()
            state["server_instance_id"] = instance_id
            state["server_started_ts"] = time.time()
            self.state_store.save(state)
        self._notify_start(cfg)

    def notify_stop(self, cfg: AppConfig, requested: bool, exit_code: Optional[int] = None) -> None:
        self.update_config(cfg)
        if requested:
            self._notify_stop(cfg)
        else:
            if self._stop_requested:
                return
            self._notify_crash(cfg, exit_code)

    def start_polling(self, cfg: AppConfig) -> None:
        self.update_config(cfg)
        if not cfg.discord_enable:
            return
        if not (cfg.discord_notify_join or cfg.discord_notify_leave):
            return
        if self._poll_thread and self._poll_thread.is_alive():
            return
        if not cfg.enable_rcon:
            return
        if not self._webhook:
            return
        self._stop_event.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._stop_event.set()

    def send_test(self, cfg: AppConfig) -> None:
        self.update_config(cfg)
        if not self._webhook:
            self.logger.info("[Discord] Webhook not configured; test skipped.")
            return
        payload = {
            "embeds": [
                {
                    "title": "🔔 Discord webhook test",
                    "description": "The ARK ASA Server Manager webhook is configured correctly.",
                    "color": 0x3498DB,
                    "timestamp": utc_now_iso(),
                    "footer": {"text": "ARK ASA Server Manager"},
                }
            ]
        }
        self._post(payload)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            cfg = self._cfg
            if cfg:
                interval = max(60, int(cfg.discord_poll_interval_sec))
                self._poll_once(cfg)
            else:
                interval = 60

            deadline = time.time() + interval
            while time.time() < deadline:
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    def _poll_once(self, cfg: AppConfig) -> None:
        if not cfg.discord_enable:
            return
        if not cfg.enable_rcon:
            return
        if not self._webhook:
            return

        try:
            current = self.player_provider.get_players()
        except Exception as e:
            self.logger.info(f"[Discord] RCON ListPlayers failed: {e}")
            self._maybe_notify_error(cfg, e)
            return

        with self._state_lock:
            state = self.state_store.load()
            previous = state.get("last_players", {})
            if not isinstance(previous, dict):
                previous = {}

            current_ids = set(current.keys())
            previous_ids = set(previous.keys())
            joined_ids = current_ids - previous_ids
            left_ids = previous_ids - current_ids

            if cfg.discord_notify_join and joined_ids:
                self._notify_player_change(cfg, current, joined_ids, joined=True)
            if cfg.discord_notify_leave and left_ids:
                self._notify_player_change(cfg, previous, left_ids, joined=False)

            state["last_players"] = current
            state["last_error_hash"] = ""
            state["last_error_notify_ts"] = 0
            self.state_store.save(state)

    def _load_mention_map(self, cfg: AppConfig) -> Dict[str, str]:
        raw = (cfg.discord_mention_map_json or "").strip()
        if not raw:
            return {}

        data: Dict[str, str] = {}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = {str(k): str(v) for k, v in parsed.items()}
                return data
        except Exception:
            pass

        try:
            path = Path(raw)
            if path.exists() and path.is_file():
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    data = {str(k): str(v) for k, v in parsed.items()}
        except Exception as e:
            self.logger.info(f"[Discord] Failed to load mention map: {e}")

        return data

    def _format_player_mention(self, cfg: AppConfig, player: Dict[str, str], mapping: Optional[Dict[str, str]] = None) -> str:
        name = player.get("name") or "Unknown"
        mode = (cfg.discord_mention_mode or "name").strip().lower()
        if mode == "none":
            return name
        if mode == "mapping":
            mapping = mapping if mapping is not None else self._load_mention_map(cfg)
            key = player.get("id") or ""
            target = mapping.get(key) if key else None
            if not target:
                target = mapping.get(name)
            if target:
                return f"<@{target}>"
        return f"**{name}**"

    def _server_context_fields(self, cfg: AppConfig) -> List[Dict[str, Any]]:
        mods = cfg.normalized_mods()
        mods_display = mods.split(",") if mods else []
        if len(mods_display) > 5:
            mods_display = mods_display[:5] + ["..."]

        return [
            {"name": "Server", "value": cfg.server_name or "default", "inline": True},
            {"name": "Map", "value": cfg.map_name or DEFAULT_MAP, "inline": True},
            {"name": "Game Port", "value": str(int(cfg.port)), "inline": True},
            {"name": "Query Port", "value": str(int(cfg.query_port)), "inline": True},
            {"name": "Max Players", "value": str(int(cfg.max_players)), "inline": True},
            {"name": "Mods", "value": ", ".join(mods_display) if mods_display else "None", "inline": False},
        ]

    def _notify_start(self, cfg: AppConfig) -> None:
        if not cfg.discord_notify_start or not self._webhook:
            return
        if not self._server_instance_id:
            return

        with self._state_lock:
            state = self.state_store.load()
            if state.get("last_start_sent_instance") == self._server_instance_id:
                return
            state["last_start_sent_instance"] = self._server_instance_id
            self.state_store.save(state)

        payload = {
            "embeds": [
                {
                    "title": "🟢 Server started",
                    "description": f"{cfg.server_name or 'default'} is now online.",
                    "color": 0x2ECC71,
                    "fields": self._server_context_fields(cfg),
                    "timestamp": utc_now_iso(),
                    "footer": {"text": "ARK ASA Server Manager"},
                }
            ]
        }
        self._post(payload)

    def _notify_stop(self, cfg: AppConfig) -> None:
        if not cfg.discord_notify_stop or not self._webhook:
            return
        if not self._server_instance_id:
            return

        with self._state_lock:
            state = self.state_store.load()
            if state.get("last_stop_sent_instance") == self._server_instance_id:
                return
            state["last_stop_sent_instance"] = self._server_instance_id
            self.state_store.save(state)

        payload = {
            "embeds": [
                {
                    "title": "🛑 Server stopped",
                    "description": f"{cfg.server_name or 'default'} has been stopped.",
                    "color": 0xE74C3C,
                    "fields": self._stop_fields(state),
                    "timestamp": utc_now_iso(),
                    "footer": {"text": "ARK ASA Server Manager"},
                }
            ]
        }
        self._post(payload)

    def _notify_crash(self, cfg: AppConfig, exit_code: Optional[int]) -> None:
        if not cfg.discord_notify_crash or not self._webhook:
            return
        if not self._server_instance_id:
            return

        with self._state_lock:
            state = self.state_store.load()
            if state.get("last_crash_sent_instance") == self._server_instance_id:
                return
            state["last_crash_sent_instance"] = self._server_instance_id
            self.state_store.save(state)

        fields = self._stop_fields(state)
        if exit_code is not None:
            fields.append({"name": "Exit Code", "value": str(exit_code), "inline": True})

        payload = {
            "embeds": [
                {
                    "title": "💥 Server exited",
                    "description": f"{cfg.server_name or 'default'} exited unexpectedly.",
                    "color": 0xF39C12,
                    "fields": fields,
                    "timestamp": utc_now_iso(),
                    "footer": {"text": "ARK ASA Server Manager"},
                }
            ]
        }
        self._post(payload)

    def _stop_fields(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        fields: List[Dict[str, Any]] = []
        started = state.get("server_started_ts")
        if isinstance(started, (int, float)):
            uptime = max(0, int(time.time() - float(started)))
            fields.append({"name": "Uptime", "value": format_duration(uptime), "inline": True})
        return fields

    def _notify_player_change(
        self,
        cfg: AppConfig,
        player_data: Dict[str, Dict[str, str]],
        player_ids: set,
        joined: bool,
    ) -> None:
        if not self._webhook:
            return
        title = "✅ Player joined" if joined else "👋 Player left"
        color = 0x2ECC71 if joined else 0x95A5A6

        mapping = self._load_mention_map(cfg) if cfg.discord_mention_mode == "mapping" else None
        mentions = [self._format_player_mention(cfg, player_data[player_id], mapping) for player_id in player_ids]
        online_now = len(player_data) if joined else max(0, len(player_data) - len(player_ids))

        if len(mentions) > 5:
            description = "\n".join(mentions)
            embed = {
                "title": title,
                "description": description,
                "color": color,
                "fields": [{"name": "Online now", "value": str(online_now), "inline": True}],
                "timestamp": utc_now_iso(),
                "footer": {"text": "ARK ASA Server Manager"},
            }
            self._post({"embeds": [embed]})
            return

        for player_id in player_ids:
            player = player_data[player_id]
            mention = self._format_player_mention(cfg, player, mapping)
            fields = [{"name": "Online now", "value": str(online_now), "inline": True}]
            if cfg.discord_include_player_id and player.get("id"):
                fields.append({"name": "Player ID", "value": player["id"], "inline": True})
            embed = {
                "title": title,
                "description": f"{mention} {'joined' if joined else 'left'} the server.",
                "color": color,
                "fields": fields,
                "timestamp": utc_now_iso(),
                "footer": {"text": "ARK ASA Server Manager"},
            }
            self._post({"embeds": [embed]})

    def _maybe_notify_error(self, cfg: AppConfig, error: Exception) -> None:
        if not self._webhook:
            return

        # --- PATCH: RCON Discord notify
        STAGE_INTERVAL_SEC = 300
        REQUIRED_STAGES = 3
        STALE_RESET_SEC = 900
        POST_NOTIFY_THROTTLE_SEC = 1800

        now = time.time()
        with self._state_lock:
            state = self.state_store.load()

            last_notify = state.get("rcon_last_notify_ts", 0)
            if isinstance(last_notify, (int, float)) and now - float(last_notify) < POST_NOTIFY_THROTTLE_SEC:
                return

            last_seen = state.get("rcon_last_seen_ts", 0)
            if isinstance(last_seen, (int, float)) and now - float(last_seen) > STALE_RESET_SEC:
                state.pop("rcon_stage_count", None)
                state.pop("rcon_stage_last_ts", None)

            state["rcon_last_seen_ts"] = now

            stage_count = int(state.get("rcon_stage_count", 0) or 0)
            stage_last_ts = float(state.get("rcon_stage_last_ts", 0) or 0)

            if stage_count == 0:
                state["rcon_stage_count"] = 1
                state["rcon_stage_last_ts"] = now
                self.state_store.save(state)
                return

            if now - stage_last_ts < STAGE_INTERVAL_SEC:
                self.state_store.save(state)
                return

            stage_count += 1
            state["rcon_stage_count"] = stage_count
            state["rcon_stage_last_ts"] = now

            if stage_count < REQUIRED_STAGES:
                self.state_store.save(state)
                return

            state["rcon_last_notify_ts"] = now
            state.pop("rcon_stage_count", None)
            state.pop("rcon_stage_last_ts", None)
            self.state_store.save(state)

        err_hash = hashlib.sha1(str(error).encode("utf-8", errors="ignore")).hexdigest()
        now = time.time()
        with self._state_lock:
            state = self.state_store.load()
            last_hash = state.get("last_error_hash", "")
            last_ts = state.get("last_error_notify_ts", 0)
            if err_hash == last_hash and isinstance(last_ts, (int, float)) and now - float(last_ts) < self.ERROR_THROTTLE_SEC:
                return
            state["last_error_hash"] = err_hash
            state["last_error_notify_ts"] = now
            self.state_store.save(state)

        payload = {
            "embeds": [
                {
                    "title": "⚠️ RCON polling error",
                    "description": str(error),
                    "color": 0xE67E22,
                    "timestamp": utc_now_iso(),
                    "footer": {"text": "ARK ASA Server Manager"},
                }
            ]
        }
        self._post(payload)

# =============================================================================
# SERVER OPS
# =============================================================================

def ark_server_exe(server_dir: Path) -> Path:
    return server_dir / "ShooterGame" / "Binaries" / "Win64" / "ArkAscendedServer.exe"


def server_saved_dir(server_dir: Path) -> Path:
    return server_dir / "ShooterGame" / "Saved"


def server_config_dir(server_dir: Path) -> Path:
    return server_dir / "ShooterGame" / "Saved" / "Config" / "WindowsServer"


def split_custom_start_args(raw: str) -> List[str]:
    cleaned = (raw or "").strip()
    if not cleaned:
        return []
    try:
        parts = shlex.split(cleaned, posix=False)
    except ValueError:
        parts = cleaned.split()
    return [p for p in parts if p]


def build_server_command(cfg: AppConfig) -> List[str]:
    exe = ark_server_exe(Path(cfg.server_dir))
    map_name = (cfg.map_name or "").strip()
    if not map_name:
        raise ValueError("Map name is required")

    session = (cfg.server_name or "").replace('"', "").strip() or DEFAULT_SERVER_NAME

    url_args = [
        f"{map_name}?listen",
        f"SessionName={session}",
        f"Port={int(cfg.port)}",
        f"QueryPort={int(cfg.query_port)}",
    ]

    if cfg.server_platform.strip():
        url_args.append(f"ServerPlatform={cfg.server_platform.strip()}")

    if cfg.alt_save_directory_name.strip():
        url_args.append(f"AltSaveDirectoryName={cfg.alt_save_directory_name.strip()}")

    url = "?".join(url_args)

    flags: List[str] = []
    flags.append("-UseBattlEye" if cfg.enable_battleye else "-NoBattlEye")

    if cfg.automanaged_mods:
        flags.append("-automanagedmods")

    mods = cfg.normalized_mods()
    if mods:
        flags.append(f"-mods={mods}")
        
    flags.append(f"-WinLiveMaxPlayers={int(cfg.max_players)}")

    if cfg.cluster_enable:
        cid = cfg.cluster_id.strip()
        if not cid:
            raise ValueError("Enable Cluster requires Cluster ID.")
        flags.append(f"-clusterid={cid}")

    # The folder belongs to the cluster: without -clusterid there is nothing to share, and the
    # status line, backups and cluster setup all treat it as unused then.
    if cfg.cluster_enable and cfg.cluster_custom_path_enable:
        p = cfg.cluster_dir_override.strip()
        if not p:
            raise ValueError("Enable Cluster Custom Path requires a valid path.")
        flags.append(f"-ClusterDirOverride={p}")

    if cfg.no_transfer_from_filtering:
        flags.append("-NoTransferFromFiltering")

    if cfg.dino_mode.strip():
        flags.append(f"-{cfg.dino_mode.strip()}")

    if cfg.log_servergamelog:
        flags.append("-servergamelog")
    if cfg.log_servergamelogincludetribelogs:
        flags.append("-servergamelogincludetribelogs")
    if cfg.log_serverrconoutputtribelogs:
        flags.append("-ServerRCONOutputTribeLogs")

    if cfg.mech_disablecustomcosmetics:
        flags.append("-DisableCustomCosmetics")
    if cfg.mech_autodestroystructures:
        flags.append("-AutoDestroyStructures")
    if cfg.mech_forcerespawndinos:
        flags.append("-ForceRespawnDinos")
    if cfg.mech_nowildbabies:
        flags.append("-NoWildBabies")
    if cfg.mech_forceallowcaveflyers:
        flags.append("-ForceAllowCaveFlyers")
    if cfg.mech_disabledinonetrangescaling:
        flags.append("-disabledinonetrangescaling")
    if cfg.mech_unstasisdinoobstructioncheck:
        flags.append("-UnstasisDinoObstructionCheck")
    if cfg.mech_alwaystickdedicatedskeletalmeshes:
        flags.append("-AlwaysTickDedicatedSkeletalMeshes")
    if cfg.mech_disablecharactertracker:
        flags.append("-disableCharacterTracker")
    if cfg.mech_useservernetspeedcheck:
        flags.append("-UseServerNetSpeedCheck")
    if cfg.mech_stasiskeepcontrollers:
        flags.append("-StasisKeepControllers")
    if cfg.mech_ignoredupeditems:
        flags.append("-ignoredupeditems")

    flags.extend(split_custom_start_args(cfg.custom_start_args))

    return [str(exe), url, *flags]

# =============================================================================
# BASELINE / STAGING APPLY
# =============================================================================

def ensure_baseline(app_base: Path, server_id: str, server_dir: Path, logger: logging.Logger, refresh: bool = True) -> Path:
    base = server_root(app_base, server_id) / BASELINE_DIR_NAME
    ensure_dir(base)

    src_gus = server_dir / GAMEUSERSETTINGS_REL
    src_game = server_dir / GAME_INI_REL

    dst_gus = base / "GameUserSettings.ini"
    dst_game = base / "Game.ini"

    if src_gus.exists() and (refresh or not dst_gus.exists()):
        shutil.copy2(src_gus, dst_gus)
        logger.info("Baseline updated: GameUserSettings.ini")
    if src_game.exists() and (refresh or not dst_game.exists()):
        shutil.copy2(src_game, dst_game)
        logger.info("Baseline updated: Game.ini")

    return base


def apply_staging_to_server(app_base: Path, server_id: str, server_dir: Path, logger: logging.Logger) -> None:
    stage_gus, stage_game = staging_paths(app_base, server_id)
    dest_gus = server_dir / GAMEUSERSETTINGS_REL
    dest_game = server_dir / GAME_INI_REL

    if stage_gus.exists():
        ensure_dir(dest_gus.parent)
        shutil.copy2(stage_gus, dest_gus)
        logger.info("Applied staging: GameUserSettings.ini")
        write_server_ini_runtime_state_for_stage(stage_gus, live_is_runtime=False)

    if stage_game.exists():
        ensure_dir(dest_game.parent)
        shutil.copy2(stage_game, dest_game)
        logger.info("Applied staging: Game.ini")
        write_server_ini_runtime_state_for_stage(stage_game, live_is_runtime=False)


def restore_baseline_to_server(app_base: Path, server_id: str, server_dir: Path, logger: logging.Logger) -> None:
    base_root = server_root(app_base, server_id) / BASELINE_DIR_NAME
    src_gus = base_root / "GameUserSettings.ini"
    src_game = base_root / "Game.ini"

    dest_gus = server_dir / GAMEUSERSETTINGS_REL
    dest_game = server_dir / GAME_INI_REL

    # Clear runtime-state marker before restoring config files
    for ini_path in (src_gus, src_game):
        try:
            write_server_ini_runtime_state_for_stage(ini_path, live_is_runtime=None)
        except Exception:
            pass

    if src_gus.exists():
        ensure_dir(dest_gus.parent)
        shutil.copy2(src_gus, dest_gus)
        logger.info("Restored baseline: GameUserSettings.ini")
        write_server_ini_runtime_state_for_stage(src_gus, live_is_runtime=False)

    if src_game.exists():
        ensure_dir(dest_game.parent)
        shutil.copy2(src_game, dest_game)
        logger.info("Restored baseline: Game.ini")
        write_server_ini_runtime_state_for_stage(src_game, live_is_runtime=False)


def ensure_required_server_settings(
    cfg: AppConfig,
    app_base: Path,
    server_id: str,
    server_dir: Path,
    logger: logging.Logger,
) -> None:
    ensure_dir((server_dir / GAMEUSERSETTINGS_REL).parent)

    # Stage GUS
    paths_gus = ini_stage_paths(app_base, server_id, server_dir, "gus")
    ensure_ini_staging_synced(paths_gus, server_running=False, logger=logger)
    doc_gus = read_ini(paths_gus.stage)

    # Password/RCON in [ServerSettings]
    sec_server = "ServerSettings"
    doc_gus.set(sec_server, "ServerAdminPassword", (cfg.admin_password or "").strip())
    doc_gus.set(sec_server, "ServerPassword", (cfg.join_password or "").strip())

    if cfg.enable_rcon:
        doc_gus.set(sec_server, "RCONEnabled", "True")
        doc_gus.set(sec_server, "RCONPort", str(int(cfg.rcon_port)))
    else:
        doc_gus.set(sec_server, "RCONEnabled", "False")

    # SessionSettings
    sec_session = "/Script/Engine.GameSession"
    doc_gus.set(sec_session, "SessionName", (cfg.server_name or DEFAULT_SERVER_NAME).strip())
    doc_gus.set(sec_session, "MaxPlayers", str(int(cfg.max_players)))

    write_ini(paths_gus.stage, doc_gus)
    logger.info("Staged GameUserSettings.ini (Admin/Join/RCON/SessionName/MaxPlayers).")

    # Stage Game.ini (critical for MaxPlayers)
    paths_game = ini_stage_paths(app_base, server_id, server_dir, "game")
    ensure_ini_staging_synced(paths_game, server_running=False, logger=logger)
    doc_game = read_ini(paths_game.stage)

    doc_game.set("/Script/Engine.GameSession", "MaxPlayers", str(int(cfg.max_players)))
    write_ini(paths_game.stage, doc_game)
    logger.info("Staged Game.ini (/Script/Engine.GameSession MaxPlayers).")

# =============================================================================
# CLUSTER
# =============================================================================

def make_cluster_id(name: str) -> str:
    """A readable, unique cluster ID: a slug of the name plus a short random tail."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:24].strip("-")
    return f"{slug or 'cluster'}-{uuid.uuid4().hex[:6]}"


def server_cluster_data_dir(server_dir: Path) -> Path:
    """Where ARK keeps cluster data when no ClusterDirOverride is given."""
    return server_dir / SERVER_CLUSTERS_REL


def default_cluster_dir(app_base: Path, cluster_id: str, server_dir: Optional[Path] = None) -> Path:
    """Shared cluster folder under the manager's storage.

    The path goes on the command line unquoted, so a storage root with spaces in it
    (an APPDATA fallback under "C:\\Users\\First Last") falls back to a folder beside
    the server install.
    """
    preferred = app_base / CLUSTERS_DIR_NAME / cluster_id
    if " " not in str(preferred) or server_dir is None:
        return preferred
    beside = server_dir.parent / "ARK-Clusters" / cluster_id
    return beside if " " not in str(beside) else preferred


def effective_cluster_dir(cfg: AppConfig) -> Optional[Path]:
    """The folder this profile's server reads and writes cluster data in, or None if standalone."""
    if not cfg.cluster_enable or not cfg.cluster_id.strip():
        return None
    override = cfg.cluster_dir_override.strip()
    if cfg.cluster_custom_path_enable and override:
        return Path(override)
    return server_cluster_data_dir(Path(cfg.server_dir))


def path_contains(outer: Path, inner: Path) -> bool:
    """True when inner is outer or lies anywhere below it."""
    try:
        return same_path(outer, inner) or outer.resolve() in inner.resolve().parents
    except OSError:
        return False


def cluster_id_data_dir(cluster_dir: Path, cluster_id: str) -> Path:
    """ARK keeps one cluster's uploads in a subfolder named after its ID."""
    return cluster_dir / cluster_id


def dir_has_files(path: Optional[Path]) -> bool:
    try:
        return path is not None and path.is_dir() and any(p.is_file() for p in path.rglob("*"))
    except OSError:
        return False


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def copy_cluster_data(src: Path, dst: Path, logger: logging.Logger) -> Tuple[int, int]:
    """Copy transfer data into a cluster folder. Files already there win; nothing is moved."""
    copied = skipped = 0
    if not src.is_dir():
        return copied, skipped
    # Listed up front, and minus dst itself when it sits inside src, so the walk never
    # picks up files this copy just wrote.
    nested = path_contains(src, dst) and not same_path(src, dst)
    files = [
        p for p in src.rglob("*")
        if p.is_file() and not (nested and path_contains(dst, p))
    ]
    ensure_dir(dst)
    for path in files:
        # Existing files win (an upload another map wrote meanwhile, too).
        if copy_file_atomic(path, dst / path.relative_to(src)):
            copied += 1
        else:
            skipped += 1
    logger.info(f"Cluster data: copied {copied} file(s) from {src} to {dst} ({skipped} already there, kept).")
    return copied, skipped


@dataclass
class ClusterInfo:
    """A cluster as seen through the profiles that use it."""
    cluster_id: str
    cluster_dir: Optional[Path] = None
    member_ids: List[str] = field(default_factory=list)
    member_names: List[str] = field(default_factory=list)
    member_dirs: List[str] = field(default_factory=list)
    member_folders: List[Path] = field(default_factory=list)  # each member's own cluster folder
    mods: List[str] = field(default_factory=list)  # normalized mod list of each member, in order

    def label(self) -> str:
        names = ", ".join(self.member_names) or "no servers"
        return f"{self.cluster_id}  ({names})"


def discover_clusters(
    app_base: Path,
    servers: List[ServerRef],
    exclude_id: str = "",
) -> List[ClusterInfo]:
    """Group the other profiles by the cluster ID they run with."""
    found: Dict[str, ClusterInfo] = {}
    custom_dir: Set[str] = set()  # clusters whose folder came from a member's own override
    for ref in servers:
        if ref.id == exclude_id:
            continue
        path = server_config_path(app_base, ref.id)
        if not path.exists():
            continue
        try:
            cfg = ServerConfigStore(path, server_lock_path(app_base, ref.id)).load().cfg
        except Exception:
            continue
        if ref.server_dir:
            cfg.server_dir = ref.server_dir
        folder = effective_cluster_dir(cfg)
        if folder is None:
            continue
        cid = cfg.cluster_id.strip()
        info = found.setdefault(cid, ClusterInfo(cluster_id=cid))
        # A shared folder a member chose beats the per-install default of another member.
        is_custom = bool(cfg.cluster_custom_path_enable and cfg.cluster_dir_override.strip())
        if info.cluster_dir is None or (is_custom and cid not in custom_dir):
            info.cluster_dir = folder
            if is_custom:
                custom_dir.add(cid)
        info.member_ids.append(ref.id)
        info.member_names.append(f"{ref.display_name or ref.id} - {cfg.map_name}")
        info.member_dirs.append(cfg.server_dir)
        info.member_folders.append(folder)
        info.mods.append(cfg.normalized_mods())
    return sorted(found.values(), key=lambda c: c.cluster_id.lower())


def cluster_config_warnings(cfg: AppConfig, backup_root: Optional[Path] = None) -> List[str]:
    """Things about this profile's cluster settings that silently break transfers."""
    warnings: List[str] = []
    cid = cfg.cluster_id.strip()
    override = cfg.cluster_dir_override.strip()

    if cfg.cluster_enable and cfg.cluster_custom_path_enable and override:
        folder = Path(override)
        if not folder.is_absolute():
            warnings.append(
                "The cluster folder is a relative path; ARK resolves it from its own Binaries folder, the "
                "manager from elsewhere. Use a full path such as D:\\ARK-Clusters\\<id>."
            )
        elif path_contains(folder, Path(cfg.server_dir)) or (backup_root is not None and path_contains(folder, backup_root)):
            warnings.append(
                "The cluster folder contains the server install or the backup folder. Give the cluster a "
                "folder of its own; backups leave it out until then."
            )

    if cfg.cluster_enable and cid and not CLUSTER_ID_RE.match(cid):
        warnings.append(
            f"Cluster ID '{cid}' has characters other than letters, numbers, '-' and '_'; "
            "ARK may not read it from the command line."
        )
    if cfg.cluster_enable and not cfg.cluster_custom_path_enable:
        warnings.append(
            "Cluster data is kept inside this install (Saved\\clusters), so only servers running from "
            "this same install folder can see transfers. Use 'Set Up / Join Cluster...' to give the "
            "cluster a shared folder."
        )
    if cfg.cluster_enable and cfg.cluster_custom_path_enable and " " in override:
        warnings.append(
            "The cluster folder path contains spaces; ARK cuts the path at the first space. "
            "Pick a folder without spaces."
        )
    if not cfg.cluster_enable and cfg.cluster_custom_path_enable and override:
        warnings.append("A cluster folder is set but 'Enable Cluster' is off, so it is not passed to the server.")
    return warnings

DEFAULT_START_STAGGER_SEC = 20


@dataclass
class ClusterView:
    """A cluster as the dashboard shows it: its ID, a display name and its member profiles."""
    cluster_id: str
    name: str
    members: List[str] = field(default_factory=list)  # server ids, in profile order
    stagger_sec: int = DEFAULT_START_STAGGER_SEC


def group_clusters(
    profiles: List[Tuple[str, AppConfig]],
    meta: Dict[str, Dict[str, Any]],
) -> Tuple[List[ClusterView], List[str]]:
    """Clusters (in order of first member) and the ids of standalone profiles."""
    clusters: Dict[str, ClusterView] = {}
    standalone: List[str] = []
    for sid, cfg in profiles:
        if effective_cluster_dir(cfg) is None:
            standalone.append(sid)
            continue
        cid = cfg.cluster_id.strip()
        view = clusters.get(cid)
        if view is None:
            info = meta.get(cid, {})
            view = ClusterView(
                cluster_id=cid,
                name=str(info.get("name") or cid),
                stagger_sec=max(0, safe_int(info.get("start_stagger_sec"), DEFAULT_START_STAGGER_SEC)),
            )
            clusters[cid] = view
        view.members.append(sid)
    return list(clusters.values()), standalone


@dataclass
class HealthIssue:
    """One thing wrong with a cluster. fix names a repair the app can make, if any."""
    severity: str  # "error" (transfers or starts break), "warning", "info"
    text: str
    fix: str = ""
    servers: List[str] = field(default_factory=list)


def _mod_set(csv: str) -> Tuple[str, ...]:
    return tuple(sorted(m for m in (csv or "").split(",") if m))


def cluster_health(
    members: List[Tuple[str, str, AppConfig]],
    others: List[Tuple[str, str, AppConfig]],
    backup_roots: List[Path],
) -> List[HealthIssue]:
    """Check a cluster: members and other profiles as (server id, display name, settings).

    Pure: reads no files except whether each member is installed.
    """
    issues: List[HealthIssue] = []
    if not members:
        return issues
    names = {sid: name for sid, name, _ in members}

    folders: Dict[str, List[str]] = {}
    for sid, _name, cfg in members:
        folder = effective_cluster_dir(cfg)
        key = os.path.normcase(os.path.normpath(str(folder))) if folder else ""
        folders.setdefault(key, []).append(sid)
    if len(folders) > 1:
        issues.append(HealthIssue(
            "error",
            "The maps do not share one cluster data folder, so survivors, dinos and items uploaded on one "
            "map cannot be downloaded on the others.",
            fix="same_folder",
            servers=[sid for sid, _n, _c in members],
        ))
    for sid, name, cfg in members:
        config_warnings = cluster_config_warnings(cfg)
        for w in config_warnings:
            sev = "error" if ("spaces" in w or "relative path" in w) else "warning"
            fix = "own_folder" if "inside this install" in w else ("same_folder" if "contains" in w else "")
            issues.append(HealthIssue(sev, f"{name}: {w}", fix=fix, servers=[sid]))
        folder = effective_cluster_dir(cfg)
        already = any("contains" in w or "spaces" in w or "relative path" in w for w in config_warnings)
        if folder is not None and cfg.cluster_custom_path_enable and not already:
            installs = [Path(c.server_dir) for _s, _n, c in members + others]
            problem = cluster_folder_problem(folder, installs, backup_roots, allow_own_saved=False)
            if problem and "spaces" not in problem and "full path" not in problem:
                issues.append(HealthIssue("warning", f"{name}: {problem}", fix="same_folder", servers=[sid]))

    mods = {}
    for sid, _name, cfg in members:
        mods.setdefault(_mod_set(cfg.normalized_mods()), []).append(sid)
    if len(mods) > 1:
        issues.append(HealthIssue(
            "warning",
            "The maps run different mod lists. Modded dinos and items are lost when they are moved to a "
            "map that lacks the mod.",
            fix="same_mods",
            servers=[sid for sid, _n, _c in members],
        ))

    platforms = {(cfg.server_platform or "").strip() for _s, _n, cfg in members}
    if len(platforms) > 1:
        issues.append(HealthIssue(
            "warning",
            "The maps do not all allow the same platforms (crossplay), so some players cannot reach every map.",
            fix="same_platform",
            servers=[sid for sid, _n, _c in members],
        ))

    seen_ports: Dict[int, Tuple[str, str]] = {}  # port -> (server id, name) of its first user
    for sid, name, cfg in others + members:
        ports = [int(cfg.port), int(cfg.query_port)] + ([int(cfg.rcon_port)] if cfg.enable_rcon else [])
        for port in ports:
            owner = seen_ports.get(port)
            if owner is not None and owner[0] != sid and sid in names:
                if not any(i.fix == "new_ports" and i.servers == [sid] for i in issues):
                    issues.append(HealthIssue(
                        "error", f"{name} uses port {port}, which {owner[1]} uses too; they cannot run at the same time.",
                        fix="new_ports", servers=[sid],
                    ))
                continue
            seen_ports.setdefault(port, (sid, name))

    sessions: Dict[str, List[str]] = {}
    for sid, _name, cfg in members:
        sessions.setdefault((cfg.server_name or "").strip().lower(), []).append(sid)
    for _key, sids in sessions.items():
        if len(sids) > 1:
            issues.append(HealthIssue(
                "warning",
                "These maps have the same server name, so players cannot tell them apart in the server list: "
                + ", ".join(names[s] for s in sids),
                fix="session_names",
                servers=sids,
            ))

    worlds: Dict[Tuple[str, str, str], List[str]] = {}
    for sid, _name, cfg in members + others:
        key = (os.path.normcase(os.path.normpath(cfg.server_dir)), cfg.map_name.strip().lower(),
               (cfg.alt_save_directory_name or "").strip().lower())
        worlds.setdefault(key, []).append(sid)
    all_names = dict(names)
    all_names.update({sid: name for sid, name, _c in others})
    for sids in worlds.values():
        if len(sids) > 1 and any(s in names for s in sids):
            issues.append(HealthIssue(
                "error",
                "These profiles run the same world (same install, map and save folder); started together they "
                "overwrite each other's saves: " + ", ".join(all_names[s] for s in sids),
                servers=[s for s in sids if s in names],
            ))

    dirs: Dict[str, List[str]] = {}
    for sid, _name, cfg in members:
        dirs.setdefault(os.path.normcase(os.path.normpath(cfg.server_dir)), []).append(sid)
    for sids in dirs.values():
        if len(sids) > 1:
            issues.append(HealthIssue(
                "warning",
                "These maps share one install folder, and with it their GameUserSettings.ini and Game.ini: "
                + ", ".join(names[s] for s in sids) + ". Give each map its own install to run them together.",
                servers=sids,
            ))

    for sid, name, cfg in members:
        if not cfg.enable_rcon or not (cfg.admin_password or "").strip():
            issues.append(HealthIssue(
                "warning",
                f"{name}: RCON is off or has no admin password, so the world cannot be saved before a stop, "
                "and Broadcast and the player count do not work.",
                servers=[sid],
            ))
        if not server_installed(Path(cfg.server_dir)):
            issues.append(HealthIssue("info", f"{name} is not installed yet, or its install is unfinished (First Install on its Server tab).", servers=[sid]))
    return issues


CLUSTER_EXPORT_FORMAT = "ark-asa-manager-cluster"
# Never in an export file: secrets, and what only makes sense on this machine.
EXPORT_LEFT_OUT_FIELDS = {
    "server_dir", "backup_dir", "cluster_dir_override", "admin_password", "join_password",
    "discord_webhook_url", "discord_webhook_url_enc", "schema_version", "rcon_host",
}


def strip_arg_secrets(args: str) -> str:
    """Custom server arguments without any part that carries a password."""
    kept: List[str] = []
    for token in split_custom_start_args(args):
        if "password" not in token.lower():
            kept.append(token)
            continue
        if "?" in token:  # a ?Key=Value chain: drop only the password parts
            parts = [p for p in token.split("?") if "password" not in p.lower()]
            if any(parts):
                kept.append("?".join(parts))
    return " ".join(kept)


def cluster_export_document(
    name: str,
    cluster_id: str,
    stagger_sec: int,
    members: List[Tuple[str, AppConfig, Dict[str, str]]],
) -> Dict[str, Any]:
    """A portable cluster: name, delay, and per map its settings and staged INI text.
    members: (display name, settings, {"GameUserSettings.ini": text, "Game.ini": text})."""
    servers = []
    for display_name, cfg, ini in members:
        settings = {k: v for k, v in asdict(cfg).items() if k not in EXPORT_LEFT_OUT_FIELDS}
        settings["custom_start_args"] = strip_arg_secrets(cfg.custom_start_args)
        servers.append({
            "display_name": display_name,
            "settings": settings,
            "ini": {name: strip_ini_secrets(text) for name, text in ini.items()},
        })
    return {
        "format": CLUSTER_EXPORT_FORMAT,
        "version": 1,
        "exported": datetime.now().isoformat(timespec="seconds"),
        "cluster": {"id": cluster_id, "name": name, "start_stagger_sec": int(stagger_sec)},
        "servers": servers,
    }


INI_SECRET_KEYS = {"serveradminpassword", "serverpassword", "spectatorpassword"}


def strip_ini_secrets(text: str) -> str:
    """INI text without passwords, for a file meant to leave this machine. The manager
    writes the admin and join password into staging again on every start anyway."""
    doc = IniDocument.parse(text)
    for section, entries in doc.kv_entries_by_section().items():
        for key in {k for _i, k, _v in entries if k.strip().lower() in INI_SECRET_KEYS}:
            doc.remove_all_kv(section, key)
    return doc.to_text()


def read_cluster_export(path: Path) -> Dict[str, Any]:
    """Load and check an export file; raises ValueError with a readable reason."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Not a readable cluster file: {e}") from e
    if not isinstance(data, dict) or data.get("format") != CLUSTER_EXPORT_FORMAT:
        raise ValueError("This file is not a cluster exported by this manager.")
    servers = data.get("servers")
    if not isinstance(servers, list) or not servers:
        raise ValueError("The cluster file lists no servers.")
    if not isinstance(data.get("cluster", {}), dict):
        raise ValueError("The cluster file's cluster section is not readable.")
    for item in servers:
        if not isinstance(item, dict) or not isinstance(item.get("settings"), dict):
            raise ValueError("A server in the cluster file has no settings.")
        if not str(item["settings"].get("map_name") or "").strip():
            raise ValueError("A server in the cluster file has no map.")
        ini = item.get("ini", {})
        if not isinstance(ini, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in ini.items()):
            raise ValueError("A server in the cluster file has unreadable INI settings.")
    return data


def settings_from_export(values: Dict[str, Any]) -> AppConfig:
    """An AppConfig from exported settings: known fields only, each in its own type."""
    cfg = AppConfig()
    for f in fields(AppConfig):
        if f.name in EXPORT_LEFT_OUT_FIELDS or f.name not in values or values[f.name] is None:
            continue
        cur = getattr(cfg, f.name)
        v = values[f.name]
        try:
            if isinstance(cur, bool):
                setattr(cfg, f.name, safe_bool(v, cur))
            elif isinstance(cur, int):
                setattr(cfg, f.name, safe_int(v, cur))
            elif isinstance(cur, list):
                setattr(cfg, f.name, list(v) if isinstance(v, list) else cur)
            else:
                setattr(cfg, f.name, str(v))
        except Exception:
            pass
    if cfg.discord_enable and not cfg.discord_webhook_url.strip():
        cfg.discord_enable = False  # the webhook never travels in a cluster file
    return cfg


def pretty_map_name(map_name: str) -> str:
    """'ScorchedEarth_WP' -> 'Scorched Earth'."""
    base = re.sub(r"_WP$", "", (map_name or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base) or map_name


def map_folder_name(map_name: str) -> str:
    """Install folder name for a map: 'ScorchedEarth_WP' -> 'ScorchedEarth'."""
    base = re.sub(r"_WP$", "", (map_name or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"[^A-Za-z0-9_-]+", "", base) or "Map"


def cluster_folder_problem(
    target: Optional[Path],
    server_dirs: List[Path],
    backup_roots: List[Path],
    *,
    allow_own_saved: bool = True,
) -> Optional[str]:
    """Why a cluster data folder cannot be used, or None when it can.

    allow_own_saved lets a server join a cluster that lives in its own install's
    Saved\\clusters (the setup dialog); the wizard never points new maps in there.
    """
    if target is None or not str(target).strip():
        return "Choose a cluster data folder."
    if not target.is_absolute():
        return "Use a full path for the cluster data folder, such as D:\\ARK-Clusters\\<id>."
    if " " in str(target):
        return "Pick a cluster data folder whose path has no spaces; ARK cuts the path at the first space."
    for d in server_dirs:
        # The install's own Saved\clusters is allowed only to join a cluster that already lives there.
        if path_contains(d, target) and not (allow_own_saved and same_path(target, server_cluster_data_dir(d))):
            return "Keep the cluster data folder outside the server installs, so updates and restores never touch it."
        if path_contains(target, d):
            return ("That folder contains a server install. Give the cluster a folder of its own, "
                    "for example D:\\ARK-Clusters\\<id>.")
    for b in backup_roots:
        if path_contains(target, b):
            return ("That folder contains the backup folder. Give the cluster a folder of its own, "
                    "for example D:\\ARK-Clusters\\<id>.")
    return None

# =============================================================================
# SINGLE PLAYER IMPORT
# =============================================================================

@dataclass(frozen=True)
class SinglePlayerSave:
    map_name: str
    folder: Path
    world_file: Optional[Path]
    profiles: Tuple[Path, ...]
    tribes: Tuple[Path, ...]
    modified: float
    size_bytes: int

    def file_count(self) -> int:
        return (1 if self.world_file else 0) + len(self.profiles) + len(self.tribes)


@dataclass
class SinglePlayerImportResult:
    destination: Optional[Path] = None
    files_copied: int = 0
    backup_zip: Optional[Path] = None
    tribe_ids: List[str] = field(default_factory=list)
    settings_source: Optional[Path] = None
    floats_tidied: int = 0
    mods_found: List[str] = field(default_factory=list)
    mods_previous: str = ""
    gus_applied: int = 0
    gus_preserved: List[str] = field(default_factory=list)
    game_applied: int = 0
    game_preserved: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def steam_install_path() -> Optional[Path]:
    if winreg is None:
        return None
    for hive, subkey in (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
    ):
        try:
            with reg_open_key_64(hive, subkey) as key:
                for name in ("SteamPath", "InstallPath"):
                    try:
                        value = str(winreg.QueryValueEx(key, name)[0]).strip()
                    except OSError:
                        continue
                    if value:
                        return Path(value)
        except Exception:
            continue
    return None


def steam_library_roots() -> List[Path]:
    """Steam install plus every extra library folder listed in libraryfolders.vdf."""
    steam = steam_install_path()
    if steam is None:
        return []

    roots: List[Path] = [steam]
    try:
        vdf = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return roots

    for match in re.finditer(r'"path"\s*"([^"]+)"', vdf):
        raw = match.group(1).replace("\\\\", "\\").strip()
        if not raw:
            continue
        candidate = Path(raw)
        if candidate not in roots:
            roots.append(candidate)
    return roots


def singleplayer_candidate_roots() -> List[Path]:
    """Known locations of an ASA single player 'Saved' folder, best first."""
    roots: List[Path] = []

    for lib in steam_library_roots():
        roots.append(lib / "steamapps" / "common" / ASA_STEAM_APP_DIR / "ShooterGame" / "Saved")

    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        base = Path(local)
        roots.append(base / "ArkSurvivalAscended" / "Saved")
        # Microsoft Store / Game Pass build keeps its own redirected LocalAppData.
        try:
            for pkg in (base / "Packages").glob("StudioWildcard.*"):
                roots.append(pkg / "LocalCache" / "Local" / "ArkSurvivalAscended" / "Saved")
        except OSError:
            pass
    return roots


def folder_holds_saves(folder: Path) -> bool:
    """True when this folder holds .ark files directly or one level down."""
    if not folder.is_dir():
        return False
    if _sp_files_with_suffix(folder, SP_WORLD_SUFFIXES):
        return True
    try:
        for child in folder.iterdir():
            if child.is_dir() and _sp_files_with_suffix(child, SP_WORLD_SUFFIXES):
                return True
    except OSError:
        return False
    return False


def resolve_singleplayer_saves_root(chosen: Path) -> Optional[Path]:
    """Use the chosen folder exactly as given when it holds saves, else probe the usual subfolders."""
    candidates: List[Path] = [chosen]
    for base in (chosen, chosen / "Saved", chosen / "ShooterGame" / "Saved"):
        candidates.extend(base / name for name in SP_SAVED_ARKS_DIR_NAMES)
    for candidate in candidates:
        if folder_holds_saves(candidate):
            return candidate
    return None


def _holds_singleplayer_ini(folder: Path) -> bool:
    try:
        return (folder / "GameUserSettings.ini").is_file() or (folder / "Game.ini").is_file()
    except OSError:
        return False


def resolve_singleplayer_config_dir(chosen: Path) -> Optional[Path]:
    """Find the Config folder for whatever the user pointed at, walking upward if needed.

    A world folder sits at <Saved>\\SavedArksLocal\\<Map>, so the INIs next to it in
    <Saved>\\Config\\Windows are two levels up; browsing straight to a map folder is common.
    """
    if _holds_singleplayer_ini(chosen):
        return chosen

    ancestors: List[Path] = [chosen, *list(chosen.parents)[:SP_CONFIG_SEARCH_DEPTH]]
    for ancestor in ancestors:
        for base in (ancestor, ancestor / "Saved", ancestor / "ShooterGame" / "Saved"):
            for platform in SP_CONFIG_PLATFORM_DIRS:
                candidate = base / SP_CONFIG_DIR_NAME / platform
                if _holds_singleplayer_ini(candidate):
                    return candidate
    return None


def detect_singleplayer_root() -> Optional[Path]:
    for root in singleplayer_candidate_roots():
        if resolve_singleplayer_saves_root(root) is not None:
            return root
    for root in singleplayer_candidate_roots():
        if root.is_dir():
            return root
    return None


def _sp_files_with_suffix(folder: Path, suffixes: Tuple[str, ...]) -> Tuple[Path, ...]:
    try:
        return tuple(
            p for p in sorted(folder.iterdir())
            if p.is_file() and p.suffix.lower() in suffixes
        )
    except OSError:
        return tuple()


def _sp_build_save(folder: Path, map_name: str, world_file: Optional[Path]) -> Optional[SinglePlayerSave]:
    profiles = _sp_files_with_suffix(folder, SP_PROFILE_SUFFIXES)
    tribes = _sp_files_with_suffix(folder, SP_TRIBE_SUFFIXES)
    files = [p for p in (world_file, *profiles, *tribes) if p is not None]
    if not files:
        return None

    size = 0
    modified = 0.0
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        size += st.st_size
        modified = max(modified, st.st_mtime)

    return SinglePlayerSave(
        map_name=map_name,
        folder=folder,
        world_file=world_file,
        profiles=profiles,
        tribes=tribes,
        modified=modified,
        size_bytes=size,
    )


def discover_singleplayer_saves(chosen: Path) -> List[SinglePlayerSave]:
    """List importable single player worlds at (or just below) the chosen folder."""
    saves_root = resolve_singleplayer_saves_root(chosen)
    found: List[SinglePlayerSave] = []
    if saves_root is None:
        return found

    try:
        folders = sorted(p for p in saves_root.iterdir() if p.is_dir())
    except OSError:
        folders = []

    for folder in folders:
        world: Optional[Path] = folder / f"{folder.name}.ark"
        if not world.is_file():
            candidates = _sp_files_with_suffix(folder, SP_WORLD_SUFFIXES)
            world = candidates[0] if candidates else None
        if world is None:
            continue
        save = _sp_build_save(folder, folder.name, world)
        if save is not None:
            found.append(save)

    # Flat layout: <saves root>\<Map>.ark sitting next to shared profile/tribe files.
    for world in _sp_files_with_suffix(saves_root, SP_WORLD_SUFFIXES):
        save = _sp_build_save(saves_root, world.stem, world)
        if save is not None:
            found.append(save)

    found.sort(key=lambda s: s.modified, reverse=True)
    return found


def parse_mod_id_list(raw: str) -> List[str]:
    """Split an ActiveMods / -mods= list into de-duplicated ids, dropping the 0 placeholder."""
    out: List[str] = []
    for token in re.split(r"[,;\s]+", (raw or "").strip()):
        token = token.strip()
        if not token or token == "0":
            continue
        if token not in out:
            out.append(token)
    return out


def read_singleplayer_active_mods(sp_root: Path) -> List[str]:
    """Mod ids the client has enabled, read from ActiveMods in its GameUserSettings.ini."""
    cfg_dir = resolve_singleplayer_config_dir(sp_root)
    if cfg_dir is None:
        return []

    gus_path = cfg_dir / "GameUserSettings.ini"
    if not gus_path.is_file():
        return []

    raw = ""
    for line in read_ini(gus_path).lines:
        if line.kind == "kv" and line.key.strip().lower() == SP_ACTIVE_MODS_KEY.lower():
            raw = line.value
    return parse_mod_id_list(raw)


def singleplayer_tribe_ids(save: SinglePlayerSave) -> List[str]:
    """Tribe ids in a save, taken from its .arktribe / .tribebak file names."""
    ids = [p.stem for p in save.tribes if SP_TRIBE_ID_RE.match(p.stem)]
    return list(dict.fromkeys(ids))


def server_saves_root(server_dir: Path, alt_save_directory_name: str = "") -> Path:
    name = (alt_save_directory_name or "").strip() or SERVER_SAVED_ARKS_DIR_NAME
    return server_saved_dir(server_dir) / name


def backup_server_save_folder(
    folder: Path,
    backup_root: Path,
    logger: logging.Logger,
    manifest: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """Zip the server save folder about to be overwritten. None when there is nothing to keep."""
    if not folder.is_dir():
        return None
    files = [p for p in folder.rglob("*") if p.is_file()]
    if not files:
        return None

    ensure_dir(backup_root)
    out_zip = reserve_zip_path(backup_root, f"ASA_PreImport_{folder.name}_{now_ts()}")
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, arcname=p.relative_to(folder).as_posix())
        if manifest is not None:
            z.writestr(BACKUP_MANIFEST_NAME, json.dumps(manifest, indent=2))
    logger.info(f"Pre-import backup created: {out_zip}")
    return out_zip


def import_singleplayer_save(
    save: SinglePlayerSave,
    server_dir: Path,
    alt_save_directory_name: str,
    logger: logging.Logger,
    *,
    include_profiles: bool = True,
    include_tribes: bool = True,
    backup_root: Optional[Path] = None,
    backup_info: Optional[Dict[str, Any]] = None,
    result: Optional[SinglePlayerImportResult] = None,
) -> SinglePlayerImportResult:
    res = result if result is not None else SinglePlayerImportResult()

    if save.world_file is None:
        raise ValueError(f"No .ark world file found in {save.folder}")

    dest_dir = server_saves_root(server_dir, alt_save_directory_name) / save.map_name
    res.destination = dest_dir

    if backup_root is not None:
        res.backup_zip = backup_server_save_folder(dest_dir, backup_root, logger, backup_info)

    ensure_dir(dest_dir)

    sources: List[Path] = [save.world_file]
    if include_profiles:
        sources.extend(save.profiles)
    if include_tribes:
        sources.extend(save.tribes)

    for src in sources:
        shutil.copy2(src, dest_dir / src.name)
        res.files_copied += 1

    if include_tribes:
        res.tribe_ids = singleplayer_tribe_ids(save)

    logger.info(f"Imported {res.files_copied} single player save file(s) into {dest_dir}")
    return res


_INI_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _shortest_float32_text(value: float) -> str:
    """The shortest decimal that still round-trips to the same float32.

    Plain decimal notation is tried first: %g would turn 10.0 into "1e+01", which ARK
    parses but nobody wants to read in the INI editor.
    """
    if abs(value) < 1e16:
        for decimals in range(0, 10):
            text = f"{value:.{decimals}f}"
            if _float32(float(text)) == value:
                return text

    for precision in range(1, 18):
        text = f"{value:.{precision}g}"
        if _float32(float(text)) == value:
            return text
    return repr(value)


def tidy_ini_float(raw: str, snap: bool = True) -> str:
    """Rewrite ARK float spew as a readable number.

    Shortening is lossless: 0.00999999978 and 0.01 are the same float32, so the value
    the server reads does not change. Snapping is not - the settings sliders write
    0.999989986 where the user picked 1 - so it is kept behind a flag.
    """
    stripped = raw.strip()
    if not _INI_FLOAT_RE.match(stripped):
        return raw

    try:
        value = _float32(float(stripped))
    except (ValueError, OverflowError):
        return raw

    # Past float32's exact integer range the shortened form looks like a different
    # number (123456789 reads back as 123456792), so leave the game's own text alone.
    if abs(value) >= SP_FLOAT_MAX_EXACT:
        return raw

    # Snapping is an absolute correction of about 1e-5, so it must not be applied to
    # values small enough for that to swamp them - 0.0000001 would become 0.
    if snap and abs(value) >= SP_FLOAT_SNAP_MIN_MAGNITUDE:
        snapped = round(value, SP_FLOAT_SNAP_DECIMALS)
        if snapped != value and abs(value - snapped) <= max(abs(value), 1.0) * SP_FLOAT_SNAP_TOLERANCE:
            value = _float32(snapped)

    if value == 0.0:
        value = 0.0  # collapse -0.0

    cleaned = _shortest_float32_text(value)
    return cleaned if cleaned != stripped else raw


def _canonical_section_name(doc: IniDocument, section: str) -> str:
    """Reuse the destination spelling of a section so no duplicate header is created."""
    target = section.strip().lower()
    for line in doc.lines:
        if line.kind == "section" and line.section.strip().lower() == target:
            return line.section
    return section.strip()


def merge_ini_sections(
    dest: IniDocument,
    src: IniDocument,
    *,
    skip_sections: Set[str],
    protected_keys: Dict[str, Set[str]],
    protected_anywhere: Optional[Set[str]] = None,
    tidy_floats: bool = True,
    snap_floats: bool = True,
) -> Tuple[int, List[str], int]:
    """Copy src values into dest section by section.

    Returns (applied, preserved labels, floats tidied).
    """
    applied = 0
    preserved: List[str] = []
    tidied = 0

    for section, entries in src.kv_entries_by_section().items():
        sec_l = section.strip().lower()
        if not sec_l or sec_l in skip_sections:
            continue

        prot = set(protected_keys.get(sec_l, set()))
        if protected_anywhere:
            prot |= protected_anywhere
        dest_section = _canonical_section_name(dest, section)

        ordered_keys: List[str] = []
        values_by_key: Dict[str, List[str]] = {}
        for _idx, key, value in entries:
            k = key.strip()
            if not k:
                continue
            k_l = k.lower()
            if k_l in prot:
                label = f"[{section}] {k}"
                if label not in preserved:
                    preserved.append(label)
                continue
            if tidy_floats:
                cleaned = tidy_ini_float(value, snap=snap_floats)
                if cleaned != value:
                    tidied += 1
                    value = cleaned
            if k_l not in values_by_key:
                ordered_keys.append(k)
                values_by_key[k_l] = []
            values_by_key[k_l].append(value)

        for k in ordered_keys:
            values = values_by_key[k.lower()]
            dest.remove_all_kv(dest_section, k)
            if len(values) == 1:
                dest.set(dest_section, k, values[0])
            else:
                dest.ensure_section(dest_section)
                for v in values:
                    dest.append_kv(dest_section, k, v)
            applied += len(values)

    return applied, preserved, tidied


def import_singleplayer_settings(
    sp_root: Path,
    app_base: Path,
    server_id: str,
    server_dir: Path,
    logger: logging.Logger,
    *,
    server_running: bool = False,
    import_gus: bool = True,
    import_game: bool = True,
    tidy_floats: bool = True,
    snap_floats: bool = True,
    result: Optional[SinglePlayerImportResult] = None,
) -> SinglePlayerImportResult:
    """Merge the single player GameUserSettings.ini / Game.ini into this server INI staging."""
    res = result if result is not None else SinglePlayerImportResult()
    cfg_dir = resolve_singleplayer_config_dir(sp_root)
    if cfg_dir is None:
        res.notes.append(
            f"No GameUserSettings.ini or Game.ini found near {sp_root}. "
            "Point the Saved Folder at the game's ShooterGame\\Saved folder and try again."
        )
        logger.info("Single player config folder not found; settings import skipped.")
        return res

    res.settings_source = cfg_dir
    logger.info(f"Single player settings source: {cfg_dir}")

    protected_anywhere = set(SP_NEVER_IMPORT_KEYS) | SP_MOD_KEYS_LOWER

    jobs: List[Tuple[str, Path, Set[str]]] = []
    if import_gus:
        jobs.append(("gus", cfg_dir / "GameUserSettings.ini", set(SP_GUS_CLIENT_ONLY_SECTIONS)))
    if import_game:
        jobs.append(("game", cfg_dir / "Game.ini", set(SP_GAME_CLIENT_ONLY_SECTIONS)))

    for target, src_path, skip_sections in jobs:
        if not src_path.is_file():
            res.notes.append(f"Single player {src_path.name} not found in {cfg_dir}")
            logger.info(f"Single player {src_path.name} not found; skipped.")
            continue

        paths = ini_stage_paths(app_base, server_id, server_dir, target)
        ensure_ini_staging_synced(paths, server_running=server_running, logger=logger)

        dest_doc = read_ini(paths.stage)
        src_doc = read_ini(src_path)
        applied, preserved, tidied = merge_ini_sections(
            dest_doc,
            src_doc,
            skip_sections=skip_sections,
            protected_keys=SP_PROTECTED_INI_KEYS,
            protected_anywhere=protected_anywhere,
            tidy_floats=tidy_floats,
            snap_floats=snap_floats,
        )
        write_ini(paths.stage, dest_doc)
        res.floats_tidied += tidied

        if target == "gus":
            res.gus_applied = applied
            res.gus_preserved = preserved
        else:
            res.game_applied = applied
            res.game_preserved = preserved

        logger.info(f"Imported {applied} setting(s) from single player {src_path.name} into staged {paths.stage.name}.")
        if preserved:
            logger.info(f"Kept server-managed values: {', '.join(preserved)}")

    return res

# Spawn container entries name containers of one map; on another map they mean nothing.
MAP_SPECIFIC_INI_KEYS = {
    "configaddnpcspawnentriescontainer",
    "configsubtractnpcspawnentriescontainer",
    "configoverridenpcspawnentriescontainer",
    "configoverridesupplycrateitems",  # crate classes differ per map
}
# Bound to the machine or the server, never copied between servers.
NEVER_COPY_INI_KEYS = {"multihome"}


@dataclass
class IniCopyResult:
    applied: Dict[str, int] = field(default_factory=dict)  # "GameUserSettings.ini" -> values written
    preserved: List[str] = field(default_factory=list)


def copy_ini_settings(
    app_base: Path,
    source_id: str,
    source_dir: Path,
    target_id: str,
    target_dir: Path,
    logger: logging.Logger,
    *,
    source_running: bool,
    target_running: bool,
    include_gus: bool = True,
    include_game: bool = True,
    include_map_specific: bool = False,
    sync_source: bool = True,
) -> IniCopyResult:
    """Merge one server's staged INI settings into another server's staging.

    Each server keeps what belongs to it: name, ports, passwords, RCON and its mod list.
    Values only the target has are kept. Applied on the target's next start, like any
    INI Editor change.
    """
    res = IniCopyResult()
    protected_anywhere = set(SP_MOD_KEYS_LOWER) | NEVER_COPY_INI_KEYS
    if not include_map_specific:
        protected_anywhere |= MAP_SPECIFIC_INI_KEYS
    for target, enabled in (("gus", include_gus), ("game", include_game)):
        if not enabled:
            continue
        src_paths = ini_stage_paths(app_base, source_id, source_dir, target)
        if sync_source:  # off when the caller synced it once for several copies running at once
            ensure_ini_staging_synced(src_paths, server_running=source_running, logger=logger)
        dst_paths = ini_stage_paths(app_base, target_id, target_dir, target)
        ensure_ini_staging_synced(dst_paths, server_running=target_running, logger=logger)
        dest_doc = read_ini(dst_paths.stage)
        applied, preserved, _ = merge_ini_sections(
            dest_doc,
            read_ini(src_paths.stage),
            skip_sections=set(),
            protected_keys=SP_PROTECTED_INI_KEYS,
            protected_anywhere=protected_anywhere,
            tidy_floats=False,
        )
        write_ini(dst_paths.stage, dest_doc)
        res.applied[dst_paths.stage.name] = applied
        for label in preserved:
            if label not in res.preserved:
                res.preserved.append(label)
        logger.info(f"Copied {applied} setting(s) into staged {dst_paths.stage.name} from another server.")
    return res


# =============================================================================
# BACKUP
# =============================================================================

def resolve_backup_root(cfg: AppConfig, app_base: Path) -> Path:
    raw = (cfg.backup_dir or "").strip()
    return Path(raw) if raw else (app_base / BACKUP_DIR_NAME)


def backup_manifest(
    kind: str,
    server_id: str = "",
    server_name: str = "",
    map_name: str = "",
    cluster_id: str = "",
) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "format": 1,
        "kind": kind,
        "server_id": server_id,
        "server_name": server_name,
        "map": map_name,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    if cluster_id:
        manifest["cluster_id"] = cluster_id
    return manifest


def read_backup_manifest(path: Path) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as z:
            if BACKUP_MANIFEST_NAME not in z.namelist():
                return {}
            data = json.loads(z.read(BACKUP_MANIFEST_NAME).decode("utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _zip_server_tree(
    out_zip: Path,
    server_dir: Path,
    roots: List[Path],
    manifest: Optional[Dict[str, Any]],
    extra_roots: Optional[List[Tuple[Path, str]]] = None,
    skipped_out: Optional[List[str]] = None,
) -> int:
    """Zip every file under roots, stored relative to the server folder, each file once.

    extra_roots are folders outside the server folder, stored under their own arc prefix.
    They may be shared with servers that are running, so a file that cannot be read there
    is skipped (and listed in skipped_out) rather than failing the whole zip.
    """
    base = server_dir.resolve()
    own_zip = out_zip.resolve()
    seen: Set[str] = set()
    sources = [(root.resolve(), None) for root in roots]
    sources += [(root.resolve(), prefix) for root, prefix in (extra_roots or [])]
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, prefix in sources:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.resolve() == own_zip:
                    continue
                if prefix is None:
                    arc = path.relative_to(base).as_posix()
                else:
                    arc = prefix + path.relative_to(root).as_posix()
                if arc in seen:
                    continue
                if prefix is None:
                    z.write(path, arcname=arc)
                else:
                    # Read whole first: a read failing part way through z.write would still
                    # leave a truncated entry in the zip.
                    try:
                        info = zipfile.ZipInfo.from_file(path, arcname=arc)
                        data = path.read_bytes()
                    except OSError as e:
                        if skipped_out is not None:
                            skipped_out.append(f"{path}: {e}")
                        continue
                    info.compress_type = zipfile.ZIP_DEFLATED
                    z.writestr(info, data)
                seen.add(arc)
        if manifest is not None:
            z.writestr(BACKUP_MANIFEST_NAME, json.dumps(manifest, indent=2))
    return len(seen)


def prune_server_backups(
    target_dir: Path,
    server_id: str,
    retention: Any,
    logger: logging.Logger,
    prefix: str = BACKUP_FULL_PREFIX,
) -> None:
    """Keep the newest zips of this server that carry the given prefix.

    Backups and auto-saves use different prefixes, so each is only ever pruned against
    its own count. Several profiles can share one backup folder; a zip whose manifest
    names another server is left alone. Zips from before manifests existed are pruned
    as before.
    """
    try:
        keep = max(1, int(retention))
    except Exception:
        return

    mine: List[Path] = []
    for p in target_dir.glob(f"{prefix}*.zip"):
        owner = str(read_backup_manifest(p).get("server_id", "") or "")
        if owner and server_id and owner != server_id:
            continue
        mine.append(p)

    def modified(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    mine.sort(key=modified, reverse=True)
    for old in mine[keep:]:
        try:
            old.unlink()
            logger.info(f"Retention: deleted {old.name}")
        except Exception:
            pass


def _write_server_zip(
    cfg: AppConfig,
    app_base: Path,
    logger: logging.Logger,
    server_id: str,
    *,
    prefix: str,
    kind: str,
    retention: Any,
    label: str,
) -> Optional[Path]:
    server_dir = Path(cfg.server_dir)
    saved = server_saved_dir(server_dir)
    if not saved.exists():
        logger.info(f"{label} skipped: Saved folder not found.")
        return None

    target_dir = resolve_backup_root(cfg, app_base)
    ensure_dir(target_dir)
    out_zip = reserve_zip_path(target_dir, f"{prefix}{now_ts()}")

    logger.info(f"Creating {label.lower()}: {out_zip}")
    # The config folder lives inside Saved, so zipping Saved already covers it. So does
    # cluster data on the per-install default; a shared cluster folder has to be added.
    # Only full backups carry it: every map of the cluster zips the same folder, and
    # auto-saves running on each map would multiply that many times over.
    extra: List[Tuple[Path, str]] = []
    cluster_dir = effective_cluster_dir(cfg)
    if kind == BACKUP_KIND_FULL and cluster_dir is not None and cluster_dir.is_dir():
        if path_contains(saved, cluster_dir):
            pass  # already inside the Saved zip
        elif path_contains(cluster_dir, server_dir) or path_contains(cluster_dir, target_dir):
            # Zipping it would take in whole installs, other maps and the backups themselves.
            logger.info(f"{label}: cluster folder {cluster_dir} contains the install or the backup folder; left out.")
        else:
            extra.append((cluster_dir, f"{CLUSTER_ZIP_PREFIX}{cfg.cluster_id.strip()}/"))
            logger.info(f"Including cluster data: {cluster_dir}")
    skipped: List[str] = []
    try:
        _zip_server_tree(
            out_zip,
            server_dir,
            [saved],
            backup_manifest(kind, server_id, cfg.server_name, cfg.map_name, cfg.cluster_id.strip() if cfg.cluster_enable else ""),
            extra_roots=extra,
            skipped_out=skipped,
        )
    except Exception:
        # A half-written zip would be listed as a backup and count towards retention.
        with contextlib.suppress(OSError):
            out_zip.unlink()
        raise
    if skipped:
        logger.info(f"{label}: skipped {len(skipped)} cluster file(s) in use by another server, e.g. {skipped[0]}")
    logger.info(f"{label} completed.")

    prune_server_backups(target_dir, server_id, retention, logger, prefix=prefix)
    return out_zip


def backup_server(cfg: AppConfig, app_base: Path, logger: logging.Logger, server_id: str = "") -> Optional[Path]:
    return _write_server_zip(
        cfg, app_base, logger, server_id,
        prefix=BACKUP_FULL_PREFIX, kind=BACKUP_KIND_FULL, retention=cfg.backup_retention, label="Backup",
    )


def auto_save_server(cfg: AppConfig, app_base: Path, logger: logging.Logger, server_id: str = "") -> Optional[Path]:
    """Zip the save as an auto-save; these rotate in their own slots, apart from backups."""
    return _write_server_zip(
        cfg, app_base, logger, server_id,
        prefix=AUTO_SAVE_PREFIX, kind=BACKUP_KIND_AUTO_SAVE,
        retention=clamp_auto_save_keep(cfg.auto_save_retention), label="Auto-save",
    )


# -- auto-saves ----------------------------------------------------------------

def clamp_auto_save_minutes(value: Any) -> int:
    minutes = safe_int(value, AUTO_SAVE_DEFAULT_INTERVAL_MIN)
    if minutes <= 0:
        minutes = AUTO_SAVE_DEFAULT_INTERVAL_MIN
    return max(AUTO_SAVE_MIN_INTERVAL_MIN, min(AUTO_SAVE_MAX_INTERVAL_MIN, minutes))


def describe_span(seconds: float) -> str:
    """'45 minutes', '10 hours', '2 days 6 hours' - for sentences, unlike format_duration."""
    minutes = max(0, int(round(seconds / 60)))
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)

    def unit(n: int, word: str) -> str:
        return f"{n} {word}" + ("" if n == 1 else "s")

    if days:
        return unit(days, "day") + (f" {unit(hours, 'hour')}" if hours else "")
    if hours:
        return unit(hours, "hour") + (f" {unit(mins, 'minute')}" if mins else "")
    return unit(mins, "minute")


def clamp_auto_save_keep(value: Any) -> int:
    keep = safe_int(value, AUTO_SAVE_DEFAULT_KEEP)
    if keep <= 0:
        keep = AUTO_SAVE_DEFAULT_KEEP
    return min(AUTO_SAVE_MAX_KEEP, keep)


def auto_save_interval_seconds(cfg: AppConfig) -> Optional[float]:
    """Seconds between auto-saves, or None when they are off."""
    if not cfg.auto_save_enabled:
        return None
    return float(clamp_auto_save_minutes(cfg.auto_save_interval_minutes) * 60)


class AutoSaveScheduler:
    """Decides when the next auto-save is due; the clock is passed in.

    The countdown starts when the server is seen running and resets whenever it
    stops, the feature is turned off, or the interval changes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_due: Optional[float] = None
        self._interval: Optional[float] = None

    def tick(self, now: float, interval: Optional[float], server_running: bool) -> bool:
        """True when an auto-save should start now; call mark_started() once it has."""
        with self._lock:
            if interval is None or not server_running:
                self._next_due = None
                self._interval = None
                return False
            if self._next_due is None or interval != self._interval:
                self._interval = interval
                self._next_due = now + interval
                return False
            return now >= self._next_due

    def mark_started(self, now: float) -> None:
        with self._lock:
            if self._interval is not None:
                self._next_due = now + self._interval

    def seconds_until_due(self, now: float) -> Optional[float]:
        with self._lock:
            return None if self._next_due is None else max(0.0, self._next_due - now)


# -- listing and restoring ---------------------------------------------------

SERVER_SAVED_ZIP_PREFIX = "ShooterGame/Saved/"
_BACKUP_NAME_RE = re.compile(
    r"^ASA_(?:Backup|AutoSave|PreImport|PreRestore)_(?:(?P<map>.+)_)?"
    r"(?P<ts>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:-\d+)?\.zip$",
    re.IGNORECASE,
)


@dataclass
class ServerBackup:
    path: Path
    kind: str = "unknown"
    created: float = 0.0
    size_bytes: int = 0
    server_id: str = ""
    server_name: str = ""
    layout: str = ""  # "server": paths relative to the server folder; "map": a bare map folder
    map_prefixes: Dict[str, str] = field(default_factory=dict)  # map -> zip member prefix
    config_members: Dict[str, str] = field(default_factory=dict)  # "gus" / "game" -> member
    file_count: int = 0
    error: str = ""

    @property
    def maps(self) -> List[str]:
        return sorted(self.map_prefixes)

    @property
    def restorable(self) -> bool:
        return not self.error and bool(self.map_prefixes or self.config_members)


def _session_name_from_ini(data: bytes) -> str:
    try:
        doc = IniDocument.parse(data.decode("utf-8-sig", errors="ignore"))
    except Exception:
        return ""
    values = doc.get_last_value_map()
    for section in ("SessionSettings", "/Script/Engine.GameSession"):
        name = (values.get(section, {}).get("SessionName") or "").strip()
        if name:
            return name
    return ""


def read_server_backup(path: Path) -> ServerBackup:
    """Describe a backup zip from its contents; the file name is only a fallback."""
    b = ServerBackup(path=path)
    try:
        st = path.stat()
    except OSError as e:
        b.error = str(e)
        return b
    b.size_bytes = st.st_size
    b.created = st.st_mtime

    for prefix, kind in BACKUP_ZIP_PREFIXES:
        if path.name.startswith(prefix):
            b.kind = kind
            break
    name_match = _BACKUP_NAME_RE.match(path.name)
    if name_match:
        try:
            b.created = datetime.strptime(name_match.group("ts"), "%Y-%m-%d_%H-%M-%S").timestamp()
        except ValueError:
            pass

    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in dict.fromkeys(z.namelist()) if not n.endswith("/")]
            manifest: Dict[str, Any] = {}
            if BACKUP_MANIFEST_NAME in names:
                try:
                    loaded = json.loads(z.read(BACKUP_MANIFEST_NAME).decode("utf-8"))
                    manifest = loaded if isinstance(loaded, dict) else {}
                except Exception:
                    manifest = {}
                names.remove(BACKUP_MANIFEST_NAME)

            b.file_count = len(names)
            b.kind = str(manifest.get("kind") or b.kind)
            b.server_id = str(manifest.get("server_id") or "")
            b.server_name = str(manifest.get("server_name") or "")

            if any(n.startswith(SERVER_SAVED_ZIP_PREFIX) for n in names):
                b.layout = "server"
                for n in names:
                    if not n.startswith(SERVER_SAVED_ZIP_PREFIX) or not n.lower().endswith(".ark"):
                        continue
                    parts = n[len(SERVER_SAVED_ZIP_PREFIX):].split("/")
                    # <SavedArks or AltSaveDirectoryName>/<map>/<file>.ark
                    if len(parts) < 3 or parts[0].lower() == "config":
                        continue
                    b.map_prefixes.setdefault(parts[1], f"{SERVER_SAVED_ZIP_PREFIX}{parts[0]}/{parts[1]}/")
                for key, rel in (("gus", GAMEUSERSETTINGS_REL), ("game", GAME_INI_REL)):
                    member = rel.as_posix()
                    if member in names:
                        b.config_members[key] = member
                if not b.server_name and "gus" in b.config_members:
                    b.server_name = _session_name_from_ini(z.read(b.config_members["gus"]))
            else:
                top_arks = [n for n in names if "/" not in n and n.lower().endswith(".ark")]
                if top_arks:
                    b.layout = "map"
                    # The folder also keeps dated copies (TheIsland_WP_19.09.2026_00.40.52.ark);
                    # the live world is the shortest name.
                    map_name = str(manifest.get("map") or "")
                    if not map_name and name_match and name_match.group("map"):
                        map_name = name_match.group("map")
                    if not map_name:
                        map_name = min((Path(n).stem for n in top_arks), key=len)
                    b.map_prefixes[map_name] = ""
    except (zipfile.BadZipFile, OSError) as e:
        b.error = f"Unreadable zip: {e}"

    if not b.error and not b.restorable:
        b.error = "No world save or server settings inside."
    return b


def list_server_backups(backup_root: Path) -> List[ServerBackup]:
    if not backup_root.is_dir():
        return []
    items = [read_server_backup(p) for p in backup_root.glob("*.zip") if p.is_file()]
    items.sort(key=lambda b: b.created, reverse=True)
    return items


def backup_owner_match(backup: ServerBackup, server_id: str, server_name: str) -> Optional[bool]:
    """True when the zip is this server's, False when it is provably another's, None if unknown."""
    if backup.server_id and server_id:
        return backup.server_id == server_id
    if backup.server_name and server_name:
        return backup.server_name.strip().lower() == server_name.strip().lower()
    return None


@dataclass
class BackupRestoreResult:
    safety_zip: Optional[Path] = None
    destination: Optional[Path] = None
    maps_restored: List[str] = field(default_factory=list)
    files_written: int = 0
    config_restored: List[str] = field(default_factory=list)


def _member_target(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    base = root.resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"Refusing to extract outside the save folder: {rel}")
    return target


def create_pre_restore_backup(
    server_dir: Path,
    alt_save_directory_name: str,
    backup_root: Path,
    logger: logging.Logger,
    server_id: str = "",
    server_name: str = "",
) -> Optional[Path]:
    """Zip the saves and INIs a restore is about to replace, in the same layout as a full backup."""
    roots = [
        p for p in (server_saves_root(server_dir, alt_save_directory_name), server_config_dir(server_dir))
        if p.is_dir() and any(q.is_file() for q in p.rglob("*"))
    ]
    if not roots:
        return None
    ensure_dir(backup_root)
    out_zip = reserve_zip_path(backup_root, f"ASA_PreRestore_{now_ts()}")
    count = _zip_server_tree(
        out_zip, server_dir, roots, backup_manifest(BACKUP_KIND_PRE_RESTORE, server_id, server_name)
    )
    logger.info(f"Pre-restore backup created: {out_zip} ({count} file(s))")
    return out_zip


def restore_server_backup(
    backup: ServerBackup,
    server_dir: Path,
    alt_save_directory_name: str,
    app_base: Path,
    server_id: str,
    logger: logging.Logger,
    *,
    restore_world: bool = True,
    restore_config: bool = True,
    safety_root: Optional[Path] = None,
    server_name: str = "",
) -> BackupRestoreResult:
    """Put a backup back on the server.

    Each map folder is rebuilt beside the live one and swapped in, so a failure part way
    leaves the current save untouched. Restored INIs replace the live copy and the INI
    staging together, so the next start applies exactly what the backup held.
    """
    maps = backup.maps if restore_world else []
    configs = dict(backup.config_members) if restore_config else {}
    if not maps and not configs:
        raise ValueError("Nothing selected to restore from this backup.")

    res = BackupRestoreResult()
    saves_root = server_saves_root(server_dir, alt_save_directory_name)
    res.destination = saves_root

    if safety_root is not None:
        res.safety_zip = create_pre_restore_backup(
            server_dir, alt_save_directory_name, safety_root, logger, server_id, server_name
        )

    with zipfile.ZipFile(backup.path) as z:
        names = [n for n in dict.fromkeys(z.namelist()) if not n.endswith("/") and n != BACKUP_MANIFEST_NAME]

        for map_name in maps:
            prefix = backup.map_prefixes[map_name]
            members = [n for n in names if n.startswith(prefix)]
            if not members:
                continue

            ensure_dir(saves_root)
            dest = saves_root / map_name
            incoming = saves_root / f".{map_name}.restoring"
            outgoing = saves_root / f".{map_name}.replaced"
            for leftover in (incoming, outgoing):
                if leftover.exists():
                    shutil.rmtree(leftover, ignore_errors=True)
            ensure_dir(incoming)

            written = 0
            try:
                for n in members:
                    target = _member_target(incoming, n[len(prefix):])
                    ensure_dir(target.parent)
                    with z.open(n) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                    stamp = time.mktime(z.getinfo(n).date_time + (0, 0, -1))
                    os.utime(target, (stamp, stamp))
                    written += 1

                if dest.exists():
                    os.replace(dest, outgoing)
                try:
                    os.replace(incoming, dest)
                except Exception:
                    if outgoing.exists() and not dest.exists():
                        os.replace(outgoing, dest)
                    raise
            except Exception:
                shutil.rmtree(incoming, ignore_errors=True)
                raise

            shutil.rmtree(outgoing, ignore_errors=True)
            res.files_written += written
            res.maps_restored.append(map_name)
            logger.info(f"Restored {written} file(s) of {map_name} from {backup.path.name}")

        for key, member in configs.items():
            data = z.read(member)
            paths = ini_stage_paths(app_base, server_id, server_dir, key)
            for target in (paths.live, paths.stage, paths.stage_base):
                atomic_write_bytes(target, data)
            if paths.baseline.exists():
                atomic_write_bytes(paths.baseline, data)
            write_server_ini_runtime_state_for_stage(paths.live, live_is_runtime=False)
            write_server_ini_runtime_state_for_stage(paths.stage, live_is_runtime=False)
            write_server_ini_runtime_state_for_stage(paths.stage_base, live_is_runtime=False)
            res.config_restored.append(paths.live.name)
            logger.info(f"Restored {paths.live.name} from {backup.path.name}")

    return res


@dataclass
class FoundProcess:
    pid: int
    command_line: str
    started: Optional[datetime] = None


def _exe_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def find_all_server_processes() -> Dict[str, List[FoundProcess]]:
    """Every ArkAscendedServer.exe on the machine, by normalized executable path: one
    query, however many installs are looked at. Empty when the query fails."""
    return query_server_processes() or {}


def process_image_path(pid: int) -> str:
    """Full path of a process's executable, also for one running with more rights; "" if unknown."""
    if os.name != "nt":
        return ""
    try:
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            return buf.value if k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
        finally:
            k.CloseHandle(handle)
    except Exception:
        return ""


def pid_exists(pid: int) -> bool:
    """A process with this ID exists (even one this manager may not open)."""
    if os.name != "nt":
        return False
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k.OpenProcess(0x1000, False, pid)
        if handle:
            k.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5  # access denied: it exists
    except Exception:
        return False


def query_server_processes() -> Optional[Dict[str, List[FoundProcess]]]:
    """Like find_all_server_processes, but None when the query itself failed, so a
    caller can tell "none running" from "could not look"."""
    if os.name != "nt":
        return {}
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='ArkAscendedServer.exe'\" | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss', "
        "[Globalization.CultureInfo]::InvariantCulture))|"
        "$($_.ExecutablePath)|$($_.CommandLine)\" }"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return None
    # A query that failed prints nothing, which must not read as "no server running".
    if res.returncode != 0 or (res.stderr or "").strip() or res.stdout is None:
        return None
    out = res.stdout
    found: Dict[str, List[FoundProcess]] = {}
    for line in (out or "").splitlines():
        parts = line.strip().split("|", 3)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        if not parts[2]:
            # WMI hides the path of a process running with more rights; Windows still tells it.
            parts[2] = process_image_path(int(parts[0]))
            if not parts[2]:
                continue
        started = None
        with contextlib.suppress(ValueError):
            started = datetime.strptime(parts[1], "%Y-%m-%d %H:%M:%S")
        proc = FoundProcess(pid=int(parts[0]), command_line=parts[3] if len(parts) > 3 else "", started=started)
        found.setdefault(_exe_key(parts[2]), []).append(proc)
    return found


def find_server_process_details(server_dir: Path) -> List[FoundProcess]:
    """ArkAscendedServer.exe processes running from this install, with their command lines,
    so each can be told apart by the ports it was started with."""
    return find_all_server_processes().get(_exe_key(str(ark_server_exe(server_dir))), [])


def find_server_processes(server_dir: Path) -> List[int]:
    """PIDs of ArkAscendedServer.exe running from this install, including ones the manager did not start."""
    return [proc.pid for proc in find_server_process_details(server_dir)]


def command_line_world(command_line: str) -> Optional[Tuple[str, str]]:
    """(map, AltSaveDirectoryName) of an ARK server command line, lowercased; None if unreadable."""
    try:
        args = shlex.split(command_line or "", posix=False)
    except ValueError:
        args = (command_line or "").split()
    url = next((a.strip('"') for a in args[1:] if "?" in a), "")
    if not url:
        return None
    parts = url.split("?")
    alt = ""
    for part in parts[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "altsavedirectoryname":
            alt = value.strip()
    return parts[0].strip().lower(), alt.lower()


def command_line_port(command_line: str) -> Optional[int]:
    """The game port in an ARK server command line ('<exe> TheIsland_WP?listen?...?Port=7777?...').

    Only the map URL is read, split at '?', so a server name that contains 'Port=' does
    not count.
    """
    try:
        args = shlex.split(command_line or "", posix=False)
    except ValueError:
        args = (command_line or "").split()
    url = next((a.strip('"') for a in args[1:] if "?" in a), "")
    for part in url.split("?"):
        key, _, value = part.partition("=")
        if key.strip().lower() == "port" and value.strip().isdigit():
            return int(value.strip())
    return None


class AttachedProcess:
    """A server process this manager did not start (it was left running when the manager
    closed), made to look enough like subprocess.Popen for the start/stop code to use it.
    There is no stdout to read; a watcher thread waits for it to exit instead."""

    _SYNCHRONIZE = 0x00100000
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 0x102
    _INFINITE = 0xFFFFFFFF

    def __init__(self, pid: int, expected_exe: Optional[Path] = None) -> None:
        if os.name != "nt":
            raise OSError("Attaching to a process is only supported on Windows.")
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self._k = k
        self.can_terminate = True
        handle = k.OpenProcess(self._SYNCHRONIZE | self._PROCESS_TERMINATE | self._PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # A server running elevated above the manager can still be watched, just not killed.
            handle = k.OpenProcess(self._SYNCHRONIZE | self._PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            self.can_terminate = False
        if not handle:
            raise OSError(f"Cannot open process {pid}.")
        self._handle = handle
        self.pid = pid
        self.stdout = None
        self.returncode: Optional[int] = None
        self.expected_exe = expected_exe
        # The PID may have been reused since it was listed: hold on only to an ARK server.
        if expected_exe is not None:
            image = self.image_path()
            if not image or _exe_key(image) != _exe_key(str(expected_exe)):
                with contextlib.suppress(Exception):
                    k.CloseHandle(handle)
                self._handle = None
                raise OSError(f"Process {pid} is no longer the ARK server ({image or 'unknown image'}).")

    def image_path(self) -> str:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if self._k.QueryFullProcessImageNameW(self._handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""

    def _exit_code(self) -> Optional[int]:
        code = wintypes.DWORD()
        if self._k.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            return int(code.value)
        return None

    def poll(self) -> Optional[int]:
        if self.returncode is None and self._k.WaitForSingleObject(self._handle, 0) == self._WAIT_OBJECT_0:
            self.returncode = self._exit_code()
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        if self.returncode is not None:
            return self.returncode
        ms = self._INFINITE if timeout is None else max(0, int(timeout * 1000))
        result = self._k.WaitForSingleObject(self._handle, ms)
        if result == self._WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired("ArkAscendedServer.exe", timeout or 0)
        if result != self._WAIT_OBJECT_0:
            # WAIT_FAILED and the like: nothing is known about the process, least of all that it ended.
            raise OSError(f"Waiting for process {self.pid} failed (result {result}, error {ctypes.GetLastError()}).")
        return self.poll()

    def terminate(self) -> None:
        if not self.can_terminate:
            raise OSError(f"No right to end process {self.pid}; it runs with more rights than the manager.")
        self._k.TerminateProcess(self._handle, 1)

    kill = terminate

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            if self._handle:
                self._k.CloseHandle(self._handle)

class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def process_usage(pid: int) -> Optional[Tuple[float, int]]:
    """(CPU seconds used so far, working set in bytes) of a process, or None."""
    if os.name != "nt":
        return None
    try:
        k = ctypes.windll.kernel32
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, as GetProcessMemoryInfo documents;
        # without VM_READ (a server running with more rights) the CPU time can still be read.
        handle = k.OpenProcess(0x1000 | 0x0010, False, pid) or k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                return None

            def ticks(ft: wintypes.FILETIME) -> int:
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

            cpu = (ticks(kernel) + ticks(user)) / 10_000_000.0  # 100 ns units
            mem = _PROCESS_MEMORY_COUNTERS()
            mem.cb = ctypes.sizeof(mem)
            working_set = int(mem.WorkingSetSize) if psapi.GetProcessMemoryInfo(handle, ctypes.byref(mem), mem.cb) else 0
            return cpu, working_set
        finally:
            k.CloseHandle(handle)
    except Exception:
        return None


# =============================================================================
# GUI LOGGING
# =============================================================================

class ServerLogTag(logging.Filter):
    """Tags each record of one server's logger: who it came from, and a [name] prefix."""

    def __init__(self, server_id: str, name_fn: Callable[[], str]) -> None:
        super().__init__()
        self.server_id = server_id
        self._name_fn = name_fn

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "server_id", None) is None:
            record.server_id = self.server_id
            try:
                name = self._name_fn() or self.server_id
            except Exception:
                name = self.server_id
            # Formatted here, so a '%' in the name cannot clash with the record's own args.
            try:
                text = record.getMessage()
            except Exception:
                text = str(record.msg)
            record.msg = f"[{name}] {text}"
            record.args = None
        return True


class TkTextHandler(logging.Handler):
    """Log into the console widget without touching Tk from the logging thread.

    emit() runs under the handler lock. Calling Tk there from a worker thread makes the
    worker wait for the main thread while holding that lock, and the main thread then
    deadlocks the moment it logs anything itself. So emit() only queues, and the main
    thread drains the queue on a timer.

    Several servers log into the one console. Recent lines are kept with the server they
    came from, so the console can be narrowed to one server and widened again.
    """

    DRAIN_MS = 100
    HISTORY_LINES = 5000
    # The widget is trimmed back to HISTORY_LINES once it grows past this.
    MAX_WIDGET_LINES = 8000

    def __init__(self, text: tk.Text):
        super().__init__()
        self._text = text
        self._queue: "queue.SimpleQueue[Tuple[Optional[str], str]]" = queue.SimpleQueue()
        self._history: "collections.deque[Tuple[Optional[str], str]]" = collections.deque(maxlen=self.HISTORY_LINES)
        self._only: Optional[str] = None  # show just this server's lines (plus app-wide ones)
        self._drop_predicate: Callable[[str], bool] = lambda _msg: False

    def set_drop_predicate(self, pred: Callable[[str], bool]) -> None:
        self._drop_predicate = pred or (lambda _msg: False)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            raw_msg = record.getMessage()
            if self._drop_predicate(raw_msg):
                return
        except Exception:
            pass

        try:
            self._queue.put((getattr(record, "server_id", None), self.format(record)))
        except Exception:
            pass

    def start_pump(self) -> None:
        """Call once from the main thread."""
        try:
            self._text.after(self.DRAIN_MS, self._drain)
        except Exception:
            pass

    def set_server_filter(self, server_id: Optional[str]) -> None:
        """Main thread only. None shows every server."""
        if server_id == self._only:
            return
        self._only = server_id
        self._render([line for sid, line in self._history if self._shown(sid)], replace=True)

    def _shown(self, server_id: Optional[str]) -> bool:
        return self._only is None or server_id is None or server_id == self._only

    def _render(self, lines: List[str], replace: bool = False) -> None:
        try:
            self._text.configure(state="normal")
            if replace:
                self._text.delete("1.0", "end")
            if lines:
                try:
                    self._text.insert("end", "\n".join(lines) + "\n")
                except tk.TclError:
                    # One line Tk refuses (say a lone surrogate from server output) must not
                    # cost the rest of the batch.
                    for line in lines:
                        with contextlib.suppress(tk.TclError):
                            self._text.insert("end", line + "\n")
            count = int(self._text.index("end-1c").split(".")[0])
            if count > self.MAX_WIDGET_LINES:
                self._text.delete("1.0", f"{count - self.HISTORY_LINES}.0")
            self._text.see("end")
        except Exception:
            pass
        finally:
            # Whatever happened, never leave the console editable.
            with contextlib.suppress(Exception):
                self._text.configure(state="disabled")

    def _drain(self) -> None:
        shown: List[str] = []
        try:
            for _ in range(2000):
                sid, line = self._queue.get_nowait()
                self._history.append((sid, line))
                if self._shown(sid):
                    shown.append(line)
        except queue.Empty:
            pass
        if shown:
            self._render(shown)
        # Re-armed on its own: a failed insert must never stop the pump for good.
        try:
            self._text.after(self.DRAIN_MS, self._drain)
        except Exception:
            pass  # widget destroyed: the app is closing


# Settings a running process is bound to; changing them in the form only applies on the
# next start, so actions on a running server read them from what it was started with.
PROCESS_BOUND_FIELDS = (
    "server_dir", "map_name", "alt_save_directory_name", "port", "query_port",
    "enable_rcon", "rcon_host", "rcon_port", "admin_password",
    "cluster_enable", "cluster_id", "cluster_custom_path_enable", "cluster_dir_override",
)


RUN_STATE_FILE_NAME = "run_state.json"


def run_state_path(app_base: Path, server_id: str) -> Path:
    return server_root(app_base, server_id) / RUN_STATE_FILE_NAME


def write_run_state(app_base: Path, server_id: str, pid: int, cfg: AppConfig) -> None:
    """Remember, on disk, the process this manager launched and what it is bound to, so a
    later manager session can reattach with the right ports and RCON login even after the
    form was edited, and knows the server is its own."""
    state: Dict[str, Any] = {"pid": int(pid), "launched": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    for name in PROCESS_BOUND_FIELDS:
        if name != "admin_password":
            state[name] = getattr(cfg, name)
    password = str(cfg.admin_password or "")
    try:
        state["admin_password_enc"] = dpapi_encrypt(password) if password else ""
    except Exception:
        state["admin_password_enc"] = ""
    path = run_state_path(app_base, server_id)
    ensure_dir(path.parent)
    atomic_write_text(path, json.dumps(state, indent=2), encoding="utf-8")


def read_run_state(app_base: Path, server_id: str) -> Optional[Dict[str, Any]]:
    path = run_state_path(app_base, server_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("pid"), int) else None
    except Exception:
        return None


def clear_run_state(app_base: Path, server_id: str) -> None:
    with contextlib.suppress(OSError):
        run_state_path(app_base, server_id).unlink()


def cfg_from_run_state(cfg: AppConfig, state: Dict[str, Any]) -> AppConfig:
    """The saved profile with what the recorded process was started with laid over it."""
    run = copy.deepcopy(cfg)
    for name in PROCESS_BOUND_FIELDS:
        if name == "admin_password":
            enc = str(state.get("admin_password_enc") or "")
            if enc:
                with contextlib.suppress(Exception):
                    run.admin_password = dpapi_decrypt(enc)
        elif name in state:
            with contextlib.suppress(Exception):
                setattr(run, name, type(getattr(run, name))(state[name]))
    return run


def live_rcon_settings(server_dir: Path) -> Tuple[Optional[int], Optional[str], Optional[bool]]:
    """RCON port, admin password and RCON on/off as the install's live GameUserSettings.ini
    has them: what a running server read when it started."""
    try:
        values = read_ini(server_dir / GAMEUSERSETTINGS_REL).get_last_value_map()
    except Exception:
        return None, None, None
    sec = next((v for k, v in values.items() if k.strip().lower() == "serversettings"), {})
    low = {k.lower(): v for k, v in sec.items()}
    port = low.get("rconport", "")
    enabled = low.get("rconenabled")
    return (
        int(port) if str(port).strip().isdigit() else None,
        low.get("serveradminpassword"),
        None if enabled is None else str(enabled).strip().lower() == "true",
    )


class ServerRuntime:
    """What one server profile has while the app runs: its process and background state.

    The profile selector only picks which server the window shows and edits; every
    server keeps its own runtime, so several can run, save and update at the same time.
    Jobs bind to a runtime when they start and use its cfg, the profile as last saved.
    """

    def __init__(self, server_id: str, cfg: AppConfig, logger: logging.Logger) -> None:
        self.server_id = server_id
        self.cfg = cfg
        self.logger = logger
        self.proc: Optional[subprocess.Popen] = None
        self.proc_lock = threading.Lock()
        # The profile as it was when the running process started; None while stopped.
        self.run_cfg: Optional[AppConfig] = None
        self.stop_log_reader = threading.Event()
        self.stop_requested = threading.Event()
        self.task_lock = threading.Lock()
        self.task: Optional[str] = None  # name of the task running for this server
        # Host, port and password the running process was started with: editing the form
        # while it runs must not break RCON to it.
        self.rcon_snapshot: Optional[Tuple[str, int, str]] = None
        self.discord: Optional[DiscordNotificationCoordinator] = None
        self.started_at: Optional[datetime] = None
        self.auto_save = AutoSaveScheduler()
        self.auto_save_waiting = False
        self.auto_save_last: Optional[Tuple[datetime, bool]] = None
        self.auto_update_next: Optional[datetime] = None
        self.auto_update_waiting = False
        self.restart_next: Optional[datetime] = None
        self.restart_time = ""  # the HH:MM restart_next was computed from
        # A countdown warning players before a planned restart: its label, and its cancel.
        self.countdown: Optional[str] = None
        self.countdown_cancel: Optional[threading.Event] = None
        self.countdown_kind: Optional[str] = None  # "restart", "update" or "group" (a Restart All)
        self.countdown_announced = False  # players were told; a cancel must tell them too
        self.removed = False  # its profile was removed; nothing may start for it any more
        # True when this manager launched the running process (now, or in a session whose
        # run state it found). Only then are the install's INIs its to restore on stop.
        self.launched_by_us = False
        self.players: Optional[int] = None  # last ListPlayers count while running, None if unknown
        # CPU (percent of the whole machine) and memory of the running process, sampled.
        self.cpu_percent: Optional[float] = None
        self.memory_bytes: Optional[int] = None
        self._cpu_sample: Optional[Tuple[float, float, int]] = None  # (wall clock, cpu seconds, pid)
        self.auto_update_time = ""  # the HH:MM auto_update_next was computed from
        self.ended_run_cfg: Optional[AppConfig] = None  # the last run's settings, for its stop message

    def is_running(self) -> bool:
        with self.proc_lock:
            p = self.proc
        return p is not None and p.poll() is None

    def end_run(self) -> None:
        """The process is gone: its settings stay readable until its stop is reported."""
        if self.run_cfg is not None:
            self.ended_run_cfg = self.run_cfg
        self.run_cfg = None

    def live_cfg(self) -> AppConfig:
        """Settings to act on for this server now.

        While it runs, what the process is bound to (its folder, saves, ports, RCON,
        cluster) comes from the settings it started with, since the form may have changed
        since. Everything else, such as backup policy, follows the saved profile as usual.
        """
        run = self.run_cfg
        if run is None or not self.is_running():
            return self.cfg
        merged = copy.copy(self.cfg)
        for name in PROCESS_BOUND_FIELDS:
            setattr(merged, name, getattr(run, name))
        return merged

    def pid(self) -> Optional[int]:
        with self.proc_lock:
            p = self.proc
        return p.pid if p is not None and p.poll() is None else None

    def is_busy(self) -> bool:
        with self.task_lock:
            return self.task is not None

    def try_claim(self, name: str) -> bool:
        with self.task_lock:
            if self.task is not None or self.removed:
                return False
            self.task = name
            return True

    def release(self) -> None:
        with self.task_lock:
            self.task = None

    def close_logger(self) -> None:
        for h in list(self.logger.handlers):
            with contextlib.suppress(Exception):
                self.logger.removeHandler(h)
                h.close()


def make_server_logger(
    base_logger: logging.Logger,
    server_id: str,
    name_fn: Callable[[], str],
    log_dir: Optional[Path],
) -> logging.Logger:
    """Child of the app logger: lines reach the console and app.log tagged with the server,
    and also go to the server's own log file."""
    logger = logging.getLogger(f"{base_logger.name}.server.{server_id}")
    logger.setLevel(LOG_LEVEL)
    for f in list(logger.filters):
        logger.removeFilter(f)
    logger.addFilter(ServerLogTag(server_id, name_fn))
    for h in list(logger.handlers):
        logger.removeHandler(h)
        with contextlib.suppress(Exception):
            h.close()
    if log_dir is not None:
        try:
            ensure_dir(log_dir)
            fh = logging.handlers.RotatingFileHandler(
                log_dir / SERVER_LOG_FILE_NAME, maxBytes=LOG_ROTATE_BYTES, backupCount=LOG_ROTATE_KEEP, encoding="utf-8"
            )
            fh.setLevel(LOG_LEVEL)
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
            logger.addHandler(fh)
        except Exception:
            pass
    logger.propagate = True
    return logger

def build_logger(log_dir: Path, text_widget: tk.Text) -> Tuple[logging.Logger, TkTextHandler]:
    ensure_dir(log_dir)
    logger = logging.getLogger("asa_manager")
    logger.setLevel(LOG_LEVEL)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    h_gui = TkTextHandler(text_widget)
    h_gui.setLevel(LOG_LEVEL)
    h_gui.setFormatter(fmt)
    h_gui.start_pump()
    logger.addHandler(h_gui)

    # Every server's output lands here too, so it rotates instead of growing for good.
    h_file = logging.handlers.RotatingFileHandler(
        log_dir / LOG_FILE_NAME, maxBytes=LOG_ROTATE_BYTES, backupCount=LOG_ROTATE_KEEP, encoding="utf-8"
    )
    h_file.setLevel(LOG_LEVEL)
    h_file.setFormatter(fmt)
    logger.addHandler(h_file)

    return logger, h_gui

# =============================================================================
# APP
# =============================================================================

@dataclass
class SinglePlayerImportRequest:
    sp_root: Path
    save: Optional[SinglePlayerSave] = None
    import_save: bool = True
    include_profiles: bool = True
    include_tribes: bool = True
    backup_first: bool = True
    set_server_map: bool = True
    import_gus: bool = True
    import_game: bool = True
    import_mods: bool = True
    snap_floats: bool = True


def _format_size(num_bytes: int) -> str:
    size = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


class SinglePlayerImportDialog(tk.Toplevel):
    """Pick a single player world and/or its settings and bring them onto the active server."""

    def __init__(self, app: "ServerManagerApp") -> None:
        super().__init__(app.root)
        self.app = app
        self._saves: List[SinglePlayerSave] = []
        self._iid_to_save: Dict[str, SinglePlayerSave] = {}

        detected = detect_singleplayer_root()
        self.var_sp_root = tk.StringVar(master=self, value=str(detected) if detected else "")
        self.var_import_save = tk.BooleanVar(master=self, value=True)
        self.var_include_profiles = tk.BooleanVar(master=self, value=True)
        self.var_include_tribes = tk.BooleanVar(master=self, value=True)
        self.var_backup_first = tk.BooleanVar(master=self, value=True)
        self.var_set_map = tk.BooleanVar(master=self, value=True)
        self.var_import_gus = tk.BooleanVar(master=self, value=True)
        self.var_import_game = tk.BooleanVar(master=self, value=True)
        self.var_snap_floats = tk.BooleanVar(master=self, value=True)
        self.var_import_mods = tk.BooleanVar(master=self, value=True)
        self.var_mods_label = tk.StringVar(master=self, value="Import the enabled mod list")
        self._sp_mods: List[str] = []
        self.var_selection_info = tk.StringVar(master=self, value="No save selected.")
        self.var_resolved_info = tk.StringVar(master=self, value="")
        self.var_tribe_hint = tk.StringVar(master=self, value="")
        self._guide_vars: List[tk.StringVar] = []
        self._rendered_tribe_ids: Optional[List[str]] = None
        self.var_mods_detail = tk.StringVar(master=self, value="")
        self.var_destination = tk.StringVar(master=self, value="")

        self.title("Import Single Player Save")
        self.transient(app.root)
        self.configure(background=THEME_COLORS["bg"])
        apply_initial_window_geometry(self, 1200, 800)
        apply_min_window_size(self, 1040, 640)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())

        self._refresh_saves()
        self._sync_enabled_state()

        self.grab_set()
        self.focus_set()

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        theme = THEME_COLORS
        wrap = 660
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=0, minsize=380)
        root.rowconfigure(1, weight=1)

        lf_source = ttk.LabelFrame(root, text="Single Player Location", padding=10)
        lf_source.grid(row=0, column=0, columnspan=2, sticky="ew")
        lf_source.columnconfigure(1, weight=1)

        ttk.Label(lf_source, text="Saved Folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(lf_source, textvariable=self.var_sp_root).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(lf_source, text="Browse", command=self._browse_root).grid(row=0, column=2)
        ttk.Button(lf_source, text="Rescan", command=self._refresh_saves).grid(row=0, column=3, padx=(6, 0))
        ttk.Label(
            lf_source,
            text=r"Usually %LOCALAPPDATA%\ArkSurvivalAscended\Saved (worlds live in SavedArksLocal). "
                 "The folder is used as given. Stop the game before importing.",
            foreground=theme["muted"],
            wraplength=1080,
            justify="left",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Label(
            lf_source,
            textvariable=self.var_resolved_info,
            foreground=theme["muted"],
            wraplength=1080,
            justify="left",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        lf_saves = ttk.LabelFrame(root, text="Available Worlds", padding=10)
        lf_saves.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        lf_saves.columnconfigure(0, weight=1)
        lf_saves.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            lf_saves,
            columns=("map", "played", "size", "contents"),
            show="headings",
            height=6,
            selectmode="browse",
        )
        self.tree.heading("map", text="Map")
        self.tree.heading("played", text="Last Played")
        self.tree.heading("size", text="Size")
        self.tree.heading("contents", text="Contents")
        self.tree.column("map", width=200, anchor="w")
        self.tree.column("played", width=140, anchor="w")
        self.tree.column("size", width=80, anchor="e")
        self.tree.column("contents", width=200, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        yscroll = ttk.Scrollbar(lf_saves, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=yscroll.set)

        ttk.Label(lf_saves, textvariable=self.var_selection_info, foreground=theme["muted"], wraplength=wrap, justify="left") \
            .grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        lf_world = ttk.LabelFrame(root, text="World Import", padding=10)
        lf_world.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        lf_world.columnconfigure(1, weight=1)

        self.chk_import_save = ttk.Checkbutton(
            lf_world,
            text="Import the selected world into this server",
            variable=self.var_import_save,
            command=self._sync_enabled_state,
        )
        self.chk_import_save.grid(row=0, column=0, columnspan=3, sticky="w")

        self.chk_profiles = ttk.Checkbutton(lf_world, text="Include player profiles", variable=self.var_include_profiles)
        self.chk_profiles.grid(row=1, column=0, sticky="w", padx=(20, 0))
        self.chk_tribes = ttk.Checkbutton(lf_world, text="Include tribes", variable=self.var_include_tribes)
        self.chk_tribes.grid(row=1, column=1, sticky="w")

        self.chk_backup = ttk.Checkbutton(
            lf_world,
            text="Back up the current server save first",
            variable=self.var_backup_first,
        )
        self.chk_backup.grid(row=2, column=0, columnspan=3, sticky="w", padx=(20, 0), pady=(4, 0))

        self.chk_set_map = ttk.Checkbutton(
            lf_world,
            text="Switch this server to the imported map",
            variable=self.var_set_map,
        )
        self.chk_set_map.grid(row=3, column=0, columnspan=3, sticky="w", padx=(20, 0))

        self.lbl_destination = ttk.Label(lf_world, textvariable=self.var_destination, foreground=theme["muted"], wraplength=wrap, justify="left")
        self.lbl_destination.grid(row=4, column=0, columnspan=3, sticky="w", padx=(20, 0), pady=(6, 0))

        lf_settings = ttk.LabelFrame(root, text="Settings and Mods Import", padding=10)
        lf_settings.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        lf_settings.columnconfigure(0, weight=1)

        ttk.Checkbutton(
            lf_settings,
            text="Import single player GameUserSettings.ini (rates, taming, day cycle, ...)",
            variable=self.var_import_gus,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            lf_settings,
            text="Import single player Game.ini (per-level stats, engrams, breeding, ...)",
            variable=self.var_import_game,
        ).grid(row=1, column=0, sticky="w")
        self.chk_import_mods = ttk.Checkbutton(
            lf_settings,
            textvariable=self.var_mods_label,
            variable=self.var_import_mods,
        )
        self.chk_import_mods.grid(row=2, column=0, sticky="w")
        ttk.Label(
            lf_settings,
            textvariable=self.var_mods_detail,
            foreground=theme["muted"],
            wraplength=wrap,
            justify="left",
        ).grid(row=3, column=0, sticky="w", padx=(20, 0), pady=(2, 0))

        ttk.Checkbutton(
            lf_settings,
            text="Round off slider noise (0.999989986 becomes 1, 0.439990014 becomes 0.44)",
            variable=self.var_snap_floats,
        ).grid(row=4, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            lf_settings,
            text="ARK writes floats like 0.00999999978 for 0.01; those are always rewritten to the short "
                 "form, which is the same value. Its settings sliders also emit 0.999989986 where you "
                 "picked 1 - rounding those off changes the value very slightly, so untick the box to "
                 "import them exactly as the game wrote them.",
            foreground=theme["muted"],
            wraplength=wrap,
            justify="left",
        ).grid(row=5, column=0, sticky="w", padx=(20, 0), pady=(2, 0))
        ttk.Label(
            lf_settings,
            text="Merged into this server INI staging, so the INI Editor shows them right away and they "
                 "apply on the next start. Server name, passwords, RCON, ports and max players are kept. "
                 "Note that single player applies extra hidden multipliers the server does not, so some "
                 "rates will still feel different.",
            foreground=theme["muted"],
            wraplength=wrap,
            justify="left",
        ).grid(row=6, column=0, sticky="w", pady=(6, 0))

        self._build_guide(root)

        buttons = ttk.Frame(root)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        self.btn_import = ttk.Button(buttons, text="Import", command=self._on_import)
        self.btn_import.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=1)

    def _build_guide(self, parent: ttk.Frame) -> None:
        """Step by step for bringing the survivor across, which the world copy cannot do."""
        theme = THEME_COLORS
        wrap = 340
        lf = ttk.LabelFrame(parent, text="Moving Your Character", padding=10)
        lf.grid(row=1, column=1, rowspan=3, sticky="nsew", padx=(10, 0), pady=(10, 0))
        lf.columnconfigure(0, weight=1)

        def para(row: int, text: str, muted: bool = False, top: int = 8) -> int:
            ttk.Label(
                lf,
                text=text,
                foreground=(theme["muted"] if muted else theme["text"]),
                wraplength=wrap,
                justify="left",
            ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(top, 0))
            return row + 1

        row = para(
            0,
            "Your survivor does not come across with the world: the server rebuilds player data "
            "for its own accounts on its first save. Bring it over through an obelisk instead.",
            muted=True,
            top=0,
        )
        row = para(
            row,
            "1. Before importing, load this world in single player and upload your survivor at an "
            "obelisk or supply terminal. Put anything you want to keep in your base first - the base "
            "comes across with the world.",
        )
        row = para(row, "2. Exit the game, import the world here, then start the server and join it.")
        row = para(row, "3. Download that survivor at an obelisk or terminal on the server.")
        row = para(
            row,
            "4. Your base and tames still belong to the single player tribe. Open the console, "
            "enable admin, then take them over:",
        )
        row = self._guide_command(lf, row, "enablecheats <admin password>")

        self.guide_tribe_frame = ttk.Frame(lf)
        self.guide_tribe_frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        self.guide_tribe_frame.columnconfigure(0, weight=1)
        row += 1
        ttk.Label(
            lf,
            textvariable=self.var_tribe_hint,
            foreground=theme["muted"],
            wraplength=wrap,
            justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
        row += 1

        row = para(row, "Or look at one of your structures or tames and run:", muted=True)
        row = self._guide_command(lf, row, "cheat TakeAllStructure")
        row = self._guide_command(lf, row, "cheat TakeAllDino")
        para(
            row,
            "Each takes everything of the tribe you are looking at, not only that one structure or tame.",
            muted=True,
            top=4,
        )

        self._render_tribe_commands([])

    def _guide_command(self, parent: tk.Misc, row: int, command: str) -> int:
        var = tk.StringVar(master=self, value=command)
        self._guide_vars.append(var)
        ttk.Entry(parent, textvariable=var, state="readonly", font=("Consolas", 10)) \
            .grid(row=row, column=0, sticky="ew", pady=(4, 0))
        btn = ttk.Button(parent, text="Copy", width=7)
        btn.configure(command=lambda: self._copy_command(var.get(), btn))
        btn.grid(row=row, column=1, sticky="e", padx=(6, 0), pady=(4, 0))
        return row + 1

    def _copy_command(self, command: str, button: ttk.Button) -> None:
        self.clipboard_clear()
        self.clipboard_append(command)
        button.configure(text="Copied")

        def reset() -> None:
            try:
                button.configure(text="Copy")
            except tk.TclError:
                pass

        self.after(1200, reset)

    def _render_tribe_commands(self, tribe_ids: List[str]) -> None:
        # Selecting a row fires <<TreeviewSelect>> on top of the direct call; rebuilding
        # identical rows would drop a pending "Copied" confirmation for nothing.
        if tribe_ids == self._rendered_tribe_ids and self.guide_tribe_frame.winfo_children():
            return
        self._rendered_tribe_ids = list(tribe_ids)
        for child in self.guide_tribe_frame.winfo_children():
            child.destroy()
        shown = tribe_ids[:3]
        commands = [f"cheat TakeTribe {tid}" for tid in shown] or ["cheat TakeTribe <tribe id>"]
        row = 0
        for command in commands:
            row = self._guide_command(self.guide_tribe_frame, row, command)

        if tribe_ids:
            hint = "Takes every structure and tame of the tribe in one go - no need to find them. The id comes from this save's tribe file."
            if len(tribe_ids) > len(shown):
                hint += f" This save has {len(tribe_ids) - len(shown)} more tribe(s)."
        else:
            hint = "This save has no tribe file, so use the look-at commands below instead."
        self.var_tribe_hint.set(hint)

    # -- data --------------------------------------------------------------
    def _browse_root(self) -> None:
        initial = self.var_sp_root.get().strip()
        chosen = filedialog.askdirectory(
            parent=self,
            title="Select the ARK: Survival Ascended 'Saved' folder",
            initialdir=initial or None,
        )
        if not chosen:
            return
        self.var_sp_root.set(str(Path(chosen)))
        self._refresh_saves()

    def _current_root(self) -> Optional[Path]:
        raw = self.var_sp_root.get().strip()
        return Path(raw) if raw else None

    def _refresh_saves(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._iid_to_save.clear()
        self._saves = []

        root = self._current_root()
        if root is None:
            self.var_resolved_info.set("")
            self.var_selection_info.set("Choose the single player 'Saved' folder to scan.")
            self._update_destination()
            return

        try:
            self._saves = discover_singleplayer_saves(root)
        except Exception as e:
            self.var_resolved_info.set("")
            self.var_selection_info.set(f"Could not scan saves: {e}")
            self._update_destination()
            return

        self._update_resolved_info(root)

        for save in self._saves:
            contents = []
            if save.profiles:
                contents.append(f"{len(save.profiles)} profile(s)")
            if save.tribes:
                contents.append(f"{len(save.tribes)} tribe(s)")
            iid = self.tree.insert(
                "",
                "end",
                values=(
                    save.map_name,
                    datetime.fromtimestamp(save.modified).strftime("%Y-%m-%d %H:%M"),
                    _format_size(save.size_bytes),
                    ", ".join(contents) or "world only",
                ),
            )
            self._iid_to_save[iid] = save

        if not self._saves:
            self.var_selection_info.set(f"No .ark world files found in or under {root}")
        else:
            first = self.tree.get_children()
            if first:
                self.tree.selection_set(first[0])
                self.tree.focus(first[0])
        self._on_select()

    def _update_resolved_info(self, root: Path) -> None:
        saves_root = resolve_singleplayer_saves_root(root)
        cfg_dir = resolve_singleplayer_config_dir(root)
        parts = [f"Worlds: {saves_root}" if saves_root else "Worlds: none found"]
        parts.append(f"Settings: {cfg_dir}" if cfg_dir else "Settings: no INI found")
        self.var_resolved_info.set("   |   ".join(parts))
        self._update_mods_info(root)

    def _update_mods_info(self, root: Path) -> None:
        try:
            self._sp_mods = read_singleplayer_active_mods(root)
        except Exception:
            self._sp_mods = []

        count = len(self._sp_mods)
        if count:
            self.var_mods_label.set(f"Import the {count} mod(s) enabled in single player")
            shown = ", ".join(self._sp_mods[:8])
            if count > 8:
                shown += f" (+{count - 8} more)"
            self.var_mods_detail.set(
                f"{shown}\nReplaces this server's Mods field, which is what -mods= is built from."
            )
        else:
            self.var_mods_label.set("Import the enabled mod list (none found)")
            self.var_mods_detail.set("")
        try:
            self.chk_import_mods.configure(state=("normal" if count else "disabled"))
        except Exception:
            pass

    def _selected_save(self) -> Optional[SinglePlayerSave]:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._iid_to_save.get(sel[0])

    def _on_select(self) -> None:
        save = self._selected_save()
        if save is None:
            if self._saves:
                self.var_selection_info.set("No save selected.")
            self._render_tribe_commands([])
            self._update_destination()
            return

        tribe_ids = singleplayer_tribe_ids(save)
        detail = f"Source: {save.folder}"
        if tribe_ids:
            detail += f"  |  Tribe ID: {', '.join(tribe_ids[:3])}"
        self.var_selection_info.set(detail)
        self._render_tribe_commands(tribe_ids)
        self._update_destination()
        self._sync_enabled_state()

    def _update_destination(self) -> None:
        save = self._selected_save()
        if save is None or not self.var_import_save.get():
            self.var_destination.set("")
            return
        cfg = self.app.cfg
        dest = server_saves_root(Path(cfg.server_dir), cfg.alt_save_directory_name) / save.map_name
        self.var_destination.set(f"Destination: {dest}")

    def _sync_enabled_state(self) -> None:
        importing = bool(self.var_import_save.get())
        state = "normal" if importing else "disabled"
        for widget in (self.chk_profiles, self.chk_tribes, self.chk_backup, self.chk_set_map):
            widget.configure(state=state)
        self._update_destination()

    # -- submit ------------------------------------------------------------
    def _on_import(self) -> None:
        import_save = bool(self.var_import_save.get())
        import_gus = bool(self.var_import_gus.get())
        import_game = bool(self.var_import_game.get())
        import_mods = bool(self.var_import_mods.get()) and bool(self._sp_mods)

        if not (import_save or import_gus or import_game or import_mods):
            messagebox.showwarning("Import Single Player", "Nothing selected to import.", parent=self)
            return

        root = self._current_root()
        if root is None or not root.is_dir():
            messagebox.showerror("Import Single Player", "Select a valid single player 'Saved' folder.", parent=self)
            return

        save = self._selected_save()
        if import_save and save is None:
            messagebox.showwarning("Import Single Player", "Select a world to import.", parent=self)
            return

        summary = []
        if import_save and save is not None:
            dest = server_saves_root(Path(self.app.cfg.server_dir), self.app.cfg.alt_save_directory_name) / save.map_name
            summary.append(f"World '{save.map_name}' -> {dest}")
            if dest.is_dir() and any(dest.iterdir()):
                summary.append("An existing server save for this map will be overwritten.")
        if import_gus:
            summary.append("Single player GameUserSettings.ini merged into staging.")
        if import_game:
            summary.append("Single player Game.ini merged into staging.")
        if import_mods:
            summary.append(f"Mods field replaced with {len(self._sp_mods)} mod id(s).")

        if not messagebox.askyesno("Import Single Player", "\n".join(summary) + "\n\nContinue?", parent=self):
            return

        req = SinglePlayerImportRequest(
            sp_root=root,
            save=save if import_save else None,
            import_save=import_save,
            include_profiles=bool(self.var_include_profiles.get()),
            include_tribes=bool(self.var_include_tribes.get()),
            backup_first=bool(self.var_backup_first.get()),
            set_server_map=bool(self.var_set_map.get()),
            import_gus=import_gus,
            import_game=import_game,
            import_mods=import_mods,
            snap_floats=bool(self.var_snap_floats.get()),
        )

        self.destroy()
        self.app.start_singleplayer_import(req)


@dataclass
class BackupRestoreRequest:
    backup: ServerBackup
    restore_world: bool = True
    restore_config: bool = False
    safety_backup: bool = True


class BackupManagerDialog(tk.Toplevel):
    """List this server's backup zips, make new ones, and put one back."""

    KIND_LABELS = {
        BACKUP_KIND_FULL: "Full backup",
        BACKUP_KIND_PRE_IMPORT: "Before import",
        BACKUP_KIND_PRE_RESTORE: "Before restore",
        BACKUP_KIND_AUTO_SAVE: "Auto-save",
    }

    def __init__(self, app: "ServerManagerApp") -> None:
        super().__init__(app.root)
        self.app = app
        self.backup_root = resolve_backup_root(app.cfg, app.app_base)
        self._backups: List[ServerBackup] = []
        self._picked: List[ServerBackup] = []
        self._iid_to_backup: Dict[str, ServerBackup] = {}

        self.var_folder = tk.StringVar(master=self, value=str(self.backup_root))
        self.var_only_mine = tk.BooleanVar(master=self, value=True)
        self.var_show_auto_saves = tk.BooleanVar(master=self, value=True)
        self.var_hidden = tk.StringVar(master=self, value="")
        self.var_detail = tk.StringVar(master=self, value="")
        self.var_warning = tk.StringVar(master=self, value="")
        self.var_restore_world = tk.BooleanVar(master=self, value=True)
        self.var_restore_config = tk.BooleanVar(master=self, value=False)
        self.var_safety = tk.BooleanVar(master=self, value=True)

        self.title(f"Backups - {app.cfg.server_name}")
        self.transient(app.root)
        self.configure(background=THEME_COLORS["bg"])
        apply_initial_window_geometry(self, 1000, 680)
        apply_min_window_size(self, 840, 560)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())

        self._refresh()

        self.grab_set()
        self.focus_set()

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        theme = THEME_COLORS
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        lf_folder = ttk.LabelFrame(root, text="Backup Folder", padding=10)
        lf_folder.grid(row=0, column=0, sticky="ew")
        lf_folder.columnconfigure(0, weight=1)
        ttk.Label(lf_folder, textvariable=self.var_folder, wraplength=760, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Button(lf_folder, text="Open Folder", command=self._open_folder).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(lf_folder, text="Refresh", command=self._refresh).grid(row=0, column=2, padx=(6, 0))
        filters = ttk.Frame(lf_folder)
        filters.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            filters,
            text="Only show this server's backups",
            variable=self.var_only_mine,
            command=self._populate,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            filters,
            text="Show auto-saves",
            variable=self.var_show_auto_saves,
            command=self._populate,
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))
        ttk.Label(lf_folder, textvariable=self.var_hidden, foreground=theme["muted"], wraplength=900, justify="left") \
            .grid(row=2, column=0, columnspan=3, sticky="w")

        lf_list = ttk.LabelFrame(root, text="Backups", padding=10)
        lf_list.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        lf_list.columnconfigure(0, weight=1)
        lf_list.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            lf_list,
            columns=("created", "kind", "server", "contents", "size"),
            show="headings",
            height=10,
            selectmode="browse",
        )
        for col, label, width, anchor in (
            ("created", "Created", 140, "w"),
            ("kind", "Type", 110, "w"),
            ("server", "Server", 150, "w"),
            ("contents", "Contents", 320, "w"),
            ("size", "Size", 90, "e"),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor=anchor)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        yscroll = ttk.Scrollbar(lf_list, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=yscroll.set)

        ttk.Label(lf_list, textvariable=self.var_detail, foreground=theme["muted"], wraplength=900, justify="left") \
            .grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        lf_restore = ttk.LabelFrame(root, text="Restore", padding=10)
        lf_restore.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        lf_restore.columnconfigure(0, weight=1)

        self.chk_world = ttk.Checkbutton(
            lf_restore,
            text="World save - replaces the map folder on this server",
            variable=self.var_restore_world,
            command=self._sync_state,
        )
        self.chk_world.grid(row=0, column=0, sticky="w")
        self.chk_config = ttk.Checkbutton(
            lf_restore,
            text="Server settings - GameUserSettings.ini and Game.ini, replacing anything staged in the INI Editor",
            variable=self.var_restore_config,
            command=self._sync_state,
        )
        self.chk_config.grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(
            lf_restore,
            text="Zip the current save and settings first (recommended)",
            variable=self.var_safety,
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            lf_restore,
            text="The server must be stopped. Server name, passwords, RCON and max players come from this "
                 "profile and are applied again on the next start, whatever the backup holds.",
            foreground=theme["muted"],
            wraplength=900,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Label(lf_restore, textvariable=self.var_warning, foreground=theme["warning"], wraplength=900, justify="left") \
            .grid(row=4, column=0, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(root)
        buttons.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        buttons.columnconfigure(3, weight=1)
        self.btn_backup = ttk.Button(buttons, text="Backup Now", command=self._backup_now)
        self.btn_backup.grid(row=0, column=0)
        ttk.Button(buttons, text="Restore From Zip...", command=self._pick_zip).grid(row=0, column=1, padx=(8, 0))
        self.btn_delete = ttk.Button(buttons, text="Delete", command=self._delete)
        self.btn_delete.grid(row=0, column=2, padx=(8, 0))
        self.btn_restore = ttk.Button(buttons, text="Restore", command=self._on_restore)
        self.btn_restore.grid(row=0, column=4, padx=(0, 8))
        ttk.Button(buttons, text="Close", command=self.destroy).grid(row=0, column=5)

    # -- data --------------------------------------------------------------
    def _owner(self, backup: ServerBackup) -> Optional[bool]:
        return backup_owner_match(backup, self.app.active_server_id, self.app.cfg.server_name)

    def _is_picked(self, backup: ServerBackup) -> bool:
        return any(p.path == backup.path for p in self._picked)

    def _refresh(self) -> None:
        try:
            self._backups = list_server_backups(self.backup_root)
        except Exception as e:
            self._backups = []
            self.var_detail.set(f"Could not read the backup folder: {e}")
        self._populate()

    def _row_values(self, backup: ServerBackup) -> Tuple[str, str, str, str, str]:
        created = datetime.fromtimestamp(backup.created).strftime("%Y-%m-%d %H:%M") if backup.created else ""
        kind = self.KIND_LABELS.get(backup.kind, "Other zip")
        if self._is_picked(backup):
            kind += " (picked)"
        if backup.error:
            contents = backup.error
        else:
            parts = []
            if backup.maps:
                parts.append(", ".join(backup.maps))
            if backup.config_members:
                parts.append("settings")
            contents = " + ".join(parts)
        return created, kind, backup.server_name or "unknown", contents, _format_size(backup.size_bytes)

    def _populate(self, select_path: Optional[Path] = None) -> None:
        current = self._selected()
        want = select_path or (current.path if current is not None else None)

        self.tree.delete(*self.tree.get_children())
        self._iid_to_backup.clear()

        only_mine = bool(self.var_only_mine.get())
        show_auto = bool(self.var_show_auto_saves.get())
        hidden = 0
        chosen_iid = ""
        for backup in self._picked + self._backups:
            if only_mine and not self._is_picked(backup) and self._owner(backup) is False:
                hidden += 1
                continue
            if not show_auto and not self._is_picked(backup) and backup.kind == BACKUP_KIND_AUTO_SAVE:
                continue
            iid = self.tree.insert("", "end", values=self._row_values(backup))
            self._iid_to_backup[iid] = backup
            if want is not None and backup.path == want and not chosen_iid:
                chosen_iid = iid

        if not self._backups and not self._picked:
            self.var_hidden.set("No backups in this folder yet.")
        elif hidden:
            self.var_hidden.set(f"{hidden} backup(s) from other servers sharing this folder are hidden.")
        else:
            self.var_hidden.set("")

        children = self.tree.get_children()
        target = chosen_iid or (children[0] if children else "")
        if target:
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)
        self._on_select()

    def _selected(self) -> Optional[ServerBackup]:
        try:
            sel = self.tree.selection()
        except tk.TclError:
            return None
        return self._iid_to_backup.get(sel[0]) if sel else None

    def _on_select(self) -> None:
        backup = self._selected()
        if backup is None:
            self.var_detail.set("Select a backup." if self.tree.get_children() else "")
            self.var_warning.set("")
            self._sync_state()
            return

        lines = [str(backup.path)]
        if backup.error:
            lines.append(backup.error)
        else:
            settings = "included" if backup.config_members else "not included"
            lines.append(
                f"{backup.file_count} file(s). World: {', '.join(backup.maps) or 'none'}. Settings: {settings}."
            )
        self.var_detail.set("\n".join(lines))

        owner = self._owner(backup)
        if owner is False:
            self.var_warning.set(
                f"This backup belongs to '{backup.server_name or 'another server'}', not to {self.app.cfg.server_name}."
            )
        elif owner is None and backup.restorable:
            self.var_warning.set("This backup does not record which server it came from - check the date before restoring.")
        else:
            self.var_warning.set("")
        self._sync_state()

    def _sync_state(self) -> None:
        backup = self._selected()
        busy = self.app._is_busy()
        has_world = bool(backup is not None and backup.restorable and backup.maps)
        has_config = bool(backup is not None and backup.restorable and backup.config_members)
        self.chk_world.configure(state=("normal" if has_world else "disabled"))
        self.chk_config.configure(state=("normal" if has_config else "disabled"))
        wanted = (has_world and bool(self.var_restore_world.get())) or (has_config and bool(self.var_restore_config.get()))
        self.btn_restore.configure(state=("normal" if wanted and not busy else "disabled"))
        can_delete = backup is not None and not self._is_picked(backup) and not busy
        self.btn_delete.configure(state=("normal" if can_delete else "disabled"))
        self.btn_backup.configure(state=("disabled" if busy else "normal"))

    # -- actions -----------------------------------------------------------
    def _open_folder(self) -> None:
        open_folder(self.backup_root)

    def _backup_now(self) -> None:
        if self.app._is_busy():
            messagebox.showinfo("Backups", "Another task is running. Try again when it finishes.", parent=self)
            return
        for button in (self.btn_backup, self.btn_restore, self.btn_delete):
            button.configure(state="disabled")
        self.var_detail.set("Creating backup...")
        self.app.backup_now(on_done=self._after_backup)

    def _after_backup(self, path: Optional[Path]) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            self._backups = list_server_backups(self.backup_root)
        except Exception:
            pass
        self._populate(select_path=path)

    def _pick_zip(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self,
            title="Select a backup zip to restore",
            filetypes=[("Zip archives", "*.zip"), ("All files", "*.*")],
            initialdir=str(self.backup_root) if self.backup_root.is_dir() else None,
        )
        if not chosen:
            return
        backup = read_server_backup(Path(chosen))
        if not backup.restorable:
            messagebox.showerror("Backups", f"{Path(chosen).name}: {backup.error}", parent=self)
            return
        in_folder = any(b.path.resolve() == backup.path.resolve() for b in self._backups)
        if not in_folder:
            self._picked = [backup] + [p for p in self._picked if p.path != backup.path]
        self._populate(select_path=backup.path)

    def _delete(self) -> None:
        backup = self._selected()
        if backup is None or self._is_picked(backup):
            return
        if not messagebox.askyesno(
            "Delete Backup",
            f"Delete {backup.path.name}?\n\nThis cannot be undone.",
            icon="warning",
            parent=self,
        ):
            return
        try:
            backup.path.unlink()
        except Exception as e:
            messagebox.showerror("Delete Backup", f"Could not delete {backup.path.name}: {e}", parent=self)
            return
        self.app.logger.info(f"Deleted backup {backup.path.name}")
        self._refresh()

    def _on_restore(self) -> None:
        backup = self._selected()
        if backup is None or not backup.restorable:
            return
        restore_world = bool(self.var_restore_world.get()) and bool(backup.maps)
        restore_config = bool(self.var_restore_config.get()) and bool(backup.config_members)
        if not (restore_world or restore_config):
            messagebox.showwarning("Restore Backup", "Nothing selected to restore.", parent=self)
            return
        if self.app._is_server_running():
            messagebox.showwarning("Restore Backup", "Stop the server before restoring a backup.", parent=self)
            return
        # The task runner drops requests while busy; say so instead of closing on nothing.
        if self.app._is_busy():
            messagebox.showinfo("Restore Backup", "Another task is running. Try again when it finishes.", parent=self)
            return

        cfg = self.app.cfg
        saves_root = server_saves_root(Path(cfg.server_dir), cfg.alt_save_directory_name)
        created = datetime.fromtimestamp(backup.created).strftime("%Y-%m-%d %H:%M") if backup.created else "unknown date"
        safety = bool(self.var_safety.get())
        owner = self._owner(backup)

        lines = [f"Restore {backup.path.name} ({created})?"]
        if restore_world:
            for map_name in backup.maps:
                lines.append(f"World '{map_name}' replaces:\n{saves_root / map_name}")
        if restore_config:
            lines.append(
                "GameUserSettings.ini and Game.ini replace this server's settings, "
                "including anything staged in the INI Editor."
            )
        if safety:
            lines.append("The current save and settings are zipped first.")
        else:
            lines.append("No safety backup will be made: the current save is lost.")
        if owner is False:
            lines.append(f"WARNING: this backup belongs to '{backup.server_name}', not to {cfg.server_name}.")

        risky = owner is False or not safety
        if not messagebox.askyesno(
            "Restore Backup",
            "\n\n".join(lines),
            icon=("warning" if risky else "question"),
            parent=self,
        ):
            return

        req = BackupRestoreRequest(
            backup=backup,
            restore_world=restore_world,
            restore_config=restore_config,
            safety_backup=safety,
        )
        self.destroy()
        self.app.start_backup_restore(req)


@dataclass
class ClusterSetupRequest:
    cluster_id: str
    cluster_dir: Path
    joining: bool = False
    copy_from: Optional[Path] = None  # the old cluster ID's folder
    copy_to: Optional[Path] = None  # the new cluster ID's folder
    backup_first: bool = True


@dataclass
class ClusterSetupResult:
    backup_zip: Optional[Path] = None
    copied: int = 0
    skipped: int = 0


class TaskCancelled(Exception):
    """The user declined a confirmation inside a task; logged, not shown as an error."""


class ClusterSetupDialog(tk.Toplevel):
    """Bring the active server into a cluster: start a new one with it, or join one another profile runs.

    Only the profile's start options change. The world save stays where it is, and a running
    server keeps running untouched until it is restarted.
    """

    def __init__(self, app: "ServerManagerApp") -> None:
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.server_dir = Path(self.cfg.server_dir)
        self.running = app._is_server_running()
        self.clusters = discover_clusters(app.app_base, app.global_cfg.servers, exclude_id=app.active_server_id)
        self.current_dir = effective_cluster_dir(self.cfg)
        self.current_id = self.cfg.cluster_id.strip() if self.current_dir is not None else ""
        self._used_ports = app._collect_used_ports(ignore_server_id=app.active_server_id)
        # Other maps still reading the folder this server's data sits in (its override, or the
        # install's Saved\clusters a sibling profile may run from): copying it would duplicate
        # their players' uploads.
        source_base = self.current_dir if self.current_dir is not None else server_cluster_data_dir(self.server_dir)
        self._current_shared_with: List[str] = [
            name
            for c in self.clusters
            for name, folder in zip(c.member_names, c.member_folders)
            if same_path(folder, source_base)
        ]

        joined = next((c for c in self.clusters if c.cluster_id == self.current_id), None)
        start_id = self.current_id or make_cluster_id(self.cfg.server_name)
        if self.current_dir is not None and self.cfg.cluster_custom_path_enable:
            start_dir = self.current_dir
        else:
            start_dir = default_cluster_dir(app.app_base, start_id, self.server_dir)

        self.var_mode = tk.StringVar(master=self, value="join" if joined else "new")
        self.var_cluster_id = tk.StringVar(master=self, value=start_id)
        self.var_cluster_dir = tk.StringVar(master=self, value=str(start_dir))
        self.var_join_pick = tk.StringVar(master=self, value=joined.label() if joined else "")
        self.var_copy = tk.BooleanVar(master=self, value=True)
        self.var_copy_label = tk.StringVar(master=self, value="")
        self.var_backup = tk.BooleanVar(master=self, value=True)
        self.var_warning = tk.StringVar(master=self, value="")
        self.var_join_detail = tk.StringVar(master=self, value="")
        # The folder follows the typed ID until the user picks one themselves.
        self._dir_auto = not (self.current_dir is not None and self.cfg.cluster_custom_path_enable)

        title = f"Cluster - {self.cfg.server_name}"
        self.title(title)
        self.transient(app.root)
        self.configure(background=THEME_COLORS["bg"])
        apply_initial_window_geometry(self, 820, 640)
        apply_min_window_size(self, 720, 560)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())

        self.var_cluster_id.trace_add("write", lambda *_: self._on_id_changed())
        self.var_cluster_dir.trace_add("write", lambda *_: self._refresh())
        if joined:
            self._on_join_picked()
        self._sync_mode()

        self.grab_set()
        self.focus_set()

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        theme = THEME_COLORS
        wrap = 740
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)

        state = "running" if self.running else "stopped"
        where = f"in cluster '{self.current_id}'" if self.current_id else "standalone (not in a cluster)"
        ttk.Label(
            root,
            text=f"{self.cfg.server_name}  -  {self.cfg.map_name}  -  {state}, {where}",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(root, text=f"Install: {self.server_dir}", foreground=theme["muted"]).grid(row=1, column=0, sticky="w")

        lf = ttk.LabelFrame(root, text="Cluster", padding=10)
        lf.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        lf.columnconfigure(1, weight=1)

        ttk.Radiobutton(
            lf, text="Start a new cluster with this server", variable=self.var_mode, value="new",
            command=self._sync_mode,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        self.rb_join = ttk.Radiobutton(
            lf, text="Join a cluster another profile already runs", variable=self.var_mode, value="join",
            command=self._sync_mode,
        )
        self.rb_join.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        if not self.clusters:
            self.rb_join.configure(state="disabled", text="Join a cluster another profile already runs (none found)")

        self.cmb_join = ttk.Combobox(
            lf, textvariable=self.var_join_pick, state="readonly",
            values=[c.label() for c in self.clusters],
        )
        self.cmb_join.grid(row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=(4, 0))
        self.cmb_join.bind("<<ComboboxSelected>>", lambda _e: self._on_join_picked())
        ttk.Label(lf, textvariable=self.var_join_detail, foreground=theme["muted"], wraplength=wrap - 120, justify="left") \
            .grid(row=3, column=1, columnspan=2, sticky="w", padx=6)

        ttk.Label(lf, text="Cluster ID").grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.ent_id = ttk.Entry(lf, textvariable=self.var_cluster_id)
        self.ent_id.grid(row=4, column=1, columnspan=2, sticky="ew", padx=6, pady=(10, 0))

        ttk.Label(lf, text="Cluster data folder").grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.ent_dir = ttk.Entry(lf, textvariable=self.var_cluster_dir)
        self.ent_dir.grid(row=5, column=1, sticky="ew", padx=6, pady=(6, 0))
        self.ent_dir.bind("<KeyRelease>", lambda _e: self._mark_dir_manual())
        self.btn_browse = ttk.Button(lf, text="Browse", command=self._browse_dir)
        self.btn_browse.grid(row=5, column=2, pady=(6, 0))

        ttk.Label(
            lf,
            text="Every server in a cluster must use the same ID and the same data folder; the folder holds "
                 "uploaded survivors, dinos and items. Letters, numbers, '-' and '_' only. To join a cluster "
                 "that is not listed (for example one run by another tool), choose 'Start a new cluster' and "
                 "enter its ID and folder.",
            foreground=theme["muted"], wraplength=wrap, justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))

        lf_data = ttk.LabelFrame(root, text="Existing Data", padding=10)
        lf_data.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        lf_data.columnconfigure(0, weight=1)
        self.chk_copy = ttk.Checkbutton(lf_data, textvariable=self.var_copy_label, variable=self.var_copy)
        self.chk_copy.grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            lf_data,
            text="Back up this server first (world, settings and any current cluster data)",
            variable=self.var_backup,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        lf_info = ttk.LabelFrame(root, text="What Happens", padding=10)
        lf_info.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        lf_info.columnconfigure(0, weight=1)
        when = (
            "The server is running now and keeps running unchanged; it joins the cluster when it restarts, "
            "and you will be offered a safe restart right after."
            if self.running else
            "The change takes effect the next time the server starts."
        )
        ttk.Label(
            lf_info,
            text="Your world save, survivors, tribes and INI settings stay exactly where they are; only this "
                 f"profile's cluster start options change. {when}\n\n"
                 "To be safe, ask players to download anything they keep in an obelisk, transmitter or "
                 "terminal before that restart, and upload it again afterwards.",
            wraplength=wrap, justify="left",
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(root, textvariable=self.var_warning, foreground=theme["warning"], wraplength=wrap, justify="left") \
            .grid(row=5, column=0, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(root)
        buttons.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        buttons.columnconfigure(0, weight=1)
        if self.current_id:
            ttk.Button(buttons, text="Leave Cluster", command=self._on_leave).grid(row=0, column=0, sticky="w")
        self.btn_apply = ttk.Button(buttons, text="Create Cluster", command=self._on_apply)
        self.btn_apply.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=2)

    # -- state -------------------------------------------------------------
    def _picked_cluster(self) -> Optional[ClusterInfo]:
        idx = self.cmb_join.current()
        if 0 <= idx < len(self.clusters):
            return self.clusters[idx]
        return None

    def _sync_mode(self) -> None:
        joining = self.var_mode.get() == "join"
        self.cmb_join.configure(state="readonly" if joining else "disabled")
        entry_state = "readonly" if joining else "normal"
        self.ent_id.configure(state=entry_state)
        self.ent_dir.configure(state=entry_state)
        self.btn_browse.configure(state="disabled" if joining else "normal")
        if joining:
            if self._picked_cluster() is None and self.clusters:
                self.cmb_join.current(0)
            self._on_join_picked()
        else:
            self.var_join_detail.set("")
        self._refresh()

    def _on_join_picked(self) -> None:
        info = self._picked_cluster()
        if info is None:
            return
        self.var_cluster_id.set(info.cluster_id)
        if info.cluster_dir is not None:
            self.var_cluster_dir.set(str(info.cluster_dir))
        self.var_join_detail.set("Servers: " + "; ".join(info.member_names))
        self._refresh()

    def _on_id_changed(self) -> None:
        if self.var_mode.get() == "new" and self._dir_auto:
            cid = self.var_cluster_id.get().strip()
            if cid:
                self.var_cluster_dir.set(str(default_cluster_dir(self.app.app_base, cid, self.server_dir)))
        self._refresh()

    def _mark_dir_manual(self) -> None:
        self._dir_auto = False

    def _browse_dir(self) -> None:
        p = filedialog.askdirectory(title="Select the cluster data folder", parent=self, mustexist=False)
        if p:
            self._dir_auto = False
            self.var_cluster_dir.set(str(Path(p)))

    def _old_cluster_id(self, base: Path) -> str:
        """The ID this server's existing transfer data is filed under, or "" if unknown."""
        if self.current_id:
            return self.current_id
        # A standalone server can still hold data from an earlier cluster; only a single
        # ID folder says unambiguously which one.
        try:
            subdirs = [p.name for p in base.iterdir() if p.is_dir()]
        except OSError:
            return ""
        return subdirs[0] if len(subdirs) == 1 else ""

    def _copy_plan(self, target: Optional[Path], new_id: str) -> Optional[Tuple[Path, Path]]:
        """(source, destination) for transfer data that would otherwise be left behind.

        ARK files uploads under <cluster folder>\\<cluster id>, so the data moves from the
        old ID's folder to the new ID's folder; other IDs in the old folder stay put.
        """
        if target is None or not new_id:
            return None
        base = self.current_dir if self.current_dir is not None else server_cluster_data_dir(self.server_dir)
        old_id = self._old_cluster_id(base)
        if not old_id:
            return None
        src = cluster_id_data_dir(base, old_id)
        dst = cluster_id_data_dir(target, new_id)
        if not dir_has_files(src) or same_path(src, dst):
            return None
        return src, dst

    def _target_dir(self) -> Optional[Path]:
        raw = self.var_cluster_dir.get().strip()
        return Path(raw) if raw else None

    def _refresh(self) -> None:
        joining = self.var_mode.get() == "join"
        cid = self.var_cluster_id.get().strip()
        target = self._target_dir()

        plan = self._copy_plan(target, cid)
        if plan is not None and self._current_shared_with:
            self.var_copy.set(False)
            self.var_copy_label.set(
                f"Not copying {plan[0]}: it is still in use by " + "; ".join(self._current_shared_with)
                + ", and copying it would duplicate their players' uploads."
            )
            self.chk_copy.configure(state="disabled")
        elif plan is not None:
            text = f"Bring this server's transfer data along: copy {plan[0]} to {plan[1]}"
            if self.running:
                # Copied while the server is stopped: anything players download meanwhile
                # would otherwise exist in both folders.
                text += " (the server is saved and stopped first, then started again)"
            self.var_copy_label.set(text)
            self.chk_copy.configure(state="normal")
        else:
            self.var_copy_label.set("No existing transfer data to bring along.")
            self.chk_copy.configure(state="disabled")

        self.btn_apply.configure(
            text="Save" if self._unchanged(cid, target) else ("Join Cluster" if joining else "Create Cluster")
        )

        warnings: List[str] = []
        if cid and not CLUSTER_ID_RE.match(cid):
            warnings.append("The cluster ID may only use letters, numbers, '-' and '_'.")
        if target is not None and " " in str(target):
            warnings.append("The folder path contains spaces; ARK cuts the path at the first space. Pick another folder.")
        if not joining:
            clash = next((c for c in self.clusters if c.cluster_id == cid), None)
            if clash is not None:
                warnings.append(
                    f"Another profile already uses the ID '{cid}' ({'; '.join(clash.member_names)}). "
                    "Pick 'Join' to share its folder, or use a different ID."
                )
        info = self._picked_cluster() if joining else None
        if info is not None:
            def mod_set(csv: str) -> Set[str]:
                return {m for m in csv.split(",") if m}
            # Transfers only care which mods are present, not their order.
            mine = mod_set(self.cfg.normalized_mods())
            odd = [name for name, mods in zip(info.member_names, info.mods) if mod_set(mods) != mine]
            if odd:
                warnings.append(
                    "Mods differ from: " + "; ".join(odd) + ". Modded dinos and items are lost on transfer "
                    "to a map that lacks the mod, so keep the mod lists the same."
                )
            shared = [n for n, d in zip(info.member_names, info.member_dirs) if same_path(Path(d), self.server_dir)]
            if shared:
                warnings.append(
                    "Runs from the same install folder as: " + "; ".join(shared) + ". Servers that run at "
                    "the same time each need their own install folder."
                )
            if info.cluster_dir is not None and any(
                same_path(info.cluster_dir, server_cluster_data_dir(Path(d))) for d in info.member_dirs
            ):
                warnings.append(
                    "That cluster keeps its data inside one server's install. It works, but open that "
                    "server's cluster setup and give the cluster its own folder when convenient."
                )
        clashes = sorted(self._used_ports & {int(self.cfg.port), int(self.cfg.query_port), int(self.cfg.rcon_port)})
        if clashes:
            warnings.append(
                f"Port(s) {', '.join(map(str, clashes))} are also set on another profile; servers that run "
                "at the same time need their own ports."
            )
        self.var_warning.set("\n".join(warnings))

    def _unchanged(self, cid: str, target: Optional[Path]) -> bool:
        """The profile already runs with exactly this ID and shared folder."""
        return (
            bool(self.current_id) and cid == self.current_id and target is not None
            and self.current_dir is not None and same_path(target, self.current_dir)
            and self.cfg.cluster_custom_path_enable
        )

    # -- actions -----------------------------------------------------------
    def _on_apply(self) -> None:
        cid = self.var_cluster_id.get().strip()
        target = self._target_dir()
        joining = self.var_mode.get() == "join"
        # A listed cluster, or the one this profile already runs, keeps its ID whatever it
        # contains; only IDs typed in new are held to the pattern.
        if not cid or (not joining and cid != self.current_id and not CLUSTER_ID_RE.match(cid)):
            messagebox.showerror("Cluster", "Enter a cluster ID using letters, numbers, '-' and '_' only.", parent=self)
            return
        if target is None:
            messagebox.showerror("Cluster", "Choose a cluster data folder.", parent=self)
            return
        if self._unchanged(cid, target):
            self.destroy()  # nothing to back up, copy or restart for
            return
        if joining and " " in str(target):
            info = self._picked_cluster()
            who = "; ".join(info.member_names) if info is not None else "the other servers"
            messagebox.showerror(
                "Cluster",
                f"That cluster's folder has a space in its path, which ARK cannot read. Move it to a folder "
                f"without spaces in the cluster setup of: {who}; then join it.",
                parent=self,
            )
            return
        problem = cluster_folder_problem(target, [self.server_dir], [resolve_backup_root(self.cfg, self.app.app_base)])
        if problem:
            messagebox.showerror("Cluster", problem, parent=self)
            return

        plan = self._copy_plan(target, cid)
        copy_ok = plan is not None and self.var_copy.get() and not self._current_shared_with
        req = ClusterSetupRequest(
            cluster_id=cid,
            cluster_dir=target,
            joining=joining,
            copy_from=plan[0] if (copy_ok and plan) else None,
            copy_to=plan[1] if (copy_ok and plan) else None,
            backup_first=bool(self.var_backup.get()),
        )
        self.destroy()
        self.app.start_cluster_setup(req)

    def _on_leave(self) -> None:
        if not messagebox.askyesno(
            "Leave Cluster",
            f"Take {self.cfg.server_name} out of cluster '{self.current_id}'?\n\n"
            "Its world and the cluster folder are kept as they are; the server just stops sharing "
            "transfers with the other maps. "
            + ("It keeps running unchanged until it restarts." if self.running else "Applies on the next start."),
            parent=self,
        ):
            return
        self.destroy()
        self.app.leave_cluster()


@dataclass
class PlannedServer:
    """One profile the cluster wizard is about to create."""
    map_name: str
    display_name: str
    session_name: str
    server_dir: Path
    port: int = 0
    query_port: int = 0
    rcon_port: int = 0
    settings: Optional[AppConfig] = None  # its own settings instead of the template's (import)
    ini: Dict[str, str] = field(default_factory=dict)  # staged INI text by file name (import)


CROSSPLAY_PLATFORM = "PC+XSX+WINGDK"


class ClusterWizardDialog(tk.Toplevel):
    """Create a cluster from scratch, one profile and install folder per map, or add maps to one.

    Every new profile starts as a copy of a template profile (its rates, backups, Discord,
    advanced flags...), then gets its map, its own install folder and ports, the shared
    cluster settings and the cluster ID and folder.
    """

    def __init__(self, app: "ServerManagerApp", add_to: Optional[ClusterView] = None) -> None:
        super().__init__(app.root)
        self.app = app
        self.add_to = add_to
        self._plan: List[PlannedServer] = []
        self._plan_error = ""
        self._refresh_after: Optional[str] = None

        refs = list(app.global_cfg.servers)
        self._template_ids = [r.id for r in refs]
        self._template_labels = [r.display_name or r.id for r in refs]
        if add_to is not None and add_to.members:
            template_id = add_to.members[0]
        else:
            template_id = app.active_server_id
        self._template_start_id = template_id
        template = app._runtime(template_id).cfg
        self._existing_maps: Set[str] = set()
        member_dirs: List[str] = []
        if add_to is not None:
            for sid in add_to.members:
                mcfg = app._runtime(sid).cfg
                self._existing_maps.add(mcfg.map_name)
                member_dirs.append(mcfg.server_dir)

        if add_to is not None:
            name, cid = add_to.name, add_to.cluster_id
            # A member that chose a shared folder knows the cluster's folder best.
            chosen = next(
                (app._runtime(sid).cfg for sid in add_to.members
                 if app._runtime(sid).cfg.cluster_custom_path_enable and app._runtime(sid).cfg.cluster_dir_override.strip()),
                template,
            )
            folder = str(effective_cluster_dir(chosen) or "")
        else:
            name = "My Cluster"
            cid = make_cluster_id(name)
            folder = str(default_cluster_dir(app.app_base, cid, Path(template.server_dir)))
        root_guess = Path(member_dirs[0]).parent if member_dirs else Path(template.server_dir).parent / "ARK-Cluster"

        self.var_name = tk.StringVar(master=self, value=name)
        self.var_cid = tk.StringVar(master=self, value=cid)
        self.var_dir = tk.StringVar(master=self, value=folder)
        self.var_root = tk.StringVar(master=self, value=str(root_guess))
        self.var_prefix = tk.StringVar(master=self, value=name if add_to is None else self._prefix_from(template, add_to))
        self.var_admin = tk.StringVar(master=self, value=template.admin_password)
        self.var_join = tk.StringVar(master=self, value=template.join_password)
        self.var_max_players = tk.StringVar(master=self, value=str(template.max_players))
        self.var_mods = tk.StringVar(master=self, value=template.normalized_mods())
        self.var_crossplay = tk.BooleanVar(master=self, value=(template.server_platform or "").strip() == CROSSPLAY_PLATFORM)
        self.var_template = tk.StringVar(master=self, value=self._template_labels[self._template_ids.index(template_id)] if template_id in self._template_ids else "")
        self.var_install_now = tk.BooleanVar(master=self, value=True)
        self.var_custom_map = tk.StringVar(master=self, value="")
        self.var_warning = tk.StringVar(master=self, value="")
        self.var_maps: Dict[str, tk.BooleanVar] = {}
        self._cid_auto = add_to is None
        self._dir_auto = add_to is None
        self._prefix_auto = add_to is None

        self.title("Add Maps to Cluster" if add_to is not None else "New Cluster")
        self.transient(app.root)
        self.configure(background=THEME_COLORS["bg"])
        apply_initial_window_geometry(self, 1120, 760)
        apply_min_window_size(self, 980, 640)
        self._problems: List[str] = []
        self._build()
        if template_id in self._template_ids:
            self.cmb_template.current(self._template_ids.index(template_id))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())

        for var in (self.var_name, self.var_cid, self.var_dir, self.var_root, self.var_prefix,
                    self.var_admin, self.var_max_players, self.var_custom_map, self.var_template):
            var.trace_add("write", lambda *_: self._schedule_refresh())
        self.var_name.trace_add("write", lambda *_: self._on_name_changed())
        self.var_cid.trace_add("write", lambda *_: self._on_cid_changed())
        self._refresh()

        self.grab_set()
        self.focus_set()

    @staticmethod
    def _prefix_from(cfg: AppConfig, view: ClusterView) -> str:
        """Session name prefix the cluster's maps already use ('PvE - The Island' -> 'PvE')."""
        session = (cfg.server_name or "").strip()
        tail = f" - {pretty_map_name(cfg.map_name)}"
        return session[: -len(tail)] if session.endswith(tail) else view.name

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        theme = THEME_COLORS
        adding = self.add_to is not None
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        lf_cluster = ttk.LabelFrame(root, text="Cluster", padding=10)
        lf_cluster.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        lf_cluster.columnconfigure(1, weight=1)
        ttk.Label(lf_cluster, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(lf_cluster, textvariable=self.var_name, state="readonly" if adding else "normal") \
            .grid(row=0, column=1, columnspan=2, sticky="ew", padx=6)
        ttk.Label(lf_cluster, text="Cluster ID").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ent_cid = ttk.Entry(lf_cluster, textvariable=self.var_cid, state="readonly" if adding else "normal")
        ent_cid.grid(row=1, column=1, columnspan=2, sticky="ew", padx=6, pady=(6, 0))
        ent_cid.bind("<KeyRelease>", lambda _e: setattr(self, "_cid_auto", False))
        ttk.Label(lf_cluster, text="Data folder").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ent_dir = ttk.Entry(lf_cluster, textvariable=self.var_dir, state="readonly" if adding else "normal")
        ent_dir.grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))
        ent_dir.bind("<KeyRelease>", lambda _e: setattr(self, "_dir_auto", False))
        ttk.Button(lf_cluster, text="Browse", command=self._browse_dir, state="disabled" if adding else "normal") \
            .grid(row=2, column=2, pady=(6, 0))
        ttk.Label(
            lf_cluster,
            text="Uploaded survivors, dinos and items of every map live in the data folder.",
            foreground=theme["muted"], wraplength=440, justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

        lf_maps = ttk.LabelFrame(root, text="Maps", padding=10)
        lf_maps.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        for i, map_name in enumerate(MAP_PRESETS):
            in_cluster = map_name in self._existing_maps
            var = tk.BooleanVar(master=self, value=(not adding and i < 2))
            self.var_maps[map_name] = var
            text = pretty_map_name(map_name) + ("  (in the cluster)" if in_cluster else "")
            cb = ttk.Checkbutton(lf_maps, text=text, variable=var, command=self._schedule_refresh)
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 16))
            if in_cluster:
                var.set(False)
                cb.configure(state="disabled")
        ttk.Label(lf_maps, text="Other map (its exact name)").grid(row=(len(MAP_PRESETS) + 1) // 2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(lf_maps, textvariable=self.var_custom_map).grid(row=(len(MAP_PRESETS) + 1) // 2, column=1, sticky="ew", pady=(8, 0))

        lf_shared = ttk.LabelFrame(root, text="Shared Settings", padding=10)
        lf_shared.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(10, 0))
        lf_shared.columnconfigure(1, weight=1)
        rows = [
            ("Install folder for the maps", self.var_root),
            ("Server name prefix", self.var_prefix),
            ("Admin password (RCON)", self.var_admin),
            ("Join password", self.var_join),
            ("Max players", self.var_max_players),
            ("Mods (comma separated)", self.var_mods),
        ]
        for r, (label, var) in enumerate(rows):
            ttk.Label(lf_shared, text=label).grid(row=r, column=0, sticky="w", pady=(0 if r == 0 else 6, 0))
            ent = ttk.Entry(lf_shared, textvariable=var)
            ent.grid(row=r, column=1, sticky="ew", padx=6, pady=(0 if r == 0 else 6, 0))
            if var is self.var_prefix:
                ent.bind("<KeyRelease>", lambda _e: setattr(self, "_prefix_auto", False))
        ttk.Button(lf_shared, text="Browse", command=self._browse_root).grid(row=0, column=2)
        ttk.Checkbutton(lf_shared, text=f"Crossplay ({CROSSPLAY_PLATFORM})", variable=self.var_crossplay) \
            .grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(lf_shared, text="Other settings from").grid(row=len(rows) + 1, column=0, sticky="w", pady=(8, 0))
        self.cmb_template = ttk.Combobox(lf_shared, textvariable=self.var_template, values=self._template_labels, state="readonly")
        self.cmb_template \
            .grid(row=len(rows) + 1, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Label(
            lf_shared,
            text="Each map gets its own install folder inside the install folder above, its own ports, "
                 "and a server name like '<prefix> - The Island'. Backups, auto-saves, updates, Discord and "
                 "advanced start options are copied from the profile chosen above; game rates (INI settings) "
                 "are set per map in the INI Editor.",
            foreground=theme["muted"], wraplength=440, justify="left",
        ).grid(row=len(rows) + 2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        lf_plan = ttk.LabelFrame(root, text="Profiles To Create", padding=10)
        lf_plan.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(10, 0))
        lf_plan.columnconfigure(0, weight=1)
        lf_plan.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(lf_plan, columns=("map", "folder", "ports"), show="tree headings", height=8)
        self.tree.heading("#0", text="Profile")
        self.tree.heading("map", text="Map")
        self.tree.heading("folder", text="Install folder")
        self.tree.heading("ports", text="Game / Query / RCON")
        self.tree.column("#0", width=200)
        self.tree.column("map", width=120)
        self.tree.column("folder", width=230)
        self.tree.column("ports", width=140)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Checkbutton(
            lf_plan,
            text="Install the servers right away (one after another with SteamCMD)",
            variable=self.var_install_now,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        ttk.Label(root, textvariable=self.var_warning, foreground=theme["warning"], wraplength=1060, justify="left") \
            .grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        buttons = ttk.Frame(root)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self.btn_create = ttk.Button(buttons, text="Add Maps" if adding else "Create Cluster", command=self._on_create)
        self.btn_create.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=1)

    # -- state -------------------------------------------------------------
    def _on_name_changed(self) -> None:
        name = self.var_name.get().strip()
        if self._cid_auto and self.add_to is None:
            self.var_cid.set(make_cluster_id(name))
        if self._prefix_auto:
            self.var_prefix.set(name)

    def _on_cid_changed(self) -> None:
        if self._dir_auto and self.add_to is None:
            cid = self.var_cid.get().strip()
            if cid:
                self.var_dir.set(str(default_cluster_dir(self.app.app_base, cid, Path(self.var_root.get() or "."))))

    def _browse_dir(self) -> None:
        p = filedialog.askdirectory(title="Select the cluster data folder", parent=self, mustexist=False)
        if p:
            self._dir_auto = False
            self.var_dir.set(str(Path(p)))

    def _browse_root(self) -> None:
        p = filedialog.askdirectory(title="Select where the map installs go", parent=self, mustexist=False)
        if p:
            self.var_root.set(str(Path(p)))

    def _schedule_refresh(self) -> None:
        if self._refresh_after is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._refresh_after)
        self._refresh_after = self.after(300, self._refresh)

    def _template_cfg(self) -> AppConfig:
        # By position, not by label: two profiles may carry the same name.
        idx = self.cmb_template.current() if hasattr(self, "cmb_template") else -1
        if 0 <= idx < len(self._template_ids):
            return self.app._runtime(self._template_ids[idx]).cfg
        return self.app._runtime(self._template_start_id).cfg

    def _selected_maps(self) -> List[str]:
        maps = [m for m, var in self.var_maps.items() if var.get()]
        custom = self.var_custom_map.get().strip()
        if custom and custom not in maps:
            maps.append(custom)
        return maps

    def _make_plan(self) -> Tuple[List[PlannedServer], List[str], List[str]]:
        """(plan, problems that block creating, notes that do not)."""
        problems: List[str] = []
        notes: List[str] = []
        maps = self._selected_maps()
        name = self.var_name.get().strip()
        cid = self.var_cid.get().strip()
        root_raw = self.var_root.get().strip()
        prefix = self.var_prefix.get().strip()
        if not name:
            problems.append("Give the cluster a name.")
        if self.add_to is None:
            if not CLUSTER_ID_RE.match(cid):
                problems.append("The cluster ID may only use letters, numbers, '-' and '_'.")
            elif any(c.cluster_id == cid for c in self.app._cluster_views()[0]):
                problems.append(f"A cluster with the ID '{cid}' exists already; use Add Maps on it instead.")
        if not maps:
            problems.append("Tick at least one map.")
        custom = self.var_custom_map.get().strip()
        if custom and not re.match(r"^[A-Za-z0-9_]+$", custom):
            problems.append("The other map's name may only use letters, numbers and '_' (for example Astraeos_WP).")
        if custom and custom.lower() in {m.lower() for m in self._existing_maps}:
            problems.append(f"{custom} is already in this cluster.")
        if not (self.var_admin.get() or "").strip():
            problems.append("Set an admin password: the manager saves and stops the servers through RCON.")
        if not (1 <= safe_int(self.var_max_players.get(), 0) <= 255):
            problems.append("Max players must be between 1 and 255.")
        root_dir = Path(root_raw) if root_raw else None
        if root_dir is None or not root_dir.is_absolute():
            problems.append("Choose a full path for the install folder, such as D:\\ARK\\MyCluster.")
            return [], problems, notes

        template = self._template_cfg()
        if re.search(r"(?i)(-clusterid|clusterdiroverride|[?-]port=|queryport|sessionname|altsavedirectoryname)", template.custom_start_args or ""):
            notes.append(
                "The template's custom server arguments set ports, the session name or cluster options; they "
                f"would override the per-map values: '{template.custom_start_args}'. They are left out of the new maps."
            )
        existing_dirs = [Path(r.server_dir) for r in self.app.global_cfg.servers if r.server_dir]
        taken_names = {(r.display_name or "").strip().lower() for r in self.app.global_cfg.servers}
        plan: List[PlannedServer] = []
        used_ports = self.app._collect_used_ports()  # read once per refresh, not once per map
        planned_ports: Set[int] = set()
        seen_dirs: Dict[str, str] = {}
        for map_name in maps:
            pretty = pretty_map_name(map_name)
            server_dir = root_dir / map_folder_name(map_name)
            key = os.path.normcase(os.path.normpath(str(server_dir)))
            if key in seen_dirs:
                problems.append(f"{map_name} and {seen_dirs[key]} would share the install folder {server_dir}; tick each map once.")
                continue
            seen_dirs[key] = map_name
            clash = next((d for d in existing_dirs if path_contains(d, server_dir) or path_contains(server_dir, d)), None)
            if clash is not None:
                problems.append(f"{server_dir} overlaps the install folder of another profile ({clash}).")
            display = f"{pretty} ({name})" if name else pretty
            n = 2
            while display.lower() in taken_names:
                display = f"{pretty} ({name}) {n}"
                n += 1
            taken_names.add(display.lower())
            item = PlannedServer(
                map_name=map_name,
                display_name=display,
                session_name=f"{prefix} - {pretty}" if prefix else pretty,
                server_dir=server_dir,
            )
            probe = copy.deepcopy(template)
            try:
                item.port, item.query_port, item.rcon_port = self.app.auto_assign_ports(
                    probe, stride=10, max_tries=300, extra_used=planned_ports, used=used_ports
                )
                planned_ports |= {item.port, item.query_port, item.rcon_port}
            except Exception as e:
                problems.append(f"No free ports for {pretty}: {e}")
            plan.append(item)

        target = Path(self.var_dir.get().strip()) if self.var_dir.get().strip() else None
        all_cfgs = [self.app._runtime(r.id).cfg for r in self.app.global_cfg.servers]
        backup_roots = {str(resolve_backup_root(c, self.app.app_base)) for c in all_cfgs + [template]}
        problem = cluster_folder_problem(
            target,
            [p.server_dir for p in plan] + existing_dirs,
            [Path(b) for b in backup_roots],
            allow_own_saved=False,
        )
        if self.add_to is not None:
            folders = {os.path.normcase(os.path.normpath(str(effective_cluster_dir(self.app._runtime(sid).cfg) or "")))
                       for sid in self.add_to.members}
            if len(folders) > 1:
                problem = ("The maps of this cluster do not share one data folder, so transfers between them do not "
                           "work yet. Open Cluster... on each of them and pick the same folder, then add maps.")
            elif problem:
                # Seen when the cluster still keeps its data inside one map's install: new maps must
                # not be pointed in there. Moving the folder is one Cluster... click on that map.
                problem = (f"This cluster's data folder ({target}) cannot be shared with new maps: {problem} "
                           "Open Cluster... on one of its servers and give the cluster its own folder first.")
        if problem:
            problems.append(problem)
        return plan, problems, notes

    def _refresh(self) -> None:
        self._refresh_after = None
        self._plan, problems, notes = self._make_plan()
        self._problems = problems
        self.tree.delete(*self.tree.get_children())
        for item in self._plan:
            ports = f"{item.port} / {item.query_port} / {item.rcon_port}" if item.port else "-"
            self.tree.insert("", "end", text=item.display_name, values=(item.map_name, str(item.server_dir), ports))
        self.var_warning.set("\n".join(problems + [f"Note: {n}" for n in notes]))
        self.btn_create.configure(state="disabled" if problems or not self._plan else "normal")

    # -- actions -----------------------------------------------------------
    def _on_create(self) -> None:
        self._refresh()
        plan, problems = self._plan, self._problems
        if problems or not plan:
            return
        template = self._template_cfg()
        shared = {
            "admin_password": self.var_admin.get().strip(),
            "join_password": self.var_join.get().strip(),
            "max_players": safe_int(self.var_max_players.get(), template.max_players),
            "mods": ",".join(p.strip() for p in self.var_mods.get().split(",") if p.strip()),
            "server_platform": CROSSPLAY_PLATFORM if self.var_crossplay.get() else "",
        }
        cluster = {
            "cluster_id": self.var_cid.get().strip(),
            "cluster_dir": self.var_dir.get().strip(),
            "name": self.var_name.get().strip(),
        }
        install = bool(self.var_install_now.get())
        self.destroy()
        self.app.create_cluster_profiles(plan, template, shared, cluster, install=install, adding=self.add_to is not None)


class ClusterHealthDialog(tk.Toplevel):
    """List what is wrong with a cluster and repair what can be repaired with one click."""

    SEVERITY_LABELS = {"error": "Problem", "warning": "Warning", "info": "Note"}

    def __init__(self, app: "ServerManagerApp", view: ClusterView) -> None:
        super().__init__(app.root)
        self.app = app
        self.view = view
        self._issues: List[HealthIssue] = []
        self.var_summary = tk.StringVar(master=self, value="")
        self.title(f"Cluster Health - {view.name}")
        self.transient(app.root)
        self.configure(background=THEME_COLORS["bg"])
        apply_initial_window_geometry(self, 900, 520)
        apply_min_window_size(self, 760, 420)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())
        self._recheck()
        self.grab_set()
        self.focus_set()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        ttk.Label(root, textvariable=self.var_summary, wraplength=860, justify="left").grid(row=0, column=0, sticky="w")
        self.tree = ttk.Treeview(root, columns=("what", "fix"), show="headings", selectmode="browse")
        self.tree.heading("what", text="What is wrong")
        self.tree.heading("fix", text="One-click fix")
        self.tree.column("what", width=640)
        self.tree.column("fix", width=160)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync())
        buttons = ttk.Frame(root)
        buttons.grid(row=2, column=0, sticky="e", pady=(10, 0))
        self.btn_fix = ttk.Button(buttons, text="Fix Selected", command=self._fix_selected)
        self.btn_fix.grid(row=0, column=0, padx=(0, 8))
        self.btn_fix_all = ttk.Button(buttons, text="Fix All", command=self._fix_all)
        self.btn_fix_all.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(buttons, text="Check Again", command=self._recheck).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(buttons, text="Close", command=self.destroy).grid(row=0, column=3)

    def _recheck(self) -> None:
        view = self.app._cluster_view(self.view.cluster_id)
        if view is not None:
            self.view = view
        self._issues = self.app._cluster_health(self.view)
        self.tree.delete(*self.tree.get_children())
        for i, issue in enumerate(self._issues):
            fix = ServerManagerApp.HEALTH_FIX_LABELS.get(issue.fix, "")
            self.tree.insert("", "end", iid=str(i), values=(f"{self.SEVERITY_LABELS.get(issue.severity, issue.severity)}: {issue.text}", fix))
        counts = {sev: sum(1 for x in self._issues if x.severity == sev) for sev in ("error", "warning", "info")}
        if not self._issues:
            self.var_summary.set(f"{self.view.name}: no problems found.")
        else:
            self.var_summary.set(
                f"{self.view.name}: {counts['error']} problem(s), {counts['warning']} warning(s), {counts['info']} note(s). "
                "Fixes change the saved profiles; running maps pick them up on their next restart."
            )
        self._sync()

    def _selected(self) -> Optional[HealthIssue]:
        sel = self.tree.selection()
        if not sel:
            return None
        idx = int(sel[0])
        return self._issues[idx] if 0 <= idx < len(self._issues) else None

    def _sync(self) -> None:
        issue = self._selected()
        self.btn_fix.configure(state="normal" if issue is not None and issue.fix else "disabled")
        self.btn_fix_all.configure(state="normal" if any(x.fix for x in self._issues) else "disabled")

    def _fix_selected(self) -> None:
        issue = self._selected()
        if issue is not None and issue.fix:
            self.app._fix_health_issue(self.view, issue, parent=self)
            self._recheck()

    # Fixes that change nothing a player owns. Mod lists and data folders change what maps
    # load and where uploads live; those are fixed one at a time, each with its own question.
    FIX_ALL_SAFE = ("new_ports", "session_names", "same_platform")

    def _fix_all(self) -> None:
        todo: List[HealthIssue] = []
        seen: Set[Tuple[str, Tuple[str, ...]]] = set()
        for issue in self._issues:
            key = (issue.fix, tuple(issue.servers))
            if issue.fix in self.FIX_ALL_SAFE and key not in seen:
                seen.add(key)
                todo.append(issue)
        left = [i for i in self._issues if i.fix and i.fix not in self.FIX_ALL_SAFE]
        if not todo:
            messagebox.showinfo(
                "Cluster Health",
                "The remaining fixes change mod lists or data folders; select each and use Fix Selected.",
                parent=self,
            )
            return
        lines = [f"- {ServerManagerApp.HEALTH_FIX_LABELS.get(i.fix, i.fix)}: "
                 + ", ".join(self.app._display_name(sid) for sid in i.servers) for i in todo]
        extra = ("\n\nMod list and data folder fixes are not included; do them one at a time." if left else "")
        if not messagebox.askyesno("Cluster Health", "Apply these fixes?\n\n" + "\n".join(lines) + extra, parent=self):
            return
        for issue in todo:
            self.app._fix_health_issue(self.view, issue, parent=self, ask=False)
        self._recheck()


class CopySettingsDialog(tk.Toplevel):
    """Copy one server's INI settings (rates, taming, breeding...) onto other servers."""

    def __init__(self, app: "ServerManagerApp", source_id: str) -> None:
        super().__init__(app.root)
        self.app = app
        self.source_id = source_id
        self.var_gus = tk.BooleanVar(master=self, value=True)
        self.var_game = tk.BooleanVar(master=self, value=True)
        self.var_spawns = tk.BooleanVar(master=self, value=False)
        self.var_targets: Dict[str, tk.BooleanVar] = {}
        src_cfg = app._runtime(source_id).cfg
        mates: Set[str] = set()
        if effective_cluster_dir(src_cfg) is not None:
            view = app._cluster_view(src_cfg.cluster_id.strip())
            mates = set(view.members) if view else set()
        self._order = [r.id for r in app.global_cfg.servers if r.id != source_id]
        for sid in self._order:
            self.var_targets[sid] = tk.BooleanVar(master=self, value=sid in mates)

        self.title(f"Copy Settings - from {app._display_name(source_id)}")
        self.transient(app.root)
        self.configure(background=THEME_COLORS["bg"])
        apply_initial_window_geometry(self, 680, 520)
        apply_min_window_size(self, 560, 420)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.focus_set()

    def _build(self) -> None:
        theme = THEME_COLORS
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        ttk.Label(
            root,
            text=f"Copy the INI settings of {self.app._display_name(self.source_id)} onto the servers ticked below. "
                 "Each server keeps its own name, ports, passwords, RCON and mod list, and any setting only it "
                 "has. The copies are staged and apply on each server's next start.",
            wraplength=640, justify="left",
        ).grid(row=0, column=0, sticky="w")
        lf = ttk.LabelFrame(root, text="Copy To", padding=10)
        lf.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        for i, sid in enumerate(self._order):
            rt = self.app._runtime(sid)
            note = "  (running: applies on restart)" if rt.is_running() else ""
            ttk.Checkbutton(lf, text=self.app._display_name(sid) + note, variable=self.var_targets[sid]) \
                .grid(row=i, column=0, sticky="w")
        if not self._order:
            ttk.Label(lf, text="There is no other server profile yet.", foreground=theme["muted"]).grid(row=0, column=0, sticky="w")
        opts = ttk.LabelFrame(root, text="What", padding=10)
        opts.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(opts, text="GameUserSettings.ini (rates, taming, structures...)", variable=self.var_gus).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(opts, text="Game.ini (per-level stats, breeding, spawns...)", variable=self.var_game).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(
            opts,
            text="Also map-specific spawn container entries (only between servers on the same map)",
            variable=self.var_spawns,
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        buttons = ttk.Frame(root)
        buttons.grid(row=3, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Copy", command=self._on_copy).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=1)

    def _on_copy(self) -> None:
        targets = [sid for sid in self._order if self.var_targets[sid].get()]
        if not targets:
            messagebox.showwarning("Copy Settings", "Tick at least one server to copy to.", parent=self)
            return
        if not (self.var_gus.get() or self.var_game.get()):
            messagebox.showwarning("Copy Settings", "Tick GameUserSettings.ini, Game.ini or both.", parent=self)
            return
        opts = {
            "include_gus": bool(self.var_gus.get()),
            "include_game": bool(self.var_game.get()),
            "include_map_specific": bool(self.var_spawns.get()),
        }
        self.destroy()
        self.app.copy_settings(self.source_id, targets, **opts)


class ClusterImportDialog(tk.Toplevel):
    """Recreate an exported cluster on this machine: new installs, new ports, same settings."""

    def __init__(self, app: "ServerManagerApp", path: Path, data: Dict[str, Any]) -> None:
        super().__init__(app.root)
        self.app = app
        self.path = path
        self.data = data
        info = data.get("cluster") or {}
        name = str(info.get("name") or "Imported Cluster")
        cid = str(info.get("id") or "")
        existing = {c.cluster_id for c in app._cluster_views()[0]}
        if not CLUSTER_ID_RE.match(cid) or cid in existing:
            cid = make_cluster_id(name)  # never merge into a cluster that already exists here
        self.var_name = tk.StringVar(master=self, value=name)
        self.var_cid = tk.StringVar(master=self, value=cid)
        self.var_root = tk.StringVar(master=self, value=str(Path(app.cfg.server_dir).parent / map_folder_name(name)))
        self.var_dir = tk.StringVar(master=self, value=str(default_cluster_dir(app.app_base, cid, Path(app.cfg.server_dir))))
        self.var_admin = tk.StringVar(master=self, value="")
        self.var_join = tk.StringVar(master=self, value="")
        self.var_install_now = tk.BooleanVar(master=self, value=True)
        self.var_warning = tk.StringVar(master=self, value="")
        self._plan: List[PlannedServer] = []
        self._problems: List[str] = []
        self._plan_problems: List[str] = []
        self.title(f"Import Cluster - {path.name}")
        self.transient(app.root)
        self.configure(background=THEME_COLORS["bg"])
        apply_initial_window_geometry(self, 900, 600)
        apply_min_window_size(self, 760, 520)
        self._build()
        self._refresh_after: Optional[str] = None
        self._planned_for: Optional[str] = None  # install folder the current plan was made for
        for var in (self.var_name, self.var_cid, self.var_root, self.var_dir, self.var_admin):
            var.trace_add("write", lambda *_: self._schedule_refresh())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())
        self._refresh()
        self.grab_set()
        self.focus_set()

    def _build(self) -> None:
        theme = THEME_COLORS
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(7, weight=1)
        rows = [
            ("Cluster name", self.var_name),
            ("Cluster ID", self.var_cid),
            ("Install folder for the maps", self.var_root),
            ("Cluster data folder", self.var_dir),
            ("Admin password (RCON)", self.var_admin),
            ("Join password", self.var_join),
        ]
        for r, (label, var) in enumerate(rows):
            ttk.Label(root, text=label).grid(row=r, column=0, sticky="w", pady=(0 if r == 0 else 6, 0))
            ttk.Entry(root, textvariable=var).grid(row=r, column=1, sticky="ew", padx=6, pady=(0 if r == 0 else 6, 0))
        ttk.Label(
            root,
            text="Passwords and machine paths are not part of a cluster file; set them here. Every map gets its "
                 "own install folder and free ports, and its INI settings from the file are staged for its first start.",
            foreground=theme["muted"], wraplength=860, justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.tree = ttk.Treeview(root, columns=("map", "folder", "ports"), show="tree headings", height=8)
        self.tree.heading("#0", text="Profile")
        self.tree.heading("map", text="Map")
        self.tree.heading("folder", text="Install folder")
        self.tree.heading("ports", text="Game / Query / RCON")
        self.tree.column("#0", width=220)
        self.tree.column("folder", width=320)
        self.tree.grid(row=7, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        ttk.Checkbutton(root, text="Install the servers right away (one after another with SteamCMD)",
                        variable=self.var_install_now).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(root, textvariable=self.var_warning, foreground=theme["warning"], wraplength=860, justify="left") \
            .grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(root)
        buttons.grid(row=10, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self.btn_import = ttk.Button(buttons, text="Import", command=self._on_import)
        self.btn_import.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(row=0, column=1)

    def _schedule_refresh(self) -> None:
        if self._refresh_after is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(self._refresh_after)
        self._refresh_after = self.after(300, self._refresh)

    def _refresh(self) -> None:
        self._refresh_after = None
        if not self.winfo_exists():
            return  # closed while a refresh was still pending
        problems: List[str] = []
        name = self.var_name.get().strip()
        cid = self.var_cid.get().strip()
        if not name:
            problems.append("Give the cluster a name.")
        if not CLUSTER_ID_RE.match(cid):
            problems.append("The cluster ID may only use letters, numbers, '-' and '_'.")
        elif any(c.cluster_id == cid for c in self.app._cluster_views()[0]):
            problems.append(f"A cluster with the ID '{cid}' exists already here.")
        if not self.var_admin.get().strip():
            problems.append("Set an admin password: the manager saves and stops the servers through RCON.")
        root_raw = self.var_root.get().strip()
        root_dir = Path(root_raw) if root_raw else None
        plan: List[PlannedServer] = []
        if root_dir is None or not root_dir.is_absolute():
            problems.append("Choose a full path for the install folder.")
        elif self._planned_for == root_raw and self._plan:
            # Same install folder: keep the plan (and its ports); only re-check the rest.
            plan = self._plan
            problems.extend(self._plan_problems)
            existing_dirs = [Path(r.server_dir) for r in self.app.global_cfg.servers if r.server_dir]
            problem = cluster_folder_problem(
                Path(self.var_dir.get().strip()) if self.var_dir.get().strip() else None,
                [p.server_dir for p in plan] + existing_dirs,
                [resolve_backup_root(self.app.cfg, self.app.app_base)],
                allow_own_saved=False,
            )
            if problem:
                problems.append(problem)
        else:
            plan_problems: List[str] = []
            existing_dirs = [Path(r.server_dir) for r in self.app.global_cfg.servers if r.server_dir]
            taken = {(r.display_name or "").strip().lower() for r in self.app.global_cfg.servers}
            used = self.app._collect_used_ports()
            planned: Set[int] = set()
            seen: Set[str] = set()
            for item in self.data["servers"]:
                cfg = settings_from_export(item["settings"])
                server_dir = root_dir / map_folder_name(cfg.map_name)
                key = os.path.normcase(os.path.normpath(str(server_dir)))
                n = 2
                while key in seen:  # the same map twice in one cluster: number the folders
                    server_dir = root_dir / f"{map_folder_name(cfg.map_name)}{n}"
                    key = os.path.normcase(os.path.normpath(str(server_dir)))
                    n += 1
                seen.add(key)
                clash = next((d for d in existing_dirs if path_contains(d, server_dir) or path_contains(server_dir, d)), None)
                if clash is not None:
                    plan_problems.append(f"{server_dir} overlaps the install folder of another profile ({clash}).")
                display = str(item.get("display_name") or pretty_map_name(cfg.map_name))
                base_name, n = display, 2
                while display.lower() in taken:
                    display, n = f"{base_name} {n}", n + 1
                taken.add(display.lower())
                planned_item = PlannedServer(cfg.map_name, display, cfg.server_name, server_dir)
                planned_item.settings = cfg
                planned_item.ini = {k: str(v) for k, v in (item.get("ini") or {}).items()}
                try:
                    planned_item.port, planned_item.query_port, planned_item.rcon_port = self.app.auto_assign_ports(
                        copy.deepcopy(cfg), stride=10, max_tries=300, extra_used=planned, used=used
                    )
                    planned |= {planned_item.port, planned_item.query_port, planned_item.rcon_port}
                except Exception as e:
                    plan_problems.append(f"No free ports for {display}: {e}")
                plan.append(planned_item)
            self._planned_for, self._plan_problems = root_raw, plan_problems
            problems.extend(plan_problems)
            problem = cluster_folder_problem(
                Path(self.var_dir.get().strip()) if self.var_dir.get().strip() else None,
                [p.server_dir for p in plan] + existing_dirs,
                [resolve_backup_root(self.app.cfg, self.app.app_base)],
                allow_own_saved=False,
            )
            if problem:
                problems.append(problem)
        self._plan, self._problems = plan, problems
        self.tree.delete(*self.tree.get_children())
        for p in plan:
            ports = f"{p.port} / {p.query_port} / {p.rcon_port}" if p.port else "-"
            self.tree.insert("", "end", text=p.display_name, values=(p.map_name, str(p.server_dir), ports))
        self.var_warning.set("\n".join(problems))
        self.btn_import.configure(state="disabled" if problems or not plan else "normal")

    def _on_import(self) -> None:
        self._refresh()
        if self._problems or not self._plan:
            return
        info = self.data.get("cluster") or {}
        shared = {"admin_password": self.var_admin.get().strip(), "join_password": self.var_join.get().strip()}
        cluster = {"cluster_id": self.var_cid.get().strip(), "cluster_dir": self.var_dir.get().strip(),
                   "name": self.var_name.get().strip()}
        stagger = min(3600, max(0, safe_int(info.get("start_stagger_sec"), DEFAULT_START_STAGGER_SEC)))
        install = bool(self.var_install_now.get())
        plan = self._plan
        self.destroy()
        self.app.create_cluster_profiles(plan, self.app.cfg, shared, cluster, install=install, adding=False, stagger_sec=stagger)


class ServerManagerApp:
    def __init__(self, root: tk.Tk, app_base: Path):
        self.root = root
        self.root.title(APP_NAME)
        apply_min_window_size(self.root, 1220, 780)

        self.app_base = app_base
        self.global_config_path = self.app_base / GLOBAL_CONFIG_NAME
        self.log_dir = self.app_base / LOG_DIR_NAME

        self.global_store = GlobalConfigStore(self.global_config_path, global_lock_path(self.app_base))
        global_res = self.global_store.load()
        self.global_cfg = global_res.cfg
        self._global_migrated = global_res.migrated
        self._global_warnings = global_res.warnings

        self.active_server_id: str = ""
        self.server_store: Optional[ServerConfigStore] = None
        self.cfg = AppConfig()
        self._server_migrated = False
        self._server_warnings: List[str] = []

        # One runtime per server profile: process, tasks and timers of each run side by side.
        self._runtimes: Dict[str, ServerRuntime] = {}
        self._runtimes_lock = threading.RLock()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_stop = threading.Event()
        self._auto_update_announced: Dict[str, bool] = {}
        # Maps that heard a Restart All countdown, by id() of its cancel event.
        self._group_countdown_members: Dict[int, List[ServerRuntime]] = {}
        # Cluster-wide sequences running now (Start All, Restart All), by cluster ID, and the
        # event that cancels each: Stop All and closing the window set it.
        self._group_ops: Dict[str, str] = {}
        self._group_cancel: Dict[str, threading.Event] = {}
        self._player_poll_stop = threading.Event()
        # Bumped whenever the SteamCMD slot changes hands (see _take_update_slot).
        self._update_slot_turns = 0
        # One SteamCMD run (install, update, validate) at a time across this manager's
        # servers. Reentrant: Update & Restart holds it across stop, update and start.
        self._update_slot = threading.RLock()

        # Bumped on every profile save, so cached per-profile results know they are stale.
        self._cfg_generation = 0
        self._health_cache: Dict[Tuple[str, Tuple[str, ...]], Tuple[int, float, List[HealthIssue]]] = {}
        self._health_running: Set[Tuple[str, Tuple[str, ...]]] = set()
        self._initialize_profiles()

        self._autosave_after_id: Optional[str] = None
        self._autosave_guard = False
        self._in_background = False  # window hidden, servers still managed
        self._was_zoomed = False  # maximized when it went to the background
        self._close_asking = False
        self._closing_stop = False  # "stop the servers and quit" is under way
        self._quit_when_idle = False  # in the background only until the running tasks finish
        self._quit_check_id: Optional[str] = None
        self._grab_on_show: Any = None  # a modal dialog hidden with the window
        self._followups = 0  # tasks finished whose then/after have not run yet
        self._followups_lock = threading.Lock()
        self._status_key: Optional[Tuple[Any, ...]] = None
        # Ports of servers between their port check and their launch (see _reserve_ports).
        self._starting_ports: Dict[str, Set[int]] = {}

        self._rcon_factory = create_rcon_client

        # Console noise filter state (GUI only)
        self._suppress_ga_console: bool = True

        # INI editor state
        self._ini_loaded_target: Optional[str] = None
        self._ini_doc: Optional[IniDocument] = None
        self._ini_items_index: Dict[str, int] = {}
        self._ini_apply_after_id: Optional[str] = None
        self._ini_selected_line_idx: Optional[int] = None
        self._ini_paths_current: Optional[IniStagePaths] = None

        self._server_id_order: List[str] = []

        self._init_vars()
        self._build_layout()
        self._apply_text_theme()
        self.root.after(0, self._apply_text_theme)
        self._refresh_server_selector(self.active_server_id)

        self.logger, self._gui_log_handler = build_logger(self.log_dir, self.txt_log)
        self._gui_log_handler.set_drop_predicate(self._drop_console_message)
        self._sync_console_log_filter_state()
        self._write_banner()

        shared = _programdata_base()
        if shared:
            ensure_programdata_acl(shared, self.logger)

        for w in self._global_warnings:
            self.logger.info(f"[CONFIG] {w}")
        for w in self._server_warnings:
            self.logger.info(f"[CONFIG] {w}")
        if self._global_migrated:
            try:
                self.global_store.save(self.global_cfg)
                self.logger.info("[CONFIG] Migrated/initialized global config saved.")
            except Exception as e:
                self.logger.info(f"[CONFIG] Save after global migration failed: {e}")
        if self._server_migrated and self.server_store:
            try:
                self.server_store.save(self.cfg)
                self.logger.info("[CONFIG] Migrated/initialized server config saved.")
            except Exception as e:
                self.logger.info(f"[CONFIG] Save after server migration failed: {e}")

        self._autosave_guard = True
        self._apply_global_cfg_to_vars(self.global_cfg)
        self._apply_cfg_to_vars(self.cfg)
        self._autosave_guard = False

        self._sync_map_mode()
        self._sync_backup_label_texts()
        self._hook_autosave()
        self._sync_cluster_summary()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_buttons()
        self._sync_auto_update_scheduler()
        self._start_scheduler()
        self._start_player_poll()
        self._start_stats_sampler()
        self.root.after(700, self._poll_show_request)
        global _BEFORE_DIALOG
        _BEFORE_DIALOG = self._before_dialog
        self._reattach_done = threading.Event()
        threading.Thread(target=self._reattach_on_launch, daemon=True, name="reattach").start()
        self._dash_tick()
        # Several profiles: start on the overview. A single server: straight to its settings.
        self.nb.select(self.tab_dash if len(self.global_cfg.servers) > 1 else self.tab_server)
        self._sync_auto_save_hint()
        self.root.after(800, self._auto_start_on_launch)

    # ---------------------------------------------------------------------
    # Profile / config init
    # ---------------------------------------------------------------------
    def _initialize_profiles(self) -> None:
        legacy_path = self.app_base / LEGACY_CONFIG_NAME
        if not self.global_config_path.exists() and legacy_path.exists():
            self._migrate_legacy_config(legacy_path)
            return

        if not self.global_cfg.servers:
            self._create_default_server_profile()
            return

        self._select_active_server_from_global()

    def _migrate_legacy_config(self, legacy_path: Path) -> None:
        try:
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}

        steamcmd_dir = str(raw.get("steamcmd_dir") or DEFAULT_STEAMCMD_DIR) if isinstance(raw, dict) else DEFAULT_STEAMCMD_DIR

        legacy_store = ServerConfigStore(legacy_path, self.app_base / LOCKS_DIR_NAME / "legacy.lock")
        legacy_res = legacy_store.load()
        self._server_warnings.extend(legacy_res.warnings)

        server_cfg = legacy_res.cfg
        server_id = str(uuid.uuid4())
        server_dir = server_cfg.server_dir or DEFAULT_SERVER_DIR
        display_name = server_cfg.server_name or "Server 1"

        server_ref = ServerRef(id=server_id, display_name=display_name, server_dir=server_dir)
        self.global_cfg = GlobalConfig(
            steamcmd_dir=steamcmd_dir,
            last_selected_server_id=server_id,
            servers=[server_ref],
        )
        self._global_migrated = True

        server_cfg.server_dir = server_dir
        server_store = ServerConfigStore(server_config_path(self.app_base, server_id), server_lock_path(self.app_base, server_id))
        try:
            server_store.save_new(server_cfg)
            self._server_migrated = True
        except FileExistsError:
            existing = server_store.load()
            server_cfg = existing.cfg
            self._server_migrated = existing.migrated
            self._server_warnings.extend(existing.warnings)

        self.server_store = server_store
        self.cfg = server_cfg
        self.active_server_id = server_id

    def _create_default_server_profile(self) -> None:
        server_id = str(uuid.uuid4())
        cfg = AppConfig()
        server_ref = ServerRef(id=server_id, display_name="Server 1", server_dir=cfg.server_dir)
        self.global_cfg.servers.append(server_ref)
        self.global_cfg.last_selected_server_id = server_id
        self._global_migrated = True

        self.server_store = ServerConfigStore(server_config_path(self.app_base, server_id), server_lock_path(self.app_base, server_id))
        try:
            self.server_store.save_new(cfg)
            self._server_migrated = True
        except FileExistsError:
            pass
        self.cfg = cfg
        self.active_server_id = server_id

    def _select_active_server_from_global(self) -> None:
        server_ids = [ref.id for ref in self.global_cfg.servers]
        if not server_ids:
            self._create_default_server_profile()
            return

        selected = self.global_cfg.last_selected_server_id
        if selected not in server_ids:
            selected = server_ids[0]
            self.global_cfg.last_selected_server_id = selected
            self._global_migrated = True

        self.active_server_id = selected
        self._load_active_server_config(selected)

    def _load_active_server_config(self, server_id: str) -> None:
        ref = self._server_ref_by_id(server_id)
        if not ref:
            self._create_default_server_profile()
            return

        store = ServerConfigStore(server_config_path(self.app_base, server_id), server_lock_path(self.app_base, server_id))
        res = store.load()
        cfg = res.cfg
        self._server_migrated = res.migrated
        self._server_warnings = res.warnings

        if ref.server_dir:
            cfg.server_dir = ref.server_dir
        elif cfg.server_dir:
            ref.server_dir = cfg.server_dir
            self._global_migrated = True

        self.server_store = store
        self.cfg = cfg
        rt = self._runtimes.get(server_id)
        if rt is not None:
            rt.cfg = cfg

    def _server_ref_by_id(self, server_id: str) -> Optional[ServerRef]:
        for ref in self.global_cfg.servers:
            if ref.id == server_id:
                return ref
        return None

    def _server_dir_in_use(self, server_dir: str, exclude_id: Optional[str] = None) -> bool:
        for ref in self.global_cfg.servers:
            if exclude_id and ref.id == exclude_id:
                continue
            if ref.server_dir.strip().lower() == server_dir.strip().lower():
                return True
        return False

    def _udp_port_free(self, port: int, host: str = "0.0.0.0") -> bool:
        if not (1 <= port <= 65535):
            return False
        try:
            with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as sock:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                sock.bind((host, port))
            return True
        except OSError:
            return False

    def _tcp_port_free(self, port: int, host: str = "0.0.0.0") -> bool:
        if not (1 <= port <= 65535):
            return False
        try:
            with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                sock.bind((host, port))
                sock.listen(1)
            return True
        except OSError:
            return False

    def _read_profile_ports(self, server_id: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        try:
            cfg_path = server_config_path(self.app_base, server_id)
            if not cfg_path.exists():
                return None, None, None

            data = json.loads(cfg_path.read_text(encoding="utf-8"))

            def _get_int(*keys: str) -> Optional[int]:
                for key in keys:
                    value = data.get(key)
                    if isinstance(value, int):
                        return value
                    if isinstance(value, str) and value.isdigit():
                        return int(value)
                return None

            game = _get_int("port", "game_port", "gamePort")
            query = _get_int("query_port", "queryPort")
            rcon = _get_int("rcon_port", "rconPort")
            return game, query, rcon
        except Exception:
            return None, None, None

    def _collect_used_ports(self, ignore_server_id: Optional[str] = None) -> Set[int]:
        used: Set[int] = set()
        for ref in getattr(self.global_cfg, "servers", []):
            sid = getattr(ref, "id", None)
            if not sid or sid == ignore_server_id:
                continue
            game, query, rcon = self._read_profile_ports(sid)
            for port in (game, query, rcon):
                if isinstance(port, int):
                    used.add(port)
        return used

    def auto_assign_ports(
        self,
        cfg: AppConfig,
        *,
        stride: int = 10,
        max_tries: int = 200,
        ignore_server_id: Optional[str] = None,
        extra_used: Optional[Set[int]] = None,
        used: Optional[Set[int]] = None,
    ) -> Tuple[int, int, int]:
        # used: ports of the saved profiles, when the caller already read them.
        used = set(used) if used is not None else self._collect_used_ports(ignore_server_id=ignore_server_id)
        used |= set(extra_used or ())  # ports planned for profiles not saved yet
        base_game = int(getattr(cfg, "port", 0) or DEFAULT_PORT)
        base_query = int(getattr(cfg, "query_port", 0) or DEFAULT_QUERY_PORT)
        base_rcon = int(getattr(cfg, "rcon_port", 0) or DEFAULT_RCON_PORT)

        def _block_ok(game: int, query: int, rcon: int) -> bool:
            if game in used or query in used or rcon in used:
                return False
            if len({game, query, rcon}) != 3:
                return False
            if not self._udp_port_free(game):
                return False
            if not self._udp_port_free(query):
                return False
            if not self._tcp_port_free(rcon):
                return False
            return True

        if _block_ok(base_game, base_query, base_rcon):
            cfg.port = base_game
            cfg.query_port = base_query
            cfg.rcon_port = base_rcon
            return base_game, base_query, base_rcon

        for n in range(1, max_tries + 1):
            game = base_game + n * stride
            query = base_query + n * stride
            rcon = base_rcon + n * stride

            if max(game, query, rcon) > 65535:
                break

            if _block_ok(game, query, rcon):
                cfg.port = game
                cfg.query_port = query
                cfg.rcon_port = rcon
                return game, query, rcon

        raise RuntimeError(
            "No free port block found "
            f"(base={base_game}/{base_query}/{base_rcon}, stride={stride}, tries={max_tries})."
        )

    def _save_active_server_config(self, cfg: AppConfig) -> None:
        if not self.server_store:
            raise RuntimeError("Server config store not initialized.")

        ref = self._server_ref_by_id(self.active_server_id)
        if not ref:
            raise RuntimeError("Active server profile is missing.")

        self.server_store.save(cfg)
        if ref and ref.server_dir != cfg.server_dir:
            ref.server_dir = cfg.server_dir
            self.global_store.save(self.global_cfg)
        # Jobs start from the runtime's copy, so it always matches what was saved.
        rt = self._runtimes.get(self.active_server_id)
        if rt is not None:
            rt.cfg = cfg
        self._cfg_generation += 1

    def _save_global_config(self, cfg: Optional[GlobalConfig] = None) -> None:
        self.global_cfg = cfg or self.global_cfg
        self.global_store.save(self.global_cfg)
        self._cfg_generation += 1  # profiles added or removed

    # ---------------------------------------------------------------------
    # Profile actions
    # ---------------------------------------------------------------------
    def _profile_label(self, ref: ServerRef) -> str:
        label = ref.display_name or ref.id
        rt = self._runtimes.get(ref.id)
        if rt is not None and rt.is_running():
            label += "   (running)"
        elif rt is not None and rt.is_busy():
            label += f"   ({rt.task})"
        return label

    def _refresh_server_selector(self, selected_id: Optional[str] = None) -> None:
        refs = self.global_cfg.servers
        self._server_id_order = [ref.id for ref in refs]
        labels = [self._profile_label(ref) for ref in refs]
        try:
            if list(self.cmb_server_profile.cget("values")) != labels:
                self.cmb_server_profile.configure(values=labels)
        except Exception:
            pass

        if not refs:
            self.var_server_profile.set("")
            return

        target_id = selected_id or self.active_server_id or refs[0].id
        if target_id not in self._server_id_order:
            target_id = refs[0].id

        idx = self._server_id_order.index(target_id)
        try:
            if self.cmb_server_profile.current() != idx or self.var_server_profile.get() != labels[idx]:
                self.cmb_server_profile.current(idx)
        except Exception:
            self.var_server_profile.set(labels[idx])

    def _on_server_profile_selected(self) -> None:
        try:
            idx = self.cmb_server_profile.current()
        except Exception:
            return
        if idx < 0 or idx >= len(getattr(self, "_server_id_order", [])):
            return
        server_id = self._server_id_order[idx]
        self._switch_server_profile(server_id)

    def _switch_server_profile(self, server_id: str) -> None:
        """Show and edit another profile. Servers keep running and their tasks carry on:
        each is bound to its own runtime, not to what the window shows."""
        if server_id == self.active_server_id:
            return

        try:
            self.cfg = self._collect_vars_to_cfg()
            self._save_active_server_config(self.cfg)
            self.global_cfg = self._collect_vars_to_global_cfg()
            self._save_global_config(self.global_cfg)
        except Exception as e:
            messagebox.showerror("Switch profile", str(e))
            self._refresh_server_selector(self.active_server_id)
            return

        self.active_server_id = server_id
        self.global_cfg.last_selected_server_id = server_id
        self._save_global_config(self.global_cfg)

        self._load_active_server_config(server_id)
        self._show_active_profile()

    def _show_active_profile(self) -> None:
        """Fill the window from the profile that just became active."""
        # A visual INI edit still waiting to be written belongs to the profile left behind.
        if getattr(self, "_ini_visual_debounce_id", None):
            with contextlib.suppress(Exception):
                self.root.after_cancel(self._ini_visual_debounce_id)
            self._ini_visual_debounce_id = None
        self._autosave_guard = True
        self._apply_cfg_to_vars(self.cfg)
        self._autosave_guard = False
        self._sync_map_mode()
        self._reset_ini_state()
        # The visual editor shows this profile's staged INIs, not the last profile's values.
        with contextlib.suppress(Exception):
            self._ini_visual_refresh_all()
        self._sync_console_server_filter()
        self._sync_cluster_summary()
        self._sync_auto_save_hint()
        self._refresh_buttons()

    def _add_server_profile(self) -> None:
        display_name = simpledialog.askstring("New Server Profile", "Display name:", initialvalue="New Server")
        if not display_name:
            return

        new_dir = filedialog.askdirectory(
            title="Select ARK server install directory for this NEW profile",
            mustexist=False,
        )
        if not new_dir:
            return

        new_dir = str(Path(new_dir).resolve())

        if self._server_dir_in_use(new_dir):
            ok = messagebox.askyesno(
                "New Server Profile",
                "This server install directory is already used by another profile.\n"
                "Use the same install directory anyway (shared install)?\n\n"
                "Profiles that share an install also share its GameUserSettings.ini and Game.ini, "
                "so maps that should run at the same time are best given their own install folder.",
            )
            if not ok:
                return

        try:
            # The profile on screen is the template; it is saved first so nothing typed is lost.
            self.cfg = self._collect_vars_to_cfg()
            self._save_active_server_config(self.cfg)
            cfg = copy.deepcopy(self.cfg)
        except Exception:
            cfg = AppConfig()

        cfg.server_dir = new_dir
        cfg.server_name = display_name.strip() or cfg.server_name or DEFAULT_SERVER_NAME

        try:
            self.auto_assign_ports(cfg, stride=10, max_tries=300)
        except Exception as e:
            messagebox.showerror("New Server Profile", f"Port auto-detection failed: {e}")
            return

        server_id = str(uuid.uuid4())
        new_store = ServerConfigStore(server_config_path(self.app_base, server_id), server_lock_path(self.app_base, server_id))
        try:
            new_store.save_new(cfg)
        except FileExistsError:
            messagebox.showerror("New Server Profile", "Refusing to overwrite an existing server config.")
            return

        self.global_cfg.servers.append(ServerRef(id=server_id, display_name=display_name.strip(), server_dir=cfg.server_dir))
        self.global_cfg.last_selected_server_id = server_id
        self._save_global_config(self.global_cfg)

        self.active_server_id = server_id
        self.server_store = new_store
        self.cfg = cfg
        self._refresh_server_selector(server_id)
        self._show_active_profile()

    def _rename_server_profile(self) -> None:
        ref = self._server_ref_by_id(self.active_server_id)
        if not ref:
            return
        new_name = simpledialog.askstring("Rename Server Profile", "Display name:", initialvalue=ref.display_name)
        if not new_name:
            return
        ref.display_name = new_name.strip()
        self._save_global_config(self.global_cfg)
        self._refresh_server_selector(self.active_server_id)

    def _remove_server_profile(self) -> None:
        if self._is_server_running():
            messagebox.showwarning("Remove Profile", "Stop the server before removing a profile.")
            return
        if self._is_busy():
            messagebox.showwarning("Remove Profile", "Wait for the current task to finish before removing a profile.")
            return
        if len(self.global_cfg.servers) <= 1:
            messagebox.showwarning("Remove Profile", "At least one server profile must exist.")
            return
        ref = self._server_ref_by_id(self.active_server_id)
        if not ref:
            return
        if not messagebox.askyesno(
            "Remove Profile",
            f"Remove profile '{ref.display_name}'?\nFiles on disk will be kept.",
        ):
            return

        self.global_cfg.servers = [r for r in self.global_cfg.servers if r.id != ref.id]
        self._drop_runtime(ref.id)
        self._prune_cluster_meta()
        self.active_server_id = self.global_cfg.servers[0].id
        self.global_cfg.last_selected_server_id = self.active_server_id
        self._save_global_config(self.global_cfg)

        self._load_active_server_config(self.active_server_id)
        self._refresh_server_selector(self.active_server_id)
        self._show_active_profile()

    def _reset_ini_state(self) -> None:
        self._ini_loaded_target = None
        self._ini_paths_current = None
        self._ini_doc = None
        self._ini_items_index = {}
        try:
            self.lbl_ini_target.configure(text="(not loaded)")
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Console filter (GUI only)
    # ---------------------------------------------------------------------
    def _sync_console_log_filter_state(self) -> None:
        try:
            self._suppress_ga_console = bool(self.var_hide_gameanalytics_console_logs.get())
        except Exception:
            self._suppress_ga_console = True

    def _sync_console_server_filter(self) -> None:
        handler = getattr(self, "_gui_log_handler", None)
        if handler is None:
            return
        only = bool(self.var_console_only_active.get())
        handler.set_server_filter(self.active_server_id if only else None)

    def _drop_console_message(self, message: str) -> bool:
        # Only affects GUI console (Tk handler). File log stays complete.
        if not getattr(self, "_suppress_ga_console", False):
            return False

        s = (message or "").strip()
        if not s:
            return False

        # Hide GameAnalytics spam (all severities)
        if "GameAnalytics" in s:
            return True

        return False

    # ---------------------------------------------------------------------
    # Server runtimes
    # ---------------------------------------------------------------------
    def _display_name(self, server_id: str) -> str:
        ref = self._server_ref_by_id(server_id)
        return (ref.display_name if ref else "") or server_id

    def _runtime(self, server_id: str) -> ServerRuntime:
        """The runtime of a profile, made on first use with the profile as saved on disk.

        Always from disk, never from the form: the scheduler thread can get here while the
        window is between profiles, when self.cfg still belongs to the previous one.
        """
        with self._runtimes_lock:
            rt = self._runtimes.get(server_id)
            if rt is not None:
                return rt
            ref = self._server_ref_by_id(server_id)
            if ref is None:
                raise KeyError(f"No server profile {server_id}")
            cfg = ServerConfigStore(server_config_path(self.app_base, server_id), server_lock_path(self.app_base, server_id)).load().cfg
            if ref.server_dir:
                cfg.server_dir = ref.server_dir
            logger = make_server_logger(
                self.logger,
                server_id,
                lambda sid=server_id: self._display_name(sid),
                server_root(self.app_base, server_id) / LOG_DIR_NAME,
            )
            rt = ServerRuntime(server_id, cfg, logger)
            self._runtimes[server_id] = rt
            return rt

    @property
    def rt(self) -> ServerRuntime:
        """Runtime of the server the window currently shows."""
        return self._runtime(self.active_server_id)

    def _all_runtimes(self) -> List[ServerRuntime]:
        """Runtimes of every profile, in profile order. A profile removed meanwhile is skipped,
        so a pass that started before the removal cannot bring its runtime back."""
        out: List[ServerRuntime] = []
        for ref in list(self.global_cfg.servers):
            try:
                out.append(self._runtime(ref.id))
            except KeyError:
                continue
        return out

    def _running_runtimes(self) -> List[ServerRuntime]:
        with self._runtimes_lock:
            items = list(self._runtimes.values())
        return [rt for rt in items if rt.is_running()]

    def _install_running(self, server_dir: str) -> bool:
        """A server of this manager runs from this install folder, whichever profile it is.

        The INI editor asks this rather than whether the profile on screen runs: profiles
        sharing an install share its live INIs, which must not be merged into another
        profile's staging while a sibling server is using them.
        """
        target = Path(server_dir)
        return any(same_path(Path(rt.live_cfg().server_dir), target) for rt in self._running_runtimes())

    def _drop_runtime(self, server_id: str) -> None:
        with self._runtimes_lock:
            rt = self._runtimes.pop(server_id, None)
        if rt is not None:
            rt.removed = True
            if rt.discord is not None:
                rt.discord.stop_polling()
            rt.close_logger()

    # ---------------------------------------------------------------------
    # RCON FIX helpers
    # ---------------------------------------------------------------------
    def _capture_runtime_rcon(self, rt: ServerRuntime, cfg: AppConfig) -> None:
        # Capture values used to start the *running* server.
        rt.rcon_snapshot = ((cfg.rcon_host or DEFAULT_RCON_HOST).strip(), int(cfg.rcon_port), str(cfg.admin_password or ""))

    def _rcon_params(self, rt: ServerRuntime) -> Tuple[str, int, str]:
        # If server is running, use the runtime snapshot (prevents mismatch when GUI edits values).
        snap = rt.rcon_snapshot
        if rt.is_running() and snap is not None and snap[0] and snap[1]:
            return snap
        cfg = rt.cfg
        return (cfg.rcon_host or DEFAULT_RCON_HOST).strip(), int(cfg.rcon_port), str(cfg.admin_password or "")

    # ---------------------------------------------------------------------
    # Discord helpers
    # ---------------------------------------------------------------------
    def _discord_get_coordinator(self, rt: Optional[ServerRuntime] = None) -> DiscordNotificationCoordinator:
        rt = rt or self.rt
        if rt.discord is None:
            rt.discord = DiscordNotificationCoordinator(
                server_discord_state_path(self.app_base, rt.server_id),
                rt.server_id,
                rt.logger,
                lambda cmd, timeout, _rt=rt: self._rcon_try(cmd, timeout=timeout, rt=_rt),
            )
            rt.discord.label_fn = lambda _sid=rt.server_id: self._discord_source_label(_sid)
        rt.discord.update_config(rt.cfg)
        return rt.discord

    def _discord_update_config(self) -> None:
        rt = self.rt
        if rt.discord is not None:
            rt.discord.update_config(rt.cfg)

    def _cluster_of(self, server_id: str) -> Optional[ClusterView]:
        return next((c for c in self._cluster_views()[0] if server_id in c.members), None)

    def _discord_source_label(self, server_id: str) -> str:
        """'PvE Land · The Island' for a map of a cluster, else the profile's name.

        From what the server runs on (its map and cluster as started), and cheap: it is
        asked on every message, from worker threads too."""
        with self._runtimes_lock:
            rt = self._runtimes.get(server_id)
            others = [o for o in self._runtimes.values() if o is not rt and not o.removed]
        name = self._display_name(server_id)
        if rt is None:
            return name
        ended = rt.ended_run_cfg
        cfg = rt.live_cfg() if ended is None or rt.is_running() else ended
        if effective_cluster_dir(cfg) is None:
            return name
        cid = cfg.cluster_id.strip()
        cluster = str((self.global_cfg.clusters.get(cid) or {}).get("name") or cid)
        label = f"{cluster} · {pretty_map_name(cfg.map_name or DEFAULT_MAP)}"
        # Two copies of one map in a cluster: the profile name tells them apart.
        for o in others:
            oc = o.live_cfg()
            if (effective_cluster_dir(oc) is not None and oc.cluster_id.strip() == cid
                    and (oc.map_name or DEFAULT_MAP).lower() == (cfg.map_name or DEFAULT_MAP).lower()):
                return f"{label} · {name}"
        return label

    def _discord_use_for_cluster(self) -> None:
        """Give every map of this profile's cluster the same Discord setup (webhook,
        notifications, mentions), so the whole cluster posts to one channel."""
        if not self._save_form("Discord"):
            return
        sid = self.active_server_id
        view = self._cluster_of(sid)
        if view is None:
            messagebox.showinfo(
                "Discord", "This profile is not part of a cluster. Turn on Enable Cluster first (Server tab or Cluster...)."
            )
            return
        others = [m for m in view.members if m != sid]
        if not others:
            messagebox.showinfo("Discord", f"{self._display_name(sid)} is the only map of {view.name} so far.")
            return
        src = self.rt.cfg
        if src.discord_enable and not src.discord_webhook_url.strip():
            messagebox.showwarning("Discord", "Enter the webhook URL first (or turn Discord notifications off).")
            return
        if src.discord_webhook_url.strip():
            # A typo here would replace every map's working webhook with one that cannot post.
            try:
                validate_discord_webhook_url(src.discord_webhook_url)
            except ValueError as e:
                messagebox.showwarning("Discord", f"The webhook URL is not valid, so it was not copied:\n\n{e}")
                return
        names = "\n".join(f"- {self._display_name(m)}" for m in others)
        if src.discord_enable:
            what = "Each message names its map, so one channel serves the whole cluster."
        else:
            what = ("Discord notifications are OFF on this profile, so this turns them off for these maps too"
                    + ("" if src.discord_webhook_url.strip() else " (each keeps its own webhook URL)") + ".")
        if not messagebox.askyesno(
            "Use for Every Map",
            f"Give these maps of {view.name} the Discord settings of {self._display_name(sid)} "
            f"(webhook, notifications, mentions)?\n\n{names}\n\n{what}",
        ):
            return
        values = {k: getattr(src, k) for k in DISCORD_SHARED_FIELDS}
        if not src.discord_webhook_url.strip():
            values.pop("discord_webhook_url")  # nothing to share: each map keeps its own
        done: List[str] = []
        failed: List[str] = []
        later: List[str] = []
        for other in others:
            rt = self._runtime(other)
            # Discord on for this very run (told at its start, or polling since a reconnect):
            # it follows the new settings now. Anything else picks them up at its next start.
            live = rt.is_running() and rt.discord is not None and (
                rt.discord.instance_pid == rt.pid() or rt.discord.polling()
            )
            try:
                self._save_profile_fields(other, **values)
            except Exception as e:
                failed.append(f"- {self._display_name(other)}: {e}")
                continue
            done.append(other)
            if rt.discord is not None:
                rt.discord.update_config(rt.cfg)
            if live and rt.is_running():  # it may have stopped since
                rt.discord.start_polling(rt.cfg)
            elif rt.is_running():
                later.append(self._display_name(other))
        msg = f"Discord settings copied to {len(done)} map(s)."
        if later and src.discord_enable:
            msg += f"\n\nAlready running, so fully in effect from their next start: {', '.join(later)}."
        if failed:
            messagebox.showwarning("Discord", msg + "\n\nNot copied:\n" + "\n".join(failed))
        else:
            messagebox.showinfo("Discord", msg)

    def _discord_start_notifications(self, rt: ServerRuntime, pid: int) -> None:
        if not rt.cfg.discord_enable:
            return
        coordinator = self._discord_get_coordinator(rt)
        coordinator.start_instance(rt.cfg, pid)
        coordinator.start_polling(rt.cfg)

    def _discord_request_stop(self, rt: ServerRuntime) -> None:
        # A running notifier is told even if Discord was switched off since the start.
        if not rt.cfg.discord_enable and rt.discord is None:
            return
        self._discord_get_coordinator(rt).request_stop()

    def _discord_stop_notifications(
        self, rt: ServerRuntime, requested: bool, exit_code: Optional[int] = None, pid: Optional[int] = None
    ) -> None:
        try:
            if not rt.cfg.discord_enable:
                if rt.discord is not None:
                    rt.discord.stop_polling()  # its poller must not outlive the server
                return
            coordinator = self._discord_get_coordinator(rt)
            coordinator.notify_stop(rt.cfg, requested=requested, exit_code=exit_code)
            coordinator.stop_polling()
        finally:
            # The run's last word: a requested stop reports twice (its process watcher first,
            # silently, then the stop itself), so only that second call, or a crash, ends it.
            if requested or not rt.stop_requested.is_set():
                rt.ended_run_cfg = None
                if rt.discord is not None:
                    rt.discord.end_instance(pid)

    def _discord_send_test(self) -> None:
        if not self._save_form():
            return
        rt = self.rt

        def job() -> None:
            self._discord_get_coordinator(rt).send_test(rt.cfg)

        self._run_task("Discord Test", job)

    def _discord_open_state_folder(self) -> None:
        server_id = self.active_server_id
        path = server_discord_state_path(self.app_base, server_id)
        open_in_explorer(path, select_file=True)

    # ---------------------------------------------------------------------
    # Vars
    # ---------------------------------------------------------------------
    def _init_vars(self) -> None:
        m = self.root

        self.var_server_profile = tk.StringVar(master=m)
        self.var_steamcmd_dir = tk.StringVar(master=m)
        self.var_server_dir = tk.StringVar(master=m)

        self.var_map_preset = tk.StringVar(master=m)
        self.var_map_custom = tk.StringVar(master=m)
        self.var_server_name = tk.StringVar(master=m)

        self.var_port = tk.StringVar(master=m)
        self.var_query_port = tk.StringVar(master=m)
        self.var_max_players = tk.StringVar(master=m)

        # moved to Advanced tab
        self.var_server_platform_crossplay = tk.BooleanVar(master=m)

        self.var_join_password = tk.StringVar(master=m)
        self.var_admin_password = tk.StringVar(master=m)

        self.var_enable_battleye = tk.BooleanVar(master=m)
        self.var_automanaged_mods = tk.BooleanVar(master=m)
        self.var_validate_on_update = tk.BooleanVar(master=m)

        self.var_enable_rcon = tk.BooleanVar(master=m)
        self.var_rcon_host = tk.StringVar(master=m)
        self.var_rcon_port = tk.StringVar(master=m)

        self.var_discord_enable = tk.BooleanVar(master=m)
        self.var_discord_webhook_url = tk.StringVar(master=m)
        self.var_discord_poll_interval_min = tk.StringVar(master=m)
        self.var_discord_notify_start = tk.BooleanVar(master=m)
        self.var_discord_notify_stop = tk.BooleanVar(master=m)
        self.var_discord_notify_join = tk.BooleanVar(master=m)
        self.var_discord_notify_leave = tk.BooleanVar(master=m)
        self.var_discord_notify_crash = tk.BooleanVar(master=m)
        self.var_discord_include_player_id = tk.BooleanVar(master=m)
        self.var_discord_mention_mode = tk.StringVar(master=m)
        self.var_discord_mention_map_json = tk.StringVar(master=m)

        self.var_backup_on_stop = tk.BooleanVar(master=m)
        self.var_backup_dir = tk.StringVar(master=m)
        self.var_backup_retention = tk.StringVar(master=m)
        self.var_auto_save_enabled = tk.BooleanVar(master=m)
        self.var_auto_save_interval = tk.StringVar(master=m)
        self.var_auto_save_keep = tk.StringVar(master=m)
        self.var_auto_save_hint = tk.StringVar(master=m, value="")

        self.var_auto_update_restart = tk.BooleanVar(master=m)
        self.var_auto_start_on_launch = tk.BooleanVar(master=m)
        self.var_auto_update_time = tk.StringVar(master=m)
        self.var_update_on_startup = tk.BooleanVar(master=m)
        self.var_scheduled_restart_enabled = tk.BooleanVar(master=m)
        self.var_scheduled_restart_time = tk.StringVar(master=m)
        self.var_restart_warnings_enabled = tk.BooleanVar(master=m)
        self.var_restart_warning_minutes = tk.StringVar(master=m)

        # Hide GameAnalytics spam (console only)
        self.var_hide_gameanalytics_console_logs = tk.BooleanVar(master=m)

        self.var_status = tk.StringVar(master=m)

        self.var_cluster_enable = tk.BooleanVar(master=m)
        self.var_cluster_id = tk.StringVar(master=m)
        self.var_cluster_custom_path_enable = tk.BooleanVar(master=m)
        self.var_cluster_dir_override = tk.StringVar(master=m)
        self.var_no_transfer_from_filtering = tk.BooleanVar(master=m)
        self.var_alt_save_directory_name = tk.StringVar(master=m)
        self.var_cluster_summary = tk.StringVar(master=m, value="")
        self.var_console_only_active = tk.BooleanVar(master=m, value=False)

        self.var_dino_mode = tk.StringVar(master=m)

        self.var_log_servergamelog = tk.BooleanVar(master=m)
        self.var_log_servergamelogincludetribelogs = tk.BooleanVar(master=m)
        self.var_log_serverrconoutputtribelogs = tk.BooleanVar(master=m)

        self.var_m_disablecustomcosmetics = tk.BooleanVar(master=m)
        self.var_m_autodestroystructures = tk.BooleanVar(master=m)
        self.var_m_forcerespawndinos = tk.BooleanVar(master=m)
        self.var_m_nowildbabies = tk.BooleanVar(master=m)
        self.var_m_forceallowcaveflyers = tk.BooleanVar(master=m)
        self.var_m_disabledinonetrangescaling = tk.BooleanVar(master=m)
        self.var_m_unstasisdinoobstructioncheck = tk.BooleanVar(master=m)
        self.var_m_alwaystickdedicatedskeletalmeshes = tk.BooleanVar(master=m)
        self.var_m_disablecharactertracker = tk.BooleanVar(master=m)
        self.var_m_useservernetspeedcheck = tk.BooleanVar(master=m)
        self.var_m_stasiskeepcontrollers = tk.BooleanVar(master=m)
        self.var_m_ignoredupeditems = tk.BooleanVar(master=m)

        self.var_custom_start_args = tk.StringVar(master=m)

        self.var_rcon_cmd = tk.StringVar(master=m)
        self.var_rcon_saved = tk.StringVar(master=m)

        self.var_ini_filter = tk.StringVar(master=m)
        self.var_ini_key = tk.StringVar(master=m)
        self.var_ini_section = tk.StringVar(master=m)
        self.var_ini_value = tk.StringVar(master=m)
        self.var_ini_bool = tk.StringVar(master=m)
        self.var_ini_scale = tk.DoubleVar(master=m)

        self.var_ini_add_section = tk.StringVar(master=m)
        self.var_ini_add_key = tk.StringVar(master=m)
        self.var_ini_add_value = tk.StringVar(master=m)

        self._ini_visual_vars: Dict[str, tk.Variable] = {}   # "gus|section|key" -> Variable
        self._ini_visual_debounce_id: Optional[str] = None
        self._ini_visual_loading: bool = False                # guard against feedback loops

    # ---------------------------------------------------------------------
    # Layout
    # ---------------------------------------------------------------------
    def _build_layout(self) -> None:
        theme = THEME_COLORS
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.paned = ttk.PanedWindow(self.root, orient="vertical")
        self.paned.grid(row=0, column=0, sticky="nsew")

        top = ttk.Frame(self.paned, padding=10)
        bottom = ttk.Frame(self.paned, padding=10)
        self.paned.add(top, weight=5)
        self.paned.add(bottom, weight=1)

        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)

        self.nb = ttk.Notebook(top)
        self.nb.grid(row=0, column=0, sticky="nsew")

        header = ttk.Frame(top)
        header.place(in_=self.nb, relx=1.0, x=-8, y=6, anchor="ne")
        ttk.Label(header, text="Status:", foreground=theme["muted"]).grid(row=0, column=0, sticky="e")
        ttk.Label(header, textvariable=self.var_status, anchor="e").grid(row=0, column=1, sticky="e", padx=(6, 0))

        self.tab_dash = ttk.Frame(self.nb, padding=10)
        self.tab_server = ttk.Frame(self.nb, padding=10)
        self.tab_adv = ttk.Frame(self.nb, padding=10)
        self.tab_rcon = ttk.Frame(self.nb, padding=10)
        self.tab_discord = ttk.Frame(self.nb, padding=10)
        self.tab_ini = ttk.Frame(self.nb, padding=10)

        self.nb.add(self.tab_dash, text="Dashboard")
        self.nb.add(self.tab_server, text="Server")
        self.nb.add(self.tab_adv, text="Advanced Start Args")
        self.nb.add(self.tab_rcon, text="RCON")
        self.nb.add(self.tab_discord, text="Discord")
        self.nb.add(self.tab_ini, text="INI Editor")

        self._build_dashboard_tab()

        # ---------------- Server tab ----------------
        self.tab_server.columnconfigure(0, weight=1)
        self.tab_server.columnconfigure(1, weight=1)

        vcmd = (self.root.register(self._validate_digits), "%P")

        lf_profiles = ttk.LabelFrame(self.tab_server, text="Server Profiles", padding=10)
        lf_profiles.grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        lf_profiles.columnconfigure(1, weight=1)

        ttk.Label(lf_profiles, text="Active Server").grid(row=0, column=0, sticky="w")
        self.cmb_server_profile = ttk.Combobox(
            lf_profiles,
            textvariable=self.var_server_profile,
            state="readonly",
            values=[],
        )
        self.cmb_server_profile.grid(row=0, column=1, sticky="ew", padx=6)
        self.cmb_server_profile.bind("<<ComboboxSelected>>", lambda e: self._on_server_profile_selected())

        profile_actions = ttk.Frame(lf_profiles)
        profile_actions.grid(row=0, column=2, sticky="e")
        ttk.Button(profile_actions, text="Add", command=self._add_server_profile).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(profile_actions, text="Rename", command=self._rename_server_profile).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(profile_actions, text="Remove", command=self._remove_server_profile).grid(row=0, column=2)

        lf_paths = ttk.LabelFrame(self.tab_server, text="Paths", padding=10)
        lf_paths.grid(row=1, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        lf_paths.columnconfigure(1, weight=1)
        lf_paths.columnconfigure(4, weight=1)

        ttk.Label(lf_paths, text="SteamCMD Directory").grid(row=0, column=0, sticky="w")
        ttk.Entry(lf_paths, textvariable=self.var_steamcmd_dir).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(lf_paths, text="Browse", command=self._browse_steamcmd).grid(row=0, column=2)

        ttk.Label(lf_paths, text="Server Install Directory").grid(row=0, column=3, sticky="w", padx=(18, 0))
        ttk.Entry(lf_paths, textvariable=self.var_server_dir).grid(row=0, column=4, sticky="ew", padx=6)
        ttk.Button(lf_paths, text="Browse", command=self._browse_server_dir).grid(row=0, column=5)

        lf_server = ttk.LabelFrame(self.tab_server, text="Server Settings", padding=10)
        lf_server.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)
        lf_server.columnconfigure(1, weight=1)

        lf_ops = ttk.LabelFrame(self.tab_server, text="Operations", padding=10)
        lf_ops.grid(row=2, column=1, sticky="nsew", padx=5, pady=5)
        lf_ops.columnconfigure(1, weight=1)

        ttk.Label(lf_server, text="Map Preset").grid(row=0, column=0, sticky="w")
        self.cmb_map = ttk.Combobox(
            lf_server,
            textvariable=self.var_map_preset,
            state="readonly",
            values=[*MAP_PRESETS, MAP_CUSTOM_SENTINEL],
        )
        self.cmb_map.grid(row=0, column=1, sticky="ew", padx=6)
        self.cmb_map.bind("<<ComboboxSelected>>", lambda e: self._sync_map_mode())

        ttk.Label(lf_server, text="Custom Map Name").grid(row=1, column=0, sticky="w")
        self.ent_map_custom = ttk.Entry(lf_server, textvariable=self.var_map_custom)
        self.ent_map_custom.grid(row=1, column=1, sticky="ew", padx=6)

        ttk.Label(lf_server, text="Server Name").grid(row=2, column=0, sticky="w")
        ttk.Entry(lf_server, textvariable=self.var_server_name).grid(row=2, column=1, sticky="ew", padx=6)

        ttk.Label(lf_server, text="Port").grid(row=3, column=0, sticky="w")
        ttk.Entry(lf_server, textvariable=self.var_port, validate="key", validatecommand=vcmd).grid(row=3, column=1, sticky="ew", padx=6)

        ttk.Label(lf_server, text="Query Port").grid(row=4, column=0, sticky="w")
        ttk.Entry(lf_server, textvariable=self.var_query_port, validate="key", validatecommand=vcmd).grid(row=4, column=1, sticky="ew", padx=6)

        ttk.Label(lf_server, text="Max Players").grid(row=5, column=0, sticky="w")
        ttk.Entry(lf_server, textvariable=self.var_max_players, validate="key", validatecommand=vcmd).grid(row=5, column=1, sticky="ew", padx=6)

        ttk.Label(lf_server, text="Join Password").grid(row=6, column=0, sticky="w")
        ttk.Entry(lf_server, textvariable=self.var_join_password).grid(row=6, column=1, sticky="ew", padx=6)

        ttk.Label(lf_server, text="Admin Password (RCON/Admin)").grid(row=7, column=0, sticky="w")
        ttk.Entry(lf_server, textvariable=self.var_admin_password).grid(row=7, column=1, sticky="ew", padx=6)

        ttk.Label(lf_server, text="Mods (comma separated)").grid(row=8, column=0, sticky="nw", pady=(6, 0))
        mods_frame = ttk.Frame(lf_server)
        mods_frame.grid(row=8, column=1, sticky="ew", padx=6, pady=(6, 0))
        mods_frame.columnconfigure(0, weight=1)

        self.txt_mods = tk.Text(
            mods_frame,
            height=4,
            wrap="none",
            background=theme["surface"],
            foreground=theme["text"],
            insertbackground=theme["text"],
            selectbackground=theme["accent_light"],
            highlightthickness=1,
            highlightbackground=theme["border"],
            highlightcolor=theme["accent"],
        )
        self.txt_mods.grid(row=0, column=0, sticky="ew")

        xscroll = ttk.Scrollbar(mods_frame, orient="horizontal", command=self.txt_mods.xview)
        xscroll.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.txt_mods.configure(xscrollcommand=xscroll.set)

        ttk.Label(lf_server, text="Custom Server Arguments (optional)").grid(row=9, column=0, sticky="w", pady=(8, 0))
        self.ent_custom_start_args = ttk.Entry(lf_server, textvariable=self.var_custom_start_args)
        self.ent_custom_start_args.grid(row=9, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Label(lf_server, text="").grid(row=10, column=1, sticky="w", padx=6, pady=(2, 0))
        self._custom_args_tooltip = HoverTooltip(
            self.ent_custom_start_args,
            "Example: -Parameter1 -Parameter2",
        )

        actions = ttk.Frame(lf_server, padding=(0, 10, 0, 0))
        actions.grid(row=11, column=0, columnspan=2, sticky="ew")
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        actions.columnconfigure(2, weight=1)

        self.btn_first_install = ttk.Button(actions, text="First Install", command=self.first_install)
        self.btn_update_validate = ttk.Button(actions, text="Update / Validate", command=self.update_validate)
        self.btn_update_restart = ttk.Button(actions, text="Update / Restart (Safe)", command=self.update_and_restart_safe)
        self.btn_start = ttk.Button(actions, text="Start Server", command=self.start_server)
        self.btn_stop = ttk.Button(actions, text="Stop Server (Safe)", command=self.stop_server_safe)
        self.btn_backup_now = ttk.Button(actions, text="Backup Now", command=self.backup_now)

        self.btn_first_install.grid(row=0, column=0, padx=5, pady=(0, 6), sticky="ew")
        self.btn_stop.grid(row=0, column=1, padx=5, pady=(0, 6), sticky="ew")
        self.btn_start.grid(row=0, column=2, padx=5, pady=(0, 6), sticky="ew")
        self.btn_update_validate.grid(row=1, column=0, padx=5, sticky="ew")
        self.btn_update_restart.grid(row=1, column=1, padx=5, sticky="ew")
        self.btn_backup_now.grid(row=1, column=2, padx=5, sticky="ew")

        update_frame = ttk.LabelFrame(lf_ops, text="Update Options", padding=8)
        update_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        update_frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(update_frame, text="Validate on Update", variable=self.var_validate_on_update).grid(row=0, column=0, sticky="w")
        ttk.Label(
            update_frame,
            text="Runs SteamCMD validation to repair missing or corrupt files (slower updates).",
            wraplength=380,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=(24, 0))
        ttk.Checkbutton(
            update_frame,
            text="Update on startup (before starting server)",
            variable=self.var_update_on_startup,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(
            update_frame,
            text="Checks for updates when launching the app and before starting the server.",
            wraplength=380,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=(24, 0))

        self.chk_auto_update_restart = ttk.Checkbutton(
            update_frame,
            text="Auto Update & Restart",
            variable=self.var_auto_update_restart,
            command=self._sync_auto_update_scheduler
        )
        self.chk_auto_update_restart.grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Label(
            update_frame,
            text="Schedules a daily update window and restarts the server after applying updates.",
            wraplength=380,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=(24, 0))
        ttk.Label(update_frame, text="Schedule Time (HH:MM)").grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(update_frame, textvariable=self.var_auto_update_time).grid(row=6, column=1, sticky="ew", padx=6, pady=(6, 0))
        self.btn_auto_update_test = ttk.Button(update_frame, text="Test", command=self.auto_update_test)
        self.btn_auto_update_test.grid(row=6, column=2, padx=(6, 0), pady=(6, 0))

        ttk.Checkbutton(update_frame, text="Daily restart at (HH:MM)", variable=self.var_scheduled_restart_enabled) \
            .grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(update_frame, textvariable=self.var_scheduled_restart_time).grid(row=7, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Label(
            update_frame,
            text="Restarts the server every day while it is running (safe: save, stop, start). Never starts a stopped one.",
            wraplength=380,
            justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w", padx=(24, 0))
        ttk.Checkbutton(update_frame, text="Warn players first, minutes before", variable=self.var_restart_warnings_enabled) \
            .grid(row=9, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(update_frame, textvariable=self.var_restart_warning_minutes).grid(row=9, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Label(
            update_frame,
            text="In-game chat warnings over RCON before the daily restart, the nightly update and a warned Restart All, "
                 "for example 15,5,1. Stopping the server cancels the countdown.",
            wraplength=380,
            justify="left",
        ).grid(row=10, column=0, columnspan=3, sticky="w", padx=(24, 0))

        backup_frame = ttk.LabelFrame(lf_ops, text="Backup", padding=8)
        backup_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        backup_frame.columnconfigure(1, weight=1)

        self.chk_backup_on_stop = ttk.Checkbutton(
            backup_frame,
            text="Backup on Stop",
            variable=self.var_backup_on_stop,
            command=self._sync_backup_label_texts,
        )
        self.chk_backup_on_stop.grid(row=0, column=0, sticky="w")

        ttk.Label(backup_frame, text="Backup Directory").grid(row=1, column=0, sticky="w")
        ttk.Entry(backup_frame, textvariable=self.var_backup_dir).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(backup_frame, text="Browse", command=self._browse_backup_dir).grid(row=1, column=2, padx=(6, 0))

        ttk.Label(backup_frame, text="Retention (zip count)").grid(row=2, column=0, sticky="w")
        ttk.Entry(backup_frame, textvariable=self.var_backup_retention, validate="key", validatecommand=vcmd).grid(row=2, column=1, sticky="ew", padx=6)

        auto_row = ttk.Frame(backup_frame)
        auto_row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.chk_auto_save = ttk.Checkbutton(
            auto_row,
            text="Auto-save every",
            variable=self.var_auto_save_enabled,
        )
        self.chk_auto_save.grid(row=0, column=0, sticky="w")
        self.ent_auto_save_interval = ttk.Entry(
            auto_row,
            textvariable=self.var_auto_save_interval,
            width=6,
            validate="key",
            validatecommand=vcmd,
        )
        self.ent_auto_save_interval.grid(row=0, column=1, padx=6)
        ttk.Label(auto_row, text="minutes while the server is running").grid(row=0, column=2, sticky="w")
        ttk.Label(auto_row, text="Keep").grid(row=1, column=0, sticky="e", pady=(4, 0))
        self.ent_auto_save_keep = ttk.Entry(
            auto_row,
            textvariable=self.var_auto_save_keep,
            width=6,
            validate="key",
            validatecommand=vcmd,
        )
        self.ent_auto_save_keep.grid(row=1, column=1, padx=6, pady=(4, 0))
        ttk.Label(auto_row, text="auto-saves, in their own slots apart from backups").grid(
            row=1, column=2, sticky="w", pady=(4, 0)
        )
        HoverTooltip(
            self.chk_auto_save,
            "While the server runs: saves the world over RCON, then zips it into the\n"
            "backup folder as ASA_AutoSave_*.zip. Auto-saves rotate in their own slots,\n"
            "so they never push manual or on-stop backups out of retention.\n"
            f"Minimum {AUTO_SAVE_MIN_INTERVAL_MIN} minutes. Not the same as ARK's Auto-Save Interval\n"
            "in the INI Editor, which only writes the world to disk.",
        )
        ttk.Label(
            backup_frame,
            textvariable=self.var_auto_save_hint,
            foreground=THEME_COLORS["muted"],
            wraplength=380,
            justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", padx=(24, 0))

        self.btn_manage_backups = ttk.Button(
            backup_frame,
            text="Manage / Restore Backups...",
            command=self.open_backup_manager,
        )
        self.btn_manage_backups.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        HoverTooltip(
            self.btn_manage_backups,
            "List this server's backup zips, make a new one, and restore a world save\n"
            "or its settings. The current save is zipped first.",
        )

        sp_frame = ttk.LabelFrame(lf_ops, text="Single Player", padding=8)
        sp_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        sp_frame.columnconfigure(0, weight=1)

        self.btn_sp_import = ttk.Button(
            sp_frame,
            text="Import Single Player Save / Settings...",
            command=self.open_singleplayer_import,
        )
        self.btn_sp_import.grid(row=0, column=0, sticky="ew")
        self._sp_import_tooltip = HoverTooltip(
            self.btn_sp_import,
            "Copy a local single player world onto this server, and optionally merge its\n"
            "GameUserSettings.ini / Game.ini into this server INI staging.\n"
            "Server name, passwords, RCON, ports and max players are kept.",
        )

        ttk.Checkbutton(
            lf_ops,
            text="Start server on app launch (last profile)",
            variable=self.var_auto_start_on_launch,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))

        ttk.Checkbutton(
            lf_ops,
            text="Hide GameAnalytics debug spam (console only)",
            variable=self.var_hide_gameanalytics_console_logs,
            command=self._sync_console_log_filter_state,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

        # ---------------- Advanced Start Args tab ----------------
        self.tab_adv.columnconfigure(0, weight=1)
        self.tab_adv.columnconfigure(1, weight=1)

        lf_cluster = ttk.LabelFrame(self.tab_adv, text="Cluster Configuration", padding=10)
        lf_cluster.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        lf_cluster.columnconfigure(1, weight=1)

        self.lbl_cluster_summary = ttk.Label(
            lf_cluster,
            textvariable=self.var_cluster_summary,
            foreground=theme["muted"],
            wraplength=520,
            justify="left",
        )
        self.lbl_cluster_summary.grid(row=0, column=0, columnspan=2, sticky="w")
        self.btn_cluster_setup = ttk.Button(
            lf_cluster,
            text="Set Up / Join Cluster...",
            command=self.open_cluster_setup,
        )
        self.btn_cluster_setup.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        HoverTooltip(
            self.btn_cluster_setup,
            "Turn this server into the first map of a new cluster, or join a cluster another\n"
            "profile runs. Sets the cluster ID and a shared data folder, can bring existing\n"
            "transfer data along, and leaves the world save untouched.",
        )
        ttk.Separator(lf_cluster).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        ttk.Label(lf_cluster, text="Manual settings", foreground=theme["muted"]).grid(row=3, column=0, sticky="w")

        ttk.Checkbutton(
            lf_cluster, text="Enable Cluster", variable=self.var_cluster_enable,
            command=self._on_cluster_enable_toggled,
        ).grid(row=4, column=0, sticky="w", pady=(4, 0))
        ttk.Label(lf_cluster, text="Cluster ID").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(lf_cluster, textvariable=self.var_cluster_id).grid(row=5, column=1, sticky="ew", padx=6, pady=(6, 0))

        ttk.Checkbutton(lf_cluster, text="NoTransferFromFiltering", variable=self.var_no_transfer_from_filtering).grid(row=6, column=0, sticky="w", pady=(8, 0))

        ttk.Checkbutton(lf_cluster, text="Enable Cluster Custom Path", variable=self.var_cluster_custom_path_enable).grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Label(lf_cluster, text="ClusterDirOverride Path").grid(row=8, column=0, sticky="w", pady=(6, 0))
        path_row = ttk.Frame(lf_cluster)
        path_row.grid(row=8, column=1, sticky="ew", padx=6, pady=(6, 0))
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(path_row, textvariable=self.var_cluster_dir_override).grid(row=0, column=0, sticky="ew")
        ttk.Button(path_row, text="Browse", command=self._browse_cluster_dir).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(lf_cluster, text="AltSaveDirectoryName (optional, per instance)").grid(row=9, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(lf_cluster, textvariable=self.var_alt_save_directory_name).grid(row=9, column=1, sticky="ew", padx=6, pady=(8, 0))

        # moved: ServerPlatform
        lf_platform = ttk.LabelFrame(self.tab_adv, text="Platform / Crossplay", padding=10)
        lf_platform.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        ttk.Checkbutton(
            lf_platform,
            text='ServerPlatform: PC+XSX+WINGDK',
            variable=self.var_server_platform_crossplay
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            lf_platform,
            text="Enable BattlEye",
            variable=self.var_enable_battleye
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        lf_dinos = ttk.LabelFrame(self.tab_adv, text="Dinosaur Settings (mutual exclusive)", padding=10)
        lf_dinos.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)

        dino_opts = [
            ("Default", ""),
            ("No Dinos", "NoDinos"),
            ("No Dinos Except Forced Spawn", "NoDinosExceptForcedSpawn"),
            ("No Dinos Except Streaming Spawn", "NoDinosExceptStreamingSpawn"),
            ("No Dinos Except Manual Spawn", "NoDinosExceptManualSpawn"),
            ("No Dinos Except Water Spawn", "NoDinosExceptWaterSpawn"),
        ]
        for i, (label, val) in enumerate(dino_opts):
            ttk.Radiobutton(lf_dinos, text=label, variable=self.var_dino_mode, value=val).grid(row=i, column=0, sticky="w")

        lf_logs = ttk.LabelFrame(self.tab_adv, text="Logs", padding=10)
        lf_logs.grid(row=1, column=1, sticky="nsew", padx=5, pady=5)
        ttk.Checkbutton(lf_logs, text="servergamelog", variable=self.var_log_servergamelog).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(lf_logs, text="servergamelogincludetribelogs", variable=self.var_log_servergamelogincludetribelogs).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(lf_logs, text="ServerRCONOutputTribeLogs", variable=self.var_log_serverrconoutputtribelogs).grid(row=2, column=0, sticky="w")

        lf_runtime = ttk.LabelFrame(self.tab_adv, text="Mods & RCON", padding=10)
        lf_runtime.grid(row=2, column=1, sticky="nsew", padx=5, pady=5)
        lf_runtime.columnconfigure(1, weight=1)

        ttk.Checkbutton(lf_runtime, text="Automanaged Mods", variable=self.var_automanaged_mods).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(lf_runtime, text="Enable RCON", variable=self.var_enable_rcon).grid(row=1, column=0, sticky="w", pady=(6, 0))

        ttk.Label(lf_runtime, text="RCON Host").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(lf_runtime, textvariable=self.var_rcon_host).grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))

        ttk.Label(lf_runtime, text="RCON Port").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(lf_runtime, textvariable=self.var_rcon_port, validate="key", validatecommand=vcmd).grid(row=3, column=1, sticky="ew", padx=6, pady=(6, 0))

        lf_mech = ttk.LabelFrame(self.tab_adv, text="Mechanics / Performance", padding=10)
        lf_mech.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)

        mech_items = [
            ("DisableCustomCosmetics", self.var_m_disablecustomcosmetics),
            ("AutoDestroyStructures", self.var_m_autodestroystructures),
            ("ForceRespawnDinos", self.var_m_forcerespawndinos),
            ("NoWildBabies", self.var_m_nowildbabies),
            ("ForceAllowCaveFlyers", self.var_m_forceallowcaveflyers),
            ("disabledinonetrangescaling", self.var_m_disabledinonetrangescaling),
            ("UnstasisDinoObstructionCheck", self.var_m_unstasisdinoobstructioncheck),
            ("AlwaysTickDedicatedSkeletalMeshes", self.var_m_alwaystickdedicatedskeletalmeshes),
            ("disableCharacterTracker", self.var_m_disablecharactertracker),
            ("UseServerNetSpeedCheck", self.var_m_useservernetspeedcheck),
            ("StasisKeepControllers", self.var_m_stasiskeepcontrollers),
            ("ignoredupeditems", self.var_m_ignoredupeditems),
        ]
        for i, (label, var) in enumerate(mech_items):
            ttk.Checkbutton(lf_mech, text=label, variable=var).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 14), pady=2)

        lf_tools = ttk.LabelFrame(self.tab_adv, text="Folders", padding=10)
        lf_tools.grid(row=3, column=1, sticky="nsew", padx=5, pady=5)
        self.btn_open_app = ttk.Button(lf_tools, text="Open App Folder", command=lambda: open_folder(self.app_base))
        self.btn_open_server_cfg = ttk.Button(lf_tools, text="Open Server Config", command=self.open_server_config_dir)
        self.btn_open_app.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.btn_open_server_cfg.grid(row=1, column=0, sticky="ew")

        # ---------------- RCON tab ----------------
        self.tab_rcon.columnconfigure(0, weight=1)

        lf_rcon = ttk.LabelFrame(self.tab_rcon, text="RCON", padding=10)
        lf_rcon.grid(row=0, column=0, sticky="ew")
        lf_rcon.columnconfigure(1, weight=1)

        ttk.Label(lf_rcon, text="Saved Commands").grid(row=0, column=0, sticky="w")
        self.cmb_rcon_saved = ttk.Combobox(lf_rcon, textvariable=self.var_rcon_saved, state="readonly", values=[])
        self.cmb_rcon_saved.grid(row=0, column=1, sticky="ew", padx=6)
        self.cmb_rcon_saved.bind("<<ComboboxSelected>>", lambda e: self.var_rcon_cmd.set(self.var_rcon_saved.get()))

        ttk.Label(lf_rcon, text="Command").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.ent_rcon_cmd = ttk.Entry(lf_rcon, textvariable=self.var_rcon_cmd)
        self.ent_rcon_cmd.grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 0))
        self.ent_rcon_cmd.bind("<Return>", lambda e: self.send_rcon())

        self.btn_rcon_send = ttk.Button(lf_rcon, text="Send", command=self.send_rcon)
        self.btn_rcon_send.grid(row=1, column=2, pady=(8, 0))

        btn_row = ttk.Frame(lf_rcon)
        btn_row.grid(row=2, column=1, sticky="w", padx=6, pady=(10, 0))
        ttk.Button(btn_row, text="Save Command", command=self._rcon_save_current).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(btn_row, text="Remove Selected", command=self._rcon_remove_selected).grid(row=0, column=1)

        ttk.Label(self.tab_rcon, text="Responses are written to the shared Console.").grid(row=1, column=0, sticky="w", pady=(8, 0))

        # ---------------- Discord tab ----------------
        self.tab_discord.columnconfigure(0, weight=1)

        lf_discord = ttk.LabelFrame(self.tab_discord, text="Discord Webhooks", padding=10)
        lf_discord.grid(row=0, column=0, sticky="ew")
        lf_discord.columnconfigure(1, weight=1)

        ttk.Checkbutton(lf_discord, text="Enable Discord notifications", variable=self.var_discord_enable).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        ttk.Label(lf_discord, text="Webhook URL").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(lf_discord, textvariable=self.var_discord_webhook_url).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))

        ttk.Label(lf_discord, text="Poll interval (minutes)").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(lf_discord, textvariable=self.var_discord_poll_interval_min).grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))

        notify_frame = ttk.LabelFrame(self.tab_discord, text="Notifications", padding=10)
        notify_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        ttk.Checkbutton(notify_frame, text="Server start", variable=self.var_discord_notify_start).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Checkbutton(notify_frame, text="Server stop", variable=self.var_discord_notify_stop).grid(row=0, column=1, sticky="w", padx=(0, 14))
        ttk.Checkbutton(notify_frame, text="Server crash/exit", variable=self.var_discord_notify_crash).grid(row=0, column=2, sticky="w")

        ttk.Checkbutton(notify_frame, text="Player join", variable=self.var_discord_notify_join).grid(row=1, column=0, sticky="w", padx=(0, 14))
        ttk.Checkbutton(notify_frame, text="Player leave", variable=self.var_discord_notify_leave).grid(row=1, column=1, sticky="w", padx=(0, 14))
        ttk.Checkbutton(
            notify_frame,
            text="Include player IDs (advanced)",
            variable=self.var_discord_include_player_id,
        ).grid(row=2, column=0, sticky="w", padx=(0, 14), pady=(6, 0))

        mention_frame = ttk.LabelFrame(self.tab_discord, text="Mentions", padding=10)
        mention_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        mention_frame.columnconfigure(1, weight=1)

        ttk.Label(mention_frame, text="Mention mode").grid(row=0, column=0, sticky="w")
        self.cmb_discord_mention_mode = ttk.Combobox(
            mention_frame,
            textvariable=self.var_discord_mention_mode,
            state="readonly",
            values=["name", "mapping", "none"],
            width=14,
        )
        self.cmb_discord_mention_mode.grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(mention_frame, text="Mention map (JSON or file path)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(mention_frame, textvariable=self.var_discord_mention_map_json).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))

        discord_btns = ttk.Frame(self.tab_discord)
        discord_btns.grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Button(discord_btns, text="Send Test", command=self._discord_send_test).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(discord_btns, text="Open State Folder", command=self._discord_open_state_folder).grid(row=0, column=1)
        ttk.Button(discord_btns, text="Use for Every Map in the Cluster", command=self._discord_use_for_cluster).grid(
            row=0, column=2, padx=(6, 0)
        )
        ttk.Label(
            self.tab_discord,
            text="Every message names its server (cluster and map), so the maps of a cluster can share one channel.",
            foreground=THEME_COLORS["muted"],
        ).grid(row=4, column=0, sticky="w", pady=(6, 0))

        # ---------------- INI Editor tab ----------------
        self.tab_ini.columnconfigure(0, weight=1)
        self.tab_ini.rowconfigure(0, weight=1)

        self.ini_sub_nb = ttk.Notebook(self.tab_ini)
        self.ini_sub_nb.grid(row=0, column=0, sticky="nsew")

        raw_tab = ttk.Frame(self.ini_sub_nb, padding=6)
        self.ini_sub_nb.add(raw_tab, text="Raw Editor")
        raw_tab.columnconfigure(0, weight=2)
        raw_tab.columnconfigure(1, weight=1)
        raw_tab.rowconfigure(2, weight=1)

        ini_top = ttk.Frame(raw_tab)
        ini_top.grid(row=0, column=0, columnspan=2, sticky="ew")
        ini_top.columnconfigure(1, weight=1)

        ttk.Label(ini_top, text="Target").grid(row=0, column=0, sticky="w")
        self.lbl_ini_target = ttk.Label(ini_top, text="(not loaded)")
        self.lbl_ini_target.grid(row=0, column=1, sticky="w", padx=6)

        ttk.Button(ini_top, text="Load GameUserSettings.ini", command=self.load_gameusersettings).grid(row=0, column=2, padx=4)
        ttk.Button(ini_top, text="Load Game.ini", command=self.load_game_ini).grid(row=0, column=3, padx=4)
        ttk.Button(ini_top, text="Open Live + Staging", command=self.open_loaded_ini).grid(row=0, column=4, padx=4)
        ttk.Button(ini_top, text="Resync from Upstream", command=self._ini_resync_from_upstream).grid(row=0, column=5, padx=4)
        ttk.Button(
            ini_top, text="Copy Settings To...",
            command=lambda: (self._save_form("Copy Settings", show_error=False), self.open_copy_settings(self.active_server_id)),
        ).grid(row=0, column=6, padx=4)

        ttk.Label(ini_top, text="Filter").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ent_filter = ttk.Entry(ini_top, textvariable=self.var_ini_filter)
        ent_filter.grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ent_filter.bind("<KeyRelease>", lambda e: self._ini_refresh_tree())

        tree_frame = ttk.Frame(raw_tab)
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=(0, 10))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.tree_ini = ttk.Treeview(tree_frame, columns=("value",), show="tree headings")
        self.tree_ini.heading("#0", text="Section / Key")
        self.tree_ini.heading("value", text="Value")
        self.tree_ini.column("value", width=420, anchor="w")
        self.tree_ini.grid(row=0, column=0, sticky="nsew")

        vs_tree = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_ini.yview)
        self.tree_ini.configure(yscrollcommand=vs_tree.set)
        vs_tree.grid(row=0, column=1, sticky="ns")

        self.tree_ini.bind("<<TreeviewSelect>>", lambda e: self._ini_on_select())

        editor = ttk.LabelFrame(raw_tab, text="Edit Selected Line", padding=10)
        editor.grid(row=2, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)

        ttk.Label(editor, text="Section").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.var_ini_section, state="readonly").grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Label(editor, text="Key").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(editor, textvariable=self.var_ini_key, state="readonly").grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))

        ttk.Label(editor, text="Value").grid(row=2, column=0, sticky="w", pady=(10, 0))

        self.ent_ini_value = ttk.Entry(editor, textvariable=self.var_ini_value)
        self.cmb_ini_bool = ttk.Combobox(editor, textvariable=self.var_ini_bool, state="readonly", values=["True", "False"])

        self.ent_ini_value.grid(row=2, column=1, sticky="ew", padx=6, pady=(10, 0))
        self.cmb_ini_bool.grid_forget()

        self.scale_ini = ttk.Scale(editor, variable=self.var_ini_scale, from_=0.0, to=10.0, command=self._ini_scale_changed)
        self.scale_ini.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self.canvas_ticks = tk.Canvas(editor, height=14, highlightthickness=0)
        self.canvas_ticks.grid(row=4, column=0, columnspan=2, sticky="ew")

        btn_line = ttk.Frame(editor)
        btn_line.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(btn_line, text="Delete Selected Line", command=self._ini_delete_selected).grid(row=0, column=0, sticky="w")

        ttk.Label(
            editor,
            text="Edits are staged and auto-saved.\n"
                 "On Start: staging is copied into the server folder.\n"
                 "On Stop (Safe): baseline is restored into the server folder.",
            wraplength=360
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))

        self.ent_ini_value.bind("<KeyRelease>", lambda e: self._ini_schedule_apply())
        self.cmb_ini_bool.bind("<<ComboboxSelected>>", lambda e: self._ini_schedule_apply())

        add_box = ttk.LabelFrame(raw_tab, text="Add / Append Line", padding=10)
        add_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        add_box.columnconfigure(1, weight=1)
        add_box.columnconfigure(3, weight=1)

        ttk.Label(add_box, text="Section").grid(row=0, column=0, sticky="w")
        ttk.Entry(add_box, textvariable=self.var_ini_add_section).grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Label(add_box, text="Key").grid(row=0, column=2, sticky="w")
        ttk.Entry(add_box, textvariable=self.var_ini_add_key).grid(row=0, column=3, sticky="ew", padx=6)

        ttk.Label(add_box, text="Value").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(add_box, textvariable=self.var_ini_add_value).grid(row=1, column=1, columnspan=3, sticky="ew", padx=6, pady=(6, 0))

        add_btns = ttk.Frame(add_box)
        add_btns.grid(row=2, column=1, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(add_btns, text="Append Line (duplicate keys allowed)", command=self._ini_append_line).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(add_btns, text="Set/Replace First Occurrence", command=self._ini_set_line).grid(row=0, column=1)

        visual_sections = [
            ("Environment", INI_SETTINGS_ENVIRONMENT, None, None),
            ("Dino Settings", INI_SETTINGS_DINOS_FULL, INI_SETTINGS_DINO_STATS, _SPAWN_ENTRY_KEYS),
            ("Player Settings", INI_SETTINGS_PLAYERS, INI_SETTINGS_PLAYER_STATS, None),
            ("Structures", INI_SETTINGS_STRUCTURES, None, None),
            ("Stack Size Overrides", INI_SETTINGS_STACK_SIZE, None, None),
            ("Server Options", INI_SETTINGS_SERVER_OPTIONS, None, None),
            ("Cluster & Transfers", INI_SETTINGS_CLUSTER_TRANSFERS, None, None),
        ]

        for tab_label, settings_list, stats_list, spawn_keys in visual_sections:
            vtab = ttk.Frame(self.ini_sub_nb, padding=4)
            self.ini_sub_nb.add(vtab, text=tab_label)
            vtab.columnconfigure(0, weight=1)
            vtab.rowconfigure(1, weight=1)

            vbar = ttk.Frame(vtab)
            vbar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
            vbar.columnconfigure(2, weight=1)
            ttk.Button(vbar, text="Load GameUserSettings.ini",
                       command=lambda: self._ini_visual_load("gus")).grid(row=0, column=0, padx=(0, 4))
            ttk.Button(vbar, text="Load Game.ini",
                       command=lambda: self._ini_visual_load("game")).grid(row=0, column=1, padx=(0, 4))
            ttk.Label(vbar, text="Load both INI files to enable all settings. Changes auto-save to staging.",
                      foreground=theme["muted"]).grid(row=0, column=2, sticky="w", padx=6)

            canvas = tk.Canvas(vtab, highlightthickness=0, background=theme["bg"])
            vscroll = ttk.Scrollbar(vtab, orient="vertical", command=canvas.yview)
            inner = ttk.Frame(canvas, padding=6)
            inner.columnconfigure(0, weight=1)

            inner.bind("<Configure>", lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.create_window((0, 0), window=inner, anchor="nw", tags="inner_win")
            canvas.bind("<Configure>", lambda e, c=canvas: c.itemconfigure("inner_win", width=e.width))
            canvas.configure(yscrollcommand=vscroll.set)

            canvas.grid(row=1, column=0, sticky="nsew")
            vscroll.grid(row=1, column=1, sticky="ns")

            def _on_mousewheel(event, c=canvas):
                c.yview_scroll(int(-1 * (event.delta / 120)), "units")

            def _bind_wheel(event, c=canvas, handler=_on_mousewheel):
                c.bind_all("<MouseWheel>", handler)

            def _unbind_wheel(event, c=canvas):
                c.unbind_all("<MouseWheel>")

            canvas.bind("<Enter>", _bind_wheel)
            canvas.bind("<Leave>", _unbind_wheel)

            row_i = 0

            row_i = self._ini_visual_build_settings(inner, settings_list, row_i, theme)
            if stats_list:
                row_i = self._ini_visual_build_stats_table(inner, stats_list, row_i, theme)
            if spawn_keys:
                row_i = self._ini_visual_build_spawn_section(inner, spawn_keys, row_i, theme)
            if tab_label == "Stack Size Overrides":
                row_i = self._ini_visual_build_stack_size_table(inner, row_i, theme)
            if tab_label == "Environment":
                row_i = self._ini_visual_build_harvest_table(inner, row_i, theme)

        self.ini_sub_nb.bind("<<NotebookTabChanged>>", lambda e: self._ini_visual_refresh_all())

        # ---------------- Console (bottom) ----------------
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)

        lf_console = ttk.LabelFrame(bottom, text="Console", padding=5)
        lf_console.grid(row=0, column=0, sticky="nsew")
        lf_console.columnconfigure(0, weight=1)
        lf_console.rowconfigure(1, weight=1)

        console_bar = ttk.Frame(lf_console)
        console_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Checkbutton(
            console_bar,
            text="Only show the selected server",
            variable=self.var_console_only_active,
            command=self._sync_console_server_filter,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            console_bar,
            text="Each line starts with the server it came from; every server also logs to its own server.log.",
            foreground=theme["muted"],
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.txt_log = tk.Text(
            lf_console,
            height=12,
            wrap="word",
            state="disabled",
            background=theme["console_bg"],
            foreground=theme["console_fg"],
            insertbackground=theme["console_fg"],
            selectbackground=theme["console_select"],
            highlightthickness=1,
            highlightbackground=theme["border"],
            highlightcolor=theme["accent"],
            font=tkfont.nametofont("TkFixedFont"),
        )
        vs = ttk.Scrollbar(lf_console, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=vs.set)
        self.txt_log.grid(row=1, column=0, sticky="nsew")
        vs.grid(row=1, column=1, sticky="ns")

        self.root.after(0, self._initialize_paned_layout)

    def _initialize_paned_layout(self) -> None:
        try:
            self.root.update_idletasks()
            height = self.root.winfo_height()
            if height > 1:
                self.paned.sashpos(0, int(height * 0.75))
        except Exception:
            pass

    def _apply_text_theme(self) -> None:
        theme = THEME_COLORS
        self.txt_mods.configure(
            background=theme["surface"],
            foreground=theme["text"],
            insertbackground=theme["text"],
            selectbackground=theme["accent_light"],
            highlightthickness=1,
            highlightbackground=theme["border"],
            highlightcolor=theme["accent"],
        )
        self.txt_log.configure(
            background=theme["console_bg"],
            foreground=theme["console_fg"],
            insertbackground=theme["console_fg"],
            selectbackground=theme["console_select"],
            highlightthickness=1,
            highlightbackground=theme["border"],
            highlightcolor=theme["accent"],
            font=tkfont.nametofont("TkFixedFont"),
        )
        self.canvas_ticks.configure(background=theme["bg"])

    # ---------------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------------
    def _validate_digits(self, proposed: str) -> bool:
        return proposed == "" or proposed.isdigit()

    # ---------------------------------------------------------------------
    # Config mapping
    # ---------------------------------------------------------------------
    def _mods_text_set(self, csv: str) -> None:
        self.txt_mods.delete("1.0", "end")
        self.txt_mods.insert("end", (csv or "").strip())

    def _mods_text_get(self) -> str:
        raw = self.txt_mods.get("1.0", "end").strip()
        if not raw:
            return ""
        items: List[str] = []
        for line in raw.replace("\r", "\n").split("\n"):
            for tok in line.split(","):
                t = tok.strip()
                if t:
                    items.append(t)
        return ",".join(items)

    def _apply_global_cfg_to_vars(self, cfg: GlobalConfig) -> None:
        self.var_steamcmd_dir.set(cfg.steamcmd_dir)
        self.var_auto_start_on_launch.set(cfg.start_on_startup)

    def _apply_cfg_to_vars(self, cfg: AppConfig) -> None:
        self.var_server_dir.set(cfg.server_dir)

        if cfg.map_name in MAP_PRESETS:
            self.var_map_preset.set(cfg.map_name)
            self.var_map_custom.set(cfg.map_name)
        else:
            self.var_map_preset.set(MAP_CUSTOM_SENTINEL)
            self.var_map_custom.set(cfg.map_name)

        self.var_server_name.set(cfg.server_name)
        self.var_port.set(str(cfg.port))
        self.var_query_port.set(str(cfg.query_port))
        self.var_max_players.set(str(cfg.max_players))

        self.var_server_platform_crossplay.set(cfg.server_platform.strip() == "PC+XSX+WINGDK")

        self.var_join_password.set(cfg.join_password)
        self.var_admin_password.set(cfg.admin_password)

        self.var_enable_battleye.set(cfg.enable_battleye)
        self.var_automanaged_mods.set(cfg.automanaged_mods)
        self.var_validate_on_update.set(cfg.validate_on_update)

        self._mods_text_set(cfg.mods)

        self.var_enable_rcon.set(cfg.enable_rcon)
        self.var_rcon_host.set(cfg.rcon_host)
        self.var_rcon_port.set(str(cfg.rcon_port))

        self.var_discord_enable.set(cfg.discord_enable)
        self.var_discord_webhook_url.set(cfg.discord_webhook_url)
        poll_min = max(1, int(int(cfg.discord_poll_interval_sec) / 60)) if cfg.discord_poll_interval_sec else 5
        self.var_discord_poll_interval_min.set(str(poll_min))
        self.var_discord_notify_start.set(cfg.discord_notify_start)
        self.var_discord_notify_stop.set(cfg.discord_notify_stop)
        self.var_discord_notify_join.set(cfg.discord_notify_join)
        self.var_discord_notify_leave.set(cfg.discord_notify_leave)
        self.var_discord_notify_crash.set(cfg.discord_notify_crash)
        self.var_discord_include_player_id.set(cfg.discord_include_player_id)
        self.var_discord_mention_mode.set(cfg.discord_mention_mode or "name")
        self.var_discord_mention_map_json.set(cfg.discord_mention_map_json)

        self.var_backup_on_stop.set(cfg.backup_on_stop)
        backup_dir = (cfg.backup_dir or "").strip()
        default_backup_dir = str(self.app_base / BACKUP_DIR_NAME)
        if backup_dir and os.path.normcase(os.path.normpath(backup_dir)) == os.path.normcase(os.path.normpath(default_backup_dir)):
            backup_dir = ""
        self.var_backup_dir.set(backup_dir)
        self.var_backup_retention.set(str(cfg.backup_retention))
        self.var_auto_save_enabled.set(bool(cfg.auto_save_enabled))
        self.var_auto_save_interval.set(str(clamp_auto_save_minutes(cfg.auto_save_interval_minutes)))
        self.var_auto_save_keep.set(str(clamp_auto_save_keep(cfg.auto_save_retention)))

        self.var_auto_update_restart.set(cfg.auto_update_restart)
        self.var_auto_update_time.set(cfg.auto_update_time or DEFAULT_SCHEDULE_TIME)
        self.var_update_on_startup.set(cfg.update_on_startup)
        self.var_scheduled_restart_enabled.set(cfg.scheduled_restart_enabled)
        self.var_scheduled_restart_time.set(cfg.scheduled_restart_time or "06:00")
        self.var_restart_warnings_enabled.set(cfg.restart_warnings_enabled)
        self.var_restart_warning_minutes.set(cfg.restart_warning_minutes)

        self.var_hide_gameanalytics_console_logs.set(bool(cfg.hide_gameanalytics_console_logs))
        self._sync_console_log_filter_state()

        self.var_cluster_enable.set(cfg.cluster_enable)
        self.var_cluster_id.set(cfg.cluster_id)
        self.var_cluster_custom_path_enable.set(cfg.cluster_custom_path_enable)
        self.var_cluster_dir_override.set(cfg.cluster_dir_override)
        self.var_no_transfer_from_filtering.set(cfg.no_transfer_from_filtering)
        self.var_alt_save_directory_name.set(cfg.alt_save_directory_name)

        self.var_dino_mode.set(cfg.dino_mode)

        self.var_log_servergamelog.set(cfg.log_servergamelog)
        self.var_log_servergamelogincludetribelogs.set(cfg.log_servergamelogincludetribelogs)
        self.var_log_serverrconoutputtribelogs.set(cfg.log_serverrconoutputtribelogs)

        self.var_m_disablecustomcosmetics.set(cfg.mech_disablecustomcosmetics)
        self.var_m_autodestroystructures.set(cfg.mech_autodestroystructures)
        self.var_m_forcerespawndinos.set(cfg.mech_forcerespawndinos)
        self.var_m_nowildbabies.set(cfg.mech_nowildbabies)
        self.var_m_forceallowcaveflyers.set(cfg.mech_forceallowcaveflyers)
        self.var_m_disabledinonetrangescaling.set(cfg.mech_disabledinonetrangescaling)
        self.var_m_unstasisdinoobstructioncheck.set(cfg.mech_unstasisdinoobstructioncheck)
        self.var_m_alwaystickdedicatedskeletalmeshes.set(cfg.mech_alwaystickdedicatedskeletalmeshes)
        self.var_m_disablecharactertracker.set(cfg.mech_disablecharactertracker)
        self.var_m_useservernetspeedcheck.set(cfg.mech_useservernetspeedcheck)
        self.var_m_stasiskeepcontrollers.set(cfg.mech_stasiskeepcontrollers)
        self.var_m_ignoredupeditems.set(cfg.mech_ignoredupeditems)

        self.var_custom_start_args.set(cfg.custom_start_args)

        self._rcon_refresh_saved(cfg)

    def _collect_vars_to_cfg(self) -> AppConfig:
        cfg = copy.deepcopy(self.cfg) if isinstance(self.cfg, AppConfig) else AppConfig()
        cfg.schema_version = AppConfig().schema_version
        cfg.server_dir = self.var_server_dir.get().strip() or DEFAULT_SERVER_DIR

        cfg.map_name = self.var_map_custom.get().strip() or DEFAULT_MAP
        cfg.server_name = self.var_server_name.get().strip() or DEFAULT_SERVER_NAME

        cfg.port = safe_int(self.var_port.get(), DEFAULT_PORT) or DEFAULT_PORT
        cfg.query_port = safe_int(self.var_query_port.get(), DEFAULT_QUERY_PORT) or DEFAULT_QUERY_PORT
        cfg.max_players = safe_int(self.var_max_players.get(), DEFAULT_MAX_PLAYERS) or DEFAULT_MAX_PLAYERS

        cfg.server_platform = "PC+XSX+WINGDK" if bool(self.var_server_platform_crossplay.get()) else ""

        # RCON FIX NOTE: keep mapping strict - JoinPassword != AdminPassword
        cfg.join_password = (self.var_join_password.get() or "").strip()
        cfg.admin_password = (self.var_admin_password.get() or "").strip()

        cfg.enable_battleye = bool(self.var_enable_battleye.get())
        cfg.automanaged_mods = bool(self.var_automanaged_mods.get())
        cfg.validate_on_update = bool(self.var_validate_on_update.get())

        cfg.mods = self._mods_text_get()

        cfg.enable_rcon = bool(self.var_enable_rcon.get())
        cfg.rcon_host = self.var_rcon_host.get().strip() or DEFAULT_RCON_HOST
        cfg.rcon_port = safe_int(self.var_rcon_port.get(), DEFAULT_RCON_PORT) or DEFAULT_RCON_PORT

        cfg.discord_enable = bool(self.var_discord_enable.get())
        cfg.discord_webhook_url = self.var_discord_webhook_url.get().strip()
        poll_min = safe_int(self.var_discord_poll_interval_min.get(), 5) or 5
        cfg.discord_poll_interval_sec = max(60, int(poll_min) * 60)
        cfg.discord_notify_start = bool(self.var_discord_notify_start.get())
        cfg.discord_notify_stop = bool(self.var_discord_notify_stop.get())
        cfg.discord_notify_join = bool(self.var_discord_notify_join.get())
        cfg.discord_notify_leave = bool(self.var_discord_notify_leave.get())
        cfg.discord_notify_crash = bool(self.var_discord_notify_crash.get())
        cfg.discord_include_player_id = bool(self.var_discord_include_player_id.get())
        cfg.discord_mention_mode = (self.var_discord_mention_mode.get() or "name").strip().lower()
        cfg.discord_mention_map_json = self.var_discord_mention_map_json.get().strip()
        if cfg.discord_mention_mode not in ("name", "mapping", "none"):
            cfg.discord_mention_mode = "name"

        cfg.backup_on_stop = bool(self.var_backup_on_stop.get())
        cfg.backup_dir = self.var_backup_dir.get().strip()
        cfg.backup_retention = safe_int(self.var_backup_retention.get(), 20) or 20
        cfg.auto_save_enabled = bool(self.var_auto_save_enabled.get())
        cfg.auto_save_interval_minutes = clamp_auto_save_minutes(self.var_auto_save_interval.get())
        cfg.auto_save_retention = clamp_auto_save_keep(self.var_auto_save_keep.get())
        cfg.backup_include_configs = True

        cfg.auto_update_restart = bool(self.var_auto_update_restart.get())
        if bool(self.var_auto_update_restart.get()) and parse_time_hhmm(self.var_auto_update_time.get()) is None:
            raise ValueError("Auto update time must be in HH:MM (24h) format, for example 03:00.")
        cfg.auto_update_time = normalize_time_hhmm(self.var_auto_update_time.get(), DEFAULT_SCHEDULE_TIME)
        cfg.update_on_startup = bool(self.var_update_on_startup.get())
        cfg.scheduled_restart_enabled = bool(self.var_scheduled_restart_enabled.get())
        if bool(self.var_scheduled_restart_enabled.get()) and parse_time_hhmm(self.var_scheduled_restart_time.get()) is None:
            raise ValueError("Daily restart time must be in HH:MM (24h) format, for example 06:00.")
        cfg.scheduled_restart_time = normalize_time_hhmm(self.var_scheduled_restart_time.get(), "06:00")
        cfg.restart_warnings_enabled = bool(self.var_restart_warnings_enabled.get())
        if cfg.restart_warnings_enabled and not parse_warning_minutes(self.var_restart_warning_minutes.get()):
            raise ValueError("Warning minutes must be whole minutes from 1 to 120, for example 15,5,1 (or untick the warnings).")
        cfg.restart_warning_minutes = ",".join(map(str, parse_warning_minutes(self.var_restart_warning_minutes.get()))) or "15,5,1"

        cfg.hide_gameanalytics_console_logs = bool(self.var_hide_gameanalytics_console_logs.get())

        cfg.install_optional_certificates = True

        cfg.cluster_enable = bool(self.var_cluster_enable.get())
        cfg.cluster_id = self.var_cluster_id.get().strip()
        cfg.cluster_custom_path_enable = bool(self.var_cluster_custom_path_enable.get())
        cfg.cluster_dir_override = self.var_cluster_dir_override.get().strip()
        cfg.no_transfer_from_filtering = bool(self.var_no_transfer_from_filtering.get())
        cfg.alt_save_directory_name = self.var_alt_save_directory_name.get().strip()

        cfg.dino_mode = self.var_dino_mode.get().strip()

        cfg.log_servergamelog = bool(self.var_log_servergamelog.get())
        cfg.log_servergamelogincludetribelogs = bool(self.var_log_servergamelogincludetribelogs.get())
        cfg.log_serverrconoutputtribelogs = bool(self.var_log_serverrconoutputtribelogs.get())

        cfg.mech_disablecustomcosmetics = bool(self.var_m_disablecustomcosmetics.get())
        cfg.mech_autodestroystructures = bool(self.var_m_autodestroystructures.get())
        cfg.mech_forcerespawndinos = bool(self.var_m_forcerespawndinos.get())
        cfg.mech_nowildbabies = bool(self.var_m_nowildbabies.get())
        cfg.mech_forceallowcaveflyers = bool(self.var_m_forceallowcaveflyers.get())
        cfg.mech_disabledinonetrangescaling = bool(self.var_m_disabledinonetrangescaling.get())
        cfg.mech_unstasisdinoobstructioncheck = bool(self.var_m_unstasisdinoobstructioncheck.get())
        cfg.mech_alwaystickdedicatedskeletalmeshes = bool(self.var_m_alwaystickdedicatedskeletalmeshes.get())
        cfg.mech_disablecharactertracker = bool(self.var_m_disablecharactertracker.get())
        cfg.mech_useservernetspeedcheck = bool(self.var_m_useservernetspeedcheck.get())
        cfg.mech_stasiskeepcontrollers = bool(self.var_m_stasiskeepcontrollers.get())
        cfg.mech_ignoredupeditems = bool(self.var_m_ignoredupeditems.get())

        cfg.custom_start_args = self.var_custom_start_args.get().strip()

        if not isinstance(cfg.rcon_saved_commands, list) or not cfg.rcon_saved_commands:
            cfg.rcon_saved_commands = ["SaveWorld", "DoExit", "ListPlayers", "DestroyWildDinos"]

        self._validate_cfg(cfg)
        return cfg

    def _collect_vars_to_global_cfg(self) -> GlobalConfig:
        cfg = copy.deepcopy(self.global_cfg) if isinstance(self.global_cfg, GlobalConfig) else GlobalConfig()
        cfg.schema_version = GlobalConfig().schema_version
        cfg.steamcmd_dir = self.var_steamcmd_dir.get().strip() or DEFAULT_STEAMCMD_DIR
        cfg.start_on_startup = bool(self.var_auto_start_on_launch.get())
        self._validate_global_cfg(cfg)
        return cfg

    def _validate_cfg(self, cfg: AppConfig) -> None:
        if not cfg.server_dir.strip():
            raise ValueError("Server directory is required.")
        if not cfg.map_name.strip():
            raise ValueError("Map name is required.")

        for name, p in [("Port", cfg.port), ("QueryPort", cfg.query_port), ("RCONPort", cfg.rcon_port)]:
            if not (1 <= int(p) <= 65535):
                raise ValueError(f"{name} out of range (1..65535).")

        if not (1 <= int(cfg.max_players) <= 255):
            raise ValueError("MaxPlayers out of range (1..255).")

        if cfg.scheduled_restart_enabled and parse_time_hhmm(cfg.scheduled_restart_time) is None:
            raise ValueError("Daily restart time must be in HH:MM (24h) format.")
        if cfg.auto_update_restart and parse_time_hhmm(cfg.auto_update_time) is None:
            raise ValueError("Auto update time must be in HH:MM (24h) format.")

    def _validate_global_cfg(self, cfg: GlobalConfig) -> None:
        if not cfg.steamcmd_dir.strip():
            raise ValueError("SteamCMD directory is required.")

    def _hook_autosave(self) -> None:
        def on_change(*_: Any) -> None:
            if self._autosave_guard:
                return
            self._schedule_autosave()

        vars_to_watch = [
            self.var_steamcmd_dir, self.var_auto_start_on_launch, self.var_server_dir,
            self.var_map_preset, self.var_map_custom, self.var_server_name,
            self.var_port, self.var_query_port, self.var_max_players,
            self.var_server_platform_crossplay,
            self.var_join_password, self.var_admin_password,
            self.var_enable_battleye, self.var_automanaged_mods, self.var_validate_on_update,
            self.var_enable_rcon, self.var_rcon_host, self.var_rcon_port,
            self.var_discord_enable, self.var_discord_webhook_url, self.var_discord_poll_interval_min,
            self.var_discord_notify_start, self.var_discord_notify_stop, self.var_discord_notify_join,
            self.var_discord_notify_leave, self.var_discord_notify_crash, self.var_discord_include_player_id,
            self.var_discord_mention_mode, self.var_discord_mention_map_json,
            self.var_backup_on_stop, self.var_backup_dir, self.var_backup_retention,
            self.var_auto_save_enabled, self.var_auto_save_interval, self.var_auto_save_keep,
            self.var_auto_update_restart, self.var_auto_update_time, self.var_update_on_startup,
            self.var_scheduled_restart_enabled, self.var_scheduled_restart_time,
            self.var_restart_warnings_enabled, self.var_restart_warning_minutes,
            self.var_hide_gameanalytics_console_logs,
            self.var_cluster_enable, self.var_cluster_id, self.var_cluster_custom_path_enable, self.var_cluster_dir_override,
            self.var_no_transfer_from_filtering, self.var_alt_save_directory_name,
            self.var_dino_mode,
            self.var_log_servergamelog, self.var_log_servergamelogincludetribelogs, self.var_log_serverrconoutputtribelogs,
            self.var_m_disablecustomcosmetics, self.var_m_autodestroystructures, self.var_m_forcerespawndinos,
            self.var_m_nowildbabies, self.var_m_forceallowcaveflyers, self.var_m_disabledinonetrangescaling,
            self.var_m_unstasisdinoobstructioncheck, self.var_m_alwaystickdedicatedskeletalmeshes,
            self.var_m_disablecharactertracker, self.var_m_useservernetspeedcheck, self.var_m_stasiskeepcontrollers,
            self.var_m_ignoredupeditems,
            self.var_custom_start_args,
        ]
        for v in vars_to_watch:
            try:
                v.trace_add("write", on_change)
            except Exception:
                pass

        self.txt_mods.bind("<KeyRelease>", lambda e: (None if self._autosave_guard else self._schedule_autosave()))

        try:
            self.var_hide_gameanalytics_console_logs.trace_add("write", lambda *_: self._sync_console_log_filter_state())
        except Exception:
            pass
        try:
            self.var_backup_on_stop.trace_add("write", lambda *_: self._sync_backup_label_texts())
        except Exception:
            pass
        for v in (self.var_auto_save_enabled, self.var_auto_save_interval, self.var_auto_save_keep):
            try:
                v.trace_add("write", lambda *_: self._sync_auto_save_hint())
            except Exception:
                pass
        for v in (
            self.var_server_dir, self.var_cluster_enable, self.var_cluster_id,
            self.var_cluster_custom_path_enable, self.var_cluster_dir_override,
        ):
            try:
                v.trace_add("write", lambda *_: self._sync_cluster_summary())
            except Exception:
                pass

    def _schedule_autosave(self) -> None:
        if self._autosave_after_id:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except Exception:
                pass
        self._autosave_after_id = self.root.after(AUTOSAVE_DEBOUNCE_MS, self._autosave)

    def _autosave(self) -> None:
        self._autosave_after_id = None
        if self._autosave_guard:
            return
        try:
            self.cfg = self._collect_vars_to_cfg()
            self._save_active_server_config(self.cfg)
            self.global_cfg = self._collect_vars_to_global_cfg()
            self.global_store.save(self.global_cfg)
            self._set_status(f"Auto-saved at {self._status_timestamp()}")
        except Exception as e:
            self.logger.info(f"Autosave skipped: {e}")
            self._set_status(f"Config invalid: {e}")

        self._sync_auto_update_scheduler()
        self._discord_update_config()

    # ---------------------------------------------------------------------
    # Busy / State
    # ---------------------------------------------------------------------
    def _is_busy(self) -> bool:
        """A task is running for the server the window shows."""
        return self.rt.is_busy()

    def _any_busy(self) -> bool:
        with self._runtimes_lock:
            items = list(self._runtimes.values())
        return any(rt.is_busy() for rt in items)

    def _is_server_running(self) -> bool:
        """The server the window shows is running."""
        return self.rt.is_running()

    def _refresh_buttons(self) -> None:
        rt = self.rt
        busy = rt.is_busy()
        running = rt.is_running()

        self.btn_first_install.configure(state=("disabled" if busy else "normal"))
        self.btn_update_validate.configure(state=("disabled" if busy else "normal"))
        self.btn_update_restart.configure(state=("disabled" if busy else "normal"))

        self.btn_start.configure(state=("disabled" if busy or running else "normal"))
        self.btn_stop.configure(state=("disabled" if busy or not running else "normal"))
        self.btn_backup_now.configure(state=("disabled" if busy else "normal"))
        self.btn_sp_import.configure(state=("disabled" if busy or running else "normal"))
        self.btn_manage_backups.configure(state=("disabled" if busy else "normal"))
        self.btn_cluster_setup.configure(state=("disabled" if busy else "normal"))

        self.btn_rcon_send.configure(state=("disabled" if busy else "normal"))
        self.btn_auto_update_test.configure(state=("disabled" if busy else "normal"))

        self._refresh_server_selector(self.active_server_id)

        others = [r for r in self._running_runtimes() if r.server_id != rt.server_id]
        # The state line is written only when the state changes, so a message posted
        # meanwhile ("Auto-save failed", "Backup created ...") stays until then.
        key = (rt.server_id, running, rt.task if busy else None, len(others), rt.countdown)
        if key == self._status_key:
            return
        self._status_key = key
        also = f" | {len(others)} other server(s) running" if others else ""
        if running and rt.countdown:
            self._set_status(f"Server: RUNNING - {rt.countdown}{also}")
        elif running:
            self._set_status(f"Server: RUNNING{also}")
        elif busy:
            self._set_status(f"Busy: {rt.task}{also}")
        elif not self.var_status.get().startswith("Config invalid"):
            self._set_status(f"Ready{also}")

    def _ui(self, fn: Callable[[], None]) -> None:
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def _save_form(self, title: str = "Settings", *, show_error: bool = True) -> bool:
        """Save the form into the profile it shows, so a job can start from the saved settings."""
        try:
            self.cfg = self._collect_vars_to_cfg()
            self._save_active_server_config(self.cfg)
            return True
        except Exception as e:
            if show_error:
                messagebox.showerror(title, f"Current configuration is invalid: {e}")
            return False

    def _run_task(
        self,
        name: str,
        fn: Callable[[], None],
        rt: Optional[ServerRuntime] = None,
        *,
        quiet: bool = False,
        then: Optional[Callable[[], None]] = None,
        after: Optional[Callable[[bool], None]] = None,
    ) -> bool:
        """Start fn on a worker thread for one server; False when that server already runs a task.

        Claims the server before returning: the scheduler calls this from its own thread,
        so a check here and a claim on the worker would let two tasks slip in. Tasks of
        different servers run side by side. quiet tasks (unattended ones: timers, crash
        restarts) report failures in the log and status bar, never in a modal dialog that
        would sit there blocking the window until someone comes back. then runs on the
        Tk thread once fn succeeded and the server is free again, so it may start the
        next task for it; after does the same whether fn worked or not, and is told which.
        """
        rt = rt or self.rt
        if not rt.try_claim(name):
            return False
        title = f"{name} - {self._display_name(rt.server_id)}"

        def worker() -> None:
            self._ui(self._refresh_buttons)
            ok = False
            quiet_error: Optional[str] = None
            try:
                rt.logger.info(f"=== {name} started ===")
                fn()
                rt.logger.info(f"=== {name} completed ===")
                ok = True
            except TaskCancelled as e:
                rt.logger.info(f"=== {name} cancelled: {e} ===")
            except Exception as e:
                err = str(e)
                rt.logger.error(f"{name} failed: {err}")
                if quiet:
                    quiet_error = err
                else:
                    self._ui(lambda _err=err: messagebox.showerror(title, _err))
            finally:
                # Until its follow-ups ran (they may start the next task), the work is not done.
                with self._followups_lock:
                    self._followups += 1
                rt.release()
                self._ui(self._refresh_buttons)
                if quiet_error is not None:
                    # After the refresh, which would put "Ready" over it. Finishing in the
                    # background to quit, a failure is what the user must see: say it in a box
                    # (which brings the window back and keeps it open).
                    def report(_err: str = quiet_error) -> None:
                        if self._quit_when_idle:
                            messagebox.showwarning(title, f"{name} failed: {_err}")
                        else:
                            self._set_status_for(rt, f"{name} failed: {_err}")
                    self._ui(report)
                if ok and then is not None:
                    self._ui(then)
                if after is not None:
                    self._ui(lambda: after(ok))  # whatever happened, with whether it worked
                self._ui(self._followups_done)

        try:
            threading.Thread(target=worker, daemon=True, name=f"task-{name}").start()
        except Exception:
            rt.release()
            raise
        return True

    # ---------------------------------------------------------------------
    # UX
    # ---------------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.var_status.set(text)

    def _status_timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _set_backup_status(self, backup_path: Optional[Path], label: str, rt: Optional[ServerRuntime] = None) -> None:
        text = f"{label}: {backup_path.name}" if backup_path else f"{label}: skipped (Saved folder missing)"
        if rt is None:
            self._set_status(text)
        else:
            self._set_status_for(rt, text)

    def _sync_backup_label_texts(self) -> None:
        backup_enabled = bool(self.var_backup_on_stop.get())
        auto_update_text = "Auto Update & Restart"
        if backup_enabled:
            auto_update_text += " (Backup on Stop)"
        update_restart_text = "Update / Restart (Safe)"
        if backup_enabled:
            update_restart_text += " + Backup"
        try:
            self.chk_auto_update_restart.configure(text=auto_update_text)
        except Exception:
            pass
        try:
            self.btn_update_restart.configure(text=update_restart_text)
        except Exception:
            pass

    def _write_banner(self) -> None:
        server_cfg_path = server_config_path(self.app_base, self.active_server_id)
        self.logger.info(
            f"{APP_NAME} started | Admin={is_admin()} | GlobalConfig={self.global_config_path} | ServerConfig={server_cfg_path}"
        )
        self.logger.info(f"Logs: {self.log_dir / LOG_FILE_NAME}")
        self._set_status("Ready")

    def _reattach_on_launch(self) -> None:
        try:
            self._reattach_running_servers()
        except Exception as e:
            self.logger.info(f"Looking for running servers failed: {e}")
        finally:
            self._reattach_done.set()

    def _auto_start_on_launch(self) -> None:
        if not safe_bool(self.global_cfg.start_on_startup):
            return
        # Wait for the scan of running servers, so a server already up is not started twice.
        if not self._reattach_done.is_set():
            self.root.after(500, self._auto_start_on_launch)
            return
        if self._is_busy() or self._is_server_running():
            return
        self.logger.info("Auto-start on launch enabled -> starting active server profile.")
        self.start_server()

    def _sync_map_mode(self) -> None:
        choice = self.var_map_preset.get()
        if choice == MAP_CUSTOM_SENTINEL:
            self.ent_map_custom.configure(state="normal")
        else:
            self.var_map_custom.set(choice)
            self.ent_map_custom.configure(state="disabled")

    # ---------------------------------------------------------------------
    # Browsers
    # ---------------------------------------------------------------------
    def _browse_steamcmd(self) -> None:
        p = filedialog.askdirectory(title="Select SteamCMD directory")
        if p:
            self.var_steamcmd_dir.set(p)

    def _browse_server_dir(self) -> None:
        p = filedialog.askdirectory(title="Select ARK server install directory")
        if p:
            self.var_server_dir.set(p)

    def _browse_backup_dir(self) -> None:
        p = filedialog.askdirectory(title="Select backup target directory")
        if p:
            self.var_backup_dir.set(p)

    def _browse_cluster_dir(self) -> None:
        p = filedialog.askdirectory(title="Select ClusterDirOverride directory")
        if p:
            self.var_cluster_dir_override.set(p)

    def open_server_config_dir(self) -> None:
        d = server_config_dir(Path(self.cfg.server_dir))
        open_folder(d)

    # ---------------------------------------------------------------------
    # Dependencies (First Install)
    # ---------------------------------------------------------------------
    def _ensure_dependencies_first_install(self, logger: logging.Logger) -> None:
        v = vc14_x64_version()
        if v:
            logger.info(f"VC++ v14 x64 OK (Version={v})")
        else:
            logger.info("VC++ v14 x64 missing -> installing...")
            install_vcredist(logger)

        dx_reg = directx_registry_present()
        dx_legacy = has_directx_legacy()

        if dx_reg and dx_legacy:
            logger.info("DirectX legacy OK (registry + legacy DLLs detected)")
        else:
            logger.info(f"DirectX check: registry_present={dx_reg} legacy_dlls_present={dx_legacy} -> installing DirectX websetup...")
            install_directx_web(logger)

            if has_directx_legacy():
                logger.info("DirectX legacy OK (post-install)")
            else:
                logger.info("DirectX legacy DLLs still not detected after install (this can be normal).")

    # ---------------------------------------------------------------------
    # First Install / Update Validate
    # ---------------------------------------------------------------------
    def first_install(self) -> None:
        if not self._save_form("First Install"):
            return
        self.first_install_for(self.rt)

    def first_install_for(
        self,
        rt: ServerRuntime,
        *,
        copy_from: Optional[Path] = None,
        then: Optional[Callable[[], None]] = None,
        after: Optional[Callable[[bool], None]] = None,
    ) -> bool:
        """Install the server. With copy_from (an existing install), its program files are
        copied first and SteamCMD only brings them up to date: much faster than a download."""
        def job() -> None:
            log = rt.logger
            # Installers download to fixed temp paths and SteamCMD may reinstall itself, so
            # first installs of several servers take turns.
            self._take_update_slot(rt)
            try:
                shared = _programdata_base()
                if shared:
                    ensure_programdata_acl(shared, log)

                if not is_admin():
                    log.info("Warning: Not running as Administrator. Installs may fail.")

                self._ensure_dependencies_first_install(log)
                install_asa_certificates(log)

                steamcmd_exe = ensure_steamcmd(self.global_cfg.steamcmd_dir, log)

                target = Path(rt.cfg.server_dir)
                unfinished = (target / COPY_MARKER_NAME).exists()
                source = copy_from
                if source is None and unfinished:
                    source = copy_marker_source(target)  # resume a copy cut short in an earlier session
                copied = unfinished
                if (source is not None and server_installed(source) and not same_path(source, target)
                        and (unfinished or not ark_server_exe(target).exists())):
                    log.info(f"{'Resuming the copy of' if unfinished else 'Copying'} the server files from {source} "
                             "instead of downloading them...")
                    copied = True
                    src_cfg = next((self._runtime(r.id).cfg for r in self.global_cfg.servers
                                    if same_path(Path(self._runtime(r.id).cfg.server_dir), source)), None)
                    keep_out = []
                    if src_cfg is not None:
                        keep_out.append(resolve_backup_root(src_cfg, self.app_base))
                        folder = effective_cluster_dir(src_cfg)
                        if folder is not None:
                            keep_out.append(folder)
                    try:
                        copy_server_install(source, target, log, also_skip=keep_out)
                    except OSError as e:
                        # A locked or unreadable file: SteamCMD downloads what the copy did not get.
                        log.info(f"Copying stopped ({e}); SteamCMD downloads the rest.")

                # A copy carries the source's manifest, so SteamCMD would take it as complete:
                # validate, which only reads the local files, catches anything missing or damaged.
                steamcmd_app_update(
                    logger=log,
                    steamcmd_exe=steamcmd_exe,
                    install_dir=target,
                    app_id=ARK_ASA_APP_ID,
                    validate=copied,
                    lock_root=self.app_base,
                    retries=2,
                )
                if copied:
                    # Validated: only now is the copied install complete.
                    with contextlib.suppress(OSError):
                        (target / COPY_MARKER_NAME).unlink()
            finally:
                self._update_slot.release()

            exe = ark_server_exe(Path(rt.cfg.server_dir))
            log.info(f"Server executable: {exe if exe.exists() else 'NOT FOUND'}")

        return self._run_task("First Install", job, rt, then=then, after=after)

    def update_validate(self) -> None:
        if not self._save_form("Update / Validate"):
            return
        rt = self.rt
        self._run_task("Update / Validate", lambda: self._update_server_install(rt, validate=True), rt)

    def _update_server_install(self, rt: ServerRuntime, *, validate: Optional[bool] = None, check_install: bool = True) -> None:
        cfg = rt.cfg
        if check_install:
            self._ensure_install_free(rt, Path(cfg.server_dir), confirm=False)
        if validate is None:
            validate = bool(cfg.validate_on_update)

        # An unfinished copy is never trusted: it is always validated.
        if (Path(cfg.server_dir) / COPY_MARKER_NAME).exists():
            validate = True
        # One SteamCMD run at a time across every server: the self-check in ensure_steamcmd
        # may reinstall SteamCMD, which must never happen under another server's download.
        self._take_update_slot(rt)
        try:
            steamcmd_exe = ensure_steamcmd(self.global_cfg.steamcmd_dir, rt.logger)
            steamcmd_app_update(
                logger=rt.logger,
                steamcmd_exe=steamcmd_exe,
                install_dir=Path(cfg.server_dir),
                app_id=ARK_ASA_APP_ID,
                validate=bool(validate),
                lock_root=self.app_base,
                retries=2,
            )
            if validate:
                with contextlib.suppress(OSError):
                    (Path(cfg.server_dir) / COPY_MARKER_NAME).unlink()
        finally:
            self._update_slot.release()

    # ---------------------------------------------------------------------
    # Start / Stop / RCON
    # ---------------------------------------------------------------------
    def _stage_required_configs_for_start(self, rt: ServerRuntime, cfg: AppConfig) -> None:
        server_dir = Path(cfg.server_dir)
        ensure_baseline(self.app_base, rt.server_id, server_dir, rt.logger, refresh=True)
        ensure_required_server_settings(cfg, self.app_base, rt.server_id, server_dir, rt.logger)

    def _should_auto_restart(self, rt: ServerRuntime, exit_code: Optional[int]) -> bool:
        if exit_code is None:
            return False
        if exit_code not in AUTO_RESTART_EXIT_CODES:
            return False
        if rt.stop_requested.is_set():
            return False
        if rt.is_busy():
            return False
        return True

    def _auto_restart_server(self, rt: ServerRuntime, exit_code: Optional[int]) -> None:
        if not self._should_auto_restart(rt, exit_code):
            return
        rt.logger.info(f"Server exited with code {exit_code} -> auto-restart scheduled.")

        def job() -> None:
            time.sleep(AUTO_RESTART_DELAY_SEC)
            rt.logger.info("Auto-restarting server...")
            # Unattended, so no shared-install prompt: a sibling on this install was
            # already accepted when this server first started.
            self._start_server_inline(rt, check_install=False)

        # As a task, so the window sees it and closing asks before cutting it off.
        if not self._run_task("Auto-restart", job, rt, quiet=True):
            rt.logger.error("Auto-restart skipped: another task runs for this server; the server stays down.")

    def start_server(self) -> bool:
        if not self._save_form("Start Server"):
            return False
        return self.start_server_for(self.rt)

    def start_server_for(self, rt: ServerRuntime, *, quiet: bool = False) -> bool:
        """Start a server from its saved profile; False if that server already runs a task."""
        def job() -> None:
            if rt.is_running():
                raise RuntimeError("Server already running")
            self._wait_reattach_scan(rt)
            if rt.cfg.update_on_startup:
                rt.logger.info("Update on startup enabled -> updating server install.")
                self._update_server_install(rt, validate=None)  # refuses an install a server runs from
                self._start_server_inline(rt, check_install=False)
            else:
                self._start_server_inline(rt, check_install=True)

        return self._run_task("Start Server", job, rt, quiet=quiet)

    def _server_log_reader(self, rt: ServerRuntime, p: subprocess.Popen) -> None:
        if not p.stdout:
            return
        try:
            for line in p.stdout:
                if rt.stop_log_reader.is_set():
                    break
                rt.logger.info(line.rstrip())
        except Exception as e:
            rt.logger.info(f"Log reader stopped: {e}")
        finally:
            self._on_process_exit(rt, p)

    def _watch_attached(self, rt: ServerRuntime, p: AttachedProcess) -> None:
        """No stdout to follow for a reattached server: wait for it to exit instead."""
        try:
            p.wait()
        except Exception as e:
            # The handle cannot be waited on: look for the process by its install instead,
            # rather than taking a live server for a stopped one.
            rt.logger.info(f"Watching the reattached server by its handle failed ({e}); checking its process instead.")
            exe = _exe_key(str(ark_server_exe(Path(rt.live_cfg().server_dir))))
            while True:
                with rt.proc_lock:
                    if rt.proc is not p:
                        return  # stopped (and cleaned up) by the manager meanwhile
                found = query_server_processes()
                if found is not None and p.pid not in [x.pid for x in found.get(exe, [])]:
                    break  # looked, and it is gone; a failed look proves nothing
                time.sleep(10)
        self._on_process_exit(rt, p)

    def _on_process_exit(self, rt: ServerRuntime, p: Any) -> None:
        # Work left until reported: finishing in the background must not quit before the
        # crash message went out or an auto-restart claimed the server.
        with self._followups_lock:
            self._followups += 1
        try:
            self._handle_process_exit(rt, p)
        finally:
            with self._followups_lock:
                self._followups -= 1

    def _handle_process_exit(self, rt: ServerRuntime, p: Any) -> None:
        code: Optional[int] = None
        try:
            # Output can end a moment before the process does; give it that moment.
            code = p.wait(timeout=5)
            if code is not None:
                rt.logger.info(f"Server exited with code {code}")
        except Exception:
            with contextlib.suppress(Exception):
                code = p.poll()
        with rt.proc_lock:
            mine = rt.proc is p
            if mine:
                rt.proc = None
        if mine:
            # RCON FIX: server is gone -> runtime snapshot is no longer valid
            rt.rcon_snapshot = None
            rt.started_at = None
            rt.end_run()
            clear_run_state(self.app_base, rt.server_id)
            self._auto_restart_server(rt, code)
            self._discord_stop_notifications(rt, requested=False, exit_code=code, pid=getattr(p, "pid", None))
        self._ui(self._refresh_buttons)

    def _reattach_running_servers(self) -> None:
        """Pick up servers left running when the manager last closed (or started elsewhere).

        A server this manager launched left a run state behind: it is matched by its PID
        and gets back exactly the ports and RCON login it runs with. Others are matched by
        the game port in their command line (or, one profile with one process on an
        install, by being the only candidates), with the RCON login the install's live INI
        holds, and are treated as not started by the manager.
        """
        everything = query_server_processes()
        if everything is None:
            self.logger.info("Looking for running servers failed; none were reattached.")
            return
        claimed: Set[int] = set()
        attached: List[str] = []

        def attach(rt: ServerRuntime, proc: FoundProcess, run: AppConfig, ours: bool) -> bool:
            try:
                handle = AttachedProcess(proc.pid, expected_exe=ark_server_exe(Path(run.server_dir)))
            except Exception as e:
                rt.logger.info(f"Found its server running (PID {proc.pid}) but cannot attach: {e}")
                return False
            with rt.proc_lock:
                if rt.proc is not None:
                    return False
                rt.proc = handle
                rt.run_cfg = run
                rt.ended_run_cfg = None
                rt.started_at = proc.started or datetime.now()
                rt.launched_by_us = ours
            self._capture_runtime_rcon(rt, run)
            rt.stop_requested.clear()
            claimed.add(proc.pid)
            threading.Thread(target=self._watch_attached, args=(rt, handle), daemon=True,
                             name=f"attached-{rt.server_id}").start()
            if rt.cfg.discord_enable:
                with contextlib.suppress(Exception):
                    self._discord_get_coordinator(rt).start_polling(rt.cfg)
            how = "as started by this manager" if ours else "started outside this manager or before it kept a record"
            rt.logger.info(f"Reattached to the running server (PID {proc.pid}, {how}); its console output is not available.")
            attached.append(self._display_name(rt.server_id))
            return True

        runtimes = [rt for rt in self._all_runtimes() if not rt.is_running()]
        all_found = {proc.pid: (key, proc) for key, procs in everything.items() for proc in procs}
        # 1) Our own launches, by the PID they recorded. Asked of Windows directly, not of the
        # WMI list, which can miss a server running with more rights than this manager.
        for rt in runtimes:
            state = read_run_state(self.app_base, rt.server_id)
            if state is None:
                continue
            pid = int(state["pid"])
            run = cfg_from_run_state(rt.cfg, state)
            image = process_image_path(pid)
            if image and _exe_key(image) == _exe_key(str(ark_server_exe(Path(run.server_dir)))):
                hit = all_found.get(pid)
                proc = hit[1] if hit is not None else FoundProcess(pid=pid, command_line="")
                attach(rt, proc, run, ours=True)
            elif not image and pid_exists(pid):
                # Running, but not even its path can be read: leave the record for a later look.
                rt.logger.info(f"Its server (PID {pid}) still runs but cannot be examined; it was not reattached.")
            else:
                clear_run_state(self.app_base, rt.server_id)  # that process is gone (or is not ARK)
        # 2) Everything else, by install folder and game port.
        by_dir: Dict[str, List[ServerRuntime]] = {}
        for rt in runtimes:
            if rt.is_running() or not ark_server_exe(Path(rt.cfg.server_dir)).exists():
                continue
            by_dir.setdefault(_exe_key(str(ark_server_exe(Path(rt.cfg.server_dir)))), []).append(rt)
        for key, candidates in by_dir.items():
            procs = [x for x in everything.get(key, []) if x.pid not in claimed]
            for proc in procs:
                port = command_line_port(proc.command_line)
                free = [rt for rt in candidates if not rt.is_running()]
                match = [rt for rt in free if port is not None and int(rt.cfg.port) == port]
                if not match and len(candidates) == 1 and len(procs) == 1 and free:
                    match = free  # one profile, one process on this install: it is that one
                    if port is not None:
                        free[0].logger.info(f"Its server runs on port {port}, not the saved {free[0].cfg.port}; attaching anyway.")
                if len(match) != 1:
                    if match or port is not None:
                        self.logger.info(f"A server runs from {candidates[0].cfg.server_dir} (PID {proc.pid}, port {port}) "
                                         "that no profile could be matched to; it was left alone.")
                    continue
                rt = match[0]
                run = copy.deepcopy(rt.cfg)
                if port is not None:
                    run.port = port
                live_port, live_password, live_enabled = live_rcon_settings(Path(run.server_dir))
                if live_port is not None:
                    run.rcon_port = live_port
                if live_password is not None:
                    run.admin_password = live_password
                if live_enabled is not None:
                    run.enable_rcon = live_enabled
                attach(rt, proc, run, ours=False)
        if attached:
            self._ui(lambda: self._set_status(f"Reattached to running server(s): {', '.join(attached)}"))
        self._ui(self._refresh_buttons)

    def _rcon_try(self, cmd: str, *, timeout: float = 4.0, rt: Optional[ServerRuntime] = None) -> str:
        rt = rt or self.rt
        # What the running server listens on, not what the form says since it started.
        if not rt.live_cfg().enable_rcon:
            raise RCONError("RCON is disabled in this profile.")

        host, port, password = self._rcon_params(rt)

        if not password:
            raise RCONError("Admin password is empty.")

        try:
            with self._rcon_factory(host, port, password, float(timeout)) as rcon:
                return str(rcon.command(cmd) or "").strip()
        except RCONAuthError as e:
            raise RCONAuthError(
                f"RCON auth failed ({host}:{port}). Check AdminPassword and server RCON settings."
            ) from e
        except RCONConnectionError as e:
            raise RCONConnectionError(
                f"RCON connection failed ({host}:{port}). Is the server running and port reachable?"
            ) from e
        except Exception as e:
            raise RCONError(f"RCON command failed: {type(e).__name__}: {e}") from e

    def send_rcon(self) -> None:
        cmd = self.var_rcon_cmd.get().strip()
        if not cmd:
            return
        if not self._save_form("RCON"):
            return
        rt = self.rt
        server_id = rt.server_id

        def job() -> None:
            rt.logger.info(f"RCON> {cmd}")
            out = self._rcon_try(cmd, timeout=5.0, rt=rt)
            rt.logger.info(out if out else "(no response)")
            self._ui(lambda: self._after_rcon_sent(server_id, cmd))

        self._run_task("RCON", job, rt)

    def _after_rcon_sent(self, server_id: str, cmd: str) -> None:
        if server_id != self.active_server_id:
            return  # the window moved on to another server; leave its form alone
        if cmd not in self.cfg.rcon_saved_commands:
            self.cfg.rcon_saved_commands.append(cmd)
            try:
                self._save_active_server_config(self.cfg)
            except Exception as e:
                self.logger.info(f"Saving the RCON command list failed: {e}")
            self._rcon_refresh_saved(self.cfg)
        if self.var_rcon_cmd.get().strip() == cmd:
            self.var_rcon_cmd.set("")

    def _rcon_refresh_saved(self, cfg: AppConfig) -> None:
        vals = cfg.rcon_saved_commands[:] if isinstance(cfg.rcon_saved_commands, list) else []
        self.cmb_rcon_saved.configure(values=vals)

    def _rcon_save_current(self) -> None:
        cmd = self.var_rcon_cmd.get().strip()
        if not cmd:
            return
        if cmd not in self.cfg.rcon_saved_commands:
            self.cfg.rcon_saved_commands.append(cmd)
            self._save_active_server_config(self.cfg)
            self._rcon_refresh_saved(self.cfg)
            self.var_rcon_saved.set(cmd)

    def _rcon_remove_selected(self) -> None:
        sel = self.var_rcon_saved.get().strip()
        if not sel:
            return
        try:
            self.cfg.rcon_saved_commands = [c for c in self.cfg.rcon_saved_commands if c != sel]
            self._save_active_server_config(self.cfg)
            self._rcon_refresh_saved(self.cfg)
            self.var_rcon_saved.set("")
        except Exception:
            pass

    def _stop_server_impl(self, rt: ServerRuntime) -> None:
        """Save, stop and clean up after one server. Runs inside one of its tasks."""
        with rt.proc_lock:
            p = rt.proc
        # The install, INIs and backup settings it was started with: edits made in the
        # form while it ran must not redirect its shutdown to another folder.
        cfg = rt.live_cfg()
        log = rt.logger

        if not p or p.poll() is not None:
            log.info("Server not running.")
            # RCON FIX: ensure snapshot cleared if process already ended
            rt.rcon_snapshot = None
            return

        if cfg.enable_rcon:
            try:
                log.info("RCON: SaveWorld")
                _ = self._rcon_try("SaveWorld", timeout=30.0, rt=rt)
            except Exception as e:
                log.info(f"RCON SaveWorld failed: {e}")

            try:
                log.info("RCON: DoExit")
                _ = self._rcon_try("DoExit", timeout=6.0, rt=rt)
            except Exception as e:
                log.info(f"RCON DoExit failed -> terminate process: {e}")

        # Ends as soon as the server has exited; the time is only there for big worlds on a
        # busy disk (several maps stopping at once), so it is not cut off mid-save.
        t_end = time.time() + STOP_WAIT_SEC
        while time.time() < t_end and p.poll() is None:
            time.sleep(0.5)

        if p.poll() is None:
            log.info("Terminating server process...")
            rt.stop_log_reader.set()
            p.terminate()
            try:
                p.wait(timeout=12)
            except subprocess.TimeoutExpired:
                log.info("Killing server process...")
                p.kill()
                p.wait(timeout=5)

        with rt.proc_lock:
            if rt.proc is p:
                rt.proc = None

        # RCON FIX: server stopped -> snapshot invalid
        rt.rcon_snapshot = None
        rt.started_at = None
        rt.end_run()
        ours = rt.launched_by_us
        rt.launched_by_us = False
        clear_run_state(self.app_base, rt.server_id)

        # ARK writes config files during shutdown. Treat the live INIs as runtime-generated until
        # the manager explicitly restores or applies a user-authored baseline/staging copy.
        if ours:
            for ini_name in (GAMEUSERSETTINGS_REL, GAME_INI_REL):
                try:
                    write_server_ini_runtime_state_for_stage(Path(cfg.server_dir) / ini_name, live_is_runtime=True)
                except Exception:
                    pass

        log.info("Server stopped.")

        if cfg.backup_on_stop:
            backup_path = backup_server(cfg, self.app_base, log, rt.server_id)
            self._ui(lambda: self._set_backup_status(backup_path, label="Backup on stop", rt=rt))

        if ours:
            restore_baseline_to_server(self.app_base, rt.server_id, Path(cfg.server_dir), log)
        else:
            # Started outside this manager: its live INIs may hold edits the baseline never saw.
            log.info("Left the live INIs as they are: this server was not started by the manager.")
        self._ui(self._refresh_buttons)

    def _safe_stop(self, rt: ServerRuntime) -> None:
        """Stop with the stop marked as requested, so it is not taken for a crash."""
        if not rt.is_running():
            self._stop_server_impl(rt)
            return
        rt.stop_requested.set()
        pid = rt.pid()
        self._discord_request_stop(rt)
        try:
            self._stop_server_impl(rt)
        finally:
            # Also when something after the shutdown failed (a backup on stop): the server is
            # down, so say so. Still running means the stop itself failed: nothing to report.
            if not rt.is_running():
                self._discord_stop_notifications(rt, requested=True, pid=pid)

    def stop_server_safe(self) -> None:
        # Stopping needs nothing from the form, so an invalid field must not keep a server up.
        if not self._save_form("Stop Server", show_error=False):
            self.rt.logger.info("Stop Server: the form has an invalid value; stopping with the saved settings.")
        self.stop_server_for(self.rt)

    def stop_server_for(self, rt: ServerRuntime) -> bool:
        def job() -> None:
            # Cancelled once the stop holds the server: a refused click must not drop a
            # planned restart players were told about.
            if self._cancel_countdown(rt, kinds=("restart", "update")):
                rt.logger.info("Stop: the planned restart was cancelled.")
            self._detach_from_group_countdown(rt)
            self._safe_stop(rt)

        return self._run_task("Stop Server", job, rt)

    def update_and_restart_safe(self) -> None:
        if not self._save_form("Update & Restart"):
            return
        self.update_and_restart_for(self.rt)

    def _take_update_slot(self, rt: ServerRuntime) -> None:
        """Wait for this manager's SteamCMD turn.

        A long queue (installing a whole cluster) is fine: it only gives up when one run
        holds the slot for UPDATE_SLOT_WAIT_SEC without the slot changing hands.
        """
        if not self._update_slot.acquire(blocking=False):
            rt.logger.info("Waiting for another server's SteamCMD run to finish...")
            while True:
                turns = self._update_slot_turns
                if self._update_slot.acquire(timeout=UPDATE_SLOT_WAIT_SEC):
                    break
                if self._update_slot_turns == turns:
                    raise RuntimeError(
                        f"Another server's SteamCMD run has been going for over {UPDATE_SLOT_WAIT_SEC // 60} minutes; "
                        "this one was left as it is. Try again later."
                    )
        self._update_slot_turns += 1

    def update_and_restart_for(
        self, rt: ServerRuntime, *, quiet: bool = False, from_countdown: bool = False, only_if_running: bool = False
    ) -> bool:
        def job() -> None:
            if only_if_running and (not rt.is_running() or rt.stop_requested.is_set()):
                raise TaskCancelled("The server is no longer running; the planned update is skipped.")
            if not from_countdown and self._cancel_countdown(rt, kinds=("restart", "update")):
                rt.logger.info("Update & Restart: the planned restart was cancelled; this replaces it.")
            # Maps updating at the same time take turns here, while still running: stopping
            # first and then queueing for SteamCMD would keep every waiting map down.
            self._take_update_slot(rt)
            try:
                if only_if_running and (not rt.is_running() or rt.stop_requested.is_set()):
                    raise TaskCancelled("The server was stopped while waiting its turn; the planned update is skipped.")
                # Before stopping our own server: if another server holds the install, the update
                # cannot run, and stopping first would only leave this map down until someone looks.
                self._ensure_install_free(rt, Path(rt.cfg.server_dir), confirm=False)

                was_running = rt.is_running()
                try:
                    if was_running:
                        rt.logger.info("Server running -> performing safe stop before update.")
                        self._safe_stop(rt)
                    self._update_server_install(rt, validate=None, check_install=False)
                except Exception as e:
                    # A failed stop or update must not leave a map down: bring it back on what
                    # is installed, and report the original failure, not a follow-up one.
                    if was_running and not rt.is_running():
                        rt.logger.error(f"Update & Restart failed ({e}); starting the server again on the installed version.")
                        try:
                            self._start_server_inline(rt, check_install=False)
                        except Exception as e2:
                            rt.logger.error(f"Starting the server again failed too: {e2}")
                    raise
            finally:
                self._update_slot.release()

            rt.logger.info("Restarting server after update...")
            self._start_server_inline(rt, check_install=False)

        return self._run_task("Update & Restart", job, rt, quiet=quiet)

    def restart_server_safe(self) -> bool:
        """Save, stop and start again with the current profile settings. False if another task runs."""
        if not self._save_form("Restart Server"):
            return False
        return self.restart_server_for(self.rt)

    def restart_server_for(
        self, rt: ServerRuntime, *, quiet: bool = False, from_countdown: bool = False, only_if_running: bool = False
    ) -> bool:
        def job() -> None:
            if only_if_running and (not rt.is_running() or rt.stop_requested.is_set()):
                raise TaskCancelled("The server is no longer running; the planned restart is skipped.")
            # A restart made by hand replaces a planned restart that is counting down, which
            # would otherwise restart the server a second time. A planned update stays: it
            # still has to update.
            if not from_countdown and self._cancel_countdown(rt, kinds=("restart",)):
                rt.logger.info("Restart: the planned restart was cancelled; this restart replaces it.")
            # A running server restarts on the install it already shares (accepted when it
            # first started); only a stopped one is asked about siblings, before it starts.
            if not rt.is_running():
                self._ensure_install_free(rt, Path(rt.cfg.server_dir), confirm=True)
            if rt.is_running():
                rt.logger.info("Restart: safe stop first.")
                self._safe_stop(rt)
            self._start_server_inline(rt, check_install=False)

        return self._run_task("Restart Server", job, rt, quiet=quiet)

    def auto_update_test(self) -> None:
        if self._is_busy():
            messagebox.showwarning("Auto Update Test", "This server is busy. Try again when ready.")
            return
        self.rt.logger.info("Auto update test triggered -> Update / Restart (Safe).")
        self.update_and_restart_safe()

    def _wait_reattach_scan(self, rt: ServerRuntime) -> None:
        """Right after launch the scan for servers left running is still going: a start must
        not race it and launch a second process for a server that is already up."""
        done = getattr(self, "_reattach_done", None)
        if done is not None and not done.is_set():
            rt.logger.info("Waiting for the check for servers that are already running...")
            done.wait(timeout=90)
        if rt.is_running():
            raise TaskCancelled("The server is already running (it was found running and reattached).")

    def _start_server_inline(self, rt: ServerRuntime, *, check_install: bool = True) -> None:
        self._wait_reattach_scan(rt)
        # Read once: the form may be saved while this runs, and the staged INIs, the RCON
        # snapshot and the command line must all come from the same settings.
        cfg = copy.deepcopy(rt.cfg)
        server_dir = Path(cfg.server_dir)
        exe = ark_server_exe(server_dir)
        if not exe.exists():
            raise FileNotFoundError(f"Server EXE not found: {exe}")
        if (server_dir / COPY_MARKER_NAME).exists():
            raise RuntimeError("The install of this server is unfinished (a copy was cut short). Run First Install to finish it.")

        if cfg.enable_rcon and not (cfg.admin_password or "").strip():
            raise RuntimeError("Admin/RCON password is empty. Set 'Admin Password (RCON/Admin)' before starting.")

        self._reserve_ports(rt, cfg)
        try:
            self._launch_server(rt, cfg, server_dir, exe, check_install=check_install)
        finally:
            with self._runtimes_lock:
                self._starting_ports.pop(rt.server_id, None)

    def _launch_server(self, rt: ServerRuntime, cfg: AppConfig, server_dir: Path, exe: Path, *, check_install: bool) -> None:
        if check_install:
            self._ensure_install_free(rt, server_dir, confirm=True)
        self._prepare_cluster_for_start(cfg, rt.logger)
        self._stage_required_configs_for_start(rt, cfg)
        apply_staging_to_server(self.app_base, rt.server_id, server_dir, rt.logger)

        # RCON FIX: capture runtime values for this running instance
        self._capture_runtime_rcon(rt, cfg)
        rt.stop_requested.clear()

        cmd = build_server_command(cfg)
        rt.logger.info("Starting server...")
        rt.logger.info(" ".join(cmd))

        with rt.proc_lock:
            if rt.proc is not None and rt.proc.poll() is None:
                raise RuntimeError("Server already running")
            rt.stop_log_reader.clear()
            p = subprocess.Popen(
                cmd,
                cwd=str(exe.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            rt.proc = p
            rt.run_cfg = cfg
            rt.ended_run_cfg = None
            rt.started_at = datetime.now()
            rt.launched_by_us = True
        try:
            write_run_state(self.app_base, rt.server_id, p.pid, cfg)
        except Exception as e:
            rt.logger.info(f"Could not record the run state (reattach after a restart of the manager may guess): {e}")

        threading.Thread(target=self._server_log_reader, args=(rt, p), daemon=True, name=f"log-{rt.server_id}").start()
        self._discord_start_notifications(rt, p.pid)
        self._ui(self._refresh_buttons)

    @staticmethod
    def _ports_of(cfg: AppConfig) -> Set[int]:
        ports = {int(cfg.port), int(cfg.query_port)}
        if cfg.enable_rcon:
            ports.add(int(cfg.rcon_port))
        return ports

    def _ensure_ports_free(self, rt: ServerRuntime, cfg: Optional[AppConfig] = None) -> None:
        """Refuse to start on a port another server of this manager runs, or is starting, on."""
        mine = self._ports_of(cfg or rt.cfg)
        with self._runtimes_lock:
            starting = {sid: set(p) for sid, p in self._starting_ports.items() if sid != rt.server_id}
        for other in self._running_runtimes():
            if other is not rt:
                starting.setdefault(other.server_id, set()).update(self._ports_of(other.live_cfg()))
        for sid, theirs in starting.items():
            clash = sorted(mine & theirs)
            if clash:
                raise RuntimeError(
                    f"Port(s) {', '.join(map(str, clash))} are in use by {self._display_name(sid)}, "
                    "which is running or starting. Give this profile its own ports first."
                )

    def _reserve_ports(self, rt: ServerRuntime, cfg: AppConfig) -> None:
        """Check and claim this start's ports in one step, so two servers starting at the
        same moment cannot both pass the check and then fight over a port."""
        with self._runtimes_lock:
            self._ensure_ports_free(rt, cfg)
            self._starting_ports[rt.server_id] = self._ports_of(cfg)

    def backup_now(self, on_done: Optional[Callable[[Optional[Path]], None]] = None) -> None:
        if not self._save_form("Backup"):
            return
        self._backup_for(self.rt, on_done)

    # ---------------------------------------------------------------------
    # Single player import
    # ---------------------------------------------------------------------
    def open_singleplayer_import(self) -> None:
        if self._is_busy():
            return
        if self._is_server_running():
            messagebox.showwarning(
                "Import Single Player",
                "Stop the server before importing a single player save.",
            )
            return

        try:
            self.cfg = self._collect_vars_to_cfg()
            self._save_active_server_config(self.cfg)
        except Exception as e:
            messagebox.showerror("Import Single Player", f"Current configuration is invalid: {e}")
            return

        SinglePlayerImportDialog(self)

    def start_singleplayer_import(self, req: SinglePlayerImportRequest) -> None:
        if self._is_server_running():
            messagebox.showwarning(
                "Import Single Player",
                "Stop the server before importing a single player save.",
            )
            return

        rt = self.rt
        cfg = rt.cfg
        log = rt.logger
        server_dir = Path(cfg.server_dir)
        server_id = rt.server_id
        app_base = self.app_base
        backup_root = resolve_backup_root(cfg, app_base)

        def job() -> None:
            result = SinglePlayerImportResult()

            if req.import_save and req.save is not None:
                if find_server_processes(server_dir):
                    raise RuntimeError("An ARK server is still running from this folder. Stop it before importing.")
                import_singleplayer_save(
                    req.save,
                    server_dir,
                    cfg.alt_save_directory_name,
                    log,
                    include_profiles=req.include_profiles,
                    include_tribes=req.include_tribes,
                    backup_root=backup_root if req.backup_first else None,
                    backup_info=backup_manifest(
                        BACKUP_KIND_PRE_IMPORT, server_id, cfg.server_name, req.save.map_name
                    ),
                    result=result,
                )

            if req.import_gus or req.import_game:
                import_singleplayer_settings(
                    req.sp_root,
                    app_base,
                    server_id,
                    server_dir,
                    log,
                    server_running=False,
                    import_gus=req.import_gus,
                    import_game=req.import_game,
                    tidy_floats=True,
                    snap_floats=req.snap_floats,
                    result=result,
                )

            if req.import_mods:
                result.mods_found = read_singleplayer_active_mods(req.sp_root)
                if result.mods_found:
                    log.info(
                        f"Single player mods: {', '.join(result.mods_found)}"
                    )

            self._ui(lambda: self._finish_singleplayer_import(req, result, server_id))

        self._run_task("Import Single Player", job, rt)

    def _finish_singleplayer_import(
        self, req: SinglePlayerImportRequest, result: SinglePlayerImportResult, server_id: str = ""
    ) -> None:
        if server_id and server_id != self.active_server_id:
            # The files are imported; only the form changes (map, mods) need its profile on screen.
            messagebox.showinfo(
                "Import Single Player",
                f"The import into {self._display_name(server_id)} finished. Switch back to it to set its map "
                "and mod list from the import, if wanted.",
            )
            return
        if req.set_server_map and req.save is not None and req.import_save:
            map_name = req.save.map_name
            if map_name in MAP_PRESETS:
                self.var_map_preset.set(map_name)
            else:
                self.var_map_preset.set(MAP_CUSTOM_SENTINEL)
            self.var_map_custom.set(map_name)
            self._sync_map_mode()
            try:
                self.cfg = self._collect_vars_to_cfg()
                self._save_active_server_config(self.cfg)
            except Exception:
                pass

        if req.import_mods and result.mods_found:
            result.mods_previous = self._mods_text_get().strip()
            self._mods_text_set(",".join(result.mods_found))
            try:
                self.cfg = self._collect_vars_to_cfg()
                self._save_active_server_config(self.cfg)
            except Exception as e:
                self.logger.error(f"Saving imported mod list failed: {e}")

        if req.import_gus or req.import_game:
            try:
                if self._ini_loaded_target:
                    self._ini_load_target(self._ini_loaded_target)
                self._ini_visual_refresh_all()
            except Exception as e:
                self.logger.error(f"Refreshing INI editor after import failed: {e}")

        lines: List[str] = []
        if req.import_save and result.destination is not None:
            lines.append(f"Copied {result.files_copied} save file(s) to:\n{result.destination}")
            if result.backup_zip is not None:
                lines.append(f"Previous server save archived to:\n{result.backup_zip}")
            if req.set_server_map and req.save is not None:
                lines.append(f"Server map set to {req.save.map_name}.")
            character = (
                "Your survivor does not come across with the world: upload it at an obelisk in "
                "single player and download it on the server."
            )
            if result.tribe_ids:
                character += " Then take back the base and tames with:\n" + "\n".join(
                    f"cheat TakeTribe {tid}" for tid in result.tribe_ids[:3]
                )
            lines.append(character)

        if result.settings_source is not None:
            lines.append(f"Settings read from:\n{result.settings_source}")
            if req.import_gus:
                lines.append(f"GameUserSettings.ini: {result.gus_applied} value(s) staged.")
            if req.import_game:
                lines.append(f"Game.ini: {result.game_applied} value(s) staged.")
            if result.floats_tidied:
                lines.append(f"Cleaned up {result.floats_tidied} float value(s) written by the game.")

        if req.import_mods and result.mods_found:
            mod_line = f"Mods field set to {len(result.mods_found)} mod(s):\n{', '.join(result.mods_found)}"
            if result.mods_previous and result.mods_previous != ",".join(result.mods_found):
                mod_line += f"\n(previously: {result.mods_previous})"
            lines.append(mod_line)
            lines.append(
                "Install those mods on the server too - they are passed with -mods= on the next start."
            )

        preserved = list(dict.fromkeys(result.gus_preserved + result.game_preserved))
        if preserved:
            shown = ", ".join(preserved[:6])
            if len(preserved) > 6:
                shown += f" (+{len(preserved) - 6} more)"
            lines.append(f"Kept server-managed values: {shown}")

        lines.extend(result.notes)

        if result.settings_source is not None:
            lines.append("Staged INI changes are written to the server on the next start.")

        self._set_status(f"Single player import done ({self._status_timestamp()})")
        messagebox.showinfo("Import Single Player", "\n\n".join(lines) if lines else "Nothing was imported.")

    # ---------------------------------------------------------------------
    # Backups
    # ---------------------------------------------------------------------
    def open_backup_manager(self) -> None:
        if self._is_busy():
            return
        try:
            self.cfg = self._collect_vars_to_cfg()
            self._save_active_server_config(self.cfg)
        except Exception as e:
            messagebox.showerror("Backups", f"Current configuration is invalid: {e}")
            return
        BackupManagerDialog(self)

    def start_backup_restore(self, req: BackupRestoreRequest) -> None:
        if self._is_server_running():
            messagebox.showwarning("Restore Backup", "Stop the server before restoring a backup.")
            return
        if self._is_busy():
            messagebox.showwarning("Restore Backup", "Another task is running. Try the restore again when it finishes.")
            return

        rt = self.rt
        cfg = rt.cfg
        server_dir = Path(cfg.server_dir)
        server_id = rt.server_id
        app_base = self.app_base
        safety_root = resolve_backup_root(cfg, app_base) if req.safety_backup else None

        def job() -> None:
            if find_server_processes(server_dir):
                raise RuntimeError("An ARK server is still running from this folder. Stop it before restoring.")
            result = restore_server_backup(
                req.backup,
                server_dir,
                cfg.alt_save_directory_name,
                app_base,
                server_id,
                rt.logger,
                restore_world=req.restore_world,
                restore_config=req.restore_config,
                safety_root=safety_root,
                server_name=cfg.server_name,
            )
            self._ui(lambda: self._finish_backup_restore(req, result, server_id))

        self._run_task("Restore Backup", job, rt)

    def _finish_backup_restore(self, req: BackupRestoreRequest, result: BackupRestoreResult, server_id: str = "") -> None:
        if server_id and server_id != self.active_server_id:
            messagebox.showinfo("Restore Backup", f"The restore into {self._display_name(server_id)} finished.")
            return
        if result.config_restored:
            try:
                if self._ini_loaded_target:
                    self._ini_load_target(self._ini_loaded_target)
                self._ini_visual_refresh_all()
            except Exception as e:
                self.logger.error(f"Refreshing INI editor after restore failed: {e}")

        lines: List[str] = [f"Restored from {req.backup.path.name}."]
        if result.maps_restored:
            lines.append(
                f"World: {', '.join(result.maps_restored)} ({result.files_written} file(s)) in:\n{result.destination}"
            )
            current_map = (self.cfg.map_name or "").strip()
            if current_map and current_map not in result.maps_restored:
                lines.append(
                    f"This server is set to run {current_map}; switch the map to "
                    f"{result.maps_restored[0]} to load the restored world."
                )
        if result.config_restored:
            lines.append(
                f"Settings: {', '.join(result.config_restored)}. Server name, passwords, RCON and max "
                "players from this profile are applied again on the next start."
            )
        if result.safety_zip is not None:
            lines.append(f"The previous save and settings were zipped to:\n{result.safety_zip}")

        self._set_status(f"Backup restored ({self._status_timestamp()})")
        messagebox.showinfo("Restore Backup", "\n\n".join(lines))

    # ---------------------------------------------------------------------
    # Cluster
    # ---------------------------------------------------------------------
    def open_cluster_setup(self) -> None:
        if self._is_busy():
            messagebox.showwarning("Cluster", "Another task is running. Try again when it finishes.")
            return
        try:
            self.cfg = self._collect_vars_to_cfg()
            self._save_active_server_config(self.cfg)
        except Exception as e:
            messagebox.showerror("Cluster", f"Current configuration is invalid: {e}")
            return
        ClusterSetupDialog(self)

    def start_cluster_setup(self, req: ClusterSetupRequest) -> None:
        rt = self.rt
        cfg = rt.cfg
        server_id = rt.server_id
        app_base = self.app_base

        def job() -> None:
            log = rt.logger
            stopped = False
            if req.copy_from is not None and rt.is_running():
                # Copy only while nothing serves the old folder: a download made after the copy
                # would leave the same dino or item in both folders.
                log.info("Cluster setup: stopping the server so its transfer data is copied while unused.")
                self._safe_stop(rt)
                stopped = True

            # A stop with backup on stop has just zipped everything; one backup is enough.
            if req.backup_first and not (stopped and cfg.backup_on_stop):
                if rt.is_running() and cfg.enable_rcon:
                    try:
                        log.info("Cluster setup: RCON SaveWorld before the backup")
                        self._rcon_try("SaveWorld", timeout=30.0, rt=rt)
                        time.sleep(AUTO_SAVE_SETTLE_SEC)
                    except Exception as e:
                        log.info(f"Cluster setup: SaveWorld failed ({e}); backing up the last save on disk.")
                result.backup_zip = backup_server(cfg, app_base, log, server_id)

            ensure_dir(req.cluster_dir)
            if req.copy_from is not None and req.copy_to is not None:
                result.copied, result.skipped = copy_cluster_data(req.copy_from, req.copy_to, log)
            outcome["stopped"] = stopped

        outcome: Dict[str, bool] = {"stopped": False}
        result = ClusterSetupResult()
        if not self._run_task(
            "Cluster Setup",
            job,
            rt,
            then=lambda: self._finish_cluster_setup(req, result, server_id, outcome["stopped"]),
        ):
            messagebox.showwarning("Cluster", "Another task is running. Try again when it finishes.")

    def _cluster_vars(self) -> Tuple[bool, str, bool, str]:
        return (
            bool(self.var_cluster_enable.get()),
            self.var_cluster_id.get(),
            bool(self.var_cluster_custom_path_enable.get()),
            self.var_cluster_dir_override.get(),
        )

    def _set_cluster_vars(self, values: Tuple[bool, str, bool, str]) -> None:
        enable, cid, custom, folder = values
        self.var_cluster_enable.set(enable)
        self.var_cluster_id.set(cid)
        self.var_cluster_custom_path_enable.set(custom)
        self.var_cluster_dir_override.set(folder)

    def _save_cluster_vars(self, values: Tuple[bool, str, bool, str], title: str) -> bool:
        """Apply cluster fields and save; on failure put the old ones back, so autosave cannot persist them later."""
        before = self._cluster_vars()
        self._set_cluster_vars(values)
        try:
            self.cfg = self._collect_vars_to_cfg()
            self._save_active_server_config(self.cfg)
        except Exception as e:
            self._set_cluster_vars(before)
            messagebox.showerror(title, f"Saving the cluster settings failed, nothing was changed: {e}")
            return False
        return True

    def _finish_cluster_setup(
        self,
        req: ClusterSetupRequest,
        result: ClusterSetupResult,
        server_id: str,
        stopped: bool = False,
    ) -> None:
        if server_id != self.active_server_id:
            # The window moved to another profile while this ran: finish on the profile itself.
            self._finish_cluster_setup_offscreen(req, result, server_id, stopped)
            return
        if not self._save_cluster_vars((True, req.cluster_id, True, str(req.cluster_dir)), "Cluster"):
            if stopped:
                messagebox.showwarning("Cluster", "The server was stopped for the copy; start it again from the Server tab.")
            return

        verb = "joined" if req.joining else "now runs in"
        self.logger.info(f"[CLUSTER] {self.cfg.server_name} {verb} cluster '{req.cluster_id}' (data folder {req.cluster_dir}).")
        self._sync_cluster_summary()
        self._set_status(f"Cluster '{req.cluster_id}' set ({self._status_timestamp()})")

        lines = [f"{self.cfg.server_name} {verb} cluster '{req.cluster_id}'.", f"Cluster data folder:\n{req.cluster_dir}"]
        if result.backup_zip is not None:
            lines.append(f"Backup made first:\n{result.backup_zip}")
        if req.copy_from is not None:
            line = f"Copied {result.copied} existing transfer file(s) from {req.copy_from} to {req.copy_to}."
            if result.skipped:
                line += f" {result.skipped} file(s) were already there and were kept."
            lines.append(line)
        if not req.joining:
            lines.append(
                "To add another map, create a profile for it with its own install folder, then use "
                "'Set Up / Join Cluster...' on it and pick 'Join'."
            )

        if stopped:
            lines.append("The server was stopped for the copy and is starting again now, inside the cluster.")
            messagebox.showinfo("Cluster", "\n\n".join(lines))
            if not self.start_server():
                messagebox.showwarning("Cluster", "Another task is running. Start the server when it finishes.")
        elif self._is_server_running():
            lines.append(
                "The server is still running with its old start options. Restart it now (safe: save, stop, "
                "start) to bring it into the cluster? Players are disconnected for the restart."
            )
            if messagebox.askyesno("Cluster", "\n\n".join(lines)):
                if not self.restart_server_safe():
                    messagebox.showwarning("Cluster", "Another task is running. Restart the server when it finishes.")
        else:
            lines.append("Takes effect the next time you start the server.")
            messagebox.showinfo("Cluster", "\n\n".join(lines))

    def _save_profile_fields(self, server_id: str, **values: Any) -> AppConfig:
        """Change and save fields of a profile that is not on screen; its form is not involved."""
        store = ServerConfigStore(server_config_path(self.app_base, server_id), server_lock_path(self.app_base, server_id))
        cfg = store.load().cfg
        ref = self._server_ref_by_id(server_id)
        if ref and ref.server_dir:
            cfg.server_dir = ref.server_dir
        for key, value in values.items():
            setattr(cfg, key, value)
        store.save(cfg)
        rt = self._runtimes.get(server_id)
        if rt is not None:
            rt.cfg = cfg
        self._cfg_generation += 1
        return cfg

    def _finish_cluster_setup_offscreen(
        self, req: ClusterSetupRequest, result: ClusterSetupResult, server_id: str, stopped: bool
    ) -> None:
        name = self._display_name(server_id)
        extra = ""
        if result.backup_zip is not None:
            extra += f"\n\nBackup made first:\n{result.backup_zip}"
        if req.copy_from is not None:
            extra += f"\n\nCopied {result.copied} existing transfer file(s) to {req.copy_to}."
        try:
            self._save_profile_fields(
                server_id,
                cluster_enable=True,
                cluster_id=req.cluster_id,
                cluster_custom_path_enable=True,
                cluster_dir_override=str(req.cluster_dir),
            )
        except Exception as e:
            msg = f"Saving the cluster settings of {name} failed, nothing was changed: {e}"
            if stopped:
                msg += "\n\nThe server was stopped for the copy; start it again from its profile."
            messagebox.showerror("Cluster", msg)
            return
        rt = self._runtime(server_id)
        rt.logger.info(f"[CLUSTER] Now in cluster '{req.cluster_id}' (data folder {req.cluster_dir}).")
        if stopped:
            if not self.start_server_for(rt):
                messagebox.showwarning("Cluster", f"{name} is in cluster '{req.cluster_id}' but was stopped for the copy; start it from its profile.")
                return
            messagebox.showinfo("Cluster", f"{name} is in cluster '{req.cluster_id}' and is starting again.{extra}")
        elif rt.is_running():
            messagebox.showinfo(
                "Cluster",
                f"{name} is in cluster '{req.cluster_id}'. It still runs with its old start options; restart it "
                f"from its profile to bring it in.{extra}",
            )
        else:
            messagebox.showinfo("Cluster", f"{name} is in cluster '{req.cluster_id}'. Takes effect on its next start.{extra}")

    def leave_cluster(self) -> None:
        _enable, old_id, _custom, folder = self._cluster_vars()
        # The ID and folder stay filled in, so joining again is one tick away.
        if not self._save_cluster_vars((False, old_id, False, folder), "Leave Cluster"):
            return
        old_id = old_id.strip()
        self.logger.info(f"[CLUSTER] {self.cfg.server_name} left cluster '{old_id}'.")
        self._sync_cluster_summary()
        self._prune_cluster_meta()

        msg = f"{self.cfg.server_name} is no longer in cluster '{old_id}'."
        if self._is_server_running():
            if messagebox.askyesno("Leave Cluster", msg + "\n\nRestart the server now (safe) to apply it?"):
                if not self.restart_server_safe():
                    messagebox.showwarning("Leave Cluster", "Another task is running. Restart the server when it finishes.")
        else:
            messagebox.showinfo("Leave Cluster", msg + " Applies on the next start.")

    def _on_cluster_enable_toggled(self) -> None:
        """Ticking 'Enable Cluster' by hand fills in an ID and a shared folder, so transfers work."""
        if not self.var_cluster_enable.get():
            return
        if not self.var_cluster_id.get().strip():
            self.var_cluster_id.set(make_cluster_id(self.var_server_name.get()))
        if not self.var_cluster_custom_path_enable.get() and not self.var_cluster_dir_override.get().strip():
            server_dir = Path(self.var_server_dir.get().strip() or DEFAULT_SERVER_DIR)
            # Uploads already kept inside the install would vanish from the players' view if the
            # folder silently moved; leave it there and let the summary point at the setup.
            if dir_has_files(server_cluster_data_dir(server_dir)):
                return
            cid = self.var_cluster_id.get().strip()
            self.var_cluster_custom_path_enable.set(True)
            self.var_cluster_dir_override.set(str(default_cluster_dir(self.app_base, cid, server_dir)))

    def _sync_cluster_summary(self) -> None:
        try:
            cfg = AppConfig(
                server_dir=self.var_server_dir.get().strip(),
                backup_dir=self.var_backup_dir.get().strip(),
                cluster_enable=bool(self.var_cluster_enable.get()),
                cluster_id=self.var_cluster_id.get().strip(),
                cluster_custom_path_enable=bool(self.var_cluster_custom_path_enable.get()),
                cluster_dir_override=self.var_cluster_dir_override.get().strip(),
            )
            folder = effective_cluster_dir(cfg)
            if folder is None:
                text = ("Standalone server, not in a cluster. Use 'Set Up / Join Cluster...' to start a "
                        "cluster with this server or to join one.")
            else:
                text = f"In cluster '{cfg.cluster_id}'. Cluster data folder: {folder}"
            warnings = cluster_config_warnings(cfg, resolve_backup_root(cfg, self.app_base))
            if warnings:
                text += "\n" + "\n".join(f"Warning: {w}" for w in warnings)
            self.var_cluster_summary.set(text)
            self.lbl_cluster_summary.configure(
                foreground=THEME_COLORS["warning"] if warnings else THEME_COLORS["muted"]
            )
        except Exception:
            pass

    def _ask_on_ui(self, title: str, message: str) -> bool:
        """Yes/No asked on the Tk thread; callable from a worker, which waits for the answer."""
        if threading.current_thread() is threading.main_thread():
            return bool(messagebox.askyesno(title, message, icon="warning"))
        done = threading.Event()
        answer = [False]

        def ask() -> None:
            try:
                answer[0] = bool(messagebox.askyesno(title, message, icon="warning"))
            finally:
                done.set()

        self._ui(ask)
        done.wait()
        return answer[0]

    def _ensure_install_free(self, rt: ServerRuntime, server_dir: Path, *, confirm: bool) -> None:
        """Check no other server runs from this install (this profile's own server is ignored).

        Starting a second one is allowed after a warning: profiles may share an install on
        purpose, at the cost of sharing its INIs. Updating never is: SteamCMD cannot replace
        files a running server holds open. confirm=False refuses outright.
        """
        own = rt.pid()
        found = query_server_processes()
        if found is None:
            msg = (f"Could not check whether another ARK server runs from {server_dir} "
                   "(the Windows process query failed).")
            if not confirm:
                raise RuntimeError(msg + " Try again in a moment.")
            if not self._ask_on_ui("Running servers unknown", msg + "\n\nStart anyway?"):
                raise TaskCancelled("Start cancelled: running servers could not be checked.")
            found = {}
        # A server this manager launched earlier and has not reattached still counts.
        state = read_run_state(self.app_base, rt.server_id)
        image = process_image_path(int(state["pid"])) if state is not None and int(state["pid"]) != own else ""
        if image and _exe_key(image) == _exe_key(str(ark_server_exe(server_dir))):
            raise RuntimeError(
                f"This server still seems to run from an earlier session (PID {state['pid']}). Restart the manager "
                "to reconnect to it, or stop it first."
            )
        procs = [x for x in found.get(_exe_key(str(ark_server_exe(server_dir))), []) if x.pid != own]
        pids = [x.pid for x in procs]
        if not pids:
            return
        # Never two servers on one world: same install, same map, same save folder.
        mine = (rt.cfg.map_name.strip().lower(), (rt.cfg.alt_save_directory_name or "").strip().lower())
        for other in self._running_runtimes():
            if other is not rt and other.pid() in pids:
                ocfg = other.live_cfg()
                if (ocfg.map_name.strip().lower(), (ocfg.alt_save_directory_name or "").strip().lower()) == mine:
                    raise RuntimeError(
                        f"{self._display_name(other.server_id)} already runs {rt.cfg.map_name} from this install with "
                        "the same save folder; two servers on one world overwrite each other's saves. Give this "
                        "profile its own install folder (or its own AltSaveDirectoryName)."
                    )
        for proc in procs:
            if command_line_world(proc.command_line) == mine:
                raise RuntimeError(
                    f"An ARK server (PID {proc.pid}) already runs {rt.cfg.map_name} from this install with the same "
                    "save folder; two servers on one world overwrite each other's saves."
                )
        ours = [self._display_name(o.server_id) for o in self._running_runtimes() if o.pid() in pids]
        who = f"the profile {', '.join(ours)}" if ours else "another profile or another manager window"
        where = (
            f"An ARK server is already running from {server_dir} (PID {', '.join(map(str, pids))}), "
            f"started by {who}."
        )
        if not confirm:
            raise RuntimeError(
                where + " SteamCMD cannot update an install while a server runs from it; stop that server first."
            )
        if not self._ask_on_ui(
            "Shared install folder",
            where + "\n\nBoth servers would share this install's GameUserSettings.ini and Game.ini: starting "
            "this one writes this profile's settings over them, and each server writes its own back when it "
            "stops. Giving each map its own install folder avoids that.\n\nStart anyway?",
        ):
            raise TaskCancelled("Start cancelled: another server is running from this install folder.")

    def _prepare_cluster_for_start(self, cfg: AppConfig, logger: logging.Logger) -> None:
        for w in cluster_config_warnings(cfg, resolve_backup_root(cfg, self.app_base)):
            logger.info(f"[CLUSTER] Warning: {w}")
        folder = effective_cluster_dir(cfg)
        if folder is not None:
            if cfg.cluster_custom_path_enable:
                ensure_dir(folder)
            logger.info(f"[CLUSTER] Cluster '{cfg.cluster_id.strip()}', data folder {folder}")

    def _on_close(self, tasks_cut_ok: bool = False) -> None:
        """Closing the window. tasks_cut_ok: the user already chose to cut running tasks off."""
        if self._close_asking:
            return  # the close question is already open
        if self._closing_stop:
            messagebox.showinfo(
                "Closing",
                "The servers are being saved and stopped; the window closes once they are all down.",
            )
            return
        running = self._running_runtimes()
        choice: Optional[str] = None
        if running:
            self._close_asking = True
            try:
                choice = self._ask_close_choice(running)
            finally:
                self._close_asking = False
            if choice is None:
                return
            if choice == "background":
                self._go_to_background()  # everything keeps going; no question needed
                return
        elif self._can_background():
            tasks = self._busy_task_names()
            if tasks:
                self._close_asking = True
                try:
                    busy_choice = self._ask_close_busy_choice(tasks)
                finally:
                    self._close_asking = False
                if busy_choice is None:
                    return
                if self._running_runtimes():
                    # A task started a server while the question was open: that one needs its
                    # own question (stop it, leave it, or keep managing it).
                    self._on_close(tasks_cut_ok=not busy_choice)
                    return
                if busy_choice:
                    self._quit_when_idle = True
                    self._go_to_background()
                    self._schedule_quit_check()
                    return
                tasks_cut_ok = True
        with self._runtimes_lock:
            ops = sorted(set(self._group_ops.values()))
        if ops and not tasks_cut_ok and not messagebox.askyesno(
            "Cluster action running",
            f"A cluster-wide action is still running ({', '.join(ops)}). Closing cancels it; maps it has not "
            "reached yet are left as they are.\n\nContinue closing?",
            icon="warning",
            default="no",
        ):
            return
        # Cancelled only once the window really goes (below): backing out of a later
        # question must leave the action running.
        if running:
            if choice == "stop":
                # Tasks of servers that are not running (an install, a restore) die with the
                # window just the same once the running ones are down.
                idle_busy = [rt for rt in self._all_runtimes() if rt.is_busy() and rt not in running]
                if idle_busy and not tasks_cut_ok and not messagebox.askyesno(
                    "Task running",
                    "A task is still running for " + ", ".join(self._display_name(rt.server_id) for rt in idle_busy)
                    + ". It is cut off when the window closes, which can leave it half done.\n\n"
                    "Stop the servers and close anyway?",
                    icon="warning",
                    default="no",
                ):
                    return
                self._stop_and_close(running)  # cancels cluster actions once it is sure to go on
                return
        # A countdown has told players a restart is coming; leaving it to die with the window
        # would leave them waiting for nothing.
        counting = [rt for rt in self._running_runtimes() if rt.countdown is not None]
        if counting and not messagebox.askyesno(
            "Countdown running",
            "A restart countdown is running for " + ", ".join(self._display_name(rt.server_id) for rt in counting)
            + ". Closing cancels it and tells the players the restart is off.\n\nClose anyway?",
            icon="warning",
            default="no",
        ):
            return
        # Workers are daemon threads: closing kills them mid-write (INIs, zips, SteamCMD, a
        # restart), whether or not servers are left running.
        if (self._any_busy() or self._followups_pending()) and not tasks_cut_ok and not messagebox.askyesno(
            "Task running",
            "A task is still running (see the console). Closing now can leave it half done, for example "
            "a partly written backup or settings file, or a server started but no longer tracked.\n\n"
            "Close anyway?",
            icon="warning",
            default="no",
        ):
            return
        # Every question answered: now cancel, and tell the players of any countdown.
        if counting:
            self._cancel_countdowns_and_announce(counting)
        self._quit_now()

    def _poll_show_request(self) -> None:
        """A second launch of the manager asks this one to show its window."""
        try:
            if show_event_signaled():
                self._show_window()
        finally:
            with contextlib.suppress(Exception):
                self.root.after(700, self._poll_show_request)

    def _show_window(self) -> None:
        grab, self._grab_on_show = self._grab_on_show, None
        if grab is not None:
            def regrab() -> None:
                with contextlib.suppress(Exception):
                    if grab.winfo_exists():
                        grab.grab_set()
            with contextlib.suppress(Exception):
                self.root.after(150, regrab)  # once it is mapped again
        with contextlib.suppress(Exception):
            if self.root.state() == "iconic":
                self.root.state("normal")  # back to maximized if it was
            else:
                self.root.deiconify()
                if self._in_background and self._was_zoomed:
                    self.root.state("zoomed")  # withdraw forgets it
            self.root.lift()
            # Briefly on top: Windows may refuse a plain focus change from a background process.
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            modal = self.root.grab_current()  # an open dialog keeps the keyboard
            (modal if modal is not None else self.root).focus_force()
        if self._in_background:
            self._in_background = False
            with contextlib.suppress(Exception):
                if self._dash_visible():
                    self._dash_refresh()  # it was not redrawn while hidden
            self._quit_when_idle = False  # the user is back: never quit under them
            if self._quit_check_id is not None:
                with contextlib.suppress(Exception):
                    self.root.after_cancel(self._quit_check_id)
                self._quit_check_id = None
            self.logger.info("Back from the background.")

    def _show_if_hidden(self) -> None:
        if self._in_background:
            self._show_window()

    def _before_dialog(self, kind: str, title: str, message: str) -> bool:
        """A message box is about to show. While finishing tasks in the background to quit, a
        plain report ("Restore complete") goes to the log instead; a question or a problem
        brings the window back, and it stays open."""
        if self._quit_when_idle and kind == "showinfo":
            self.logger.info(f"{title}: {message}")
            return False
        self._show_if_hidden()
        return True

    def _go_to_background(self) -> None:
        """Hide the window but keep managing: auto-saves, nightly updates, crash restarts and
        countdowns go on. Opening the manager again shows the window."""
        self._in_background = True
        with contextlib.suppress(Exception):
            self._was_zoomed = self.root.state() == "zoomed"
        self.logger.info("Running in the background: servers stay managed. Open the manager again to show this window.")
        self.root.withdraw()

    def _ask_close_choice(self, running: List[ServerRuntime]) -> Optional[str]:
        """'stop', 'background', 'leave' or None (cancel), for closing while servers run."""
        names = ", ".join(self._display_name(rt.server_id) for rt in running)
        result: Dict[str, Optional[str]] = {"choice": None}
        self._restore_if_minimized()
        prev_grab = None
        with contextlib.suppress(Exception):
            prev_grab = self.root.grab_current()  # an open dialog's; given back afterwards
        dlg = tk.Toplevel(self.root)
        dlg.title("Servers running")
        dlg.transient(self.root)
        dlg.configure(background=THEME_COLORS["bg"])
        body = ttk.Frame(dlg, padding=14)
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text=f"Still running: {names}", wraplength=520, justify="left",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        options = tuple(o for o in (
            ("background", "Keep running in the background",
             "Hides this window; the servers stay managed (auto-saves, updates, crash restarts, countdowns). "
             "Open the manager again to show it."),
            ("stop", "Stop the servers and quit", "Saves and shuts down every running server, then quits."),
            ("leave", "Quit and leave the servers running",
             "The servers keep running without the manager; it reconnects to them when it is opened again."),
        ) if o[0] != "background" or self._can_background())
        for i, (key, label, hint) in enumerate(options, start=1):
            ttk.Button(body, text=label, command=lambda k=key: (result.__setitem__("choice", k), dlg.destroy())) \
                .grid(row=2 * i - 1, column=0, sticky="ew", pady=(10 if i == 1 else 6, 0))
            ttk.Label(body, text=hint, foreground=THEME_COLORS["muted"], wraplength=520, justify="left") \
                .grid(row=2 * i, column=0, sticky="w")
        ttk.Button(body, text="Cancel", command=dlg.destroy).grid(row=2 * len(options) + 1, column=0, sticky="e", pady=(12, 0))
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        with contextlib.suppress(tk.TclError):
            dlg.grab_set()
        dlg.focus_set()
        self.root.wait_window(dlg)
        if prev_grab is not None:
            if result["choice"] == "background":
                self._grab_on_show = prev_grab  # hidden with the window: grab again once shown
            else:
                with contextlib.suppress(Exception):
                    if prev_grab.winfo_exists():
                        prev_grab.grab_set()
        return result["choice"]

    def _followups_done(self) -> None:
        with self._followups_lock:
            self._followups -= 1
        if self._quit_when_idle:
            self._check_quit_when_idle()

    def _busy_task_names(self) -> List[str]:
        with self._runtimes_lock:
            items = list(self._runtimes.values())
            ops = sorted(set(self._group_ops.values()))
        out = []
        for rt in items:
            task = rt.task  # read once: a worker may release it meanwhile
            if task:
                out.append(f"{task} ({self._display_name(rt.server_id)})")
        if not out and self._followups_pending():
            out.append("finishing a task (its next step is about to start)")
        return out + [f"{op} (cluster)" for op in ops]

    def _followups_pending(self) -> bool:
        with self._followups_lock:
            return self._followups > 0

    def _work_left(self) -> bool:
        with self._runtimes_lock:
            ops = bool(self._group_ops)
        return ops or self._followups_pending() or self._any_busy() or DiscordWebhookClient.sending()

    def _restore_if_minimized(self) -> None:
        """A dialog of a minimized window would stay out of sight."""
        with contextlib.suppress(Exception):
            if self.root.state() == "iconic":
                self.root.state("normal")

    def _ask_close_busy_choice(self, tasks: List[str]) -> Optional[bool]:
        """True: finish in the background, then quit; False: quit now; None: keep the window."""
        self._restore_if_minimized()
        with self._runtimes_lock:
            group = bool(self._group_ops)
        cut = ("This cuts the task off and can leave it half done, for example a partly written backup, "
               "a half-installed server or a map started but no longer tracked.")
        if group:
            cut += " A cluster action stops where it is; maps it has not reached yet are left as they are."
        return messagebox.askyesnocancel(
            "Task running",
            "Still working: " + ", ".join(tasks) + ".\n\n"
            "Yes: finish in the background, then quit. The window hides now; opening the manager again shows it.\n\n"
            f"No: quit now. {cut}\n\n"
            "Cancel: keep the window open.",
            icon="warning",
            default="yes",
        )

    def _schedule_quit_check(self) -> None:
        if self._quit_check_id is not None:
            with contextlib.suppress(Exception):
                self.root.after_cancel(self._quit_check_id)
        self._quit_check_id = self.root.after(2000, self._check_quit_when_idle)

    def _check_quit_when_idle(self) -> None:
        """In the background only to let tasks finish: quit once nothing runs any more.

        Runs when a task's follow-ups are done and, as a fallback, every 2 seconds."""
        if self._quit_check_id is not None:
            with contextlib.suppress(Exception):
                self.root.after_cancel(self._quit_check_id)  # reached early: that timer is not needed
            self._quit_check_id = None
        if not self._quit_when_idle:
            return
        if self._work_left():
            self._schedule_quit_check()
            return
        if show_event_signaled():
            self._show_window()  # opened again just now: the user is back, so stay
            return
        self._quit_when_idle = False
        if self._running_runtimes():
            # A task started a server (an install that starts it, a Start All): quitting would
            # leave it unwatched, and staying hidden would leave nobody knowing the manager is
            # still there. Show the window instead.
            self.logger.info("Tasks finished; a server is running now, so the manager stays open to look after it.")
            self._show_window()
            return
        self.logger.info("Tasks finished; quitting.")
        self._quit_now()

    QUIT_SEND_WAIT_SEC = 15.0

    def _quit_now(self) -> None:
        """The window goes, and the manager with it, once Discord messages being sent (a
        crash report) went out, for at most QUIT_SEND_WAIT_SEC."""
        self._cancel_groups()
        close_show_event()
        if not DiscordWebhookClient.sending():
            self.root.destroy()
            return
        with contextlib.suppress(Exception):
            self.root.withdraw()
        deadline = time.monotonic() + self.QUIT_SEND_WAIT_SEC

        def wait() -> None:
            if DiscordWebhookClient.sending() and time.monotonic() < deadline:
                self.root.after(200, wait)
            else:
                self.root.destroy()

        wait()

    def _can_background(self) -> bool:
        """Only where opening the manager again can bring the hidden window back."""
        return _SHOW_EVENT is not None

    def _stop_and_close(self, running: List[ServerRuntime]) -> None:
        # No new timer tasks while closing: one could claim a server between the check
        # below and its stop, or start something the closing window would then cut off.
        self._scheduler_stop.set()
        running = self._running_runtimes()  # what runs now, not when the question was asked
        busy = [rt for rt in running if rt.is_busy()]
        if busy:
            self._start_scheduler()
            messagebox.showwarning(
                "Server running",
                "A task is running for " + ", ".join(self._display_name(rt.server_id) for rt in busy)
                + ". Close again when it finishes.",
            )
            return

        self._closing_stop = True
        close_show_event()  # a launch from now on starts afresh instead of reaching this window
        self._cancel_groups()
        if not running:
            # Everything stopped while the questions were open: nothing to wait for.
            self._close_state = {"left": 0, "failed": []}
            self._after_stop_all()
            return
        for rt in running:
            self._cancel_countdown(rt)  # they are being stopped now; nothing left to count down to
        state = {"left": len(running), "failed": []}
        lock = threading.Lock()

        def one_done(rt: ServerRuntime, error: Optional[str]) -> None:
            with lock:
                state["left"] -= 1
                if error is not None:
                    state["failed"].append((rt, error))
                last = state["left"] == 0
            if last:
                self._ui(self._after_stop_all)

        self._close_state = state
        # Every server stops at the same time; the window closes after the last one, and
        # only if every stop went through.
        for rt in running:
            def job(_rt: ServerRuntime = rt) -> None:
                try:
                    self._safe_stop(_rt)
                except Exception as e:
                    one_done(_rt, str(e))
                    raise
                one_done(_rt, None)

            if not self._run_task("Stop Server", job, rt, quiet=True):
                one_done(rt, "another task claimed it first")

    def _after_stop_all(self) -> None:
        state = getattr(self, "_close_state", {"failed": []})
        failed = state.get("failed") or []
        still = self._running_runtimes()
        if not failed and not still:
            self.root.destroy()
            return
        # Keep the window: something did not stop cleanly, and closing would hide it.
        self._closing_stop = False
        create_show_event(self.app_base)
        self._start_scheduler()
        lines = [f"- {self._display_name(rt.server_id)}: {err}" for rt, err in failed]
        lines += [f"- {self._display_name(rt.server_id)}: still running" for rt in still if rt not in [f[0] for f in failed]]
        messagebox.showwarning(
            "Close",
            "Not every server stopped cleanly, so the window stays open:\n\n" + "\n".join(lines)
            + "\n\nSee the console for details, then close again.",
        )

    # ---------------------------------------------------------------------
    # Dashboard: every server, grouped by cluster
    # ---------------------------------------------------------------------
    DASH_REFRESH_MS = 2000
    PLAYER_POLL_SEC = 60.0

    def _cluster_views(self) -> Tuple[List[ClusterView], List[str]]:
        profiles = [(rt.server_id, rt.cfg) for rt in self._all_runtimes()]
        return group_clusters(profiles, self.global_cfg.clusters)

    def _prune_cluster_meta(self) -> None:
        """Forget names and delays of clusters no profile belongs to any more."""
        live = {c.cluster_id for c in self._cluster_views()[0]}
        stale = [cid for cid in self.global_cfg.clusters if cid not in live]
        if stale:
            for cid in stale:
                self.global_cfg.clusters.pop(cid, None)
            with contextlib.suppress(Exception):
                self._save_global_config(self.global_cfg)

    def _cluster_view(self, cluster_id: str) -> Optional[ClusterView]:
        return next((c for c in self._cluster_views()[0] if c.cluster_id == cluster_id), None)

    def _build_dashboard_tab(self) -> None:
        theme = THEME_COLORS
        tab = self.tab_dash
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top.columnconfigure(3, weight=1)
        ttk.Button(top, text="New Cluster...", command=self.open_cluster_wizard).grid(row=0, column=0, padx=(0, 6))
        self.btn_dash_add_maps = ttk.Button(top, text="Add Maps to Cluster...", command=self._dash_add_maps)
        self.btn_dash_add_maps.grid(row=0, column=1, padx=(0, 6))
        ttk.Button(top, text="Add Server Profile...", command=self._add_server_profile).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(top, text="Import Cluster...", command=self.import_cluster).grid(row=0, column=4, padx=(12, 0))
        ttk.Label(
            top,
            text="Select a server or a cluster below. Double-click a server to open it in the other tabs.",
            foreground=theme["muted"],
        ).grid(row=0, column=3, sticky="w", padx=(12, 0))

        frame = ttk.Frame(tab)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.dash_tree = ttk.Treeview(
            frame,
            columns=("map", "status", "players", "cpu", "ram", "ports", "uptime", "folder"),
            show="tree headings",
            selectmode="browse",
        )
        for col, text, width, anchor in (
            ("#0", "Server", 260, "w"),
            ("map", "Map", 150, "w"),
            ("status", "Status", 200, "w"),
            ("players", "Players", 70, "e"),
            ("cpu", "CPU", 60, "e"),
            ("ram", "Memory", 80, "e"),
            ("ports", "Ports (game/query/RCON)", 170, "w"),
            ("uptime", "Uptime", 90, "e"),
            ("folder", "Install folder", 320, "w"),
        ):
            self.dash_tree.heading(col, text=text)
            self.dash_tree.column(col, width=width, anchor=anchor, stretch=(col == "folder"))
        self.dash_tree.grid(row=0, column=0, sticky="nsew")
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.dash_tree.yview)
        ys.grid(row=0, column=1, sticky="ns")
        self.dash_tree.configure(yscrollcommand=ys.set)
        self.dash_tree.bind("<<TreeviewSelect>>", lambda _e: self._dash_sync_buttons())
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._dash_refresh() if self._dash_visible() else None, add="+")
        self._dash_views: Dict[str, ClusterView] = {}
        self.dash_tree.bind("<Double-1>", lambda _e: self._dash_open_selected())

        srv = ttk.LabelFrame(tab, text="Selected Server", padding=8)
        srv.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._dash_server_buttons: List[ttk.Button] = []
        for i, (text, cmd) in enumerate((
            ("Start", lambda: self._dash_server_action("start")),
            ("Stop (Safe)", lambda: self._dash_server_action("stop")),
            ("Restart (Safe)", lambda: self._dash_server_action("restart")),
            ("Update & Restart", lambda: self._dash_server_action("update")),
            ("Back Up Now", lambda: self._dash_server_action("backup")),
            ("Open", self._dash_open_selected),
            ("Cluster...", lambda: self._dash_server_action("cluster")),
            ("Copy Settings To...", lambda: self._dash_server_action("copy")),
            ("Cancel Countdown", lambda: self._dash_server_action("cancel_countdown")),
        )):
            b = ttk.Button(srv, text=text, command=cmd)
            b.grid(row=i // 5, column=i % 5, padx=(0, 6), pady=(0, 4), sticky="ew")
            self._dash_server_buttons.append(b)

        cl = ttk.LabelFrame(tab, text="Selected Cluster", padding=8)
        cl.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self._dash_cluster_buttons: List[ttk.Button] = []
        for i, (text, cmd) in enumerate((
            ("Start All", lambda: self._dash_cluster_action("start")),
            ("Stop All (Safe)", lambda: self._dash_cluster_action("stop")),
            ("Restart All (one by one)", lambda: self._dash_cluster_action("restart")),
            ("Update All", lambda: self._dash_cluster_action("update")),
            ("Back Up All", lambda: self._dash_cluster_action("backup")),
            ("Broadcast...", lambda: self._dash_cluster_action("broadcast")),
            ("Health Check...", lambda: self._dash_cluster_action("health")),
            ("Export...", lambda: self._dash_cluster_action("export")),
            ("Rename...", lambda: self._dash_cluster_action("rename")),
            ("Start Delay...", lambda: self._dash_cluster_action("stagger")),
        )):
            b = ttk.Button(cl, text=text, command=cmd)
            b.grid(row=i // 5, column=i % 5, padx=(0, 6), pady=(0, 4), sticky="ew")
            self._dash_cluster_buttons.append(b)
        self.var_dash_hint = tk.StringVar(master=self.root, value="")
        ttk.Label(tab, textvariable=self.var_dash_hint, foreground=theme["muted"], wraplength=1100, justify="left") \
            .grid(row=4, column=0, sticky="w", pady=(6, 0))

    def _dash_selection(self) -> Tuple[str, str]:
        """('server', id), ('cluster', cluster id) or ('', '')."""
        sel = self.dash_tree.selection() if hasattr(self, "dash_tree") else ()
        if not sel:
            return "", ""
        iid = sel[0]
        if iid.startswith("s:"):
            return "server", iid[2:]
        if iid.startswith("c:"):
            return "cluster", iid[2:]
        return "", ""

    def _dash_sync_buttons(self) -> None:
        kind, ident = self._dash_selection()
        for b in self._dash_server_buttons:
            b.configure(state="normal" if kind == "server" else "disabled")
        for b in self._dash_cluster_buttons:
            b.configure(state="normal" if kind == "cluster" else "disabled")
        self.btn_dash_add_maps.configure(state="normal" if kind == "cluster" else "disabled")
        hint = ""
        if kind == "cluster":
            view = self._dash_views.get(ident)  # as grouped by the last refresh
            if view is not None:
                hint = (
                    f"Start All starts the stopped maps one after another, {view.stagger_sec} s apart. Restart All "
                    "restarts the running maps one at a time, so the rest of the cluster stays up. Update All updates "
                    "every map; running ones take turns and come back up."
                )
        elif kind == "server":
            hint = "Actions apply to this server only; the others keep running."
        self.var_dash_hint.set(hint)

    @staticmethod
    def _dash_status(rt: ServerRuntime) -> str:
        running = rt.is_running()
        task = rt.task
        if running and rt.countdown:
            return f"Running - {rt.countdown}"
        if running and task:
            return f"Running - {task}"
        if running:
            return "Running"
        if task:
            return f"{task}..."
        if (Path(rt.cfg.server_dir) / COPY_MARKER_NAME).exists():
            return "Install unfinished"
        if not ark_server_exe(Path(rt.cfg.server_dir)).exists():
            return "Not installed"
        return "Stopped"

    def _dash_server_values(self, rt: ServerRuntime) -> Tuple[str, ...]:
        cfg = rt.live_cfg()
        ports = f"{cfg.port} / {cfg.query_port} / {cfg.rcon_port if cfg.enable_rcon else '-'}"
        uptime = ""
        if rt.is_running() and rt.started_at is not None:
            uptime = format_duration(int((datetime.now() - rt.started_at).total_seconds()))
        running = rt.is_running()
        players = "" if rt.players is None or not running else str(rt.players)
        cpu = "" if rt.cpu_percent is None or not running else f"{rt.cpu_percent:.0f}%"
        ram = "" if not rt.memory_bytes or not running else f"{rt.memory_bytes / 1024 ** 3:.1f} GB"
        return (pretty_map_name(cfg.map_name), self._dash_status(rt), players, cpu, ram, ports, uptime, cfg.server_dir)

    def _dash_refresh(self) -> None:
        if not hasattr(self, "dash_tree"):
            return
        try:
            clusters, standalone = self._cluster_views()
        except Exception as e:
            self.logger.info(f"Dashboard: {e}")
            return
        tree = self.dash_tree
        self._dash_views = {c.cluster_id: c for c in clusters}
        wanted: List[str] = []

        def put(iid: str, parent: str, index: int, text: str, values: Tuple[str, ...]) -> None:
            if tree.exists(iid):
                if tree.parent(iid) != parent or tree.index(iid) != index:
                    tree.move(iid, parent, index)
                tree.item(iid, text=text, values=values)
            else:
                tree.insert(parent, index, iid=iid, text=text, values=values, open=True)
            wanted.append(iid)

        for ci, view in enumerate(clusters):
            members = [self._runtime(sid) for sid in view.members]
            running = sum(1 for rt in members if rt.is_running())
            counts = [rt.players for rt in members if rt.is_running() and rt.players is not None]
            mem = sum(rt.memory_bytes or 0 for rt in members if rt.is_running())
            status = f"{running} of {len(members)} running"
            op = self._group_ops.get(view.cluster_id)
            if op:
                status += f" - {op}"
            try:
                n_issues = sum(1 for i in self._cluster_health_cached(view) if i.severity in ("error", "warning"))
            except Exception:
                n_issues = 0
            if n_issues:
                status += f" - {n_issues} issue(s), see Health Check"
            put(
                f"c:{view.cluster_id}", "", ci,
                f"Cluster: {view.name}",
                (f"{len(members)} map(s)", status, str(sum(counts)) if counts else "", "",
                 f"{mem / 1024 ** 3:.1f} GB" if mem else "", "", "", f"ID {view.cluster_id}"),
            )
            for mi, rt in enumerate(members):
                put(f"s:{rt.server_id}", f"c:{view.cluster_id}", mi, self._display_name(rt.server_id), self._dash_server_values(rt))
        if standalone:
            put("g:standalone", "", len(clusters), "Standalone servers", ("",) * 8)
            for si, sid in enumerate(standalone):
                rt = self._runtime(sid)
                put(f"s:{sid}", "g:standalone", si, self._display_name(sid), self._dash_server_values(rt))

        keep = set(wanted)
        for parent in ("",) + tuple(tree.get_children("")):
            if parent and not tree.exists(parent):
                continue  # went with its stale parent a moment ago
            for iid in tree.get_children(parent):
                if iid not in keep:
                    tree.delete(iid)
        self._dash_sync_buttons()

    def _dash_visible(self) -> bool:
        try:
            return self.nb.select() == str(self.tab_dash)
        except Exception:
            return False

    def _dash_tick(self) -> None:
        try:
            if self._dash_visible() and not self._in_background:
                self._dash_refresh()
        finally:
            with contextlib.suppress(Exception):
                self.root.after(self.DASH_REFRESH_MS, self._dash_tick)

    STATS_SAMPLE_SEC = 5.0

    def _start_stats_sampler(self) -> None:
        """CPU and memory of every running server, for the Dashboard."""
        cpus = max(1, os.cpu_count() or 1)

        def loop() -> None:
            while not self._player_poll_stop.wait(self.STATS_SAMPLE_SEC):
                with self._runtimes_lock:
                    items = list(self._runtimes.values())
                for rt in items:
                    pid = rt.pid() if rt.is_running() else None
                    usage = process_usage(pid) if pid else None
                    if usage is None:
                        rt.cpu_percent = rt.memory_bytes = None
                        rt._cpu_sample = None
                        continue
                    cpu_s, working_set = usage
                    now = time.monotonic()
                    prev = rt._cpu_sample
                    if prev is not None and prev[2] == pid and now > prev[0]:
                        rt.cpu_percent = max(0.0, (cpu_s - prev[1]) / (now - prev[0]) / cpus * 100.0)
                    rt._cpu_sample = (now, cpu_s, pid)
                    rt.memory_bytes = working_set

        threading.Thread(target=loop, daemon=True, name="stats").start()

    def _start_player_poll(self) -> None:
        def loop() -> None:
            # Its own event: closing pauses the scheduler, and must not end this for good.
            while not self._player_poll_stop.wait(self.PLAYER_POLL_SEC):
                for rt in self._running_runtimes():
                    if rt.removed or not rt.live_cfg().enable_rcon:
                        rt.players = None
                        continue
                    try:
                        provider = PlayerListProvider(
                            lambda cmd, timeout, _rt=rt: self._rcon_try(cmd, timeout=timeout, rt=_rt), rt.logger
                        )
                        rt.players = len(provider.get_players())
                    except Exception:
                        rt.players = None

        threading.Thread(target=loop, daemon=True, name="player-poll").start()

    def _dash_open_selected(self) -> None:
        kind, ident = self._dash_selection()
        if kind != "server":
            return
        self._switch_server_profile(ident)
        if self.active_server_id == ident:
            self._refresh_server_selector(ident)
            self.nb.select(self.tab_server)

    def _dash_prepare(self, server_id: str, title: str) -> bool:
        """The form holds unsaved edits only for the profile on screen; save them first."""
        if server_id == self.active_server_id:
            return self._save_form(title)
        return True

    def _dash_server_action(self, action: str) -> None:
        kind, sid = self._dash_selection()
        if kind != "server":
            return
        if action == "cluster":
            self._switch_server_profile(sid)
            if self.active_server_id == sid:
                self._refresh_server_selector(sid)
                self.open_cluster_setup()
            return
        if action == "cancel_countdown":
            rt = self._runtime(sid)
            if self._cancel_countdown(rt, announce=True):
                rt.logger.info("Countdown cancelled from the Dashboard.")
            else:
                messagebox.showinfo("Cancel Countdown", f"No restart countdown is running for {self._display_name(sid)}.")
            return
        if action == "copy":
            if sid == self.active_server_id:
                self._save_form("Copy Settings", show_error=False)
            self.open_copy_settings(sid)
            return
        titles = {"start": "Start Server", "stop": "Stop Server", "restart": "Restart Server",
                  "update": "Update & Restart", "backup": "Backup"}
        if action != "stop" and not self._dash_prepare(sid, titles[action]):
            return
        if action == "stop" and sid == self.active_server_id:
            self._save_form("Stop Server", show_error=False)
        rt = self._runtime(sid)
        started = {
            "start": lambda: self.start_server_for(rt),
            "stop": lambda: self.stop_server_for(rt),
            "restart": lambda: self.restart_server_for(rt),
            "update": lambda: self.update_and_restart_for(rt),
            "backup": lambda: self._backup_for(rt),
        }[action]()
        if not started:
            messagebox.showwarning(titles[action], f"A task is running for {self._display_name(sid)}. Try again when it finishes.")
        self._dash_refresh()

    def _backup_for(self, rt: ServerRuntime, on_done: Optional[Callable[[Optional[Path]], None]] = None) -> bool:
        def job() -> None:
            cfg = rt.live_cfg()  # a running server's folder is the one it started with
            if rt.is_running() and cfg.enable_rcon:
                # Zip what the world is now, not the last autosave minutes ago.
                try:
                    rt.logger.info("Backup: RCON SaveWorld")
                    self._rcon_try("SaveWorld", timeout=30.0, rt=rt)
                    time.sleep(AUTO_SAVE_SETTLE_SEC)
                except Exception as e:
                    rt.logger.info(f"Backup: SaveWorld failed ({e}); zipping the last save on disk.")
            path = backup_server(cfg, self.app_base, rt.logger, rt.server_id)
            self._ui(lambda: self._set_backup_status(path, label="Backup created", rt=rt))
            if on_done is not None:
                self._ui(lambda: on_done(path))

        return self._run_task("Backup", job, rt)

    @staticmethod
    def _wait_idle(rt: ServerRuntime, timeout: float, cancel: Optional[threading.Event] = None) -> None:
        end = time.monotonic() + timeout
        while rt.is_busy() and time.monotonic() < end:
            if cancel is not None and cancel.wait(0.5):
                return
            if cancel is None:
                time.sleep(0.5)

    def _wait_ready(self, rt: ServerRuntime, cancel: threading.Event, timeout: float = READY_WAIT_SEC) -> bool:
        """After a start: wait until the map has loaded (RCON answers) or failed.

        True when it is up, or still starting when the time is up; False when its task
        ended without a running server. A cancel returns at once.
        """
        self._wait_idle(rt, timeout=30 * 60, cancel=cancel)
        if cancel.is_set():
            return True
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not rt.is_running():
                return False
            if not rt.live_cfg().enable_rcon:
                return True  # nothing to ask; the process is up
            try:
                self._rcon_try("ListPlayers", timeout=3.0, rt=rt)
                return True
            except Exception:
                pass
            if cancel.wait(5.0):
                return True
        rt.logger.info("Still loading after the wait; moving on to the next map.")
        return rt.is_running()

    def _cancel_groups(self, cluster_id: Optional[str] = None) -> List[str]:
        """Cancel the cluster-wide sequences of one cluster (or all); returns their labels."""
        with self._runtimes_lock:
            items = [(cid, ev) for cid, ev in self._group_cancel.items() if cluster_id in (None, cid)]
            labels = [self._group_ops.get(cid, "") for cid, _ in items]
        for _, ev in items:
            ev.set()
        return labels

    def _run_group(self, cluster_id: str, label: str, fn: Callable[[threading.Event], None]) -> bool:
        """A cluster-wide sequence on its own thread; one at a time per cluster.

        fn gets the sequence's cancel event: it checks it before every step and waits on
        it instead of sleeping, so Stop All or closing the window ends it at once.
        """
        cancel = threading.Event()
        with self._runtimes_lock:
            if cluster_id in self._group_ops:
                return False
            self._group_ops[cluster_id] = label
            self._group_cancel[cluster_id] = cancel

        def worker() -> None:
            try:
                self.logger.info(f"=== Cluster {cluster_id}: {label} started ===")
                fn(cancel)
                state = "cancelled" if cancel.is_set() else "completed"
                self.logger.info(f"=== Cluster {cluster_id}: {label} {state} ===")
            except Exception as e:
                self.logger.error(f"Cluster {cluster_id}: {label} failed: {e}")
            finally:
                with self._followups_lock:
                    self._followups += 1  # what it queued (a failure warning) runs before any quit
                with self._runtimes_lock:
                    self._group_ops.pop(cluster_id, None)
                    self._group_cancel.pop(cluster_id, None)
                self._ui(self._dash_refresh)
                self._ui(self._followups_done)

        threading.Thread(target=worker, daemon=True, name=f"group-{cluster_id}").start()
        return True

    def _dash_add_maps(self) -> None:
        kind, cid = self._dash_selection()
        view = self._cluster_view(cid) if kind == "cluster" else None
        if view is not None:
            self.open_cluster_wizard(view)

    def _dash_cluster_action(self, action: str) -> None:
        kind, cid = self._dash_selection()
        view = self._cluster_view(cid) if kind == "cluster" else None
        if view is None:
            return
        # Members act from their saved profiles; the one on screen may hold unsaved edits.
        if self.active_server_id in view.members:
            self._save_form("Cluster", show_error=False)
        members = [self._runtime(sid) for sid in view.members]
        names = lambda rts: ", ".join(self._display_name(r.server_id) for r in rts)  # noqa: E731

        if action == "export":
            self.export_cluster(view)
            return
        if action == "health":
            self.open_cluster_health(view)
            self._dash_refresh()
            return
        if action == "rename":
            new = simpledialog.askstring("Rename Cluster", "Display name:", initialvalue=view.name, parent=self.root)
            if new and new.strip():
                self.global_cfg.clusters.setdefault(view.cluster_id, {})["name"] = new.strip()
                self._save_global_config(self.global_cfg)
            self._dash_refresh()
            return
        if action == "stagger":
            sec = simpledialog.askinteger(
                "Start Delay", "Seconds between maps when starting the whole cluster:",
                initialvalue=view.stagger_sec, minvalue=0, maxvalue=3600, parent=self.root,
            )
            if sec is not None:
                self.global_cfg.clusters.setdefault(view.cluster_id, {})["start_stagger_sec"] = int(sec)
                self._save_global_config(self.global_cfg)
            self._dash_refresh()
            return
        if action == "stop":
            # Stops a Start All / Restart All of this cluster first, or it would bring maps back.
            if self._cancel_groups(view.cluster_id):
                self.logger.info(f"Cluster {view.cluster_id}: Stop All cancelled the cluster-wide action in progress.")
            # The maps are going down: no planned restart may bring one back afterwards.
            for rt in members:
                self._cancel_countdown(rt)
            running = [rt for rt in members if rt.is_running() and not rt.is_busy()]
            for rt in running:
                self.stop_server_for(rt)
            # Maps in the middle of a task (a start, a restart, an update) would come up after
            # this; they are stopped as soon as their task is done.
            pending = [rt for rt in members if rt.is_busy()]
            if pending:
                def sweep(_pending: List[ServerRuntime] = pending) -> None:
                    for rt in _pending:
                        end = time.monotonic() + 60 * 60
                        while time.monotonic() < end:
                            self._wait_idle(rt, timeout=max(1.0, end - time.monotonic()))
                            if not rt.is_running() or self.stop_server_for(rt):
                                break
                            time.sleep(2.0)  # another task took the server first; try again
                        else:
                            rt.logger.info("Stop All: gave up stopping, the server stayed busy for an hour.")

                threading.Thread(target=sweep, daemon=True, name="stop-all-sweep").start()
                rt0 = pending[0]
                self._set_status_for(rt0, f"Stop All: {names(pending)} will be stopped when their current task ends.")
            self._dash_refresh()
            return
        if action == "backup":
            refused = [rt for rt in members if not self._backup_for(rt)]
            if refused:
                messagebox.showwarning("Back Up All", f"A task is running for {names(refused)}; back it up when it finishes.")
            self._dash_refresh()
            return
        if action == "update":
            refused = []
            for rt in members:
                if rt.is_running():
                    ok = self.update_and_restart_for(rt)
                elif server_installed(Path(rt.cfg.server_dir)) or (Path(rt.cfg.server_dir) / COPY_MARKER_NAME).exists():
                    ok = self._run_task("Update / Validate", lambda _rt=rt: self._update_server_install(_rt, validate=None), rt)
                else:
                    continue  # not installed: First Install is a deliberate step
                if not ok:
                    refused.append(rt)
            if refused:
                messagebox.showwarning("Update All", f"A task is running for {names(refused)}; update it when it finishes.")
            self._dash_refresh()
            return
        if action == "broadcast":
            running = [rt for rt in members if rt.is_running() and rt.live_cfg().enable_rcon]
            if not running:
                messagebox.showinfo("Broadcast", "No map of this cluster is running with RCON on.")
                return
            msg = simpledialog.askstring("Broadcast", f"Message to every running map of {view.name}:", parent=self.root)
            if not msg or not msg.strip():
                return
            text = msg.strip().replace("\n", " ")

            def send() -> None:
                for rt in running:
                    try:
                        self._rcon_try(f"ServerChat {text}", timeout=5.0, rt=rt)
                        rt.logger.info(f"Broadcast sent: {text}")
                    except Exception as e:
                        rt.logger.error(f"Broadcast failed: {e}")

            threading.Thread(target=send, daemon=True, name="broadcast").start()
            return

        if action == "start":
            stagger = view.stagger_sec
            errors = [i for i in self._cluster_health(view) if i.severity == "error"]
            if errors and not messagebox.askyesno(
                "Start All",
                f"{len(errors)} problem(s) found in {view.name}:\n- " + "\n- ".join(i.text for i in errors[:6])
                + "\n\nStart anyway? Health Check... on the Dashboard fixes most of them.",
                icon="warning",
                default="no",
            ):
                return

            def start_all(cancel: threading.Event) -> None:
                todo = []
                for rt in members:
                    if rt.is_running():
                        continue
                    if not server_installed(Path(rt.cfg.server_dir)):
                        rt.logger.info("Start All: skipped, the server is not installed or its install is unfinished (use First Install).")
                        continue
                    todo.append(rt)
                failed: List[str] = []
                for i, rt in enumerate(todo):
                    if cancel.is_set():
                        break
                    if not self.start_server_for(rt, quiet=True):
                        rt.logger.info("Start All: skipped, a task is running for this server.")
                        continue
                    # Maps load their world on start; the next one waits until this one answers.
                    if not self._wait_ready(rt, cancel):
                        failed.append(self._display_name(rt.server_id))
                        continue
                    if i < len(todo) - 1 and stagger and cancel.wait(stagger):
                        break
                if failed:
                    self._ui(lambda: messagebox.showwarning(
                        "Start All", "These maps did not start (see the console):\n- " + "\n- ".join(failed)))

            if not self._run_group(view.cluster_id, "Starting all", start_all):
                messagebox.showwarning("Start All", "Another cluster-wide action is still running for this cluster.")
        elif action == "restart":
            stagger = view.stagger_sec
            # Every warning minute any map asks for; maps with warnings off still get these.
            minutes = sorted({m for rt in members for m in self._warning_minutes(rt)}, reverse=True)
            warn = False
            if minutes and any(rt.is_running() and rt.live_cfg().enable_rcon for rt in members):
                choice = messagebox.askyesnocancel(
                    "Restart All",
                    f"Warn the players of every map first ({', '.join(map(str, minutes))} minutes before), then "
                    "restart the maps one by one?\n\nYes: warn first.\nNo: restart now.\nCancel: do nothing.",
                )
                if choice is None:
                    return
                warn = bool(choice)

            def restart_all(cancel: threading.Event) -> None:
                pending_update = [rt for rt in members if rt.is_running() and rt.countdown_kind == "update"]
                for rt in pending_update:
                    rt.logger.info("Restart All: skipped, a planned update restarts this map soon anyway.")
                todo = [rt for rt in members if rt.is_running() and rt not in pending_update]
                # Restart All replaces each map's own planned restart (it would restart the map a
                # second time); a planned update stays, since it still has to update.
                for rt in todo:
                    if self._cancel_countdown(rt, kinds=("restart",)):
                        rt.logger.info("Restart All takes over the planned restart of this map.")
                        end = time.monotonic() + 5
                        while rt.countdown_kind == "restart" and time.monotonic() < end:
                            time.sleep(0.1)
                if warn:
                    # Shown on each map, and it blocks a second countdown on them. Cancel Countdown
                    # on one of them cancels the whole Restart All (and tells every map); stopping
                    # one map only takes that map out.
                    owned: List[ServerRuntime] = []
                    for rt in todo:
                        with rt.task_lock:
                            if rt.countdown is None:
                                rt.countdown = f"restarts in {minutes[0]} min (Restart All)"
                                rt.countdown_cancel = cancel
                                rt.countdown_kind = "group"
                                rt.countdown_announced = False
                                owned.append(rt)
                    self._group_countdown_members[id(cancel)] = owned

                    def step(m: int) -> None:
                        for rt in owned:
                            if rt.countdown_cancel is cancel:
                                rt.countdown = f"restarts in {m} min (Restart All)"
                                rt.countdown_announced = True
                        self._ui(self._refresh_buttons)

                    try:
                        listeners = [rt for rt in owned]
                        if not self._run_countdown(listeners, "restarts (one map at a time, starting)", minutes, cancel, step):
                            return
                    finally:
                        self._group_countdown_members.pop(id(cancel), None)
                        for rt in owned:
                            with rt.task_lock:
                                if rt.countdown_cancel is cancel:
                                    rt.countdown = None
                                    rt.countdown_cancel = None
                                    rt.countdown_kind = None
                                    rt.countdown_announced = False
                        self._ui(self._refresh_buttons)
                    owned_ids = {rt.server_id for rt in owned}
                else:
                    owned_ids = set()
                for i, rt in enumerate(todo):
                    # One map at a time, so players can stay on the others meanwhile.
                    self._wait_idle(rt, timeout=30 * 60, cancel=cancel)
                    if cancel.is_set():
                        return
                    if not rt.is_running():
                        # Stopped since the sequence began: a restart would bring it back up.
                        rt.logger.info("Restart All: skipped, the server was stopped meanwhile.")
                        continue
                    if not self.restart_server_for(
                        rt, quiet=True, from_countdown=rt.server_id in owned_ids, only_if_running=True
                    ):
                        rt.logger.info("Restart All: skipped, a task is running for this server.")
                        continue
                    if not self._wait_ready(rt, cancel):
                        # Whatever failed here (the shared cluster drive, a port) would likely fail
                        # for the next map too: stop rather than take the cluster down map by map.
                        name = self._display_name(rt.server_id)
                        self._ui(lambda: messagebox.showwarning(
                            "Restart All",
                            f"{name} did not come back up (see the console), so Restart All stopped there; "
                            "the other maps were left running."))
                        return
                    if i < len(todo) - 1 and stagger and cancel.wait(stagger):
                        return

            if not self._run_group(view.cluster_id, "Restarting one by one", restart_all):
                messagebox.showwarning("Restart All", "Another cluster-wide action is still running for this cluster.")
        self._dash_refresh()

    # ---------------------------------------------------------------------
    # Cluster wizard
    # ---------------------------------------------------------------------
    def open_cluster_wizard(self, add_to: Optional[ClusterView] = None) -> None:
        if not self._save_form("New Cluster"):
            return
        ClusterWizardDialog(self, add_to)

    def _install_chain(self, runtimes: List[ServerRuntime], source: Optional[Path]) -> None:
        """Install maps one after another. Each copies from source when there is one; the
        first map that installs becomes the source of the rest, and a map that fails does
        not keep the others from installing."""
        if not runtimes:
            return
        rt, rest = runtimes[0], runtimes[1:]

        def next_one(ok: bool) -> None:
            nxt = source
            if nxt is None and ok and server_installed(Path(rt.cfg.server_dir)):
                nxt = Path(rt.cfg.server_dir)
            self._install_chain(rest, nxt)

        if not self.first_install_for(rt, copy_from=source, after=next_one):
            rt.logger.info("First Install skipped: a task is running for this server.")
            self._install_chain(rest, source)

    def _installed_copy_source(self, template: AppConfig, exclude: Set[str]) -> Optional[Path]:
        """An install to copy the server files from: the template's, else any installed profile's."""
        if server_installed(Path(template.server_dir)):
            return Path(template.server_dir)
        for ref in self.global_cfg.servers:
            if ref.id in exclude:
                continue
            folder = Path(self._runtime(ref.id).cfg.server_dir)
            if server_installed(folder):
                return folder
        return None

    def create_cluster_profiles(
        self,
        plan: List[PlannedServer],
        template: AppConfig,
        shared: Dict[str, Any],
        cluster: Dict[str, str],
        *,
        install: bool,
        adding: bool,
        stagger_sec: Optional[int] = None,
    ) -> None:
        cid = cluster["cluster_id"]
        created: List[str] = []
        meta = self.global_cfg.clusters.setdefault(cid, {})
        if not adding or "name" not in meta:
            meta["name"] = cluster["name"]
        if stagger_sec is not None:
            meta["start_stagger_sec"] = int(stagger_sec)
        meta.setdefault("start_stagger_sec", DEFAULT_START_STAGGER_SEC)
        try:
            ensure_dir(Path(cluster["cluster_dir"]))
            for item in plan:
                cfg = copy.deepcopy(item.settings if item.settings is not None else template)
                cfg.server_dir = str(item.server_dir)
                cfg.map_name = item.map_name
                cfg.server_name = item.session_name
                cfg.port, cfg.query_port, cfg.rcon_port = item.port, item.query_port, item.rcon_port
                for key, value in shared.items():
                    setattr(cfg, key, value)
                cfg.enable_rcon = True
                cfg.cluster_enable = True
                cfg.cluster_id = cid
                cfg.cluster_custom_path_enable = True
                cfg.cluster_dir_override = cluster["cluster_dir"]
                cfg.alt_save_directory_name = ""  # each map has its own install, so its own saves
                if re.search(r"(?i)(-clusterid|clusterdiroverride|[?-]port=|queryport|sessionname|altsavedirectoryname)",
                             cfg.custom_start_args or ""):
                    cfg.custom_start_args = ""  # they would override this map's own ports, name or cluster
                sid = str(uuid.uuid4())
                ServerConfigStore(server_config_path(self.app_base, sid), server_lock_path(self.app_base, sid)).save_new(cfg)
                # Imported INI settings become the new map's staging, applied on its first start.
                stage_gus, stage_game = staging_paths(self.app_base, sid)
                for file_name, stage in (("GameUserSettings.ini", stage_gus), ("Game.ini", stage_game)):
                    text = item.ini.get(file_name)
                    if text:
                        atomic_write_text(stage, text, encoding="utf-8")
                        # An empty base: every imported value counts as set by the user, so the
                        # INIs the server writes on its first run do not merge them away.
                        atomic_write_text(stage.with_suffix(".base.ini"), "", encoding="utf-8")
                self.global_cfg.servers.append(ServerRef(id=sid, display_name=item.display_name, server_dir=cfg.server_dir))
                created.append(sid)
            self._save_global_config(self.global_cfg)
        except Exception as e:
            note = ""
            try:
                self._save_global_config(self.global_cfg)  # keep the profiles that were made
            except Exception as e2:
                note = f"\n\nSaving the profile list failed too: {e2}"
            messagebox.showerror(
                "Cluster",
                f"Creating the profiles stopped part way: {e}\n\nCreated: {len(created)} of {len(plan)}; they are "
                f"listed on the Dashboard and can be removed like any profile.{note}",
            )
            self._refresh_server_selector(self.active_server_id)
            self._dash_refresh()
            return

        self.logger.info(f"[CLUSTER] {'Added' if adding else 'Created'} {len(created)} map(s) in cluster '{cid}'.")
        self._refresh_server_selector(self.active_server_id)
        self._dash_refresh()
        with contextlib.suppress(Exception):
            self.nb.select(self.tab_dash)

        lines = [f"{'Added' if adding else 'Created'} {len(created)} profile(s) in cluster '{cluster['name']}':"]
        lines += [f"- {item.display_name}: {item.server_dir} (ports {item.port}/{item.query_port}/{item.rcon_port})" for item in plan]
        if install and created:
            source = self._installed_copy_source(template, exclude=set(created))
            runtimes = [self._runtime(sid) for sid in created]
            self._install_chain(runtimes, source)
            if source is not None:
                how = f"one after another, by copying the server files from {source}, then letting SteamCMD check them"
            else:
                how = "the first map with a full download, the others by copying the first one that installed"
            lines.append(
                f"They are being installed now ({how}); see the console. When they finish, use Start All "
                "on the Dashboard."
            )
        else:
            lines.append("Install each one with First Install on its Server tab, then use Start All on the Dashboard.")
        lines.append(
            "Game rates and other INI settings are per map: set them in each map's INI Editor. Forward each "
            "map's game and query ports (UDP) on your router, as for a single server."
        )
        messagebox.showinfo("Cluster", "\n".join(lines))

    # ---------------------------------------------------------------------
    # Cluster health and settings copy
    # ---------------------------------------------------------------------
    HEALTH_FIX_LABELS = {
        "same_folder": "Use one data folder",
        "own_folder": "Use the cluster's folder",
        "same_mods": "Use one mod list",
        "same_platform": "Use one platform setting",
        "new_ports": "Pick free ports",
        "session_names": "Rename servers",
    }

    HEALTH_CACHE_SEC = 30.0

    def _cluster_health_cached(self, view: ClusterView) -> List[HealthIssue]:
        """For the dashboard's 2-second refresh: recomputed when profiles change, at most
        every HEALTH_CACHE_SEC otherwise (an install finishing, say)."""
        key = (view.cluster_id, tuple(view.members))
        hit = self._health_cache.get(key)
        now = time.monotonic()
        if hit is not None and hit[0] == self._cfg_generation and now - hit[1] < self.HEALTH_CACHE_SEC:
            return hit[2]
        # Worked out on a thread: it touches the disk (installs, a shared cluster drive that
        # may be slow or gone), which must never freeze the window. Until then, the last result.
        if key not in self._health_running:
            self._health_running.add(key)
            generation = self._cfg_generation

            def work() -> None:
                try:
                    issues = self._cluster_health(view)
                    self._health_cache[key] = (generation, time.monotonic(), issues)
                except Exception as e:
                    self.logger.info(f"Cluster health check failed: {e}")
                finally:
                    self._health_running.discard(key)
                self._ui(lambda: self._dash_refresh() if self._dash_visible() else None)

            threading.Thread(target=work, daemon=True, name="health").start()
        return hit[2] if hit is not None else []

    def _cluster_health(self, view: ClusterView) -> List[HealthIssue]:
        # A running server is checked with what it runs on, which may differ from its saved
        # profile until its next restart.
        members = [(sid, self._display_name(sid), self._runtime(sid).live_cfg()) for sid in view.members]
        member_ids = set(view.members)
        others = [(r.id, self._display_name(r.id), self._runtime(r.id).live_cfg())
                  for r in self.global_cfg.servers if r.id not in member_ids]
        roots = {str(resolve_backup_root(cfg, self.app_base)) for _s, _n, cfg in members + others}
        return cluster_health(members, others, [Path(r) for r in roots])

    def _apply_profile_fields(self, server_id: str, **values: Any) -> None:
        """Change saved fields of any profile; the form follows when it shows that profile."""
        if server_id == self.active_server_id and not self._save_form("Settings", show_error=False):
            # Reloading the form from disk now would throw away everything typed since the
            # last save: refuse instead and let the user correct the form first.
            raise RuntimeError(
                "The profile on screen has an invalid value, so it cannot be saved first. "
                "Correct it on the Server tab (the status bar names it), then try again."
            )
        self._save_profile_fields(server_id, **values)
        if server_id == self.active_server_id:
            self._load_active_server_config(server_id)
            self._show_active_profile()

    def _cluster_common_folder(self, view: ClusterView) -> Path:
        """The data folder a cluster should use: the one most members chose, if it is safe."""
        cfgs = [self._runtime(r.id).cfg for r in self.global_cfg.servers]
        installs = [Path(c.server_dir) for c in cfgs]
        roots = [Path(r) for r in {str(resolve_backup_root(c, self.app_base)) for c in cfgs}]
        counts: Dict[str, int] = {}
        for sid in view.members:
            cfg = self._runtime(sid).cfg
            if cfg.cluster_custom_path_enable and cfg.cluster_dir_override.strip():
                folder = Path(cfg.cluster_dir_override.strip())
                if cluster_folder_problem(folder, installs, roots, allow_own_saved=False) is None:
                    counts[str(folder)] = counts.get(str(folder), 0) + 1
        if counts:
            return Path(max(counts, key=lambda k: counts[k]))
        first = self._runtime(view.members[0]).cfg
        fallback = default_cluster_dir(self.app_base, view.cluster_id, Path(first.server_dir))
        problem = cluster_folder_problem(fallback, installs, roots, allow_own_saved=False)
        if problem:
            raise RuntimeError(
                f"No safe shared folder could be picked for this cluster ({problem}). Choose one with Cluster... on one of its maps."
            )
        return fallback

    def _fix_health_issue(self, view: ClusterView, issue: HealthIssue, *, parent: Any = None, ask: bool = True) -> None:
        members = [self._runtime(sid) for sid in view.members]
        targets = [self._runtime(sid) for sid in issue.servers if sid in view.members] or members
        try:
            if issue.fix in ("same_folder", "own_folder"):
                folder = self._cluster_common_folder(view)
                who = members if issue.fix == "same_folder" else targets
                if ask and not messagebox.askyesno(
                    "Cluster Health",
                    f"Point {', '.join(self._display_name(r.server_id) for r in who)} at the data folder\n{folder}?\n\n"
                    "Uploads stored in another folder stay there, so ask players to download them before "
                    "the maps restart.",
                    parent=parent,
                ):
                    return
                ensure_dir(folder)
                for rt in who:
                    self._apply_profile_fields(
                        rt.server_id, cluster_enable=True, cluster_id=view.cluster_id,
                        cluster_custom_path_enable=True, cluster_dir_override=str(folder),
                    )
            elif issue.fix == "same_mods":
                # Never removes a mod (that would delete its dinos and structures from that map's
                # world, or keep a mod map from loading): every map gets the mods any map has.
                union: List[str] = []
                for rt in members:
                    for mod in rt.cfg.normalized_mods().split(","):
                        if mod and mod not in union:
                            union.append(mod)
                gains = []
                for rt in members:
                    have = set(m for m in rt.cfg.normalized_mods().split(",") if m)
                    new = [m for m in union if m not in have]
                    if new:
                        gains.append(f"- {self._display_name(rt.server_id)}: adds {', '.join(new)}")
                if not gains:
                    return
                if ask and not messagebox.askyesno(
                    "Cluster Health",
                    "Give every map the same mods by adding the ones it lacks (none are removed):\n\n"
                    + "\n".join(gains) + "\n\nThe servers download them on their next start.",
                    parent=parent,
                ):
                    return
                for rt in members:
                    self._apply_profile_fields(rt.server_id, mods=",".join(union))
            elif issue.fix == "same_platform":
                field_name = "server_platform"
                values: Dict[str, int] = {}
                for rt in members:
                    v = rt.cfg.normalized_mods() if field_name == "mods" else (rt.cfg.server_platform or "").strip()
                    values[v] = values.get(v, 0) + 1
                # The most common one; on a tie, the first map's.
                first = members[0].cfg.normalized_mods() if field_name == "mods" else (members[0].cfg.server_platform or "").strip()
                best = max(values, key=lambda v: (values[v], v == first))
                shown = best or "(none)"
                if ask and not messagebox.askyesno(
                    "Cluster Health", f"Set {'the mod list' if field_name == 'mods' else 'the platform setting'} "
                    f"of every map to:\n{shown}?", parent=parent,
                ):
                    return
                for rt in members:
                    self._apply_profile_fields(rt.server_id, **{field_name: best})
            elif issue.fix == "new_ports":
                for rt in targets:
                    cfg = copy.deepcopy(rt.cfg)
                    game, query, rcon = self.auto_assign_ports(cfg, stride=10, max_tries=300, ignore_server_id=rt.server_id)
                    self._apply_profile_fields(rt.server_id, port=game, query_port=query, rcon_port=rcon)
                    rt.logger.info(f"Cluster Health: new ports {game}/{query}/{rcon}.")
            elif issue.fix == "session_names":
                taken: Set[str] = {(r.cfg.server_name or "").strip().lower() for r in members if r not in targets}
                for rt in targets:
                    base = f"{view.name} - {pretty_map_name(rt.cfg.map_name)}"
                    name, n = base, 2
                    while name.lower() in taken:
                        name, n = f"{base} {n}", n + 1
                    taken.add(name.lower())
                    self._apply_profile_fields(rt.server_id, server_name=name)
        except Exception as e:
            messagebox.showerror("Cluster Health", f"The fix could not be applied: {e}", parent=parent)

    def export_cluster(self, view: ClusterView) -> None:
        if self.active_server_id in view.members:
            self._save_form("Export Cluster", show_error=False)
        path_raw = filedialog.asksaveasfilename(
            title="Export cluster", defaultextension=".json",
            initialfile=f"{map_folder_name(view.name)}-cluster.json",
            filetypes=[("Cluster file", "*.json"), ("All files", "*.*")],
        )
        if not path_raw:
            return
        members: List[Tuple[str, AppConfig, Dict[str, str]]] = []
        try:
            for sid in view.members:
                rt = self._runtime(sid)
                ini: Dict[str, str] = {}
                for target in ("gus", "game"):
                    # Read as staged, without syncing: a start of that map may be writing it now.
                    paths = ini_stage_paths(self.app_base, sid, Path(rt.cfg.server_dir), target)
                    if paths.stage.exists():
                        ini[paths.stage.name] = paths.stage.read_text(encoding="utf-8", errors="replace")
                members.append((self._display_name(sid), rt.cfg, ini))
            doc = cluster_export_document(view.name, view.cluster_id, view.stagger_sec, members)
            atomic_write_text(Path(path_raw), json.dumps(doc, indent=2), encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Export Cluster", f"Exporting failed: {e}")
            return
        self.logger.info(f"[CLUSTER] Exported '{view.name}' ({len(members)} map(s)) to {path_raw}.")
        messagebox.showinfo(
            "Export Cluster",
            f"Exported {len(members)} map(s) of {view.name} to:\n{path_raw}\n\nPasswords, the Discord webhook and "
            "folders on this machine are not in the file. Use Import Cluster on the other machine.",
        )

    def import_cluster(self) -> None:
        path_raw = filedialog.askopenfilename(
            title="Import cluster", filetypes=[("Cluster file", "*.json"), ("All files", "*.*")],
        )
        if not path_raw:
            return
        try:
            data = read_cluster_export(Path(path_raw))
        except ValueError as e:
            messagebox.showerror("Import Cluster", str(e))
            return
        ClusterImportDialog(self, Path(path_raw), data)

    def open_cluster_health(self, view: ClusterView) -> None:
        ClusterHealthDialog(self, view)

    def open_copy_settings(self, source_id: str) -> None:
        if len(self.global_cfg.servers) < 2:
            messagebox.showinfo("Copy Settings", "Add another server profile first; settings are copied between servers.")
            return
        CopySettingsDialog(self, source_id)

    def copy_settings(self, source_id: str, targets: List[str], **opts: bool) -> None:
        src_rt = self._runtime(source_id)
        src_dir = Path(src_rt.cfg.server_dir)
        if src_rt.is_busy():
            # Its own task (a start, say) may be writing the very staging about to be copied.
            messagebox.showwarning("Copy Settings", f"A task is running for {self._display_name(source_id)}; copy when it finishes.")
            return
        # Synced here, once: the copies below run in parallel and must only read the source.
        source_running = self._install_running(str(src_dir))
        try:
            for target in ("gus", "game"):
                ensure_ini_staging_synced(ini_stage_paths(self.app_base, source_id, src_dir, target),
                                          server_running=source_running, logger=src_rt.logger)
        except Exception as e:
            messagebox.showerror("Copy Settings", f"Reading the settings of {self._display_name(source_id)} failed: {e}")
            return
        refused: List[str] = []
        started: List[str] = []
        for sid in targets:
            rt = self._runtime(sid)

            def job(_rt: ServerRuntime = rt) -> None:
                copy_ini_settings(
                    self.app_base, source_id, src_dir, _rt.server_id, Path(_rt.cfg.server_dir), _rt.logger,
                    source_running=source_running,
                    target_running=self._install_running(_rt.cfg.server_dir),
                    sync_source=False,
                    **opts,
                )

            def after(_sid: str = sid) -> None:
                if _sid == self.active_server_id:
                    # The INI Editor shows this server: reload what was just staged straight from
                    # the files, or its next edit would write its old copy back over the copy.
                    self._ini_reload_from_staging()

            if self._run_task("Copy Settings", job, rt, then=after):
                started.append(self._display_name(sid))
            else:
                refused.append(self._display_name(sid))
        lines = []
        if started:
            lines.append(f"Copying the settings of {self._display_name(source_id)} to: {', '.join(started)}. "
                         "They apply on each server's next start.")
        if refused:
            lines.append(f"Skipped, a task is running for: {', '.join(refused)}. Copy to them when it finishes.")
        messagebox.showinfo("Copy Settings", "\n\n".join(lines))

    # ---------------------------------------------------------------------
    # Scheduler (auto-saves and nightly updates)
    # ---------------------------------------------------------------------
    def _start_scheduler(self) -> None:
        """One timer thread drives every server's auto-saves and nightly updates."""
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return
        self._scheduler_stop.clear()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="scheduler")
        self._scheduler_thread.start()

    def _scheduler_loop(self) -> None:
        # Reads each runtime's cfg on every pass, so edits apply without a restart.
        while not self._scheduler_stop.wait(AUTO_SAVE_TICK_SEC):
            try:
                runtimes = self._all_runtimes()
            except Exception as e:
                self.logger.info(f"Scheduler: {e}")
                continue
            for rt in runtimes:
                if rt.removed:
                    continue
                # The update first: in a tick where both are due, an auto-save claiming the
                # server would only make the update wait for it.
                try:
                    self._tick_auto_update(rt, datetime.now())
                except Exception as e:
                    rt.logger.info(f"Auto update scheduler: {e}")
                try:
                    self._tick_scheduled_restart(rt, datetime.now())
                except Exception as e:
                    rt.logger.info(f"Daily restart scheduler: {e}")
                try:
                    self._tick_auto_save(rt, time.monotonic())
                except Exception as e:
                    rt.logger.info(f"Auto-save scheduler: {e}")
            self._ui(self._sync_auto_save_hint)

    def _tick_auto_save(self, rt: ServerRuntime, now: float) -> None:
        due = rt.auto_save.tick(now, auto_save_interval_seconds(rt.cfg), rt.is_running())
        if not due:
            rt.auto_save_waiting = False
        elif self._run_task("Auto-save", lambda: self._auto_save_job(rt), rt, quiet=True):
            rt.auto_save.mark_started(now)
            rt.auto_save_waiting = False
        elif not rt.auto_save_waiting:
            rt.auto_save_waiting = True
            rt.logger.info("Auto-save is due; waiting for the current task to finish.")

    COUNTDOWN_MINUTE_SEC = 60.0  # one countdown minute, in seconds

    def _warning_minutes(self, rt: ServerRuntime) -> List[int]:
        cfg = rt.cfg
        return parse_warning_minutes(cfg.restart_warning_minutes) if cfg.restart_warnings_enabled else []

    def _run_countdown(
        self,
        runtimes: List[ServerRuntime],
        reason: str,
        minutes: List[int],
        cancel: threading.Event,
        on_step: Optional[Callable[[int], None]] = None,
    ) -> bool:
        """Tell the players of these servers, over RCON, that they go down in N minutes, at
        each of the given minutes before, then wait out the last one. False if cancelled."""
        if not minutes:
            return not cancel.is_set()
        unit = self.COUNTDOWN_MINUTE_SEC
        start = time.monotonic()
        first = minutes[0]
        for m in minutes:
            target = start + (first - m) * unit
            if cancel.wait(max(0.0, target - time.monotonic())):
                return False
            if on_step is not None:
                on_step(m)
            text = f"Server {reason} in {m} minute{'s' if m != 1 else ''}."
            for rt in runtimes:
                if rt.is_running() and rt.live_cfg().enable_rcon:
                    try:
                        self._rcon_try(f"ServerChat {text}", timeout=5.0, rt=rt)
                        rt.logger.info(f"Countdown: {text}")
                    except Exception as e:
                        rt.logger.info(f"Countdown message failed: {e}")
        return not cancel.wait(minutes[-1] * unit)

    COUNTDOWN_RETRY_SEC = 2 * 60 * 60  # how long a planned action waits for a busy server

    def _countdown_then(self, rt: ServerRuntime, reason: str, then: Callable[[], bool], kind: str = "restart") -> bool:
        """Warn the players of a running server first, then run then() once the server is free.

        Without warnings set (or RCON off) there is no countdown, but then() still waits for
        the server to be free, for up to COUNTDOWN_RETRY_SEC, so a planned restart or update
        is not lost to a task that happens to be running. False when a countdown (or a
        planned action waiting) is already there for this server.
        """
        minutes = self._warning_minutes(rt)
        if not rt.is_running() or not rt.live_cfg().enable_rcon:
            minutes = []
        cancel = threading.Event()
        with rt.task_lock:
            if rt.countdown is not None:
                return False
            rt.countdown = f"{reason} in {minutes[0]} min" if minutes else f"{reason} (waiting)"
            rt.countdown_cancel = cancel
            rt.countdown_kind = kind
            rt.countdown_announced = False

        def step(m: int) -> None:
            rt.countdown = f"{reason} in {m} min"
            rt.countdown_announced = True
            self._ui(self._refresh_buttons)

        def worker() -> None:
            try:
                if minutes:
                    rt.logger.info(f"Countdown started: the server {reason} in {minutes[0]} minute(s).")
                if not self._run_countdown([rt], reason, minutes, cancel, step):
                    rt.logger.info("Countdown cancelled.")
                    return
                if minutes and not rt.is_running():
                    rt.logger.info("Countdown over, but the server was stopped meanwhile; nothing to do.")
                    return
                rt.countdown = f"{reason} now"
                end = time.monotonic() + self.COUNTDOWN_RETRY_SEC
                while not cancel.is_set():
                    self._wait_idle(rt, timeout=max(1.0, end - time.monotonic()), cancel=cancel)
                    if cancel.is_set():
                        return
                    # A planned action restarts a running server; it never brings up one that was
                    # stopped meanwhile (the jobs check again once they hold the server).
                    if not rt.is_running() or rt.stop_requested.is_set():
                        rt.logger.info(f"The planned action ({reason}) is skipped: the server is no longer running.")
                        return
                    if then():
                        return
                    if time.monotonic() >= end:
                        rt.logger.error(f"The planned action ({reason}) gave up: the server stayed busy for too long.")
                        self._ui(lambda: self._set_status_for(rt, f"Planned action skipped ({reason}): the server stayed busy."))
                        return
                    cancel.wait(5.0)  # another task took the server first; try again shortly
            finally:
                with rt.task_lock:
                    if rt.countdown_cancel is cancel:
                        rt.countdown = None
                        rt.countdown_cancel = None
                        rt.countdown_kind = None
                        rt.countdown_announced = False
                self._ui(self._refresh_buttons)

        threading.Thread(target=worker, daemon=True, name=f"countdown-{rt.server_id}").start()
        self._ui(self._refresh_buttons)
        return True

    def _cancel_countdowns_and_announce(self, runtimes: List[ServerRuntime], wait: float = 4.0) -> None:
        """Cancel countdowns and tell their players, waiting a moment so the message is sent
        before the window (and its threads) go."""
        threads = []
        for rt in runtimes:
            told = rt.countdown_announced
            if self._cancel_countdown(rt) and told and rt.live_cfg().enable_rcon:
                t = threading.Thread(
                    target=lambda _rt=rt: self._rcon_try("ServerChat The announced restart is cancelled.", timeout=3.0, rt=_rt),
                    daemon=True,
                )
                t.start()
                threads.append(t)
        end = time.monotonic() + wait
        for t in threads:
            t.join(max(0.0, end - time.monotonic()))

    def _announce_countdown_cancelled(self, rt: ServerRuntime) -> None:
        """Tell players the announced restart is off, on a thread (RCON can be slow)."""
        def send() -> None:
            with contextlib.suppress(Exception):
                self._rcon_try("ServerChat The announced restart is cancelled.", timeout=5.0, rt=rt)

        threading.Thread(target=send, daemon=True, name="countdown-cancel").start()

    def _cancel_countdown(
        self, rt: ServerRuntime, kinds: Optional[Tuple[str, ...]] = None, announce: bool = False
    ) -> bool:
        """Cancel this server's countdown when it is one of kinds (any, if None).

        A Restart All countdown ("group") is shared by its maps: cancelling it ends it for
        all of them, and with announce every map that was warned is told.
        """
        with rt.task_lock:
            ev, kind, told = rt.countdown_cancel, rt.countdown_kind, rt.countdown_announced
        if ev is None or (kinds is not None and kind not in kinds):
            return False
        # Who was told, read before the cancel: the countdown clears these flags as it ends.
        targets = list(self._group_countdown_members.get(id(ev), [rt])) if kind == "group" else [rt]
        warned = [t for t in targets if (told if t is rt else t.countdown_announced)]
        ev.set()
        if announce:
            for target in warned:
                if target.is_running():
                    self._announce_countdown_cancelled(target)
        return True

    def _detach_from_group_countdown(self, rt: ServerRuntime) -> None:
        """A map stopped during a Restart All countdown: the others go on; it just leaves."""
        with rt.task_lock:
            if rt.countdown_kind == "group":
                rt.countdown = None
                rt.countdown_cancel = None
                rt.countdown_kind = None
                rt.countdown_announced = False

    @staticmethod
    def _minutes_apart(a: str, b: str) -> int:
        ta, tb = parse_time_hhmm(a), parse_time_hhmm(b)
        if ta is None or tb is None:
            return 24 * 60
        d = abs((ta.hour * 60 + ta.minute) - (tb.hour * 60 + tb.minute))
        return min(d, 24 * 60 - d)

    def _countdown_lead(self, rt: ServerRuntime) -> timedelta:
        """How long before a planned time its countdown starts, so the restart itself happens
        at the time set (03:00 means down at 03:00, warned from 02:45)."""
        minutes = self._warning_minutes(rt)
        if not minutes or not rt.is_running() or not rt.live_cfg().enable_rcon:
            return timedelta(0)
        return timedelta(seconds=minutes[0] * self.COUNTDOWN_MINUTE_SEC)

    def _tick_scheduled_restart(self, rt: ServerRuntime, now: datetime) -> None:
        cfg = rt.cfg
        if not cfg.scheduled_restart_enabled:
            rt.restart_next = None
            return
        when = parse_time_hhmm(cfg.scheduled_restart_time) or dt_time(hour=6, minute=0)
        due = rt.restart_next
        if due is None or rt.restart_time != cfg.scheduled_restart_time:
            rt.restart_time = cfg.scheduled_restart_time
            rt.restart_next = next_scheduled_datetime(now + self._countdown_lead(rt), when)
            return
        if now < due - self._countdown_lead(rt):
            return
        rt.restart_next = next_scheduled_datetime(max(now, due), when)
        if not rt.is_running():
            return  # a planned restart keeps a running server fresh; it never starts one
        if now > due + SCHEDULE_GRACE:
            # The PC slept or the clock jumped: hours late, players would be kicked at a random time.
            rt.logger.info(f"Daily restart skipped: it was due at {due:%H:%M} and is now long past.")
            return
        if cfg.auto_update_restart and self._minutes_apart(cfg.auto_update_time, cfg.scheduled_restart_time) < 30:
            rt.logger.info("Daily restart skipped: the nightly update restarts the server within half an hour of it.")
            return
        if rt.countdown is not None:
            rt.logger.info("Daily restart skipped: a countdown or planned restart is already running for this server.")
            return
        rt.logger.info("Daily restart due.")
        self._countdown_then(
            rt, "restarts", lambda: self.restart_server_for(rt, quiet=True, from_countdown=True, only_if_running=True)
        )

    def _tick_auto_update(self, rt: ServerRuntime, now: datetime) -> None:
        cfg = rt.cfg
        if not cfg.auto_update_restart:
            rt.auto_update_next = None
            rt.auto_update_waiting = False
            return
        when = parse_time_hhmm(cfg.auto_update_time or DEFAULT_SCHEDULE_TIME) or dt_time(hour=3, minute=0)
        due = rt.auto_update_next  # read once: the Tk thread may change it meanwhile
        if due is None or rt.auto_update_time != cfg.auto_update_time:
            rt.auto_update_time = cfg.auto_update_time
            rt.auto_update_next = next_scheduled_datetime(now + self._countdown_lead(rt), when)
            return
        # With warnings on, the countdown starts early enough that the update runs at the time set.
        if now < due - self._countdown_lead(rt):
            return
        if now > due + SCHEDULE_GRACE:
            rt.logger.info(f"Auto update skipped: it was due at {due:%H:%M} and is now long past (sleep or a clock change).")
            rt.auto_update_waiting = False
            rt.auto_update_next = next_scheduled_datetime(now, when)
            return
        if not rt.is_running():
            # The nightly update keeps running servers current; it never brings up a server
            # someone stopped on purpose, nor installs into a profile nobody ran yet.
            rt.logger.info("Auto update skipped: the server is not running.")
            rt.auto_update_waiting = False
            rt.auto_update_next = next_scheduled_datetime(now, when)
            return
        # Due. A busy server keeps it due and is tried again on the next tick, so a task
        # running at 03:00 delays tonight's update instead of skipping it. With warnings set,
        # players are told first and the update follows the countdown.
        if rt.countdown is not None or not self._countdown_then(
            rt, "restarts for an update",
            lambda: self.update_and_restart_for(rt, quiet=True, from_countdown=True, only_if_running=True),
            kind="update",
        ):
            if not rt.auto_update_waiting:
                rt.auto_update_waiting = True
                rt.logger.info("Auto update is due; waiting for the current task to finish.")
            return
        rt.auto_update_waiting = False
        rt.logger.info("Auto update trigger reached -> Update & Restart (Safe).")
        rt.auto_update_next = next_scheduled_datetime(max(now, due), when)

    def _auto_save_job(self, rt: ServerRuntime) -> None:
        cfg = rt.live_cfg()  # zip the folder the running server writes, whatever the form says now
        log = rt.logger
        if not rt.is_running():
            log.info("Auto-save skipped: the server is no longer running.")
            return

        if cfg.enable_rcon:
            try:
                log.info("Auto-save: RCON SaveWorld")
                self._rcon_try("SaveWorld", timeout=30.0, rt=rt)
                time.sleep(AUTO_SAVE_SETTLE_SEC)
            except Exception as e:
                log.info(f"Auto-save: SaveWorld failed ({e}); zipping the last save on disk.")
        else:
            log.info("Auto-save: RCON is off, so this zips the server's last autosave.")

        # A timer should not throw a dialog at the user; failures go to the log and status bar.
        try:
            path = auto_save_server(cfg, self.app_base, log, rt.server_id)
        except Exception as e:
            err = str(e)
            log.error(f"Auto-save failed: {err}")
            rt.auto_save_last = (datetime.now(), False)
            self._ui(lambda: self._set_status_for(rt, f"Auto-save failed: {err}"))
            return

        rt.auto_save_last = (datetime.now(), path is not None)
        self._ui(lambda: self._set_backup_status(path, label="Auto-save", rt=rt))

    def _set_status_for(self, rt: ServerRuntime, text: str) -> None:
        """Status bar text about a server; prefixed when it is not the one on screen."""
        if rt is self.rt:
            self._set_status(text)
        else:
            self._set_status(f"{self._display_name(rt.server_id)}: {text}")

    def _auto_save_hint_text(self) -> str:
        if not bool(self.var_auto_save_enabled.get()):
            return ""

        rt = self.rt
        typed = safe_int(self.var_auto_save_interval.get(), 0)
        minutes = clamp_auto_save_minutes(typed)
        keep = clamp_auto_save_keep(self.var_auto_save_keep.get())
        parts: List[str] = []
        if 0 < typed < AUTO_SAVE_MIN_INTERVAL_MIN:
            parts.append(f"The minimum is {AUTO_SAVE_MIN_INTERVAL_MIN} minutes.")

        wait = rt.auto_save.seconds_until_due(time.monotonic())
        if not rt.is_running():
            parts.append("Starts counting when the server is running.")
        elif rt.auto_save_waiting:
            parts.append("Due now - waiting for the current task to finish.")
        elif wait is not None:
            parts.append(f"Next one at {(datetime.now() + timedelta(seconds=wait)):%H:%M}.")
        else:
            parts.append(f"First one {minutes} minutes after the server is seen running.")

        if rt.auto_save_last is not None:
            when, ok = rt.auto_save_last
            parts.append(f"Last one {'at' if ok else 'failed at'} {when:%H:%M}.")

        parts.append(
            f"Keeping {keep} covers about the last {describe_span(minutes * 60 * keep)}; "
            "backups are never touched."
        )
        return " ".join(parts)

    def _sync_auto_save_hint(self) -> None:
        try:
            text = self._auto_save_hint_text()
            if text != self.var_auto_save_hint.get():
                self.var_auto_save_hint.set(text)
            state = "normal" if self.var_auto_save_enabled.get() else "disabled"
            self.ent_auto_save_interval.configure(state=state)
            self.ent_auto_save_keep.configure(state=state)
        except Exception:
            pass

    def _sync_auto_update_scheduler(self) -> None:
        """Pick up the auto update checkbox and time for the server on screen."""
        try:
            self.cfg = self._collect_vars_to_cfg()
            self._save_active_server_config(self.cfg)
        except Exception:
            return
        rt = self.rt
        enabled = bool(rt.cfg.auto_update_restart)
        # Only announced here. The tick clears the schedule when it is off and works out the
        # next run when it comes back on or the time changes, so nothing here can push an
        # update that is due right now to tomorrow.
        if self._auto_update_announced.get(rt.server_id, False) != enabled:
            self._auto_update_announced[rt.server_id] = enabled
            rt.logger.info(f"Auto Update & Restart scheduler {'enabled' if enabled else 'disabled'}.")

    # ---------------------------------------------------------------------
    # INI Visual Editor – helpers
    # ---------------------------------------------------------------------
    def _ini_visual_var_key(self, ini_file: str, section: str, key: str) -> str:
        """Build a unique dict key for a visual control variable."""
        return f"{ini_file}|{section}|{key}"

    def _ini_visual_build_settings(self, parent: ttk.Frame, settings: list, start_row: int, theme: dict) -> int:
        """Create labelled controls for a list of setting definitions and return next row index."""
        m = self.root
        row = start_row
        for s in settings:
            ini_file, section, key, label, desc, vtype, default, lo, hi = s[:9]
            step = s[9] if len(s) > 9 else (0.25 if vtype == "float" else 1)
            vk = self._ini_visual_var_key(ini_file, section, key)

            frame = ttk.Frame(parent)
            frame.grid(row=row, column=0, sticky="ew", pady=2, padx=2)
            frame.columnconfigure(1, weight=1)

            if vtype == "bool":
                var = tk.BooleanVar(master=m, value=(default.lower() == "true"))
                self._ini_visual_vars[vk] = var
                cb = ttk.Checkbutton(frame, text=label, variable=var,
                                     command=lambda _vk=vk: self._ini_visual_on_change(_vk))
                cb.grid(row=0, column=0, columnspan=2, sticky="w")
                ttk.Label(frame, text=desc, foreground=theme["muted"],
                          wraplength=600).grid(row=1, column=0, columnspan=2, sticky="w", padx=(20, 0))
            elif vtype == "float":
                var = tk.StringVar(master=m, value=default)
                self._ini_visual_vars[vk] = var
                ttk.Label(frame, text=label, width=28, anchor="w").grid(row=0, column=0, sticky="w")
                entry = ttk.Entry(frame, textvariable=var, width=14)
                entry.grid(row=0, column=1, sticky="w", padx=(4, 4))
                scale_var = tk.DoubleVar(master=m, value=float(default))
                scale = ttk.Scale(frame, variable=scale_var, from_=lo, to=hi,
                                  command=lambda v, sv=scale_var, tv=var, _vk=vk, _st=step:
                                  self._ini_visual_scale_to_entry(sv, tv, _vk, "float", _st))
                scale.grid(row=0, column=2, sticky="ew", padx=(0, 4))
                frame.columnconfigure(2, weight=1)
                self._ini_visual_vars[vk + "|scale"] = scale_var
                entry.bind("<KeyRelease>", lambda e, sv=scale_var, tv=var, _vk=vk:
                           self._ini_visual_entry_to_scale(tv, sv, _vk))
                ttk.Label(frame, text=desc, foreground=theme["muted"],
                          wraplength=600).grid(row=1, column=0, columnspan=3, sticky="w", padx=(4, 0))
            elif vtype == "int":
                var = tk.StringVar(master=m, value=default)
                self._ini_visual_vars[vk] = var
                ttk.Label(frame, text=label, width=28, anchor="w").grid(row=0, column=0, sticky="w")
                entry = ttk.Entry(frame, textvariable=var, width=14)
                entry.grid(row=0, column=1, sticky="w", padx=(4, 4))
                scale_var = tk.DoubleVar(master=m, value=float(default))
                scale = ttk.Scale(frame, variable=scale_var, from_=lo, to=hi,
                                  command=lambda v, sv=scale_var, tv=var, _vk=vk, _st=step:
                                  self._ini_visual_scale_to_entry(sv, tv, _vk, "int", _st))
                scale.grid(row=0, column=2, sticky="ew", padx=(0, 4))
                frame.columnconfigure(2, weight=1)
                self._ini_visual_vars[vk + "|scale"] = scale_var
                entry.bind("<KeyRelease>", lambda e, sv=scale_var, tv=var, _vk=vk:
                           self._ini_visual_entry_to_scale(tv, sv, _vk))
                ttk.Label(frame, text=desc, foreground=theme["muted"],
                          wraplength=600).grid(row=1, column=0, columnspan=3, sticky="w", padx=(4, 0))

            row += 1

        return row

    def _ini_visual_build_stats_table(self, parent: ttk.Frame, stats: list, start_row: int, theme: dict) -> int:
        """Build a compact grid of per-level stat multipliers."""
        m = self.root
        sep = ttk.Separator(parent, orient="horizontal")
        sep.grid(row=start_row, column=0, sticky="ew", pady=(10, 4))
        start_row += 1

        lbl = ttk.Label(parent, text="Per-Level Stat Multipliers", font=("", 10, "bold"))
        lbl.grid(row=start_row, column=0, sticky="w", padx=4)
        start_row += 1

        grid_frame = ttk.Frame(parent)
        grid_frame.grid(row=start_row, column=0, sticky="ew", padx=4, pady=4)

        per_stat_count = sum(1 for s in stats if "[0]" in s[2])
        is_dino = per_stat_count >= 4

        if is_dino:
            col_info = [
                ("Stat", ""),
                ("Wild", "Multiplier per wild level-up point."),
                ("Tamed", "Multiplier per tamed level-up point."),
                ("Tamed Add", "Flat bonus added after taming (stacks with Tamed)."),
                ("Affinity", "Bonus multiplier based on taming effectiveness."),
            ]
            for ci, (h, tip) in enumerate(col_info):
                ttk.Label(grid_frame, text=h, font=("", 9, "bold")).grid(
                    row=0, column=ci, sticky="w", padx=4, pady=(2, 0))
                if tip:
                    ttk.Label(grid_frame, text=tip, foreground=theme["muted"],
                              font=("", 7), wraplength=100).grid(
                        row=1, column=ci, sticky="w", padx=4, pady=(0, 4))

            data_row_offset = 2  # row 0 = header, row 1 = desc, data starts at 2

            for si, sn in enumerate(_STAT_NAMES):
                defs = _DINO_STAT_DEFAULTS.get(si, (1.0, 1.0, 1.0, 1.0))
                label_text = f"{sn}  (def: {defs[0]}, {defs[1]}, {defs[2]}, {defs[3]})"
                ttk.Label(grid_frame, text=label_text).grid(
                    row=si + data_row_offset, column=0, sticky="w", padx=4, pady=1)
                for gi, suffix in enumerate(["DinoWild", "DinoTamed", "DinoTamed_Add", "DinoTamed_Affinity"]):
                    key = f"PerLevelStatsMultiplier_{suffix}[{si}]"
                    sd = next((x for x in stats if x[2] == key), None)
                    if sd is None:
                        continue
                    ini_file, section = sd[0], sd[1]
                    default = sd[6]
                    vk = self._ini_visual_var_key(ini_file, section, key)
                    var = tk.StringVar(master=m, value=default)
                    self._ini_visual_vars[vk] = var
                    ent = ttk.Entry(grid_frame, textvariable=var, width=8)
                    ent.grid(row=si + data_row_offset, column=gi + 1, padx=2, pady=1)
                    ent.bind("<KeyRelease>", lambda e, _vk=vk: self._ini_visual_schedule_write(_vk))
        else:
            headers = ["Stat", "Multiplier"]
            for ci, h in enumerate(headers):
                ttk.Label(grid_frame, text=h, font=("", 9, "bold")).grid(
                    row=0, column=ci, sticky="w", padx=4, pady=2)

            for si, sn in enumerate(_STAT_NAMES):
                key = f"PerLevelStatsMultiplier_Player[{si}]"
                sd = next((x for x in stats if x[2] == key), None)
                if sd is None:
                    continue
                ini_file, section = sd[0], sd[1]
                default = sd[6]
                vk = self._ini_visual_var_key(ini_file, section, key)
                var = tk.StringVar(master=m, value=default)
                self._ini_visual_vars[vk] = var
                ttk.Label(grid_frame, text=f"{sn}  (def: 1.0)",
                          ).grid(row=si + 1, column=0, sticky="w", padx=4, pady=1)
                ent = ttk.Entry(grid_frame, textvariable=var, width=10)
                ent.grid(row=si + 1, column=1, padx=2, pady=1)
                ent.bind("<KeyRelease>", lambda e, _vk=vk: self._ini_visual_schedule_write(_vk))

        start_row += 1
        return start_row

    # -----------------------------------------------------------------
    # Stack Size Override table builder
    # -----------------------------------------------------------------
    def _ini_visual_build_stack_size_table(self, parent: ttk.Frame,
                                           start_row: int, theme: dict) -> int:
        """Build a table for ConfigOverrideItemMaxQuantity entries with item picker."""
        sep = ttk.Separator(parent, orient="horizontal")
        sep.grid(row=start_row, column=0, sticky="ew", pady=(10, 4))
        start_row += 1

        ttk.Label(parent, text="Per-Item Stack Size Overrides", font=("", 10, "bold")).grid(
            row=start_row, column=0, sticky="w", padx=4)
        start_row += 1

        ttk.Label(parent, text=(
            "Override stack sizes for individual items. "
            "Select an item from the dropdown, set the max quantity, "
            "and whether to ignore the global stack multiplier."),
            foreground=theme["muted"], wraplength=700).grid(
            row=start_row, column=0, sticky="w", padx=4, pady=(0, 6))
        start_row += 1

        vk = self._ini_visual_var_key("game", _GM, "ConfigOverrideItemMaxQuantity")

        frame = ttk.Frame(parent)
        frame.grid(row=start_row, column=0, sticky="ew", padx=2, pady=4)
        frame.columnconfigure(0, weight=1)

        # Input row
        input_frame = ttk.Frame(frame)
        input_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        ttk.Label(input_frame, text="Item:").grid(row=0, column=0, padx=(0, 4))
        item_cb = ttk.Combobox(input_frame, values=_ITEM_NAMES_SORTED, width=32)
        item_cb.grid(row=0, column=1, padx=(0, 8))
        self._make_searchable_cb(item_cb, list(_ITEM_NAMES_SORTED))

        ttk.Label(input_frame, text="Max Qty:").grid(row=0, column=2, padx=(0, 4))
        qty_var = tk.StringVar(value="100")
        ttk.Entry(input_frame, textvariable=qty_var, width=8).grid(row=0, column=3, padx=(0, 8))

        ttk.Label(input_frame, text="Ignore Multiplier:").grid(row=0, column=4, padx=(0, 4))
        ignore_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(input_frame, variable=ignore_var).grid(row=0, column=5, padx=(0, 8))

        # Listbox for entries
        list_frame = ttk.Frame(frame)
        list_frame.grid(row=1, column=0, sticky="ew", pady=(2, 2))
        list_frame.columnconfigure(0, weight=1)

        lb = tk.Listbox(list_frame, height=8, font=("Consolas", 9))
        lb.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=lb.yview)
        sb.grid(row=0, column=1, sticky="ns")
        lb.configure(yscrollcommand=sb.set)

        data = _SpawnListData(lb)
        self._ini_visual_vars[vk] = data

        def _refresh_listbox():
            lb.delete(0, "end")
            for raw in data.entries:
                display = self._stack_raw_to_display(raw)
                lb.insert("end", display)

        def add_entry():
            item_name = item_cb.get().strip()
            if not item_name:
                return
            item_info = _ITEM_DATA.get(item_name)
            if item_info:
                item_cls = item_info[0]
            else:
                item_cls = item_name  # allow raw class name as fallback
            qty = qty_var.get().strip() or "100"
            ignore = "true" if ignore_var.get() else "false"
            raw = (f'(ItemClassString="{item_cls}",'
                   f'Quantity=(MaxItemQuantity={qty},bIgnoreMultiplier={ignore}))')
            data.add(raw, self._stack_raw_to_display(raw))
            self._ini_visual_schedule_write(vk)

        def remove_entry():
            sel = lb.curselection()
            if not sel:
                return
            for s in reversed(sel):
                if s < len(data.entries):
                    del data.entries[s]
            _refresh_listbox()
            self._ini_visual_schedule_write(vk)

        ttk.Button(input_frame, text="Add", command=add_entry, width=6).grid(row=0, column=6)
        ttk.Button(frame, text="Remove Selected", command=remove_entry).grid(
            row=2, column=0, sticky="w", pady=(2, 0))

        start_row += 1
        return start_row

    def _ini_visual_build_harvest_table(self, parent: ttk.Frame,
                                        start_row: int, theme: dict) -> int:
        """Build a table for HarvestResourceItemAmountClassMultipliers entries."""
        sep = ttk.Separator(parent, orient="horizontal")
        sep.grid(row=start_row, column=0, sticky="ew", pady=(10, 4))
        start_row += 1

        ttk.Label(parent, text="Custom Harvest Amount Multipliers", font=("", 10, "bold")).grid(
            row=start_row, column=0, sticky="w", padx=4)
        start_row += 1

        ttk.Label(parent, text=(
            "Override harvest multipliers for individual resources. "
            "Click a row and use the slider or enter a value to change the multiplier. "
            "Resources at 1.0x are not written to the INI."),
            foreground=theme["muted"], wraplength=700).grid(
            row=start_row, column=0, sticky="w", padx=4, pady=(0, 6))
        start_row += 1

        vk = self._ini_visual_var_key("game", _GM, "HarvestResourceItemAmountClassMultipliers")

        frame = ttk.Frame(parent)
        frame.grid(row=start_row, column=0, sticky="nsew", padx=2, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        # Filter row
        filter_frame = ttk.Frame(frame)
        filter_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(filter_frame, text="Search:").pack(side="left", padx=(0, 4))
        filter_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=filter_var, width=30).pack(side="left", padx=(0, 8))
        ttk.Label(filter_frame, text="Only modified:").pack(side="left", padx=(0, 4))
        modified_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filter_frame, variable=modified_only_var).pack(side="left")

        # Treeview table
        tree_frame = ttk.Frame(frame)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("multiplier",)
        tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings",
                            height=12, selectmode="browse")
        tree.heading("#0", text="Resource", anchor="w")
        tree.heading("multiplier", text="Multiplier", anchor="center")
        tree.column("#0", width=350, minwidth=200)
        tree.column("multiplier", width=100, minwidth=80, anchor="center")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # State: resource_name -> multiplier
        harvest_state: dict[str, float] = {}
        for name in _HARVEST_NAMES_SORTED:
            harvest_state[name] = 1.0

        self._harvest_tree = tree
        self._ini_visual_vars[vk] = harvest_state

        def _populate_tree(*_args):
            tree.delete(*tree.get_children())
            ft = filter_var.get().lower()
            show_modified = modified_only_var.get()
            for name in _HARVEST_NAMES_SORTED:
                if ft and ft not in name.lower():
                    continue
                mult = harvest_state.get(name, 1.0)
                if show_modified and abs(mult - 1.0) < 0.001:
                    continue
                tree.insert("", "end", iid=name, text=name,
                            values=(f"{mult:.2f}x",))

        _populate_tree()
        filter_var.trace_add("write", _populate_tree)
        modified_only_var.trace_add("write", lambda *a: _populate_tree())

        # Edit controls below tree
        edit_frame = ttk.Frame(frame)
        edit_frame.grid(row=2, column=0, sticky="ew", pady=(4, 0))

        ttk.Label(edit_frame, text="Selected:").pack(side="left", padx=(0, 4))
        sel_label = ttk.Label(edit_frame, text="(none)", width=30, anchor="w")
        sel_label.pack(side="left", padx=(0, 8))

        ttk.Label(edit_frame, text="Multiplier:").pack(side="left", padx=(0, 4))
        mult_var = tk.DoubleVar(value=1.0)
        mult_entry = ttk.Entry(edit_frame, textvariable=mult_var, width=8)
        mult_entry.pack(side="left", padx=(0, 4))

        mult_scale = ttk.Scale(edit_frame, from_=0.0, to=10.0, variable=mult_var,
                               orient="horizontal", length=200)
        mult_scale.pack(side="left", padx=(0, 8))

        _current_selection = [None]
        _snapping = [False]

        def _on_tree_select(event=None):
            sel = tree.selection()
            if not sel:
                return
            name = sel[0]
            _current_selection[0] = name
            sel_label.configure(text=name)
            mult_var.set(harvest_state.get(name, 1.0))

        tree.bind("<<TreeviewSelect>>", _on_tree_select)

        def _on_mult_change(*_args):
            if _snapping[0]:
                return
            name = _current_selection[0]
            if not name:
                return
            try:
                val = round(round(mult_var.get() / 0.25) * 0.25, 2)
            except (ValueError, tk.TclError):
                return
            harvest_state[name] = val
            if tree.exists(name):
                tree.item(name, values=(f"{val:.2f}x",))
            _snapping[0] = True
            mult_var.set(val)
            _snapping[0] = False
            self._ini_visual_schedule_write(vk)

        mult_var.trace_add("write", _on_mult_change)

        def _apply_to_all():
            """Set all visible resources to the current multiplier."""
            try:
                val = round(round(mult_var.get() / 0.25) * 0.25, 2)
            except (ValueError, tk.TclError):
                return
            for item_id in tree.get_children():
                harvest_state[item_id] = val
                tree.item(item_id, values=(f"{val:.2f}x",))
            self._ini_visual_schedule_write(vk)

        ttk.Button(edit_frame, text="Apply to visible", command=_apply_to_all).pack(
            side="left", padx=(8, 0))

        def _reset_all():
            """Reset all resources to 1.0x."""
            for name in harvest_state:
                harvest_state[name] = 1.0
            _populate_tree()
            self._ini_visual_schedule_write(vk)

        ttk.Button(edit_frame, text="Reset all", command=_reset_all).pack(
            side="left", padx=(4, 0))

        start_row += 1
        return start_row

    @staticmethod
    def _stack_raw_to_display(raw: str) -> str:
        """Convert a raw ConfigOverrideItemMaxQuantity value to display text."""
        m_item = re.search(r'ItemClassString="([^"]*)"', raw)
        m_qty = re.search(r'MaxItemQuantity=(\d+)', raw)
        m_ignore = re.search(r'bIgnoreMultiplier=(true|false)', raw, re.IGNORECASE)
        cls = m_item.group(1) if m_item else "?"
        item = _CLASS_TO_ITEM_NAME.get(cls, cls)
        qty = m_qty.group(1) if m_qty else "?"
        ignore = m_ignore.group(1).lower() if m_ignore else "false"
        return f"{item}  qty={qty}  ignore={ignore}"

    @staticmethod
    def _spawn_raw_to_display(key: str, raw: str) -> str:
        """Convert a raw INI spawn value to a human-readable display string."""
        if key == "ConfigOverrideItemMaxQuantity":
            m_item = re.search(r'ItemClassString="([^"]*)"', raw)
            m_qty = re.search(r'MaxItemQuantity=(\d+)', raw)
            m_ignore = re.search(r'bIgnoreMultiplier=(true|false)', raw, re.IGNORECASE)
            item = m_item.group(1) if m_item else "?"
            qty = m_qty.group(1) if m_qty else "?"
            ignore = m_ignore.group(1).lower() if m_ignore else "false"
            return f"{item}  qty={qty}  ignore={ignore}"
        elif key == "NPCReplacements":
            m = re.search(r'FromClassName="([^"]*)".*?ToClassName="([^"]*)"', raw)
            if m:
                from_id, to_id = m.group(1), m.group(2)
                from_name = _ENTITY_ID_TO_NAME.get(from_id, from_id)
                to_name = _ENTITY_ID_TO_NAME.get(to_id, to_id) if to_id else "(disabled)"
                return f"{from_name}  \u2192  {to_name}"
        elif key == "DinoSpawnWeightMultipliers":
            m_tag = re.search(r'DinoNameTag=(\w+)', raw)
            m_w = re.search(r'SpawnWeightMultiplier=([\d.]+)', raw)
            m_l = re.search(r'SpawnLimitPercentage=([\d.]+)', raw)
            tag = m_tag.group(1) if m_tag else "?"
            w = m_w.group(1) if m_w else "?"
            lim = m_l.group(1) if m_l else "?"
            return f"{tag}   W={w}  Limit={lim}"
        else:
            m_c = re.search(r'NPCSpawnEntriesContainerClassString="([^"]*)"', raw)
            container = m_c.group(1) if m_c else "?"
            m_bp = re.search(r'NPCsToSpawnStrings=\("([^"]*)"', raw)
            if m_bp:
                bp = m_bp.group(1)
                creature = bp
                for name, cdata in _CREATURE_DATA.items():
                    if cdata[2] == bp:
                        creature = name
                        break
                return f"{container}  \u2190  {creature}"
            return f"{container}  \u2190  ..."
        return raw[:80]

    def _ini_visual_build_spawn_section(self, parent: ttk.Frame, spawn_keys: list,
                                        start_row: int, theme: dict) -> int:
        """Build a dino replacement table showing all creatures with spawnable/tameable toggles."""
        sep = ttk.Separator(parent, orient="horizontal")
        sep.grid(row=start_row, column=0, sticky="ew", pady=(10, 4))
        start_row += 1

        ttk.Label(parent, text="Dino Spawn Control", font=("", 10, "bold")).grid(
            row=start_row, column=0, sticky="w", padx=4)
        start_row += 1

        ttk.Label(parent, text=(
            "Control which creatures can spawn, be tamed, or be replaced. "
            "Uncheck Spawnable to disable a creature via NPCReplacements. "
            "Uncheck Tameable to prevent taming via PreventDinoTameClassNames. "
            "Use Replace With to swap a creature for another."),
            foreground=theme["muted"], wraplength=700).grid(
            row=start_row, column=0, sticky="w", padx=4, pady=(0, 6))
        start_row += 1

        vk_spawn = self._ini_visual_var_key("game", _GM, "NPCReplacements")
        vk_tame = self._ini_visual_var_key("game", _GM, "PreventDinoTameClassNames")

        # Container frame
        frame = ttk.Frame(parent)
        frame.grid(row=start_row, column=0, sticky="nsew", padx=2, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        # Search filter
        filter_frame = ttk.Frame(frame)
        filter_frame.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(filter_frame, text="Search:").pack(side="left", padx=(0, 4))
        filter_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=filter_var, width=30).pack(side="left", padx=(0, 8))
        ttk.Label(filter_frame, text="Only modified:").pack(side="left", padx=(0, 4))
        modified_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filter_frame, variable=modified_only_var).pack(side="left")

        # Treeview table
        tree_frame = ttk.Frame(frame)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("spawnable", "tameable", "replace_with")
        tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings",
                            height=15, selectmode="browse")
        tree.heading("#0", text="Creature", anchor="w")
        tree.heading("spawnable", text="Spawnable", anchor="center")
        tree.heading("tameable", text="Tameable", anchor="center")
        tree.heading("replace_with", text="Replace With", anchor="w")
        tree.column("#0", width=250, minwidth=150)
        tree.column("spawnable", width=80, minwidth=70, anchor="center")
        tree.column("tameable", width=80, minwidth=70, anchor="center")
        tree.column("replace_with", width=200, minwidth=120, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # Track state: creature_name -> {spawnable, tameable, replace_with}
        spawn_state: dict[str, dict] = {}
        for name in _CREATURE_NAMES_SORTED:
            spawn_state[name] = {"spawnable": True, "tameable": True, "replace_with": ""}

        # Store references
        self._dino_spawn_tree = tree
        self._dino_spawn_state = spawn_state
        self._dino_spawn_vk = vk_spawn
        self._dino_spawn_vk_tame = vk_tame
        self._ini_visual_vars[vk_spawn] = spawn_state
        self._ini_visual_vars[vk_tame] = spawn_state  # same dict, keyed by both

        creature_names_with_empty = [""] + list(_CREATURE_NAMES_SORTED)

        def _row_values(name: str):
            st = spawn_state.get(name, {"spawnable": True, "tameable": True, "replace_with": ""})
            sp = "\u2611" if st["spawnable"] else "\u2610"
            tm = "\u2611" if st["tameable"] else "\u2610"
            rw = st["replace_with"] if st["replace_with"] else ""
            return (sp, tm, rw)

        def _populate_tree(*_args):
            tree.delete(*tree.get_children())
            ft = filter_var.get().lower()
            show_modified = modified_only_var.get()
            for name in _CREATURE_NAMES_SORTED:
                if ft and ft not in name.lower():
                    continue
                st = spawn_state.get(name, {"spawnable": True, "tameable": True, "replace_with": ""})
                is_modified = (not st["spawnable"] or not st["tameable"] or st["replace_with"])
                if show_modified and not is_modified:
                    continue
                tree.insert("", "end", iid=name, text=name, values=_row_values(name))

        _populate_tree()
        filter_var.trace_add("write", _populate_tree)
        modified_only_var.trace_add("write", lambda *a: _populate_tree())

        def _on_click(event):
            col = tree.identify_column(event.x)
            item = tree.identify_row(event.y)
            if not item:
                return
            name = item
            st = spawn_state.get(name, {"spawnable": True, "tameable": True, "replace_with": ""})
            if col == "#1":  # spawnable column
                st["spawnable"] = not st["spawnable"]
                tree.item(name, values=_row_values(name))
                self._ini_visual_schedule_write(vk_spawn)
            elif col == "#2":  # tameable column
                st["tameable"] = not st["tameable"]
                tree.item(name, values=_row_values(name))
                self._ini_visual_schedule_write(vk_tame)

        tree.bind("<ButtonRelease-1>", _on_click)

        # Replace-with controls below tree
        replace_frame = ttk.Frame(frame)
        replace_frame.grid(row=2, column=0, sticky="ew", pady=(4, 0))

        ttk.Label(replace_frame, text="Selected:").pack(side="left", padx=(0, 4))
        sel_label = ttk.Label(replace_frame, text="(none)", width=25, anchor="w")
        sel_label.pack(side="left", padx=(0, 8))

        ttk.Label(replace_frame, text="Replace With:").pack(side="left", padx=(0, 4))
        replace_cb = ttk.Combobox(replace_frame, values=creature_names_with_empty, width=30)
        replace_cb.pack(side="left", padx=(0, 4))
        self._make_searchable_cb(replace_cb, creature_names_with_empty)

        _current_selection = [None]

        def _on_tree_select(event=None):
            sel = tree.selection()
            if not sel:
                return
            name = sel[0]
            _current_selection[0] = name
            sel_label.configure(text=name)
            st = spawn_state.get(name, {"spawnable": True, "tameable": True, "replace_with": ""})
            replace_cb.set(st["replace_with"])

        tree.bind("<<TreeviewSelect>>", _on_tree_select)

        def _on_replace_change(event=None):
            name = _current_selection[0]
            if not name:
                return
            new_val = replace_cb.get().strip()
            if new_val == name:
                new_val = ""
            st = spawn_state.get(name, {"spawnable": True, "tameable": True, "replace_with": ""})
            st["replace_with"] = new_val
            tree.item(name, values=_row_values(name))
            self._ini_visual_schedule_write(vk_spawn)

        replace_cb.bind("<<ComboboxSelected>>", _on_replace_change)

        def _apply_replace():
            name = _current_selection[0]
            if not name:
                return
            _on_replace_change()

        ttk.Button(replace_frame, text="Apply", command=_apply_replace, width=8).pack(
            side="left", padx=(4, 0))

        def _clear_replace():
            name = _current_selection[0]
            if not name:
                return
            replace_cb.set("")
            st = spawn_state.get(name, {"spawnable": True, "tameable": True, "replace_with": ""})
            st["replace_with"] = ""
            tree.item(name, values=_row_values(name))
            self._ini_visual_schedule_write(vk_spawn)

        ttk.Button(replace_frame, text="Clear", command=_clear_replace, width=8).pack(
            side="left", padx=(4, 0))

        start_row += 1
        return start_row

    @staticmethod
    def _make_searchable_cb(cb: ttk.Combobox, full_values: list) -> None:
        """Bind KeyRelease so the combobox filters its drop-down as the user types."""
        def _filter(event=None):
            if event and event.keysym in ('Up', 'Down', 'Return', 'Escape',
                                          'Tab', 'Shift_L', 'Shift_R',
                                          'Control_L', 'Control_R',
                                          'Alt_L', 'Alt_R'):
                return
            typed = cb.get()
            pos = cb.index(tk.INSERT)
            if typed:
                cb['values'] = [v for v in full_values if typed.lower() in v.lower()]
            else:
                cb['values'] = full_values
            cb.icursor(pos)
        cb.bind('<KeyRelease>', _filter)

    def _build_spawn_npc_replacements(self, parent: ttk.Frame, vk: str, key: str) -> None:
        """Legacy - unused, spawn section now uses dino table."""
        pass

    def _build_spawn_weight_multipliers(self, parent: ttk.Frame, vk: str, key: str) -> None:
        """Legacy - unused, spawn section now uses dino table."""
        pass

    def _build_spawn_container_op(self, parent: ttk.Frame, vk: str, key: str) -> None:
        """Legacy - unused, spawn section now uses dino table."""
        pass

    def _ini_visual_scale_to_entry(self, scale_var: tk.DoubleVar, text_var: tk.StringVar,
                                   vk: str, vtype: str, step: float = 0.25) -> None:
        """Scale slider changed -> snap to step, update entry and schedule INI write."""
        if self._ini_visual_loading:
            return
        val = scale_var.get()
        if step > 0:
            val = round(round(val / step) * step, 10)
            scale_var.set(val)
        if vtype == "int":
            text_var.set(str(int(round(val))))
        else:
            text_var.set(f"{val:.6f}".rstrip("0").rstrip("."))
        self._ini_visual_schedule_write(vk)

    def _ini_visual_entry_to_scale(self, text_var: tk.StringVar, scale_var: tk.DoubleVar,
                                   vk: str) -> None:
        """Entry changed -> update scale and schedule INI write."""
        if self._ini_visual_loading:
            return
        try:
            scale_var.set(float(text_var.get()))
        except (ValueError, tk.TclError):
            pass
        self._ini_visual_schedule_write(vk)

    def _ini_visual_on_change(self, vk: str) -> None:
        """Checkbox toggles directly schedule write."""
        if self._ini_visual_loading:
            return
        self._ini_visual_schedule_write(vk)

    def _ini_visual_schedule_write(self, _vk: str) -> None:
        """Debounced write of all dirty visual settings to staged INI."""
        if self._ini_visual_debounce_id:
            try:
                self.root.after_cancel(self._ini_visual_debounce_id)
            except Exception:
                pass
        self._ini_visual_write_for = self.active_server_id
        self._ini_visual_debounce_id = self.root.after(INI_VISUAL_DEBOUNCE_MS, self._ini_visual_write_all)

    def _ini_visual_load(self, target: str) -> None:
        """Load an INI file and keep both docs available for visual editor."""
        self._ini_load_target(target)
        self._ini_visual_refresh_all()

    def _ini_visual_get_doc(self, ini_file: str) -> Optional["IniDocument"]:
        """Return the staged IniDocument for gus or game, loading if needed."""
        self.cfg = self._collect_vars_to_cfg()
        self._save_active_server_config(self.cfg)
        server_dir = Path(self.cfg.server_dir)
        ensure_dir(server_root(self.app_base, self.active_server_id) / BASELINE_DIR_NAME)
        paths = ini_stage_paths(self.app_base, self.active_server_id, server_dir, ini_file)
        if not paths.stage.exists() and not paths.live.exists():
            return None
        ensure_ini_staging_synced(paths, server_running=self._install_running(self.cfg.server_dir), logger=self.logger)
        return read_ini(paths.stage)

    def _ini_visual_refresh_all(self) -> None:
        """Read current INI values and populate all visual controls."""
        self._ini_visual_loading = True
        try:
            gus_doc = self._ini_visual_get_doc("gus")
            game_doc = self._ini_visual_get_doc("game")

            gus_map = gus_doc.get_last_value_map() if gus_doc else {}
            game_map = game_doc.get_last_value_map() if game_doc else {}

            for vk, var in self._ini_visual_vars.items():
                if "|scale" in vk:
                    continue
                parts = vk.split("|", 2)
                if len(parts) != 3:
                    continue
                ini_file, section, key = parts

                if isinstance(var, tk.Text):
                    doc = gus_doc if ini_file == "gus" else game_doc
                    if doc is None:
                        continue
                    vals = doc.get_all_values(section, key)
                    var.delete("1.0", "end")
                    if vals:
                        var.insert("1.0", "\n".join(vals))
                    continue

                if isinstance(var, _SpawnListData):
                    doc = gus_doc if ini_file == "gus" else game_doc
                    if doc is None:
                        continue
                    vals = doc.get_all_values(section, key)
                    var.clear()
                    for raw_val in vals:
                        display = self._spawn_raw_to_display(key, raw_val)
                        var.add(raw_val, display)
                    continue

                if isinstance(var, dict) and key in ("NPCReplacements", "PreventDinoTameClassNames"):
                    # Only process once (NPCReplacements is the primary key)
                    if key == "PreventDinoTameClassNames":
                        continue
                    doc = gus_doc if ini_file == "gus" else game_doc
                    if doc is None:
                        continue
                    for name in var:
                        var[name] = {"spawnable": True, "tameable": True, "replace_with": ""}
                    vals = doc.get_all_values(section, key)
                    for raw_val in vals:
                        m = re.search(r'FromClassName="([^"]*)".*?ToClassName="([^"]*)"', raw_val)
                        if m:
                            from_id = m.group(1)
                            to_id = m.group(2)
                            name = _ENTITY_ID_TO_NAME.get(from_id)
                            if name and name in var:
                                if to_id == "":
                                    var[name]["spawnable"] = False
                                else:
                                    to_name = _ENTITY_ID_TO_NAME.get(to_id, to_id)
                                    var[name]["replace_with"] = to_name
                    tame_vals = doc.get_all_values(section, "PreventDinoTameClassNames")
                    for raw_val in tame_vals:
                        entity_id = raw_val.strip().strip('"')
                        name = _ENTITY_ID_TO_NAME.get(entity_id)
                        if name and name in var:
                            var[name]["tameable"] = False
                    if hasattr(self, '_dino_spawn_tree'):
                        tree = self._dino_spawn_tree
                        for item_id in tree.get_children():
                            st = var.get(item_id, {"spawnable": True, "tameable": True, "replace_with": ""})
                            sp = "\u2611" if st["spawnable"] else "\u2610"
                            tm = "\u2611" if st["tameable"] else "\u2610"
                            rw = st["replace_with"] if st["replace_with"] else ""
                            tree.item(item_id, values=(sp, tm, rw))
                    continue

                if isinstance(var, dict) and key == "HarvestResourceItemAmountClassMultipliers":
                    doc = gus_doc if ini_file == "gus" else game_doc
                    if doc is None:
                        continue
                    # Reset all to 1.0
                    for name in var:
                        var[name] = 1.0
                    vals = doc.get_all_values(section, key)
                    for raw_val in vals:
                        m = re.search(r'ClassName="([^"]*)".*?Multiplier=([\d.]+)', raw_val)
                        if m:
                            cls = m.group(1)
                            mult = float(m.group(2))
                            res_name = _HARVEST_CLASS_TO_NAME.get(cls)
                            if res_name and res_name in var:
                                var[res_name] = mult
                    # Refresh tree display if available
                    if hasattr(self, '_harvest_tree'):
                        tree = self._harvest_tree
                        for item_id in tree.get_children():
                            mult = var.get(item_id, 1.0)
                            tree.item(item_id, values=(f"{mult:.2f}x",))
                    continue

                val_map = gus_map if ini_file == "gus" else game_map
                current = val_map.get(section, {}).get(key, None)

                if current is None:
                    continue  # leave default

                if isinstance(var, tk.BooleanVar):
                    var.set(current.strip().lower() == "true")
                elif isinstance(var, tk.StringVar):
                    var.set(current)
                    sk = vk + "|scale"
                    if sk in self._ini_visual_vars:
                        try:
                            self._ini_visual_vars[sk].set(float(current))
                        except (ValueError, tk.TclError):
                            pass
        except Exception:
            pass
        finally:
            self._ini_visual_loading = False

    def _ini_visual_write_all(self) -> None:
        """Write all visual control values to staged INI files."""
        self._ini_visual_debounce_id = None
        if getattr(self, "_ini_visual_write_for", self.active_server_id) != self.active_server_id:
            return  # the edits were for a profile no longer on screen; never write them into this one

        self.cfg = self._collect_vars_to_cfg()
        self._save_active_server_config(self.cfg)
        server_dir = Path(self.cfg.server_dir)
        ensure_dir(server_root(self.app_base, self.active_server_id) / BASELINE_DIR_NAME)

        gus_paths = ini_stage_paths(self.app_base, self.active_server_id, server_dir, "gus")
        game_paths = ini_stage_paths(self.app_base, self.active_server_id, server_dir, "game")

        install_running = self._install_running(self.cfg.server_dir)
        ensure_ini_staging_synced(gus_paths, server_running=install_running, logger=self.logger)
        ensure_ini_staging_synced(game_paths, server_running=install_running, logger=self.logger)

        gus_doc = read_ini(gus_paths.stage)
        game_doc = read_ini(game_paths.stage)

        gus_dirty = False
        game_dirty = False

        for vk, var in self._ini_visual_vars.items():
            if "|scale" in vk:
                continue
            parts = vk.split("|", 2)
            if len(parts) != 3:
                continue
            ini_file, section, key = parts

            doc = gus_doc if ini_file == "gus" else game_doc

            if isinstance(var, tk.Text):
                content = var.get("1.0", "end").strip()
                doc.remove_all_kv(section, key)
                if content:
                    for line in content.split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        if line.lower().startswith(key.lower() + "="):
                            line = line[len(key) + 1:]
                        doc.append_kv(section, key, line)
                if ini_file == "gus":
                    gus_dirty = True
                else:
                    game_dirty = True
                continue

            if isinstance(var, _SpawnListData):
                doc.remove_all_kv(section, key)
                for raw_val in var.entries:
                    raw_val = raw_val.strip()
                    if raw_val:
                        doc.append_kv(section, key, raw_val)
                if ini_file == "gus":
                    gus_dirty = True
                else:
                    game_dirty = True
                continue

            if isinstance(var, dict) and key in ("NPCReplacements", "PreventDinoTameClassNames"):
                # Only process once (NPCReplacements is the primary trigger)
                if key == "PreventDinoTameClassNames":
                    continue
                doc.remove_all_kv(section, "NPCReplacements")
                for name, st in var.items():
                    entity_id = _CREATURE_DATA.get(name, ("", "", ""))[0]
                    if not entity_id:
                        continue
                    if not st["spawnable"]:
                        raw = f'(FromClassName="{entity_id}",ToClassName="")'
                        doc.append_kv(section, "NPCReplacements", raw)
                    elif st["replace_with"]:
                        to_data = _CREATURE_DATA.get(st["replace_with"])
                        if to_data:
                            raw = f'(FromClassName="{entity_id}",ToClassName="{to_data[0]}")'
                            doc.append_kv(section, "NPCReplacements", raw)
                doc.remove_all_kv(section, "PreventDinoTameClassNames")
                for name, st in var.items():
                    if not st["tameable"]:
                        entity_id = _CREATURE_DATA.get(name, ("", "", ""))[0]
                        if entity_id:
                            doc.append_kv(section, "PreventDinoTameClassNames", f'"{entity_id}"')
                if ini_file == "gus":
                    gus_dirty = True
                else:
                    game_dirty = True
                continue

            if isinstance(var, dict) and key == "HarvestResourceItemAmountClassMultipliers":
                doc.remove_all_kv(section, key)
                for name, mult in var.items():
                    if abs(mult - 1.0) > 0.001:
                        cls = _HARVEST_RESOURCES.get(name, "")
                        if cls:
                            raw = f'(ClassName="{cls}",Multiplier={mult:.6f}'.rstrip("0").rstrip(".") + ")"
                            doc.append_kv(section, key, raw)
                if ini_file == "gus":
                    gus_dirty = True
                else:
                    game_dirty = True
                continue

            if isinstance(var, tk.BooleanVar):
                val = "True" if var.get() else "False"
            elif isinstance(var, tk.StringVar):
                val = var.get()
            else:
                continue

            doc.set(section, key, val)
            if ini_file == "gus":
                gus_dirty = True
            else:
                game_dirty = True

        if gus_dirty:
            write_ini(gus_paths.stage, gus_doc)
        if game_dirty:
            write_ini(game_paths.stage, game_doc)

        if self._ini_loaded_target == "gus" and gus_dirty:
            self._ini_doc = read_ini(gus_paths.stage)
            self._ini_refresh_tree()
        elif self._ini_loaded_target == "game" and game_dirty:
            self._ini_doc = read_ini(game_paths.stage)
            self._ini_refresh_tree()

        self._set_status("INI settings staged")

    # ---------------------------------------------------------------------
    # INI Editor
    # ---------------------------------------------------------------------
    def _ini_reload_from_staging(self) -> None:
        """Re-read the staged INIs into the editor, without saving the form first."""
        try:
            if self._ini_loaded_target and self._ini_paths_current is not None:
                self._ini_doc = read_ini(self._ini_paths_current.stage)
                self._ini_refresh_tree()
        except Exception as e:
            self.logger.info(f"Reloading the INI Editor failed: {e}")
            self._reset_ini_state()  # better unloaded than holding stale values
        with contextlib.suppress(Exception):
            self._ini_visual_refresh_all()

    def _ini_load_target(self, target: str) -> None:
        self.cfg = self._collect_vars_to_cfg()
        self._save_active_server_config(self.cfg)

        server_dir = Path(self.cfg.server_dir)
        ensure_dir(server_root(self.app_base, self.active_server_id) / BASELINE_DIR_NAME)

        paths = ini_stage_paths(self.app_base, self.active_server_id, server_dir, target)
        ensure_ini_staging_synced(paths, server_running=self._install_running(self.cfg.server_dir), logger=self.logger)

        self._ini_loaded_target = target
        self._ini_paths_current = paths
        self._ini_doc = read_ini(paths.stage)

        if target == "gus":
            self.lbl_ini_target.configure(text=f"Editing STAGED GameUserSettings.ini  |  {paths.stage}")
        else:
            self.lbl_ini_target.configure(text=f"Editing STAGED Game.ini  |  {paths.stage}")

        if not self.var_ini_add_section.get().strip():
            self.var_ini_add_section.set("ServerSettings")

        self._ini_refresh_tree()

    def load_gameusersettings(self) -> None:
        self._ini_load_target("gus")

    def load_game_ini(self) -> None:
        self._ini_load_target("game")

    def open_loaded_ini(self) -> None:
        if not self._ini_paths_current:
            return
        paths = self._ini_paths_current
        open_in_explorer(paths.stage, select_file=True)
        open_in_explorer(paths.live, select_file=True)

    def _ini_resync_from_upstream(self) -> None:
        if not self._ini_paths_current:
            return
        ensure_ini_staging_synced(self._ini_paths_current, server_running=self._install_running(self.cfg.server_dir), logger=self.logger)
        self._ini_doc = read_ini(self._ini_paths_current.stage)
        self._ini_refresh_tree()
        self._set_status("INI resynced")

    def _ini_refresh_tree(self) -> None:
        self.tree_ini.delete(*self.tree_ini.get_children())
        self._ini_items_index.clear()

        if not self._ini_doc:
            return

        kv_by_section = self._ini_doc.kv_entries_by_section()
        filt = (self.var_ini_filter.get() or "").strip().lower()

        idx_to_iid: Dict[int, str] = {}
        selected_idx = self._ini_selected_line_idx

        for section, entries in kv_by_section.items():
            visible: List[Tuple[int, str, str]] = []
            for idx, key, val in entries:
                hay = f"{section}\n{key}\n{val}".lower()
                if filt and filt not in hay:
                    continue
                visible.append((idx, key, val))

            if not visible:
                continue

            sec_iid = self.tree_ini.insert("", "end", text=f"[{section}]", values=("",))
            self.tree_ini.item(sec_iid, open=True)

            counts: Dict[str, int] = {}
            totals: Dict[str, int] = {}
            for _, k, _ in entries:
                totals[k.lower()] = totals.get(k.lower(), 0) + 1

            for idx, key, val in visible:
                k_l = key.lower()
                counts[k_l] = counts.get(k_l, 0) + 1
                label = key if totals.get(k_l, 0) <= 1 else f"{key} [{counts[k_l]}]"
                iid = self.tree_ini.insert(sec_iid, "end", text=label, values=(val,))
                self._ini_items_index[iid] = idx
                idx_to_iid[idx] = iid

        if selected_idx is not None and selected_idx in idx_to_iid:
            iid = idx_to_iid[selected_idx]
            try:
                self.tree_ini.selection_set(iid)
                self.tree_ini.see(iid)
            except Exception:
                pass

    def _ini_on_select(self) -> None:
        sel = self.tree_ini.selection()
        if not sel:
            return
        iid = sel[0]
        if iid not in self._ini_items_index:
            return
        idx = self._ini_items_index[iid]
        assert self._ini_doc is not None

        if not (0 <= idx < len(self._ini_doc.lines)):
            return
        line = self._ini_doc.lines[idx]
        if line.kind != "kv":
            return

        self._ini_selected_line_idx = idx
        self.var_ini_section.set(line.section)
        self.var_ini_key.set(line.key)

        if line.section.strip():
            self.var_ini_add_section.set(line.section)

        val = line.value
        t = self._detect_value_type(str(val))
        if t == "bool":
            self.ent_ini_value.grid_forget()
            self.cmb_ini_bool.grid(row=2, column=1, sticky="ew", padx=6, pady=(10, 0))
            self.var_ini_bool.set("True" if str(val).strip().lower() == "true" else "False")
            self.scale_ini.configure(state="disabled")
            self.canvas_ticks.delete("all")
        else:
            self.cmb_ini_bool.grid_forget()
            self.ent_ini_value.grid(row=2, column=1, sticky="ew", padx=6, pady=(10, 0))
            self.var_ini_value.set(str(val))

            if t in ("int", "float"):
                lo, hi = self._guess_numeric_range(str(val))
                self.scale_ini.configure(from_=lo, to=hi, state="normal")
                try:
                    self.var_ini_scale.set(float(val))
                except Exception:
                    self.var_ini_scale.set(float(lo))
                self._draw_ticks(lo, hi)
            else:
                self.scale_ini.configure(state="disabled")
                self.canvas_ticks.delete("all")

    def _detect_value_type(self, s: str) -> str:
        v = s.strip()
        if v.lower() in ("true", "false"):
            return "bool"
        try:
            int(v)
            return "int"
        except Exception:
            pass
        try:
            float(v)
            return "float"
        except Exception:
            return "str"

    def _guess_numeric_range(self, s: str) -> Tuple[float, float]:
        try:
            v = float(s)
        except Exception:
            return (0.0, 10.0)
        if -2.0 <= v <= 2.0:
            return (-5.0, 5.0)
        if 0.0 <= v <= 5.0:
            return (0.0, 10.0)
        if 0.0 <= v <= 100.0:
            return (0.0, 200.0)
        return (min(v * 0.5, v - 1.0), max(v * 1.5, v + 1.0))

    def _draw_ticks(self, lo: float, hi: float) -> None:
        self.canvas_ticks.delete("all")
        try:
            w = self.canvas_ticks.winfo_width()
            if w <= 1:
                self.root.after(50, lambda: self._draw_ticks(lo, hi))
                return
        except Exception:
            return

        w = self.canvas_ticks.winfo_width()
        h = int(self.canvas_ticks["height"])
        n = 11
        for i in range(n):
            x = int((w - 2) * (i / (n - 1))) + 1
            tick_h = 10 if i in (0, n - 1) or i == (n - 1) // 2 else 6
            self.canvas_ticks.create_line(x, h, x, h - tick_h)

    def _ini_scale_changed(self, _=None) -> None:
        if self._ini_selected_line_idx is None:
            return
        cur = self.var_ini_value.get().strip()
        t = self._detect_value_type(cur)
        if t == "int":
            self.var_ini_value.set(str(int(round(self.var_ini_scale.get()))))
        elif t == "float":
            self.var_ini_value.set(f"{self.var_ini_scale.get():.6f}".rstrip("0").rstrip("."))
        self._ini_schedule_apply()

    def _ini_schedule_apply(self) -> None:
        if self._ini_apply_after_id:
            try:
                self.root.after_cancel(self._ini_apply_after_id)
            except Exception:
                pass
        self._ini_apply_after_id = self.root.after(INI_APPLY_DEBOUNCE_MS, self._ini_apply_now)

    def _ini_apply_now(self) -> None:
        self._ini_apply_after_id = None
        if not self._ini_doc or not self._ini_loaded_target or not self._ini_paths_current:
            return
        if self._ini_selected_line_idx is None:
            return

        if self.cmb_ini_bool.winfo_ismapped():
            val = self.var_ini_bool.get().strip()
        else:
            val = self.var_ini_value.get()

        self._ini_doc.update_value_at(self._ini_selected_line_idx, val)
        write_ini(self._ini_paths_current.stage, self._ini_doc)

        self._ini_refresh_tree()
        self._set_status("INI staged")

    def _ini_append_line(self) -> None:
        if not self._ini_doc or not self._ini_paths_current:
            return
        sec = self.var_ini_add_section.get().strip()
        key = self.var_ini_add_key.get().strip()
        val = self.var_ini_add_value.get()

        if not sec or not key:
            messagebox.showerror("INI", "Section and Key are required.")
            return

        idx = self._ini_doc.append_kv(sec, key, val)
        if idx < 0:
            return

        write_ini(self._ini_paths_current.stage, self._ini_doc)
        self._ini_selected_line_idx = idx
        self._ini_refresh_tree()
        self._set_status("INI line appended")

    def _ini_set_line(self) -> None:
        if not self._ini_doc or not self._ini_paths_current:
            return
        sec = self.var_ini_add_section.get().strip()
        key = self.var_ini_add_key.get().strip()
        val = self.var_ini_add_value.get()

        if not sec or not key:
            messagebox.showerror("INI", "Section and Key are required.")
            return

        self._ini_doc.set(sec, key, val)
        write_ini(self._ini_paths_current.stage, self._ini_doc)
        self._ini_refresh_tree()
        self._set_status("INI key set")

    def _ini_delete_selected(self) -> None:
        if not self._ini_doc or not self._ini_paths_current:
            return
        if self._ini_selected_line_idx is None:
            return

        idx = self._ini_selected_line_idx
        self._ini_doc.delete_at(idx)
        write_ini(self._ini_paths_current.stage, self._ini_doc)

        self._ini_selected_line_idx = None
        self.var_ini_section.set("")
        self.var_ini_key.set("")
        self.var_ini_value.set("")
        self.var_ini_bool.set("")
        self._ini_refresh_tree()
        self._set_status("INI line deleted")

# =============================================================================
# ENTRYPOINT
# =============================================================================

_INSTANCE_MUTEX: Any = None


def acquire_single_instance(app_base: Path) -> bool:
    """One manager per storage folder: a second one would reattach to the first one's
    servers and run their saves, restarts and updates a second time."""
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = wintypes.HANDLE
        k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        handle = k.CreateMutexW(None, False, f"Local\\ARKASAManager-{_instance_tag(app_base)}")
        err = ctypes.get_last_error()
        if not handle:
            # Access denied: a manager running with more rights holds it.
            return err != 5
        if err == 183:  # ERROR_ALREADY_EXISTS
            return False
        _INSTANCE_MUTEX = handle  # held for the life of the process
        return True
    except Exception:
        return True


_SHOW_EVENT: Any = None


def _instance_tag(app_base: Path) -> str:
    return hashlib.sha1(os.path.normcase(str(app_base)).encode("utf-8")).hexdigest()[:16]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]


_SHOW_WAIT: Any = None


def create_show_event(app_base: Path) -> Any:
    """The running manager's "show your window" signal (a named auto-reset event)."""
    global _SHOW_EVENT, _SHOW_WAIT
    if os.name != "nt":
        return None
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateEventW.restype = wintypes.HANDLE
        k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.LocalFree.argtypes = [ctypes.c_void_p]
        # Anyone in this session may set it (it only shows the window), and a medium-integrity
        # label lets a normal launch reach a manager that runs as Administrator.
        sd = ctypes.c_void_p()
        sa = None
        try:
            a = ctypes.WinDLL("advapi32", use_last_error=True)
            a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
            if a.ConvertStringSecurityDescriptorToSecurityDescriptorW("D:(A;;0x100002;;;WD)S:(ML;;NW;;;ME)", 1, ctypes.byref(sd), None):
                sa = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), sd, False)
        except Exception:
            sa = None
        try:
            name = f"Local\\ARKASAManager-show-{_instance_tag(app_base)}"
            _SHOW_EVENT = k.CreateEventW(ctypes.byref(sa) if sa is not None else None, False, False, name) or None
            if _SHOW_EVENT is None and ctypes.get_last_error() == 5:
                # It still exists (a launch has it open this instant), and its DACL refuses
                # the full access CreateEvent asks for: wait and modify are all we need.
                k.OpenEventW.restype = wintypes.HANDLE
                k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
                _SHOW_EVENT = k.OpenEventW(0x100002, False, name) or None
        finally:
            if sd:
                k.LocalFree(sd)
        _SHOW_WAIT = k.WaitForSingleObject if _SHOW_EVENT else None
    except Exception:
        _SHOW_EVENT = None
    return _SHOW_EVENT


def close_show_event() -> None:
    """Stop answering second launches: this manager is on its way out, and a launch now
    should not be swallowed by a window that is about to close."""
    global _SHOW_EVENT, _SHOW_WAIT
    handle, _SHOW_EVENT, _SHOW_WAIT = _SHOW_EVENT, None, None
    if handle:
        with contextlib.suppress(Exception):
            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle))


def show_event_signaled() -> bool:
    """True once each time a second launch asked this manager to show itself."""
    if _SHOW_EVENT is None or _SHOW_WAIT is None:
        return False
    try:
        return _SHOW_WAIT(_SHOW_EVENT, 0) == 0
    except Exception:
        return False


def signal_show_event(app_base: Path) -> bool:
    """Ask the manager that is already running to show its window; False if it cannot be asked."""
    if os.name != "nt":
        return False
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenEventW.restype = wintypes.HANDLE
        k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k.SetEvent.argtypes = [wintypes.HANDLE]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k.OpenEventW(0x0002, False, f"Local\\ARKASAManager-show-{_instance_tag(app_base)}")  # EVENT_MODIFY_STATE
        if not handle:
            return False
        try:
            # This launch may bring a window forward; let the running manager do it.
            with contextlib.suppress(Exception):
                ctypes.windll.user32.AllowSetForegroundWindow(wintypes.DWORD(0xFFFFFFFF))  # ASFW_ANY
            return bool(k.SetEvent(handle))
        finally:
            k.CloseHandle(handle)
    except Exception:
        return False


def launch_gui(cli_options: Optional[RuntimeCliOptions] = None) -> None:
    opts = cli_options or RuntimeCliOptions()

    set_windows_appusermodel_id(APP_USERMODEL_ID)
    configure_dpi_awareness()

    root = tk.Tk()
    apply_tk_scaling(root)
    configure_modern_theme(root)
    apply_initial_window_geometry(root, 1220, 780)

    apply_window_icon(root)

    # Already running (perhaps in the background): show it, before any question.
    with contextlib.suppress(Exception):
        if signal_show_event(resolve_storage_root()):
            root.destroy()
            return

    if os.name == "nt" and not is_admin() and not opts.no_admin_prompt:
        try:
            if messagebox.askyesno(
                "Administrator rights recommended",
                "This app installs dependencies and manages server files.\n"
                "Run as Administrator for full functionality.\n\n"
                "Relaunch with admin rights now?"
            ):
                if relaunch_as_admin():
                    root.destroy()
                    return
        except Exception:
            pass

    try:
        app_base = resolve_storage_root()
        ensure_dir(app_base)
    except PermissionError as e:
        if os.name == "nt" and not is_admin() and not opts.no_admin_prompt:
            try:
                if messagebox.askyesno(
                    "Shared storage requires admin",
                    f"{e}\n\nRelaunch with admin rights to initialize shared storage?",
                ):
                    if relaunch_as_admin():
                        root.destroy()
                        return
            except Exception:
                pass
        messagebox.showerror("Storage unavailable", str(e))
        root.destroy()
        return

    if not acquire_single_instance(app_base):
        # Already running (perhaps in the background): bring that window up instead.
        if not signal_show_event(app_base):
            messagebox.showwarning(
                "Already open",
                f"{APP_NAME} is already open (it may be minimized or running in the background). Two managers "
                "on the same servers would both save, restart and update them; use the one that is open.",
            )
        root.destroy()
        return
    create_show_event(app_base)

    _ = ServerManagerApp(root, app_base)
    root.mainloop()


if __name__ == "__main__":
    launch_gui(parse_runtime_cli_options(sys.argv[1:]))

#!/usr/bin/env python3
"""
Single window front end for the BO2 PS3 PSN freeze fix.

Point it at a game folder. It reads the signing parameters back out of your own
three files, decrypts them, applies the patch from patch-bo2.py, re-signs using
the values it read rather than assumed ones, and verifies every result before
anything lands in the output folder.

https://github.com/setsid
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from collections import namedtuple
from tkinter import filedialog, font as tkfont, messagebox, ttk

PROJECT_URL = "https://github.com/setsid"
# Windows groups taskbar buttons by application id. Without one of its own, a
# Python program is grouped under the interpreter and shows its icon there
# however the window is set up.
APP_ID = "setsid.bo2-psn-fix"
VERSION = "1.0"
# scetool is naehrwert's, bundled here so the program works out of the
# box. This project is not affiliated with it.
BUNDLED_SCETOOL = os.path.join("tools", "scetool")
CONFIG_DIR_NAME = "bo2-psn-fix"

# The game spawns the two .self files with this klicensee rather than the free
# one, so decrypting and re-signing them both need it. It is the same on every
# region. EBOOT.BIN is free and must not be given one.
KLICENSEE = "8C10AC1473DF38ADD7A4F2EE8C838DAB"

# Keeps scetool from flashing a console window on every one of the many calls.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# An NPDRM app type is either an executable or an sprx. EBOOT.BIN is the former
# and the two selfs are the latter; which of the two spellings a given install
# carries is read from the file rather than assumed.
EXEC_TYPES = ("EXEC", "UEXEC")
SPRX_TYPES = ("SPRX", "USPRX")

Slot = namedtuple("Slot",
                  "key label out_name needs_klicensee family family_name role")

SLOTS = (
    Slot("eboot", "EBOOT.BIN", "EBOOT.BIN", False, EXEC_TYPES,
         "an executable", ""),
    Slot("spzm", "t6_ps3f.self", "t6_ps3f.self", True, SPRX_TYPES,
         "an sprx", "campaign/zombies"),
    Slot("mp", "t6mp_ps3f.self", "t6mp_ps3f.self", True, SPRX_TYPES,
         "an sprx", "multiplayer"),
)

# EBOOT.BIN has no role of its own to show, so its app type is spelled out.
APP_TYPE_WORDS = {"UEXEC": "update executable", "EXEC": "executable",
                  "USPRX": "update sprx", "SPRX": "sprx"}

TICK = "\u2713"

# First column values that mean the row is not usable, whatever else is shown.
BAD_ROWS = ("not in this folder", "could not be read", "header not understood")

DEFAULT_SETTINGS = {
    "scetool": "",
    "write_log": True,
    "open_when_done": True,
    "verify": True,
}

# Read header, decrypt, then sign and verify, for each of the three files. The
# patching itself is in memory and takes no measurable time.
STAGES = ("reading header", "decrypting", "signing and verifying")
TOTAL_STEPS = len(SLOTS) * len(STAGES)

# Fields are found by their own label, anchored to the start of a line, and
# nothing else. Which block a field sits in and how many blocks precede it vary
# between files: the NPDRM values live inside the third "Control Info" block on
# a retail file, and an earlier version of this looked for an "NPDRM Info"
# heading that does not exist, so every one of them was silently dropped.
#
# The one exception is the application version, whose label is the bare word
# "Version". That also appears in the SCE header and the ELF header, so it is
# the only field that has to know which block it is in.
APP_SECTIONS = ("application info", "app info", "program identification")
HEADER_FIELDS = {
    "key_revision": ((), ("key revision", "key-revision")),
    "self_type": ((), ("self-type", "self type", "selftype")),
    "auth_id": ((), ("auth-id", "auth id", "authid")),
    "vendor_id": ((), ("vendor-id", "vendor id", "vendorid")),
    "app_version": (APP_SECTIONS, ("app version", "version")),
    "fw_version": ((), ("fw version", "firmware version", "self-fw-version")),
    "licence_type": ((), ("licence type", "license type", "drm type",
                          "np license type", "np licence type")),
    "app_type": ((), ("app type", "app-type", "apptype", "application type",
                      "np app type")),
    "content_id": ((), ("contentid", "content id", "content-id")),
    "cid_fn_hash": ((), ("cid_fn hash", "cid-fn hash", "cid fn hash",
                         "cidfn hash", "real filename hash",
                         "real fname hash")),
}

# Without these five there is nothing to re-sign with, so a file missing any of
# them is not a BO2 NPDRM binary and the run stops.
REQUIRED_FIELDS = ("key_revision", "self_type", "app_type", "licence_type",
                   "content_id")

# Read from the header when scetool prints them, which not every build does.
# These are the BLES01717 values, and a run that falls back to them says so.
FIELD_DEFAULTS = {
    "auth_id": "1010000001000003",
    "vendor_id": "01000002",
    "app_version": "0001000000000000",
    "fw_version": "0004002000000000",
}

# Some builds print a name, some print only the number, and a retail file
# prints "NPDRM Application" where the documentation says "NPDRM", so each of
# these is resolved by name, by distinctive word, and by value.
SELF_TYPES = {"npdrm": "NPDRM", "app": "APP", "application": "APP",
              "lv0": "LV0", "lv1": "LV1", "lv2": "LV2", "iso": "ISO",
              "ldr": "LDR"}
SELF_TYPE_NUMBERS = {1: "LV0", 2: "LV1", 3: "LV2", 4: "APP", 5: "ISO",
                     6: "LDR", 8: "NPDRM"}
APP_TYPES = {"sprx": "SPRX", "exec": "EXEC", "executable": "EXEC",
             "usprx": "USPRX", "update sprx": "USPRX",
             "uexec": "UEXEC", "update exec": "UEXEC",
             "update executable": "UEXEC"}
# Read off real retail files: EBOOT.BIN is 0x21 and both selfs are 0x20, the
# same on BLES01717 and BLUS31140.
APP_TYPE_NUMBERS = {0x01: "SPRX", 0x02: "EXEC", 0x20: "USPRX", 0x21: "UEXEC"}
LICENCE_TYPES = {"free": "FREE", "local": "LOCAL", "network": "NETWORK"}
LICENCE_TYPE_NUMBERS = {1: "NETWORK", 2: "LOCAL", 3: "FREE"}
# scetool prints these as names rather than values on a retail file. The pair
# is what its own sign command wants back for -3 and -4.
AUTH_ID_NAMES = {"retail game/update": "1010000001000003"}
VENDOR_ID_NAMES = {"normal": "01000002"}

FIELD_TITLES = {
    "key_revision": "Key revision",
    "self_type": "SELF type",
    "auth_id": "Auth-ID",
    "vendor_id": "Vendor-ID",
    "app_version": "App version",
    "fw_version": "Firmware version",
    "licence_type": "Licence type",
    "app_type": "App type",
    "content_id": "ContentID",
    "cid_fn_hash": "CID_FN hash",
}

LOG_ORDER = ("key_revision", "self_type", "app_type", "licence_type",
             "auth_id", "vendor_id", "app_version", "fw_version",
             "content_id", "cid_fn_hash")


class Failure(Exception):
    """An expected stop with an explanation. Reported without a traceback."""


def app_dir():
    """The folder the user sees this program in. Under PyInstaller --onefile
    that is where the exe sits, not the temporary extraction directory."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """Where files bundled into the exe are unpacked at run time."""
    return getattr(sys, "_MEIPASS", app_dir())


def config_path():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = (os.environ.get("XDG_CONFIG_HOME")
                or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, CONFIG_DIR_NAME, "config.json")


def patcher_path():
    """Bundled alongside the exe by the build script, or sat in the repo."""
    for base in (bundle_dir(), app_dir()):
        candidate = os.path.join(base, "patch-bo2.py")
        if os.path.isfile(candidate):
            return candidate
    return None


def bundled_scetool():
    """The copy shipped with the program. Under PyInstaller --onefile the whole
    tools folder is unpacked under _MEIPASS, so that is where it lives at run
    time; from a checkout it sits next to this script."""
    for base in (bundle_dir(), app_dir()):
        for name in ("scetool.exe", "scetool"):
            candidate = os.path.join(base, BUNDLED_SCETOOL, name)
            if os.path.isfile(candidate):
                return candidate
    return ""


def find_scetool(saved):
    """An override set in Settings wins, otherwise the bundled copy."""
    if saved and os.path.isfile(saved):
        return saved
    return bundled_scetool()


def validate_scetool(path):
    """Returns (complaint, version banner). A complaint means do not save it.

    Catching a bad scetool here rather than nine minutes into a job is the
    point: the usual mistake is copying scetool.exe out on its own, which only
    fails much later and with a misleading message about the klicensee."""
    if not path:
        return "No path is set.", ""
    if not os.path.isfile(path):
        return f"{path} does not exist.", ""
    folder = os.path.dirname(os.path.abspath(path))
    # scetool has to run with its own folder as the working directory, and
    # cmd.exe refuses a UNC working directory outright and silently falls back
    # to the Windows directory, where the keys are not. Caught here because the
    # symptom otherwise is a decrypt failing for no visible reason.
    if sys.platform == "win32" and os.path.normpath(folder).startswith("\\\\"):
        return (f"scetool is on a network path ({folder}). It cannot be run "
                f"from there, because it needs its own folder as the working "
                f"directory and Windows will not use a UNC path for that. "
                f"Copy this program to a local drive and run it from there.",
                "")
    if not os.path.isdir(os.path.join(folder, "data")):
        return ("There is no data folder beside this scetool. It looks up its "
                "keys relative to its own folder, so a copy taken out of the "
                "Tools folder on its own cannot work. Point at the scetool "
                "still sitting in Tools, with data next to it.", "")
    if not os.path.exists(os.path.join(folder, "data", "keys")):
        return ("The data folder beside this scetool has no keys file in it. "
                "Use the whole Tools folder from the Eboot-Self Builder "
                "rather than a partial copy.", "")
    try:
        proc = subprocess.run([path], cwd=folder, capture_output=True,
                              text=True, errors="replace",
                              creationflags=NO_WINDOW, timeout=20)
    except OSError as exc:
        return (f"This file could not be run ({exc}). It may be built for a "
                f"different kind of machine.", "")
    except subprocess.TimeoutExpired:
        return "This file did not respond when it was run.", ""
    banner = f"{proc.stdout}\n{proc.stderr}"
    if "scetool" not in banner.lower():
        return ("Running this printed no scetool version banner, so it does "
                "not look like scetool.", "")
    version = next((line.strip() for line in banner.splitlines()
                    if "scetool" in line.lower()), "scetool")
    return None, version


def icon_file(extension):
    for base in (bundle_dir(), app_dir()):
        candidate = os.path.join(base, f"icon.{extension}")
        if os.path.isfile(candidate):
            return candidate
    return ""


def apply_icon(root):
    """Title bar and taskbar. Missing or unreadable icons are not worth
    stopping over, so every step here fails quietly."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_ID)
        except (ImportError, AttributeError, OSError):
            pass
    windows_icon = icon_file("ico")
    if windows_icon:
        try:
            root.iconbitmap(default=windows_icon)
            return
        except tk.TclError:
            pass
    portable_icon = icon_file("png")
    if portable_icon:
        try:
            root.iconphoto(True, tk.PhotoImage(file=portable_icon))
        except tk.TclError:
            pass


def load_patcher():
    """patch-bo2.py is the single source of truth for where the fault is."""
    path = patcher_path()
    if not path:
        raise Failure("patch-bo2.py could not be found. It has to sit next to "
                      "this program, or be bundled into it at build time.")
    spec = importlib.util.spec_from_file_location("bo2_patch", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit as exc:
        raise Failure(f"patch-bo2.py exited while being imported: {exc}")
    return module


def patch_elf(patcher, data):
    """Run patch-bo2.py's pattern search over an ELF held in memory."""
    buf = bytearray(data)
    try:
        fmt_va = patcher.find_format_string(buf)
        sites = patcher.find_construct(buf, fmt_va)
        call = patcher.find_call(buf, sites[0]) if len(sites) == 1 else None
    except SystemExit as exc:
        # patch-bo2.py reports its own failures by exiting, which would take
        # the window down with it.
        raise Failure(str(exc))
    if call is None:
        raise Failure(f"expected 1 format string construct site, found "
                      f"{len(sites)}")
    old = patcher.u32(buf, call)
    struct.pack_into(">I", buf, call, patcher.NOP)
    report = (f'"crm %lld %s" at vaddr {fmt_va:08X}\n'
              f"call at vaddr {call + patcher.LOAD_BASE:08X} "
              f"(file {call:08X}): {old:08X} -> {patcher.NOP:08X}")
    return bytes(buf), report


def _bracketed(text):
    match = re.search(r"\[([^\]]+)\]", text)
    return match.group(1).strip() if match else text.strip()


def _hex_value(text, digits):
    """Pull a fixed width hex field out, however widely it has been printed."""
    match = re.search(r"0x([0-9A-Fa-f]+)", text)
    if not match:
        match = re.search(r"\b([0-9A-Fa-f]{%d})\b" % digits, text)
    if not match:
        return None
    value = match.group(1).upper().lstrip("0") or "0"
    if len(value) > digits:
        return None
    return value.zfill(digits)


def _named_value(text, table, numbers=None, priority=()):
    """By exact name, then by the distinctive word inside a longer name, then
    by the raw value when the build prints no name at all.

    Priority names are tried before the general pass. A retail file says
    "NPDRM Application", which contains both npdrm and application, and the
    longest match is the wrong one."""
    token = _bracketed(text).lower().strip()
    if token in table:
        return table[token]
    for name in priority:
        if re.search(rf"\b{re.escape(name)}\b", token):
            return table[name]
    for name in sorted(table, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", token):
            return table[name]
    number = _hex_value(text, 8)
    if number is not None and numbers:
        return numbers.get(int(number, 16))
    return None


def _id_value(text, digits, names):
    value = _hex_value(text, digits)
    if value is not None:
        return value
    return names.get(_bracketed(text).lower().strip())


def _version_value(text):
    """The version fields are decimal digits laid into a hex field, not a hex
    number: firmware 04.20 is 0004002000000000, so converting the 20 from base
    16 would sign the file as 4.32."""
    for token in re.findall(r"[0-9A-Fa-fXx.]+", text):
        token = re.sub(r"^0[xX]", "", token)
        match = re.fullmatch(r"(\d{1,4})\.(\d{1,4})", token)
        if match:
            return f"{match.group(1).zfill(4)}{match.group(2).zfill(4)}00000000"
        if re.fullmatch(r"[0-9A-Fa-f]{16}", token):
            return token.upper()
    return None


def _content_id(text):
    value = _bracketed(text).replace("\x00", "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9_\-]{1,48}", value) else None


NORMALISERS = {
    "key_revision": lambda v: _hex_value(v, 2),
    "auth_id": lambda v: _id_value(v, 16, AUTH_ID_NAMES),
    "vendor_id": lambda v: _id_value(v, 8, VENDOR_ID_NAMES),
    "self_type": lambda v: _named_value(v, SELF_TYPES, SELF_TYPE_NUMBERS,
                                        priority=("npdrm",)),
    "app_type": lambda v: _named_value(v, APP_TYPES, APP_TYPE_NUMBERS),
    "licence_type": lambda v: _named_value(v, LICENCE_TYPES,
                                           LICENCE_TYPE_NUMBERS),
    "app_version": _version_value,
    "fw_version": _version_value,
    "content_id": _content_id,
    "cid_fn_hash": lambda v: re.sub(r"[^0-9A-Fa-f]", "", v).upper() or None,
}


def parse_header(text):
    """Pull the signing parameters out of scetool -i output."""
    found = {}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[*]"):
            section = line[3:].strip().rstrip(":").lower()
            continue
        lowered = line.lower()
        for name, (sections, labels) in HEADER_FIELDS.items():
            if name in found:
                continue
            if sections and not any(part in section for part in sections):
                continue
            for label in labels:
                if not lowered.startswith(label):
                    continue
                value = line[len(label):].lstrip(" :\t")
                if value:
                    found[name] = value
                break
    info = {}
    for name, value in found.items():
        normalised = NORMALISERS[name](value)
        if normalised:
            info[name] = normalised
    return info


def title_id_from(content_id):
    """UP0002-BLUS31011_00-CODBLOPS2PATCH09 is noise; BLUS31011 is the part
    anyone actually checks."""
    match = re.search(r"-([A-Za-z]{4}\d{5})_", content_id or "")
    return match.group(1) if match else (content_id or "")


def parse_param_sfo(path):
    """PARAM.SFO sits one level up from USRDIR and is the only place the title
    update number appears. Every BO2 SELF reports app version 01.00 whatever
    update is installed, so the header cannot be used for it."""
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 0x14 or data[:4] != b"\x00PSF":
        return {}
    key_table, data_table, count = struct.unpack_from("<III", data, 0x08)
    values = {}
    for index in range(count):
        entry = 0x14 + index * 0x10
        if entry + 0x10 > len(data):
            break
        key_offset, fmt, length, _maximum, data_offset = struct.unpack_from(
            "<HHIII", data, entry)
        key_start = key_table + key_offset
        key_end = data.find(b"\x00", key_start)
        value_start = data_table + data_offset
        if key_start >= len(data) or key_end < 0 or value_start + length > len(data):
            continue
        key = data[key_start:key_end].decode("ascii", "replace")
        raw = data[value_start:value_start + length]
        if fmt == 0x0404:
            values[key] = (str(struct.unpack_from("<I", raw)[0])
                           if len(raw) >= 4 else "")
        else:
            values[key] = raw.split(b"\x00")[0].decode("utf-8", "replace")
    return values


def read_param_sfo(folder):
    """Beside USRDIR in a normal install, and inside it in an odd one."""
    if not folder:
        return {}
    for candidate in (os.path.join(folder, os.pardir, "PARAM.SFO"),
                      os.path.join(folder, "PARAM.SFO")):
        candidate = os.path.abspath(candidate)
        if os.path.isfile(candidate):
            try:
                return parse_param_sfo(candidate)
            except (OSError, struct.error):
                return {}
    return {}


def update_words(app_ver):
    """PARAM.SFO writes the update as 01.19."""
    match = re.fullmatch(r"(\d{1,2})\.(\d{2})", (app_ver or "").strip())
    return f"{int(match.group(1))}.{match.group(2)}" if match else ""


def run_scetool(scetool, args, what):
    """scetool resolves its data and keys folder relative to the working
    directory, so it has to run from its own folder whatever ours is."""
    try:
        proc = subprocess.run([scetool] + args, cwd=os.path.dirname(scetool),
                              capture_output=True, text=True, errors="replace",
                              creationflags=NO_WINDOW)
    except OSError as exc:
        raise Failure(f"scetool could not be run ({exc}). Check the path "
                      f"points at the executable itself and that it is built "
                      f"for this machine.")
    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr)
                       if part and part.strip())
    if proc.returncode != 0:
        raise Failure(f"{what} failed, scetool exited {proc.returncode}.\n"
                      f"{output or '(scetool printed nothing)'}")
    return output


def header_args(slot, path):
    args = ["-l", KLICENSEE] if slot.needs_klicensee else []
    return args + ["-i", path]


def sha1_of(path):
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def first_difference(left, right):
    for offset, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return offset
    return min(len(left), len(right))


def same_path(first, second):
    """Windows is case insensitive and both sides may be links, so string
    equality on the typed paths is not enough to protect the originals."""
    if os.path.exists(first) and os.path.exists(second):
        return os.path.samefile(first, second)
    return (os.path.normcase(os.path.realpath(first))
            == os.path.normcase(os.path.realpath(second)))


def default_output_for(folder):
    """A patched folder beside the game folder, never inside it."""
    if not folder:
        return ""
    folder = os.path.abspath(folder)
    parent = os.path.dirname(folder)
    if parent == folder:
        return os.path.join(folder, "patched")
    return os.path.join(parent, "patched")


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def load_config():
    path = config_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config):
    """Returns the reason it could not be saved, or None."""
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
    except OSError as exc:
        return str(exc)
    return None


class AboutDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("About")
        self.resizable(False, False)

        frame = ttk.Frame(self, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="BO2 PSN freeze fix",
                  style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=f"Version {VERSION}", style="Hint.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 10))

        link = ttk.Label(frame, text=PROJECT_URL, style="Link.TLabel",
                         cursor="hand2")
        link.grid(row=2, column=0, sticky="w")
        link.bind("<Button-1>", lambda event: webbrowser.open(PROJECT_URL))

        ttk.Separator(frame, orient="horizontal").grid(
            row=3, column=0, sticky="ew", pady=12)
        ttk.Label(frame,
                  text="scetool is naehrwert's work, bundled here so that this "
                       "runs without any setup. This project is not affiliated "
                       "with it.",
                  style="Hint.TLabel", wraplength=340, justify="left").grid(
            row=4, column=0, sticky="w")

        ttk.Button(frame, text="Close",
                   style="Accent.TButton", command=self.destroy).grid(
            row=5, column=0, sticky="e", pady=(16, 0))

        self.transient(parent)
        self.grab_set()
        self.wait_window(self)


class SettingsDialog(tk.Toplevel):
    """Everything here is optional. The program runs without it being opened."""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.title("Settings")
        self.resizable(True, False)
        self.result = None
        self.version = ""
        self.path = tk.StringVar(value=settings.get("scetool", ""))
        self.write_log = tk.BooleanVar(value=settings.get("write_log", True))
        self.open_when_done = tk.BooleanVar(
            value=settings.get("open_when_done", True))
        self.verify = tk.BooleanVar(value=settings.get("verify", True))

        frame = ttk.Frame(self, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="When a job finishes",
                  style="Heading.TLabel").grid(row=0, column=0, columnspan=3,
                                               sticky="w")
        ttk.Checkbutton(frame, text="Keep a log file next to the output",
                        variable=self.write_log).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="Worth leaving on. If something goes wrong, the "
                             "log is the thing anyone helping will ask for.",
                  style="Hint.TLabel", wraplength=440, justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=(20, 0))
        ttk.Checkbutton(frame, text="Open the output folder when finished",
                        variable=self.open_when_done).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        ttk.Separator(frame, orient="horizontal").grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=12)

        ttk.Label(frame, text="Checking", style="Heading.TLabel").grid(
            row=5, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frame, text="Verify each file after signing",
                        variable=self.verify).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="Turning this off is not recommended. It is the "
                             "round trip decrypt and compare, and it is the "
                             "only thing standing between a bad file and your "
                             "console.",
                  style="Hint.TLabel", wraplength=440, justify="left").grid(
            row=7, column=0, columnspan=3, sticky="w", padx=(20, 0))

        ttk.Separator(frame, orient="horizontal").grid(
            row=8, column=0, columnspan=3, sticky="ew", pady=12)

        ttk.Label(frame, text="scetool", style="Heading.TLabel").grid(
            row=9, column=0, columnspan=3, sticky="w")
        ttk.Label(frame,
                  text="Do not change this unless the bundled copy is not "
                       "working. A different build or keyset can produce files "
                       "your console will refuse.",
                  style="Warning.TLabel", wraplength=440, justify="left").grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(6, 8))
        ttk.Entry(frame, textvariable=self.path, width=56).grid(
            row=11, column=0, columnspan=2, sticky="ew")
        ttk.Button(frame, text="Browse",
                   command=self._browse).grid(row=11, column=2, sticky="w",
                                              padx=(8, 0))
        ttk.Button(frame, text="Use bundled copy",
                   command=self._detect).grid(row=12, column=0, sticky="w",
                                              pady=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=13, column=0, columnspan=3, sticky="e", pady=(16, 0))
        # The padding is split evenly between the two columns so that it
        # cannot bias one against the other.
        for column in (0, 1):
            buttons.columnconfigure(column, weight=1, uniform="dialog")
        ttk.Button(buttons, text="Cancel", command=self.destroy).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(buttons, text="Save", style="Accent.TButton",
                   command=self._save).grid(row=0, column=1, sticky="ew",
                                            padx=(6, 0))

        self.transient(parent)
        self.grab_set()
        self.wait_window(self)

    def _browse(self):
        chosen = filedialog.askopenfilename(title="Select scetool", parent=self)
        if chosen:
            self.path.set(os.path.normpath(chosen))

    def _detect(self):
        found = bundled_scetool()
        if found:
            self.path.set(found)
        else:
            messagebox.showinfo(
                "Not found",
                "The bundled scetool is missing, which means this program was "
                "not extracted completely. Browse to a copy of your own "
                "instead.", parent=self)

    def _save(self):
        path = self.path.get().strip()
        if path:
            problem, version = validate_scetool(path)
            if problem:
                messagebox.showerror("scetool cannot be used", problem,
                                     parent=self)
                return
            self.version = version
        self.result = {
            "scetool": path,
            "write_log": bool(self.write_log.get()),
            "open_when_done": bool(self.open_when_done.get()),
            "verify": bool(self.verify.get()),
        }
        self.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.settings = dict(DEFAULT_SETTINGS)
        self.settings.update(load_config())
        self.scetool = find_scetool(self.settings.get("scetool", ""))
        self.last_output = ""
        self.advanced_shown = False
        self.scan = {}
        self.scan_sfo = {}
        self.scan_token = 0
        self.scan_job = None
        self.log_lines = []

        root.title("BO2 PSN freeze fix")
        apply_icon(root)
        root.minsize(760, 640)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self._init_style()
        self._build_menu(root)
        self._build(root)
        self._attach_traces()
        self._check_scetool()
        self._refresh()

        self.log("Pick the game folder holding EBOOT.BIN, t6_ps3f.self and "
                 "t6mp_ps3f.self. On the console that is "
                 "/dev_hdd0/game/<TITLE>/USRDIR/.")

    def _check_scetool(self):
        if not self.scetool:
            self.log("The bundled scetool is missing, which means this "
                     "program was not extracted completely. Point at a copy "
                     "of your own under Settings, or extract it again.")
            return
        problem, version = validate_scetool(self.scetool)
        if problem:
            self.log(f"The scetool at {self.scetool} cannot be used: {problem}")
            self.log("Point at a working copy under Settings, or extract this "
                     "program again.")
            self.scetool = ""
        else:
            self.log(f"Using {version}")

    # Appearance ---------------------------------------------------------

    def _init_style(self):
        style = ttk.Style()
        available = style.theme_names()
        if sys.platform == "win32" and "vista" in available:
            style.theme_use("vista")
        elif "clam" in available:
            style.theme_use("clam")
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        style.configure("Accent.TButton", font=bold)
        style.configure("Heading.TLabel", font=bold)
        style.configure("Found.TLabel", foreground="#1a7f37")
        style.configure("Missing.TLabel", foreground="#b42318")
        style.configure("Warning.TLabel", foreground="#b42318")
        style.configure("Hint.TLabel", foreground="#555555")
        style.configure("Link.TLabel", foreground="#3465a4")
        self.mono = tkfont.nametofont("TkFixedFont")

    def _build_menu(self, root):
        menubar = tk.Menu(root)
        self.file_menu = tk.Menu(menubar, tearoff=0)
        self.file_menu.add_command(label="Open game folder",
                                   command=self._open_game_folder)
        self.file_menu.add_command(label="Open output folder",
                                   command=self._open_output_folder)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=root.destroy)
        menubar.add_cascade(label="File", menu=self.file_menu)

        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="Settings", command=self._open_settings)
        menubar.add_cascade(label="Tools", menu=tools)

        about = tk.Menu(menubar, tearoff=0)
        about.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=about)
        root.configure(menu=menubar)

    def _heading(self, parent, row, title):
        header = ttk.Frame(parent)
        header.grid(row=row, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=title, style="Heading.TLabel").grid(
            row=0, column=0, sticky="w")
        return header

    def _build(self, root):
        frame = ttk.Frame(root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)

        self.game_folder = tk.StringVar()
        self.outdir = tk.StringVar()
        self.overrides = {slot.key: tk.StringVar() for slot in SLOTS}

        self._heading(frame, 0, "Game folder")
        self._build_input(frame, 1)
        self._heading(frame, 2, "Output folder")
        self._build_output(frame, 3)
        self._build_actions(frame, 4)
        self._build_progress(frame, 5)
        self._heading(frame, 6, "Log")
        self._build_log(frame, 7)
        frame.rowconfigure(7, weight=1)
        self._build_footer(frame, 8)

    def _build_input(self, parent, row):
        group = ttk.Frame(parent)
        group.grid(row=row, column=0, sticky="ew", pady=(6, 0))
        group.columnconfigure(0, weight=1)

        picker = ttk.Frame(group)
        picker.grid(row=0, column=0, sticky="ew")
        picker.columnconfigure(0, weight=1)
        ttk.Entry(picker, textvariable=self.game_folder).grid(
            row=0, column=0, sticky="ew")
        ttk.Button(picker, text="Browse",
                   command=self._browse_game).grid(row=0, column=1,
                                                   padx=(8, 0))

        self.found_labels = {}
        found = ttk.Frame(group)
        found.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        found.columnconfigure(0, weight=1)
        for index, slot in enumerate(SLOTS):
            label = ttk.Label(found, text="", font=self.mono,
                              style="Hint.TLabel")
            label.grid(row=index, column=0, sticky="w")
            self.found_labels[slot.key] = label

        self.summary = ttk.Label(group, text="", style="Hint.TLabel")
        self.summary.grid(row=2, column=0, sticky="w", pady=(8, 0))

        self.advanced_toggle = ttk.Checkbutton(
            group, text="Advanced: choose each file separately",
            command=self._toggle_advanced)
        self.advanced_toggle.state(["!alternate", "!selected"])
        # Without the gap below it this reads as the first thing in the
        # output section rather than the last thing in this one.
        self.advanced_toggle.grid(row=3, column=0, sticky="w", pady=(10, 14))

        self.advanced = ttk.Frame(group)
        self.advanced.columnconfigure(1, weight=1)
        ttk.Label(self.advanced,
                  text="Anything set here is used instead of the file of that "
                       "name in the game folder.",
                  style="Hint.TLabel").grid(row=0, column=0, columnspan=3,
                                            sticky="w", pady=(0, 6))
        for index, slot in enumerate(SLOTS, start=1):
            ttk.Label(self.advanced, text=slot.label).grid(
                row=index, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Entry(self.advanced, textvariable=self.overrides[slot.key]).grid(
                row=index, column=1, sticky="ew", pady=2)
            ttk.Button(self.advanced, text="Browse",
                       command=lambda s=slot: self._browse_override(s)).grid(
                row=index, column=2, padx=(8, 0), pady=2)

    def _build_output(self, parent, row):
        group = ttk.Frame(parent)
        group.grid(row=row, column=0, sticky="ew", pady=(6, 0))
        group.columnconfigure(0, weight=1)
        ttk.Entry(group, textvariable=self.outdir).grid(
            row=0, column=0, sticky="ew")
        ttk.Button(group, text="Browse",
                   command=self._browse_output).grid(row=0, column=1,
                                                     padx=(8, 0))
        ttk.Label(group, text="Created if it does not exist. It must not be "
                             "the folder the originals came from.",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=2,
                                            sticky="w", pady=(6, 0))

    def _build_actions(self, parent, row):
        actions = ttk.Frame(parent)
        actions.grid(row=row, column=0, sticky="ew", pady=(14, 0))
        actions.columnconfigure(0, weight=1)
        # The pair sits in a frame of its own so that giving its two columns
        # equal weight in one uniform group sizes them both to the wider label,
        # without also stretching them across the window.
        pair = ttk.Frame(actions)
        pair.grid(row=0, column=1, sticky="e")
        for column in (0, 1):
            pair.columnconfigure(column, weight=1, uniform="action")
        self.open_output = ttk.Button(pair, text="Open output folder",
                                      command=self._open_output_folder,
                                      state="disabled")
        self.open_output.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.go = ttk.Button(pair, text="Patch all three",
                             style="Accent.TButton", command=self.on_go)
        self.go.grid(row=0, column=1, sticky="ew", padx=(6, 0))

    def _build_progress(self, parent, row):
        progress = ttk.Frame(parent)
        progress.grid(row=row, column=0, sticky="ew", pady=(12, 0))
        progress.columnconfigure(0, weight=1)
        self.bar = ttk.Progressbar(progress, mode="determinate",
                                   maximum=TOTAL_STEPS)
        self.bar.grid(row=0, column=0, sticky="ew")
        self.status = ttk.Label(progress, text="Idle", style="Hint.TLabel")
        self.status.grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _build_log(self, parent, row):
        group = ttk.Frame(parent)
        group.grid(row=row, column=0, sticky="nsew", pady=(6, 0))
        group.columnconfigure(0, weight=1)
        group.rowconfigure(0, weight=1)
        # Text has no ttk equivalent, so it is the one plain widget here.
        self.log_text = tk.Text(group, wrap="word", height=12, width=80,
                                state="disabled", font=self.mono,
                                relief="flat", borderwidth=0,
                                highlightthickness=0)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(group, orient="vertical",
                               command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        # A disabled Text only takes focus on Windows, and without focus the
        # copy binding never fires, so the log cannot be copied anywhere else.
        self.log_text.bind("<Button-1>", lambda event: self.log_text.focus_set())

    def _build_footer(self, parent, row):
        link = ttk.Label(parent, text=PROJECT_URL, style="Link.TLabel",
                         cursor="hand2")
        link.grid(row=row, column=0, sticky="w", pady=(10, 0))
        link.bind("<Button-1>", lambda event: webbrowser.open(PROJECT_URL))

    # Input resolution ---------------------------------------------------

    def _attach_traces(self):
        self.game_folder.trace_add("write", lambda *_: self._on_folder_change())
        for var in self.overrides.values():
            var.trace_add("write", lambda *_: self._on_folder_change())

    def _resolved(self):
        """Effective path for each slot, whether scanned or overridden."""
        folder = self.game_folder.get().strip()
        paths = {}
        for slot in SLOTS:
            override = self.overrides[slot.key].get().strip()
            if override:
                paths[slot.key] = os.path.abspath(override)
            elif folder:
                paths[slot.key] = os.path.abspath(
                    os.path.join(folder, slot.out_name))
            else:
                paths[slot.key] = ""
        return paths

    _last_default = ""

    def _on_folder_change(self):
        folder = self.game_folder.get().strip()
        # Only ever replaces a default with a default, so anything the user has
        # typed into the output box survives them re-picking the game folder.
        if folder and self.outdir.get().strip() in ("", self._last_default):
            self.outdir.set(default_output_for(folder))
        self._last_default = default_output_for(folder)
        self.scan = {}
        self.scan_sfo = {}
        self._refresh()
        # Typing a path fires this per keystroke, so settle before reading.
        if self.scan_job:
            self.root.after_cancel(self.scan_job)
        self.scan_job = self.root.after(400, self._start_scan)

    # Reading the headers ------------------------------------------------

    def _start_scan(self):
        self.scan_job = None
        paths = self._resolved()
        if not self.scetool or not all(os.path.isfile(paths[slot.key])
                                       for slot in SLOTS):
            return
        self.scan_token += 1
        token = self.scan_token
        self.status.configure(text="Reading headers")
        threading.Thread(target=self._scan_headers,
                         args=(token, paths, self.game_folder.get().strip(),
                               self.scetool),
                         daemon=True).start()

    def _scan_headers(self, token, paths, folder, scetool):
        """Done as soon as the folder is picked rather than on Go, so the
        window has something to say about the files straight away."""
        results = {}
        for slot in SLOTS:
            try:
                output = run_scetool(scetool, header_args(slot, paths[slot.key]),
                                     f"reading {slot.label}")
                results[slot.key] = {"info": parse_header(output),
                                     "size": os.path.getsize(paths[slot.key])}
            except Failure as exc:
                results[slot.key] = {"error": str(exc)}
        self.root.after(0, self._scan_done, token, results,
                        read_param_sfo(folder))

    def _scan_done(self, token, results, sfo):
        if token != self.scan_token:
            return
        self.scan = results
        self.scan_sfo = sfo
        self._refresh()

    def _row_parts(self, slot, path):
        """The three columns after the file name, or None if there is nothing
        to say yet."""
        if not self.scan.get(slot.key):
            return None
        entry = self.scan[slot.key]
        if "error" in entry:
            return ["could not be read", "", ""]
        info = entry["info"]
        if any(field not in info for field in REQUIRED_FIELDS):
            # Blank columns and a tick would read as a file that is fine.
            return ["header not understood", "", ""]
        title = title_id_from(info.get("content_id", ""))
        kind = slot.role or APP_TYPE_WORDS.get(info.get("app_type", ""),
                                               info.get("app_type", ""))
        return [title, kind, f"{entry['size']:,} bytes"]

    def _refresh(self):
        paths = self._resolved()
        rows = {}
        ready = bool(self.scetool)
        for slot in SLOTS:
            path = paths[slot.key]
            if not path:
                rows[slot.key] = None
                ready = False
            elif not os.path.isfile(path):
                rows[slot.key] = ["not in this folder", "", ""]
                ready = False
            else:
                rows[slot.key] = self._row_parts(slot, path)
                if rows[slot.key] is None or rows[slot.key][0] in BAD_ROWS:
                    ready = False
        self._render_rows(rows)
        self._render_summary(ready)
        self._refresh_menu()
        self.go.configure(state="normal" if self._ready else "disabled")

    def _render_rows(self, rows):
        widths = [0, 0, 0]
        for parts in rows.values():
            if parts:
                for index, part in enumerate(parts):
                    widths[index] = max(widths[index], len(part))
        name_width = max(len(slot.label) for slot in SLOTS) + 3
        for slot in SLOTS:
            label = self.found_labels[slot.key]
            parts = rows[slot.key]
            if parts is None:
                label.configure(text=f"{slot.label:<{name_width}}"
                                     f"waiting for a folder",
                                style="Hint.TLabel")
                continue
            columns = "  ".join(part.ljust(widths[index])
                                for index, part in enumerate(parts))
            found = parts[0] not in BAD_ROWS
            tick = f"  {TICK}" if found else ""
            label.configure(text=f"{slot.label:<{name_width}}{columns}{tick}",
                            style="Found.TLabel" if found else "Missing.TLabel")

    def _render_summary(self, ready):
        self._ready = False
        if not ready or len(self.scan) != len(SLOTS):
            self.summary.configure(text="", style="Hint.TLabel")
            if not self.scetool:
                self.status.configure(text="scetool is unavailable. "
                                           "See the log.")
            elif self.scan_job or (self.scan and not ready):
                self.status.configure(text="Idle")
            return
        titles = {title_id_from(self.scan[slot.key]["info"].get("content_id", ""))
                  for slot in SLOTS}
        if len(titles) != 1:
            listing = ", ".join(sorted(titles))
            self.summary.configure(
                text=f"These files are not from the same game: {listing}. "
                     f"Mixing files from two consoles produces a set that "
                     f"fails on the console for no obvious reason.",
                style="Warning.TLabel")
            self.status.configure(text="Refusing to run. See above.")
            return
        title = titles.pop()
        # Absent whenever the files were chosen individually rather than from a
        # game folder, so the summary simply drops it.
        update = update_words(self.scan_sfo.get("APP_VER", ""))
        detail = f", title update {update}" if update else ""
        self.summary.configure(text=f"{title}{detail}", style="Hint.TLabel")
        self.status.configure(text="Ready.")
        self._ready = True

    _ready = False

    def _refresh_menu(self):
        for index, folder in ((0, self.game_folder.get().strip()),
                              (1, self.last_output or self.outdir.get().strip())):
            self.file_menu.entryconfigure(
                index, state="normal" if folder and os.path.isdir(folder)
                else "disabled")

    def _toggle_advanced(self):
        self.advanced_shown = not self.advanced_shown
        if self.advanced_shown:
            self.advanced.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        else:
            self.advanced.grid_remove()

    def _browse_game(self):
        chosen = filedialog.askdirectory(title="Select the game folder")
        if chosen:
            self.game_folder.set(os.path.normpath(chosen))

    def _browse_output(self):
        chosen = filedialog.askdirectory(title="Select the output folder")
        if chosen:
            self.outdir.set(os.path.normpath(chosen))

    def _browse_override(self, slot):
        chosen = filedialog.askopenfilename(title=f"Select {slot.label}")
        if chosen:
            self.overrides[slot.key].set(os.path.normpath(chosen))

    def _open_settings(self):
        dialog = SettingsDialog(self.root, self.settings)
        if dialog.result is None:
            return
        self.settings.update(dialog.result)
        self.scetool = find_scetool(self.settings.get("scetool", ""))
        if dialog.version:
            self.log(f"Using {dialog.version}")
        problem = save_config(self.settings)
        if problem:
            self.log(f"Could not save settings to {config_path()}: {problem}")
        self.scan = {}
        self.scan_sfo = {}
        self._refresh()
        self._start_scan()

    def _open_game_folder(self):
        folder = self.game_folder.get().strip()
        if folder and os.path.isdir(folder):
            open_folder(folder)

    def _open_output_folder(self):
        """The finished files if there are any, otherwise wherever they are
        headed, which is the folder someone checking on a failed run wants."""
        for folder in (self.last_output, self.outdir.get().strip()):
            if folder and os.path.isdir(folder):
                open_folder(folder)
                return

    def _show_about(self):
        AboutDialog(self.root)

    # Log and progress ---------------------------------------------------

    def log(self, text=""):
        self.log_lines.append(text)
        self.root.after(0, self._append, text)

    def _append(self, text):
        at_bottom = self.log_text.yview()[1] >= 0.999
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.configure(state="disabled")
        # Leave the view alone if the user has scrolled back to read something.
        if at_bottom:
            self.log_text.see("end")

    def progress(self, step, text):
        self.root.after(0, self._set_progress, step, text)

    def _set_progress(self, step, text):
        self.bar.configure(value=step)
        self.status.configure(text=text)

    def _finished(self, output):
        self.go.configure(state="normal")
        self._refresh()
        if not output:
            return
        self.last_output = output
        self.open_output.configure(state="normal")
        if self.settings.get("open_when_done", True):
            open_folder(output)
        self._show_next_steps(output)

    def _show_next_steps(self, output):
        title = "<TITLE>"
        if self.scan.get("eboot", {}).get("info"):
            title = title_id_from(
                self.scan["eboot"]["info"].get("content_id", "")) or title
        messagebox.showinfo(
            "Finished",
            f"Three patched files are in:\n\n{output}\n\n"
            f"Copy all three onto the console, into\n"
            f"/dev_hdd0/game/{title}/USRDIR/, replacing the files already "
            f"there.\n\n"
            f"Keep your original EBOOT.BIN, t6_ps3f.self and t6mp_ps3f.self "
            f"somewhere safe first. They are the only way back if you ever "
            f"want to undo this, and they cannot be rebuilt from the patched "
            f"copies.\n\n"
            f"All three are now fake signed, so syscalls have to be enabled or "
            f"none of them will boot. Reboot the console fully before testing.")

    def on_go(self):
        paths = self._resolved()
        for slot in SLOTS:
            if not os.path.isfile(paths[slot.key]):
                return messagebox.showerror(
                    "Missing file", f"{slot.label} was not found. Pick a game "
                                    f"folder that holds all three files, or "
                                    f"set them separately under Advanced.")
        if not self.scetool or not os.path.isfile(self.scetool):
            return messagebox.showerror(
                "scetool not set",
                "Set the location of scetool under Settings.")

        outdir = self.outdir.get().strip()
        if not outdir:
            return messagebox.showerror("No output folder",
                                        "Choose where the patched files go.")
        outdir = os.path.abspath(outdir)
        if not os.path.isdir(outdir):
            try:
                os.makedirs(outdir, exist_ok=True)
            except OSError as exc:
                return messagebox.showerror(
                    "Could not create folder", f"{outdir}\n\n{exc}")

        # Writing next to the originals is how people end up signing a patched
        # file back over the only clean copy they had.
        for slot in SLOTS:
            if same_path(os.path.dirname(paths[slot.key]), outdir):
                return messagebox.showerror(
                    "Bad output folder",
                    f"The output folder is the folder {slot.label} was taken "
                    f"from. Choose somewhere else so the originals are left "
                    f"alone.")
        for slot in SLOTS:
            destination = os.path.join(outdir, slot.out_name)
            for other in SLOTS:
                if same_path(destination, paths[other.key]):
                    return messagebox.showerror(
                        "Bad output folder",
                        f"Writing {slot.out_name} to the output folder would "
                        f"overwrite the {other.label} that was selected as an "
                        f"input. Choose a different output folder.")

        existing = [slot.out_name for slot in SLOTS
                    if os.path.exists(os.path.join(outdir, slot.out_name))]
        if existing:
            if not messagebox.askyesno(
                    "Replace files?",
                    "These already exist in the output folder and will be "
                    "replaced:\n\n" + "\n".join(existing) + "\n\nCarry on?"):
                return

        self.go.configure(state="disabled")
        self.open_output.configure(state="disabled")
        self.bar.configure(value=0)
        self.log_lines = []
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        threading.Thread(target=self._work, args=(paths, outdir, self.scetool),
                         daemon=True).start()

    def _write_log_file(self, outdir):
        """The log is the first thing anyone helping will ask for."""
        target = os.path.join(outdir, "bo2-psn-fix-log.txt")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(f"BO2 PSN freeze fix, {stamp}\n\n")
                handle.write("\n".join(self.log_lines) + "\n")
        except OSError as exc:
            self.log(f"Could not write the log file to {target}: {exc}")
            return
        self.log(f"Log written to {target}")

    def _work(self, inputs, outdir, scetool):
        workdir = tempfile.mkdtemp(prefix="bo2-patch-")
        written = ""
        try:
            self._pipeline(inputs, outdir, scetool, workdir)
            written = outdir
        except Failure as exc:
            self.log("")
            self.log(f"Stopped: {exc}")
            self.progress(self.bar["value"], "Stopped. See the log.")
        except BaseException:
            self.log("")
            self.log("Unexpected error:")
            self.log(traceback.format_exc())
            self.progress(self.bar["value"], "Stopped. See the log.")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            if self.settings.get("write_log", True) and os.path.isdir(outdir):
                self._write_log_file(outdir)
            self.root.after(0, self._finished, written)

    def _step(self, index, slot, stage):
        self.progress(index, f"{slot.label} - {stage}  "
                             f"(step {index + 1} of {TOTAL_STEPS})")

    def _scetool(self, scetool, args, what):
        return run_scetool(scetool, args, what)

    def _read_header(self, scetool, path, slot):
        output = self._scetool(scetool, header_args(slot, path),
                               f"reading the {slot.label} header")
        info = parse_header(output)
        missing = [FIELD_TITLES[name] for name in REQUIRED_FIELDS
                   if name not in info]
        if missing:
            raise Failure(f"could not read {', '.join(missing)} out of "
                          f"{slot.label}. scetool said:\n{output}")
        defaulted = [name for name in FIELD_DEFAULTS if name not in info]
        for name in defaulted:
            info[name] = FIELD_DEFAULTS[name]
        for name in LOG_ORDER:
            if name not in info:
                continue
            note = "   <- not in this header" if name in defaulted else ""
            self.log(f"  {FIELD_TITLES[name]:<18} {info[name]}{note}")
        if defaulted:
            self.log("  WARNING: the values marked above were not printed by "
                     "this scetool build, so the stock BLES01717 ones are "
                     "being used. Check them before trusting the output if "
                     "this is another region or update.")
        return info

    def _decrypt(self, scetool, src, dst, slot, what):
        args = ["-l", KLICENSEE] if slot.needs_klicensee else []
        output = self._scetool(scetool, args + ["-d", src, dst], what)
        # scetool reports plenty of failures on a zero exit code, so the output
        # file is the real test and its own output is the only useful message.
        if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            raise Failure(f"{what} produced no output, which usually means the "
                          f"key set or the klicensee is wrong for this file. "
                          f"scetool said:\n"
                          f"{output or '(scetool printed nothing)'}")
        with open(dst, "rb") as handle:
            return handle.read()

    def _pipeline(self, inputs, outdir, scetool, workdir):
        patcher = load_patcher()

        self.log("Reading headers")
        info = {}
        for index, slot in enumerate(SLOTS):
            self._step(index, slot, STAGES[0])
            self.log(f"{slot.label}  ({inputs[slot.key]})")
            info[slot.key] = self._read_header(scetool, inputs[slot.key], slot)
            self.log("")
        self._cross_check(info)

        self.log("Decrypting")
        elfs = {}
        for index, slot in enumerate(SLOTS):
            self._step(len(SLOTS) + index, slot, STAGES[1])
            dst = os.path.join(workdir, slot.key + ".elf")
            elfs[slot.key] = self._decrypt(scetool, inputs[slot.key], dst, slot,
                                           f"decrypting {slot.label}")
            self.log(f"  {slot.label} -> {len(elfs[slot.key]):,} bytes")
        if elfs["eboot"] != elfs["spzm"]:
            raise Failure("EBOOT.BIN and t6_ps3f.self did not decrypt to the "
                          "same ELF. They should be one binary signed twice, "
                          "so one of them is from a different install.")
        self.log("  EBOOT.BIN and t6_ps3f.self decrypt to the same ELF, "
                 "patching it once for both.")
        self.log("")

        self.log("Patching")
        patched = {}
        spzm_elf, report = patch_elf(patcher, elfs["eboot"])
        for line in report.splitlines():
            self.log(f"  SP/ZM: {line}")
        patched["eboot"] = spzm_elf
        patched["spzm"] = spzm_elf
        patched["mp"], report = patch_elf(patcher, elfs["mp"])
        for line in report.splitlines():
            self.log(f"  MP:    {line}")
        self.log("")

        self.log("Signing and verifying")
        staged = {}
        for index, slot in enumerate(SLOTS):
            self._step(2 * len(SLOTS) + index, slot, STAGES[2])
            self.log(slot.label)
            elf_path = os.path.join(workdir, slot.key + ".patched.elf")
            with open(elf_path, "wb") as handle:
                handle.write(patched[slot.key])
            staged[slot.key] = os.path.join(workdir, "out-" + slot.out_name)
            self._sign(scetool, info[slot.key], slot, elf_path,
                       staged[slot.key])
            if self.settings.get("verify", True):
                self._verify(scetool, info[slot.key], slot, patched[slot.key],
                             staged[slot.key], workdir)
            else:
                self.log("  WARNING: not verified, because verification is "
                         "switched off in Settings. Nothing has checked that "
                         "this file is what it should be.")
            self.log("")

        # Nothing is moved until all three have passed, so a failure on the
        # last file cannot leave a half patched set in the output folder.
        self.log("Writing")
        built = []
        for slot in SLOTS:
            final = os.path.join(outdir, slot.out_name)
            shutil.move(staged[slot.key], final)
            built.append(final)
            self.log(f"  {final}")
        self.log("")

        self.log("sha1 of the finished files")
        for path in built:
            self.log(f"  {sha1_of(path)}  {os.path.basename(path)}")
        self.log("")
        self.log("Done. Copy all three into /dev_hdd0/game/<TITLE>/USRDIR/ on "
                 "the console, keeping your backups, and make sure syscalls "
                 "are enabled before rebooting.")
        self.progress(TOTAL_STEPS, "Done. Three files written.")

    def _cross_check(self, info):
        content_ids = {info[slot.key]["content_id"] for slot in SLOTS}
        if len(content_ids) != 1:
            listing = "\n".join(f"  {slot.label}: {info[slot.key]['content_id']}"
                                for slot in SLOTS)
            raise Failure("the three files carry different ContentIDs, so they "
                          "are not from the same install:\n" + listing)
        for slot in SLOTS:
            if info[slot.key]["self_type"] != "NPDRM":
                raise Failure(f"{slot.label} is a {info[slot.key]['self_type']} "
                              f"SELF rather than an NPDRM one. Re-signing it "
                              f"would drop the NPDRM header and it would not "
                              f"boot.")
            if info[slot.key]["app_type"] not in slot.family:
                raise Failure(f"{slot.label} reports app type "
                              f"{info[slot.key]['app_type']}, but a "
                              f"{slot.label} should be {slot.family_name} "
                              f"({' or '.join(slot.family)}). Either the wrong "
                              f"file has been picked for that slot, or this "
                              f"install is not laid out the way this tool "
                              f"expects.\n" + self._app_types(info))
        if info["spzm"]["app_type"] != info["mp"]["app_type"]:
            raise Failure("the two selfs report different app types, so they "
                          "are not from the same install.\n"
                          + self._app_types(info))
        self.log(f"All three are {content_ids.pop()}.")
        self.log("App types, which have to be an executable for EBOOT.BIN and "
                 "an sprx for the two selfs:")
        self.log(self._app_types(info))
        self.log("")

    @staticmethod
    def _app_types(info):
        """Always shown with a rejection: the value that was read is the only
        thing that explains why a file was turned away."""
        return "\n".join(f"  {slot.label:<16} {info[slot.key]['app_type']}"
                         for slot in SLOTS)

    def _sign(self, scetool, info, slot, elf_path, out_path):
        args = ["-0", "SELF", "-1", "TRUE", "-s", "FALSE",
                "-2", info["key_revision"],
                "-3", info["auth_id"],
                "-4", info["vendor_id"],
                "-5", info["self_type"],
                "-A", info["app_version"],
                "-6", info["fw_version"],
                "-b", info["licence_type"],
                "-c", info["app_type"],
                "-f", info["content_id"],
                # -g goes into the CID_FN hash in the NPDRM header, so it has
                # to be the name the file will carry on the console rather than
                # whatever we happen to be writing to here. Get it wrong and
                # the file is valid but will not load.
                "-g", slot.out_name]
        if slot.needs_klicensee:
            args += ["-l", KLICENSEE]
        args += ["-e", elf_path, out_path]
        output = self._scetool(scetool, args, f"signing {slot.label}")
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise Failure(f"signing {slot.label} produced no output. scetool "
                          f"said:\n{output or '(scetool printed nothing)'}")
        self.log("  signed")

    def _verify(self, scetool, original, slot, expected_elf, built, workdir):
        check_path = os.path.join(workdir, slot.key + ".check.elf")
        actual = self._decrypt(scetool, built, check_path, slot,
                               f"decrypting the rebuilt {slot.label}")
        if actual != expected_elf:
            os.remove(built)
            offset = first_difference(expected_elf, actual)
            raise Failure(f"the rebuilt {slot.label} does not decrypt back to "
                          f"the patched ELF (in {len(expected_elf)} bytes, out "
                          f"{len(actual)} bytes, first difference at "
                          f"{offset:#x}). It has been discarded and nothing has "
                          f"been written to the output folder.")
        self.log("  decrypts back to the patched ELF byte for byte")

        args = ["-l", KLICENSEE] if slot.needs_klicensee else []
        output = self._scetool(scetool, args + ["-i", built],
                               f"re-reading the rebuilt {slot.label} header")
        rebuilt = parse_header(output)
        compared = []
        for name in ("content_id", "cid_fn_hash"):
            if name not in original and name not in rebuilt:
                self.log(f"  WARNING: {FIELD_TITLES[name]} is not printed by "
                         f"this scetool build, so it could not be checked")
                continue
            if rebuilt.get(name) != original.get(name):
                os.remove(built)
                raise Failure(f"{FIELD_TITLES[name]} changed on the rebuilt "
                              f"{slot.label}: was {original.get(name)}, now "
                              f"{rebuilt.get(name)}. It has been discarded and "
                              f"nothing has been written to the output folder.")
            compared.append(FIELD_TITLES[name])
        if compared:
            self.log(f"  {' and '.join(compared)} match the original")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

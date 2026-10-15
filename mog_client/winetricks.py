from __future__ import annotations

import re

# An entry is a verb (vcrun2019) or a setting with a value (vd=1280x1024). It goes on a command line: only what they are made
# of (no space, quote, `$`, `;`, `/`, a second `=`), and never something that starts like an option. The same as the server's.
NAME = r"[a-z0-9][a-z0-9_.-]{0,39}"
VALUE = r"[A-Za-z0-9_.,:+-]{1,40}"
VERB = re.compile(NAME + "(?:=" + VALUE + ")?")
MAX_VERBS = 100

# (name, what it is, the values offered): the settings games most often ask for. Any other value is added by hand.
SETTINGS = (
    ("vd", "Virtual desktop size", ("off", "800x600", "1024x768", "1280x720", "1280x1024", "1600x900", "1920x1080")),
    ("renderer", "Direct3D renderer", ("gdi", "gl", "vulkan")),
    ("csmt", "Command stream multithreading", ("on", "off")),
    ("mwo", "Mouse warp override", ("enable", "disable", "force")),
    ("sound", "Sound driver", ("alsa", "pulse", "disabled")),
)

# (verb, what it is): what games most often ask for, in the order they are listed.
CATALOG = (
    ("vcrun2019", "Visual C++ 2015-2022 runtime"),
    ("vcrun2013", "Visual C++ 2013 runtime"),
    ("vcrun2012", "Visual C++ 2012 runtime"),
    ("vcrun2010", "Visual C++ 2010 runtime"),
    ("vcrun2008", "Visual C++ 2008 runtime"),
    ("vcrun2005", "Visual C++ 2005 runtime"),
    ("vcrun6", "Visual C++ 6 runtime"),
    ("dotnet48", ".NET Framework 4.8"),
    ("dotnet40", ".NET Framework 4.0"),
    ("dotnet35", ".NET Framework 3.5"),
    ("dotnet20", ".NET Framework 2.0"),
    ("d3dx9", "DirectX 9 helper libraries (d3dx9)"),
    ("d3dx10", "DirectX 10 helper libraries (d3dx10)"),
    ("d3dx11_43", "DirectX 11 helper library (d3dx11_43)"),
    ("d3dcompiler_47", "Direct3D shader compiler 47"),
    ("d3dcompiler_43", "Direct3D shader compiler 43"),
    ("xact", "XACT audio (32 bit)"),
    ("xact_x64", "XACT audio (64 bit)"),
    ("xna40", "Microsoft XNA Framework 4.0"),
    ("physx", "NVIDIA PhysX"),
    ("openal", "OpenAL"),
    ("directplay", "DirectPlay"),
    ("dinput8", "DirectInput 8"),
    ("quartz", "DirectShow (quartz), for video"),
    ("lavfilters", "LAV Filters, for video"),
    ("wmp11", "Windows Media Player 11"),
    ("mfc42", "Microsoft Foundation Classes 4.2"),
    ("msxml6", "MSXML 6"),
    ("gdiplus", "GDI+"),
    ("corefonts", "Microsoft core fonts"),
)


def is_verb(value) -> bool:
    return isinstance(value, str) and VERB.fullmatch(value) is not None


def key_of(entry: str) -> str:
    """What an entry is about: the verb, or the name of the setting (`vd` for `vd=1280x1024`)."""
    return entry.split("=", 1)[0]


def normalize(entries) -> list[str]:
    """One entry for each key: a later one replaces an earlier one with the same key (`vd=1920x1080` after `vd=1280x1024`),
    in the place the key first had. A flag is its own key, so it stays once, as it always was."""
    kept: dict[str, str] = {}
    for entry in entries:
        kept[key_of(entry)] = entry
    return list(kept.values())


def clean(verbs) -> list[str]:
    """The entries in `verbs` (a list from the server, so not trusted) that are entries, one for each key, in order."""
    if not isinstance(verbs, list):
        return []
    return normalize(v for v in verbs if is_verb(v))[:MAX_VERBS]


def missing(wanted: list[str], applied: list[str]) -> list[str]:
    """What of `wanted` is not applied yet, in the order wanted: a verb the prefix lacks, or a setting whose value is not the
    one the prefix has (`vd=1920x1080` when it has `vd=1280x1024` is one change to make, not another verb)."""
    done = set(applied)
    return [v for v in clean(wanted) if v not in done]


def merge(applied: list[str], new: list[str]) -> list[str]:
    """`applied` with `new` put in: a setting that was there with another value takes the new one."""
    return normalize([*applied, *new])[:MAX_VERBS]


def resolve(picked: list[str], applied: list[str]) -> list[str]:
    """`picked` with one entry for each key. Where two were ticked for a key, the one the prefix does not have yet is the
    choice just made, and it wins; if neither is new, the last one."""
    done = set(applied)
    chosen: dict[str, str] = {}
    for entry in picked:
        key = key_of(entry)
        if key not in chosen or entry not in done or chosen[key] in done:
            chosen[key] = entry
    return list(chosen.values())


# What winetricks does for the settings below, written down so a setting can be put into the prefix's registry without running
# winetricks: that is silent (winetricks, through umu or Faugus, may open a window of its own, and one for a name it does not
# know). Values are checked here again: they end up in a `.reg` file. Keys are under HKEY_CURRENT_USER\\Software\\Wine.
_SIZE = re.compile(r"[1-9][0-9]{2,4}x[1-9][0-9]{2,4}")
_CHOICES = {
    "renderer": ("Direct3D", "renderer", ("gdi", "gl", "vulkan")),
    "ddr": ("Direct3D", "DirectDrawRenderer", ("gdi", "opengl")),
    "glsl": ("Direct3D", "UseGLSL", ("enabled", "disabled")),
    "mwo": ("DirectInput", "MouseWarpOverride", ("enable", "disable", "force")),
}
_YES_NO = {"windowmanagerdecorated": "Decorated", "windowmanagermanaged": "Managed", "usetakefocus": "UseTakeFocus"}


def registry_changes(entry: str) -> list[tuple[str, str]] | None:
    """The registry values (key under HKCU\\Software\\Wine, `.reg` value line) that put a setting in place, as winetricks does:
    `vd=1280x1024`, `renderer=gdi`, `csmt=on`, `mwo=force`, `sound=pulse`... None for a verb, a setting not known here (winetricks
    is left to deal with it) or a value that does not fit."""
    name, sep, value = entry.partition("=")
    if not sep or not is_verb(entry):
        return None
    if name == "vd":
        if value == "off":
            return [("Explorer", '"Desktop"=-')]
        if _SIZE.fullmatch(value):
            return [("Explorer", '"Desktop"="Default"'), ("Explorer\\Desktops", f'"Default"="{value}"')]
    elif name in _CHOICES:
        key, field, allowed = _CHOICES[name]
        if value in allowed:
            return [(key, f'"{field}"="{value}"')]
    elif name == "csmt" and value in ("on", "off"):
        return [("Direct3D", f'"csmt"=dword:0000000{1 if value == "on" else 0}')]
    elif name == "sound" and value in ("alsa", "pulse", "oss", "coreaudio", "disabled"):
        return [("Drivers", f'"Audio"="{"" if value == "disabled" else value}"')]
    elif name in _YES_NO and value in ("y", "n"):
        return [("X11 Driver", f'"{_YES_NO[name]}"="{value.upper()}"')]
    elif name == "videomemorysize" and value.isdigit() and 1 <= len(value) <= 6:
        return [("Direct3D", f'"VideoMemorySize"="{value}"')]
    return None

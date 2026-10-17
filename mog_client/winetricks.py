from __future__ import annotations

import re

# An entry is a verb (vcrun2019) or a setting with a value (vd=1280x1024). It goes on a command line: only what they are made
# of (no space, quote, `$`, `;`, `/`, a second `=`), and never something that starts like an option. The same as the server's.
NAME = r"[a-z0-9][a-z0-9_.-]{0,39}"
VALUE = r"[A-Za-z0-9_.,:+-]{1,40}"
VERB = re.compile(NAME + "(?:=" + VALUE + ")?")
MAX_VERBS = 100

# (code, name, Windows locale name, ANSI code page, OEM code page): the system languages offered. The code is a `locale=` entry's value
# and, with `.UTF-8`, the LC_ALL and LANG a game is started with.
LANGUAGES = (
    ("en_US", "English", "en-US", "1252", "437"),
    ("ja_JP", "Japanese", "ja-JP", "932", "932"),
    ("zh_CN", "Chinese (Simplified)", "zh-CN", "936", "936"),
    ("zh_TW", "Chinese (Traditional)", "zh-TW", "950", "950"),
    ("ko_KR", "Korean", "ko-KR", "949", "949"),
    ("de_DE", "German", "de-DE", "1252", "850"),
    ("fr_FR", "French", "fr-FR", "1252", "850"),
    ("es_ES", "Spanish", "es-ES", "1252", "850"),
    ("it_IT", "Italian", "it-IT", "1252", "850"),
    ("pt_BR", "Portuguese (Brazil)", "pt-BR", "1252", "850"),
    ("ru_RU", "Russian", "ru-RU", "1251", "866"),
    ("pl_PL", "Polish", "pl-PL", "1250", "852"),
)

# (name, what it is, the values offered): the settings games most often ask for. Any other value is added by hand.
SETTINGS = (
    ("vd", "Virtual desktop size", ("off", "800x600", "1024x768", "1280x720", "1280x1024", "1440x900", "1600x900", "1920x1080")),
    ("renderer", "Direct3D renderer", ("gdi", "gl", "vulkan", "no3d")),
    ("csmt", "Command stream multithreading", ("on", "off")),
    ("mwo", "Mouse warp override", ("enable", "disable", "force")),
    ("grabfullscreen", "Grab the mouse in fullscreen", ("y", "n")),
    ("hidewineexports", "Hide Wine exports from applications (wine-staging)", ("enable", "disable")),
    ("videomemorysize", "Video memory size (MB)", ("512", "1024", "2048", "default")),
    ("sound", "Sound driver", ("alsa", "pulse", "disabled")),
    ("locale", "System language (locale and code page)", tuple(code for code, *_rest in LANGUAGES)),
)

# (verb, what it is): what games most often ask for, in the order they are listed.
CATALOG = (
    ("vcrun2022", "Visual C++ 2015-2022 runtime"),
    ("vcrun2019", "Visual C++ 2015-2019 runtime"),
    ("vcrun2017", "Visual C++ 2017 runtime"),
    ("vcrun2015", "Visual C++ 2015 runtime"),
    ("vcrun2013", "Visual C++ 2013 runtime"),
    ("vcrun2012", "Visual C++ 2012 runtime"),
    ("vcrun2010", "Visual C++ 2010 runtime"),
    ("vcrun2008", "Visual C++ 2008 runtime"),
    ("vcrun2005", "Visual C++ 2005 runtime"),
    ("vcrun2003", "Visual C++ 2003 runtime"),
    ("vcrun6", "Visual C++ 6 runtime"),
    ("mfc140", "Microsoft Foundation Classes 14 (Visual C++ 2015)"),
    ("mfc42", "Microsoft Foundation Classes 4.2"),
    ("dotnetdesktop8", ".NET Desktop Runtime 8"),
    ("dotnetdesktop6", ".NET Desktop Runtime 6"),
    ("dotnet48", ".NET Framework 4.8"),
    ("dotnet472", ".NET Framework 4.7.2"),
    ("dotnet462", ".NET Framework 4.6.2"),
    ("dotnet452", ".NET Framework 4.5.2"),
    ("dotnet45", ".NET Framework 4.5"),
    ("dotnet40", ".NET Framework 4.0"),
    ("dotnet35", ".NET Framework 3.5"),
    ("dotnet20", ".NET Framework 2.0"),
    ("xna40", "Microsoft XNA Framework 4.0"),
    ("xna31", "Microsoft XNA Framework 3.1"),
    ("d3dx9", "DirectX 9 helper libraries (d3dx9)"),
    ("d3dx9_43", "DirectX 9 helper library (d3dx9_43 only)"),
    ("d3dx10", "DirectX 10 helper libraries (d3dx10)"),
    ("d3dx10_43", "DirectX 10 helper library (d3dx10_43 only)"),
    ("d3dx11_43", "DirectX 11 helper library (d3dx11_43)"),
    ("d3dcompiler_47", "Direct3D shader compiler 47"),
    ("d3dcompiler_46", "Direct3D shader compiler 46"),
    ("d3dcompiler_43", "Direct3D shader compiler 43"),
    ("xinput", "XInput (controllers)"),
    ("dinput8", "DirectInput 8"),
    ("xact", "XACT audio (32 bit)"),
    ("xact_x64", "XACT audio (64 bit)"),
    ("xaudio29", "XAudio 2.9"),
    ("faudio", "FAudio (XAudio reimplementation)"),
    ("directmusic", "DirectMusic"),
    ("dsound", "DirectSound"),
    ("dsoal", "DirectSound on OpenAL (3D sound, EAX)"),
    ("openal", "OpenAL"),
    ("directplay", "DirectPlay"),
    ("physx", "NVIDIA PhysX"),
    ("mf", "Media Foundation, for video"),
    ("quartz", "DirectShow (quartz), for video"),
    ("devenum", "DirectShow device enumeration, for video"),
    ("amstream", "DirectShow streaming, for video"),
    ("lavfilters", "LAV Filters, for video"),
    ("binkw32", "Bink video (32 bit)"),
    ("wmp11", "Windows Media Player 11"),
    ("gfw", "Games for Windows Live"),
    ("cnc_ddraw", "cnc-ddraw (DirectDraw for old games)"),
    ("msxml6", "MSXML 6"),
    ("gdiplus", "GDI+"),
    ("corefonts", "Microsoft core fonts"),
    ("tahoma", "Tahoma font"),
    ("cjkfonts", "Chinese, Japanese and Korean fonts"),
)
_VERBS = frozenset(verb for verb, _text in CATALOG)


def is_verb(value) -> bool:
    """An entry: a verb, or a setting with a value. A verb of the catalog never takes one: winetricks knows no
    `vcrun2015=true`, and through Faugus it would not even reach winetricks (see `launcher.faugus_takes_as_env`)."""
    return isinstance(value, str) and VERB.fullmatch(value) is not None and not ("=" in value and key_of(value) in _VERBS)


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
    return clean([*applied, *new])


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


def removed(kept: list[str], entries: list[str]) -> list[str]:
    """The entries of `entries` whose key has no entry in `kept`: what was unticked (a setting given another value is not)."""
    keys = {key_of(entry) for entry in kept}
    return [entry for entry in entries if key_of(entry) not in keys]


# What winetricks does for the settings below, written down so a setting can be put into the prefix's registry without running
# winetricks: that is silent (winetricks, through umu or Faugus, may open a window of its own, and one for a name it does not
# know). Values are checked here again: they end up in a `.reg` file. Keys are under HKEY_CURRENT_USER\\Software\\Wine.
_SIZE = re.compile(r"[1-9][0-9]{2,4}x[1-9][0-9]{2,4}")
_CHOICES = {
    "renderer": ("Direct3D", "renderer", ("gdi", "gl", "vulkan", "no3d")),
    "ddr": ("Direct3D", "DirectDrawRenderer", ("gdi", "opengl")),
    "glsl": ("Direct3D", "UseGLSL", ("enabled", "disabled")),
    "mwo": ("DirectInput", "MouseWarpOverride", ("enable", "disable", "force")),
}
_YES_NO = {
    "windowmanagerdecorated": "Decorated",
    "windowmanagermanaged": "Managed",
    "usetakefocus": "UseTakeFocus",
    "grabfullscreen": "GrabFullscreen",
}


CODEPAGE_KEY = "HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Control\\Nls\\CodePage"
_INTERNATIONAL = "HKEY_CURRENT_USER\\Control Panel\\International"
_CODEPAGE = CODEPAGE_KEY


def locale_of(entries) -> str | None:
    """The language code (`ja_JP`) of the `locale=` entry in `entries`, when it is one of the offered."""
    for entry in entries:
        name, _sep, value = entry.partition("=")
        if name == "locale" and any(value == code for code, *_rest in LANGUAGES):
            return value
    return None


def locale_env(entries) -> dict[str, str]:
    """The environment a game in a prefix with a `locale=` entry is started with: what makes Wine take that language."""
    code = locale_of(entries)
    return {"LC_ALL": f"{code}.UTF-8", "LANG": f"{code}.UTF-8"} if code else {}


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
    elif name == "locale":
        language = next((lang for lang in LANGUAGES if lang[0] == value), None)
        if language:
            return [(_INTERNATIONAL, f'"LocaleName"="{language[2]}"'), (_CODEPAGE, f'"ACP"="{language[3]}"'), (_CODEPAGE, f'"OEMCP"="{language[4]}"')]
    elif name == "hidewineexports" and value in ("enable", "disable"):
        return [("", '"HideWineExports"="Y"' if value == "enable" else '"HideWineExports"=-')]
    elif name == "videomemorysize" and value == "default":
        return [("Direct3D", '"VideoMemorySize"=-')]
    elif name == "videomemorysize" and value.isdigit() and 1 <= len(value) <= 6:
        return [("Direct3D", f'"VideoMemorySize"="{value}"')]
    return None


def registry_reset(entry: str) -> list[tuple[str, str]] | None:
    """The registry values to delete so that the setting of `entry` is Wine's default again, as if it had never been set: what
    unticking it does. None for a verb, which only a new prefix takes out, or a setting the client does not write itself."""
    name, sep, _value = entry.partition("=")
    if not sep or registry_changes(entry) is None:
        return None
    if name == "vd":
        return [("Explorer", '"Desktop"=-')]
    if name in _CHOICES:
        key, field, _allowed = _CHOICES[name]
        return [(key, f'"{field}"=-')]
    if name == "csmt":
        return [("Direct3D", '"csmt"=-')]
    if name == "sound":
        return [("Drivers", '"Audio"=-')]
    if name in _YES_NO:
        return [("X11 Driver", f'"{_YES_NO[name]}"=-')]
    if name == "videomemorysize":
        return [("Direct3D", '"VideoMemorySize"=-')]
    if name == "hidewineexports":
        return [("", '"HideWineExports"=-')]
    if name == "locale":  # the code pages are put back from what the prefix had (launchsettings.reset_settings)
        return [(_INTERNATIONAL, '"LocaleName"=-')]
    return None

from __future__ import annotations

import re

# A verb goes on a command line: only what verbs are made of, and never something that starts like an option.
VERB = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}")
MAX_VERBS = 100

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


def clean(verbs) -> list[str]:
    """The verbs in `verbs` (a list from the server, so not trusted) that are verbs, once each, in order."""
    if not isinstance(verbs, list):
        return []
    return list(dict.fromkeys(v for v in verbs if is_verb(v)))[:MAX_VERBS]


def missing(wanted: list[str], applied: list[str]) -> list[str]:
    """What of `wanted` is not in `applied` yet, in the order wanted."""
    return [v for v in clean(wanted) if v not in set(applied)]
"""Ed25519 signatures (RFC 8032), in plain Python so the client needs nothing beyond the standard library.

Used to check that a release was signed by the key built into this client (see updater.py). It is the RFC's own reference
code: slow (a few milliseconds a check), which does not matter for one small file per update, and not meant to hide timing:
it only ever handles public data (a public key, a message, a signature) except in `sign`, which the release script runs on a
build machine."""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _add(a: tuple, b: tuple) -> tuple:
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    p1, p2 = (y1 - x1) * (y2 - x2) % _P, (y1 + x1) * (y2 + x2) % _P
    c, d = 2 * t1 * t2 * _D % _P, 2 * z1 * z2 % _P
    e, f, g, h = p2 - p1, d - c, d + c, p2 + p1
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(scalar: int, point: tuple) -> tuple:
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a: tuple, b: tuple) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    return _P - x if (x & 1) != sign else x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(point: tuple) -> bytes:
    zinv = _inv(point[2])
    x, y = point[0] * zinv % _P, point[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes) -> tuple | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _hash_mod_q(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def _expand(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    return (int.from_bytes(digest[:32], "little") & ((1 << 254) - 8)) | (1 << 254), digest[32:]


def public_key(seed: bytes) -> bytes:
    """The 32-byte public key of a 32-byte secret seed."""
    if len(seed) != 32:
        raise ValueError("a seed is 32 bytes")
    return _compress(_mul(_expand(seed)[0], _G))


def sign(seed: bytes, message: bytes) -> bytes:
    """The 64-byte signature of `message` under the secret `seed`."""
    scalar, prefix = _expand(seed)
    public = _compress(_mul(scalar, _G))
    r = _hash_mod_q(prefix + message)
    r_point = _compress(_mul(r, _G))
    s = (r + _hash_mod_q(r_point + public + message) * scalar) % _Q
    return r_point + int.to_bytes(s, 32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether `signature` is `public`'s signature of `message`. Any malformed input is just False."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a_point = _decompress(public)
    r_point = _decompress(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if a_point is None or r_point is None or s >= _Q:
        return False
    h = _hash_mod_q(signature[:32] + public + message)
    return _equal(_mul(s, _G), _add(r_point, _mul(h, a_point)))

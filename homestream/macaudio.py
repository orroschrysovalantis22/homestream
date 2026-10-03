"""macOS sound output switching through CoreAudio itself (no SwitchAudioSource needed).

Used to point the system output at BlackHole while HomeStream relays, and to
put it back afterwards. Plain ctypes, so it works from Terminal and from the
packaged app alike.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import struct

_ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))
_cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))


def _fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode())[0]


SYSTEM_OBJECT = 1
SCOPE_GLOBAL = _fourcc("glob")
SCOPE_OUTPUT = _fourcc("outp")
ELEMENT_MAIN = 0
PROP_DEVICES = _fourcc("dev#")
PROP_DEFAULT_OUTPUT = _fourcc("dOut")
PROP_NAME = _fourcc("lnam")
PROP_STREAMS = _fourcc("stm#")
UTF8 = 0x08000100


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


_ca.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32,
                                               ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
_ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32,
                                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
_ca.AudioObjectSetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32,
                                           ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
_cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
_cf.CFRelease.argtypes = [ctypes.c_void_p]


def _size(obj: int, selector: int, scope: int = SCOPE_GLOBAL) -> int:
    size = ctypes.c_uint32(0)
    status = _ca.AudioObjectGetPropertyDataSize(obj, ctypes.byref(_Address(selector, scope, ELEMENT_MAIN)), 0, None,
                                                ctypes.byref(size))
    return size.value if status == 0 else 0


def _devices() -> list[int]:
    count = _size(SYSTEM_OBJECT, PROP_DEVICES) // 4
    ids = (ctypes.c_uint32 * count)()
    size = ctypes.c_uint32(ctypes.sizeof(ids))
    _ca.AudioObjectGetPropertyData(SYSTEM_OBJECT, ctypes.byref(_Address(PROP_DEVICES, SCOPE_GLOBAL, ELEMENT_MAIN)),
                                   0, None, ctypes.byref(size), ids)
    return list(ids)


def _name(device: int) -> str:
    ref = ctypes.c_void_p()
    size = ctypes.c_uint32(ctypes.sizeof(ref))
    if _ca.AudioObjectGetPropertyData(device, ctypes.byref(_Address(PROP_NAME, SCOPE_GLOBAL, ELEMENT_MAIN)),
                                      0, None, ctypes.byref(size), ctypes.byref(ref)) != 0 or not ref.value:
        return ""
    buffer = ctypes.create_string_buffer(512)
    ok = _cf.CFStringGetCString(ref, buffer, len(buffer), UTF8)
    _cf.CFRelease(ref)
    return buffer.value.decode() if ok else ""


def output_devices() -> dict[str, int]:
    """Name -> id of every device that can play sound."""
    return {_name(d): d for d in _devices() if _size(d, PROP_STREAMS, SCOPE_OUTPUT) > 0}


def default_output() -> str:
    device = ctypes.c_uint32(0)
    size = ctypes.c_uint32(4)
    _ca.AudioObjectGetPropertyData(SYSTEM_OBJECT, ctypes.byref(_Address(PROP_DEFAULT_OUTPUT, SCOPE_GLOBAL, ELEMENT_MAIN)),
                                   0, None, ctypes.byref(size), ctypes.byref(device))
    return _name(device.value)


def set_default_output(name: str) -> bool:
    device = output_devices().get(name)
    if device is None:
        return False
    value = ctypes.c_uint32(device)
    status = _ca.AudioObjectSetPropertyData(
        SYSTEM_OBJECT, ctypes.byref(_Address(PROP_DEFAULT_OUTPUT, SCOPE_GLOBAL, ELEMENT_MAIN)),
        0, None, 4, ctypes.byref(value),
    )
    return status == 0

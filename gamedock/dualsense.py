"""Controle PlayStation (DualSense / DualShock 4) direto pelo HID do Windows: bateria e cor da luz.

Bateria: lida do relatório de entrada que o controle manda o tempo todo (USB e Bluetooth).
Luz: relatório de saída 0x02 (DualSense USB) / 0x31 (DualSense Bluetooth, com CRC32) / 0x05 e 0x11 (DS4),
no mesmo formato do driver hid-playstation do Linux. Com o DS4Windows aberto, ele manda na luz (e pode
esconder o controle de outros programas); aí o GameDock só mostra a bateria, se conseguir ler.
Controle de Xbox: bateria pelo XInputGetBatteryInformation.
"""
from __future__ import annotations

import ctypes
import zlib
from ctypes import byref, wintypes

SONY = 0x054C
MODELS = {0x0CE6: "DualSense", 0x0DF2: "DualSense Edge", 0x05C4: "DualShock 4", 0x09CC: "DualShock 4"}
GENERIC_RW = 0x80000000 | 0x40000000
SHARE_RW = 0x1 | 0x2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID = ctypes.c_void_p(-1).value

setupapi = ctypes.windll.setupapi
hid = ctypes.windll.hid
kernel32 = ctypes.windll.kernel32
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateEventW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
# handles de 64 bits: sem argtypes o ctypes tenta passar como int de 32
_H, _P = wintypes.HANDLE, ctypes.c_void_p
for _fn, _args in ((setupapi.SetupDiEnumDeviceInterfaces, (_H, _P, _P, wintypes.DWORD, _P)),
                   (setupapi.SetupDiGetDeviceInterfaceDetailW, (_H, _P, _P, wintypes.DWORD, _P, _P)),
                   (setupapi.SetupDiDestroyDeviceInfoList, (_H,)),
                   (hid.HidD_GetAttributes, (_H, _P)), (hid.HidD_GetPreparsedData, (_H, _P)),
                   (hid.HidP_GetCaps, (_P, _P)), (hid.HidD_FreePreparsedData, (_P,)),
                   (hid.HidD_GetFeature, (_H, _P, wintypes.ULONG)),
                   (kernel32.ReadFile, (_H, _P, wintypes.DWORD, _P, _P)),
                   (kernel32.WriteFile, (_H, ctypes.c_char_p, wintypes.DWORD, _P, _P)),
                   (kernel32.GetOverlappedResult, (_H, _P, _P, wintypes.BOOL)),
                   (kernel32.WaitForSingleObject, (_H, wintypes.DWORD)), (kernel32.ResetEvent, (_H,)),
                   (kernel32.CancelIo, (_H,)), (kernel32.CloseHandle, (_H,))):
    _fn.argtypes = _args


class GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("guid", GUID), ("flags", wintypes.DWORD), ("reserved", ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT), ("ProductID", wintypes.USHORT),
                ("Version", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT), ("InputReportByteLength", wintypes.USHORT),
                ("OutputReportByteLength", wintypes.USHORT), ("FeatureReportByteLength", wintypes.USHORT),
                ("Reserved", wintypes.USHORT * 17), ("rest", wintypes.USHORT * 10)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p), ("Offset", wintypes.DWORD),
                ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]


def _device_paths() -> list[str]:
    guid = GUID()
    hid.HidD_GetHidGuid(byref(guid))
    info = setupapi.SetupDiGetClassDevsW(byref(guid), None, None, 0x2 | 0x10)   # PRESENT | DEVICEINTERFACE
    paths = []
    i = 0
    while True:
        data = SP_DEVICE_INTERFACE_DATA(cbSize=ctypes.sizeof(SP_DEVICE_INTERFACE_DATA))
        if not setupapi.SetupDiEnumDeviceInterfaces(info, None, byref(guid), i, byref(data)):
            break
        size = wintypes.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(info, byref(data), None, 0, byref(size), None)
        buf = ctypes.create_string_buffer(size.value)
        ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        if setupapi.SetupDiGetDeviceInterfaceDetailW(info, byref(data), buf, size, None, None):
            paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
        i += 1
    setupapi.SetupDiDestroyDeviceInfoList(info)
    return paths


def _open(path: str, overlapped: bool = False):
    handle = kernel32.CreateFileW(path, GENERIC_RW, SHARE_RW, None, OPEN_EXISTING,
                                  FILE_FLAG_OVERLAPPED if overlapped else 0, None)
    return None if handle in (None, INVALID) else handle


def find() -> dict | None:
    """O primeiro controle PlayStation conectado: {"path", "model", "bluetooth", "in_len", "out_len"}."""
    for path in _device_paths():
        if "vid_054c" not in path.lower():
            continue
        handle = _open(path)
        if not handle:
            continue
        try:
            attrs = HIDD_ATTRIBUTES(Size=ctypes.sizeof(HIDD_ATTRIBUTES))
            if not hid.HidD_GetAttributes(handle, byref(attrs)) or attrs.ProductID not in MODELS:
                continue
            pre = ctypes.c_void_p()
            caps = HIDP_CAPS()
            if hid.HidD_GetPreparsedData(handle, byref(pre)):
                hid.HidP_GetCaps(pre, byref(caps))
                hid.HidD_FreePreparsedData(pre)
            if caps.InputReportByteLength == 0:
                continue
            model = MODELS[attrs.ProductID]
            usb_len = 64
            return {"path": path, "model": model, "pid": attrs.ProductID,
                    "bluetooth": caps.InputReportByteLength > usb_len, "in_len": caps.InputReportByteLength,
                    "out_len": caps.OutputReportByteLength}
        finally:
            kernel32.CloseHandle(handle)
    return None


def _read_report(dev: dict, timeout_ms: int = 400) -> bytes | None:
    handle = _open(dev["path"], overlapped=True)
    if not handle:
        return None
    try:
        if dev["bluetooth"] and "DualSense" in dev["model"]:
            feature = ctypes.create_string_buffer(b"\x05" + bytes(40), 41)   # pede o modo completo (0x31)
            hid.HidD_GetFeature(handle, feature, 41)
        buf = ctypes.create_string_buffer(dev["in_len"])
        ov = OVERLAPPED(hEvent=kernel32.CreateEventW(None, True, False, None))
        for _ in range(4):   # pula os relatórios curtos
            kernel32.ResetEvent(ov.hEvent)
            kernel32.ReadFile(handle, buf, dev["in_len"], None, byref(ov))
            if kernel32.WaitForSingleObject(ov.hEvent, timeout_ms) != 0:
                kernel32.CancelIo(handle)
                break
            got = wintypes.DWORD()
            kernel32.GetOverlappedResult(handle, byref(ov), byref(got), False)
            data = buf.raw[:got.value]
            if data and data[0] in (0x01, 0x11, 0x31) and len(data) > 40:
                kernel32.CloseHandle(ov.hEvent)
                return data
        kernel32.CloseHandle(ov.hEvent)
    finally:
        kernel32.CloseHandle(handle)
    return None


def battery() -> dict | None:
    """{"model", "level" (0-100), "charging", "connection"} do controle PlayStation (ou de Xbox) conectado."""
    dev = find()
    if dev:
        data = _read_report(dev)
        if data:
            if "DualSense" in dev["model"]:
                status = data[54] if data[0] == 0x31 else data[53]
                level, state = status & 0x0F, status >> 4
                return {"model": dev["model"], "level": min(100, level * 10 + 5 if state == 0 else level * 10),
                        "charging": state in (1, 2), "full": state == 2,
                        "connection": "Bluetooth" if dev["bluetooth"] else "USB"}
            status = data[32] if data[0] == 0x11 else data[30]
            cable = bool(status & 0x10)
            level = status & 0x0F
            return {"model": dev["model"], "level": min(100, level * 100 // (11 if cable else 10)),
                    "charging": cable and level < 11, "full": cable and level >= 11,
                    "connection": "Bluetooth" if dev["bluetooth"] else "USB"}
        return {"model": dev["model"], "level": None, "connection": "Bluetooth" if dev["bluetooth"] else "USB"}
    return _xinput_battery()


def _xinput_battery() -> dict | None:
    class INFO(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ubyte), ("level", ctypes.c_ubyte)]
    try:
        xinput = ctypes.windll.xinput1_4
    except OSError:
        return None
    for i in range(4):
        info = INFO()
        if xinput.XInputGetBatteryInformation(i, 0, byref(info)) == 0 and info.type != 0:   # 0 = desconectado
            if info.type == 1:   # com fio
                return {"model": "Controle", "level": None, "charging": False, "connection": "USB"}
            return {"model": "Controle", "level": [5, 35, 70, 100][info.level], "charging": False, "connection": "Sem fio"}
    return None


def set_lightbar(rgb: tuple[int, int, int]) -> bool:
    dev = find()
    if not dev or not dev["out_len"]:
        return False
    r, g, b = rgb
    if "DualSense" in dev["model"]:
        common = bytearray(47)
        common[1] = 0x04          # valid_flag1: controle da luz
        common[38] = 0x02         # valid_flag2: libera a luz do padrão azul
        common[41] = 0x02         # lightbar_setup: apaga a animação
        common[42] = 0x00         # brilho máximo dos LEDs de jogador
        common[44], common[45], common[46] = r, g, b
        if dev["bluetooth"]:
            report = bytearray(78)
            report[0], report[1], report[2] = 0x31, 0x00, 0x10
            report[3:50] = common
            report[74:78] = zlib.crc32(b"\xa2" + bytes(report[:74])).to_bytes(4, "little")
        else:
            report = bytearray([0x02]) + common
    else:
        if dev["bluetooth"]:
            report = bytearray(78)
            report[0], report[1], report[3] = 0x11, 0xC0, 0x07
            report[8], report[9], report[10] = r, g, b
            report[74:78] = zlib.crc32(b"\xa2" + bytes(report[:74])).to_bytes(4, "little")
        else:
            report = bytearray(32)
            report[0], report[1] = 0x05, 0x07
            report[6], report[7], report[8] = r, g, b
    report = bytes(report).ljust(dev["out_len"], b"\0")[:max(dev["out_len"], len(report))]
    handle = _open(dev["path"])
    if not handle:
        return False
    try:
        written = wintypes.DWORD()
        return bool(kernel32.WriteFile(handle, report, len(report), byref(written), None))
    finally:
        kernel32.CloseHandle(handle)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)

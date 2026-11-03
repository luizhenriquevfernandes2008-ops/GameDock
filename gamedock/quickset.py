"""Ajustes rápidos do Alt+G (como no Steam Deck) e desempenho ao vivo.

- Volume e mudo do Windows: IAudioEndpointVolume (Core Audio, via COM direto no ctypes).
- Trocar a saída de som (fone/caixa): IPolicyConfig.SetDefaultEndpoint — a mesma interface que o
  painel de som do Windows usa (não documentada, mas estável desde o Windows 7).
- Brilho: tela de notebook via WMI; monitor externo via DDC/CI (dxva2), se o monitor aceitar.
- Desempenho: CPU e memória (psutil); placa NVIDIA pelo nvidia-smi que vem com o driver.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
import uuid
from ctypes import POINTER, byref, c_float, c_void_p, wintypes
from pathlib import Path

import psutil

CREATE_NO_WINDOW = 0x08000000
CLSCTX_ALL = 0x17
CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"
CLSID_PolicyConfig = "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"
IID_IPolicyConfig = "{F8679F50-850A-41CF-9C72-430F290290C8}"


class _GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    ctypes.windll.ole32.CLSIDFromString(text, byref(g))
    return g


def _method(obj: c_void_p, index: int, *argtypes):
    """Método número `index` da vtable de uma interface COM."""
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtable[index])


def _release(obj: c_void_p) -> None:
    if obj:
        _method(obj, 2)(obj)


def _create(clsid: str, iid: str) -> c_void_p:
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)   # COINIT_APARTMENTTHREADED (S_FALSE se já estava)
    obj = c_void_p()
    hr = ctypes.windll.ole32.CoCreateInstance(byref(_guid(clsid)), None, CLSCTX_ALL, byref(_guid(iid)), byref(obj))
    if hr != 0:
        raise OSError(f"CoCreateInstance falhou ({hr & 0xFFFFFFFF:#x})")
    return obj


def _endpoint_volume() -> c_void_p:
    enum = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    device = c_void_p()
    try:
        _method(enum, 4, ctypes.c_int, ctypes.c_int, POINTER(c_void_p))(enum, 0, 1, byref(device))  # eRender, eMultimedia
        volume = c_void_p()
        _method(device, 3, POINTER(_GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(
            device, byref(_guid(IID_IAudioEndpointVolume)), CLSCTX_ALL, None, byref(volume))
        return volume
    finally:
        _release(device)
        _release(enum)


def get_volume() -> dict:
    vol = _endpoint_volume()
    try:
        level, mute = c_float(), wintypes.BOOL()
        _method(vol, 9, POINTER(c_float))(vol, byref(level))
        _method(vol, 15, POINTER(wintypes.BOOL))(vol, byref(mute))
        return {"level": round(level.value * 100), "muted": bool(mute.value)}
    finally:
        _release(vol)


def set_volume(level: int | None = None, muted: bool | None = None) -> dict:
    vol = _endpoint_volume()
    try:
        if level is not None:
            _method(vol, 7, c_float, c_void_p)(vol, c_float(max(0, min(100, level)) / 100), None)
        if muted is not None:
            _method(vol, 14, wintypes.BOOL, c_void_p)(vol, bool(muted), None)
    finally:
        _release(vol)
    return get_volume()


def outputs() -> list[dict]:
    import soundcard
    default = soundcard.default_speaker().id
    return [{"id": s.id, "name": s.name, "default": s.id == default} for s in soundcard.all_speakers()]


def set_output(device_id: str) -> list[dict]:
    policy = _create(CLSID_PolicyConfig, IID_IPolicyConfig)
    try:
        for role in (0, 1, 2):   # console, multimídia, comunicações
            _method(policy, 13, wintypes.LPCWSTR, ctypes.c_int)(policy, device_id, role)
    finally:
        _release(policy)
    return outputs()


# ---------------------------------------------------------------- brilho

def _powershell(script: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                          text=True, creationflags=CREATE_NO_WINDOW, timeout=10).stdout.strip()


class _PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [("handle", wintypes.HANDLE), ("description", wintypes.WCHAR * 128)]


def _ddc_monitors() -> list[_PHYSICAL_MONITOR]:
    user32, dxva2 = ctypes.windll.user32, ctypes.windll.dxva2
    handles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, POINTER(wintypes.RECT), wintypes.LPARAM)
    def each(hmon, _hdc, _rect, _):
        count = wintypes.DWORD()
        if dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, byref(count)) and count.value:
            arr = (_PHYSICAL_MONITOR * count.value)()
            if dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, arr):
                handles.extend(arr)
        return True

    user32.EnumDisplayMonitors(None, None, each, 0)
    return handles


def get_brightness() -> dict:
    out = _powershell("(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness -ErrorAction SilentlyContinue | "
                      "Select-Object -First 1).CurrentBrightness")
    if out.isdigit():
        return {"supported": True, "level": int(out), "kind": "wmi"}
    dxva2 = ctypes.windll.dxva2
    for mon in _ddc_monitors():
        low, cur, high = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        ok = dxva2.GetMonitorBrightness(mon.handle, byref(low), byref(cur), byref(high))
        dxva2.DestroyPhysicalMonitor(mon.handle)
        if ok and high.value > low.value:
            return {"supported": True, "level": round((cur.value - low.value) * 100 / (high.value - low.value)), "kind": "ddc"}
    return {"supported": False}


def set_brightness(level: int) -> dict:
    level = max(0, min(100, int(level)))
    current = get_brightness()
    if current.get("kind") == "wmi":
        _powershell("Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods | "
                    f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={level}}}")
    elif current.get("kind") == "ddc":
        dxva2 = ctypes.windll.dxva2
        for mon in _ddc_monitors():
            low, cur, high = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
            if dxva2.GetMonitorBrightness(mon.handle, byref(low), byref(cur), byref(high)):
                dxva2.SetMonitorBrightness(mon.handle, low.value + (high.value - low.value) * level // 100)
            dxva2.DestroyPhysicalMonitor(mon.handle)
    return get_brightness()


# ---------------------------------------------------------------- desempenho

NVIDIA_SMI = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
_last_gpu: tuple[float, dict] = (0.0, {})


def _gpu() -> dict:
    global _last_gpu
    if time.time() - _last_gpu[0] < 1.5:
        return _last_gpu[1]
    data = {}
    if NVIDIA_SMI.is_file():
        try:
            out = subprocess.run([str(NVIDIA_SMI), "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                 creationflags=CREATE_NO_WINDOW, timeout=4).stdout.strip().splitlines()[0]
            name, util, temp, used, total = [x.strip() for x in out.split(",")]
            data = {"name": name, "usage": int(util), "temp": int(temp), "vram": int(used), "vramTotal": int(total)}
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            data = {}
    _last_gpu = (time.time(), data)
    return data


def performance() -> dict:
    mem = psutil.virtual_memory()
    return {"cpu": round(psutil.cpu_percent(interval=None)), "ram": round(mem.percent), "ramUsed": mem.used,
            "ramTotal": mem.total, "gpu": _gpu()}


# ---------------------------------------------------------------- Discord Rich Presence

class DiscordPresence:
    """Status "Jogando ..." no Discord pelo IPC local (\\\\.\\pipe\\discord-ipc-N). Precisa do Application ID de
    um app criado em discord.com/developers (o nome do app aparece como "Jogando <nome do app>")."""

    def __init__(self):
        self.pipe = None
        self.client_id = None

    def _send(self, op: int, payload: dict) -> dict | None:
        import json
        import struct
        data = json.dumps(payload).encode()
        self.pipe.write(struct.pack("<II", op, len(data)) + data)
        self.pipe.flush()
        header = self.pipe.read(8)
        if len(header) < 8:
            raise OSError("Discord fechou a conexão")
        _op, length = struct.unpack("<II", header)
        return json.loads(self.pipe.read(length) or b"{}")

    def _connect(self, client_id: str) -> bool:
        if self.pipe and self.client_id == client_id:
            return True
        self.close()
        for n in range(10):
            try:
                self.pipe = open(rf"\\.\pipe\discord-ipc-{n}", "r+b", buffering=0)
                break
            except OSError:
                continue
        else:
            return False
        try:
            self._send(0, {"v": 1, "client_id": client_id})
            self.client_id = client_id
            return True
        except OSError:
            self.close()
            return False

    def update(self, client_id: str, activity: dict | None) -> bool:
        if not client_id:
            return False
        try:
            if not self._connect(client_id):
                return False
            self._send(1, {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": activity},
                           "nonce": str(uuid.uuid4())})
            return True
        except OSError:
            self.close()
            return False

    def close(self) -> None:
        if self.pipe:
            try:
                self.pipe.close()
            except OSError:
                pass
        self.pipe = None
        self.client_id = None

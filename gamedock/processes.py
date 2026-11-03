"""Processos em execução com o caminho completo do .exe (via Win32, sem dependências)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD))
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


def running_executables() -> dict[int, str]:
    """{pid: caminho do exe}. Processos protegidos (sistema, anti-cheat) ficam de fora."""
    pids = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    if not _psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return {}
    result = {}
    buffer = ctypes.create_unicode_buffer(1024)
    for pid in pids[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        if not pid:
            continue
        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            size = wintypes.DWORD(len(buffer))
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                result[pid] = buffer.value
        finally:
            _kernel32.CloseHandle(handle)
    return result

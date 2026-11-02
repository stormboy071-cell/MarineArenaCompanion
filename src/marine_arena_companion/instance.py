from __future__ import annotations

import hashlib
import os
from pathlib import Path


class ProfileInstanceLock:
    def __init__(self, base_dir: Path, profile_name: str):
        digest = hashlib.sha256(profile_name.casefold().encode("utf-8")).hexdigest()[:24]
        self._handle: int | None = None
        self._lock_file = None
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_mutex = kernel32.CreateMutexW
            create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
            create_mutex.restype = ctypes.c_void_p
            wait_for_single_object = kernel32.WaitForSingleObject
            wait_for_single_object.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            wait_for_single_object.restype = ctypes.c_uint32
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (ctypes.c_void_p,)
            close_handle.restype = ctypes.c_bool
            handle = create_mutex(None, False, f"Local\\MarineArenaCompanion-{digest}")
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            wait_result = wait_for_single_object(handle, 0)
            if wait_result in (0, 0x80):
                self._handle = handle
            else:
                close_handle(handle)
                if wait_result == 0x102:
                    return
                if wait_result == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                raise RuntimeError(f"Unexpected profile mutex result: {wait_result}")
        else:
            import fcntl

            lock_dir = base_dir / ".locks"
            lock_dir.mkdir(parents=True, exist_ok=True)
            self._lock_file = (lock_dir / f"{digest}.lock").open("a+b")
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._lock_file.close()
                self._lock_file = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None or (
            self._lock_file is not None and not self._lock_file.closed
        )

    def close(self) -> None:
        if self._handle is not None:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            release_mutex = kernel32.ReleaseMutex
            release_mutex.argtypes = (ctypes.c_void_p,)
            release_mutex.restype = ctypes.c_bool
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (ctypes.c_void_p,)
            close_handle.restype = ctypes.c_bool
            if not release_mutex(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            if not close_handle(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None
        if self._lock_file is not None:
            import fcntl

            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    def __enter__(self) -> ProfileInstanceLock:
        if not self.acquired:
            raise RuntimeError("The selected local profile is already open.")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

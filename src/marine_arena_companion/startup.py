from __future__ import annotations

import os
import sys


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MarineArenaCompanion"


def set_windows_startup(enabled: bool) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows auto-start is available only on Windows")
    import winreg

    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if getattr(sys, "frozen", False):
                command = f'"{sys.executable}"'
            else:
                command = f'"{sys.executable}" -m marine_arena_companion'
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        except FileNotFoundError:
            return
        with key:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass

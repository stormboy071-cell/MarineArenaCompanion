from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock


def validate_battle_tag(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Battle.net tag must be text")
    battle_tag = value.strip()
    match = re.fullmatch(r"(.{2,32})#([0-9]{3,8})", battle_tag)
    if not match or any(ord(character) < 32 for character in battle_tag):
        raise ValueError("Enter a Battle.net tag in the format PlayerName#1234")
    return battle_tag


def app_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "MarineArenaCompanion"
    return Path.home() / ".local" / "share" / "MarineArenaCompanion"


@dataclass
class AppConfig:
    battle_tag: str = ""
    replay_folder: str = ""
    watcher_enabled: bool = True
    auto_start: bool = False
    update_repository: str = ""
    updates_enabled: bool = True
    last_update_check: float = 0.0
    dashboard_port: int = 0
    scan_concurrency: int = 1
    current_map_version: str = ""

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            allowed = {key: data[key] for key in asdict(cls()).keys() if key in data}
            config = cls(**allowed)
            if config.battle_tag:
                config.battle_tag = validate_battle_tag(config.battle_tag)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Unable to load configuration at {path}: {exc}") from exc
        if not 0 <= config.dashboard_port <= 65535:
            raise ValueError("dashboard_port must be between 0 and 65535")
        if not 1 <= config.scan_concurrency <= 4:
            raise ValueError("scan_concurrency must be between 1 and 4")
        return config

    def save(self, path: Path) -> None:
        if self.battle_tag:
            self.battle_tag = validate_battle_tag(self.battle_tag)
        if not 0 <= self.dashboard_port <= 65535:
            raise ValueError("dashboard_port must be between 0 and 65535")
        if not 1 <= self.scan_concurrency <= 4:
            raise ValueError("scan_concurrency must be between 1 and 4")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temp.replace(path)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = RLock()
        self._config = AppConfig.load(path)

    def get(self) -> AppConfig:
        with self.lock:
            return AppConfig(**asdict(self._config))

    def update(self, **changes: object) -> AppConfig:
        with self.lock:
            values = asdict(self._config)
            values.update(changes)
            self._config = AppConfig(**values)
            self._config.save(self.path)
            return self.get()


def validate_replay_folder(folder: str | Path) -> Path:
    path = Path(folder).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"Replay folder is not an available directory: {path}")
    return path

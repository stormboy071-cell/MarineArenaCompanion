from __future__ import annotations

import logging
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
import tkinter as tk

from marine_arena_companion.api import CompanionHTTPServer
from marine_arena_companion.config import (
    ConfigStore,
    app_data_dir,
    validate_battle_tag,
    validate_replay_folder,
)
from marine_arena_companion.db.repository import Repository
from marine_arena_companion.desktop.app import DesktopApp
from marine_arena_companion.overlay import OverlaySession
from marine_arena_companion.replay.scanner import ReplayScanner
from marine_arena_companion.replay.semantics import SemanticDictionary
from marine_arena_companion.replay.watcher import ReplayWatcher


def _configure_logging(data_dir: Path) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        handler = RotatingFileHandler(logs / "app.log", maxBytes=2_000_000, backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.addHandler(logging.StreamHandler())


def _require_local_profile(config: ConfigStore) -> bool:
    if config.get().battle_tag:
        return True
    root = tk.Tk()
    root.withdraw()
    dialog = tk.Toplevel(root)
    dialog.title("Set up your profile · Marine Arena Companion")
    dialog.configure(bg="#09111a")
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    result = {"saved": False}
    tag = tk.StringVar()
    error = tk.StringVar()

    def finish(saved: bool) -> None:
        result["saved"] = saved
        if dialog.winfo_exists():
            dialog.grab_release()
            dialog.destroy()

    def save_profile(_event: tk.Event | None = None) -> str:
        error.set("")
        try:
            config.update(battle_tag=validate_battle_tag(tag.get()))
        except ValueError as exc:
            error.set(str(exc))
            entry.focus_set()
            return "break"
        finish(True)
        return "break"

    shell = tk.Frame(dialog, bg="#09111a", highlightbackground="#416975", highlightthickness=1)
    shell.pack(fill="both", expand=True)
    header = tk.Frame(shell, bg="#11232c", padx=25, pady=20)
    header.pack(fill="x")
    tk.Label(
        header, text="MA", bg="#16323a", fg="#72e0e6",
        font=("Segoe UI", 13, "bold"), width=4, height=2,
    ).pack(side="left", padx=(0, 14))
    brand = tk.Frame(header, bg="#11232c")
    brand.pack(side="left", fill="y")
    tk.Label(
        brand, text="MARINE ARENA", bg="#11232c", fg="#eaf3f8",
        font=("Segoe UI", 10, "bold"),
    ).pack(anchor="w")
    tk.Label(
        brand, text="REPLAY COMPANION", bg="#11232c", fg="#93a8b7",
        font=("Segoe UI", 8),
    ).pack(anchor="w", pady=(3, 0))

    content = tk.Frame(shell, bg="#09111a", padx=28, pady=23)
    content.pack(fill="both", expand=True)
    tk.Label(
        content, text="Set up your profile", bg="#09111a", fg="#eaf3f8",
        font=("Segoe UI Semibold", 18),
    ).pack(anchor="w")
    tk.Label(
        content, text="Enter your Battle.net tag to identify your replay profile.",
        bg="#09111a", fg="#93a8b7", font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(6, 18))
    tk.Label(
        content, text="BATTLE.NET TAG", bg="#09111a", fg="#91a2b3",
        font=("Segoe UI Semibold", 8),
    ).pack(anchor="w", pady=(0, 6))
    entry = tk.Entry(
        content, textvariable=tag, bg="#0e1721", fg="#e9f0f6",
        insertbackground="#66d9e8", selectbackground="#176f78",
        relief="flat", highlightthickness=1, highlightbackground="#2b3d4d",
        highlightcolor="#66d9e8", font=("Segoe UI", 10),
    )
    entry.pack(fill="x", ipady=9)
    entry.insert(0, "")
    entry.focus_set()
    tk.Label(
        content, text="PlayerName#1234", bg="#09111a", fg="#718596",
        font=("Segoe UI", 8),
    ).pack(anchor="w", pady=(5, 0))
    tk.Label(
        content, textvariable=error, bg="#09111a", fg="#ed8a92",
        font=("Segoe UI", 9), anchor="w",
    )
    error_label.pack(fill="x", pady=(7, 0))
    actions = tk.Frame(content, bg="#09111a")
    actions.pack(fill="x", pady=(14, 0))
    tk.Button(
        actions, text="Continue", command=save_profile,
        bg="#176f78", fg="#f2ffff", activebackground="#208995",
        activeforeground="#ffffff", relief="flat", borderwidth=0,
        font=("Segoe UI Semibold", 9), padx=17, pady=9, cursor="hand2",
    ).pack(side="right")
    tk.Button(
        actions, text="Exit", command=lambda: finish(False),
        bg="#172633", fg="#dce8ee", activebackground="#263d4c",
        activeforeground="#ffffff", relief="flat", borderwidth=0,
        font=("Segoe UI Semibold", 9), padx=17, pady=9, cursor="hand2",
    ).pack(side="right", padx=(0, 8))
    dialog.bind("<Return>", save_profile)
    dialog.bind("<Escape>", lambda _event: finish(False))
    dialog.geometry("520x360")
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2
    y = (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2
    dialog.geometry(f"520x360+{x}+{y}")
    dialog.grab_set()
    root.wait_window(dialog)
    root.destroy()
    return result["saved"]


def main() -> None:
    data_dir = app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _configure_logging(data_dir)
    logging.info("Marine Arena Companion startup")
    config = ConfigStore(data_dir / "config.json")
    if not _require_local_profile(config):
        logging.info("Startup cancelled before local player profile was configured")
        return
    semantics_path = data_dir / "data" / "marine_arena_semantics.json"
    if not semantics_path.exists():
        semantics_path.parent.mkdir(parents=True, exist_ok=True)
        filename = "marine_arena_semantics.json"
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        candidates = [
            Path(__file__).resolve().parents[2] / "data" / filename,
            bundle_root / "marine_arena_companion" / "data" / filename,
            Path(__file__).resolve().parent / "data" / filename,
        ]
        resource = next((candidate for candidate in candidates if candidate.is_file()), None)
        if resource:
            shutil.copyfile(resource, semantics_path)
        else:
            logging.error("Semantic dictionary resource is missing; expected one of %s", candidates)
    repository = Repository(data_dir / "marine_arena.db")
    overlay = OverlaySession()
    semantics = SemanticDictionary(semantics_path)
    repository.save_semantic_mappings(semantics.entries())
    scanner = ReplayScanner(repository, config.get().scan_concurrency, semantics)
    watcher = ReplayWatcher(scanner)
    try:
        server = CompanionHTTPServer("127.0.0.1", config.get().dashboard_port, config, repository, scanner, watcher, overlay)
    except OSError:
        logging.exception("Configured dashboard port %s is unavailable; using an available local port", config.get().dashboard_port)
        server = CompanionHTTPServer("127.0.0.1", 0, config, repository, scanner, watcher, overlay)
    server.start()
    if config.get().dashboard_port != server.port:
        config.update(dashboard_port=server.port)
    folder = config.get().replay_folder
    if config.get().watcher_enabled and folder:
        try:
            watcher.start(validate_replay_folder(folder))
        except ValueError as exc:
            watcher.error = str(exc)
            logging.error("Configured replay folder is unavailable: %s", exc)
    try:
        DesktopApp(config, repository, scanner, watcher, server, data_dir, overlay).run()
    except Exception:
        logging.exception("Desktop application failed")
        watcher.stop()
        scanner.close()
        server.stop()
        raise
    logging.info("Marine Arena Companion shutdown")


if __name__ == "__main__":
    main()

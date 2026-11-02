from __future__ import annotations

import logging
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from marine_arena_companion.api import CompanionHTTPServer
from marine_arena_companion.config import (
    ConfigStore,
    app_data_dir,
    local_profile_data_dir,
    local_profile_tags,
    validate_battle_tag,
    validate_replay_folder,
)
from marine_arena_companion.db.repository import Repository
from marine_arena_companion.desktop.app import DesktopApp
from marine_arena_companion.instance import ProfileInstanceLock
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


def _select_local_profile(base_dir: Path, profiles: list[str] | None = None) -> str | None:
    profiles = local_profile_tags(base_dir) if profiles is None else profiles
    if len(profiles) == 1:
        return profiles[0]
    root = tk.Tk()
    root.title("Choose a profile · Marine Arena Companion")
    root.configure(bg="#09111a")
    root.resizable(False, False)
    result: dict[str, str | None] = {"tag": None}
    selected = tk.StringVar(value=profiles[0] if profiles else "")
    new_tag = tk.StringVar()
    error = tk.StringVar()

    def finish(tag: str | None) -> None:
        result["tag"] = tag
        if root.winfo_exists():
            root.destroy()

    def open_selected() -> None:
        try:
            finish(validate_battle_tag(selected.get()))
        except ValueError as exc:
            error.set(str(exc))

    def add_profile() -> None:
        error.set("")
        try:
            finish(validate_battle_tag(new_tag.get()))
        except ValueError as exc:
            error.set(str(exc))
            entry.focus_set()

    shell = tk.Frame(root, bg="#09111a", highlightbackground="#416975", highlightthickness=1)
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
        content, text="Choose your local profile", bg="#09111a", fg="#eaf3f8",
        font=("Segoe UI Semibold", 18),
    ).pack(anchor="w")
    tk.Label(
        content,
        text="Choose a local identity for this session. Profiles keep separate settings, share the indexed replay library, and do not sign in to Battle.net.",
        bg="#09111a", fg="#93a8b7", font=("Segoe UI", 9), wraplength=430, justify="left",
    ).pack(anchor="w", pady=(6, 14))
    tk.Label(
        content, text="SAVED PROFILES", bg="#09111a", fg="#91a2b3",
        font=("Segoe UI Semibold", 8),
    ).pack(anchor="w", pady=(0, 6))
    profile_picker = ttk.Combobox(
        content, textvariable=selected, values=profiles,
        state="readonly" if profiles else "disabled",
    )
    profile_picker.pack(fill="x", ipady=4)
    tk.Label(
        content, text="ADD OR OPEN ANOTHER PROFILE · BATTLE.NET TAG",
        bg="#09111a", fg="#91a2b3", font=("Segoe UI Semibold", 8),
    ).pack(anchor="w", pady=(14, 6))
    entry = tk.Entry(
        content, textvariable=new_tag, bg="#0e1721", fg="#e9f0f6",
        insertbackground="#66d9e8", selectbackground="#176f78",
        relief="flat", highlightthickness=1, highlightbackground="#2b3d4d",
        highlightcolor="#66d9e8", font=("Segoe UI", 10),
    )
    entry.pack(fill="x", ipady=7)
    tk.Label(content, text="PlayerName#1234", bg="#09111a", fg="#718596", font=("Segoe UI", 8)).pack(
        anchor="w", pady=(5, 0),
    )
    error_label = tk.Label(
        content, textvariable=error, bg="#09111a", fg="#ed8a92",
        font=("Segoe UI", 9), anchor="w",
    )
    error_label.pack(fill="x", pady=(7, 0))
    actions = tk.Frame(content, bg="#09111a")
    actions.pack(fill="x", pady=(14, 0))
    if profiles:
        tk.Button(
            actions, text="Open selected profile", command=open_selected,
            bg="#176f78", fg="#f2ffff", activebackground="#208995",
            activeforeground="#ffffff", relief="flat", borderwidth=0,
            font=("Segoe UI Semibold", 9), padx=17, pady=9, cursor="hand2",
        ).pack(side="right")
        tk.Button(
            actions, text="Add another profile", command=add_profile,
            bg="#172633", fg="#dce8ee", activebackground="#263d4c",
            activeforeground="#ffffff", relief="flat", borderwidth=0,
            font=("Segoe UI Semibold", 9), padx=17, pady=9, cursor="hand2",
        ).pack(side="right", padx=(0, 8))
    else:
        tk.Button(
            actions, text="Open profile", command=add_profile,
            bg="#176f78", fg="#f2ffff", activebackground="#208995",
            activeforeground="#ffffff", relief="flat", borderwidth=0,
            font=("Segoe UI Semibold", 9), padx=17, pady=9, cursor="hand2",
        ).pack(side="right")
    tk.Button(
        actions, text="Exit", command=lambda: finish(None),
        bg="#172633", fg="#dce8ee", activebackground="#263d4c",
        activeforeground="#ffffff", relief="flat", borderwidth=0,
        font=("Segoe UI Semibold", 9), padx=17, pady=9, cursor="hand2",
    ).pack(side="left")
    root.bind("<Return>", lambda _event: open_selected() if profiles else add_profile())
    root.bind("<Escape>", lambda _event: finish(None))
    root.protocol("WM_DELETE_WINDOW", lambda: finish(None))
    root.geometry("560x390")
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 2
    root.geometry(f"560x390+{x}+{y}")
    root.deiconify()
    root.lift()
    root.focus_force()
    if profiles:
        profile_picker.focus_set()
    else:
        entry.focus_set()
    root.mainloop()
    return result["tag"]


def main() -> None:
    base_dir = app_data_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    battle_tag = _select_local_profile(base_dir)
    if not battle_tag:
        return
    data_dir = local_profile_data_dir(base_dir, battle_tag)
    data_dir.mkdir(parents=True, exist_ok=True)
    instance_lock = ProfileInstanceLock(base_dir, battle_tag)
    if not instance_lock.acquired:
        notice_root = tk.Tk()
        notice_root.withdraw()
        from tkinter import messagebox

        messagebox.showinfo(
            "Profile already open",
            f"{battle_tag} already has Marine Arena Companion open. Choose a different profile to run another account.",
            parent=notice_root,
        )
        notice_root.destroy()
        return
    try:
        config = ConfigStore(data_dir / "config.json")
        if config.get().battle_tag.casefold() != battle_tag.casefold():
            config.update(battle_tag=battle_tag)
        _configure_logging(data_dir)
        logging.info("Marine Arena Companion startup for local profile %s", battle_tag)
        _run_profile(data_dir, base_dir, config)
    finally:
        instance_lock.close()


def _run_profile(data_dir: Path, shared_data_dir: Path, config: ConfigStore) -> None:
    semantics_path = shared_data_dir / "data" / "marine_arena_semantics.json"
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
    repository = Repository(shared_data_dir / "marine_arena.db")
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

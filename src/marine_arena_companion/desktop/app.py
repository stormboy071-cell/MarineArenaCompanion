from __future__ import annotations

import logging
import os
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from marine_arena_companion import __version__
from marine_arena_companion.api import CompanionHTTPServer
from marine_arena_companion.config import ConfigStore, validate_replay_folder
from marine_arena_companion.db.repository import Repository
from marine_arena_companion.diagnostics import export_diagnostics
from marine_arena_companion.desktop.workspace import NativeWorkspace
from marine_arena_companion.overlay import OverlaySession
from marine_arena_companion.replay.scanner import ReplayScanner
from marine_arena_companion.replay.watcher import ReplayWatcher
from marine_arena_companion.startup import set_windows_startup
from marine_arena_companion.timing import format_elapsed_time
from marine_arena_companion.updates import (
    Release,
    UpdateError,
    default_update_repository,
    launch_installer,
    latest_release,
    remove_staged_update,
    stage_update,
    validate_repository,
)


LOG = logging.getLogger(__name__)


class DesktopApp:
    def __init__(self, config: ConfigStore, repository: Repository, scanner: ReplayScanner,
                 watcher: ReplayWatcher, server: CompanionHTTPServer, data_dir: Path,
                 overlay: OverlaySession | None = None):
        self.config = config
        self.repository = repository
        self.scanner = scanner
        self.watcher = watcher
        self.server = server
        self.data_dir = data_dir
        self.overlay = overlay or OverlaySession()
        self.root = tk.Tk()
        self.root.title("Marine Arena Companion")
        self.root.geometry("920x680")
        self.root.minsize(780, 600)
        self.root.configure(bg="#09111a")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.tray_available = False
        self._tray_icon = None
        self._overlay_window: tk.Toplevel | None = None
        self._overlay_rows: dict[int, tuple[tk.StringVar, tk.Frame]] = {}
        self._overlay_signature: tuple[tuple[object, ...], ...] | None = None
        self._overlay_next_reminder_refresh = 0.0
        self._overlay_drag_origin: tuple[int, int] | None = None
        self._overlay_timer_var: tk.StringVar | None = None
        self._overlay_state_var: tk.StringVar | None = None
        self._overlay_alpha = tk.DoubleVar(value=0.93)
        self._update_checking = False
        self.version = __version__
        self._build_ui()
        self._start_tray()
        self._refresh()
        self.root.after(3500, self._check_updates_automatically)

    def _build_ui(self) -> None:
        self.workspace = NativeWorkspace(self)
        self.metric_vars = self.workspace.metric_vars
        self.status_var = self.workspace.status_var
        self.folder_var = self.workspace.folder_state_var
        self.progress_var = self.workspace.progress_var
        self.auto_start_var = self.workspace.auto_start_var

    def _button(self, parent: ttk.Widget, text: str, action: Callable[[], None],
                style: str = "TButton") -> ttk.Button:
        return ttk.Button(parent, text=text, command=action, style=style)

    def _select_folder(self) -> None:
        current = self.config.get().replay_folder
        selection = filedialog.askdirectory(
            parent=self.root, title="Select StarCraft II replay folder",
            initialdir=current if Path(current).is_dir() else str(Path.home()), mustexist=True,
        )
        if not selection:
            return
        try:
            folder = validate_replay_folder(selection)
            self.config.update(replay_folder=str(folder), watcher_enabled=True)
            self.watcher.start(folder)
            LOG.info("Replay folder configured: %s", folder)
            self._refresh()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Folder unavailable", str(exc), parent=self.root)

    def _scan(self) -> None:
        try:
            folder = validate_replay_folder(self.config.get().replay_folder)
        except ValueError as exc:
            messagebox.showwarning("Select replay folder", str(exc), parent=self.root)
            return
        if not self.scanner.scan_library(folder):
            messagebox.showinfo("Scan in progress", "A replay library scan is already running.", parent=self.root)
        self._refresh()

    def _pause(self) -> None:
        self.watcher.stop()
        self.config.update(watcher_enabled=False)
        self._refresh()

    def _retry_failed(self) -> None:
        count = self.scanner.retry_failed()
        if count == 0:
            messagebox.showinfo("No failed replays", "No retryable failed replay files were found.", parent=self.root)
        self._refresh()

    def _toggle_auto_start(self) -> None:
        enabled = self.auto_start_var.get()
        try:
            set_windows_startup(enabled)
            self.config.update(auto_start=enabled)
        except (OSError, RuntimeError) as exc:
            self.auto_start_var.set(not enabled)
            messagebox.showerror("Auto-start setting failed", str(exc), parent=self.root)

    def _resume(self) -> None:
        try:
            folder = validate_replay_folder(self.config.get().replay_folder)
        except ValueError as exc:
            messagebox.showwarning("Folder unavailable", str(exc), parent=self.root)
            return
        self.watcher.start(folder)
        self.config.update(watcher_enabled=True)
        self._refresh()

    def _open_dashboard(self) -> None:
        self.root.deiconify()
        self.workspace.show_page("overview")

    def _open_overlay(self) -> None:
        self._open_game_overlay()

    def _open_reminder_editor(self) -> None:
        self.root.deiconify()
        self.workspace.show_page("reminders")

    def _open_game_overlay(self) -> None:
        if self._overlay_window and self._overlay_window.winfo_exists():
            self._overlay_window.deiconify()
            self._overlay_window.lift()
            self._overlay_window.attributes("-topmost", True)
            return

        window = tk.Toplevel(self.root)
        self._overlay_window = window
        window.title("Marine Arena Game Reminders")
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", self._overlay_alpha.get())
        width, height = 390, 480
        x = max(0, window.winfo_screenwidth() - width - 32)
        window.geometry(f"{width}x{height}+{x}+100")
        window.configure(bg="#0b131d", highlightbackground="#416975", highlightthickness=1)
        window.protocol("WM_DELETE_WINDOW", self._close_game_overlay)

        header = tk.Frame(window, bg="#11232c", padx=14, pady=10)
        header.pack(fill="x")
        title = tk.Label(
            header, text="MATCH REMINDERS", bg="#11232c", fg="#81e8ec",
            font=("Segoe UI", 9, "bold"),
        )
        title.pack(side="left")
        close_button = tk.Button(
            header, text="×", command=self._close_game_overlay,
            bg="#11232c", fg="#b8cad4", activebackground="#223541",
            activeforeground="#ffffff", relief="flat", borderwidth=0,
            font=("Segoe UI", 16), padx=6, pady=0,
        )
        close_button.pack(side="right")
        for widget in (header, title):
            widget.bind("<ButtonPress-1>", self._overlay_drag_start)
            widget.bind("<B1-Motion>", self._overlay_drag_move)

        timer = tk.Frame(window, bg="#0e1a24", padx=14, pady=10)
        timer.pack(fill="x")
        self._overlay_timer_var = tk.StringVar(value="00:00")
        tk.Label(
            timer, textvariable=self._overlay_timer_var, bg="#0e1a24",
            fg="#e7f2f6", font=("Consolas", 24, "bold"),
        ).pack(side="left")
        self._overlay_state_var = tk.StringVar(value="READY")
        tk.Label(
            timer, textvariable=self._overlay_state_var, bg="#0e1a24",
            fg="#91a5b4", font=("Segoe UI", 8, "bold"),
        ).pack(side="right", pady=(8, 0))

        actions = tk.Frame(window, bg="#0b131d", padx=10, pady=8)
        actions.pack(fill="x")
        self._overlay_action_button(actions, "START", self.overlay.start).pack(side="left", padx=3)
        self._overlay_action_button(actions, "PAUSE", self.overlay.pause).pack(side="left", padx=3)
        self._overlay_action_button(actions, "RESET", self.overlay.reset).pack(side="left", padx=3)
        tk.Label(
            actions, text="Opacity", bg="#0b131d", fg="#91a5b4",
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(12, 4))
        opacity = tk.Scale(
            actions, from_=0.72, to=1.0, resolution=0.02, orient="horizontal",
            showvalue=False, length=75, variable=self._overlay_alpha,
            command=self._set_overlay_alpha, bg="#0b131d", fg="#b8cad4",
            troughcolor="#263b49", highlightthickness=0, bd=0,
        )
        opacity.pack(side="left", fill="x", expand=True)

        divider = tk.Frame(window, height=1, bg="#283a47")
        divider.pack(fill="x", padx=12)
        tk.Label(
            window, text="YOUR TIMED CUES", bg="#0b131d", fg="#91a5b4",
            font=("Segoe UI", 8, "bold"), anchor="w", padx=14, pady=9,
        ).pack(fill="x")
        list_container = tk.Frame(window, bg="#0b131d")
        list_container.pack(fill="both", expand=True, padx=10, pady=(0, 9))
        canvas = tk.Canvas(list_container, bg="#0b131d", highlightthickness=0)
        scrollbar = tk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        self._overlay_list = tk.Frame(canvas, bg="#0b131d")
        self._overlay_list.bind(
            "<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        list_window = canvas.create_window((0, 0), window=self._overlay_list, anchor="nw", tags="reminders")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(list_window, width=event.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))

        footer = tk.Label(
            window, text="Always on top · Drag header to move · Borderless game mode recommended",
            bg="#0b131d", fg="#738797", font=("Segoe UI", 7), padx=12, pady=7,
        )
        footer.pack(fill="x")
        self._overlay_signature = None
        self._refresh_game_overlay()

    @staticmethod
    def _overlay_action_button(parent: tk.Widget, text: str, action: Callable[[], object]) -> tk.Button:
        return tk.Button(
            parent, text=text, command=action, bg="#15323b", fg="#d9f6f6",
            activebackground="#20515b", activeforeground="#ffffff",
            relief="flat", borderwidth=0, padx=9, pady=6,
            font=("Segoe UI", 8, "bold"),
        )

    def _set_overlay_alpha(self, value: str) -> None:
        if self._overlay_window and self._overlay_window.winfo_exists():
            try:
                self._overlay_window.attributes("-alpha", float(value))
            except tk.TclError:
                LOG.warning("This Windows environment does not support overlay transparency")

    def _overlay_drag_start(self, event: tk.Event) -> None:
        if self._overlay_window:
            self._overlay_drag_origin = (
                event.x_root - self._overlay_window.winfo_x(),
                event.y_root - self._overlay_window.winfo_y(),
            )

    def _overlay_drag_move(self, event: tk.Event) -> None:
        if self._overlay_window and self._overlay_drag_origin:
            x = event.x_root - self._overlay_drag_origin[0]
            y = event.y_root - self._overlay_drag_origin[1]
            self._overlay_window.geometry(f"+{x}+{y}")

    def _refresh_game_overlay(self) -> None:
        window = self._overlay_window
        if not window or not window.winfo_exists():
            return
        session = self.overlay.status()
        elapsed = int(session["elapsed_seconds"])
        if self._overlay_timer_var:
            self._overlay_timer_var.set(format_elapsed_time(elapsed))
        if self._overlay_state_var:
            self._overlay_state_var.set("MATCH TIMER RUNNING" if session["running"] else "PAUSED · START WHEN READY")

        now = time.monotonic()
        reminders = getattr(self, "_overlay_reminders", [])
        if now >= self._overlay_next_reminder_refresh:
            reminders = self.repository.list_reminders(enabled_only=True)
            self._overlay_reminders = reminders
            self._overlay_next_reminder_refresh = now + 5
        signature = tuple(
            (item["id"], item["title"], item["at_seconds"], item["category"], item["item_label"], item["note"])
            for item in reminders
        )
        if signature != self._overlay_signature:
            self._overlay_signature = signature
            for child in self._overlay_list.winfo_children():
                child.destroy()
            self._overlay_rows.clear()
            if not reminders:
                tk.Label(
                    self._overlay_list,
                    text="No reminders yet.\nOpen Configure Reminders to create your first cue.",
                    justify="left", wraplength=320, bg="#0b131d", fg="#91a5b4",
                    font=("Segoe UI", 10), padx=8, pady=14,
                ).pack(fill="x", anchor="w")
            for reminder in reminders:
                row = tk.Frame(
                    self._overlay_list, bg="#111d28", highlightbackground="#283946",
                    highlightthickness=1, padx=10, pady=8,
                )
                row.pack(fill="x", pady=3, padx=2)
                at_seconds = int(reminder["at_seconds"])
                at_time = format_elapsed_time(at_seconds)
                item = reminder["item_label"]
                category = reminder["category"].replace("_", " ").upper()
                header_text = f"{at_time}  ·  {category}" + (f"  ·  {item}" if item else "")
                tk.Label(
                    row, text=header_text, bg="#111d28", fg="#8bdde4",
                    font=("Segoe UI", 8, "bold"), anchor="w",
                ).pack(fill="x")
                tk.Label(
                    row, text=reminder["title"], bg="#111d28", fg="#eef4f7",
                    font=("Segoe UI", 10, "bold"), anchor="w", wraplength=330,
                ).pack(fill="x", pady=(3, 0))
                if reminder["note"]:
                    tk.Label(
                        row, text=reminder["note"], bg="#111d28", fg="#a6b7c3",
                        font=("Segoe UI", 8), anchor="w", wraplength=330, justify="left",
                    ).pack(fill="x", pady=(2, 0))
                countdown = tk.StringVar()
                footer = tk.Frame(row, bg="#111d28")
                footer.pack(fill="x", pady=(5, 0))
                tk.Label(
                    footer, textvariable=countdown, bg="#111d28", fg="#a6b7c3",
                    font=("Segoe UI", 8),
                ).pack(side="left")
                due_button = self._overlay_action_button(
                    footer, "DONE", lambda reminder_id=reminder["id"]: self.overlay.dismiss(reminder_id)
                )
                due_button.pack(side="right")
                self._overlay_rows[int(reminder["id"])] = (countdown, row)
                setattr(row, "_due_button", due_button)

        dismissed = set(session["dismissed_reminder_ids"])
        for reminder in reminders:
            reminder_id = int(reminder["id"])
            if reminder_id not in self._overlay_rows:
                continue
            countdown, row = self._overlay_rows[reminder_id]
            remaining = int(reminder["at_seconds"]) - elapsed
            due = remaining <= 0
            row.configure(bg="#18332e" if due else "#111d28")
            for child in row.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg="#18332e" if due else "#111d28")
            footer = next((child for child in row.winfo_children() if isinstance(child, tk.Frame)), None)
            if footer:
                for child in footer.winfo_children():
                    if isinstance(child, tk.Label):
                        child.configure(bg="#18332e" if due else "#111d28")
            if reminder_id in dismissed:
                row.pack_forget()
            else:
                row.pack(fill="x", pady=3, padx=2)
                if due:
                    countdown.set("READY · tap done")
                    row.configure(highlightbackground="#4c8874")
                else:
                    countdown.set(f"IN {format_elapsed_time(remaining)}")
                    row.configure(highlightbackground="#283946")
                due_button = getattr(row, "_due_button")
                due_button.configure(state="normal" if due else "disabled")
        window.after(1000, self._refresh_game_overlay)

    def _close_game_overlay(self) -> None:
        if self._overlay_window and self._overlay_window.winfo_exists():
            self.root.unbind_all("<MouseWheel>")
            self._overlay_window.destroy()
        self._overlay_window = None
        self._overlay_rows.clear()
        self._overlay_signature = None
        self._overlay_drag_origin = None

    def _export(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root, title="Export diagnostics", defaultextension=".zip",
            initialfile="MarineArenaCompanion-diagnostics.zip",
            filetypes=[("ZIP archive", "*.zip")],
        )
        if not destination:
            return
        try:
            path = export_diagnostics(
                Path(destination), self.repository, self.data_dir / "data" / "marine_arena_semantics.json"
            )
            messagebox.showinfo("Diagnostics exported", f"Saved: {path}", parent=self.root)
        except OSError as exc:
            LOG.exception("Diagnostics export failed")
            messagebox.showerror("Export failed", str(exc), parent=self.root)

    def _open_logs(self) -> None:
        log_dir = self.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(log_dir))  # type: ignore[attr-defined]
        except AttributeError:
            webbrowser.open(log_dir.as_uri())
        except OSError as exc:
            messagebox.showerror("Unable to open logs", str(exc), parent=self.root)

    def _check_updates_automatically(self) -> None:
        config = self.config.get()
        repository = config.update_repository or default_update_repository()
        if (
            not getattr(sys, "frozen", False)
            or not config.updates_enabled
            or not repository
            or time.time() - config.last_update_check < 24 * 60 * 60
        ):
            return
        self._start_update_check(repository, manual=False)

    def _check_updates_manually(self) -> None:
        if self._update_checking:
            return
        repository = self.config.get().update_repository or default_update_repository()
        if not repository:
            repository = simpledialog.askstring(
                "Set up automatic updates",
                "Enter the public GitHub repository for Marine Arena Companion (owner/name):",
                parent=self.root,
            )
            if repository is None:
                return
        try:
            repository = validate_repository(repository)
            self.config.update(update_repository=repository)
        except ValueError as exc:
            messagebox.showerror("Invalid update source", str(exc), parent=self.root)
            return
        self._start_update_check(repository, manual=True)

    def _start_update_check(self, repository: str, manual: bool) -> None:
        if self._update_checking:
            return
        self._update_checking = True
        self.update_button.configure(state="disabled", text="CHECKING...")

        def check() -> None:
            try:
                release = latest_release(repository)
                self.root.after(0, lambda: self._update_check_finished(release, manual, None))
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._update_check_finished(None, manual, error))

        threading.Thread(target=check, daemon=True, name="update-check").start()

    def _update_check_finished(self, release: Release | None, manual: bool, error: Exception | None) -> None:
        self._update_checking = False
        self.update_button.configure(state="normal", text="CHECK FOR UPDATES")
        if error is not None:
            LOG.warning("Application update check failed: %s", error)
            if manual:
                messagebox.showerror("Update check failed", str(error), parent=self.root)
            return
        self.config.update(last_update_check=time.time())
        if release is None:
            if manual:
                messagebox.showinfo("You're up to date", "This is the latest available version.", parent=self.root)
            return
        if self._show_update_dialog(release):
            self._install_update(release)

    def _show_update_dialog(self, release: Release) -> bool:
        self.root.deiconify()
        self.root.lift()
        dialog = tk.Toplevel(self.root)
        dialog.title("Update available · Marine Arena Companion")
        dialog.configure(bg="#09111a")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        result = {"install": False}

        shell = tk.Frame(dialog, bg="#09111a", highlightbackground="#416975", highlightthickness=1)
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg="#11232c", padx=22, pady=17)
        header.pack(fill="x")
        tk.Label(
            header, text="UPDATE AVAILABLE", bg="#11232c", fg="#72e0e6",
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        tk.Label(
            header, text=f"Marine Arena Companion {release.version}",
            bg="#11232c", fg="#eaf3f8", font=("Segoe UI Semibold", 16),
        ).pack(anchor="w", pady=(5, 0))

        content = tk.Frame(shell, bg="#09111a", padx=22, pady=17)
        content.pack(fill="both", expand=True)
        tk.Label(
            content, text=release.title, bg="#09111a", fg="#e9f0f6",
            font=("Segoe UI Semibold", 10), anchor="w",
        ).pack(fill="x", pady=(0, 7))
        notes = tk.Text(
            content, height=8, width=64, wrap="word", bg="#0e1721", fg="#aebdca",
            relief="flat", padx=10, pady=9, font=("Segoe UI", 9),
        )
        notes.insert("1.0", release.notes or "This release does not include release notes.")
        notes.configure(state="disabled")
        notes.pack(fill="both", expand=True)
        tk.Label(
            content, text="Your replay library, reminders, and settings will be kept.",
            bg="#09111a", fg="#91a2b3", font=("Segoe UI", 8), anchor="w",
        ).pack(fill="x", pady=(10, 0))
        actions = tk.Frame(content, bg="#09111a")
        actions.pack(fill="x", pady=(15, 0))

        def finish(install: bool) -> None:
            result["install"] = install
            if dialog.winfo_exists():
                dialog.grab_release()
                dialog.destroy()

        tk.Button(
            actions, text="Install update", command=lambda: finish(True),
            bg="#176f78", fg="#f2ffff", activebackground="#208995",
            activeforeground="#ffffff", relief="flat", borderwidth=0,
            padx=14, pady=8, font=("Segoe UI Semibold", 9), cursor="hand2",
        ).pack(side="right")
        tk.Button(
            actions, text="Later", command=lambda: finish(False),
            bg="#172633", fg="#dce8ee", activebackground="#263d4c",
            activeforeground="#ffffff", relief="flat", borderwidth=0,
            padx=14, pady=8, font=("Segoe UI Semibold", 9), cursor="hand2",
        ).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        dialog.bind("<Escape>", lambda _event: finish(False))
        dialog.update_idletasks()
        width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.wait_window()
        return result["install"]

    def _install_update(self, release: Release) -> None:
        if not getattr(sys, "frozen", False):
            messagebox.showerror(
                "Cannot install update",
                "Updates can be installed from the packaged Windows app, not a development run.",
                parent=self.root,
            )
            return
        self.update_button.configure(state="disabled", text="DOWNLOADING...")

        def install() -> None:
            staged: Path | None = None
            try:
                staged = stage_update(release, Path(sys.executable).parent)
                launch_installer(staged, Path(sys.executable).parent, self.data_dir)
                self.root.after(0, self._shutdown)
            except UpdateError as exc:
                LOG.exception("Application update installation failed")
                if staged:
                    remove_staged_update(staged)
                self.root.after(0, lambda error=exc: self._update_install_failed(error))

        threading.Thread(target=install, daemon=True, name="application-update").start()

    def _update_install_failed(self, error: Exception) -> None:
        self.update_button.configure(state="normal", text="CHECK FOR UPDATES")
        messagebox.showerror("Update could not be installed", str(error), parent=self.root)

    def _refresh(self) -> None:
        watch = self.watcher.status()
        library = self.repository.summary()
        scan = self.scanner.status()
        self.workspace.refresh_status(library, watch, scan)
        self.root.after(10000, self._refresh)

    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            LOG.info("Optional system-tray dependencies are not installed")
            return
        image = Image.new("RGB", (64, 64), "#09111a")
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 56, 56), outline="#5be5ee", width=3)
        draw.text((14, 23), "MA", fill="#5be5ee")

        def action(callback: Callable[[], None]):
            return lambda icon, item: self.root.after(0, callback)

        menu = pystray.Menu(
            pystray.MenuItem("Open App", action(self._open_dashboard)),
            pystray.MenuItem("Open Game Overlay", action(self._open_game_overlay)),
            pystray.MenuItem("Manage Reminders", action(self._open_reminder_editor)),
            pystray.MenuItem("Scan Replay Library", action(self._scan)),
            pystray.MenuItem("Retry Failed Replays", action(self._retry_failed)),
            pystray.MenuItem("Watcher: On", action(self._resume)),
            pystray.MenuItem("Watcher: Off", action(self._pause)),
            pystray.MenuItem("Open Logs", action(self._open_logs)),
            pystray.MenuItem("Exit", action(self._shutdown)),
        )
        self._tray_icon = pystray.Icon("MarineArenaCompanion", image, "Marine Arena Companion", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True, name="system-tray").start()
        self.tray_available = True

    def _on_close(self) -> None:
        if self.tray_available:
            self.root.withdraw()
        else:
            self._shutdown()

    def _shutdown(self) -> None:
        if self._tray_icon:
            self._tray_icon.stop()
        self._close_game_overlay()
        self.watcher.stop()
        self.scanner.close()
        self.server.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

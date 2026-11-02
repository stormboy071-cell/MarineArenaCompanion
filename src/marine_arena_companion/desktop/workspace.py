from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Any, Callable

from marine_arena_companion.analysis.identity import split_player_name
from marine_arena_companion.config import validate_battle_tag
from marine_arena_companion.timing import format_elapsed_time, parse_elapsed_time


BG = "#050d18"
SIDEBAR = "#07111e"
PANEL = "#09182a"
PANEL_SOFT = "#071321"
LINE = "#123553"
LINE_STRONG = "#1a5277"
CYAN = "#5bd6ff"
GREEN = "#62dfb0"
AMBER = "#ffd166"
RED = "#ff8290"
TEXT = "#e9f6ff"
MUTED = "#93b1c9"

PAGE_TITLES = {
    "overview": ("Overview", "YOUR REPLAY DESK"),
    "replays": ("Replay library", "YOUR LIBRARY"),
    "players": ("Players", "PLAYER INSIGHTS"),
    "reminders": ("Overlay reminders", "CUSTOM GAME CUES"),
    "health": ("Library health", "APP HEALTH"),
    "settings": ("Settings", "APP SETTINGS"),
}
STATUS_LABELS = {
    "ANALYZED": "Ready to explore",
    "PROCESSING": "In progress",
    "DECODE_FAILED": "Could not analyze",
    "UNSUPPORTED_PROTOCOL": "Format not supported",
    "NORMALIZATION_FAILED": "Needs review",
}
MAP_LABELS = {
    "MARINE_ARENA_EU": "Marine Arena EU",
    "MARINE_ARENA_OTHER": "Other Marine Arena",
    "OTHER_MAP": "Other map",
    "UNKNOWN": "Unknown map",
}
REMINDER_CATEGORIES = {
    "unit_combo": "Unit combination",
    "hero": "Hero",
    "elite": "Elite",
    "custom": "Custom",
}
REMINDER_CATEGORY_VALUES = {label: value for value, label in REMINDER_CATEGORIES.items()}
STATUS_FILTERS = {
    "All statuses": "",
    "Ready to explore": "ANALYZED",
    "In progress": "PROCESSING",
    "Needs attention": "NEEDS_ATTENTION",
    "Format not supported": "UNSUPPORTED_PROTOCOL",
}
MAP_FILTERS = {
    "All maps": "",
    "Marine Arena": "MARINE_ARENA",
    "Marine Arena EU": "MARINE_ARENA_EU",
    "Other Marine Arena": "MARINE_ARENA_OTHER",
    "Other maps": "OTHER_MAP",
    "Unknown map": "UNKNOWN",
}
PLAYER_SORT_OPTIONS = {
    "Recently played": "recent",
    "Most games": "games",
    "Name": "name",
}


def _display_name(value: object) -> str:
    return split_player_name(value)[1]


def _map_name(name: object, classification: object) -> str:
    return str(name or MAP_LABELS.get(str(classification or ""), "Unknown map"))


def _duration(value: object) -> str:
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "—"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _recorded(value: object, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number.is_integer():
        return f"{int(number):,}{suffix}"
    return f"{number:,.1f}{suffix}"


class NativeWorkspace:
    """Native Tk workspace for the app's primary screens."""

    def __init__(self, app: Any):
        self.app = app
        self.root = app.root
        self.repository = app.repository
        self.config = app.config
        self.current_page = "overview"
        self.player_section = "mine"
        self.replay_offset = 0
        self.player_offset = 0
        self.replay_selected_id: int | None = None
        self.opponent_selected_id: int | None = None
        self.reminder_selected_id: int | None = None
        self.replay_search_var = tk.StringVar()
        self.replay_status_var = tk.StringVar(value="All statuses")
        self.replay_map_var = tk.StringVar(value="All maps")
        self.player_search_var = tk.StringVar()
        self.player_sort_var = tk.StringVar(value="Recently played")
        self.reminder_time_var = tk.StringVar(value="00:30:00")
        self.reminder_category_var = tk.StringVar(value=REMINDER_CATEGORIES["unit_combo"])
        self.reminder_enabled_var = tk.BooleanVar(value=True)
        self.auto_start_var = tk.BooleanVar(value=self.config.get().auto_start)
        self.watcher_state_var = tk.StringVar(value="Watcher status")
        self.scan_state_var = tk.StringVar(value="No library scan running.")
        self.folder_state_var = tk.StringVar(value="No replay folder selected")
        self.profile_state_var = tk.StringVar()
        self._overview_signature: tuple[tuple[object, ...], ...] | None = None
        self._hovered_nav_key: str | None = None
        self._build_styles()
        self._build_shell()
        self.show_page("overview")

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 9))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Soft.TFrame", background=PANEL_SOFT)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Eyebrow.TLabel", background=BG, foreground=CYAN, font=("Segoe UI", 8, "bold"))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 21))
        style.configure("CardValue.TLabel", background=PANEL, foreground=CYAN, font=("Segoe UI Semibold", 23))
        style.configure(
            "TButton", background="#10243a", foreground=TEXT,
            padding=(11, 8), font=("Segoe UI Semibold", 9), borderwidth=0,
        )
        style.map(
            "TButton",
            background=[("disabled", "#0a1422"), ("pressed", "#0e4167"), ("active", "#174c73")],
            foreground=[("disabled", "#627b91"), ("active", "#ffffff")],
        )
        style.configure(
            "Primary.TButton", background="#075482", foreground="#f2fbff",
            padding=(11, 8), font=("Segoe UI Semibold", 9), borderwidth=0,
        )
        style.map(
            "Primary.TButton",
            background=[("pressed", "#0873ad"), ("active", "#0b6898")],
            foreground=[("active", "#ffffff")],
        )
        style.configure(
            "Nav.TButton", background=SIDEBAR, foreground="#b7cde0",
            padding=(12, 10), anchor="w", font=("Segoe UI", 9), borderwidth=0,
        )
        style.map(
            "Nav.TButton",
            background=[("pressed", "#0e4167"), ("active", "#174c73")],
            foreground=[("active", "#ffffff")],
        )
        style.configure(
            "Treeview", background=PANEL_SOFT, fieldbackground=PANEL_SOFT,
            foreground=TEXT, rowheight=29, borderwidth=0, font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading", background="#0d2942", foreground="#9dc9e5",
            font=("Segoe UI Semibold", 8), padding=(9, 8), borderwidth=0,
        )
        style.map(
            "Treeview.Heading",
            background=[("pressed", "#15517b"), ("active", "#123e60")],
            foreground=[("active", "#ffffff")],
        )
        style.map(
            "Treeview",
            background=[("selected", "#07517b")],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "TNotebook", background=BG, borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab", background="#0a1a2b", foreground=MUTED,
            padding=(12, 8), font=("Segoe UI", 9),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#0c3b5d"), ("active", "#123653")],
            foreground=[("selected", "#e7f8ff"), ("active", "#ffffff")],
        )
        style.configure(
            "TEntry", fieldbackground="#071321", foreground=TEXT,
            selectbackground="#12608b", selectforeground="#ffffff",
            padding=7, borderwidth=1,
        )
        style.map(
            "TEntry",
            fieldbackground=[("disabled", "#08111d"), ("focus", "#091a2b")],
            foreground=[("disabled", "#71869a")],
        )
        style.configure(
            "TCombobox", fieldbackground="#071321", foreground=TEXT,
            selectbackground="#071321", selectforeground=TEXT,
            arrowcolor=CYAN, padding=6, borderwidth=1,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#071321"), ("disabled", "#08111d"), ("focus", "#091a2b"), ("active", "#0b2035")],
            foreground=[("readonly", TEXT), ("disabled", "#71869a"), ("active", "#ffffff")],
            selectbackground=[("readonly", "#071321")],
            selectforeground=[("readonly", TEXT)],
            arrowcolor=[("disabled", "#627b91"), ("active", "#ffffff")],
        )
        style.configure("TCheckbutton", background=BG, foreground="#c7dcec", indicatorcolor="#10243a", padding=3)
        style.map(
            "TCheckbutton",
            background=[("active", BG)],
            foreground=[("disabled", "#71869a"), ("active", "#ffffff")],
            indicatorcolor=[("selected", CYAN), ("pressed", "#1882b8"), ("active", "#1a5277")],
        )
        style.configure("TScrollbar", background="#10243a", troughcolor=BG, bordercolor=BG, arrowcolor=MUTED)
        style.map("TScrollbar", background=[("active", "#1a5277")])
        self.root.option_add("*TCombobox*Listbox.background", "#071321")
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#07517b")
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))

    def _set_nav_hover(self, key: str, hovering: bool) -> None:
        button = self.nav_buttons.get(key)
        if button is None:
            return
        if hovering:
            self._hovered_nav_key = key
        elif self._hovered_nav_key == key:
            self._hovered_nav_key = None
        if hovering:
            background, foreground = "#15517b", "#ffffff"
        elif self.current_page == key:
            background, foreground = "#0c3b5d", "#e7f8ff"
        else:
            background, foreground = SIDEBAR, "#b7cde0"
        button.configure(bg=background, fg=foreground)

    def _build_shell(self) -> None:
        self.root.geometry("1180x790")
        self.root.minsize(940, 650)
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg=SIDEBAR, width=220, highlightbackground=LINE, highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=SIDEBAR, padx=16, pady=23)
        brand.pack(fill="x")
        tk.Label(
            brand, text="MA", bg="#16323a", fg="#72e0e6",
            font=("Segoe UI Semibold", 12), width=4, height=2,
        ).pack(side="left", padx=(0, 10))
        brand_text = tk.Frame(brand, bg=SIDEBAR)
        brand_text.pack(side="left")
        tk.Label(
            brand_text, text="MARINE ARENA", bg=SIDEBAR, fg=TEXT,
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w")
        tk.Label(
            brand_text, text="REPLAY COMPANION", bg=SIDEBAR, fg=MUTED,
            font=("Segoe UI", 7),
        ).pack(anchor="w", pady=(3, 0))
        tk.Label(
            sidebar, text="YOUR LIBRARY", bg=SIDEBAR, fg="#74899b",
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", padx=18, pady=(17, 7))
        self.nav_buttons: dict[str, tk.Button] = {}
        nav_items = (
            ("overview", "⌂", "Overview"),
            ("replays", "▤", "Replay library"),
            ("players", "♙", "Players"),
            ("reminders", "◷", "Overlay reminders"),
        )
        for key, icon, label in nav_items:
            button = tk.Button(
                sidebar, text=f"{icon}   {label}", command=lambda page=key: self.show_page(page),
                bg=SIDEBAR, fg="#b7cde0", activebackground="#174c73",
                activeforeground="#ffffff", relief="flat", borderwidth=0, anchor="w",
                padx=16, pady=10, font=("Segoe UI", 9), cursor="hand2",
            )
            button.pack(fill="x", padx=8, pady=2)
            button.bind("<Enter>", lambda _event, page=key: self._set_nav_hover(page, True))
            button.bind("<Leave>", lambda _event, page=key: self._set_nav_hover(page, False))
            self.nav_buttons[key] = button
        tk.Label(
            sidebar, text="APP", bg=SIDEBAR, fg="#74899b",
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", padx=18, pady=(22, 7))
        for key, icon, label in (("health", "⌁", "Library health"), ("settings", "⚙", "Settings")):
            button = tk.Button(
                sidebar, text=f"{icon}   {label}", command=lambda page=key: self.show_page(page),
                bg=SIDEBAR, fg="#b7cde0", activebackground="#174c73",
                activeforeground="#ffffff", relief="flat", borderwidth=0, anchor="w",
                padx=16, pady=10, font=("Segoe UI", 9), cursor="hand2",
            )
            button.pack(fill="x", padx=8, pady=2)
            button.bind("<Enter>", lambda _event, page=key: self._set_nav_hover(page, True))
            button.bind("<Leave>", lambda _event, page=key: self._set_nav_hover(page, False))
            self.nav_buttons[key] = button
        self.sidebar_bottom = tk.Frame(sidebar, bg="#0e191f", highlightbackground="#20333c", highlightthickness=1)
        self.sidebar_bottom.pack(side="bottom", fill="x", padx=10, pady=12)
        tk.Label(
            self.sidebar_bottom, text="●  Private by design", bg="#0e191f", fg=GREEN,
            font=("Segoe UI Semibold", 8), anchor="w",
        ).pack(fill="x", padx=10, pady=(10, 3))
        tk.Label(
            self.sidebar_bottom, text="Your replay library stays on this device.",
            bg="#0e191f", fg=MUTED, font=("Segoe UI", 8), wraplength=175,
            justify="left", anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 10))

        workspace = tk.Frame(shell, bg=BG)
        workspace.pack(side="left", fill="both", expand=True)
        topbar = tk.Frame(workspace, bg="#0b121b", height=57, highlightbackground=LINE, highlightthickness=1)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        self.heading_var = tk.StringVar(value="Overview")
        tk.Label(
            topbar, text="MARINE ARENA COMPANION", bg="#0b121b", fg="#8193a3",
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(24, 12))
        tk.Frame(topbar, bg=LINE_STRONG, width=1, height=19).pack(side="left", padx=4)
        tk.Label(
            topbar, textvariable=self.heading_var, bg="#0b121b", fg=TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left", padx=10)
        self.watcher_badge = tk.Label(
            topbar, textvariable=self.watcher_state_var, bg="#102124", fg=GREEN,
            font=("Segoe UI Semibold", 8), padx=10, pady=4,
        )
        self.watcher_badge.pack(side="right", padx=(8, 20))
        self.pages_host = tk.Frame(workspace, bg=BG)
        self.pages_host.pack(fill="both", expand=True)
        self.pages: dict[str, tk.Frame] = {}
        for key in PAGE_TITLES:
            page = tk.Frame(self.pages_host, bg=BG)
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()
            self.pages[key] = page
        self.pages_host.rowconfigure(0, weight=1)
        self.pages_host.columnconfigure(0, weight=1)
        self._build_overview()
        self._build_replays()
        self._build_players()
        self._build_reminders()
        self._build_health()
        self._build_settings()
        self._build_footer(workspace)

    def _build_footer(self, parent: tk.Widget) -> None:
        footer = tk.Frame(parent, bg=BG)
        footer.pack(fill="x", padx=24, pady=(0, 10))
        tk.Label(footer, text="Marine Arena Companion", bg=BG, fg="#728392", font=("Segoe UI", 8)).pack(side="left")
        self.version_label = tk.Label(footer, text="", bg=BG, fg="#728392", font=("Segoe UI", 8))
        self.version_label.pack(side="right")

    def _page_heading(self, page: tk.Frame, title: str, subtitle: str) -> tk.Frame:
        heading = tk.Frame(page, bg=BG)
        heading.pack(fill="x", padx=24, pady=(22, 15))
        tk.Label(heading, text=subtitle, bg=BG, fg=CYAN, font=("Segoe UI Semibold", 8)).pack(anchor="w")
        tk.Label(heading, text=title, bg=BG, fg=TEXT, font=("Segoe UI Semibold", 20)).pack(anchor="w", pady=(4, 0))
        return heading

    @staticmethod
    def _panel(parent: tk.Widget, *, padx: int = 15, pady: int = 13) -> tk.Frame:
        return tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1, padx=padx, pady=pady)

    @staticmethod
    def _tree(parent: tk.Widget, columns: tuple[tuple[str, str, int], ...], height: int = 8) -> ttk.Treeview:
        keys = tuple(item[0] for item in columns)
        tree = ttk.Treeview(parent, columns=keys, show="headings", height=height, selectmode="browse")
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=60, anchor="w", stretch=True)
        return tree

    def _build_overview(self) -> None:
        page = self.pages["overview"]
        heading = self._page_heading(page, "A clearer view of your games.", "YOUR REPLAY DESK")
        tk.Label(
            heading, text="Explore replay history, player profiles, and your match reminders.",
            bg=BG, fg=MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 0))
        actions = tk.Frame(heading, bg=BG)
        actions.pack(side="right", anchor="e", pady=(5, 0))
        ttk.Button(actions, text="Scan library", style="Primary.TButton", command=self.app._scan).pack(side="left", padx=4)
        ttk.Button(actions, text="Choose replay folder", command=self.app._select_folder).pack(side="left", padx=4)
        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 15))
        self.metric_vars = {}
        cards = tk.Frame(body, bg=BG)
        cards.pack(fill="x", pady=(0, 12))
        for index, (key, label, accent) in enumerate((
            ("replays", "REPLAYS IN LIBRARY", CYAN),
            ("marine", "MARINE ARENA MAPS", GREEN),
            ("players", "PLAYERS FOUND", "#70aefa"),
            ("failed", "NEEDING ATTENTION", AMBER),
        )):
            card = self._panel(cards, padx=14, pady=11)
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
            cards.columnconfigure(index, weight=1)
            tk.Label(card, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 7)).pack(anchor="w")
            value = tk.StringVar(value="—")
            self.metric_vars[key] = value
            tk.Label(card, textvariable=value, bg=PANEL, fg=accent, font=("Segoe UI Semibold", 22)).pack(anchor="w", pady=(3, 0))
        status = self._panel(body, padx=16, pady=12)
        status.pack(fill="x", pady=(0, 12))
        tk.Label(status, text="REPLAY LIBRARY", bg=PANEL, fg=CYAN, font=("Segoe UI Semibold", 8)).pack(anchor="w")
        self.status_var = tk.StringVar(value="Starting local services…")
        tk.Label(status, textvariable=self.status_var, bg=PANEL, fg=TEXT, font=("Segoe UI", 10), anchor="w").pack(fill="x", pady=(7, 3))
        self.folder_var = tk.StringVar(value="Replay folder not configured")
        tk.Label(status, textvariable=self.folder_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x")
        self.progress_var = tk.StringVar(value="No scan running")
        tk.Label(status, textvariable=self.progress_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(6, 0))
        section = tk.Frame(body, bg=BG)
        section.pack(fill="both", expand=True)
        left = self._panel(section)
        left.pack(side="left", fill="both", expand=True, padx=(0, 7))
        right = self._panel(section)
        right.pack(side="left", fill="both", expand=True, padx=(7, 0))
        tk.Label(left, text="Recent replays", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 9))
        self.recent_tree = self._tree(left, (("filename", "REPLAY", 210), ("map", "MAP", 155), ("status", "STATUS", 135)), height=7)
        self.recent_tree.pack(fill="both", expand=True)
        self.recent_tree.bind("<Double-1>", lambda _event: self.open_selected_recent())
        tk.Label(right, text="Quick actions", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 9))
        for label, action in (
            ("Browse replay library", lambda: self.show_page("replays")),
            ("View player profiles", lambda: self.show_page("players")),
            ("Manage overlay reminders", lambda: self.show_page("reminders")),
            ("Open game overlay", self.app._open_game_overlay),
        ):
            ttk.Button(right, text=label, command=action).pack(fill="x", pady=4)

    def _build_replays(self) -> None:
        page = self.pages["replays"]
        self._page_heading(page, "Replay library", "YOUR GAMES")
        outer = tk.Frame(page, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=(0, 15))
        panel = self._panel(outer, padx=12, pady=11)
        panel.pack(fill="both", expand=True)
        filters = tk.Frame(panel, bg=PANEL)
        filters.pack(fill="x", pady=(0, 10))
        search = ttk.Entry(filters, textvariable=self.replay_search_var)
        search.pack(side="left", fill="x", expand=True, padx=(0, 7))
        search.insert(0, "")
        search.bind("<KeyRelease>", lambda _event: self._reload_replays(reset=True))
        status = ttk.Combobox(
            filters, textvariable=self.replay_status_var, state="readonly", width=20,
            values=tuple(STATUS_FILTERS),
        )
        status.pack(side="left", padx=4)
        status.bind("<<ComboboxSelected>>", lambda _event: self._reload_replays(reset=True))
        map_filter = ttk.Combobox(
            filters, textvariable=self.replay_map_var, state="readonly", width=20,
            values=tuple(MAP_FILTERS),
        )
        map_filter.pack(side="left", padx=4)
        map_filter.bind("<<ComboboxSelected>>", lambda _event: self._reload_replays(reset=True))
        self.replay_count_label = tk.StringVar(value="")
        tk.Label(panel, textvariable=self.replay_count_label, bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 6))
        self.replay_tree = self._tree(panel, (
            ("filename", "REPLAY", 245), ("map", "MAP", 180), ("status", "STATUS", 150),
            ("duration", "DURATION", 85), ("added", "ADDED", 145),
        ), height=8)
        self.replay_tree.pack(fill="both", expand=True)
        self.replay_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_replay_selection())
        pager = tk.Frame(panel, bg=PANEL)
        pager.pack(fill="x", pady=(7, 10))
        ttk.Button(pager, text="Previous", command=lambda: self._page_replays(-1)).pack(side="right", padx=3)
        ttk.Button(pager, text="Next", command=lambda: self._page_replays(1)).pack(side="right", padx=3)
        detail = self._panel(outer, padx=12, pady=9)
        detail.pack(fill="both", expand=True, pady=(10, 0))
        self.replay_detail_title = tk.StringVar(value="Select a replay to view its recorded details.")
        tk.Label(detail, textvariable=self.replay_detail_title, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 6))
        self.replay_detail_tabs = ttk.Notebook(detail)
        self.replay_detail_tabs.pack(fill="both", expand=True)
        self.replay_detail_views: dict[str, tk.Widget] = {}
        for key, label in (
            ("overview", "Match basics"), ("performance", "Player activity"),
            ("score", "Score samples"), ("units", "Units & upgrades"),
            ("build", "Build order"), ("graphs", "Graphs"),
        ):
            content = tk.Frame(self.replay_detail_tabs, bg=PANEL, padx=12, pady=10)
            self.replay_detail_tabs.add(content, text=label)
            self.replay_detail_views[key] = content

    def _build_players(self) -> None:
        page = self.pages["players"]
        self._page_heading(page, "Players in your replays", "PLAYER INSIGHTS")
        sections = tk.Frame(page, bg=BG)
        sections.pack(fill="x", padx=24, pady=(0, 11))
        self.player_section_buttons: dict[str, tk.Button] = {}
        for key, label in (("mine", "My profile"), ("opponents", "Opponents")):
            button = tk.Button(
                sections, text=label, command=lambda value=key: self.show_player_section(value),
                bg=PANEL_SOFT, fg=TEXT, activebackground="#18313a", activeforeground=TEXT,
                relief="flat", borderwidth=0, padx=14, pady=8,
                font=("Segoe UI Semibold", 9), cursor="hand2",
            )
            button.pack(side="left", padx=(0, 6))
            self.player_section_buttons[key] = button
        self.player_content = tk.Frame(page, bg=BG)
        self.player_content.pack(fill="both", expand=True, padx=24, pady=(0, 15))
        self.player_content.rowconfigure(0, weight=1)
        self.player_content.columnconfigure(0, weight=1)
        self.my_profile_page = tk.Frame(self.player_content, bg=BG)
        self.opponents_page = tk.Frame(self.player_content, bg=BG)
        self.my_profile_page.grid(row=0, column=0, sticky="nsew")
        self.opponents_page.grid(row=0, column=0, sticky="nsew")
        self._build_my_profile()
        self._build_opponents()
        self.my_profile_page.tkraise()
        self.player_section_buttons["mine"].configure(bg="#18313a")

    def _build_my_profile(self) -> None:
        panel = self._panel(self.my_profile_page, padx=15, pady=13)
        panel.pack(fill="both", expand=True)
        header = tk.Frame(panel, bg=PANEL)
        header.pack(fill="x", pady=(0, 8))
        self.my_profile_name = tk.StringVar(value="My profile")
        tk.Label(header, textvariable=self.my_profile_name, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 14)).pack(side="left")
        ttk.Button(header, text="Edit Battle.net tag", command=self._edit_battle_tag).pack(side="right")
        self.my_profile_tag = tk.StringVar(value="")
        tk.Label(panel, textvariable=self.my_profile_tag, bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 9))
        self.my_profile_tabs = ttk.Notebook(panel)
        self.my_profile_tabs.pack(fill="both", expand=True)
        self.my_profile_summary = tk.Frame(self.my_profile_tabs, bg=PANEL, padx=8, pady=8)
        self.my_profile_units = tk.Frame(self.my_profile_tabs, bg=PANEL, padx=8, pady=8)
        self.my_profile_maps = tk.Frame(self.my_profile_tabs, bg=PANEL, padx=8, pady=8)
        self.my_profile_matches = tk.Frame(self.my_profile_tabs, bg=PANEL, padx=8, pady=8)
        self.my_profile_tabs.add(self.my_profile_summary, text="Performance")
        self.my_profile_tabs.add(self.my_profile_units, text="Units & upgrades")
        self.my_profile_tabs.add(self.my_profile_maps, text="Maps & results")
        self.my_profile_tabs.add(self.my_profile_matches, text="Match history")
        self.my_match_tree = self._tree(
            self.my_profile_matches,
            (("filename", "REPLAY", 280), ("map", "MAP", 200), ("result", "RESULT", 120), ("added", "ADDED", 150)),
            height=9,
        )
        self.my_match_tree.pack(fill="both", expand=True)
        self.my_match_tree.bind("<Double-1>", lambda _event: self._open_player_match(self.my_match_tree))

    def _build_opponents(self) -> None:
        layout = tk.Frame(self.opponents_page, bg=BG)
        layout.pack(fill="both", expand=True)
        left = self._panel(layout, padx=12, pady=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        right = self._panel(layout, padx=12, pady=10)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        controls = tk.Frame(left, bg=PANEL)
        controls.pack(fill="x", pady=(0, 7))
        search = ttk.Entry(controls, textvariable=self.player_search_var)
        search.pack(side="left", fill="x", expand=True, padx=(0, 6))
        search.bind("<KeyRelease>", lambda _event: self._reload_opponents())
        sort = ttk.Combobox(
            controls, textvariable=self.player_sort_var, state="readonly", width=15,
            values=tuple(PLAYER_SORT_OPTIONS),
        )
        sort.pack(side="left")
        sort.bind("<<ComboboxSelected>>", lambda _event: self._reload_opponents())
        self.opponent_tree = self._tree(left, (("name", "PLAYER", 190), ("games", "GAMES", 70), ("seen", "LAST SEEN", 135)), height=10)
        self.opponent_tree.pack(fill="both", expand=True)
        self.opponent_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_opponent())
        self.opponent_title = tk.StringVar(value="Select a player")
        tk.Label(right, textvariable=self.opponent_title, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 13)).pack(anchor="w", pady=(2, 9))
        self.opponent_detail_tabs = ttk.Notebook(right)
        self.opponent_detail_tabs.pack(fill="both", expand=True)
        self.opponent_summary = tk.Frame(self.opponent_detail_tabs, bg=PANEL, padx=8, pady=8)
        self.opponent_units = tk.Frame(self.opponent_detail_tabs, bg=PANEL, padx=8, pady=8)
        self.opponent_maps = tk.Frame(self.opponent_detail_tabs, bg=PANEL, padx=8, pady=8)
        self.opponent_matches = tk.Frame(self.opponent_detail_tabs, bg=PANEL, padx=8, pady=8)
        self.opponent_detail_tabs.add(self.opponent_summary, text="Performance")
        self.opponent_detail_tabs.add(self.opponent_units, text="Units & upgrades")
        self.opponent_detail_tabs.add(self.opponent_maps, text="Maps & results")
        self.opponent_detail_tabs.add(self.opponent_matches, text="Match history")
        self.opponent_match_tree = self._tree(
            self.opponent_matches,
            (("filename", "REPLAY", 240), ("map", "MAP", 170), ("result", "RESULT", 100), ("added", "ADDED", 130)),
            height=8,
        )
        self.opponent_match_tree.pack(fill="both", expand=True)
        self.opponent_match_tree.bind("<Double-1>", lambda _event: self._open_player_match(self.opponent_match_tree))

    def _build_reminders(self) -> None:
        page = self.pages["reminders"]
        heading = self._page_heading(page, "Your overlay reminders", "MATCH PREPARATION")
        ttk.Button(heading, text="Open game overlay", style="Primary.TButton", command=self.app._open_game_overlay).pack(side="right")
        tk.Label(
            page, text="Cue times count from match start, not from the time of day. Example: 01:24:03.",
            bg=BG, fg=MUTED, font=("Segoe UI", 8), anchor="w",
        ).pack(fill="x", padx=24, pady=(0, 9))
        layout = tk.Frame(page, bg=BG)
        layout.pack(fill="both", expand=True, padx=24, pady=(0, 15))
        editor = self._panel(layout, padx=16, pady=13)
        editor.pack(side="left", fill="y", padx=(0, 7))
        editor.configure(width=320)
        editor.pack_propagate(False)
        self.reminder_form_title = tk.StringVar(value="Create a reminder")
        tk.Label(editor, textvariable=self.reminder_form_title, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w", pady=(0, 13))
        self.reminder_title_var = tk.StringVar()
        self.reminder_item_var = tk.StringVar()
        self.reminder_note_var = tk.StringVar()
        self._form_label(editor, "Reminder text")
        ttk.Entry(editor, textvariable=self.reminder_title_var).pack(fill="x", pady=(4, 10))
        row = tk.Frame(editor, bg=PANEL)
        row.pack(fill="x")
        time_box = tk.Frame(row, bg=PANEL)
        time_box.pack(side="left", fill="x", expand=True, padx=(0, 5))
        category_box = tk.Frame(row, bg=PANEL)
        category_box.pack(side="left", fill="x", expand=True, padx=(5, 0))
        self._form_label(time_box, "Match elapsed")
        ttk.Entry(time_box, textvariable=self.reminder_time_var).pack(fill="x", pady=(4, 10))
        tk.Label(
            time_box, text="HH:MM:SS · elapsed duration",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 7), anchor="w", wraplength=140,
        ).pack(fill="x", pady=(0, 7))
        self._form_label(category_box, "Cue type")
        category = ttk.Combobox(
            category_box, textvariable=self.reminder_category_var, state="readonly",
            values=tuple(REMINDER_CATEGORY_VALUES),
        )
        category.pack(fill="x", pady=(4, 10))
        self._form_label(editor, "Unit / character")
        ttk.Entry(editor, textvariable=self.reminder_item_var).pack(fill="x", pady=(4, 10))
        self._form_label(editor, "Extra details")
        ttk.Entry(editor, textvariable=self.reminder_note_var).pack(fill="x", pady=(4, 10))
        ttk.Checkbutton(editor, text="Include in game overlay", variable=self.reminder_enabled_var).pack(anchor="w", pady=(2, 10))
        self.reminder_form_message = tk.StringVar()
        tk.Label(editor, textvariable=self.reminder_form_message, bg=PANEL, fg=RED, wraplength=280, justify="left").pack(fill="x", pady=(0, 6))
        buttons = tk.Frame(editor, bg=PANEL)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Save reminder", style="Primary.TButton", command=self._save_reminder).pack(side="left")
        ttk.Button(buttons, text="New", command=self._clear_reminder_form).pack(side="left", padx=5)
        list_panel = self._panel(layout, padx=13, pady=11)
        list_panel.pack(side="left", fill="both", expand=True, padx=(7, 0))
        tk.Label(list_panel, text="Timed reminders", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w", pady=(1, 8))
        self.reminder_tree = self._tree(
            list_panel,
            (("time", "MATCH TIME", 100), ("type", "TYPE", 145), ("title", "REMINDER", 255), ("item", "UNIT / CHARACTER", 180), ("enabled", "OVERLAY", 80)),
            height=14,
        )
        self.reminder_tree.pack(fill="both", expand=True)
        self.reminder_tree.bind("<<TreeviewSelect>>", lambda _event: self._select_reminder())
        actions = tk.Frame(list_panel, bg=PANEL)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Delete selected", command=self._delete_reminder).pack(side="right")
        ttk.Button(actions, text="Toggle overlay", command=self._toggle_reminder).pack(side="right", padx=5)

    def _form_label(self, parent: tk.Widget, value: str) -> None:
        tk.Label(parent, text=value, bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 8)).pack(anchor="w")

    def _build_health(self) -> None:
        page = self.pages["health"]
        heading = self._page_heading(page, "Library health", "APP HEALTH")
        ttk.Button(heading, text="Refresh", command=self.refresh_health).pack(side="right")
        outer = tk.Frame(page, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=(0, 15))
        self.health_summary_var = tk.StringVar(value="Loading library health…")
        panel = self._panel(outer)
        panel.pack(fill="x", pady=(0, 12))
        tk.Label(panel, textvariable=self.health_summary_var, bg=PANEL, fg=TEXT, font=("Segoe UI", 10), justify="left", anchor="w").pack(fill="x")
        self.health_tree = self._tree(panel, (("issue", "LIBRARY STATUS", 300), ("detail", "DETAIL", 560)), height=8)
        self.health_tree.pack(fill="both", expand=True, pady=(10, 0))
        tk.Label(
            outer, text="Technical replay records are available through Export diagnostics.",
            bg=BG, fg=MUTED, font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(outer, text="Retry failed replays", command=self.app._retry_failed).pack(anchor="w")

    def _build_settings(self) -> None:
        page = self.pages["settings"]
        self._page_heading(page, "Application settings", "PREFERENCES")
        outer = tk.Frame(page, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=(0, 15))
        profile = self._panel(outer)
        profile.pack(fill="x", pady=(0, 10))
        tk.Label(profile, text="Player identity", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(profile, text="Used to identify your replay-backed profile.", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 8))
        row = tk.Frame(profile, bg=PANEL)
        row.pack(fill="x")
        self.battle_tag_var = tk.StringVar(value=self.config.get().battle_tag)
        ttk.Entry(row, textvariable=self.battle_tag_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row, text="Save tag", command=self._save_battle_tag).pack(side="left")
        self.settings_message = tk.StringVar(value="")
        tk.Label(profile, textvariable=self.settings_message, bg=PANEL, fg=GREEN, font=("Segoe UI", 8)).pack(anchor="w", pady=(6, 0))
        library = self._panel(outer)
        library.pack(fill="x", pady=(0, 10))
        tk.Label(library, text="Replay library", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(library, textvariable=self.folder_state_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w", wraplength=700).pack(fill="x", pady=(4, 8))
        controls = tk.Frame(library, bg=PANEL)
        controls.pack(fill="x")
        ttk.Button(controls, text="Choose folder", command=self.app._select_folder).pack(side="left", padx=(0, 5))
        ttk.Button(controls, text="Scan library", style="Primary.TButton", command=self.app._scan).pack(side="left", padx=5)
        ttk.Button(controls, text="Pause watcher", command=self.app._pause).pack(side="left", padx=5)
        ttk.Button(controls, text="Resume watcher", command=self.app._resume).pack(side="left", padx=5)
        preferences = self._panel(outer)
        preferences.pack(fill="x", pady=(0, 10))
        tk.Label(preferences, text="Preferences and support", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 7))
        ttk.Checkbutton(
            preferences, text="Start with Windows", variable=self.auto_start_var,
            command=self.app._toggle_auto_start,
        ).pack(anchor="w", pady=(0, 8))
        controls = tk.Frame(preferences, bg=PANEL)
        controls.pack(fill="x")
        self.app.update_button = ttk.Button(controls, text="Check for updates", command=self.app._check_updates_manually)
        self.app.update_button.pack(side="left", padx=(0, 5))
        ttk.Button(controls, text="Export diagnostics", command=self.app._export).pack(side="left", padx=5)
        ttk.Button(controls, text="Open logs", command=self.app._open_logs).pack(side="left", padx=5)
        ttk.Button(controls, text="Open game overlay", command=self.app._open_game_overlay).pack(side="left", padx=5)
        ttk.Button(controls, text="Exit app", command=self.app._shutdown).pack(side="right")

    def show_page(self, key: str) -> None:
        if key not in self.pages:
            return
        self.current_page = key
        for page_key, page in self.pages.items():
            if page_key == key:
                page.grid()
            else:
                page.grid_remove()
        title, _eyebrow = PAGE_TITLES[key]
        self.heading_var.set(title)
        for page_key in self.nav_buttons:
            self._set_nav_hover(page_key, page_key == self._hovered_nav_key)
        if key == "overview":
            self.refresh_overview()
        elif key == "replays":
            self._reload_replays()
        elif key == "players":
            self.refresh_players()
        elif key == "reminders":
            self.refresh_reminders()
        elif key == "health":
            self.refresh_health()
        elif key == "settings":
            self.refresh_settings()

    def refresh_status(self, library: dict[str, Any], watch: dict[str, Any], scan: dict[str, Any]) -> None:
        self.metric_vars["replays"].set(f"{library['total']:,}")
        self.metric_vars["marine"].set(f"{library['marine_arena']:,}")
        self.metric_vars["players"].set(f"{library['players']:,}")
        self.metric_vars["failed"].set(f"{library['failed']:,}")
        watcher_state = "WATCHING" if watch["active"] else "WATCHER PAUSED"
        if watch["error"]:
            watcher_state = "WATCHER ISSUE"
        self.watcher_state_var.set(watcher_state)
        self.watcher_badge.configure(
            bg="#102124" if watch["active"] and not watch["error"] else "#281a20",
            fg=GREEN if watch["active"] and not watch["error"] else RED,
        )
        self.status_var.set(
            f"{library['total']:,} replays · {library['analyzed']:,} analyzed · "
            f"{library['marine_arena']:,} Marine Arena · {library['failed']:,} need attention"
        )
        folder = self.config.get().replay_folder
        self.folder_state_var.set(f"Replay folder: {folder or 'Not configured'}")
        if scan["active"]:
            percent = round(scan["processed"] / scan["discovered"] * 100) if scan["discovered"] else 0
            self.progress_var.set(
                f"Scanning {scan['processed']:,} of {scan['discovered']:,} · {percent}% · "
                f"{scan['skipped']:,} already recognized · {scan['failed']:,} need attention"
            )
        else:
            self.progress_var.set(
                f"Library up to date · {scan['skipped']:,} unchanged replays skipped in last scan"
            )
        self.version_label.configure(text=f"Version {self.app.version}")
        if self.current_page == "overview":
            self.refresh_overview()

    def refresh_overview(self) -> None:
        items = self.repository.list_replays(8)
        signature = tuple(
            (
                item["id"], item["filename"], item["map_name"],
                item["map_classification"], item["decode_status"],
            )
            for item in items
        )
        if signature == self._overview_signature:
            return
        self._overview_signature = signature
        self._fill_tree(self.recent_tree, items, lambda item: (
            item["filename"], _map_name(item["map_name"], item["map_classification"]),
            STATUS_LABELS.get(item["decode_status"], item["decode_status"]),
        ))

    @staticmethod
    def _fill_tree(tree: ttk.Treeview, rows: list[dict[str, Any]], values: Callable[[dict[str, Any]], tuple[Any, ...]]) -> None:
        tree.delete(*tree.get_children())
        for row in rows:
            tree.insert("", "end", iid=str(row.get("id", row.get("replay_id", row.get("player_id", row.get("filename"))))), values=values(row))

    def open_selected_recent(self) -> None:
        selected = self.recent_tree.selection()
        if not selected:
            return
        self.show_page("replays")
        self.replay_selected_id = int(selected[0])
        self._reload_replays()
        if self.replay_tree.exists(str(self.replay_selected_id)):
            self.replay_tree.selection_set(str(self.replay_selected_id))
            self.replay_tree.see(str(self.replay_selected_id))
            self._show_replay_selection()

    def _reload_replays(self, *, reset: bool = False) -> None:
        if reset:
            self.replay_offset = 0
        status = STATUS_FILTERS.get(self.replay_status_var.get(), "")
        classification = MAP_FILTERS.get(self.replay_map_var.get(), "")
        try:
            items = self.repository.list_replays(
                40, self.replay_offset, search=self.replay_search_var.get().strip(),
                status=status, classification=classification,
            )
            total = self.repository.count_replays(
                search=self.replay_search_var.get().strip(),
                status=status, classification=classification,
            )
        except ValueError as exc:
            self.replay_count_label.set(str(exc))
            return
        self.replay_tree.delete(*self.replay_tree.get_children())
        for item in items:
            self.replay_tree.insert(
                "", "end", iid=str(item["id"]),
                values=(
                    item["filename"],
                    _map_name(item["map_name"], item["map_classification"]),
                    STATUS_LABELS.get(item["decode_status"], item["decode_status"]),
                    _duration(item["duration_seconds"]),
                    self._date(item["imported_at"]),
                ),
            )
        page_number = self.replay_offset // 40 + 1
        pages = max(1, (total + 39) // 40)
        self.replay_count_label.set(f"{total:,} replays · Page {page_number} of {pages}")
        if self.replay_selected_id and self.replay_tree.exists(str(self.replay_selected_id)):
            self.replay_tree.selection_set(str(self.replay_selected_id))
            self._show_replay_selection()

    def _page_replays(self, direction: int) -> None:
        total = self.repository.count_replays(
            search=self.replay_search_var.get().strip(),
            status=STATUS_FILTERS.get(self.replay_status_var.get(), ""),
            classification=MAP_FILTERS.get(self.replay_map_var.get(), ""),
        )
        last_offset = ((total - 1) // 40) * 40 if total else 0
        self.replay_offset = max(0, min(self.replay_offset + direction * 40, last_offset))
        self._reload_replays()

    def _show_replay_selection(self) -> None:
        selected = self.replay_tree.selection()
        if not selected:
            return
        replay_id = int(selected[0])
        summary = self.repository.replay_summary(replay_id)
        if not summary:
            return
        self.replay_selected_id = replay_id
        self.replay_detail_title.set(
            f"{summary['filename']}   ·   {_map_name(summary['map_name'], summary['map_classification'])}   ·   {_duration(summary['duration_seconds'])}"
        )
        self._render_replay_overview(summary)
        reports = summary.get("player_reports", [])
        self._render_report_metrics(self.replay_detail_views["performance"], reports, "performance")
        self._render_report_metrics(self.replay_detail_views["score"], reports, "score_summary")
        self._render_report_metrics(self.replay_detail_views["units"], reports, "units")
        self._render_build_order(self.replay_detail_views["build"], reports)
        self._render_graphs(self.replay_detail_views["graphs"], reports)

    def _render_replay_overview(self, summary: dict[str, Any]) -> None:
        target = self.replay_detail_views["overview"]
        self._clear(target)
        players = summary.get("players", [])
        details = tk.Frame(target, bg=PANEL)
        details.pack(fill="x", pady=(0, 10))
        for label, value in (
            ("Map", _map_name(summary["map_name"], summary["map_classification"])),
            ("Duration", _duration(summary["duration_seconds"])),
            ("Replay status", STATUS_LABELS.get(summary["decode_status"], summary["decode_status"])),
            ("Added to library", self._date(summary["imported_at"])),
        ):
            self._key_value(details, label, value)
        tk.Label(
            target, text="Participants · recorded slot, team and outcome",
            bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 9),
        ).pack(anchor="w", pady=(3, 5))
        table = self._tree(
            target,
            (("player", "PLAYER", 220), ("slot", "SLOT", 70), ("team", "TEAM", 70), ("result", "RECORDED RESULT", 160)),
            height=6,
        )
        table.pack(fill="both", expand=True)
        for player in players:
            table.insert("", "end", values=(
                _display_name(player["display_name"]),
                _recorded(player.get("slot")),
                _recorded(player.get("team")),
                self._result(player.get("result")) or "Not recorded",
            ))
        if not players:
            table.insert("", "end", values=("No participant details recorded", "—", "—", "—"))

    def _render_report_metrics(self, target: tk.Widget, reports: list[dict[str, Any]], section: str) -> None:
        self._clear(target)
        if not reports:
            self._text(target, "Player-attributed details are not available in this replay.")
            return
        if section == "score_summary":
            tk.Label(
                target,
                text="Values are from the final recorded score sample. Time-series samples are in Graphs.",
                bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
            ).pack(anchor="w", pady=(0, 7))
        for report in reports:
            frame = tk.Frame(target, bg=PANEL)
            frame.pack(fill="x", anchor="n", pady=(0, 10))
            tk.Label(
                frame, text=_display_name(report.get("display_name", f"Player {report.get('player_id')}")),
                bg=PANEL, fg=CYAN, font=("Segoe UI Semibold", 10),
            ).pack(anchor="w", pady=(0, 4))
            data = report.get(section, {})
            if section == "performance":
                entries = (
                    ("Approximate recorded actions per minute", data.get("actions_per_minute")),
                    ("Recorded actions", data.get("recorded_actions")),
                    ("Average unspent resources", data.get("average_unspent_resources")),
                    ("Time supply capped (seconds)", data.get("time_supply_capped_seconds")),
                    ("Workers created", data.get("workers_created")),
                )
            elif section == "score_summary":
                entries = (
                    ("Resources on hand", data.get("resources_on_hand")),
                    ("Army investment", data.get("army_investment")),
                    ("Economy investment", data.get("economy_investment")),
                    ("Technology investment", data.get("technology_investment")),
                )
            else:
                entries = (
                    ("Units trained", data.get("trained")),
                    ("Units lost", data.get("units_lost")),
                    ("Units killed", data.get("units_killed")),
                    ("Structures built", data.get("structures_built")),
                    ("Structures razed", data.get("structures_razed")),
                )
                top_units = data.get("top_units", [])
                top_upgrades = data.get("top_upgrades", [])
            for label, value in entries:
                self._key_value(frame, label, _recorded(value))
            if section == "units":
                self._key_value(
                    frame, "Most trained",
                    ", ".join(f"{item['name']} ({item['count']})" for item in top_units) or "No unit events recorded",
                )
                self._key_value(
                    frame, "Upgrades recorded",
                    ", ".join(f"{item['name']} ({item['count']})" for item in top_upgrades) or "No upgrade events recorded",
                )

    def _render_build_order(self, target: tk.Widget, reports: list[dict[str, Any]]) -> None:
        self._clear(target)
        if not reports:
            self._text(target, "Build-order events are not available in this replay.")
            return
        notebook = ttk.Notebook(target)
        notebook.pack(fill="both", expand=True)
        for report in reports:
            frame = tk.Frame(notebook, bg=PANEL, padx=8, pady=8)
            notebook.add(frame, text=_display_name(report.get("display_name", "Player")))
            tree = self._tree(frame, (("time", "TIME", 90), ("category", "TYPE", 120), ("item", "ITEM", 280), ("supply", "SUPPLY", 90)), 9)
            tree.pack(fill="both", expand=True)
            for item in report.get("build_order", []):
                tree.insert("", "end", values=(
                    _duration(item.get("time_seconds")),
                    item.get("category", ""),
                    item.get("item", ""),
                    f"{item['supply']} supply" if item.get("supply") is not None else "—",
                ))

    def _render_graphs(self, target: tk.Widget, reports: list[dict[str, Any]]) -> None:
        self._clear(target)
        if not reports:
            self._text(target, "Graph samples are not available in this replay.")
            return
        metrics = (
            ("army_value", "Army investment"),
            ("resource_collection_rate", "Resource collection rate"),
            ("upgrade_spending", "Technology investment"),
            ("workers_active", "Workers active"),
        )
        chooser = ttk.Combobox(
            target, state="readonly", values=tuple(label for _key, label in metrics), width=28,
        )
        chooser.current(0)
        chooser.pack(anchor="w", pady=(0, 5))
        graph_reports = ttk.Notebook(target)
        graph_reports.pack(fill="both", expand=True, pady=(7, 0))
        canvases = {}
        for report in reports:
            sub = tk.Frame(graph_reports, bg=PANEL)
            graph_reports.add(sub, text=_display_name(report.get("display_name", "Player")))
            chart = tk.Canvas(sub, height=190, bg=PANEL_SOFT, highlightthickness=0)
            chart.pack(fill="both", expand=True)
            canvases[report["player_id"]] = (chart, report.get("graphs", {}))
        def draw(_event: object = None) -> None:
            graph_key = metrics[chooser.current()][0]
            for chart, graph_data in canvases.values():
                chart.delete("all")
                points = graph_data.get(graph_key, [])
                if len(points) < 2:
                    chart.create_text(20, 35, anchor="w", text="Not enough graph samples", fill=MUTED, font=("Segoe UI", 9))
                    continue
                width = max(chart.winfo_width(), 360)
                height = max(chart.winfo_height(), 160)
                values = [float(point["value"]) for point in points]
                low, high = min(values), max(values)
                span = high - low or 1.0
                coords: list[float] = []
                for index, point in enumerate(points):
                    x = 30 + index / (len(points) - 1) * (width - 60)
                    y = height - 24 - (float(point["value"]) - low) / span * (height - 50)
                    coords.extend((x, y))
                chart.create_line(*coords, fill=CYAN, width=2, smooth=True)
                chart.create_text(14, 14, anchor="nw", text=f"{_recorded(low)} - {_recorded(high)}", fill=MUTED, font=("Segoe UI", 8))

        chooser.bind("<<ComboboxSelected>>", draw)
        graph_reports.bind("<<NotebookTabChanged>>", draw)
        target.after(100, draw)

    @staticmethod
    def _clear(target: tk.Widget) -> None:
        for child in target.winfo_children():
            child.destroy()

    @staticmethod
    def _text(target: tk.Widget, value: str) -> tk.Text:
        widget = tk.Text(
            target, bg=PANEL_SOFT, fg=TEXT, insertbackground=CYAN,
            relief="flat", wrap="word", font=("Segoe UI", 9), padx=12, pady=10,
        )
        widget.insert("1.0", value)
        widget.configure(state="disabled")
        widget.pack(fill="both", expand=True)
        return widget

    @staticmethod
    def _key_value(parent: tk.Widget, label: str, value: str) -> None:
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(side="left")
        tk.Label(row, text=value, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 9), anchor="e", wraplength=520).pack(side="right")

    def show_player_section(self, section: str) -> None:
        self.player_section = section
        for key, button in self.player_section_buttons.items():
            button.configure(bg="#18313a" if key == section else PANEL_SOFT)
        (self.my_profile_page if section == "mine" else self.opponents_page).tkraise()
        self.refresh_players()

    def refresh_players(self) -> None:
        if self.player_section == "mine":
            self._refresh_my_profile()
        else:
            self._reload_opponents()

    def _refresh_my_profile(self) -> None:
        for target in (self.my_profile_summary, self.my_profile_units, self.my_profile_maps):
            self._clear(target)
        battle_tag = self.config.get().battle_tag
        name = battle_tag.rsplit("#", 1)[0] if battle_tag else "My profile"
        self.my_profile_name.set(name)
        self.my_profile_tag.set(battle_tag or "Set your Battle.net tag in Settings")
        player_ids = self.repository.player_ids_for_battle_tag(battle_tag) if battle_tag else []
        profile = self.repository.my_profile(player_ids)
        if not profile["all_time_games"]:
            for target in (self.my_profile_summary, self.my_profile_units, self.my_profile_maps):
                tk.Label(
                    target,
                    text="No matching replay data is available yet. Profile matching uses recorded player names.",
                    bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                ).pack(anchor="w", pady=12)
        else:
            self._render_profile_sections(
                profile, self.my_profile_summary, self.my_profile_units, self.my_profile_maps,
            )
        self.my_match_tree.delete(*self.my_match_tree.get_children())
        for match in profile["matches"]:
            self.my_match_tree.insert(
                "", "end", iid=str(match["replay_id"]), values=(
                    match["filename"], _map_name(match["map_name"], match["map_classification"]),
                    self._result(match.get("result")) or "—", self._date(match.get("imported_at")),
                ),
            )

    def _edit_battle_tag(self) -> None:
        self.show_page("settings")
        self.battle_tag_var.set(self.config.get().battle_tag)

    def _save_battle_tag(self) -> None:
        try:
            value = validate_battle_tag(self.battle_tag_var.get())
            self.config.update(battle_tag=value)
            self.settings_message.set("Player tag saved.")
            if self.current_page == "players":
                self.refresh_players()
        except ValueError as exc:
            self.settings_message.set(str(exc))

    def _reload_opponents(self) -> None:
        current_ids = set(self.repository.player_ids_for_battle_tag(self.config.get().battle_tag))
        search = self.player_search_var.get().strip()
        items = self.repository.opponents(
            100, self.player_offset, search=search,
            sort=PLAYER_SORT_OPTIONS.get(self.player_sort_var.get(), "recent"),
            exclude_player_ids=current_ids,
        )
        self.opponent_tree.delete(*self.opponent_tree.get_children())
        for item in items:
            self.opponent_tree.insert(
                "", "end", iid=str(item["id"]),
                values=(item["display_name"], item["games"], self._date(item["latest_encounter"])),
            )
        if self.opponent_selected_id and self.opponent_tree.exists(str(self.opponent_selected_id)):
            self.opponent_tree.selection_set(str(self.opponent_selected_id))
            self._show_opponent()
        elif items and not self.opponent_selected_id:
            self.opponent_tree.selection_set(str(items[0]["id"]))
            self._show_opponent()
        elif not items:
            self.opponent_title.set("No other players found")
            for target in (self.opponent_summary, self.opponent_units, self.opponent_maps):
                self._clear(target)
            self.opponent_match_tree.delete(*self.opponent_match_tree.get_children())

    def _show_opponent(self) -> None:
        selected = self.opponent_tree.selection()
        if not selected:
            return
        player_id = int(selected[0])
        self.opponent_selected_id = player_id
        profile = self.repository.opponent_detail(player_id, 100)
        if not profile:
            return
        self.opponent_title.set(profile["display_name"])
        for target in (self.opponent_summary, self.opponent_units, self.opponent_maps):
            self._clear(target)
        self._render_profile_sections(
            profile, self.opponent_summary, self.opponent_units, self.opponent_maps,
        )
        self.opponent_match_tree.delete(*self.opponent_match_tree.get_children())
        for match in profile["matches"]:
            self.opponent_match_tree.insert(
                "", "end", iid=str(match["replay_id"]), values=(
                    match["filename"], _map_name(match["map_name"], match["map_classification"]),
                    self._result(match.get("result")) or "—", self._date(match.get("imported_at")),
                ),
            )

    def _render_profile_sections(
        self,
        detail: dict[str, Any],
        performance_target: tk.Widget,
        units_target: tk.Widget,
        maps_target: tk.Widget,
    ) -> None:
        profile = detail.get("profile", {})
        outcomes = (
            f"{profile.get('wins', 0)} wins · {profile.get('losses', 0)} losses · "
            f"{profile.get('draws', 0)} draws"
        )
        header = tk.Frame(performance_target, bg=PANEL)
        header.pack(fill="x", pady=(0, 8))
        for label, value, note in (
            ("Games analyzed", str(detail.get("all_time_games", 0)), "Matches in the replay library"),
            ("Win rate", _recorded(profile.get("win_rate"), "%"), outcomes),
            ("Recorded APM", _recorded(profile.get("average_apm")), f"Approximate · {profile.get('apm_games', 0)} game(s)"),
            ("Recorded results", str(profile.get("recorded_results", 0)), f"of {detail.get('all_time_games', 0)} games"),
        ):
            card = tk.Frame(header, bg=PANEL_SOFT, highlightbackground=LINE, highlightthickness=1, padx=10, pady=8)
            card.pack(side="left", fill="both", expand=True, padx=(0, 6))
            tk.Label(card, text=label.upper(), bg=PANEL_SOFT, fg=MUTED, font=("Segoe UI Semibold", 7)).pack(anchor="w")
            tk.Label(card, text=value, bg=PANEL_SOFT, fg=CYAN, font=("Segoe UI Semibold", 17)).pack(anchor="w", pady=(4, 1))
            tk.Label(card, text=note, bg=PANEL_SOFT, fg=MUTED, font=("Segoe UI", 7), wraplength=150).pack(anchor="w")
        units = tk.Frame(units_target, bg=PANEL)
        units.pack(fill="x")
        for title, key in (("Most deployed", "top_units"), ("Upgrades recorded", "top_upgrades")):
            section = tk.Frame(units, bg=PANEL_SOFT, highlightbackground=LINE, highlightthickness=1, padx=10, pady=8)
            section.pack(side="left", fill="both", expand=True, padx=(0, 6))
            tk.Label(section, text=title, bg=PANEL_SOFT, fg=TEXT, font=("Segoe UI Semibold", 8)).pack(anchor="w")
            entries = [f"{item['name']} · {item['count']}" for item in profile.get(key, [])]
            tk.Label(
                section, text="\n".join(entries[:10]) if entries else "No data recorded",
                bg=PANEL_SOFT, fg=MUTED, font=("Segoe UI", 8), justify="left", anchor="nw",
            ).pack(anchor="w", pady=(6, 0))
        self._key_value(
            units_target, "Units lost", _recorded(profile.get("units_lost")),
        )
        self._key_value(
            units_target, "Games with tracked unit losses", str(profile.get("unit_loss_games", 0)),
        )
        maps = tk.Frame(maps_target, bg=PANEL)
        maps.pack(fill="x", pady=(0, 9))
        for label, value in (
            ("Recorded wins", str(profile.get("wins", 0))),
            ("Recorded losses", str(profile.get("losses", 0))),
            ("Recorded draws", str(profile.get("draws", 0))),
        ):
            self._key_value(maps, label, value)
        map_history = profile.get("map_history", [])
        map_panel = tk.Frame(maps_target, bg=PANEL_SOFT, highlightbackground=LINE, highlightthickness=1, padx=10, pady=8)
        map_panel.pack(fill="x", pady=(0, 8))
        tk.Label(map_panel, text="Maps played", bg=PANEL_SOFT, fg=TEXT, font=("Segoe UI Semibold", 8)).pack(anchor="w")
        tk.Label(
            map_panel,
            text="\n".join(f"{item['map_name']} · {item['games']} game(s)" for item in map_history[:20])
            if map_history else "No map data recorded",
            bg=PANEL_SOFT, fg=MUTED, font=("Segoe UI", 8), justify="left", anchor="nw",
        ).pack(anchor="w", pady=(6, 0))
        names = detail.get("name_history", [])
        if names:
            history = tk.Frame(maps_target, bg=PANEL_SOFT, highlightbackground=LINE, highlightthickness=1, padx=10, pady=8)
            history.pack(fill="x", pady=(0, 8))
            tk.Label(history, text="Name history", bg=PANEL_SOFT, fg=TEXT, font=("Segoe UI Semibold", 8)).pack(anchor="w")
            for name in names[:6]:
                player_name = name.get("display_name", "Unknown")
                clan_tag = name.get("clan_tag")
                label = f"{player_name}  ·  Clan {clan_tag}" if clan_tag else player_name
                tk.Label(history, text=label, bg=PANEL_SOFT, fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(4, 0))

    def _open_player_match(self, tree: ttk.Treeview) -> None:
        selected = tree.selection()
        if not selected:
            return
        replay_id = int(selected[0])
        self.show_page("replays")
        self.replay_selected_id = replay_id
        self._reload_replays()
        if self.replay_tree.exists(str(replay_id)):
            self.replay_tree.selection_set(str(replay_id))
            self.replay_tree.see(str(replay_id))
            self._show_replay_selection()

    def _build_reminder_refresh(self) -> None:
        self.refresh_reminders()

    def refresh_reminders(self) -> None:
        self.reminder_tree.delete(*self.reminder_tree.get_children())
        for reminder in self.repository.list_reminders():
            seconds = int(reminder["at_seconds"])
            match_time = f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
            self.reminder_tree.insert(
                "", "end", iid=str(reminder["id"]),
                values=(
                    match_time, REMINDER_CATEGORIES.get(reminder["category"], "Custom"),
                    reminder["title"], reminder["item_label"] or "—",
                    "On" if reminder["enabled"] else "Off",
                ),
            )

    def _select_reminder(self) -> None:
        selected = self.reminder_tree.selection()
        if not selected:
            return
        reminder_id = int(selected[0])
        reminder = next(
            (item for item in self.repository.list_reminders() if int(item["id"]) == reminder_id),
            None,
        )
        if not reminder:
            return
        self.reminder_selected_id = reminder_id
        self.reminder_form_title.set("Edit reminder")
        self.reminder_title_var.set(reminder["title"])
        self.reminder_time_var.set(format_elapsed_time(int(reminder["at_seconds"])))
        self.reminder_category_var.set(REMINDER_CATEGORIES.get(reminder["category"], "Custom"))
        self.reminder_item_var.set(reminder["item_label"])
        self.reminder_note_var.set(reminder["note"])
        self.reminder_enabled_var.set(bool(reminder["enabled"]))

    @staticmethod
    def _parse_time(value: str) -> int:
        return parse_elapsed_time(value)

    def _save_reminder(self) -> None:
        try:
            values = {
                "title": self.reminder_title_var.get(),
                "at_seconds": self._parse_time(self.reminder_time_var.get()),
                "category": REMINDER_CATEGORY_VALUES.get(self.reminder_category_var.get()),
                "item_label": self.reminder_item_var.get(),
                "note": self.reminder_note_var.get(),
                "enabled": self.reminder_enabled_var.get(),
            }
            if self.reminder_selected_id is None:
                reminder_id = self.repository.create_reminder(**values)
            else:
                self.repository.update_reminder(self.reminder_selected_id, **values)
                reminder_id = self.reminder_selected_id
            if self.app._overlay_window and self.app._overlay_window.winfo_exists():
                self.app._overlay_signature = None
            self.refresh_reminders()
            self.reminder_tree.selection_set(str(reminder_id))
            self.reminder_form_message.set("Reminder saved.")
        except ValueError as exc:
            self.reminder_form_message.set(str(exc))

    def _clear_reminder_form(self) -> None:
        self.reminder_selected_id = None
        self.reminder_form_title.set("Create a reminder")
        self.reminder_title_var.set("")
        self.reminder_time_var.set("00:30:00")
        self.reminder_category_var.set(REMINDER_CATEGORIES["unit_combo"])
        self.reminder_item_var.set("")
        self.reminder_note_var.set("")
        self.reminder_enabled_var.set(True)
        self.reminder_form_message.set("")
        self.reminder_tree.selection_remove(self.reminder_tree.selection())

    def _delete_reminder(self) -> None:
        selected = self.reminder_tree.selection()
        if not selected:
            return
        self.repository.delete_reminder(int(selected[0]))
        self._clear_reminder_form()
        self.refresh_reminders()
        if self.app._overlay_window and self.app._overlay_window.winfo_exists():
            self.app._overlay_signature = None

    def _toggle_reminder(self) -> None:
        selected = self.reminder_tree.selection()
        if not selected:
            return
        reminder_id = int(selected[0])
        reminder = next(
            (item for item in self.repository.list_reminders() if int(item["id"]) == reminder_id),
            None,
        )
        if not reminder:
            return
        self.repository.update_reminder(
            reminder_id,
            title=reminder["title"],
            at_seconds=int(reminder["at_seconds"]),
            category=reminder["category"],
            item_label=reminder["item_label"],
            note=reminder["note"],
            enabled=not bool(reminder["enabled"]),
        )
        self.refresh_reminders()
        if self.app._overlay_window and self.app._overlay_window.winfo_exists():
            self.app._overlay_signature = None

    def refresh_health(self) -> None:
        data = self.repository.health_summary()
        library = data["library"]
        self.health_summary_var.set(
            f"{library['total']:,} replays saved     {library['analyzed']:,} ready to explore     "
            f"{library['failed']:,} needing attention     {library['players']:,} players"
        )
        self.health_tree.delete(*self.health_tree.get_children())
        if not data["issues"]:
            self.health_tree.insert("", "end", values=("Everything looks good", "No replay processing issues were found."))
        else:
            for issue in data["issues"]:
                self.health_tree.insert(
                    "", "end", values=(
                        f"{issue['title']} · {issue['count']}",
                        issue["action"],
                    ),
                )

    def refresh_settings(self) -> None:
        self.battle_tag_var.set(self.config.get().battle_tag)
        self.auto_start_var.set(self.config.get().auto_start)

    def _date(self, value: object) -> str:
        if not value:
            return "—"
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed.astimezone().strftime("%b %d, %Y")
        except (ValueError, TypeError):
            return str(value)

    @staticmethod
    def _result(value: object) -> str:
        normalized = str(value or "").strip().casefold()
        if normalized in {"win", "won", "victory"}:
            return "Victory"
        if normalized in {"loss", "lost", "defeat"}:
            return "Defeat"
        if normalized in {"draw", "tie"}:
            return "Draw"
        return "Result recorded" if normalized else ""

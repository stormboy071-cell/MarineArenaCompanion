# Marine Arena Companion

A local Windows companion for indexing completed StarCraft II replays. Replay files are read only; analysis, configuration, logs, and the SQLite database stay in the user's local application-data directory.

## Development setup (Windows)

1. Install Python 3.11 or newer.
2. From the project folder run:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -e ".[test,tray,build]"
   ```

3. Start the desktop application:

   ```powershell
   .\scripts\dev_run.bat
   ```

Each launch opens a local profile picker. Choose a saved `PlayerName#1234` profile, or enter another tag to create/open a separate profile. This is not a Battle.net sign-in or account verification: the tag is only a local identity label. The app allows one running instance per selected profile, while different profiles can run separately. The original profile keeps its existing settings; additional profiles store their settings and logs under `%LOCALAPPDATA%\MarineArenaCompanion\profiles`. All profiles share the indexed replay database, avoiding duplicate replay decoding and storage; reminders are shared too. The local dashboard and overlay are served only on `127.0.0.1`. Select the StarCraft II `Replays` folder, then choose **Scan Library**. The folder and watcher preference persist per profile.

The optional **Start with Windows** setting is unchecked by default and uses the current user's Run registry key only after the user enables it.

Packaged Windows releases embed their GitHub repository and check for a stable update once per day. When a newer version is available, the app shows an update popup with release notes and asks before downloading or installing. **Check for Updates** checks immediately. Development builds without a configured release source can set one in the update prompt. The archive is verified against its SHA-256 checksum, staged temporarily, and swapped into the same app folder after the app exits; the temporary backup is removed after a successful update. Replay data, reminders, and settings remain in `%LOCALAPPDATA%\MarineArenaCompanion`. The updater never installs an update without confirmation.

The desktop workspace is the primary interface, with native **Overview**, **Replay library**, **Players**, **Overlay reminders**, **Library health**, and **Settings** pages; normal workflows do not open a browser. **Players** is split into **My profile** and **Opponents**. Today, the app associates replay participants with the configured profile by matching recorded player names to the name portion of the local tag. Replay files often omit the numeric Battle.net suffix, and names are not unique, so this is not proof of account ownership. Where replay data contains a stable player toon identifier, the analyzer can track that identity across name changes; reliable self-identification still needs user-confirmed mapping from a known replay slot/account identifier to the local profile.

Replay history can be searched and filtered by readable status or map; player records can be searched and sorted. A player profile shows recorded win rate, player-linked recorded APM, unit losses when unit ownership can be tracked, top recorded units/upgrades, map history, name history, and match history. Replay details include player-scoped performance, score values, unit counts, build-order events, and sampled graphs only when those values exist in the replay. APM is calculated from player-attributed replay command, selection, and control-group events divided by recorded match duration; other profile values are likewise derived from stored replay events and metadata. Missing attribution or source events are shown as unavailable rather than guessed. Raw protocol event names, payloads, machine paths, and decoder internals remain stored locally and are not shown in the user-facing interface. The optional local browser dashboard remains available on `127.0.0.1`; its dashboard-only sections can differ from the native desktop workspace.

The web dashboard bundles Noto Sans from Google Fonts under the SIL Open Font License to render Latin and Cyrillic player names consistently on Windows and mobile browsers; the full license is included as `src\marine_arena_companion\web\NotoSans-OFL.txt`.

Create personal timed cues in **Overlay reminders** with an elapsed match time in `HH:MM:SS` (for example `00:40:00` or `01:24:03`), reminder text, category (unit combination, hero, elite, or custom), optional unit/character label, and notes. This is duration from match start, not a time of day; the time field accepts durations longer than 24 hours. Choose **Open game overlay** in the desktop app for a draggable, translucent, always-on-top timer and cue window. Start its timer at match start, then pause or reset it as needed; a cue becomes highlighted when its match time arrives and can be dismissed. Borderless/windowed StarCraft II mode is recommended, because exclusive fullscreen games may cover desktop overlays. The cue library is stored locally in the versioned SQLite database and does not read game memory or infer strategy.

## Tests

```powershell
python -m pytest
```

Tests use temporary files and fabricated metadata only to test application logic; they are not replay statistics and are not presented as actual game data.

## Build a Windows executable

```powershell
.\scripts\build_windows.bat
```

The build script generates a portable onedir package at `dist\MarineArenaCompanion`; its executable runs together with the adjacent `_internal` folder. This is temporary build output, not the installed app or source of truth, and can be removed after deployment (the next build recreates it). For normal per-user use, install the complete bundle at `%LOCALAPPDATA%\Programs\MarineArenaCompanion` and keep only a desktop shortcut pointing to its executable. Replay data, reminders, configuration, and logs remain separately in `%LOCALAPPDATA%\MarineArenaCompanion` and are shared between app versions. Release builds embed the repository from the GitHub Actions release context, so installed copies can automatically check that same project for updates without asking users to configure it.

To publish an update, increment both `pyproject.toml` and `src\marine_arena_companion\__init__.py` to the same semantic version, then push a matching `vX.Y.Z` tag to the public GitHub repository. The GitHub Actions release workflow builds the canonical Windows bundle and attaches the expected ZIP and SHA-256 checksum assets. Windows is the only packaged updater target today; Android and iOS require platform-specific clients and release channels.

## Features and limitations

- Recursive `.SC2Replay` watcher with stability checks and bounded analysis workers.
- Persistent replay-file index: unchanged files are recognized by path, size, and modified time, so later scans skip hashing and decoding them. New or changed files are still SHA-256 checked, preserving deduplication across renamed/moved copies; failed files can be explicitly retried.
- Versioned SQLite schema, persistent scan status, raw event storage, native desktop workspace, local JSON API, optional browser dashboard, and overlay.
- User-authored, timed unit-composition, hero, elite, and custom reminders displayed in a translucent always-on-top desktop overlay with a match timer.
- Replay metadata, tracker/game/message event decoding via Blizzard's `s2protocol`; unsupported builds are reported instead of silently guessed.
- SHA-256 deduplication and a read-only replay scanner; every completed replay remains in its original location and is only opened for reading.
- Marine Arena map classification is deliberately conservative. The editable seed is `data\marine_arena_semantics.json`; at runtime, edit `%LOCALAPPDATA%\MarineArenaCompanion\data\marine_arena_semantics.json` and restart the app to apply confirmed mappings.
- Local profiles are separate workspaces, not authenticated Battle.net accounts. The current **My profile** grouping is name-based and can misidentify duplicate names; stable replay toon identifiers and user-confirmed identity mapping are needed for trustworthy account-level grouping. The app does not contact Battle.net. Advanced Marine Arena strategy tendencies and live-match recommendations are not claimed.
- Profile metrics are limited to events the replay decoder can attribute to a stable player ID or unit owner. Older or partially decoded replays can have incomplete action, unit-loss, or name-history samples; profiles show those gaps instead of treating missing events as zero.
- Replay structure totals use a curated list of recognized StarCraft II structure names; unfamiliar/custom unit names are not confidently classified as structures and can be omitted from those totals.
- Android and hosted live access are not available in this release. The desktop app currently serves its dashboard on `127.0.0.1` and stores its database locally. Cross-device access requires a hosted API, authenticated accounts, secure per-user replay-data synchronization, and a separately packaged mobile client; the local dashboard is not exposed to the network.
- No game-memory access, input automation, replay upload, fabricated replay-derived data, or claims of live-match insight.
- No real `.SC2Replay` files were included in this workspace, so replay decoding has not been validated against the user's archive. The automated scanner tests use synthetic bytes and mocked decoded metadata, never actual replay statistics.
- The packaged tray icon is available when optional tray dependencies are installed; the Tk application window remains the primary control surface.

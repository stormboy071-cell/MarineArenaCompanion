@echo off
setlocal
cd /d "%~dp0\.."
python -m pip install -e ".[tray,build]"
if errorlevel 1 exit /b 1
python scripts\write_update_repository.py build\update_repository.txt
if errorlevel 1 exit /b 1
pyinstaller --noconfirm --clean --windowed --name MarineArenaCompanion --additional-hooks-dir scripts --collect-all s2protocol --collect-all mpyq --paths src --distpath dist --workpath build\MarineArenaCompanion --add-data "build\update_repository.txt;marine_arena_companion" --add-data "src\marine_arena_companion\web;marine_arena_companion\web" --add-data "src\marine_arena_companion\data;marine_arena_companion\data" src\marine_arena_companion\main.py
if errorlevel 1 exit /b 1
echo Build output: dist\MarineArenaCompanion\MarineArenaCompanion.exe

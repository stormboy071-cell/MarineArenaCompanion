@echo off
cd /d "%~dp0\.."
python -m marine_arena_companion
if errorlevel 1 pause

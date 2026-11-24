@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File set_mitigations.ps1 restore
pause

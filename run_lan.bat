@echo off
title TurnitOut Server
cd /d "%~dp0"
echo.
echo   TurnitOut - starting in LAN mode...
echo   Leave this window open. Students on your network can connect
echo   using the addresses printed below.
echo.
python run.py --lan
pause

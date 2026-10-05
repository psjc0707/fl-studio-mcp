@echo off
REM FL Studio Bridge launcher
REM 1) starts fl_bridge.py on :8765 (talks to FL Studio via MIDI)
REM 2) starts fl_mcp.py on :8766 (exposes tools for any Opus 5.5 API client)

setlocal
set SCRIPT_DIR=%~dp0
set PYTHON_EXE=py -3.12
cd /d "%SCRIPT_DIR%"

REM ensure deps are present
%PYTHON_EXE% -m pip install --quiet pyflp mido python-rtmidi 2>nul

start "FL Bridge" cmd /k "%PYTHON_EXE% fl_bridge.py"
timeout /t 2 /nobreak >nul
start "FL MCP Gateway" cmd /k "%PYTHON_EXE% fl_mcp.py"

echo.
echo ============================================
echo  FL Studio Bridge is live
echo  Bridge    : http://localhost:8765
echo  Tool GW   : http://localhost:8766
echo  GET /tools  -> catalog of 13 tools
echo  POST /call  -> {"name": "...", "arguments": {...}}
echo ============================================
echo.
echo Press any key to stop both servers...
pause >nul
taskkill /FI "WINDOWTITLE eq FL Bridge*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq FL MCP Gateway*" /T /F >nul 2>&1
endlocal
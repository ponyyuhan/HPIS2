@echo off
rem Double-click on Windows to start the HPIS 2.0 demo and open the browser.
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv-demo\Scripts\python.exe" (
  py -3 -m venv .venv-demo || python -m venv .venv-demo
  ".venv-demo\Scripts\python.exe" -m pip install --upgrade pip
  ".venv-demo\Scripts\python.exe" -m pip install -e . -r demo\requirements.txt
)
".venv-demo\Scripts\python.exe" -m demo --open %*
pause

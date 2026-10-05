@echo off
setlocal
cd /d "%~dp0"
python scraper_subakoua.py --periods 1 --show-browser
if errorlevel 1 (
  echo.
  echo [ERREUR] Le scraper s'est termine avec une erreur.
) else (
  echo.
  echo [OK] Extraction terminee.
)
pause

@echo off
setlocal
cd /d "%~dp0"
if not exist .env (
  echo [ERREUR] Le fichier .env est absent.
  echo Copie .env.example vers .env puis renseigne tes identifiants.
  pause
  exit /b 2
)
python scraper_subakoua.py --all --show-browser
if errorlevel 1 (
  echo.
  echo [ERREUR] Le scraper s'est termine avec une erreur.
) else (
  echo.
  echo [OK] Extraction terminee.
)
pause

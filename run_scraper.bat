@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo      SCRAPER SUBAKOUA - EXTRACTION LOCALE
echo ================================================
echo.
if not exist .env (
  echo [INFO] Aucun fichier .env detecte.
  echo [INFO] Le scraper demandera les identifiants Subakoua dans le terminal.
  echo [INFO] Pour eviter de les saisir a chaque fois, creez .env depuis .env.example.
  echo.
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

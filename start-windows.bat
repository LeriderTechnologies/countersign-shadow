@echo off
rem Countersign Shadow: dubbelklik om te starten (Windows). Niets kan betaald of verstuurd worden.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m countersign_shadow start %*
) else (
  python -m countersign_shadow start %*
)
if errorlevel 1 echo. & echo Python niet gevonden of fout. Zie pilot\GIDS.md stap 1.
pause

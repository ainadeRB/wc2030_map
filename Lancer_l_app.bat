@echo off
setlocal
title Coupe du Monde 2030 - Carte Hotels

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  Python n'est pas installe sur cet ordinateur.
    echo.
    echo  Installe-le d'abord ^(gratuit^) :
    echo  1. Ouvre le Microsoft Store
    echo  2. Cherche Python 3.12 et installe-le
    echo  3. Ferme cette fenetre et relance ce script
    echo ============================================================
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo Premiere installation, ca peut prendre quelques minutes...
    echo.
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Erreur : impossible de creer l'environnement Python.
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Erreur pendant l'installation des dependances. Verifie ta connexion internet
        echo ^(necessaire uniquement pour cette premiere installation^) et relance ce script.
        pause
        exit /b 1
    )
) else (
    call .venv\Scripts\activate.bat
)

echo.
echo Lancement de l'application... Une fenetre de navigateur va s'ouvrir.
echo Pour arreter l'application, ferme simplement cette fenetre noire.
echo.
streamlit run app.py --server.headless false

pause

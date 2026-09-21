@echo off
setlocal EnableDelayedExpansion
title Coupe du Monde 2030 - Carte Hotels

cd /d "%~dp0"

set "PYEXE="

python --version >nul 2>nul
if not errorlevel 1 (
    set "PYEXE=python"
)

if not defined PYEXE (
    for %%P in (
        "%LOCALAPPDATA%\anaconda3\python.exe"
        "%USERPROFILE%\anaconda3\python.exe"
        "%LOCALAPPDATA%\Continuum\anaconda3\python.exe"
        "%USERPROFILE%\Anaconda3\python.exe"
        "C:\ProgramData\Anaconda3\python.exe"
        "%USERPROFILE%\miniconda3\python.exe"
        "%LOCALAPPDATA%\miniconda3\python.exe"
        "C:\ProgramData\Miniconda3\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    ) do (
        if not defined PYEXE (
            if exist "%%~P" (
                set "PYEXE=%%~P"
            )
        )
    )
)

if not defined PYEXE (
    echo.
    echo ============================================================
    echo  Python n'est pas installe sur cet ordinateur.
    echo  Telechargement et installation automatique de Python...
    echo  ^(connexion internet necessaire, ca peut prendre quelques
    echo  minutes. Aucun droit administrateur n'est requis.^)
    echo ============================================================
    echo.
    set "PYINSTALLER=%TEMP%\python_installer_wc2030.exe"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '!PYINSTALLER!' -UseBasicParsing } catch { exit 1 }"
    if errorlevel 1 (
        echo.
        echo ============================================================
        echo  Le telechargement de Python a echoue.
        echo.
        echo  Cause frequente : le reseau de cet ordinateur ^(proxy/pare-feu
        echo  d'entreprise^) bloque le telechargement automatique. Essaie
        echo  depuis un autre reseau ^(ex. partage de connexion mobile^),
        echo  ou installe Python manuellement depuis
        echo  https://www.python.org/downloads/ ^(coche bien "Add python.exe
        echo  to PATH" pendant l'installation^) puis relance ce script.
        echo ============================================================
        echo.
        pause
        exit /b 1
    )

    echo Installation de Python en cours, merci de patienter...
    start /wait "" "!PYINSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0

    del "!PYINSTALLER!" >nul 2>nul

    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    ) do (
        if not defined PYEXE (
            if exist "%%~P" (
                set "PYEXE=%%~P"
            )
        )
    )

    if not defined PYEXE (
        echo.
        echo ============================================================
        echo  L'installation automatique de Python a echoue ^(souvent a
        echo  cause d'une politique de securite de l'entreprise qui bloque
        echo  l'execution de nouveaux programmes^).
        echo.
        echo  Installe Python manuellement depuis
        echo  https://www.python.org/downloads/ ^(coche bien "Add python.exe
        echo  to PATH" pendant l'installation^), puis relance ce script.
        echo ============================================================
        echo.
        pause
        exit /b 1
    )

    echo.
    echo Python installe avec succes.
    echo.
)

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo Creation de l'environnement Python...
    echo.
    "%PYEXE%" -m venv .venv
    if errorlevel 1 (
        echo.
        echo Erreur : impossible de creer l'environnement Python.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

if not exist ".venv\Scripts\streamlit.exe" (
    echo.
    echo Installation des dependances, ca peut prendre quelques minutes
    echo ^(connexion internet necessaire pour cette etape uniquement^)...
    echo.
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
)

if not exist ".venv\Scripts\streamlit.exe" (
    echo.
    echo ============================================================
    echo  L'installation des dependances a echoue.
    echo.
    echo  Cause frequente : le reseau de cet ordinateur ^(proxy/pare-feu
    echo  d'entreprise^) bloque l'acces a internet necessaire pour cette
    echo  installation. Essaie depuis un autre reseau ^(ex. partage de
    echo  connexion mobile^), ou contacte ton service informatique.
    echo.
    echo  Relancer ce script reessaiera automatiquement l'installation.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

echo.
echo Lancement de l'application... Une fenetre de navigateur va s'ouvrir.
echo Pour arreter l'application, ferme simplement cette fenetre noire.
echo.
streamlit run app.py --server.headless false

pause

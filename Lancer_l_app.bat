@echo off
setlocal
title Coupe du Monde 2030 - Carte Hotels

cd /d "%~dp0"

python --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  Python n'est pas installe ^(ou n'est pas configure
    echo  correctement^) sur cet ordinateur.
    echo.
    echo  S'il n'est pas du tout installe ^(gratuit^) :
    echo  1. Ouvre le Microsoft Store
    echo  2. Cherche Python 3.12 et installe-le
    echo  3. Ferme cette fenetre et relance ce script
    echo.
    echo  Si Python semble deja installe mais que ce message persiste :
    echo  Windows a parfois un raccourci "python" qui pointe vers le
    echo  Store au lieu du vrai Python installe. Va dans Parametres
    echo  Windows ^> Applications ^> Parametres avances des applications
    echo  ^> Alias d'execution des applications, et desactive "python.exe"
    echo  ^(et "python3.exe" si present^), puis relance ce script.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo Creation de l'environnement Python...
    echo.
    python -m venv .venv
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
    pip install -r requirements.txt
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

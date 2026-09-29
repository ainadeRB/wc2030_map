@echo off
setlocal EnableDelayedExpansion
title Publier vers l'equipe

rem ============================================================
rem A REMPLIR UNE SEULE FOIS : chemin complet vers le dossier
rem SharePoint/OneDrive partage avec l'equipe (celui qui contient
rem Lancer_l_app.bat, celui que chaque collegue a synchronise).
rem Modifie la ligne ci-dessous, puis enregistre ce fichier.
rem Exemple :
rem set "DEST=C:\Users\ainarabee\Entreprise\Equipe WC2030 - Documents\wc2030_map"
rem ============================================================
set "DEST=C:\CHEMIN\A\MODIFIER\wc2030_map"

cd /d "%~dp0"

if "%DEST%"=="C:\CHEMIN\A\MODIFIER\wc2030_map" (
    echo.
    echo ============================================================
    echo  Modifie d'abord la ligne "set DEST=..." en haut de ce
    echo  fichier ^(clic droit sur Publier_vers_equipe.bat ^> Modifier^)
    echo  avec le chemin reel de ton dossier SharePoint partage.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

if not exist "%DEST%" (
    echo.
    echo Dossier introuvable : %DEST%
    echo Verifie le chemin dans la ligne "set DEST=..." de ce script.
    echo.
    pause
    exit /b 1
)

echo Recuperation des derniers changements depuis GitHub...
git pull
if errorlevel 1 (
    echo.
    echo Erreur : le git pull a echoue. Verifie ta connexion internet
    echo et que ce dossier est bien un clone git du projet.
    pause
    exit /b 1
)

echo.
echo Preparation des fichiers a publier...
set "ZIPFILE=%TEMP%\wc2030_publish.zip"
git archive --format=zip -o "%ZIPFILE%" HEAD
if errorlevel 1 (
    echo.
    echo Erreur lors de la preparation des fichiers a publier.
    pause
    exit /b 1
)

echo Copie vers le dossier partage...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%ZIPFILE%' -DestinationPath '%DEST%' -Force"

del "%ZIPFILE%" >nul 2>nul

echo.
echo ============================================================
echo  Code publie vers :
echo  %DEST%
echo.
echo  OneDrive va le synchroniser vers toute l'equipe dans les
echo  prochaines minutes. Les vraies donnees ^(hotels.xlsx, poi.xlsx,
echo  photos^) ne sont jamais touchees par cette publication.
echo ============================================================
echo.
pause

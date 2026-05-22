@echo off
chcp 65001 >nul
echo.
echo ╔══════════════════════════════════════════════════════╗
echo ║           🅿️  FastPark — Lancement complet          ║
echo ╚══════════════════════════════════════════════════════╝
echo.

cd /d "C:\Users\surfa\Documents\FastPark"

echo [1/3] Activation de l'environnement virtuel...
call fastpark_env\Scripts\activate
if errorlevel 1 (
    echo ❌ Environnement virtuel introuvable.
    echo    Lancez : python -m venv fastpark_env
    pause & exit /b 1
)
echo ✅ Environnement activé

echo.
echo [2/3] Installation / vérification des dépendances...
pip install -r requirements.txt --quiet
echo ✅ Dépendances OK

echo.
echo [3/3] Démarrage du serveur FastPark...
echo.
echo ┌─────────────────────────────────────────────────┐
echo │  🌐  Dashboard  : http://localhost:5000          │
echo │  👑  Admin      : admin / fastpark123            │
echo │  📝  Inscription: http://localhost:5000/register │
echo │  🏥  Monitoring : http://localhost:5000/health   │
echo └─────────────────────────────────────────────────┘
echo.
echo 💡 Lancez le simulateur dans un autre terminal :
echo    double-clic sur lancer_simulateur.bat
echo.

python app.py

pause

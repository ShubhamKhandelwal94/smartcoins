@echo off
setlocal
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --noconsole --onefile --name SmartCoins app.py
echo Built: dist\SmartCoins.exe
pause

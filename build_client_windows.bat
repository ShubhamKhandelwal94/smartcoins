@echo off
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --noconsole --onefile --name SmartCoinsClient client.py
pause

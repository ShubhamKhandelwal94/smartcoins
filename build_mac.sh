#!/bin/sh
set -e
python3 -m pip install -r requirements.txt
python3 -m PyInstaller --noconfirm --clean --windowed --onefile --name SmartCoins app.py
echo "Built: dist/SmartCoins"

# Smart Coins — Final Build

## Run on macOS
python3 app.py

## Build macOS executable
chmod +x build_mac.sh
./build_mac.sh

Output: `dist/SmartCoins`

## Build Windows executable
Run `build_windows.bat` on Windows.

Output: `dist\\SmartCoins.exe`

## Runtime dependencies
The application uses Python standard-library SQLite/Tkinter. PyInstaller is only required to build the executable.

Default login: `admin` / `admin` — change it immediately.

Database: `data/smart_coins.db`
Backups: `backups/` or the Admin-selected backup path.

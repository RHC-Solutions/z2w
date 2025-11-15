@echo off
echo Starting Zendesk to Wasabi B2 Offloader...
echo.
pip install SQLAlchemy
python.exe -m pip install --upgrade pip
pip install python-dotenv
pip install Flask
python main.py
pause



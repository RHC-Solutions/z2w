@echo off
echo Starting Zendesk to Wasabi B2 Offloader...
echo.
pip install SQLAlchemy
python.exe -m pip install --upgrade pip

python main.py
pause



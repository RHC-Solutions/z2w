@echo off
echo Starting Zendesk to Wasabi B2 Offloader...
echo.
python -m pip install -r requirements.txt
python -m pip install --upgrade pip
python main.py
pause



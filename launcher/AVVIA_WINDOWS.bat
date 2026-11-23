@echo off
rem Doppio clic per aprire il launcher di SalesForB (serve Python 3.9 o successivo da python.org).
cd /d "%~dp0"
py -3 salesforb_launcher.py %*
pause

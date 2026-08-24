@echo off
cd /d "%~dp0"
python -m giftbook_bridge --config giftbook.config.example.json
pause

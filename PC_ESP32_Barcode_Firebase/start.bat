@echo off
title Barcode Scanner System
echo ============================================
echo PC + Barcode Scanner + ESP32 + Firebase
echo ============================================
echo.
python main.py
if errorlevel 1 (
    echo.
    echo Python failed to start.
    echo Try: py main.py
    pause
)

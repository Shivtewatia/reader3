@echo off
setlocal EnableDelayedExpansion
title Reader3 - Your Personal Library

echo.
echo  =========================================
echo    Reader3 ^| Your Personal Library
echo  =========================================
echo.

:: Add common uv install location to PATH for this session
set "PATH=%PATH%;%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin"

:: -------------------------------------------------------
:: Load API key from any *.env file in this folder
:: (works with key.env, .env, mykeys.env — any name ending in .env)
:: -------------------------------------------------------
for /f "delims=" %%F in ('dir /b "*.env" 2^>nul') do (
    for /f "usebackq tokens=1,* delims==" %%A in ("%%F") do (
        if /i "%%A"=="ANTHROPIC_API_KEY" (
            set "ANTHROPIC_API_KEY=%%B"
            echo  API key loaded from %%F
        )
    )
    goto :env_done
)
:: Also check for hidden .env file
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if /i "%%A"=="ANTHROPIC_API_KEY" (
            set "ANTHROPIC_API_KEY=%%B"
            echo  API key loaded from .env
        )
    )
)
:env_done

:: -------------------------------------------------------
:: Check if uv is available, install if missing
:: -------------------------------------------------------
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo  [1/3] Installing uv package manager...
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" >nul 2>&1
    set "PATH=%PATH%;%USERPROFILE%\.local\bin"
    where uv >nul 2>nul
    if !errorlevel! neq 0 (
        echo.
        echo  ERROR: Could not install uv automatically.
        echo  Please install it manually from: https://docs.astral.sh/uv/
        pause
        exit /b 1
    )
    echo  [1/3] uv installed successfully.
) else (
    echo  [1/3] uv found.
)

echo  [2/3] Setting up dependencies...
uv sync --quiet
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo  [2/3] Dependencies ready.

echo  [3/3] Starting server... your browser will open automatically.
echo.
echo  Keep this window open while using the app in your browser.
echo  To stop: press Ctrl+C in this window.
echo.

uv run server.py
pause

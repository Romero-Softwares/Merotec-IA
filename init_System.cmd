@echo off
chcp 65001 >nul
title INICIALIZACAO
color 02
cls

echo =======================================================
echo                      MEROTEC IA
echo =======================================================
echo [SISTEMA] Iniciando processos locais...

:: Usa sempre a pasta onde este iniciador esta, inclusive apos mover/clonar a IDE.
cd /d "%~dp0"

:: Ativa o ambiente virtual e inicia a IDE
if not exist "venv\Scripts\python.exe" (
    color 0C
    echo [ERRO] Ambiente venv nao encontrado em: %cd%
    echo [AVISO] criando ambiente venv...
    py -3 -m venv venv 2>nul || python -m venv venv
    if errorlevel 1 goto :startup_error
    echo [VENV] Instalando dependencias iniciais...
    "venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :startup_error
)

echo [VENV] Ambiente virtual localizado.

echo [SISTEMA] Carregando configuracoes automáticas do painel...
echo.
echo ********************************************************
echo.
echo [SISTEMA] Abrindo a interface grafica da IDE...
"venv\Scripts\python.exe" main.py
if errorlevel 1 goto :startup_error
exit /b 0

:startup_error
color 0C
echo.
echo [ERRO] Nao foi possivel iniciar a IDE. Revise a mensagem acima.
pause
exit /b 1

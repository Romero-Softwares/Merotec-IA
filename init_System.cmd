@echo off
chcp 65001 >nul
title INICIALIZACAO
color 02
cls

echo =======================================================
echo                      MEROTEC IA
echo =======================================================
echo [SISTEMA] Iniciando processos locais...

:: Navega ate o diretorio do projeto
cd /d "C:\Users\Merotec\Desktop\AI_Software_Enginering"

:: Ativa o ambiente virtual e inicia a IDE
if not exist venv (
    color 0C
    echo [ERRO] Ambiente venv nao encontrado em: %cd%
    echo [AVISO] criando ambiente venv...
    python -m venv venv
    echo [AVISO] Ambiente venv criado em: %cd%
    pause
    exit
)

echo [VENV] Ativando ambiente virtual...
call venv\Scripts\activate
echo [VENV] ambiente virtual ativado!

set "TCL_LIBRARY=%LOCALAPPDATA%\Programs\Python\Python314\tcl\tcl8.6"
set "TK_LIBRARY=%LOCALAPPDATA%\Programs\Python\Python314\tcl\tk8.6"

echo [SISTEMA] Carregando configuracoes automáticas do painel...
echo.
echo ********************************************************
echo.
echo [SISTEMA] Abrindo a interface grafica da IDE...
python main.py
pause
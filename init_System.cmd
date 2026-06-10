@echo off
chcp 65001 >nul
title GERENCIAMENTO
color 02
cls

echo =======================================================
echo          SISTEMA DE GESTAO
echo =======================================================
echo [SISTEMA] Iniciando processos locais...

:: Navega ate o diretorio do projeto
cd /d "C:\Users\Merotec\Desktop\AI_Software_Enginering"

:: 2. Ativa o ambiente virtual e inicia a IDE
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
if "%AI_PROVIDER%"=="" set "AI_PROVIDER=codex"
echo [IA] motor principal: %AI_PROVIDER%
echo [IA] Codex usa a conta ja logada no Windows.
echo.
echo ********************************************************
echo.
echo [SISTEMA] executando sistema de gerenciamento...
:: Inicia o script principal
echo.
python main.py
echo [SISTEMA] sistema de gerenciamento interrompido!

:: Mantem o terminal aberto e pronto para novos comandos
cmd /k

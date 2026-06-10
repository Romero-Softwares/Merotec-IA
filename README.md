# Merotec IA IDE

Projeto Python desktop com interface em CustomTkinter para automacao assistida por IA, gerenciamento de projetos, execucao de comandos e captura de voz.

## Requisitos

- Python 3.11 ou superior
- Windows recomendado para a interface desktop e recursos de voz
- Dependencias listadas em `requirements.txt`

## Instalação

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Execução

```bash
python main.py
```

## Estrutura

- `main.py`: entrada principal da IDE.
- `modules/`: modulos de configuracao, motor, memoria, executor, projetos e voz.
- `projects/unity-mini-csharp-game/`: exemplo de projeto/jogo com scripts Unity e versao web jogavel.
- `tcl_runtime/`: runtime Tcl/Tk local usado para estabilizar a execucao no Windows.

## Observações para Git

Arquivos locais, historicos da IDE, backups, ambientes virtuais e caches estao ignorados no `.gitignore` para evitar envio de dados temporarios ou sensiveis.

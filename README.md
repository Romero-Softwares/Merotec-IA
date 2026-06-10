# Merotec IA IDE

Projeto Python desktop com interface em CustomTkinter para automação assistida por IA, gerenciamento de projetos, execução de comandos e captura de voz.

## Requisitos

- Python 3.11 ou superior
- Windows recomendado para a interface desktop e recursos de voz
- Dependências listadas em `requirements.txt`

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

## Sequência de acesso

1. Abra o projeto pelo arquivo `init_System.cmd` ou execute `python main.py` no terminal.
2. Aguarde a interface carregar e confirme se o motor principal está como `codex`.
3. Se aparecer aviso de Codex sem login, clique em `Entrar Codex`.
4. Conclua o login na janela do terminal aberta automaticamente.
5. Volte para a Merotec IA IDE e aguarde o status `Codex pronto`.
6. Abra ou selecione o workspace desejado antes de pedir alterações, testes ou deploy.

## Estrutura

- `main.py`: entrada principal da IDE.
- `modules/`: módulos de configuração, motor, memória, executor, projetos e voz.
- `projects/unity-mini-csharp-game/`: exemplo de projeto/jogo com scripts Unity e versão web jogável.
- `tcl_runtime/`: runtime Tcl/Tk local usado para estabilizar a execução no Windows.

## Publicação no GitHub

Arquivos locais, históricos da IDE, backups, ambientes virtuais e caches estão ignorados no `.gitignore` para evitar envio de dados temporários ou sensíveis.

Fluxo recomendado depois de criar o repositório vazio no GitHub:

```bash
git remote add origin https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git
git add .
git commit -m "Preparar projeto para GitHub"
git push -u origin main
```

Se o remoto `origin` já existir, use:

```bash
git remote set-url origin https://github.com/SEU_USUARIO/SEU_REPOSITORIO.git
git push -u origin main
```

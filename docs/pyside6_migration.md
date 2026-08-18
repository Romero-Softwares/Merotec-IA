# Migração completa para PySide6

## Objetivo

A interface final deve usar somente PySide6. A versao CustomTkinter e a
referencia de comportamento durante a migração, nao uma segunda interface
permanente.

## Contratos que precisam de equivalencia

| Area | Fluxos atuais que precisam continuar funcionando | Estado PySide6 |
| --- | --- | --- |
| Shell da IDE | Menu, atalhos, barra lateral, painel de projetos, abas, status e persistencia da janela | Concluido |
| Editor | Abrir/salvar, imagens, busca, simbolos, completação, pares, comentarios, indentacao, zoom e marcadores | Concluido |
| Terminal | Comando livre, execução Python, streaming, cancelamento e limpeza | Concluido |
| Agente IA | Streaming, memoria, retry, aprovações e acoes de arquivo/comando | Concluido |
| Chat | Mensagens, anexos, imagens, clipboard, TTS e voz | Concluido |
| Navegador | Chat Web, navegação, inspeção e testes visuais | Concluido |
| Configurações | Perfis, login Codex, provedores, voz e preferencias | Concluido |
| Extensões | Plugins recebem `app`, `settings`, `workspace`, `engine`, `project_manager` e `executor` | Concluido |

## Regra de segurança 

Nenhuma thread de IA, terminal, voz ou navegador pode alterar widgets
diretamente. A camada `modules.qt_ui_bridge.QtUiBridge` concentra o despacho
para a thread Qt e substitui gradualmente o uso de `after` do Tk.

## Arranque

O `main.py` abre PySide6 por padrao. A interface CustomTkinter permanece
temporariamente acessivel por `python main.py --legacy-tk` para consulta de
comportamento durante os ultimos ajustes de equivalencia.

# Execução, memória e diagnóstico geral da IDE

A IDE guarda resultados observados por projeto em
`.merotec_system_ai/execution_lessons.json`. O contexto enviado aos provedores de
IA inclui esse histórico, o contexto atual e orientações para verificar ambiente,
interpretador e a etapa que demora antes de atribuir um erro ao código.

Um resultado só recebe `verified_success` quando um processo observado termina
com código inteiro zero, sem timeout ou cancelamento. Texto do usuário, mensagens
do modelo e progresso parcial não comprovam aprovação. Código zero também não
comprova cobertura, comportamento visual nem funcionamento físico de dispositivos.

## Camada comum para todos os fluxos

`modules/process_runtime.py` fornece a fachada de subprocessos da IDE e o ciclo
de observação utilizado pelos processos Qt. Não modifica o módulo subprocess
global do Python nem o comportamento de bibliotecas externas. São cobertos os
lançamentos da IDE: terminal, agente, execução de scripts, validadores de linguagens,
compiladores, deploy, Git, instaladores, firmware/PCB, auxiliares de navegador,
provedores de IA e ferramentas de áudio/vídeo. O executor de testes separado mantém
seu próprio controle de árvore e relatório; os processos de limpeza pertencem à
operação que estão interrompendo.

Cada resultado inclui operação, origem, cwd, duração, estado e uma sugestão de
diagnóstico baseada nos sintomas. O registro diferencia falha ao iniciar,
cancelamento, timeout, falha do processo e aprovação por código zero. Não há
reexecução automática de comandos lembrados. Credenciais e perfis de Git/deploy
não são redirecionados. Padrões de segredo e valores de credenciais conhecidos
são filtrados; respostas interativas não são comandos nem entram no histórico.

Os prazos padrão das operações reconhecidas são:

| Operação | Prazo |
| --- | --- |
| Testes | 120 s |
| Compilação/build/check | 600 s |
| Deploy, publish, push, upload | 900 s |
| Instalação, clone, atualização de dependências | 900 s |
| Git local | 180 s |
| Criação por comando | 300 s |
| Diagnósticos reconhecidos | 60 s |
| Executor direto do Codex | 1.800 s |
| Aplicativos, scripts comuns, servidores e monitor serial persistentes | Sem limite curto automático |

Prazos explícitos do chamador prevalecem. A classificação reconhece termos do
comando, não determina a intenção de qualquer programa arbitrário. Um script que
implementa deploy sem declarar isso precisa de operação/prazo explícitos via
`process_scope`. Servidores e monitores permanecem ativos até interrupção.
Timeouts e cancelamentos encerram a árvore de processos pertencente ao lançamento;
não encerram processos alheios.

O terminal persistente recolhe o código de saída em uma linha separada e acompanha
o diretório resultante. No Unix não usa subshell para executar o comando, preservando
`cd` e exports. Em comandos compostos, o status observado é o que o próprio shell
retorna; código zero não prova que todas as etapas intermediárias passaram.

Criações de projeto, alterações e validações de código têm registros próprios:
`applied` significa ação local aplicada, `rejected` significa recusada. Nenhum dos
dois é apresentado como exit code de processo ou comprovação funcional. O histórico
usa hashes das propostas de código e não guarda seu conteúdo.

## Ambiente de testes

Comandos simples `[EXECUTE: python -m unittest ...]`, `python -m pytest ...` e
`pytest ...` passam pelo executor Python da IDE. Ele mantém o diretório de
trabalho do projeto, escolhe o `venv`/`.venv` local para `python` sem caminho
explícito e redireciona AppData e temporários **somente no processo de testes**:

- `LOCALAPPDATA`, `APPDATA`, `TEMP`, `TMP`, `TMPDIR`;
- `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`.

Antes de iniciar, verifica se esses diretórios locais aceitam escrita. O ambiente
global da IDE, `HOME`, `USERPROFILE`, `CODEX_HOME`, PATH e credenciais permanecem
preservados. Essa preparação não é um sandbox de segurança: testes ainda podem
acessar caminhos explícitos permitidos pelo sistema.

O limite automático é de 120 segundos. Um timeout interrompe a árvore do processo
iniciado, registra `timeout` e exige diagnóstico; não declara que SciPy, interface,
persistência ou permissões são a causa. A saída continua no painel inferior da IDE,
com stdout/stderr incremental. O relatório estruturado mais recente fica em
`.merotec_system_ai/test_runtime/latest_run.json` e contém argumentos filtrados,
diretórios utilizados, duração, código de saída e estado. Não contém a saída bruta
nem uma cópia de todas as variáveis de ambiente. Código Python inline é omitido.

Para executar o mesmo mecanismo manualmente no PowerShell, a partir da pasta da
IDE (troque o workspace e o interpretador pelos caminhos do seu projeto):

```powershell
python modules/project_test_runner.py --workspace C:\Projetos\MeuApp --timeout 120 -- C:\Projetos\MeuApp\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Confirme o framework real antes de escolher unittest ou pytest. Aumente o timeout
explicitamente quando a duração esperada da suíte justificar. Esse ambiente testa
persistência isolada; testes que intencionalmente dependem do perfil real devem
declarar essa dependência e usar um ambiente próprio adequado.

## Repetições e escopo

Após duas falhas consecutivas do mesmo comando, código de saída e contexto, a IDE
recusa outra tentativa automática idêntica e devolve orientação de diagnóstico ao agente.
O contexto usa metadados de até 4.000 arquivos de código/configuração, interpretador
e variáveis relevantes do ambiente, além do estado local do Git. Alterações nessas entradas permitem outra
tentativa. Logs, a memória e os temporários internos não contam como correção.
Um diagnóstico reconhecido, observado com sucesso depois da última falha, permite
uma nova tentativa: outra falha exigirá outro fato novo. Isso não comprova que o
diagnóstico seja suficiente; o agente deve interpretar o resultado. Tentativas
manuais são permitidas com aviso. Uma estratégia diferente deve explicar o fato
novo; o histórico não executa comandos por conta própria. Depois de falha de
deploy/push/upload/criação, confira o estado efetivo no destino antes de repetir:
a operação pode ter sido aplicada parcialmente.

Se o Codex já iniciou comandos, alterações ou ferramentas, uma falha de comunicação,
timeout ou falta de capacidade não reinicia a tarefa inteira em outro executor/modelo.
O histórico é preservado para conferir o estado real antes de continuar. O fallback
de inicialização permanece disponível quando nenhuma atividade foi iniciada.

No Codex app-server, a IDE registra notificações `item/completed` de
`commandExecution` com `exitCode` numérico, uma vez por item. Pedidos de aprovação
de comandos consultam a mesma proteção. Falhas estruturadas devolvem um diagnóstico
limitado ao turno ativo por `turn/steer`, quando o servidor oferece esse recurso.
Se ele recusar a atualização, a IDE conserva a memória sem reiniciar/repetir o
comando. Alterações de arquivos também são registradas. O executor CLI usa
`codex exec --json` para recolher resultados de comandos e alterações.

Ferramentas internas do Codex que não pedem aprovação não passam pelo interceptador
de lançamento da IDE: seus resultados são observados pelos eventos estruturados.
O controle de timeout/cancelamento de cada comando interno continua pertencendo ao
Codex. No fallback CLI não há injeção de diagnóstico durante o turno; o histórico
fica disponível à próxima chamada. Lançadores de aplicativos do sistema que não
fornecem PID/exit code não comprovam conclusão. Processos remotos ou iniciados por
terceiros não são controlados como processos locais da IDE.

A memória é limitada a 80 registros e 256 KB, com bloqueio entre processos para
evitar perda de atualizações concorrentes. Caminhos Windows com/sem prefixo
estendido são normalizados depois de resolver links, preservando a contenção no workspace.
Ela conserva evidências e estratégias, não treina os pesos do modelo nem garante
ausência de erros futuros. Remover o arquivo de memória limpa esse aprendizado
local. Esses arquivos são ignorados pelo Git desta IDE.

Os testes de cobertura de entrada de processos verificam que novos usos de
subprocess e novos construtores QProcess não contornem os pontos monitorados.
O formato JSONL do executor segue a [documentação do Codex](https://learn.chatgpt.com/docs/non-interactive-mode);
os parâmetros de steering foram conferidos no schema gerado pelo CLI instalado.

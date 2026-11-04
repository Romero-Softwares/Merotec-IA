# Descoberta e troca de modelos de IA

Abra **Configurações da IA** e selecione o perfil que deseja usar.

- **Codex local** consulta os modelos da sessão conectada pelo app-server, usando
  somente handshake e `model/list`. Não exige chave de API nem inicia conversa.
- **API compatível com OpenAI** consulta `GET /models` no endpoint do perfil com
  a chave informada naquele formulário. Não usa o login do ChatGPT como chave.

O catálogo é carregado automaticamente ao abrir ou alternar para esses perfis.
No perfil API, concluir a edição da chave/URL agenda uma nova consulta. O botão
**Atualizar modelos disponíveis** permite atualizar novamente. A consulta roda
em segundo plano; a janela continua editável e falhas não apagam o ID informado.

Os campos de modelo são listas editáveis com busca por trecho. Escolha o **ID
exato** retornado pelo serviço. O nome comercial ou rótulo pode ser diferente do
ID. Nomes exibidos que correspondam unicamente ao catálogo Codex são associados ao
ID fornecido pelo próprio catálogo; a alteração só é aplicada ao clicar em Salvar.
Um rótulo desconhecido com espaços não é aceito como ID Codex.

O modelo atual é preservado mesmo quando não aparece no catálogo, com aviso.
Isso permite manter configurações antigas ou IDs manuais, mas não garante que
eles ainda estejam disponíveis. Se a execução rejeitar o modelo, confira acesso,
perfil, endpoint e compatibilidade da modalidade. O catálogo não informa todas as
capacidades de cada modelo: um ID de áudio/imagem não serve necessariamente ao chat.

Para Codex, os níveis de raciocínio são atualizados a partir dos valores suportados
pelo modelo. A opção automática mostra **o ID exato descoberto**, por exemplo
`gpt-6.1-sol (automático)`, mantendo a seleção interna vazia para que o app-server
continue escolhendo o padrão real. O ID não é fixado silenciosamente.

O cabeçalho do chat e o campo **Modelo:** na barra inferior exibem o ID, independentemente
das mensagens temporárias de atividade. A IDE consulta o padrão em segundo plano ao
iniciar, aplicar configurações ou concluir login. Quando o serviço retorna um modelo
efetivo na execução, essa identidade tem prioridade, inclusive em fallback. As
atualizações visuais não fazem chamadas de rede. Se ainda não houver informação
confirmada, a IDE indica **modelo não confirmado**, em vez de adivinhar um ID.

Clique em **Salvar** para persistir o perfil e aplicá-lo às próximas solicitações.
A IDE recria o motor e mostra provedor/modelo aplicado na barra de status; não é
necessário reiniciar a aplicação a cada troca. Uma tarefa já iniciada não deve ser
interpretada como validação do novo modelo: confira uma nova solicitação.
Rascunhos de outros perfis também são preservados ao alternar e gravados ao salvar.
**Cancelar** descarta as edições do diálogo.

As sugestões de catálogo ficam apenas na memória do diálogo, separadas por
provedor, endpoint e credencial. Resultados atrasados de outra configuração são
ignorados. Não são gravadas cópias adicionais de chaves no catálogo, e erros de
descoberta não mostram o corpo do servidor nem o segredo. O perfil mantém o
armazenamento de credenciais que já existia na IDE.

## Execução e compatibilidade

A API oficial OpenAI usa Responses com o ID selecionado e conteúdo multimodal
convertido para seu formato. Parâmetros antigos `temperature` e `max_tokens` não
são enviados nesse fluxo, evitando rejeições em modelos de raciocínio. Respostas
incompletas não aplicam ações parciais. Endpoints customizados e OpenRouter mantêm
o fluxo Chat Completions já existente.

A descoberta não executa geração de texto paga e não comprova saldo/cota ou suporte
do modelo a determinado endpoint. A seleção conserva o ID informado; erros de
autorização/modelo orientam a atualizar a lista. Os fallbacks de capacidade Codex
preexistentes continuam sendo anunciados quando acionados.

Referências oficiais: [List models](https://developers.openai.com/api/reference/resources/models/methods/list)
e [Codex app-server: modelos](https://learn.chatgpt.com/docs/app-server#models).

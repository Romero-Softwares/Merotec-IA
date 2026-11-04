# Merotec PCB — CAD nativo em Python

Abra **Engenharia > Editor nativo de esquemático e PCB**. O editor é uma janela
independente dentro da IDE, feita em Python/PySide6; não usa o executável, API ou
bibliotecas do KiCad para criar, editar, salvar ou verificar projetos nativos.
Projetos novos do tipo `pcb` incluem `hardware/design.mpcb.json` vazio.

## Trabalhar no circuito

1. Escolha um footprint na biblioteca e clique em **Adicionar**. Informe o valor/modelo.
2. Abra **Redes e pinos** e atribua uma rede a cada terminal. Nomes iguais representam
   a mesma rede tanto no esquemático quanto na PCB. Marque terminais intencionalmente
   desconectados como `nc`, sem nome de rede.
3. Declare os tipos elétricos: `passive`, `input`, `output`, `power_in`, `power_out`
   ou `nc`. Os componentes genéricos começam como passivos; a IDE não deduz as
   funções de um CI pelo nome do modelo.
4. No esquemático, arraste os símbolos para organizar o diagrama. As linhas são
   representações automáticas das redes, e seus cruzamentos não criam conexões.
5. Alterne para **PCB — cobre superior**. Arraste footprints, selecione e pressione
   **R** para girar 90°. O posicionamento encaixa em uma grade de 0,5 mm.
6. Ative **Rotear**, clique no pad inicial, em pontos intermediários opcionais e
   no pad de destino. Os terminais precisam estar na mesma rede. **Esc** cancela
   a rota. As linhas tracejadas são guias de conexão, não cobre, e continuam visíveis
   mesmo depois de rotear.
7. Clique em **ERC/DRC** ou pressione **F6**. A análise ocorre em segundo plano e
   informa se o projeto mudou enquanto era calculada.
8. Salve em `.mpcb.json`. O arquivo contém símbolos, footprints, redes, posições,
   regras e rotas; pode ser versionado e inspecionado pela IA como JSON.

Use **Exemplo** para abrir uma demonstração de dois resistores em paralelo com duas
redes roteadas. Ela demonstra geometria e netlist; não é um dispositivo alimentado.
**Enquadrar** ajusta o zoom ao projeto. A roda do mouse controla o zoom manual.
Selecione e use **Delete** para excluir trilhas ou componentes. Excluir componente
remove suas rotas; **Ctrl+Z** desfaz e **Ctrl+Shift+Z** refaz. Ao mover um footprint,
as extremidades das rotas seguem os pads, mantendo os pontos intermediários.

## Regras locais implementadas

- ERC: terminais sem rede, rede com um só terminal, múltiplas saídas, entrada sem
  driver, alimentação sem fonte e terminais NC indevidamente conectados.
- DRC: conexão entre redes diferentes, trilhas abaixo da largura mínima, cobre
  fora da margem, folga entre pads retangulares rotacionados, trilhas e pads,
  cruzamento/folga entre trilhas e redes ainda não totalmente roteadas.
- Placa retangular, uma camada de cobre superior, pads SMD retangulares e trilhas
  com extremidades circulares. Configure dimensões, largura e folga em **Placa e regras**.
- Conectividade física representada por rotas pad-a-pad explícitas. Cruzamentos
  na mesma rede e contato geométrico incidental não acrescentam junções à netlist.

A biblioteca inicial contém resistor/capacitor 0603, LED 0805, conector SMD de dois
terminais e footprint genérico SOIC-8. Verifique dimensões e pinagem contra o datasheet
da peça real. Os símbolos são blocos genéricos com terminais, não símbolos normalizados.
O corpo desenhado é um marcador de seleção; o DRC considera o cobre dos pads e
trilhas, sem análise de colisão entre corpos/courtyards.

## Exportação e diagnóstico

**Exportar SVG/BOM** gera uma prévia vetorial da PCB ou BOM CSV. A exportação SVG
não é Gerber e não deve ser enviada à fabricação como se fosse um conjunto fabril.
O diagnóstico da bancada lê os `.mpcb.json` salvos e roda as mesmas regras nativas
sem KiCad. Arquivos KiCad existentes continuam usando a integração externa opcional;
o formato nativo não importa nem regrava `.kicad_pcb`/`.kicad_sch`.

Esta implementação não tem multicamadas, vias, furos, zonas de cobre, autorroteamento,
simulação SPICE, visualização 3D ou exportação Gerber/Excellon. Não replica toda a
abrangência do KiCad. Ausência de violações significa apenas que as regras acima
passaram; não comprova funcionamento elétrico ou fabricação correta. EMC, térmica,
isolamento, integridade de sinal, limiares lógicos e regras do fabricante exigem
análise e ensaios próprios.

O formato v1 aceita até 500 componentes, 2000 trilhas e 2000 segmentos de cobre,
com até 100 pontos intermediários por trilha. Os documentos têm limite de 5 MB.
O salvamento substitui o arquivo atomicamente e pergunta antes de descartar edições.

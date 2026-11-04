# Diagnóstico PCB e arquitetura eletrônica

Abra **Engenharia > Bancada eletrônica > PCB e arquitetura**. Execute o diagnóstico
local ou a opção com KiCad. A saída aparece no painel inferior da própria janela,
com botão **Interromper**. A bancada permanece aberta e carrega o relatório ao
concluir; **Carregar último relatório** também permite consultá-lo manualmente. O relatório mostra
data UTC e workspace; ele representa os arquivos salvos no momento da execução.
Execute novamente após alterar o projeto. A janela não salva buffers do editor.

Os arquivos `report.txt` e `report.json` ficam em
`.merotec_system_ai/pcb_diagnostics/` no workspace. Cada execução substitui o último
relatório; copie os resultados que precisar preservar antes de uma nova execução.

## Verificações

- BOM: colunas obrigatórias, referência única por linha, quantidade inteira positiva, valor e footprint preenchidos.
- Pinagem: alocação única, sinal, direção válida e tensão numérica em volts.
- Arquitetura: integridade das referências, tensão de alimentação dos blocos,
  soma das correntes declaradas por rail e limite máximo de tensão das entradas.
- Testes funcionais: comparação das medições registradas com limites inclusivos.
- KiCad: ERC por `.kicad_sch` e DRC por `.kicad_pcb` encontrados no workspace.

`pass` significa que a regra específica passou; `fail` indica inconsistência ou erro
da ferramenta; `pending` indica dados, ferramenta ou verificação ausentes. O estado
geral prioriza falhas, depois pendências. Uma pasta vazia nunca é aprovada.

## Arquitetura declarativa

Preencha `hardware/architecture.json` seguindo este exemplo:

```json
{
  "schema_version": 1,
  "rails": [
    {"id": "3V3", "voltage_v": 3.3, "max_current_ma": 200}
  ],
  "blocks": [
    {"id": "mcu", "rail": "3V3", "current_ma": 80,
     "supply_min_v": 3.0, "supply_max_v": 3.6,
     "logic_voltage_v": 3.3, "max_input_v": 3.6},
    {"id": "sensor", "rail": "3V3", "current_ma": 20,
     "supply_min_v": 3.0, "supply_max_v": 3.6,
     "logic_voltage_v": 3.3, "max_input_v": 3.6}
  ],
  "interfaces": [
    {"from": "mcu", "to": "sensor", "level_shifter": false},
    {"from": "sensor", "to": "mcu", "level_shifter": false}
  ]
}
```

As correntes são em mA e tensões em V. Cada interface é direcional; represente os
dois sentidos quando necessário. Um conversor de nível declarado gera pendência
para revisão; sua presença não comprova compatibilidade elétrica. Os limites e
correntes devem vir de requisitos/datasheets e incluir as margens de seu projeto.

O modelo não extrai a conectividade da PCB nem valida limiares VIH/VIL, transientes,
térmica, isolamento, EMC ou sincronismo. Ele verifica os dados declarados.

## Testes funcionais registrados

Preencha `tests/functional-tests.csv`:

```csv
Test,Requirement,Min,Max,Measured,Unit,Evidence
Rail 3V3,REQ-POWER-01,3.135,3.465,3.31,V,bench-log-2026-11-02
Resposta sensor,REQ-SENSOR-01,0,100,,ms,
```

Use ponto decimal e a mesma unidade em Min, Max e Measured. Limites negativos são
aceitos. Sem medição ou identificação de evidência, o teste fica pendente.
A IDE compara os números registrados; ela não coleta medições nem verifica a
autenticidade da evidência. O segundo teste do exemplo está pendente.

Para `hardware/bom.csv`, use `Reference,Quantity,Value,Footprint` no mínimo.
Para `hardware/pinout.csv`, use `Signal,MCU_Pin,Direction,Voltage` no mínimo.
Direções aceitas: `input`, `output`, `bidirectional`, `power`, `ground`.
Pinos compartilhados precisam ser revisados e representados sem alocação duplicada
nessa tabela; este diagnóstico conservador não modela multiplexação.

## KiCad e automação

Projetos `.mpcb.json` usam o [motor nativo Python](native-pcb.md), detectado
automaticamente no workspace e sem ferramentas externas. O diagnóstico executa
ERC/DRC nas redes e geometrias do documento salvo. As verificações de BOM,
arquitetura e medições permanecem separadas, e seus dados ausentes continuam pendentes.
KiCad é necessário apenas para a opção de verificar arquivos KiCad existentes.

Instale `kicad-cli` no PATH. A integração usa os comandos
[`pcb drc` e `sch erc`](https://docs.kicad.org/9.0/en/cli/cli.html)
com `--exit-code-violations` e relatórios separados, sem salvar a placa ou preencher
zonas. Versões sem essas opções são reportadas como falha, não como aprovação.
Cada arquivo tem limite de 120 segundos. O resultado depende das regras configuradas
no projeto KiCad; as regras de fabricação devem ser revisadas pelo projetista.
Projetos com folhas hierárquicas podem gerar relatórios também para subfolhas.

Na linha de comando, a partir da pasta da IDE:

```powershell
venv\Scripts\python.exe -m modules.pcb_diagnostics "C:\caminho\projeto"
venv\Scripts\python.exe -m modules.pcb_diagnostics "C:\caminho\projeto" --kicad
```

Códigos de saída: `0` passou todas as verificações executadas sem pendências,
`1` falha, `2` pendências sem falhas. Nenhuma opção energiza, programa ou aciona hardware.

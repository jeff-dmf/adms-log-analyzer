# UI-04: calibração de `tools/ui_perf.mjs` contra a linha de base da análise 3

Aceite da issue #144: reproduzir a linha de base da análise 3 (caso de cerca de 3,5 milhões de registros, CPU 4×) com diferença de até 20%.

## Condições da medição

- Comando: `node tools/ui_perf.mjs --large --cpu 4 --report-only`, sobre o código da branch `feat/ui-04-perf-guard` (base `main` em `cc2bea7`, que já inclui a UI-01).
- Caso: `tools/bench.py --size-mb 500 --adapters 8 --profile info` (mesmos parâmetros da análise 3), só dados sintéticos.
- Registros: **3.470.424**, lidos de `GET /api/cases` (soma de `records` do caso, que vem de `sum(n_entries)` das fontes). A análise 3 relata 3.470.424: o caso é o mesmo.
- Chrome 155.0.8059.39 (Linux), janela 1600×900, CPU 4× mais lenta (`Emulation.setCPUThrottlingRate`). Uma rodada; o caso (2 GB de disco temporário) foi removido ao final.

## Resultado: medido × linha de base

| Medida | Medido | Linha de base | Diferença |
|---|---|---|---|
| Primeira linha, abertura (ms) | 1277 | 1291 | -1% |
| Primeira linha, recarga (ms) | 1256 | 1200 | +5% |
| Requisições estáticas na abertura (estáticos + documento) | 92 | 92 | 0% |
| Requisições estáticas na recarga (todas completas, sem cache) | 92 | 92 | 0% |
| JavaScript na abertura (KB) | 891 | 912 | -2% |
| Módulos JS | 72 | 72 | 0% |
| Arquivos CSS | 19 | 19 | 0% |
| Chamada de API mais lenta (ms) | 592 | 650 | -9% |
| Bytes transferidos na abertura (KB) | 1136 | cerca de 1024 | +11% |
| Layouts forçados em 120 eventos de roda | 120 | 120 | 0% |
| INP clique / setas (ms) | 24 / 24 | ≤ 24 | 0% |
| INP filtro / busca (ms) | 16 / 16 | ≤ 24 | dentro |
| Filtro até as linhas (ms) | 16 | 24 | -33% |
| Busca até as linhas (ms) | 63 | 40 | +57% |
| CLS da abertura | 0,91 | 1,58 | -43% |
| Folhas de estilo injetadas depois da primeira pintura | 8 | 23 | ver abaixo |
| Trabalho por quadro na rolagem (ms) | 6,3 (máx. 9,2 em um quadro) | sem linha de base | n/d |

## Leitura

**Dentro de 20% (tolerância atendida):** primeira linha (abertura e recarga), requisições, JS, módulos, CSS, API mais lenta, layouts forçados, INP e filtro até as linhas. São as medidas que o aceite pede para reproduzir (abertura, requisições e rolagem).

**Fora de 20%, e por quê:**

- **CLS (0,91 contra 1,58).** O CLS varia entre rodadas na mesma versão: 0,89 a 1,62 em oito execuções da ferramenta neste código. A linha de base foi medida na `main` `8969d94`, antes da UI-01 (#172), que mexeu no layout. Não é erro da ferramenta; o orçamento (≤ 0,05) fica muito abaixo de qualquer valor observado, então a guarda decide igual.
- **Folhas injetadas depois da pintura (8 contra 23).** A análise contou os módulos que injetam o próprio `<link>`; a ferramenta conta os `<link rel=stylesheet>` inseridos por script depois do First Contentful Paint. São definições diferentes; a ferramenta mede o que o orçamento pede (zero injeções depois da pintura).
- **Busca até as linhas (63 contra 40 ms).** A ferramenta mede de Enter até a grade ficar 150 ms sem mudar; a análise mediu até as primeiras linhas. A diferença é de 23 ms e o orçamento é de 200 ms.
- **Bytes (+11%).** A ferramenta soma `encodedDataLength` de todas as respostas da abertura, incluindo as 11 chamadas de API; a linha de base de 1,0 MB citava só os estáticos. A coluna é informativa (sem orçamento).
- **Trabalho por quadro.** A análise 3 só publicou o custo de layout (cerca de 1,5 ms por evento a 4×), então não há linha de base comparável. A ferramenta mede a união de script, estilo e layout por quadro realmente desenhado (evento `DrawFrame`), com o trace ligado, o que o encarece.

## Diferença da ferramenta × melhoria posterior da UI

- **Diferença da ferramenta** é a distância entre o que ela mede e a linha de base no **mesmo código** (esta tabela). Ela deve ficar em até 20% para as medidas que a análise 3 mediu igual. Passa, com as exceções explicadas acima, que vêm de definição de medida ou de variação natural, não de erro.
- **Melhoria posterior da UI** é o movimento em relação ao orçamento depois de lotes como a UI-14 (#154) e a UI-27 (#167). Hoje a ferramenta sai com código 1 na `main` porque os orçamentos são metas ainda não atingidas (92 requisições contra 45, CLS, layouts forçados). Quando um lote reduzir esses números, a queda é ganho da UI e não deve ser lida como descalibração; só a mudança de definição de medida, registrada aqui, exige nova calibração.

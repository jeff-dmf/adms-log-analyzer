# Consolidado das análises de UI/UX do viewer (2026-10-14)

> **Issues no GitHub:** UI-01 = #141, UI-02 = #142, UI-03 = #143, UI-04 = #144, UI-05 = #145, UI-06 = #146, UI-07 = #147, UI-08 = #148, UI-09 = #149, UI-10 = #150, UI-11 = #151, UI-12 = #152, UI-13 = #153, UI-14 = #154, UI-15 = #155, UI-16 = #156, UI-17 = #157, UI-18 = #158, UI-19 = #159, UI-20 = #160, UI-21 = #161, UI-22 = #162, UI-23 = #163, UI-24 = #164, UI-25 = #165, UI-26 = #166, UI-27 = #167, UI-28 = #168, UI-29 = #169, UI-30 = #170.

Análise 4: compilação das três análises do viewer (`src/dmslog/web/`, `main` em `8969d94`) em uma lista única de recomendações, sem duplicatas, com prioridade, lote, dependências, arquivos e decisões. As issues saem de `docs/analises-ui/issues.json` (um objeto por recomendação; título `UI-nn`, com `UI-nn` = `REC-nn`).

| Análise | Documento | Achados |
|---|---|---|
| 1 (frontend-design + design) | `docs/REVISAO-UI-UX.md` e `docs/UI-HANDOFF.md` | P0-1 a P0-5, P1-1 a P1-13, P2-1 a P2-14; tabela de acessibilidade (§8 #1 a #11); tarefas UX-01 a UX-16 em lotes L0 a L8; decisões D1 a D8 |
| 2 (variantes locais; o Superdesign foi descartado por exigir login externo) | `docs/analises-ui/ANALISE-VARIANTES.md` | VR-01 a VR-11; variante R recomendada |
| 3 (plugin Vercel: desempenho e composição) | `docs/analises-ui/ANALISE-VERCEL.md` | VC-01 a VC-11; orçamentos de desempenho (§5) |

## 1. Resumo executivo

As três análises concordam no diagnóstico: o motor do viewer (grid virtual, keyset, filtros, conhecimento, correlação) é rápido e seguro (INP ≤ 24 ms com CPU 4× mais lenta e 3,5 milhões de registros), e o problema está na **arquitetura da tela** e no **caminho até a primeira linha**. Cada funcionalidade ganhou um botão e um painel empilhado acima do grid; em 1366×768 o grid mostra 5 linhas, com painel aberto o cabeçalho do grid encolhe para 9 px, a lista de fontes some e a mensagem fica sem largura. Na abertura, 92 requisições e uma consulta de 480 ms seguram a primeira linha em 1,2 s, e a tela pula (CLS 1,58).

Os 54 achados numerados (32 + 11 + 11), mais as 11 linhas de acessibilidade, as 16 tarefas e as 8 decisões da análise 1, viraram **30 recomendações** (4 P0, 20 P1, 6 P2). As maiores fusões: layout P0 (P0-2, P0-3, P0-4 e VC-02 b) numa correção só de CSS; seleção, destaque, linha anormal e alto contraste (P1-2, VR-06, VC-04); colunas e legibilidade da mensagem (VR-01, VR-08, P1-3); barra de status e erro global (P1-7, VR-04, P0-1 e); timeline (P1-4, VR-05, P2-10); abertura rápida (VC-07, VC-01 b e c).

Divergências decididas com dados: dimensões da variante R no lugar das do handoff (a análise 2 mediu 0 a 8 caracteres de mensagem em ≤ 1366 px com as medidas da análise 1); timeline de 40 px como faixa de alarme em escala própria (o pico de erro só aparece assim); erro global dentro da barra de status (o banner sobre a coluna cobre o filtro); trilho da lateral abaixo de 1500 px somado às gavetas da análise 1; seleção com `box-shadow` mais bloco `forced-colors` com `outline`. A altura de linha de 20 px (contra os 22 px da D4) fica como recomendação padrão, com decisão do Jefferson.

A ordem é a dos lotes L0 a L8 da análise 1, com três mudanças: a barra de status sobe de L8 para L3 (várias telas dependem dela), as colunas e o layout responsivo sobem de L6/L7 para L4 (são P0), e a seleção com alto contraste sobe de L6 para L1 (é barata e só depende dos tokens). Três frentes sem arquivo de front-end em comum (consulta de fontes, cache de estáticos, guarda de desempenho) correm em paralelo desde o L0. Integração sempre serial: um PR por vez, e `js/main.js`, `js/strings.js`, `js/api.js`, `index.html` e `docs/ARQUITETURA.md` com a sessão principal como dono único.

## 2. Verificação no código (antes de decidir)

Conferidos na worktree (`main` em `8969d94` + docs), caminhos relativos a `src/dmslog/`:

| Achado | Confirmação |
|---|---|
| D4 × VR-07 (altura da linha) | `web/js/grid-model.js:4` `ROW_HEIGHT = 22` e `web/css/app.css:24` `--row-height: 22px`, duplicados |
| P0-2 (painéis empurram o grid) | `insertBefore` em `web/js/distinct-panel.js:377`, `aggregate-panel.js:582`, `highlight-panel.js:138`, `views-stack.js:73`; `.host-panel { max-height: 40vh }` em `web/css/grid-extras.css:24` |
| P1-2 × VC-04 (seleção) | `web/css/app.css:383-385`: `.grid-row.selected { background: var(--selected); }` e nenhum `forced-colors` em `web/css/` |
| VR-01 (largura da mensagem) | `web/css/app.css:418-440`: horário 190, PID/TID 72, Level 84, Source 160 px |
| P1-1 × VC-02 (barra que quebra) | `web/css/app.css:144-150`: `#toolbar { flex-wrap: wrap }`; 23 módulos criam `<link rel="stylesheet">` (grep `"stylesheet"` em `web/js/`) |
| VC-03 (falha esvazia o grid) | `web/js/grid.js:253-271`: `reload()` faz `window.reset()` e `render()` antes de `jump()` |
| VC-09 (layout forçado) | `web/js/grid.js:430-431` escreve e relê `scrollTop`; `grid.js:491` lê `clientHeight` em `ensurePool` |
| VC-01 (consulta de fontes) | `server/api.py` (`list_sources`): subconsulta `ORDER BY e.id DESC LIMIT 1` por fonte |
| VC-08 (sem cache) | `server/app.py:125`: `Cache-Control: no-store` em `_send` |
| P1-11 (ARIA do grid) | `web/js/grid.js:537` `aria-label` por linha; `grid.js:519` `aria-rowcount` = tamanho da janela |
| VC-05 (aba oculta) | nenhum `visibilitychange` em `web/js/` |

Uma inconsistência interna da análise 1, sem efeito no consolidado: P0-1 (d) cita "P1-13" para a ajuda de atalhos, que é a P1-12, e as dependências do §12.1 citam "UX-13 (P1-13, painel de mesma chave)", mas UX-13 é a tarefa de teclado. Aqui P1-12 → UI-28 e P1-13 → UI-24.

## 3. Rastreabilidade (cada ID de origem → recomendação)

### 3.1 Análise 1: achados

| ID | Título | Prioridade | Recomendação |
|---|---|---|---|
| P0-1 | Grid com uma fração da altura | P0 | (a) UI-12; (b) UI-19; (c) UI-18; (d) UI-28; (e) UI-13; mínimo de linhas UI-01/UI-15; aceite final de densidade UI-19 |
| P0-2 | Painéis empurram o grid; cabeçalho com 9 px | P0 | correção UI-01; doca UI-15; `dock.register` UI-11 |
| P0-3 | Lista de fontes inalcançável | P0 | correção UI-01; lateral UI-21 |
| P0-4 | Texto do registro some no detalhe | P0 | UI-01 (regras); UI-20 (ordem) |
| P0-5 | Sobreposição em janela estreita e zoom | P0 | UI-17 |
| P1-1 | 22 botões sem estado ativo | P1 | UI-12; estilo de estado UI-10; contrato UI-11 |
| P1-2 | Seleção some sob destaques e Collection | P1 | UI-07 |
| P1-3 | Coluna Source esconde o que diferencia | P1 | UI-16 |
| P1-4 | Timeline destaca o normal | P1 | UI-19 |
| P1-5 | Não se vê o filtro ativo | P1 | UI-18; contrato UI-11 |
| P1-6 | Detalhe fora da ordem de investigação | P1 | UI-20 |
| P1-7 | Mensagens deslocam o layout | P1 | UI-13; contrato UI-11 |
| P1-8 | Três caminhos para abrir arquivos | P1 | UI-22 |
| P1-9 | Painéis sem padrão | P1 | componente UI-10; migração UI-23 |
| P1-10 | Contraste abaixo de AA | P1 | UI-05 |
| P1-11 | Leitor de tela não ouve o grid | P1 | UI-26 |
| P1-12 | Teclado: paradas, atalhos invisíveis | P1 | UI-28 |
| P1-13 | Seguir um GUID | P1 | UI-24; entrada no menu de contexto UI-29 |
| P2-1 | 11 tamanhos de fonte, 6 raios, `z-index` soltos | P2 | UI-05; UI-10 |
| P2-2 | Duas pilhas mono | P2 | UI-05 |
| P2-3 | Tokens repetidos, 7 paletas | P2 | UI-05 |
| P2-4 | Sem caso: nada orienta | P2 | UI-22 |
| P2-5 | Navegador de pastas corta o fim do nome | P2 | UI-22 |
| P2-6 | Compare: cabeçalho e diff abaixo da dobra | P2 | UI-23 |
| P2-7 | "Remove collection" com o mesmo peso | P2 | UI-21 |
| P2-8 | Botão de tema ambíguo | P2 | UI-12 |
| P2-9 | Payload sem quebra de linha | P2 | UI-20 |
| P2-10 | Dica fixa na timeline | P2 | UI-19; UI-28 (diálogo `?`) |
| P2-11 | Meta da fonte sem PID | P2 | UI-21 |
| P2-12 | Fim do progresso ": 235,230 records" | P2 | UI-21 (comportamento); UI-09 (texto) |
| P2-13 | "Noise or worse"; Level ao lado de Severity | P2 | UI-09 (rótulos); UI-18 (Level em Advanced) |
| P2-14 | Operations: rótulo sem campo | P2 | UI-23 |

### 3.2 Análise 1: acessibilidade, seções, tarefas e decisões

| Origem | Recomendação |
|---|---|
| §8 #1 (1.4.3 contraste) | UI-05 |
| §8 #2 (1.4.11 bordas) | UI-05 |
| §8 #3 (1.4.10, 1.4.4 reflow) | UI-17 |
| §8 #4 (4.1.2 linhas do grid) | UI-26 |
| §8 #5 (2.1.4 atalhos de letra) | UI-28 |
| §8 #6 (2.4.1 skip link) | UI-28 |
| §8 #7 (1.4.1 estado só por cor) | UI-07 (seleção), UI-10 (pressed), UI-12 (barra) |
| §8 #8 (4.1.3 live a cada rolagem) | UI-13, UI-26 |
| §8 #9 (3.3.1 erro longe do campo) | UI-13 |
| §8 #10 (2.4.7 foco sobre a seleção) | UI-05 (anel duplo), UI-07 |
| §8 #11 (2.5.8 alvos) | UI-10 |
| §6 fluxos | UI-22 (abrir), UI-24 (GUID), UI-21 (coletas), UI-23 (exportar) |
| §7 descobribilidade | UI-29 |
| §9 auditoria do sistema de design | UI-05, UI-10 |
| §10 textos | UI-09 |
| §14 sem evidência (desempenho, NVDA, Edge) | UI-04 (desempenho), UI-26 (NVDA), riscos (Edge e fontes) |
| UX-01 · UX-02 · UX-03 · UX-04 | UI-01 · UI-05 · UI-05 (teste haiku) · UI-09 |
| UX-05 · UX-06 · UX-07 · UX-08 | UI-10 · UI-12 · UI-11 (desenho) + UI-15 · UI-18 |
| UX-09 · UX-10 · UX-11 · UX-12 | UI-19 · UI-20 · UI-21 · UI-07 + UI-16 + UI-26 + UI-27 |
| UX-13 · UX-14 · UX-15 · UX-16 | UI-28 · UI-17 · UI-13 · UI-12 (P2-8), UI-19 (P2-10), UI-20 (P2-9), UI-21 (P2-7, P2-11, P2-12), UI-22 (P2-4, P2-5), UI-23 (P2-6, P2-14) |
| D1 a D8 | Seção 7 |

### 3.3 Análise 2 (variantes)

| ID | Título | Prioridade | Recomendação |
|---|---|---|---|
| VR-01 | Mensagem sem largura em notebook | P0 | colunas UI-16; trilho e breakpoints UI-17; dimensões UI-05 |
| VR-02 | Doca esconde a linha selecionada | P1 | UI-15 |
| VR-03 | Estado "Filtered" e fichas que não cabem | P1 | UI-18 |
| VR-04 | Banner de erro cobre o filtro | P1 | UI-13; estado Disconnected UI-25 |
| VR-05 | Timeline como faixa de alarme | P1 | UI-19 |
| VR-06 | Linha anormal tingida; selo só no detalhe | P2 | UI-07; selo no detalhe UI-20 |
| VR-07 | Linha de 20 px | P2 | UI-08 (D4) |
| VR-08 | Larguras padrão em `ch` | P2 | UI-16 |
| VR-09 | Altura inicial da doca pelo conteúdo | P2 | UI-15 |
| VR-10 | Arquivo ilegível marcado na fonte | P2 | UI-21 |
| VR-11 | Estado vazio sem lateral | P2 | UI-22 |
| Variante R (§6) | Tokens e regras | | UI-05 (tokens), UI-07, UI-15, UI-16, UI-17, UI-18, UI-19 |

### 3.4 Análise 3 (Vercel)

| ID | Título | Prioridade | Recomendação |
|---|---|---|---|
| VC-01 | Cascata de 4 viagens e consulta de 480 ms | P1 | (a) UI-02; (b, c) UI-14 |
| VC-02 | CLS 1,58 na abertura | P1 | (a) UI-12; (b) UI-01 e UI-13; (c) UI-06; orçamento UI-14 |
| VC-03 | Falha apaga o que se lia | P1 | UI-25 |
| VC-04 | Seleção some em alto contraste | P1 | UI-07 |
| VC-05 | Follow tail reconstrói a lista e consulta com aba oculta | P1 | (a, b) UI-21; (c, d) UI-30 |
| VC-06 | Follow tail não vai ao fim; contagens divergentes | P2 | UI-30 |
| VC-07 | 71 módulos na abertura | P2 | UI-14 |
| VC-08 | Estáticos sem cache | P2 | UI-03 |
| VC-09 | Layout forçado por evento de roda | P2 | UI-27 |
| VC-10 | Paleta de comandos | P2 | UI-29; registro de ações UI-11 |
| VC-11 | Orçamento sem guarda | P2 | UI-04 |

## 4. Recomendações

### 4.0 Aceite comum a todas

`node --test tests/web/` e `pytest` verdes no CI em Linux e Windows; nenhum `innerHTML` novo (`tests/unit/test_conventions.py`); todo texto visível em `js/strings.js`; capturas sintéticas antes/depois dos estados tocados, claro e escuro (nada de `local-samples/`); depois da UI-04, `tools/ui_perf.mjs` sem regressão de rolagem nem de abertura. Restrições do app valem para todas: CSS e JS puros, sem build, sem CDN, sem dependência nova; conteúdo de log só por `textContent`; tema por variáveis CSS; nada mede texto nem força layout durante a rolagem do grid.

Prioridade final: P0 = UI-01, UI-15, UI-16, UI-17. P1 = UI-02, UI-05, UI-06, UI-07, UI-10 a UI-14, UI-18 a UI-26, UI-28, UI-30. P2 = UI-03, UI-04, UI-08, UI-09, UI-27, UI-29.

### REC-01 · UI-01 Corrigir o layout que esmaga grid, detalhe e lista de fontes

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P0 | L0 | S | sonnet | nenhuma |

**Origens:** P0-2 (correção imediata), P0-3 (correção imediata), P0-4, VC-02 (b), UX-01.

**Problema.** Todos os filhos de `#grid-panel` têm `flex-shrink: 1`: com qualquer painel aberto (`.host-panel`, `max-height: 40vh` em `css/grid-extras.css:24`) o `#grid-header` cai para 9 px e o grid para 5 linhas. `#sources-panel` não rola: `#source-list` fica com altura 0 e a página inteira rola, levando a barra junto. Num registro com payload, `#detail-text` encolhe para 18 px. A timeline e a trilha de visões aparecem depois da primeira pintura e deslocam o grid (parte do CLS de 1,58).

**Proposta.**

Só CSS (e um marcador estático no HTML):
- `.grid-header, .filter-bar, #views-stack-bar, .timeline-host, .grid-status { flex: none; }` e `.grid-viewport { flex: 1 1 auto; min-height: calc(var(--row-height) * 8); }`.
- Painéis atuais cedem antes do grid: `.host-panel { flex: 0 1 40vh; min-height: 120px; overflow: auto; }`.
- `#sources-panel { overflow: auto; }` e `#source-list { flex: 1 0 240px; overflow: auto; }`.
- `#detail-panel > * { flex: none; }` e `#detail-text { min-height: 6lh; max-height: 45vh; resize: vertical; overflow: auto; }`.
- A página nunca rola: `html, body { height: 100%; overflow: hidden; }` e `#app { height: 100dvh; }`; só regiões internas rolam.
- Reservar altura desde a primeira pintura: `min-height` da timeline igual à altura final e um marcador vazio da trilha de visões no `index.html` com a altura da faixa (o banner de relógio sai do fluxo na UI-13).

**Aceite.**

- Com Distinct values aberto em 1600×900: `#grid-header` = 22 px e grid com ≥ 8 linhas (captura 07 depois).
- Registro com payload: `#detail-text` com ≥ 6 linhas visíveis (captura 20 depois).
- Caso com 2 coletas: `#source-list` com ≥ 240 px e `document.scrollingElement.scrollHeight === innerHeight` (captura 27 depois).
- CLS da abertura menor que 1,58, sem deslocamento causado pela timeline nem pela trilha (trace); a meta final ≤ 0,05 fecha na UI-14.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `css/app.css`; `css/grid-extras.css`.

**Pontos compartilhados** (sessão principal): index.html (marcador da trilha de visões).

### REC-02 · UI-02 Tornar /api/sources independente do volume de registros

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L0 (servidor, paralelo) | M | opus | nenhuma |

**Origens:** VC-01 (a).

**Problema.** `list_sources` (`src/dmslog/server/api.py`, perto da linha 1050) calcula a última linha de cada fonte com `SELECT e.line_no + e.nlines - 1 FROM entry e WHERE e.source_id = s.id ORDER BY e.id DESC LIMIT 1`, que usa `ix_entry_tid` e uma B-tree temporária: lê todos os registros de cada fonte. São 480 ms com 3,47 milhões de registros, crescendo de forma linear; o grid, que tem a página pronta em 3 ms, espera por essa resposta, e a Follow tail repete a consulta a cada ~6 s.

**Proposta.**

Em dois passos, no mesmo PR:
1. Paliativo (sonnet): `(SELECT e.line_no + e.nlines - 1 FROM entry e WHERE e.id = (SELECT max(x.id) FROM entry x WHERE x.source_id = s.id))`, medido em 80 ms; conferir no `EXPLAIN QUERY PLAN` que não há B-tree temporária.
2. Definitivo (opus define esquema e migração): coluna `source.last_line INTEGER` gravada ao fim de cada lote de indexação (inclusive incremental e `--watch`); casos antigos preenchidos uma vez na abertura com a consulta do passo 1 (ou `NULL` com fallback), sem reindexar. Registro do esquema e da mudança em `docs/ARQUITETURA.md` pela sessão principal.

**Divergência decidida.** Só a análise 3 mediu. O paliativo sozinho (80 ms) não atinge o orçamento de 50 ms; por isso a coluna nova.

**Aceite.**

- `/api/sources` ≤ 50 ms no caso de 3,5 milhões (`tools/bench.py --size-mb 500 --adapters 8 --profile info`).
- Resultado idêntico ao atual: teste unitário com fontes com continuação de linhas (`nlines > 1`), com rotação e vazias.
- Caso indexado antes da mudança abre sem reindexar e com o mesmo `last_line`.
- `pytest` verde em Linux e Windows.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `src/dmslog/server/api.py`; `src/dmslog/store/` (esquema, indexação, migração); `tests/unit/`.

**Pontos compartilhados** (sessão principal): docs/ARQUITETURA.md (esquema e seção 15).

### REC-03 · UI-03 Cache dos arquivos estáticos com ETag

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P2 | L0 (servidor, paralelo) | S | sonnet | nenhuma |

**Origens:** VC-08.

**Problema.** `_send` aplica `Cache-Control: no-store` a todas as respostas (`src/dmslog/server/app.py:125`), inclusive aos estáticos: cada F5 baixa 92 arquivos e 1,0 MB, e no `.pyz` cada arquivo custa 1,8 ms para sair do zip.

**Proposta.**

- Só em `_serve_static`: `ETag` forte (hash do conteúdo) calculado uma vez por arquivo e guardado em memória junto dos bytes (o app tem ~1 MB de estáticos); `Cache-Control: no-cache`; resposta `304` sem corpo quando `If-None-Match` casa (aceitar lista e `*`).
- API e SSE continuam com `no-store` (dados de log).
- Não usar URL versionada com `immutable`: uma URL sem `?v=` ao lado de uma versionada instancia o módulo duas vezes.

**Aceite.**

- F5 com todas as respostas estáticas em `304` sem corpo (captura do Network).
- Teste unitário do servidor: `If-None-Match` igual, diferente, lista e `*`; API continua `no-store`.
- `pytest` verde em Linux e Windows.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `src/dmslog/server/app.py`; `tests/unit/` (servidor).

**Pontos compartilhados** (sessão principal): docs/ARQUITETURA.md (rever a regra "nada em cache": vale para dados, não para o código do app).

**Decisão do Jefferson:** D9 (nova): permitir cache do código estático no perfil do navegador. Recomendação: sim; o cache não contém dado de log. Se não: a UI-03 sai e o orçamento de recarga da UI-14 fica sem efeito.

### REC-04 · UI-04 Guarda local de desempenho da interface

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P2 | L0 (ferramenta, paralelo) | M | sonnet | nenhuma |

**Origens:** VC-11, REVISAO-UI-UX §14 (desempenho com milhões de linhas não medido), UI-HANDOFF §10 (perfil de rolagem).

**Problema.** Nenhum teste mede abertura, CLS, requisições ou rolagem; os números das análises vieram de scripts descartáveis. Sem guarda, as UI-14 e UI-27 não têm como provar o aceite e qualquer lote pode regredir a rolagem.

**Proposta.**

`tools/ui_perf.mjs` (Node ≥ 22, sem dependência; `WebSocket` nativo e o Chrome ou Edge instalado, perfil temporário próprio apagado no fim):
- sobe `dmslog serve` num caso gerado por `tools/bench.py` (`--quick` ou o caso grande) e mede: primeira linha do grid, número de requisições e bytes na abertura e na recarga, CLS da abertura, layouts forçados e trabalho por quadro em 120 eventos de roda, INP de clique, setas, filtro e busca; opcionalmente CPU 4× mais lenta;
- compara com `tools/ui_perf_budgets.json` (tabela de orçamentos da análise 3, seção 5) e sai com código ≠ 0 quando estoura;
- passo manual antes de cada release em `docs/RELEASE.md`; job de CI só em Linux, sem bloquear merge, fica para depois.

**Aceite.**

- Roda em Linux com `bench.py --quick` e imprime todas as medidas numa tabela e em JSON.
- Reproduz a linha de base da análise 3 (caso de 3,5 milhões, 4×) com diferença ≤ 20%.
- Não deixa processo nem pasta temporária para trás; usa só dados sintéticos.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `tools/ui_perf.mjs` (novo); `tools/ui_perf_budgets.json` (novo); `docs/RELEASE.md`.

**Pontos compartilhados** (sessão principal): nenhum.

### REC-05 · UI-05 Arquivo único de tokens com paleta AA

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L1 | M | sonnet | UI-01 |

**Origens:** P1-10, P2-1, P2-2, P2-3, REVISAO-UI-UX §8 #1, #2, #10, REVISAO-UI-UX §9, ANALISE-VARIANTES §6.1 (tokens de R), UX-02, UX-03, D3, D5.

**Problema.** Noise/VERBOSE, Warning e Critical ficam abaixo de 4,5:1 sobre seleção e hover; bordas de campo têm 1,48:1; há 11 tamanhos de fonte (com `em` que acumula), 6 raios, 25 `padding`, duas pilhas mono, tokens repetidos 3 vezes por arquivo e 7 paletas paralelas.

**Proposta.**

- `css/tokens.css`, primeiro `<link>` do `index.html`, com primitivos e semânticos da seção 2 do `UI-HANDOFF.md` (cores, `--font-ui`, `--font-mono`, 4 tamanhos em px, espaço 2/4/8/12/16/24, raios 3/4/6, camadas `--z-*`, `--shadow-overlay`, movimento 120 ms com `prefers-reduced-motion`).
- Dimensões da variante R no lugar das do handoff: `--bar-height: 36px`, `--strip-height: 30px`, `--status-height: 22px`, `--control-height: 26px` (fichas e botões de linha 24 px), `--grid-head: 22px`, `--timeline-h: 40px` (expandida 120), `--side-w: 248px`, `--detail-w: 380px`, `--cell-pad: 4px`.
- Tokens novos de R: `--abn-row` (`#f8eceb` / `#2a1a1a`), `--filtered-bg`/`--filtered-ink` (`#e3ecfa`/`#163f78`; `#17304f`/`#b8d4ff`), `--chart-warning` (`#9a6a00` / `#d9a23a`), `--chart-normal` (`#848fa0` / `#6f7b8c`).
- Info sem cor (`--sev-info: var(--ink)`, `--sev-noise: var(--ink-muted)`), conforme D5.
- Paleta categórica única `--cat-1..8` com `-bg`/`-fg`; `--tl-c*`, `--agg-c*`, `--hl-*`, `--bm-*`, `--col-*` viram apelidos (os módulos migram quando cada UI tocar neles).
- Anel de foco duplo: `outline: 2px solid var(--focus-ring); outline-offset: 1px; box-shadow: 0 0 0 3px var(--surface-panel)`.
- `--row-height` continua 22 px aqui (a mudança é a UI-08). `app.css` migra para os tokens.
- Tarefa paralela (haiku, arquivo disjunto): `tests/web/tokens-contrast.test.mjs`, no padrão de `highlight-contrast.test.mjs`.

**Divergência decidida.** A análise 1 propôs barra 40, faixa 32, status 24 e controle 28 px; a análise 2 mediu que essas medidas, somadas às colunas, deixam a mensagem sem largura em ≤ 1366 px (VR-01). Decidido pelos números de R.

**Aceite.**

- Teste de tokens: texto ≥ 4,5:1 (`--ink`, `--ink-muted`, `--sev-*` sobre `--surface-panel`, `--row-selected`, `--row-hover`, `--abn-row` e `--cat-n-bg`); ≥ 3:1 para `--line-strong`, barra de seleção sobre `--row-selected` e `--chart-*` sobre o painel; claro e escuro.
- O mesmo teste falha se os dois blocos escuros (`@media` e `[data-theme="dark"]`) divergirem ou se `--row-height` ≠ `ROW_HEIGHT` de `js/grid-model.js`.
- Margem no par mais justo: Critical sobre seleção no escuro ≥ 4,7:1 (R mediu 4,92).
- `highlight-contrast.test.mjs` continua verde; nenhuma cor literal em `app.css` fora de `tokens.css` (grep).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `css/tokens.css` (novo); `css/app.css`; `tests/web/tokens-contrast.test.mjs` (novo, haiku).

**Pontos compartilhados** (sessão principal): index.html (link do tokens.css).

**Decisão do Jefferson:** D3 (fonte: pilhas do sistema) e D5 (Info neutra).

### REC-06 · UI-06 Carregar todas as folhas de estilo pelo HTML

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L1 | S | sonnet | UI-05 |

**Origens:** VC-02 (c).

**Problema.** 23 módulos injetam o próprio `<link rel="stylesheet">` depois de carregar (por exemplo `timeline.js`, `presets.js`, `clock-banner.js`), mudando estilos depois da pintura: parte do CLS de 1,58 e mais viagens na abertura.

**Proposta.**

- Remover a injeção dos 23 módulos (`alerts-panel`, `clock-banner`, `bookmarks-panel`, `aggregate-panel`, `compare`, `copy-export`, `config-panel`, `dashboard`, `distinct-panel`, `events-panel`, `export-panel`, `highlight-panel`, `key-panel`, `links`, `knowledge`, `payload-list`, `payload`, `presets`, `redaction-panel`, `unknown-panel`, `side-by-side`, `views-stack`, `timeline`).
- O `index.html` lista as folhas na ordem `tokens.css` → `components.css` (quando existir) → `app.css` → módulos.
- Teste de convenção que falha se algum `js/*.js` criar `link` de stylesheet.

**Divergência decidida.** O orçamento da análise 3 (≤ 12 arquivos CSS) não é meta aqui: só zero injeções. A contagem cai à medida que as UI-10 e UI-23 consolidarem estilos de painel.

**Aceite.**

- 0 folhas de estilo criadas por JS (teste de convenção).
- Trace da abertura sem recálculo de estilo provocado por injeção de CSS.
- Testes de fluxo que dependiam da injeção ajustados e verdes.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): os 23 módulos listados (só a função de injeção); `tests/unit/test_conventions.py` ou teste node equivalente.

**Pontos compartilhados** (sessão principal): index.html (lista de links).

### REC-07 · UI-07 Seleção sempre visível, destaques em faixa e alto contraste do Windows

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L1 | S | sonnet | UI-05 |

**Origens:** P1-2, VR-06, VC-04, REVISAO-UI-UX §8 #7, #10, UI-HANDOFF §4.1.

**Problema.** A seleção é só `background: var(--selected)` (`css/app.css:383-385`): some sob destaque amarelo, confunde-se com a célula azul da coluna Collection e desaparece no modo de alto contraste do Windows (nenhum `@media (forced-colors: active)` no CSS). As linhas Error/Critical não se distinguem ao rolar.

**Proposta.**

Prioridade dos fundos: seleção > destaque do usuário > linha anormal > hover.
```css
.grid-row:hover:not(.selected) { background: var(--row-hover); }
.grid-row[data-sev="1"], .grid-row[data-sev="2"] { background: var(--abn-row); }
.grid-row.<destaque> { background: var(--hl-bg); box-shadow: inset 4px 0 0 var(--hl-bar); }
.grid-row.selected { background: var(--row-selected); box-shadow: inset 3px 0 0 var(--accent); }
.grid-row.selected.<destaque> { box-shadow: inset 3px 0 0 var(--accent), inset 7px 0 0 var(--hl-bar); }
.grid-viewport:focus-visible .grid-row.selected { outline: 1px solid var(--focus-ring); outline-offset: -1px; }
@media (forced-colors: active) {
  .grid-row.selected { forced-color-adjust: none; background: Highlight; color: HighlightText;
                       outline: 2px solid Highlight; outline-offset: -2px; }
  [aria-pressed="true"], [aria-expanded="true"] { border: 2px solid ButtonText; }
}
```
- Severidade como cor do texto de Level e Message; Critical e Error com peso 600 na coluna Level (sinal não cromático, vale também no modo forçado).
- Coluna Collection: célula sem fundo, etiqueta `C1` com ponto `--cat-n-fg` (variável CSS posta por `style.setProperty`, sem `innerHTML`).
- Bookmark: ★ na margem (14 px), sem mexer no fundo.

**Divergência decidida.** A análise 1 usa barra em `box-shadow`; a análise 3 mostrou que `box-shadow` some no modo forçado. Decisão: `box-shadow` no modo normal e `outline` + `Highlight` dentro de `@media (forced-colors: active)`. O selo cheio de alarme da variante C fica só no cabeçalho do detalhe (UI-20).

**Aceite.**

- Captura 21 depois: linha selecionada com destaque continua inconfundível (barra de 3 px visível).
- Emulação `forced-colors: active` (claro e escuro) mostra a linha selecionada (captura antes/depois); conferir no Edge do Windows no UAT.
- Pares do teste de tokens cobrem `--abn-row`, seleção e destaque (≥ 4,5:1 para texto).
- Nenhuma mudança de largura ou layout na rolagem (só `box-shadow` e `outline`).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `css/app.css` (estados da linha); `css/highlight.css`; `css/bookmarks.css`; `css/collections.css`; `js/grid.js` (célula Collection).

**Pontos compartilhados** (sessão principal): nenhum.

**Decisão do Jefferson:** D5 (Info neutra; só o anormal tem cor e fundo).

### REC-08 · UI-08 Linha do grid com 20 px

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P2 | L1 | S | sonnet | UI-05 |

**Origens:** VR-07, D4, REVISAO-UI-UX §9 (ROW_HEIGHT duplicado).

**Problema.** Com 22 px, em 1366×768 com a doca aberta o layout proposto mostra 13 linhas; com 20 px, 16 (medido nos protótipos). `ROW_HEIGHT = 22` está duplicado em `js/grid-model.js:4` e em `--row-height` (`css/app.css:24`).

**Proposta.**

`--row-height: 20px` e `ROW_HEIGHT = 20` em `js/grid-model.js` (o teste da UI-05 confere os dois); `line-height` 20 px com fonte mono de 12 px; cabeçalho do grid continua 22 px (`--grid-head`). Densidade fixa, sem alternância Compact/Comfortable.

**Divergência decidida.** Divergência entre a análise 1 (D4: manter 22 px) e a análise 2 (VR-07: 20 px). Decidido por 20 px com os números de VR-07 (+3 a +6 linhas sobre 22 px); 18 px (variante B) foi descartado porque deixa 3 px de entrelinha e enfraquece a severidade. A palavra final é do Jefferson.

**Aceite.**

- `grid-model.test.mjs` e testes de rolagem verdes com o novo valor.
- Em 1366×768 sem painel, +9% de linhas sobre o estado anterior ao PR (mesma captura, mesmo caso).
- Se o Jefferson escolher 22 px (D4), a issue é fechada como não planejada.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `css/tokens.css`; `js/grid-model.js`; `tests/web/grid-model.test.mjs`.

**Pontos compartilhados** (sessão principal): nenhum.

**Decisão do Jefferson:** D4 (densidade). Recomendação revista: 20 px fixos.

### REC-09 · UI-09 Textos: terminologia única, plural, reticências e códigos traduzidos

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P2 | L1 | M | haiku | nenhuma |

**Origens:** REVISAO-UI-UX §10, UI-HANDOFF §7, P2-12, P2-13 (rótulos), UX-04, D2.

**Problema.** "entry" (24) e "record" (53) misturados; "template" (20) e "message type" (9); "(s)" em 17 textos ("1 records"); "..." (30) e "…" (7); códigos internos crus (`no_operation_for_key`); progresso que termina em ": 235,230 records"; "Noise or worse".

**Proposta.**

- Haiku gera `docs/notas/ux-copy-inventario.md`: cada chave de `js/strings.js` com texto atual e proposto, pelas regras do handoff (seção 7) e da tabela de reescritas da análise 1.
- Termos: **record**, **message type**, **operations**, **collection**, **source**; reticências "…"; botões que abrem diálogo terminam em "…"; severidade "At least {name}"; fim de indexação "Indexed {n} records."; cada código interno com frase própria.
- Plural por duas chaves (`fooOne`/`fooOther`) e um auxiliar em `strings.js` que escolhe pelo `n`.
- A sessão principal aplica no `strings.js` e ajusta os testes; as UI seguintes já usam os termos novos.

**Aceite.**

- Inventário cobre 100% das chaves com texto visível (contagem conferida contra `strings.js`).
- Depois de aplicado: 0 ocorrências de "(s)", "...", "entry/entries" e "template" em valores visíveis (grep; exceções só para textos literais de log).
- Teste do auxiliar de plural (0, 1, 2).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `docs/notas/ux-copy-inventario.md` (novo, haiku); testes que conferem textos.

**Pontos compartilhados** (sessão principal): js/strings.js (aplicação pela sessão principal).

**Decisão do Jefferson:** D2 (termo da linha do log): recomendação mantida, **record**.

### REC-10 · UI-10 Componentes comuns de interface

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L2 | M | sonnet | UI-05 |

**Origens:** P1-9 (componente), P1-1 (estilo de estado ativo), P2-1, REVISAO-UI-UX §8 #7, #11, UI-HANDOFF §5, UX-05.

**Problema.** 16 módulos repetem cabeçalho, status, vazio e erro com estilos próprios; 10 variações de selo e ficha; `aria-expanded="true"` sem estilo; botão primário desabilitado com opacidade (2,05:1); fichas de 20 px.

**Proposta.**

`css/components.css` (prefixo `ui-`) e `js/ui-menu.js`:
- `.ui-button` secundário, `.is-primary`, `.is-ghost`, `.is-danger`; `[aria-pressed="true"]` e `[aria-expanded="true"]` com fundo `--row-selected` e borda `--accent`; foco `outline 2px` + offset 1; desabilitado sem `opacity` (`--ink-disabled`, borda `--line`, `cursor: not-allowed`); carregando com `aria-busy`.
- `.ui-input`/`.ui-select` com `aria-invalid` e `.ui-field-error` ligado por `aria-describedby`.
- `.ui-badge` (`.is-sensitive`, `.is-curated`, `.is-estimate`) e `.ui-chip` (24 px de altura, alvo ≥ 24×24, remover com `aria-label` "Remove {label}").
- `.ui-panel` (head/tools/body/foot), `.ui-table` (cabeçalho fixo, números à direita com `tabular-nums`, `min-width` nas colunas de texto), `.ui-status`, `.ui-empty`, `.ui-error`, `.ui-estimate` (barra fina depois de 400 ms; sem animação com `prefers-reduced-motion`).
- `.ui-menu` com comportamento em `js/ui-menu.js` (setas, Home/End, letra inicial, Enter/Espaço, Esc devolve o foco) e `.ui-dialog` sobre `<dialog>` nativo.
- Nenhum módulo migra aqui; cada UI migra os seus.

**Aceite.**

- `tests/web/ui-menu.test.mjs`: setas, Home/End, letra inicial, Esc com retorno de foco.
- Página de demonstração sintética (só em teste ou captura) com hover, foco, desabilitado e pressed, claro e escuro; bordas e estados ≥ 3:1 no teste de tokens.
- Alvos interativos ≥ 24×24 px.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `css/components.css` (novo); `js/ui-menu.js` (novo); `tests/web/ui-menu.test.mjs` (novo).

**Pontos compartilhados** (sessão principal): index.html (link).

### REC-11 · UI-11 Contratos de interface: ações, doca, barra de status e fichas de filtro

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L2 (paralelo à UI-10) | M | opus | nenhuma |

**Origens:** P0-2 (`dock.register`), VC-10 (registro único de ações), P1-1, P1-5, P1-7, D6, UX-07 (desenho).

**Problema.** Barra de menus, doca, barra de status, menu de contexto, paleta e diálogo de atalhos precisam da mesma fonte de ações e de APIs estáveis; sem contrato definido antes, cada PR reinventa a ligação em `main.js` (ponto de integração mais disputado).

**Proposta.**

Opus escreve `docs/notas/ui-contratos.md` (a sessão principal leva o essencial para `docs/ARQUITETURA.md`, seção 11 "Viewer", e registra na seção 15):
- Registro de ações: `{ id, labelKey, group, shortcut, enabled(ctx), run(ctx), load?() }`; consumidores: menus da barra, diálogo `?`, menu de contexto, paleta; `load()` é o gancho do carregamento sob demanda (UI-14).
- Doca: `register({ id, titleKey, mount(host, ctx) })`, `open`, `close`, `maximize`, eventos; alturas mínimas; chaves de `localStorage`; contrato com o grid (`ensureSelectedVisible()` no redimensionamento).
- Barra de status: `count`, `position`, `progress`, `message(text, { kind, timeoutMs })`, `connection(state)`; substitui `#message`.
- Fichas de filtro: funções puras filtro → fichas e remoção de ficha → filtro.
- Lista de regiões para `F6`.
- Para cada módulo atual: como migra (lista), o que fica em modelo puro testável.

**Aceite.**

- Documento cobre as 5 interfaces com tipos, pontos de chamada e caminho de migração de cada módulo que hoje insere botão ou painel.
- Aprovado na revisão cruzada; a sessão principal registra em `docs/ARQUITETURA.md` (seções 11 e 15).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `docs/notas/ui-contratos.md` (novo).

**Pontos compartilhados** (sessão principal): docs/ARQUITETURA.md.

### REC-12 · UI-12 Barra superior de uma linha com menus e estado ativo

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L3 | M | sonnet | UI-10, UI-11 |

**Origens:** P1-1, P0-1 (a), VC-02 (a), P2-8, REVISAO-UI-UX §8 #7, UX-06.

**Problema.** 22 botões de mesmo peso em `#toolbar` (`flex-wrap: wrap`, `css/app.css:144-150`): 2 linhas em 1600 px, 3 em 1366 e 4 em 200%; sem estado ativo; cada módulo tira o `hidden` do seu botão ao carregar, e a barra ganha a 2ª linha depois da pintura (deslocamento de 0,70 do CLS).

**Proposta.**

Barra de 36 px (`--bar-height`), sem quebra: nome do app · seletor de caso ▾ (New case…, recentes, Close case) · busca (flexível, até 560 px) · Go to time · espaçador · alternâncias Filter tree, Highlights, Bookmarks (`aria-pressed`) · menu **Analyze** (Distinct values, Group by message type, Aggregate, Operations, Adapter dashboard, Payloads, Unknown message types, Effective configuration) · **Export** (Export report…, Copy as…) · **View** (Columns…, Follow tail, Theme ▸ System/Light/Dark, Shortcuts).
- Alerts como indicador com contagem só com `serve --watch`.
- Todos os itens existem desde o HTML ou o registro síncrono de ações; módulo indisponível = item `disabled` com `title`, nunca `hidden` → visível.
- Sem caso: só caso, busca desabilitada com dica e View.
- Os módulos deixam de inserir botões na barra e passam a registrar ações (UI-11).

**Aceite.**

- Barra com 1 linha em 1280, 1366 e 1600 px de largura (capturas).
- Trace da abertura: nenhum deslocamento causado pela barra.
- Menus por teclado (testes da UI-10) e estado aberto/pressionado visível.
- Tema System/Light/Dark persiste; testes de fluxo dos painéis abrem pelo menu.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/toolbar-menu.js` e js/toolbar-menu-model.js (novos, com teste); `css/toolbar.css` (novo); `js/theme.js`; montagem do botão em distinct-panel.js, aggregate-panel.js, highlight-panel.js, copy-export.js e nos demais que inserem botão.

**Pontos compartilhados** (sessão principal): index.html:21-49; js/main.js; js/strings.js.

### REC-13 · UI-13 Barra de status fixa e mensagens no lugar da ação

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L3 | M | sonnet | UI-10, UI-11, UI-12 |

**Origens:** P1-7, VR-04, P0-1 (e), VC-02 (b, banner de relógio), REVISAO-UI-UX §8 #8, #9, UX-15.

**Problema.** O banner `#message` (`index.html:51`) aparece e some empurrando tudo 28 px, fica até a próxima mensagem e mostra erros longe do campo; a ajuda de atalhos ocupa 3 linhas fixas; `#grid-status` com `aria-live` fala a cada rolagem.

**Proposta.**

Rodapé de 22 px (`--status-height`) com segmentos: contagem ("285,093 records" ou "about 1.2M records" com `.is-estimate`), posição (sem live), indexação (`<progress>` + "Indexing 3 of 28 files"), aviso de relógio entre coletas, mensagem (`role="status"`, `aria-live="polite"`; sucesso some em 8 s; erro fica com fechar) e botão `?`.
- Erro global (conexão) troca o conteúdo da própria barra: `.is-error` com `--status-error-bg`/`--status-error-ink`, `role="alert"`, "Retry now"; nada é sobreposto à faixa de filtro.
- Erro de formulário junto do campo (`aria-invalid` + `.ui-field-error`); erro de painel no corpo do painel (`.ui-error`).
- A ajuda fixa de atalhos sai da tela; o `?` mostra o texto atual até a UI-28 entregar o diálogo.

**Divergência decidida.** A análise 1 punha o erro global num banner sobre o topo da coluna central; a análise 2 mediu que esse banner cobre a faixa de filtro (VR-04). Decidido: erro global dentro da barra de status. O lote da barra de status sobe de L8 para L3 porque várias UI seguintes (faixa de filtro, estado vazio, falha de rede) dependem dela.

**Aceite.**

- Mostrar e esconder mensagem não muda a posição de nenhum elemento (retângulo do grid igual antes/depois, em teste).
- "Path does not exist" aparece abaixo do campo, com `aria-invalid`.
- Com o servidor parado, a barra de status mostra o erro e a interseção com a faixa de filtro é 0 px.
- `#grid-status` deixa de ser região live.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/status-bar.js` (novo, com teste); `css/status-bar.css` (novo); `js/clock-banner.js`; `js/sources.js` e js/case-files.js (só erros de campo); `js/views-stack.js` (mensagem junto do botão Narrow).

**Pontos compartilhados** (sessão principal): index.html:51; js/main.js:144-150; js/strings.js.

### REC-14 · UI-14 Abertura rápida: módulos sob demanda e requisições em paralelo

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L3 | M | sonnet | UI-02, UI-04, UI-06, UI-12, UI-13 |

**Origens:** VC-07, VC-01 (b, c), VC-02 (orçamento de CLS), ANALISE-VERCEL §5.

**Problema.** 92 requisições e 1,0 MB na abertura; 36 `import()` disparados no `start()` (`main.js:745-1229`) mesmo para painéis nunca abertos; `/api/cases` fica 208 ms na fila das 6 conexões; `/collections` só começa depois de `/sources`; primeira linha em 1.236 ms.

**Proposta.**

- `js/boot.js` mínimo: dispara `/api/cases` (e, havendo caso salvo, `/sources` e `/collections` em `Promise.all`) e então `import("./main.js")`, entregando as promessas; `applyView` uma vez só, quando os dois chegam.
- `<link rel="modulepreload">` no `index.html` para o grafo estático de `main.js` (4 viagens viram 1).
- Painéis sob demanda pelo `load()` do registro de ações: `modulepreload` no `pointerenter`/`focus` do item, `await import()` no clique; falha de import = item desabilitado com mensagem (mantém a tolerância de "optional module").
- Na abertura ficam só: grid, detalhe, fontes, filtros, timeline, trilha (se houver estado salvo), Follow tail, alertas e aviso de relógio.
- Nunca misturar URL com e sem `?v=`.

**Aceite.**

- Com `tools/ui_perf.mjs` no caso de 3,5 milhões, CPU 4×: primeira linha ≤ 600 ms (com a UI-02); ≤ 45 requisições e ≤ 450 KB de JS na abertura; ≤ 2 viagens em série antes de `/page`.
- CLS da abertura ≤ 0,05 e da sessão ≤ 0,1.
- Abrir um painel pela primeira vez ≤ 100 ms depois do hover; teste de fluxo para cada painel carregado sob demanda.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/boot.js` (novo); `js/sources.js` (pedido em paralelo).

**Pontos compartilhados** (sessão principal): index.html; js/main.js; js/strings.js.

### REC-15 · UI-15 Doca inferior com abas para os painéis de análise

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P0 | L4 | L | sonnet | UI-10, UI-11, UI-12 |

**Origens:** P0-2 (solução), P0-1 (mínimo de linhas), VR-02, VR-09, D1, UI-HANDOFF §5.8, UX-07.

**Problema.** Os painéis entram em `#grid-panel` antes do filtro (`insertBefore` em `distinct-panel.js:377`, `aggregate-panel.js:582`, `highlight-panel.js:138`, `views-stack.js:73`) e empurram o grid; o painel também não cabe (coluna quebrada letra por letra, ação abaixo da dobra). Na primeira rodada dos protótipos, abrir a doca escondeu a linha selecionada.

**Proposta.**

Pelo contrato da UI-11:
- Abas (`role="tablist"`, `×` por aba), Maximize/Restore e Close all; divisor `role="separator"` horizontal com `aria-valuenow` em %, setas 5%, Home/End, duplo clique alterna 40%/maximizado.
- Altura inicial `clamp(160px, conteúdo, 40%)`; depois do primeiro arraste vale a altura lembrada (`dmslog.dockHeight`, com `try/catch`). Grid com mínimo de 8 linhas + cabeçalho; doca com mínimo de 160 px; altura da janela < 640 px abre maximizada.
- Ao abrir, redimensionar, maximizar ou mudar de breakpoint: se a linha selecionada estava visível, preserva a distância do topo; se sairia, rola para 1/3 da altura do grid (`ResizeObserver` no viewport chamando o mesmo "garantir visível" do teclado; nada roda durante a rolagem).
- Abrir move o foco para o título do painel (`h2`); Esc com foco dentro fecha e devolve o foco.
- Os módulos passam a montar no host da doca, sem mudar a lógica; Export report e Copy as viram diálogos (UI-23).

**Aceite.**

- Em 1366×768 com qualquer painel aberto: grid ≥ 8 linhas e cabeçalho de 22 px (R mediu 16 linhas).
- Linha selecionada continua visível depois de abrir, redimensionar e maximizar (teste do modelo + fluxo).
- Divisor operável por teclado; altura lembrada; teste de `dock-model` (mínimos, `clamp`, persistência).
- Capturas 07, 10, 13, 15 e 17 refeitas.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/dock.js`, js/dock-model.js, css/dock.css (novos, com teste); `js/grid.js` (ensureSelectedVisible); só a função de montagem em distinct-panel.js, aggregate-panel.js, highlight-panel.js, events-panel.js, dashboard.js, payload-list.js, alerts-panel.js, unknown-panel.js, config-panel.js, key-panel.js, compare.js, side-by-side.js.

**Pontos compartilhados** (sessão principal): js/main.js; index.html (host da doca).

**Decisão do Jefferson:** D1 (onde abrem os painéis): recomendação mantida, doca inferior com abas.

### REC-16 · UI-16 Colunas do grid que deixam a mensagem legível em notebook

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P0 | L4 | M | sonnet | UI-05, UI-07, UI-08 |

**Origens:** VR-01 (colunas e horário), VR-08, P1-3, UI-HANDOFF §4.2.

**Problema.** Colunas fixas somam 668 px (horário 190, PID e TID 72+72, Level 84, Source 160, Collection 90; `css/app.css:418-440`): com as laterais, a coluna Message fica com 0 a 12 caracteres em 1280–1366 px. A coluna Source corta o fim do nome e esconde exatamente `.sensitive`, `.log` e o índice de rotação.

**Proposta.**

- Larguras padrão em `ch` da fonte do grid (as que o usuário arrastou continuam salvas em px, 9.18): horário `HH:MM:SS.mmm` 12ch (≈ 100 px); `MM-DD HH:MM:SS.mmm` 18ch só quando o caso cobre mais de um dia (decidido uma vez por caso); PID e TID 6ch (≈ 44 px); Level 9ch (≈ 72 px); Source 20ch (≈ 148 px); Collection 5ch (≈ 36 px, etiqueta da UI-07).
- Data completa no detalhe, no intervalo da faixa de filtro e no `title` da célula.
- Source com reticências no meio (`Adapter_10…sensitive.log.0`): função pura `middleEllipsis(name, maxChars)` em `grid-model.js`, com cache por `source_id` + largura em caracteres; largura do caractere mono medida uma vez por mudança de fonte ou de coluna, nunca por linha.
- Selo `S` (`--text-xs`, borda `--line-strong`) antes do nome das fontes sensitive; `title` com o nome completo.

**Divergência decidida.** VR-01 dá as larguras em px e VR-08 em `ch`; decidido `ch` com os mesmos valores aproximados, porque a fonte mono efetiva muda entre Windows e Linux. Sobe para L4 (era L6 na análise 1) por ser P0.

**Aceite.**

- Teste de `middleEllipsis`: nome curto, exato, 120 caracteres, preserva `.sensitive`, `.log` e `.log.0`.
- Em 1600×900 com coluna Collection, Message com ≥ 60 caracteres; o aceite de ≥ 50 em 1280×720 e 1366×768 fecha na UI-17.
- Trace de rolagem sem medição de texto; larguras salvas pelo usuário preservadas (testes de `column-resize`).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/grid.js` (fillCells, formato do horário); `js/grid-model.js` (+ teste); `js/column-resize.js`; `css/app.css` (larguras de coluna); `css/collections.css`.

**Pontos compartilhados** (sessão principal): js/strings.js (rótulo acessível do selo S).

### REC-17 · UI-17 Layout responsivo sem sobreposição: trilho da lateral e gavetas

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P0 | L4 | M | sonnet | UI-12, UI-15, UI-16 |

**Origens:** P0-5, VR-01 (trilho e breakpoints), REVISAO-UI-UX §8 #3, UI-HANDOFF §3.1, UX-14.

**Problema.** No breakpoint de 1100 px (`css/app.css:535-546`) o grid transborda por cima do detalhe em 900×800; em 200% a timeline cobre o detalhe e "Create case" cobre o texto (WCAG 1.4.10 e 1.4.4). A regra "três colunas a partir de 1280 px" do handoff não garante largura para a mensagem.

**Proposta.**

- ≥ 1500 px: lateral 248 · centro `minmax(0, 1fr)` · detalhe 380.
- 1280–1499 px: lateral vira **trilho de 44 px** (botão vertical "Sources" com `3/14`, `aria-expanded`, abre a lateral como gaveta sobreposta de 280 px; Esc fecha e devolve o foco); detalhe 360.
- 960–1279 px: trilho + detalhe como gaveta à direita (420 px, `--surface-overlay`, `--shadow-overlay`, `--z-drawer`), aberta por Enter no grid ou pelo botão "Detail" na faixa de filtro.
- < 960 px: idem, e as três alternâncias da barra entram no menu View.
- Altura < 640 px: timeline recolhida e doca maximizada.
- Zoom 200% em 1600 px = 800 px CSS: cai na regra < 960.

**Divergência decidida.** Análise 1: gaveta do detalhe abaixo de 1280 px e da lateral abaixo de 960. Análise 2: trilho já abaixo de 1500 px. Decidido somar os dois: trilho < 1500, gaveta do detalhe < 1280. Sobe de L7 para L4 por ser P0.

**Aceite.**

- 900×800 e 200%: interseção 0 px entre barra, timeline, grid e detalhe (capturas 26 e 32 refeitas).
- Message com ≥ 50 caracteres em 1280×720 e em 1366×768 com 2 coletas (R mediu 56 e 68).
- Gavetas e trilho por teclado (Enter, Esc, retorno de foco), com teste do modelo das gavetas.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `css/app.css` (seção responsiva); `js/drawers.js` (novo, com teste).

**Pontos compartilhados** (sessão principal): index.html (trilho e botões); js/main.js; js/strings.js.

### REC-18 · UI-18 Faixa de filtro com estado Filtered, trilha e fichas removíveis

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L5 | M | sonnet | UI-09, UI-10, UI-11, UI-13, UI-17 |

**Origens:** P1-5, VR-03, P2-13 (Level em Advanced), UI-HANDOFF §5.9, UX-08.

**Problema.** Em "View 2" a busca volta vazia e os selects ficam em "Any", mas o grid continua filtrado; árvore de filtros, fontes e coletas não aparecem perto do grid. Nos protótipos, fichas que não cabem são cortadas sem aviso em 1366 px.

**Proposta.**

Faixa única de 30 px (`--strip-height`):
- Esquerda: trilha (`nav aria-label="Views"`, `ol`, atual com `aria-current="page"`), Back/Forward como ícones com `aria-label`.
- Direita: fichas do filtro efetivo da visão atual (`Severity ≥ Error ×`, `3 sources ×`, `C2 ×`, `text "Validation" ×`, `Filter tree (4 conditions)`), "Filters ▾" (popover com Severity "At least…", From, To, Hide noise, Unclassified only, Level em "Advanced", Apply), Narrow e `⋯` (Rename, Show in parent, Show in root).
- Com filtro: `.is-filtered { background: var(--filtered-bg); box-shadow: inset 3px 0 0 var(--accent); }` e rótulo "Filtered"; sem filtro: "No filters" em `--ink-muted`.
- Contagem `1,204 of 235,230 records` com `flex: none`; fichas que não cabem viram "+N more" (abre o popover); medir só quando o filtro ou a largura da faixa muda.
- Modelo puro filtro ↔ fichas (contrato da UI-11).

**Aceite.**

- Teste do modelo: busca, severidade, nível, intervalo, fontes, coletas, árvore, unclassified e hide noise viram fichas; remover a ficha remove a condição.
- Em 1366×768 com 3 ou mais condições: nenhuma ficha cortada e contagem inteira (captura).
- Cenário da captura 19 mostra `text "Validation"` em View 2.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/views-stack.js`; `css/views-stack.css`; `js/filters.js`; `js/filter-chips.js` e js/filter-chips-model.js (novos, com teste).

**Pontos compartilhados** (sessão principal): index.html:103-124; js/main.js; js/strings.js.

### REC-19 · UI-19 Timeline como faixa de alarme compacta

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L5 | M | sonnet | UI-05 |

**Origens:** P1-4, VR-05, P2-10, P0-1 (b), UX-09, D5.

**Problema.** Info (160 mil) em azul saturado ocupa quase toda a área (185 px) e Error (20,6 mil) vira faixa de 2 a 4 px; mesmo em cinza, com a escala comum o pico de erro continua um traço (protótipo A); o texto fixo de ajuda ocupa uma linha; eixo em `1/15/30`.

**Proposta.**

- Altura 40 px (`--timeline-h`), expansível a 120 px (lembrada).
- Barras empilhadas só de Critical, Error e Warning (`--chart-critical`, `--chart-error`, `--chart-warning`), com escala própria; Info + Verbose como linha de 1,5 px em `--chart-normal`, escala própria até 40% da altura; lacunas continuam visíveis.
- Legenda numa linha à direita, com contagens, séries alternáveis (`aria-pressed`) e "Errors only"; "N undated records" quando houver.
- Eixo com data ISO (`2030-01-15`); arrastar para filtrar continua; a dica vai para o `title` e para o diálogo `?`.

**Divergência decidida.** Análise 1: 64 px (expande a 160), séries empilhadas na mesma escala com Info cinza. Análise 2: 40 px (120), anormal com escala própria e normal como linha. Decidido pela análise 2: é a única forma medida em que o pico aparece.

**Aceite.**

- Caso sintético com pico de erro: barra do pico ≥ 50% da altura da timeline.
- Cores dos gráficos ≥ 3:1 sobre o painel (teste de tokens); teste do `timeline-model` para as duas escalas.
- Densidade final (com UI-12 a UI-18 integradas), sem doca: ≥ 24 linhas em 1600×900, ≥ 18 em 1366×768, ≥ 12 em 1280×720; com doca: ≥ 8 (R mediu 37, 30, 28 e 15 a 20).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/timeline.js`; `js/timeline-model.js` (+ teste); `css/timeline.css`.

**Pontos compartilhados** (sessão principal): js/strings.js.

**Decisão do Jefferson:** D5 (Info neutra): recomendação mantida.

### REC-20 · UI-20 Detalhe do registro em ordem de investigação

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L5 | M | sonnet | UI-01, UI-10 |

**Origens:** P1-6, P2-9, P0-4 (regras definitivas), VR-06 (selo só no detalhe), UI-HANDOFF §6, UX-10.

**Problema.** Metadados em texto corrido com " - "; botões quebram em 2 linhas; "What it means" vem depois de Bookmark e dos destaques; as chaves ficam no fim, abaixo do payload; XML largo corta à direita.

**Proposta.**

Ordem: (1) cabeçalho (horário mono 600, selo de nível na cor da severidade, nome da fonte + selo `sensitive`, PID, TID, coleta); (2) ações `.is-ghost` com ícone + rótulo curto (Context ▾, Copy, Compare, Bookmark ★, Highlight ▾); (3) título do tipo de mensagem (código no `title`); (4) texto (`min-height: 6lh; max-height: 45vh; resize: vertical`); (5) What it means + nota (some sem pack); (6) Fields e Keys (fichas "Same key"); (7) Links; (8) Payload recolhível com Formatted/Raw/Wrap lines (lembrado)/Copy; (9) nota do bookmark.
- Seções 5 a 9 com `h3` recolhível (`button` + `aria-expanded`), estado lembrado.
- Sem registro: `.ui-empty` "Select a record to see its full text, meaning and links."

**Aceite.**

- Em 1600×900, registro com payload: texto e "What it means" visíveis sem rolar.
- `detail-flow` e `knowledge-flow` atualizados e verdes; Wrap lines persiste.
- Capturas 05, 20 e 29 refeitas.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/detail.js`; `css/knowledge.css`; `css/links.css`; `css/payload.css`; `js/payload.js`; `css/bookmarks.css` (parte do detalhe); `js/highlight-panel.js` (só a seção do detalhe).

**Pontos compartilhados** (sessão principal): index.html:135-160; js/strings.js.

### REC-21 · UI-21 Lateral com o caso aberto, coletas compactas e fontes com espaço

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L5 | M | sonnet | UI-09, UI-10, UI-17 |

**Origens:** P0-3 (solução), P2-7, P2-11, P2-12, VR-10, VC-05 (a, b), UX-11, UX-16 (P2-7, P2-11, P2-12).

**Problema.** Com caso aberto a lateral mostra formulário de novo caso, campo de caminho, "Add ERS files" e o painel de coletas (459 px) acima das fontes; meta "HOST - pid - -" quando falta o PID; progresso ": 235,230 records"; arquivo ilegível avisado longe da fonte; `renderSources()` recria a lista inteira a cada atualização e quem usa o teclado perde o foco (WCAG 2.4.3).

**Proposta.**

- Cabeçalho fixo do caso (nome + "Add files…" + menu `⋯`); coletas recolhíveis (abertas com mais de uma; uma linha por coleta: `C1`, servidor, contagem, aviso) com `⋯` → "Remove C2…" (`.is-danger`, confirmação em dois passos já existente); filtro de fontes fixo; lista com rolagem e `min-height: 240px`.
- Meta sem o segmento ausente; progresso "{source}: {n} records" e, no fim, "Indexed {n} records.".
- Fonte ilegível: marcador `!` (`--status-error-ink` sobre `--status-error-bg`) antes do nome, `title` com o motivo e "1 file unreadable" no cabeçalho de Collections.
- Renderização por reconciliação por `id`: conjunto igual = só atualiza textos; mudou = insere/remove só os itens afetados, preservando `document.activeElement` e a rolagem; um listener delegado de `change`/`keydown` no `<ul>`.
- Regras da lateral saem de `app.css` para `css/sources.css`.

**Divergência decidida.** A reconciliação da lista (VC-05 a, b) entra aqui, e não na UI-30, para reescrever `renderSources()` uma vez só.

**Aceite.**

- Com 2 coletas, a lista de fontes ocupa ≥ 60% da altura da lateral e mostra ≥ 26 linhas em 1600×900 (R: 31).
- Teste: foco num checkbox continua no mesmo checkbox depois de 3 atualizações.
- Capturas 25 e 27 refeitas; testes de texto de P2-11 e P2-12.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/sources.js`; `js/collections-panel.js`; `css/collections.css`; `css/sources.css` (novo).

**Pontos compartilhados** (sessão principal): index.html:55-83; js/main.js; js/strings.js.

### REC-22 · UI-22 Diálogos de caso e estado vazio sem caso

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L5 | M | sonnet | UI-10, UI-16, UI-21 |

**Origens:** P1-8, P2-4, VR-11, P2-5, UX-16 (P2-5).

**Problema.** Três caminhos para abrir arquivos visíveis ao mesmo tempo; "Add" e "Add 1 file(s) to the case" com o mesmo verbo para ações diferentes; "Create case" desabilitado com 2,05:1; sem caso, grid vazio com filtros e timeline soltos; no navegador de pastas, nomes de ZIP cortados no fim.

**Proposta.**

- Sem caso: estado vazio no centro ("Open an ERS to start", **New case…** primário, casos recentes com nome, coletas e data, dica "Paste a path or use Browse"); lateral e detalhe com 0 px; filtros, trilha e timeline escondidos; barra de status "No case open.".
- New case… e Add files… em `.ui-dialog` com o componente `CaseFiles`; Add files… inclui a seção "Folder or single file" (campo + escopo Default/All logs) que substitui o formulário "Open"; rótulos "Add to list" e "Add 2 files to case".
- Navegador de pastas em `<dialog>`, nomes com `middleEllipsis` (UI-16) e selo Enterprise/Sensitive (tipo já calculado em `case-files-model.js`).

**Divergência decidida.** VR-11 tira os casos recentes da lateral (a análise 1 deixava nos dois lugares). Decidido pela análise 2.

**Aceite.**

- Com caso aberto, nenhum formulário na lateral (captura 04 refeita).
- Botão primário desabilitado legível, sem `opacity`; diálogo prende o foco e fecha com Esc (nativo).
- `case-files-flow` e `folder-browser-model` atualizados; capturas do estado vazio claro e escuro.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/case-files.js`; `js/case-files-model.js`; `css/case-files.css`; `js/folder-browser.js`; `js/sources.js` (remoção dos formulários).

**Pontos compartilhados** (sessão principal): index.html:55-83; js/main.js; js/strings.js.

### REC-23 · UI-23 Painéis no padrão comum e diálogos de exportação

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L5 | M | sonnet | UI-10, UI-15 |

**Origens:** P1-9 (migração), P2-6, P2-14, UX-16 (P2-6), UI-HANDOFF §5.4, §5.5, REVISAO-UI-UX §6 (fluxo Exportar).

**Problema.** Alguns painéis têm Close, outros não; títulos em 3 tamanhos; Refresh em posições diferentes; ação principal abaixo da dobra (Export, Aggregate); Compare com "Record 14941", caminho completo e código do template; Operations com rótulo "Search" sem campo ao lado.

**Proposta.**

- Cada painel migra para `.ui-panel` (cabeçalho fixo com título `h2`, resumo, ações e Close; corpo com rolagem; rodapé com a ação principal) e `.ui-table`; estados `.ui-status`, `.ui-empty`, `.ui-error` com próximo passo ("Could not load operations. {reason} Try Refresh.").
- Esc com foco dentro fecha e devolve o foco.
- Export report… e Copy as… viram `.ui-dialog` com a ação fixa no rodapé.
- Compare: cabeçalho A/B com horário, nome da fonte e tipo de mensagem; diff primeiro.
- Operations: rótulo associado ao campo; filtros numa linha.
- Pode ser dividida por painel (arquivos disjuntos), integrando um PR por vez.

**Aceite.**

- Todo painel com Close e Esc (teste de fluxo por painel).
- Com a doca em 160 px, a ação principal de cada painel visível sem rolar.
- Coluna Name de Operations sem quebra letra por letra; capturas 08 a 17 refeitas.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): os módulos de painel e seus CSS: distinct-panel, aggregate-panel, events-panel, dashboard, payload-list, alerts-panel, export-panel, copy-export, config-panel, unknown-panel, compare, side-by-side, presets, bookmarks-panel, filter-tree.

**Pontos compartilhados** (sessão principal): js/strings.js.

### REC-24 · UI-24 Seguir uma chave entre fontes

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L5 | M | sonnet | UI-15, UI-18, UI-20, UI-23 |

**Origens:** P1-13, REVISAO-UI-UX §6 (fluxo seguir um GUID), ANALISE-VARIANTES §5.1 (passo 5).

**Problema.** As fichas de chave ficam no fim do detalhe, abaixo de um payload de 600 px; o painel "Records with the same key" mostra caminho completo e código do template (contra a decisão da 9.19), horário em 3 linhas e "1 records".

**Proposta.**

- Fichas de chave no bloco 6 do detalhe (UI-20).
- Painel de mesma chave na doca com as colunas do grid (Time, nome da fonte + selo S, Level, tipo de mensagem, Message), uma linha por registro, plural correto.
- "Show in grid" cria visão encadeada com o filtro da chave (vira ficha na faixa).
- Entrada "Same key ▸" no menu de contexto chega com a UI-29.

**Aceite.**

- Em 1600×900, a partir de um registro com payload, seguir uma chave leva ≤ 2 ações sem rolar o detalhe.
- Teste: o painel não mostra caminho completo nem código de template.
- "Show in grid" cria a visão com a ficha; `key-panel-flow` verde.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/key-panel.js`; `js/key-panel-model.js`; `css/key-panel.css`; `js/links.js`.

**Pontos compartilhados** (sessão principal): js/main.js (criação da visão); js/strings.js.

### REC-25 · UI-25 Falha de rede ou de consulta sem perder o que está na tela

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L6 | M | sonnet | UI-13, UI-18, UI-21 |

**Origens:** VC-03, VR-04 (estado Disconnected), UI-HANDOFF §9 (servidor indisponível).

**Problema.** `Grid.reload()` (`js/grid.js:253-271`) zera a janela e renderiza antes de pedir a página: se o pedido falha, o grid fica vazio, o filtro novo já está na trilha e a timeline continua com dados antigos; o `EventSource` tenta reconectar sem parar (9 falhas em 20 s); não há `aria-busy`.

**Proposta.**

- Manter os dados anteriores: o grid só troca a janela quando a nova página chega; enquanto isso, linhas antigas com `opacity: .6`, `aria-busy="true"` no `role="grid"` e, depois de 400 ms, barra fina de progresso.
- Filtro e trilha só são confirmados depois do sucesso; em erro, voltam ao estado anterior e a mensagem fica junto do grid com "Retry".
- Erro de rede: estado "Disconnected" na barra de status ("dmslog is not running. Start it again, then Retry."), "Retry" chama `/api/health`.
- SSE para depois de 3 falhas seguidas e volta no Retry; timeline com falha fica marcada como desatualizada, sem misturar dados.

**Aceite.**

- Teste que simula falha de `api.page`: linhas e filtro anteriores mantidos, nenhuma visão nova na trilha.
- Com o servidor parado: no máximo 3 tentativas de SSE; captura 03 refeita.
- `aria-busy` alterna durante a recarga (teste).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/grid.js`; `js/grid-model.js` (+ teste); `js/views-stack.js`; `js/follow-tail.js` (limite de reconexão); `js/sources.js` (recarga das fontes).

**Pontos compartilhados** (sessão principal): js/main.js (aplicação do filtro e da trilha); js/strings.js.

### REC-26 · UI-26 Grid legível por leitor de tela

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L6 | M | sonnet | UI-13, UI-25 |

**Origens:** P1-11, REVISAO-UI-UX §8 #4, #8, UI-HANDOFF §4.3.

**Problema.** Cada linha tem `aria-label="Entry {id}"` (`js/grid.js:537`), que substitui as células; o cabeçalho fica fora do `role="grid"`; `aria-rowcount` vale o tamanho da janela carregada (`grid.js:519`); sem `aria-activedescendant`.

**Proposta.**

- Sem `aria-label` na linha; bookmark e destaque como texto oculto numa célula.
- `#grid-viewport` com `role="grid"`, `aria-rowcount` = total (ou `-1` quando estimado), `aria-colcount`; cabeçalho dentro do grid em `role="rowgroup"`; linhas com `aria-rowindex` quando a posição é exata.
- Ids estáveis por slot do pool e `aria-activedescendant` = linha selecionada.
- Região live só na barra de status, para contagem e filtro (UI-13).

**Aceite.**

- Teste DOM confere os atributos depois de rolar e selecionar.
- Árvore de acessibilidade (CDP) mostra o conteúdo das células na linha selecionada.
- Conferência com NVDA no Windows registrada no PR antes do merge.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/grid.js`; `tests/web/` (testes de grid).

**Pontos compartilhados** (sessão principal): index.html:125-131.

### REC-27 · UI-27 Rolagem do grid sem layout forçado

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P2 | L6 | S | sonnet | UI-04, UI-26 |

**Origens:** VC-09, UI-HANDOFF §10 (perfil de rolagem), UX-12 (aceite de desempenho).

**Problema.** 120 eventos de roda geram 120 layouts forçados: `syncScroll` escreve `scrollTop` e lê de volta (`js/grid.js:430-431`) e `ensurePool` lê `clientHeight` a cada `render()` (`grid.js:491`). Com 30 ou mais linhas visíveis e `<mark>`, o custo cresce.

**Proposta.**

- `syncScroll`: `const top = tsToScroll(row.ts, this.range); this.viewport.scrollTop = top; this.lastScroll = top;`.
- `ensurePool` usa a altura guardada do `ResizeObserver` que já existe (`entry.contentRect.height`).
- Deltas de roda acumulados e uma renderização por `requestAnimationFrame`.
- `#grid-viewport { contain: strict; }` e `.grid-row { contain: layout paint style; }`; `content-visibility: auto` só em painéis fechados e seções do detalhe, nunca nas linhas do pool.

**Aceite.**

- `tools/ui_perf.mjs` no caso de 3,5 milhões, CPU 4×: 0 layouts forçados em 120 eventos de roda; ≤ 4 ms de script + estilo + layout por quadro; 0 tarefas > 50 ms.
- INP continua ≤ 100 ms (hoje ≤ 24).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/grid.js`; `css/app.css` (contain).

**Pontos compartilhados** (sessão principal): nenhum.

### REC-28 · UI-28 Teclado: regiões, busca, diálogo de atalhos e letras desligáveis

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L7 | M | sonnet | UI-11, UI-12, UI-15, UI-17 |

**Origens:** P1-12, P0-1 (d), P2-10 (dica no diálogo), REVISAO-UI-UX §8 #5, #6, UI-HANDOFF §8, UX-13, D7.

**Problema.** 24 Tabs até o primeiro campo da lateral; sem skip link; nenhum atalho para a busca; atalhos de uma letra globais em `document` (`main.js:1028`, `1244`; `highlight-panel.js:332`) sem opção de desligar (WCAG 2.1.4); `n` sem seleção responde "Select an entry first.".

**Proposta.**

- `F6`/`Shift+F6` alternam regiões na ordem do DOM e do Tab (barra, lateral, faixa, grid, doca, detalhe; doca antes do detalhe para o F6 e o Tab andarem na mesma ordem, WCAG 2.4.3; decidido na UI-11, `docs/notas/ui-contratos.md` §5.1); `/` foca a busca; `?` abre o diálogo de atalhos (tabela tecla × ação gerada do registro de ações) com "Single-key shortcuts: on/off" (`dmslog.singleKeys`, padrão ligado).
- `n`/`N` sem seleção começam do primeiro/último registro visível.
- Skip link "Skip to log entries" como primeiro focável, visível só no foco.
- Atalhos de letra centralizados em `js/shortcuts.js`, ignorados com foco em campo de texto.

**Aceite.**

- Teste do `shortcuts-model`: letras desligadas, foco em campo ignora, `n` sem seleção.
- Do topo até o grid em ≤ 2 Tabs pelo skip link; teste de fluxo do diálogo `?`.
- WCAG 2.1.4 atendido (opção persistida).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/shortcuts.js` e js/shortcuts-model.js (novos, com teste); `js/highlight-panel.js` (remove o handler).

**Pontos compartilhados** (sessão principal): js/main.js; index.html (skip link); js/strings.js.

**Decisão do Jefferson:** D7 (atalhos de uma letra): recomendação mantida, ligados com opção de desligar.

### REC-29 · UI-29 Menu de contexto da linha e paleta de comandos

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P2 | L7 | M | sonnet | UI-11, UI-12, UI-24, UI-28 |

**Origens:** D6, REVISAO-UI-UX §7 (descobribilidade), VC-10, P1-13 (Same key no menu).

**Problema.** `t`/`T`, `[`/`]`, `c`, `Alt+←/→`, "Same key" e o ajuste de coluna só são achados por memória; não há busca de ações por nome.

**Proposta.**

- Menu de contexto na linha (clique direito, `Shift+F10`, tecla Menu): Same key ▸ (chaves do registro), Only this source, Hide this message type, Bookmark, Compare, Copy, com a tecla ao lado; usa `.ui-menu` e o registro de ações.
- Paleta `Ctrl+K` (e View → Command palette…): `.ui-dialog` com combobox + listbox, `aria-activedescendant`, filtro por rótulo (modelo puro), atalho ao lado, Enter executa, Esc devolve o foco. JS puro, sem `cmdk`.

**Aceite.**

- Teste do `commands-model` (filtro, ordem, itens desabilitados) e de teclado.
- Teste: toda ação dos menus aparece na paleta (mesmo registro).
- Menu de contexto operável por teclado (teste de fluxo).
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/commands.js`, js/commands-model.js, css/commands.css, js/context-menu.js (novos, com teste).

**Pontos compartilhados** (sessão principal): js/main.js; js/strings.js.

**Decisão do Jefferson:** D6 (menu de contexto): recomendação mantida, sim; a paleta entra junto porque usa o mesmo registro.

### REC-30 · UI-30 Follow tail: ir ao fim, contagem única e pausa com aba oculta

| Prioridade | Lote | Esforço | Modelo | Depende de |
|---|---|---|---|---|
| P1 | L8 | M | sonnet | UI-13, UI-21, UI-25 |

**Origens:** VC-05 (c, d), VC-06.

**Problema.** Ligar Follow tail fora do fim não leva ao fim; depois de 60 s há três números para a mesma coisa (status "32,187 entries", coleção 45.637, botão "13,900 new records"); a timeline não atualiza; 8 `/api/sources` e 53 `/count` em 60 s, inclusive com a aba oculta (nenhum `visibilitychange`).

**Proposta.**

- Ligar Follow tail fora do fim chama `grid.goEnd()` (rolar para cima continua parando de seguir).
- Status com o total atual ("45,637 records (+13,450 since view)") ou a contagem marcada como "updating"; a timeline entra no `refreshDerived` com o mesmo debounce.
- Com `document.hidden`: pausa atualizações derivadas e contagem, mantém o SSE e faz uma atualização ao voltar.
- `/sources` só quando o SSE indicar fonte nova ou rotação; contagens pelo próprio evento `watch` (endpoint novo só com desenho aprovado pelo Opus).

**Aceite.**

- 10 min de Follow tail: heap e nós estáveis; ≤ 2 ms por atualização; 0 perdas de foco.
- Aba oculta por 30 s: 0 `/api/sources` e 0 `/count`.
- Depois de 60 s, status, timeline e coleção mostram o mesmo total (captura 02 refeita); teste do `follow-tail-model`.
- Aceite comum (seção 4.0).

**Arquivos** (relativos a `src/dmslog/web/` quando de front-end): `js/follow-tail.js`; `js/follow-tail-model.js` (+ teste); `js/grid.js` (updateStatus); `js/timeline.js` (gancho de atualização).

**Pontos compartilhados** (sessão principal): js/main.js; js/strings.js; js/api.js (só se houver endpoint novo).

## 5. Sequência de PRs e paralelização

Regra: um PR por vez a partir da `main` atual, só depois do merge do anterior; antes de abrir, mesclar a `main` e rodar lint e a suíte completa. Pontos de integração com dono único (sessão principal): `js/main.js`, `js/strings.js`, `js/api.js`, `index.html`, `docs/ARQUITETURA.md` (seções 11 e 15) e `docs/ROADMAP.md`. As tarefas entregam no worktree o código dos arquivos exclusivos e, no relatório, um trecho para cada ponto compartilhado; a sessão principal aplica.

### 5.1 Ordem de integração

| PR | Recomendação | Lote | Modelo | Pode ser desenvolvida em paralelo com |
|---|---|---|---|---|
| 1 | UI-01 Layout P0 | L0 | sonnet | UI-02, UI-03, UI-04 |
| 2 | UI-04 Guarda de desempenho | L0 | sonnet | (já desenvolvida no PR 1) |
| 3 | UI-02 `/api/sources` | L0 | opus + sonnet | (já desenvolvida no PR 1) |
| 4 | UI-03 Cache de estáticos | L0 | sonnet | (já desenvolvida no PR 1) |
| 5 | UI-05 Tokens (+ teste haiku) | L1 | sonnet + haiku | UI-09 (inventário), UI-11 (contratos) |
| 6 | UI-06 CSS pelo HTML | L1 | sonnet | UI-11 |
| 7 | UI-07 Seleção e alto contraste | L1 | sonnet | UI-11 |
| 8 | UI-08 Linha de 20 px (se D4 = 20) | L1 | sonnet | UI-11 |
| 9 | UI-09 Textos (aplicação no `strings.js`) | L1 | haiku → sessão principal | UI-10, UI-11 |
| 10 | UI-11 Contratos | L2 | opus | (já desenvolvida) |
| 11 | UI-10 Componentes | L2 | sonnet | UI-19 |
| 12 | UI-12 Barra superior | L3 | sonnet | UI-19 |
| 13 | UI-13 Barra de status | L3 | sonnet | UI-19 |
| 14 | UI-14 Abertura rápida | L3 | sonnet | UI-19 |
| 15 | UI-15 Doca | L4 | sonnet | UI-19 |
| 16 | UI-16 Colunas | L4 | sonnet | UI-19 |
| 17 | UI-17 Responsivo | L4 | sonnet | UI-19 |
| 18 | UI-18 Faixa de filtro | L5 | sonnet | UI-20 |
| 19 | UI-19 Timeline | L5 | sonnet | (desenvolvida antes) |
| 20 | UI-20 Detalhe | L5 | sonnet | UI-18 |
| 21 | UI-23 Painéis (um PR por painel, se dividida) | L5 | sonnet | UI-21 |
| 22 | UI-21 Lateral | L5 | sonnet | UI-23 |
| 23 | UI-22 Diálogos e estado vazio | L5 | sonnet | UI-24 (só depois de UI-23 integrada) |
| 24 | UI-24 Seguir uma chave | L5 | sonnet | UI-22 |
| 25 | UI-25 Falha sem perder contexto | L6 | sonnet | UI-28 |
| 26 | UI-26 ARIA do grid | L6 | sonnet | UI-28 |
| 27 | UI-27 Rolagem sem layout forçado | L6 | sonnet | UI-28 |
| 28 | UI-28 Teclado | L7 | sonnet | (já desenvolvida) |
| 29 | UI-29 Menu de contexto e paleta | L7 | sonnet | UI-30 |
| 30 | UI-30 Follow tail | L8 | sonnet | (já desenvolvida) |

Mudanças de lote em relação à análise 1, e por quê:

- **Barra de status: L8 → L3 (UI-13).** Faixa de filtro (contagem), estado vazio ("No case open."), falha de rede ("Disconnected") e erros de campo dependem dela; deixá-la no fim obrigaria a mexer duas vezes nos mesmos pontos de `main.js`.
- **Colunas e responsivo: L6/L7 → L4 (UI-16, UI-17).** São P0 (VR-01, P0-5) e definem a grade em que doca, faixa e detalhe se encaixam.
- **Seleção e alto contraste: L6 → L1 (UI-07).** Só depende dos tokens, é S e corrige um P1 de acessibilidade.
- **Servidor e guarda de desempenho no L0.** Arquivos fora de `web/`, sem conflito; a guarda precisa existir antes das UI-14 e UI-27, que só provam o aceite com ela.
- **Contratos (UI-11) como tarefa própria do Opus no L2.** CLAUDE.md reserva interfaces ao Opus; doca, barra de menus, barra de status e paleta usam as mesmas APIs.

### 5.2 O que pode e o que não pode correr em paralelo

Podem (arquivos exclusivos disjuntos; nenhum ponto compartilhado editado pelos agentes):

- **UI-02 ∥ UI-03 ∥ UI-04 ∥ UI-01**: `server/api.py` + `store/` × `server/app.py` × `tools/` × `web/css/`. As duas de servidor tocam `docs/ARQUITETURA.md`, aplicado pela sessão principal em cada PR.
- **UI-05 ∥ teste de tokens (haiku) ∥ inventário de textos (UI-09, haiku) ∥ UI-11 (opus)**: `css/tokens.css` + `app.css` × `tests/web/tokens-contrast.test.mjs` × `docs/notas/ux-copy-inventario.md` × `docs/notas/ui-contratos.md`. UI-11 é a única tarefa de Opus nesse lote e não depende das outras.
- **UI-19 (timeline)** só depois do PR 6 (UI-06 também edita `timeline.js` ao mover o CSS injetado para o HTML): só `timeline*.js` e `css/timeline.css`, que nenhuma das UI-10 a UI-17 toca. Integra na sua vez (PR 19).
- **UI-18 ∥ UI-20** no L5: `views-stack.js`, `filters.js`, `filter-chips*` × `detail.js`, `payload*`, `knowledge.css`, `links.css`, `bookmarks.css`, seção do detalhe em `highlight-panel.js`.
- **UI-23 ∥ UI-21**: módulos de painel × `sources.js`, `collections-panel.js`, `collections.css`, `sources.css`. Dentro da UI-23, um agente por painel (arquivos disjuntos), integrados um PR por vez.
- **UI-28 ∥ UI-25/26/27** e **UI-29 ∥ UI-30**: `shortcuts*` e `commands*`/`context-menu.js` não tocam `grid.js`, `follow-tail*` nem `timeline.js`.

Não podem (mesmo arquivo exclusivo ou acoplamento forte):

- Tudo o que toca **`js/grid.js`**: UI-07, UI-15, UI-16, UI-25, UI-26, UI-27, UI-30. Em série, na ordem da tabela.
- Tudo o que toca **`css/app.css`**: UI-01, UI-05, UI-07, UI-16, UI-17, UI-27. Em série.
- **UI-12 → UI-13 → UI-14**: os três reescrevem a inicialização e a barra em `main.js`/`index.html`; mesmo sendo pontos da sessão principal, os trechos dependem um do outro. Em série.
- **UI-20 e UI-23**: ambas tocam `css/bookmarks.css` (detalhe × painel de bookmarks e Compare).
- **UI-21 e UI-22**: ambas tocam `js/sources.js` (lista × remoção dos formulários).
- **UI-15 e UI-23**: a doca muda a montagem dos mesmos módulos de painel que a UI-23 reestrutura.
- **UI-06 e qualquer UI de painel**: a UI-06 mexe nos 23 módulos que injetam CSS; por isso vem cedo, antes de todo o resto do front-end.

## 6. Descartado

| Item | Origem | Motivo |
|---|---|---|
| Variante B inteira (linha de 18 px, severidade só por peso, filtro como linha de consulta, timeline de 28 px) | Análise 2 | 3 px de entrelinha com fonte de 12 px; Warning sublinhado não se reconhece sem legenda. Fica só a economia de colunas, absorvida em UI-16 |
| Selos cheios de alarme nas linhas do grid (variante C) | Análise 2 | No escuro dominam a tela e competem com a seleção; o fundo `--abn-row` basta (UI-07). O selo fica só no cabeçalho do detalhe (UI-20) |
| Linha de 24 px (variante C) | Análise 2 | A variante menos densa; falha VR-01 em ≤ 1366 px |
| Dimensões do handoff: barra 40, faixa 32, status 24, controle 28 px; timeline 64/160 | Análise 1 (UI-HANDOFF §2.3, P0-1 b, P1-4) | Substituídas pelas de R (36, 30, 22, 26; timeline 40/120), medidas com mensagem legível |
| Banner de erro global sobre o topo da coluna central | Análise 1 (P1-7, UI-HANDOFF §9) | Cobre a faixa de filtro (VR-04); substituído pela barra de status (UI-13) |
| Regra "três colunas a partir de 1280 px" | Análise 1 (UI-HANDOFF §3.1) | Não garante largura para a mensagem; substituída pelo trilho abaixo de 1500 px (UI-17) |
| Timeline com Info cinza empilhado na mesma escala do anormal | Análise 1 (P1-4) | O pico de erro continua um traço (protótipo A); substituída pela escala própria (UI-19) |
| Série Noise separada (`--chart-noise`) na timeline | Análise 1 (UI-HANDOFF §2.1) | Info e Verbose viram uma linha só (VR-05) |
| Alternância de densidade Compact/Comfortable | Análise 1 (D4 b) | Sem pedido de usuário; dobra testes de grid. Rever só se o UAT pedir |
| Casos recentes na lateral e no centro ao mesmo tempo | Análise 2 (1ª rodada) | Repetição; ficam só no centro (UI-22) |
| URL versionada com `immutable` | Análise 3 (VC-08) | Risco de instanciar o módulo duas vezes; ETag + `no-cache` dá o mesmo ganho no loopback (UI-03) |
| `content-visibility` nas linhas do pool | Análise 3 (VC-09) | As linhas já são janeladas; não ajuda |
| Bundler, minificação, tree-shaking | Análise 3 (§2) | Restrição do app (sem build); ganho pequeno no loopback |
| Itens de plataforma Vercel (deploy, env, CDN, edge, Functions, Storage, Flags, RUM, Next.js/React, shadcn/Tailwind, `cmdk`) | Análise 3 (§2) | App local, offline, sem framework nem telemetria; da paleta fica só o padrão em JS puro (UI-29) |
| Fonte mono vendorizada (OFL) | Análise 1 (D3 b) | Dependência nova sem evidência de problema; as larguras em `ch` (UI-16) absorvem a diferença entre SOs |
| Superdesign | Análise 2 (origem) | Exige login em serviço externo; descartado pelo Jefferson |

## 7. Decisões do Jefferson

| # | Decisão | Recomendação final | O que muda se a escolha for outra |
|---|---|---|---|
| D1 | Onde abrem os painéis | **Doca inferior com abas** (UI-15), com altura inicial pelo conteúdo e seleção mantida visível. Os protótipos confirmaram: 15 a 26 linhas de grid com a doca aberta | (b) coluna à direita no lugar do detalhe: UI-15 vira troca de conteúdo do detalhe (sem divisor horizontal), VR-02 e VR-09 deixam de valer, o detalhe perde espaço justo quando se analisa; UI-17 e UI-24 se adaptam. (c) manter acima do grid: só a UI-01, e o P0-2 fica sem solução |
| D2 | Termo da linha do log | **record** (UI-09) | "entry": inverter a UI-09 e reescrever o README e as mensagens da API, que já usam "record" |
| D3 | Fonte | **Pilhas do sistema** (UI-05), com larguras de coluna em `ch` (UI-16) | Fonte OFL vendorizada: `@font-face` em `tokens.css`, ~100 KB a mais, conferência de licença; o resto não muda |
| D4 | Densidade | **Revista: 20 px fixos** (UI-08). A análise 1 recomendou 22 px sem medir linhas; a análise 2 mediu +3 a +6 linhas com 20 px | 22 px: a UI-08 é fechada como não planejada; as metas de densidade (≥ 24, ≥ 18, ≥ 12 linhas) continuam atingíveis (R com 22 px daria cerca de 34, 27 e 25) |
| D5 | Cor de Info | **Neutra** (UI-05, UI-07, UI-19) | Azul: trocar `--sev-info` e `--chart-normal` (um token cada); a timeline continua com escala própria para o anormal |
| D6 | Menu de contexto na linha | **Sim**, junto da paleta de comandos, que usa o mesmo registro de ações (UI-29) | Não: a UI-29 fica só com a paleta; "Same key" segue só pelo detalhe (UI-24) |
| D7 | Atalhos de uma letra | **Ligados, com opção de desligar** (UI-28) | Desligados por padrão: muda um valor padrão; quem usa `n`/`t`/`b` precisa ligar no diálogo `?` |
| D8 | Escopo antes da 0.1.0 | **Revista: PRs 1 a 17 (L0 a L4, que fecham os 4 P0) mais UI-25 e UI-30 (defeitos que destroem contexto ou mostram números divergentes) e as dependências delas, UI-18 (faixa de filtro) e UI-21 (lateral)**. O resto entra depois, incremental | (b) tudo antes: a 0.1.0 e o UAT esperam os 30 PRs. Só L0 a L4, sem UI-25 e UI-30: o UAT encontra os dois defeitos já conhecidos |
| D9 (nova) | Cache do código estático no navegador | **Sim**, só para estáticos, com ETag e `no-cache` (UI-03); API e SSE continuam `no-store` | Não: a UI-03 sai; o orçamento de recarga (≤ 300 ms, só `304`) deixa de valer |

Decisões tomadas aqui com dados (não precisam do Jefferson, mas ficam registradas): dimensões de R (UI-05); timeline como faixa de alarme (UI-19); erro global na barra de status (UI-13); trilho abaixo de 1500 px somado às gavetas (UI-17); larguras em `ch` (UI-16); seleção com `box-shadow` + bloco `forced-colors` (UI-07); coluna `source.last_line` (UI-02, esquema definido pelo Opus).

## 8. Riscos

| Risco | Mitigação |
|---|---|
| `js/main.js` é tocado por 13 recomendações; conflito e regressão na integração | Contratos antes (UI-11); trechos aplicados só pela sessão principal; um PR por vez; testes de fluxo por painel |
| Números de densidade e largura vêm de protótipos estáticos (78 linhas, sem JS), não do app | Cada aceite repete a medida no app real com caso sintético; a UI-04 automatiza a parte de desempenho |
| Windows/Edge não verificado: fontes (Cascadia, Consolas, Segoe), ClearType, alto contraste real, antivírus lendo o `.pyz` | Larguras em `ch`; conferência no PC de destino no UAT da 0.1.0; captura com `forced-colors` emulado como mínimo |
| Contraste no limite no escuro (Critical sobre seleção 4,78 a 4,92; texto sobre destaque 4,78) | Teste de tokens com margem (≥ 4,7 no par mais justo) antes de qualquer ajuste de `--row-selected` ou `--hl-bg` |
| Mudar `ROW_HEIGHT` afeta a conversão tempo → rolagem e os testes de grid | PR isolado (UI-08), teste de igualdade token × constante, perfil de rolagem |
| Carregamento sob demanda quebra a tolerância a módulo opcional ou duplica módulos | Falha de `import()` vira item desabilitado; proibido misturar URL com e sem `?v=`; teste de fluxo por painel |
| Migração de esquema (`source.last_line`) em casos já indexados | Preenchimento na abertura ou `NULL` com fallback; teste com caso antigo; desenho do Opus registrado em ARQUITETURA |
| Doca e migração de 12 módulos de painel no mesmo período | Doca só muda a montagem (UI-15); a reestruturação interna vem depois (UI-23), um painel por PR se preciso |
| Escopo cresce e atrasa a 0.1.0 | D8 revista: só L0 a L4 + dois defeitos antes da 0.1.0 |
| Leitor de tela real não testado | NVDA no Windows obrigatório antes do merge da UI-26 |

## 9. Como este consolidado foi validado

- `issues.json` gerado a partir da mesma fonte de dados das seções 4 e 3 e validado com `python -m json.tool`.
- Conferido por script que cada ID de origem (P0-1 a P0-5, P1-1 a P1-13, P2-1 a P2-14, VR-01 a VR-11, VC-01 a VC-11, UX-01 a UX-16) aparece nas origens de pelo menos uma recomendação; D1 a D8 estão na seção 7.
- Conferido que toda dependência aponta para uma recomendação de número menor (a ordem dos PRs é executável), que as labels usadas são só as existentes e que nenhum corpo de issue contém caminho local, nome de usuário, nome de produto de terceiros ou dado real.

# Revisão de UI/UX do viewer (2026-10-14)

Revisão do viewer web (`src/dmslog/web/`) na `main` em `8969d94`. Objetivo: acabamento de ferramenta de análise usada por horas, sem perder densidade, reduzindo o tempo até a causa de um problema no log. Especificação de implementação (tokens, componentes, estados, layout): `docs/UI-HANDOFF.md`. Capturas: `docs/ui-review/`.

## 1. Método e evidências

- **Dados só sintéticos.** Dois ERS gerados por `tools/bench.py` (perfil Verbose, 6 adapters, 235.230 registros; perfil Info, 3 adapters, 49.863 registros) e o pack sintético `tests/fixtures/knowledge/mini_pack`. Nada de `local-samples/`. Nas capturas em que apareceu um caminho local (lista de arquivos e navegador de pastas) ou o caminho de pasta do gerador, a área foi coberta com uma tarja cinza.
- **Navegador.** A aba do Claude in Chrome ficou em segundo plano (`visibilityState=hidden`) e a captura de tela expirou duas vezes; por isso as capturas foram feitas com o Chrome *headless* do sistema, dirigido pelo protocolo DevTools com um script Node local (cliques, teclado, `prefers-color-scheme`, tamanho de janela e fator de escala). Equivale a uma janela real de Chrome; o que muda no Edge do Windows está na seção 13.
- **Tamanhos testados:** 1600×900 (base), 1920×1080, 1366×768, 900×800 (meia tela de 1920 com Snap), zoom 125% e 150% num monitor 1920×1080 (viewport CSS de 1536×864 e 1280×720) e 200% (800×450 CSS). Temas claro e escuro.
- **Medições** com `getBoundingClientRect` (alturas dos blocos) e contraste WCAG calculado a partir dos tokens de `css/app.css`.
- **Skills aplicadas:** direção estética (frontend-design), crítica estruturada, auditoria WCAG 2.1 AA, auditoria do sistema de design, revisão de textos e handoff.

| Captura | Estado |
|---|---|
| `01-sem-caso.png` | Sem caso aberto |
| `02-novo-caso-lista.png`, `03-indexando.png` | Criar caso com 1 ZIP; indexando |
| `04-caso-aberto-claro.png` | Grid com 235 mil registros, 1600×900 |
| `05-selecao-detalhe-claro.png`, `06-selecao-detalhe-escuro.png` | Seleção e detalhe, claro e escuro |
| `07-distinct-cabecalho-esmagado.png` | Distinct values aberto: cabeçalho do grid com 9 px |
| `08` a `17` | Painéis: agrupar por tipo, árvore de filtros, Operations, dashboard, configuração efetiva, payloads, alertas, exportar, destaques, agregar |
| `18-busca.png`, `19-visao-encadeada.png` | Busca com marcação; visão encadeada (View 2) |
| `20-detalhe-texto-colapsado.png` | Texto do registro com 18 px de altura quando há payload |
| `21-destaque-esconde-selecao.png` | Destaque amarelo apaga a cor da seleção |
| `22-zoom125.png`, `23-zoom150.png`, `24-1366x768.png`, `25-1920x1080.png` | Densidade por tamanho de tela |
| `26-janela-900.png`, `32-zoom200.png` | Sobreposição de grid e detalhe |
| `27-rolagem-pagina-fontes-ocultas.png` | Página inteira rola; lista de fontes com altura 0 |
| `28-navegador-pastas.png` | Navegador de pastas embutido |
| `29-conhecimento-no-fim.png` | Explicação do conhecimento no fim do detalhe |
| `30-erro-caminho.png` | Erro de caminho no banner do topo |
| `31-distinct-escuro.png` | Painel no tema escuro; coluna Collection |
| `33-mesma-chave.png` | Registros com a mesma chave (seguir um GUID) |

## 2. Resumo

O viewer já tem o que importa por baixo (grid virtual rápido, keyset, filtros ricos, conhecimento, correlação, payloads, visões encadeadas, segurança de DOM). O problema é de **arquitetura de tela**: cada funcionalidade nova ganhou um botão na barra e um painel empilhado acima do grid, e o grid, que é onde a análise acontece, ficou com o que sobra. Em 1366×768 (notebook corporativo comum) ou em 150% de zoom num monitor 1080p, **o grid mostra 5 linhas**; com qualquer painel aberto, o cabeçalho do grid encolhe para 9 px; depois de criar um caso, **a lista de fontes some** (altura 0) e a página inteira passa a rolar; num registro com payload, **o texto do próprio registro some** (18 px de altura).

As cinco mudanças de maior retorno, nesta ordem:

1. **Correções de layout P0** (S): `flex: none` nos blocos fixos da coluna central e do detalhe, rolagem própria do painel de fontes. Resolve 07, 20 e 27 sem redesenho.
2. **Doca de análise inferior** (L): painéis abrem numa área com abas e divisor arrastável abaixo do grid, em vez de empurrá-lo; o grid nunca fica com menos de 8 linhas.
3. **Barra superior de uma linha** (M): busca, ir para horário e menus agrupados (Analyze, Export, View) no lugar de 22 botões soltos; estado ativo visível.
4. **Cor só para anormal** (M): Info e Noise neutros na timeline e no grid; seleção com barra de destaque que nenhum destaque cobre; contraste AA em todos os pares.
5. **Detalhe em ordem de investigação** (M): cabeçalho estruturado, texto, "What it means", campos e chaves, payload; ações compactas no topo.

## 3. Achados P0 (quebram o uso)

### P0-1. O grid fica com uma fração da altura

- **Tela/estado:** caso aberto, nenhum painel. Capturas `04`, `24`, `22`, `23`.
- **Evidência:** em 1600×900 o grid tem 294 px de 813 (36%, 13 linhas). Em 1366×768 e em 150% de zoom, 120 px (`min-height` de `.grid-viewport`, 5 linhas). Acima do grid: barra (77 px em 2 linhas; 3 linhas em 1366), banner de mensagem (28 px), timeline (185 px), barra de visões (28 px), barra de filtros (79 px em 2 linhas); abaixo: status (22 px) e ajuda de atalhos em 3 linhas (54 px).
- **Problema:** a tarefa principal (ler registros em sequência) disputa altura com controles usados de vez em quando. Investigar com 5 linhas visíveis obriga a rolar a cada passo e perde o contexto em volta do erro.
- **Proposta:** (a) barra superior de uma linha com menus (P1-1); (b) timeline compacta por padrão, 64 px, com botão para expandir a 160 px (lembrado em `localStorage`); (c) filtros e trilha de visões numa única faixa de 32 px com resumo em fichas (P1-5); (d) ajuda de atalhos sai da tela e vira o diálogo `?` (P1-13); (e) mensagens numa barra de status fixa de 24 px no rodapé, sem deslocar o layout (P1-7). **Aceite:** sem painel aberto, o grid mostra pelo menos 24 linhas em 1600×900, 18 em 1366×768 e 12 em 1280×720 (150% de 1080p).
- **Esforço:** L. **Arquivos:** `index.html`, `css/app.css`, `css/timeline.css`, `js/timeline.js`, `css/views-stack.css`, `js/views-stack.js`, `js/main.js`.

### P0-2. Painéis empurram o grid e o cabeçalho do grid encolhe para 9 px

- **Tela/estado:** qualquer painel aberto (Distinct values, Operations, Payloads, Export, Aggregate, Compare, Same key). Capturas `07`, `10`, `13`, `15`, `17`, `33`.
- **Evidência:** os painéis entram em `#grid-panel` antes do filtro (`index.html:90-102` e `insertBefore` em `js/distinct-panel.js:377`, `js/aggregate-panel.js:582`, `js/highlight-panel.js:138`, `js/views-stack.js:73`), com `max-height: 40vh` (`css/grid-extras.css:24-29`). Com um painel aberto em 1600×900: painel 181 px, grid 120 px (5 linhas) e `#grid-header` com **9 px** (todos os filhos de `#grid-panel` têm `flex-shrink: 1`; `css/app.css:340-349` não fixa o cabeçalho). O próprio painel fica com 180 px: em Operations a coluna Name quebra letra por letra (`10`), em Export o botão de exportar fica abaixo da dobra (`15`), em Aggregate o resultado não aparece (`17`).
- **Problema:** abrir uma ferramenta de análise destrói a leitura do grid, e a ferramenta também não cabe. Não dá para ver o resultado e os registros ao mesmo tempo, que é o ponto do drill-down.
- **Proposta:** correção imediata (S): `flex: none` em `.grid-header`, `.filter-bar`, `#views-stack-bar`, `.timeline-host`, `.grid-status`. Solução (L): **doca de análise** abaixo do grid, com abas (um painel visível por vez, os abertos ficam como abas), divisor arrastável (teclado: setas com foco no divisor), altura padrão 40% da coluna, lembrada; o grid mantém no mínimo 8 linhas (176 px) e a doca no mínimo 160 px. Painéis largos (Operations, Dashboard, Aggregate, Compare) ganham botão "Maximize" que troca o grid pela doca até fechar. Os módulos já recebem `panelParent`/`toolbar` de `main.js`; passam a receber a doca (`dock.register({ id, title, host })`), sem mudar a lógica deles.
- **Esforço:** S (correção) + L (doca). **Arquivos:** `css/app.css`, `css/grid-extras.css`, novo `js/dock.js` e `css/dock.css`, `js/main.js`, `index.html`; nos módulos de painel, só a montagem.

### P0-3. Lista de fontes inalcançável depois de criar o caso

- **Tela/estado:** caso criado com 1 ou 2 coletas. Captura `27`.
- **Evidência:** `#sources-panel` não rola (`overflow: visible`); o conteúdo soma 1.214 px numa janela de 813 px; `#source-list` fica com **altura 0** (conteúdo de 2.342 px) e o documento inteiro rola, levando a barra superior junto. Acima da lista continuam visíveis o formulário de novo caso (229 px), o de abrir caminho e o de adicionar arquivos (269 px) e o painel de coletas (459 px).
- **Problema:** escolher fontes, que é o filtro mais usado num ERS com 28 a 44 arquivos, fica impossível sem rolar a página; ao rolar, a barra e o grid saem da tela.
- **Proposta:** correção imediata (S): `#sources-panel { overflow: auto }` e `#source-list { flex: 1 0 240px }`. Solução (M): o painel lateral mostra só o caso aberto (nome + botão "Add files…" + menu do caso), as coletas em lista compacta recolhível (uma linha por coleta: `C1`, servidor, contagem, aviso) e as fontes ocupando o resto. Criar caso e adicionar arquivos vão para um diálogo (P1-8).
- **Esforço:** S + M. **Arquivos:** `css/app.css:217-231` e `268-274`, `js/sources.js`, `css/collections.css`, `js/collections-panel.js`, `css/case-files.css`.

### P0-4. O texto do registro some no detalhe quando há payload ou links

- **Tela/estado:** registro com payload (XML) selecionado. Captura `20`.
- **Evidência:** `#detail-text` com 18 px de altura e 1.216 px de conteúdo; `#detail-panel` é coluna flex com `overflow: auto`, e o `pre` encolhe porque `max-height: 55vh` (`css/app.css:475-487`) não impede o `flex-shrink`.
- **Problema:** o texto original do registro é a evidência principal; ele aparece como uma faixa de uma linha cortada.
- **Proposta:** `#detail-panel > * { flex: none }` e `#detail-text { min-height: 6lh; max-height: 45vh; resize: vertical }` (S).
- **Esforço:** S. **Arquivos:** `css/app.css`.

### P0-5. Sobreposição de grid e detalhe em janela estreita e em zoom alto

- **Tela/estado:** 900×800 (meia tela de 1920 com Snap do Windows, uso comum ao lado do chamado) e zoom 200%. Capturas `26` e `32`.
- **Evidência:** no breakpoint de 1100 px (`css/app.css:535-546`) o detalhe vai para baixo com `max-height: 40vh`, mas o grid mantém `min-height: 120px` e transborda por cima do detalhe; em 200% a timeline cobre o detalhe e o botão "Create case" cobre o texto. Falha WCAG 1.4.10 (Reflow) e 1.4.4.
- **Proposta:** abaixo de 1280 px o detalhe vira **gaveta à direita** (sobreposta, largura 420 px, alternada por `Enter` no grid e `Esc`); abaixo de 960 px a barra lateral vira gaveta à esquerda (botão "Sources"). O grid sempre ocupa a coluna inteira. Teste de aceite: 900×800 e 200% sem sobreposição (captura antes/depois).
- **Esforço:** M. **Arquivos:** `css/app.css`, `js/main.js` (alternância das gavetas), `index.html` (botões).

## 4. Achados P1 (atrito forte)

### P1-1. Barra com 22 botões iguais, sem estado ativo

- **Evidência:** `01`, `24` (3 linhas em 1366), `32` (4 linhas em 200%). 22 botões de mesmo peso; `aria-expanded="true"` não tem estilo (nenhum seletor `[aria-expanded="true"]` nos CSS); os botões aparecem e ficam ativos sem caso aberto.
- **Problema:** não há hierarquia (Search e Theme têm o mesmo peso); não se sabe qual painel está aberto; o usuário novo lê 22 rótulos para achar uma ação.
- **Proposta:** barra de uma linha (40 px): nome do app · seletor de caso · busca (flexível, até 560 px) · ir para horário · espaçador · `Filter tree` · `Highlights` · `Bookmarks` (alternâncias com estado "pressed") · menu **Analyze** (Distinct values, Group by message type, Aggregate, Operations, Adapter dashboard, Payloads, Unknown message types, Effective configuration) · menu **Export** (Export report…, Copy as…) · menu **View** (Columns…, Follow tail, Theme, Shortcuts). Alerts entra como indicador com contagem só quando o servidor segue uma pasta. Sem caso: só caso, busca desabilitada com dica, e View.
- **Esforço:** M. **Arquivos:** `index.html:21-49`, `css/app.css:143-196`, `js/main.js`, os módulos que inserem botões na barra (`distinct-panel.js`, `aggregate-panel.js`, `highlight-panel.js`, `copy-export.js`), `js/strings.js`.

### P1-2. A seleção some sob destaques e sob a coluna Collection

- **Evidência:** `21` (linha selecionada com destaque amarelo: só um contorno fino indica a seleção), `27`/`31` (a célula C1 é azul, quase a cor da seleção; no escuro, `--col-1-bg #21447a` contra `--selected #24406b`).
- **Problema:** com destaques ativos, perde-se a linha em análise; a coluna Collection pinta a célula inteira e compete com seleção e severidade.
- **Proposta:** seleção = fundo `--row-selected` + **barra de 3 px** `--accent` à esquerda (inset `box-shadow`, sem custo de layout), por cima de qualquer destaque; destaque = faixa de 4 px à esquerda + fundo leve, sem trocar a barra; Collection = etiqueta pequena (`C1`) com ponto de cor, sem pintar a célula. Ordem de prioridade documentada no handoff.
- **Esforço:** S. **Arquivos:** `css/app.css:379-385`, `css/highlight.css`, `css/bookmarks.css`, `css/collections.css`.

### P1-3. A coluna Source esconde o que diferencia os arquivos

- **Evidência:** `04` e `25`: `AVLAdapter_1022.se…`, `AVLAdapter_1022.lo…`; o corte no fim esconde exatamente `.sensitive`, `.log` e o índice de rotação (`.log.0`).
- **Problema:** normal × sensitive é a distinção mais importante na correlação (M5); hoje só aparece no detalhe ou passando o mouse.
- **Proposta:** (a) indicador `S` (sensitive) numa coluna de 18 px ou como prefixo visual na célula; (b) reticências no meio (`AVLAdapter_10…sensitive.log.0`), calculadas uma vez por nome de fonte e largura de coluna (cache por `source_id` + largura; nada de medir texto por linha durante a rolagem); (c) `title` com o nome completo (já existe).
- **Esforço:** M. **Arquivos:** `js/grid.js` (`fillCells`), `js/grid-model.js` (função pura de corte, testada), `css/app.css`.

### P1-4. A timeline destaca o normal, não o anormal

- **Evidência:** `04`: Info (160 mil) em azul saturado ocupa quase toda a área; Error (20,6 mil) é uma faixa vermelha de 2 a 4 px na base.
- **Problema:** o histograma serve para achar picos de erro e lacunas; em escala linear com o normal colorido, os erros somem.
- **Proposta:** Info e Noise em cinza neutro (`--chart-normal`, `--chart-noise`), Warning, Error e Critical saturados e empilhados **na base**; botão "Errors only" na legenda (as legendas já alternam séries com `aria-pressed`); eixo com data ISO (`2030-01-15`, hoje `1/15/30`). Altura compacta (P0-1).
- **Esforço:** M. **Arquivos:** `css/timeline.css`, `js/timeline.js`, `js/timeline-model.js`.

### P1-5. Não se vê qual filtro está ativo

- **Evidência:** `19`: em "View 2" a busca volta vazia, os selects ficam em "Any", mas o grid continua filtrado por `text "Validation"`; a árvore de filtros, o filtro de fontes e as coletas não aparecem em lugar nenhum perto do grid.
- **Problema:** conclusões erradas ("não há erro") quando há um filtro esquecido; é o atrito mais caro numa investigação.
- **Proposta:** **faixa de filtro** de uma linha acima do grid: trilha de visões à esquerda (`All records › text "Validation" › View 2`) e, à direita, fichas do filtro efetivo da visão atual (`Severity ≥ Error ×`, `3 sources ×`, `C2 ×`, `text "Validation" ×`, `Filter tree (4 conditions)`), cada uma removível; Severity, Level, From e To ficam num popover "Filters" que monta essas fichas. Contagem do filtro sempre visível no status.
- **Esforço:** M. **Arquivos:** `js/views-stack.js`, `js/filters.js`, `css/views-stack.css`, `index.html:103-124`, `js/main.js`, `js/strings.js`.

### P1-6. Detalhe fora da ordem de investigação

- **Evidência:** `05`, `20`, `29`. A linha de metadados é um texto corrido com " - "; "Copy text" e "Compare with..." quebram em 2 linhas; o botão do título do template ocupa a largura toda e parece link; **"What it means"** (conhecimento) vem depois de Bookmark e de dois botões de destaque; as chaves (para seguir um GUID) ficam no fim, abaixo do payload.
- **Proposta:** ordem: (1) cabeçalho: horário em mono, selo de nível/severidade, fonte com selo `sensitive`, PID/TID, coleta; (2) barra compacta de ações com ícone + rótulo curto (`Context`, `Copy`, `Compare`, `Bookmark`, `Highlight ▾`); (3) título do tipo de mensagem; (4) texto; (5) **What it means** e nota do vault; (6) campos e **chaves** (fichas clicáveis "Same key"); (7) links normal↔sensitive; (8) payload recolhível; (9) nota do bookmark. Seções com cabeçalho recolhível e estado lembrado.
- **Esforço:** M. **Arquivos:** `index.html:135-160`, `js/detail.js`, `css/app.css:467-532`, `css/knowledge.css`, `css/links.css`, `css/payload.css`, `css/bookmarks.css`, `js/highlight-panel.js` (onde monta a seção).

### P1-7. Mensagens globais deslocam o layout, persistem e ficam longe da ação

- **Evidência:** `03`, `30`, `19`. O banner `#message` (`index.html:51`) aparece e some empurrando tudo 28 px; "Case created and 1 file(s) opened." fica até a próxima mensagem; "Set a filter in this view first, then narrow it." aparece no topo ao clicar num botão 200 px abaixo; o erro "Path does not exist" fica longe do campo.
- **Proposta:** **barra de status** fixa no rodapé (24 px, `role="status"`, `aria-live="polite"`): contagem, posição, progresso de indexação e a última mensagem (sucesso some em 8 s); erros de formulário **junto do campo** (`aria-describedby` + `aria-invalid`); erros globais (403, rede) num banner fixo com botão fechar, sem deslocar o grid (sobreposto ao topo da coluna central).
- **Esforço:** M. **Arquivos:** `index.html`, `js/main.js:144-150`, `js/sources.js`, `js/case-files.js`, `js/views-stack.js`, `css/app.css`.

### P1-8. Três caminhos para abrir arquivos visíveis ao mesmo tempo

- **Evidência:** `02`, `04`. Com caso aberto, o painel lateral mostra o formulário de novo caso (com a lista e "Create case" desabilitado), o campo de caminho com escopo e "Open", e "Add ERS files to this case" com outro "Add" e "Add files". "Add" (incluir caminho na lista) e "Add 1 file(s) to the case" (confirmar) usam o mesmo verbo para ações diferentes. "Create case" desabilitado tem contraste 2,05:1 (opacidade 0,5).
- **Proposta:** sem caso: estado vazio no centro (P2-4) com **New case…** e a lista de casos recentes. Com caso: botão **Add files…** abre um diálogo com o componente `CaseFiles` (modo add) e uma seção "Folder or single file" (campo + escopo Default/All logs) que substitui o formulário "Open". "New case…" fica no menu do seletor de caso. Na lista: "Add to list" e "Add 2 files to case".
- **Esforço:** M. **Arquivos:** `index.html:55-83`, `js/sources.js`, `js/case-files.js`, `css/case-files.css`, `js/strings.js`.

### P1-9. Painéis sem padrão

- **Evidência:** `07` a `17`: alguns têm "Close" (Payloads, Alerts, Compare, Same key), outros não (Distinct, Operations, Dashboard, Export, Aggregate); títulos em 3 tamanhos; "Refresh" em posições diferentes; ação principal abaixo da dobra (Export, Aggregate). 16 módulos repetem `-head`, `-title`, `-status`, `-empty`, `-error` com estilos próprios (`.dst-head`, `.ev-toolbar`, `.agg-head`, `.hl-head`, ...).
- **Proposta:** componente **panel** comum (cabeçalho fixo: título, resumo curto, ações à direita, `Close`; corpo com rolagem; rodapé fixo para a ação principal), e componentes **status**, **empty**, **error** (handoff, seção 5). Os painéis migram um a um.
- **Esforço:** M (componente) + S por painel. **Arquivos:** novo `css/components.css`; CSS e montagem de cada painel.

### P1-10. Contraste abaixo de AA em pares usados o tempo todo

| Par | Claro | Escuro | Mínimo |
|---|---|---|---|
| Noise/VERBOSE (`--sev-5`) sobre o painel | 3,10 | 3,59 | 4,5 |
| `--sev-5` sobre a seleção | 2,36 | 2,26 | 4,5 |
| `--sev-5` sobre hover (`--panel-alt`) | 2,71 | 3,13 | 4,5 |
| Warning (`--sev-3`) sobre a seleção | 3,70 | 6,19 | 4,5 |
| Critical (`--sev-1`) sobre a seleção | 5,57 | 3,80 | 4,5 |
| Borda de campo (`--border`) sobre o painel | 1,48 | 1,58 | 3,0 (1.4.11) |
| Botão primário desabilitado (texto/fundo) | 2,05 | 2,45 | sem mínimo; ilegível |

- **Proposta:** paleta do handoff (todos os pares acima ≥ 4,5:1; bordas de controle ≥ 3:1) e um teste node que verifica os pares, no padrão de `tests/web/highlight-contrast.test.mjs`.
- **Esforço:** S (tokens) + S (teste). **Arquivos:** `css/app.css:1-77`, novo `tests/web/tokens-contrast.test.mjs`.

### P1-11. Leitor de tela não ouve o conteúdo do grid

- **Evidência:** `js/grid.js:537`: cada linha tem `aria-label="Entry {id}"`, que substitui o conteúdo das células; o cabeçalho (`#grid-header`, `role="row"`) está fora do `role="grid"`; `aria-rowcount` vai no contêiner das linhas e vale o tamanho da janela carregada, não o total; não há `aria-activedescendant` nem foco por linha (o foco fica no viewport); `#grid-status` (`aria-live="polite"`) muda a cada rolagem ("Showing from ...") e gera fala contínua.
- **Proposta:** tirar o `aria-label` da linha (bookmark e destaque viram texto oculto numa célula); `aria-rowindex` por linha e `aria-rowcount` total (ou `-1` quando estimado) no `role="grid"`; cabeçalho dentro do grid (`role="rowgroup"`); `aria-activedescendant` apontando para a linha selecionada (ids estáveis por slot); região live só para mudanças de contagem e de filtro, não para posição.
- **Esforço:** M. **Arquivos:** `js/grid.js`, `index.html:125-131`, `tests/web/` (fluxo de grid).

### P1-12. Teclado: muitas paradas, atalhos invisíveis e sem desligar

- **Evidência:** 24 Tabs até o primeiro campo do painel lateral; sem link para pular para o grid; nenhum atalho para focar a busca; atalhos de uma letra (`n`, `t`, `b`, `c`, `[`, `]`) globais em `document` (`js/main.js:1028`, `1244`; `js/highlight-panel.js:332`), sem opção de desligar (WCAG 2.1.4); `n` sem seleção responde "Select an entry first." em vez de começar do topo; a ajuda de atalhos ocupa 3 linhas fixas.
- **Proposta:** `F6`/`Shift+F6` alterna regiões (barra, lateral, grid, detalhe, doca); `/` foca a busca; `?` abre o diálogo de atalhos (com opção "Single-key shortcuts: on/off", lembrada); `n`/`N` sem seleção começam do primeiro/último registro visível; link "Skip to log entries" visível no foco.
- **Esforço:** M. **Arquivos:** `js/main.js`, `index.html`, novo `js/shortcuts.js` (+ modelo puro testado), `js/strings.js`.

### P1-13. Seguir um GUID exige rolar o detalhe e o resultado perde contexto

- **Evidência:** `25`, `33`. As fichas de chave ficam no fim do detalhe (abaixo de um payload de 600 px); o painel "Records with the same key" mostra o **caminho completo** da fonte (em vez do nome do arquivo, decisão da 9.19), o **código** do template (`GEN-PLD-01`, contra a mesma decisão), horário quebrado em 3 linhas e "1 records".
- **Proposta:** chaves no bloco 6 do detalhe (P1-6) e também como ação no menu de contexto da célula (clique direito ou `Shift+F10` na linha: "Same key ▸ msgid = …"); painel de mesma chave na doca, com as colunas do grid (Time, Source basename + selo, Level, Message type, Message) e "Show in grid" que cria uma visão encadeada com o filtro da chave.
- **Esforço:** M. **Arquivos:** `js/key-panel.js`, `css/key-panel.css`, `js/links.js`, `js/strings.js`.

## 5. Achados P2 (polimento)

| Id | Tela | Problema | Proposta | Esforço | Arquivos |
|---|---|---|---|---|---|
| P2-1 | Todas | 11 tamanhos de fonte, inclusive `0.9em` (28×) e `0.85em` (12×) que se acumulam em elementos aninhados; 6 raios (2, 3, 4, 6, 8, 10 px); 25 combinações de `padding`; `z-index` 10, 20 e 1000 soltos | Escala do handoff (4 tamanhos, 3 raios, espaçamento em múltiplos de 4, camadas nomeadas) | M | todos os `css/*.css` |
| P2-2 | Todas | Mono em duas pilhas: 23 regras usam `ui-monospace, monospace`, 3 usam `ui-monospace, "Cascadia Mono", Consolas, monospace`; no Windows, a primeira cai na fonte mono padrão do navegador | Tokens `--font-mono` e `--font-ui`; números com `font-variant-numeric: tabular-nums` | S | todos os `css/*.css` |
| P2-3 | Todas | Tokens repetidos 3 vezes em cada arquivo (claro, `@media` escuro, `[data-theme=dark]`); 7 paletas paralelas (`--tl-c*`, `--agg-c*`, `--hl-*`, `--bm-*`, `--ev-h-*`, `--cmp-*`, `--col-*`); fallback solto em `css/links.css:84` | `css/tokens.css` único (primitivos + semânticos), paletas derivadas de uma paleta categórica única; teste node que confere que o bloco `@media` e o `[data-theme=dark]` são idênticos | M | `css/*.css`, `index.html`, `tests/web/` |
| P2-4 | Sem caso | Grid vazio com cabeçalho, filtros, trilha (Back/Forward) e timeline com o select "Severity" sozinho; nada orienta (`01`) | Estado vazio no centro: "Open an ERS to start" + **New case…** + casos recentes (nome, coletas, data) + dica "Paste a path or use Browse"; filtros, trilha e timeline escondidos sem caso | S | `index.html`, `js/main.js`, `css/app.css` |
| P2-5 | Navegador de pastas | Nomes de ZIP cortados no fim: some a parte que distingue (data/hora da captura, Enterprise/Sensitive) (`28`) | Reticências no meio + selo Enterprise/Sensitive (o tipo já é calculado em `case-files-model.js`) | S | `js/folder-browser.js`, `css/case-files.css` |
| P2-6 | Compare | Cabeçalho mostra "Record 14941", caminho completo e código do template; o diff fica abaixo da dobra | Cabeçalho A/B com horário, fonte (nome), tipo de mensagem; diff primeiro; na doca | S | `js/compare.js`, `css/bookmarks.css` |
| P2-7 | Coletas | "Remove collection" com o mesmo peso das outras ações, dentro de cada cartão | Menu `⋯` do cartão com "Remove C2…" (a confirmação em dois passos já existe) | S | `js/collections-panel.js`, `css/collections.css` |
| P2-8 | Tema | Botão "Theme: light" não diz se é o estado ou a ação; não há "System" | Menu View → Theme: System / Light / Dark (radio); `theme.js` já resolve o padrão do sistema | S | `js/theme.js`, `js/main.js`, `js/strings.js` |
| P2-9 | Payload | XML largo corta à direita sem opção de quebra (`20`) | Alternância "Wrap lines" (lembrada) | S | `js/payload.js`, `css/payload.css` |
| P2-10 | Timeline | Texto fixo "Drag to select a time range, double-click to clear." ocupa uma linha | Vai para o `title` da área do gráfico e para o diálogo de atalhos | S | `js/timeline.js` |
| P2-11 | Lista de fontes | Meta "BLTHOST - pid - - 3,508 records" quando não há PID (`25`) | Omitir o segmento ausente | S | `js/sources.js:267-271` |
| P2-12 | Progresso | Ao terminar a indexação fica ": 235,230 records" (fonte nula em `S.progressLine`, `js/sources.js:404-407`) | "Indexed 235,230 records." no fim; durante, "{source}: {records} records" | S | `js/sources.js`, `js/strings.js` |
| P2-13 | Severidade | Opções "Noise or worse", "Info or worse": "worse" com Noise soa estranho; nível e severidade lado a lado confundem | "At least Error", "At least Warning", ..., "All"; Level em popover "Advanced" | S | `js/strings.js`, `js/main.js:164-165` |
| P2-14 | Operations | Rótulo "Search" sem campo ao lado e campo "Key or text" na linha seguinte (`10`) | Rótulo associado ao campo; filtros em uma linha na doca | S | `js/events.js`, `css/events.css` |

## 6. Fluxos de ponta a ponta

| Fluxo | Hoje (passos e atritos) | Depois |
|---|---|---|
| Abrir o ERS e achar a causa de um erro | Nome do caso, colar caminho, **Add**, rolar até **Create** (4 ações). Erros: Severity "Error or worse" + **Apply** (3) ou `n`, que exige selecionar uma linha antes. No detalhe, rolar até "What it means" (abaixo de bookmark e destaques). Com 5 a 13 linhas visíveis, cada `n` perde o contexto anterior. | **New case…** (diálogo: nome, Browse/colar, Create). `n` começa do topo. Detalhe mostra texto e "What it means" sem rolar. Grid com 18 a 24 linhas. |
| Seguir um GUID entre fontes | Selecionar, rolar o detalhe até "Keys" (depois do payload), clicar a ficha; o painel abre acima do grid e o empurra; resultado com caminho completo e código de template; voltar ao grid exige fechar o painel. | Ficha de chave no bloco de campos ou clique direito na linha; resultado na doca com as colunas do grid; "Show in grid" vira visão encadeada. |
| Comparar duas coletas | Abrir o caso, "Add ERS files to this case" (rolar), Add, Add files; marcar C1/C2 no painel de coletas (que empurra a lista de fontes para fora da tela); coluna Collection pinta células inteiras. | "Add files…" no topo do painel lateral; coletas compactas com caixas; Collection como etiqueta; filtro de coleta aparece como ficha na faixa de filtro. |
| Exportar | Botão Export abre painel de 180 px; o botão de exportar fica abaixo da dobra; "Copy as" é outro botão com outro painel. | Menu Export → "Export report…" e "Copy as…" num diálogo com a ação principal fixa no rodapé. |

## 7. Descobribilidade

- Só por atalho ou escondido: `t`/`T` (próximo do mesmo tipo), `[`/`]` (próximo destaque), `Alt+←/→` (visões), `c` (comparar), duplo clique na timeline para limpar, Enter no cabeçalho da coluna para ajustar a largura, fichas de chave no fim do detalhe, "Same key".
- Usuário novo não acha: que a busca e as visões se combinam (a busca muda a raiz da trilha, `18`/`19`); que "Narrow this view" exige um filtro antes; que as coletas filtram o grid (a caixa fica longe do grid).
- Disputam espaço: timeline × painéis × filtros × trilha na coluna central; formulários de caso × coletas × fontes na lateral; ações × conteúdo no detalhe.
- Proposta: menu de contexto na linha do grid (Same key, Only this source, Hide this message type, Bookmark, Compare, Copy) espelhando os atalhos e mostrando a tecla ao lado do rótulo; diálogo `?`; dicas curtas nos estados vazios.

## 8. Acessibilidade (WCAG 2.1 AA)

| # | Critério | Achado | Severidade | Correção |
|---|---|---|---|---|
| 1 | 1.4.3 | Noise/VERBOSE, Warning e Critical sobre seleção e hover abaixo de 4,5:1 (P1-10) | Alta | Paleta do handoff + teste |
| 2 | 1.4.11 | Bordas de campos e botões 1,48:1 (claro) e 1,58:1 (escuro) | Alta | `--line-strong` ≥ 3:1 |
| 3 | 1.4.10, 1.4.4 | Sobreposição a 200% e em 900 px (P0-5) | Alta | Gavetas |
| 4 | 4.1.2, 1.3.1 | Linhas do grid anunciadas como "Entry {id}"; cabeçalho fora do grid (P1-11) | Alta | ARIA do grid |
| 5 | 2.1.4 | Atalhos de uma letra globais sem desligar (P1-12) | Média | Opção on/off |
| 6 | 2.4.1 | Sem link de pular para o conteúdo; 24 Tabs até a lateral | Média | Skip link + F6 |
| 7 | 1.4.1 | Painel aberto só indicado por `aria-expanded`, sem pista visual; seleção indicada só por cor sob destaque | Média | Estado pressed; barra de seleção |
| 8 | 4.1.3 | `#grid-status` live anuncia a posição a cada rolagem | Média | Live só para contagem/filtro |
| 9 | 3.3.1 | Erros de formulário longe do campo, sem `aria-invalid` | Média | Erro junto ao campo |
| 10 | 2.4.7 | Foco visível OK (2 px `--focus`), mas some sobre a seleção no escuro em botões de linha | Baixa | Anel duplo (`outline` + `box-shadow` do fundo) |
| 11 | 2.5.8 (2.2, referência) | Alvos de 28 px de altura OK para mouse; fichas de 20 px e "+" de coleta com 24 px | Baixa | Mínimo 24 × 24 px |

O que já está certo: rótulos em todos os `input`/`select` visíveis (nenhum sem nome), `aria-label` nos botões "+" das coletas, Esc fecha o navegador de pastas e devolve o foco, `role="status"` nas mensagens, regiões `banner` e `main`, `lang="en"`.

## 9. Auditoria do sistema de design

| Categoria | Definido | Valores soltos encontrados |
|---|---|---|
| Cor | 21 tokens base × 3 blocos + 7 paletas de módulo | 151 cores literais em 8 arquivos (`timeline.css` 36, `bookmarks.css` 30, `aggregate.css` 24, `highlight.css` 24, `events.css` 18, `collections.css` 17), quase todas dentro de blocos de tokens próprios do módulo |
| Tipografia | 1 família UI, 2 pilhas mono | 11 tamanhos (px e em misturados) |
| Espaçamento | nenhum token | 25 `padding`, 10 `gap` distintos |
| Raio | nenhum token | 6 valores |
| Elevação | nenhuma | menus sem sombra (`.menu`, `css/app.css:183-196`), diálogo com `rgba(0,0,0,.45)` |
| Densidade | `--row-height: 22px` | `ROW_HEIGHT = 22` duplicado em `js/grid-model.js:4` |

Componentes repetidos sem base comum: painel (16 variações), ficha/selo (`.badge`, `.pl-chip`, `.lnk-chip`, `.hl-chip`, `.key-tag`, `.col-tag`, `.cfg-badge`, `.ev-badge`, `.kn-badge`, `.pr-badge`), tabela de painel (Distinct, Operations, Dashboard, Payloads, Same key), estados (`-status`, `-empty`, `-error`, `-loading` em cada módulo). Proposta completa em `docs/UI-HANDOFF.md`.

## 10. Textos (`strings.js`, 1.260 chaves)

**Terminologia (decidir uma e aplicar em tudo):**

| Conceito | Hoje | Proposta |
|---|---|---|
| Linha do log | "entry" (24) e "record" (53) misturados; o grid diz "235,230 entries", o README e as coletas dizem "records" | **record** (o README e a API já usam) |
| Tipo de mensagem | "template" (20: "Unknown templates", "The selected entry has no template", ajuda de atalhos) e "message type" (9) | **message type** na UI; "template" só em texto de desenvolvedor |
| Painel de eventos | "Operations" (botão) e "events" (mensagens de erro, "CSV of operations (events)") | **operations** |
| Reticências | "..." (30) e "…" (7) | **…** |
| Plural | "(s)" em 17 textos ("Create case with 1 file(s)") | Duas chaves (`_one`/`_other`) e `format` escolhe pelo `n` |

**Reescritas (amostra; a lista completa vai junto da tarefa de textos):**

| Chave | Hoje | Proposta |
|---|---|---|
| `themeLight`/`themeDark` | "Theme: light" | Menu: "Theme" → "System", "Light", "Dark" |
| `progressLine` (fim) | ": 235,230 records" | "Indexed 235,230 records." |
| `severityAtLeast` | "{name} or worse" | "At least {name}" |
| `cfCreateWith` | "Create case with {n} file(s)" | "Create case ({n} file)" / "Create case ({n} files)" |
| `cfAdd` | "Add" | "Add to list" |
| `cfAddWith` | "Add {n} file(s) to the case" | "Add {n} files to case" |
| `navNoSelection` | "Select an entry first." | (some: `n` começa do topo) |
| `navNoTemplate` | "The selected entry has no template." | "This record has no message type." |
| `plReason` | "Shown as is: {reason}" (mostra `no_operation_for_key`) | Mapear cada código para frase: "Not linked to an operation (no matching key)." |
| `cfgNoProducts` | "No stored configuration in this case (no Registry in the collection?)." | "No configuration found. The ERS has no Registry export." |
| `cmpButton` | "Compare with..." | "Compare…" |
| `keyCount` | "{count} records" ("1 records") | Plural por chave: "1 record" / "{count} records" |
| `toolbarUnknown` | "Unknown templates" | "Unknown message types" |
| `colApproxOrderMore` | 4 frases | "Collections come from servers with different clocks. Order across collections is approximate; inside one collection it is exact." |
| `*Error` ("Could not load the events", 25 variações) | Sem próximo passo | "Could not load operations. {reason} Try Refresh." (motivo do servidor quando houver) |
| `shortcutsHelp` | Parágrafo fixo | Conteúdo do diálogo `?`, em tabela (tecla, ação) |

## 11. Pontos fortes (não mudar)

- Grid virtual com pool de linhas, keyset e `ROW_HEIGHT` fixo: rolagem fluida com 285 mil registros; nada nesta proposta mede texto por linha nem força layout na rolagem.
- Segurança de DOM: só `textContent`, marcação de busca com `<mark>` criado por DOM; manter em todos os componentes novos.
- Estado preservado ao recarregar (caso, trilha de visões, filtro).
- Drill-down "Only"/"Hide" em Distinct values; links de operação no dashboard; arrastar na timeline para filtrar.
- "What it means" do conhecimento, selos `curated` nos presets, aviso de relógio entre coletas, mensagens de erro específicas ("Path does not exist: …").
- Payload com Formatted/Raw/Copy; selo `sensitive`; "+N lines" no grid.
- Navegador de pastas: teclado completo, Esc, seleção entre pastas, "Show hidden".
- Teste de contraste dos destaques (`tests/web/highlight-contrast.test.mjs`): estender o padrão aos tokens.
- Larguras de coluna redimensionáveis e lembradas (9.18); coluna Source no lugar do template (9.19).

## 12. Direção visual e plano

Direção: **console de sala de controle**. Os usuários vêm do mundo do ADMS/SCADA, em que as telas de alto desempenho (filosofia ISA-101) usam fundo neutro e reservam a cor para o anormal. Aplicado aqui: superfícies cinza neutras de baixo brilho, texto de alto contraste, Info e Noise sem cor, cor saturada só para Warning, Error e Critical, um único azul de interação (foco, seleção, ação principal). Tipografia de sistema para a interface e mono para dados, com números tabulares. Densidade alta e estável. Detalhes, tokens e componentes: `docs/UI-HANDOFF.md`.

### 12.1 Lotes de implementação

Regra: um PR por vez a partir da `main` atual; dentro de um lote, tarefas com arquivos disjuntos; **pontos de integração com dono único, a sessão principal**: `js/main.js`, `js/strings.js`, `js/api.js`, `index.html`, `docs/ARQUITETURA.md` (tabela de mudanças na seção 15 e numeração), `docs/ROADMAP.md`. As tarefas entregam no worktree o código do próprio módulo e um **trecho** para cada ponto de integração (chaves novas de `strings.js`, ligação em `main.js`, marcação em `index.html`) descrito no relatório; a sessão principal aplica.

Aceite comum a todos os PRs: `node --test tests/web/` e `pytest` verdes em Linux e Windows (CI); capturas antes/depois (sintéticas) dos estados tocados em `docs/ui-review/` ou anexas ao PR; nenhum `innerHTML` novo (`tests/unit/test_conventions.py`); nenhum texto visível fora de `strings.js`; rolagem do grid sem regressão (inspecionar com o Performance do DevTools: sem *forced reflow* no `scroll`).

| Lote | Tarefa | Modelo | Arquivos (exclusivos da tarefa) | Aceite verificável |
|---|---|---|---|---|
| **L0** (1 PR) | UX-01 Correções P0 de layout | sonnet | `css/app.css`, `css/grid-extras.css` | Com Distinct aberto em 1600×900, `#grid-header` = 22 px; detalhe com payload: `#detail-text` ≥ 6 linhas; caso com 2 coletas: `#source-list` visível e `document.scrollingElement.scrollHeight === innerHeight` |
| **L1** (paralelo, 1 PR) | UX-02 `css/tokens.css` (primitivos + semânticos, claro/escuro) e migração de `app.css` para os tokens | sonnet | novo `css/tokens.css`, `css/app.css` | Teste de contraste dos pares do handoff ≥ 4,5:1 (texto) e ≥ 3:1 (bordas, gráficos); blocos `@media` e `[data-theme=dark]` iguais |
| | UX-03 Teste de tokens | haiku | novo `tests/web/tokens-contrast.test.mjs` | Falha se um par cair abaixo do mínimo |
| | UX-04 Inventário de textos (terminologia, "(s)", "...", erros) com proposta por chave | haiku | novo `docs/notas/ux-copy-inventario.md` (só leitura do código) | Lista de chaves × texto novo; a sessão principal aplica no `strings.js` |
| **L2** (1 PR) | UX-05 Componentes comuns: botão (primário, secundário, fantasma, perigo, alternância), campo, selo/ficha, painel, status/vazio/erro, menu | sonnet | novo `css/components.css` | Página de demonstração só em teste (`tests/web/` com DOM simulado) ou captura; estados hover/foco/desabilitado/pressed com contraste ≥ 3:1 |
| **L3** (1 PR) | UX-06 Barra de uma linha com menus Analyze/Export/View e estado ativo | sonnet | novo `js/toolbar-menu.js` (+ `toolbar-menu-model.js` testado), `css/toolbar.css`; trechos para `index.html`/`main.js`/`strings.js` e para os módulos que inserem botões | Barra com 1 linha em 1366 px; `aria-expanded`/`aria-pressed` com estilo; menus com setas, Esc e foco de retorno |
| **L4** (1 PR) | UX-07 Doca de análise (abas, divisor, maximizar) e migração da montagem dos painéis | opus (desenho da API da doca) + sonnet (implementação) | novos `js/dock.js`, `js/dock-model.js`, `css/dock.css`; nos módulos de painel, só a função de montagem | Com qualquer painel aberto em 1366×768, grid ≥ 8 linhas; divisor por teclado; altura lembrada |
| **L5** (paralelo, 1 PR por tarefa, em série) | UX-08 Faixa de filtro (trilha + fichas) | sonnet | `js/views-stack.js`, `css/views-stack.css`, novo `js/filter-chips.js` (+ modelo testado) | Toda condição ativa aparece como ficha removível; contagem visível |
| | UX-09 Timeline compacta e cores neutras | sonnet | `js/timeline.js`, `js/timeline-model.js`, `css/timeline.css` | 64 px por padrão; erros na base; eixo com data ISO |
| | UX-10 Detalhe reordenado | sonnet | `js/detail.js`, `css/knowledge.css`, `css/links.css`, `css/payload.css` | Ordem do handoff; texto e "What it means" visíveis sem rolar em 1600×900 |
| | UX-11 Lateral: caso compacto, diálogo "Add files…", coletas recolhíveis | sonnet | `js/sources.js`, `js/case-files.js`, `js/collections-panel.js`, `css/case-files.css`, `css/collections.css` | Fontes com ≥ 60% da altura da lateral com 2 coletas |
| **L6** (1 PR) | UX-12 Grid: seleção com barra, Source com reticências no meio e selo S, ARIA do grid | sonnet | `js/grid.js`, `js/grid-model.js`, `css/highlight.css`, `css/bookmarks.css` | Seleção visível com destaque; leitor de tela anuncia células; teste puro do corte no meio |
| **L7** (1 PR) | UX-13 Teclado e atalhos (F6, `/`, `?`, desligar letras, skip link) | sonnet | novos `js/shortcuts.js`, `js/shortcuts-model.js` | Teste de fluxo do diálogo; 2.1.4 atendido |
| | UX-14 Gavetas em janela estreita e zoom | sonnet | `css/app.css` (seção responsiva, depois do L0) | 900×800 e 200% sem sobreposição |
| **L8** | UX-15 Barra de status e mensagens junto do campo | sonnet | novo `js/status-bar.js`, `css/status-bar.css` | Sem deslocamento de layout ao mostrar mensagem; erros com `aria-invalid` |
| | UX-16 P2 restantes (navegador de pastas, compare, coletas, tema, payload wrap, fontes sem PID, progresso) | haiku/sonnet | um arquivo de módulo por item | Captura antes/depois de cada item |

Dependências: L0 antes de tudo; L1 antes de L2; L2 antes de L3, L4, L5 e L6; L4 antes de UX-13 (P1-13, painel de mesma chave na doca); UX-14 depois de L4 (as gavetas usam a mesma estrutura de colunas). Registro na `docs/ARQUITETURA.md` (seção 11, linha "Viewer"; seção 15, registro de mudanças) e numeração no `ROADMAP` ficam com a sessão principal, uma vez por PR.

## 13. Decisões que dependem do Jefferson

| # | Decisão | Opções | Recomendação |
|---|---|---|---|
| D1 | Onde abrem os painéis de análise | (a) doca inferior com abas; (b) coluna à direita trocando o detalhe; (c) manter acima do grid | **(a)**: preserva a largura do grid e o detalhe ao mesmo tempo; o detalhe continua à direita |
| D2 | Termo da linha do log | "record" ou "entry" | **record** (README, API e coletas já usam) |
| D3 | Fonte | (a) pilhas do sistema (Segoe UI/Cascadia Mono/Consolas no Windows); (b) vendorizar uma mono livre (OFL) para ficar igual em Windows e Linux | **(a)**: zero dependência nova; Cascadia Mono e Consolas existem no Windows 10/11; rever só se o UAT mostrar diferença incômoda |
| D4 | Densidade | (a) fixa em 22 px; (b) alternância Compact 20 / Comfortable 24 | **(a)** agora; (b) só se houver pedido no UAT (muda `ROW_HEIGHT` e testes de grid) |
| D5 | Cor de Info no grid e na timeline | (a) neutra (proposta, cor só para anormal); (b) manter azul | **(a)** (opinião de direção, ancorada em ISA-101; reversível por token) |
| D6 | Menu de contexto na linha do grid | sim/não | **sim**: torna descobríveis os atalhos e o "Same key" |
| D7 | Atalhos de uma letra | (a) ligados por padrão, com opção de desligar; (b) desligados por padrão | **(a)** |
| D8 | Escopo antes da 0.1.0 | (a) L0 a L4 antes da 0.1.0 e o resto depois; (b) tudo antes | **(a)**: L0 a L4 resolvem os P0 e o atrito principal; o restante é incremental |

## 14. O que ficou sem evidência visual

- **Edge no Windows**: capturas só no Chrome do Linux. Conferir no PC de destino: fonte mono efetiva nas regras `ui-monospace, monospace` (P2-2), renderização de Segoe UI e o diálogo nativo de arquivos.
- **Diálogo nativo** (zenity/kdialog/Windows): não aberto, para não abrir janela na área de trabalho; o navegador embutido foi aberto direto pelo módulo, apontando para a pasta sintética.
- **Follow tail, alertas disparando e banner de alerta**: exigem `serve --watch` com escrita contínua; vistos só o painel vazio e o código.
- **Bookmarks com nota, side-by-side normal↔sensitive, preset aplicado, Unknown templates com dados reais de adapter novo**: abertos rapidamente ou só por código; os achados comuns de painel (P1-9) valem para eles.
- **Leitor de tela real (NVDA)**: auditoria por código e árvore de acessibilidade; testar com NVDA no Windows antes de fechar o L6.
- **Desempenho com milhões de linhas**: não medido nesta revisão (caso de 285 mil registros); o critério de aceite do L6 pede perfil de rolagem no caso de 2 GB do `tools/bench.py`.

# Análise 3 de UI/UX: plugin Vercel (desempenho de front-end e verificação ponta a ponta)

Viewer web (`src/dmslog/web/`) na `main` em `8969d94`, analisado em 2026-10-14. Complementa a análise 1 (`docs/REVISAO-UI-UX.md` e `docs/UI-HANDOFF.md`, na branch `docs/ui-ux-review`), que cobriu layout, hierarquia, tokens, contraste, textos e teclado. Esta análise **não repete** esses achados. Ela trata do que a análise 1 deixou sem medição: carregamento, desempenho percebido, robustez de interação e verificação do fluxo navegador → API → dados. Quando há sobreposição, o achado cita o da análise 1 pelo número.

## 1. O que foi usado do plugin e o que cada parte contribuiu

| Skill/agente | Uso | Contribuição |
|---|---|---|
| `vercel:verification` | Roteiro de verificação por fronteira: interface → requisição → API → dados → resposta → interface, parando na primeira fronteira quebrada | Fluxo principal medido de ponta a ponta (seção 3). A fronteira quebrada foi "servidor indisponível" (VC-03). Também apareceram as inconsistências do Follow tail (VC-05, VC-06) |
| `vercel:react-best-practices` | Só as regras que fazem sentido em JS puro | `async-parallel` (VC-01), `bundle-conditional` e `bundle-preload` (VC-07), `client-event-listeners` (VC-05), `rendering-content-visibility` e `js-batch-dom-css`/leitura depois de escrita (VC-09), `client-passive-event-listeners` (o `wheel` com `passive: false` em `grid.js:90-103` é justificado, porque a rolagem é virtual) |
| `vercel:shadcn` | Só a parte de composição de interface: estados vazio/carregando/erro com tratamento visual, `Command` para descoberta por teclado, `AlertDialog` para ação destrutiva, tokens | Paleta de comandos (VC-10) e estado "carregando" com os dados anteriores na tela (VC-03). Tokens, `Sheet`/gavetas, menu de contexto e confirmação destrutiva já estão na análise 1 (P2-3, P0-5, D6, P2-7) |
| Agente `vercel:performance-optimizer` | Revisão só de leitura do código, com os meus números medidos | Confirmou as prioridades e trouxe dois cuidados: (a) URL versionada exige a mesma `?v=` em todo `import` e em toda `<link>` injetada, senão o módulo é instanciado duas vezes; (b) `contain` só no viewport e nas linhas, porque `content-visibility` não ajuda nas linhas do pool, que já são janeladas. Também deu orçamentos, revistos na seção 5 |

## 2. O que não se aplica, e por quê

- **Deploy, preview, domínios, `vercel env`, CI da Vercel, Firewall, Functions, Storage, Workflow, AI SDK/Gateway, Flags, Marketplace, microfrontends:** o app é local (`127.0.0.1`), offline, sem conta nem infraestrutura. Nenhum comando `vercel` foi rodado e nenhum MCP foi autenticado.
- **Next.js/React (RSC, `'use client'`, SSR/SSG/ISR, Cache Components, `next/image`, `next/font`, `next/script`, Turbopack):** o app não tem framework nem build (restrição do projeto). As regras de re-render do React (`rerender-*`, `useTransition`, `memo`) não têm equivalente: o grid já reaproveita um pool fixo de linhas.
- **CDN, edge, regiões, TTFB de rede, compressão gzip/brotli:** tudo passa pelo loopback. Bytes custam pouco. O que custa é o número de requisições (HTTP/1.1, 6 conexões por host) e a fila que elas formam (VC-01, VC-07, VC-08).
- **Fontes web (`next/font`, preload de fonte):** o app não tem `@font-face`. Usa pilhas do sistema, então não há FOIT/FOUT. A decisão sobre a fonte mono é a D3 da análise 1.
- **Bundler, minificação, tree-shaking, bundle analyzer:** não há passo de build. O equivalente manual é `import()` sob demanda com `modulepreload` (VC-07). Minificar daria pouco ganho no loopback e não vale um build.
- **shadcn/Tailwind/Radix, Geist, Lucide:** dependências e estética fora das restrições. Fica só o princípio de composição.
- **Speed Insights/RUM:** o app não tem telemetria (restrição). A medição é local, com o script da seção 6.

## 3. Método e números medidos

- **Dados sintéticos:** `tools/bench.py --size-mb 500 --adapters 8 --profile info` gerou um caso com **3.470.424 registros** (banco de 1,4 GB). Para o Follow tail, `--size-mb 5 --adapters 3` (32 mil registros), com `serve --watch` e um escritor sintético anexando 200 linhas/s. Nada de `local-samples/`.
- **Navegador:** Chrome headless do sistema dirigido pelo protocolo DevTools, com scripts Node locais (fora do repo): Resource Timing, Event Timing (INP), Layout Instability (CLS), `Performance.getMetrics` e tracing `devtools.timeline` para detectar layout forçado. Janela 1600×900. Medido sem limitação de CPU e com CPU 4× mais lenta, para aproximar um PC corporativo.
- **Servidor:** `dmslog serve` a partir de `src/` e do `dmslog.pyz` gerado por `packaging/build_pyz.py`.

| Medida | Valor |
|---|---|
| Requisições estáticas na abertura | **92** (72 JS, incluindo uPlot com 145 KB; 19 CSS; `index.html`) |
| Bytes estáticos na abertura | **1,0 MB** (JS 912 KB sem minificar, CSS 63 KB) |
| Recarregar (F5) | 92 requisições e 1,0 MB de novo, 0 do cache (`Cache-Control: no-store`, sem ETag) |
| Grafo estático de `main.js` | 35 módulos, 391 KB, profundidade 3 (4 viagens em cascata) |
| Módulos carregados por `import()` na inicialização | 36, todos disparados no `start()` (`main.js:745-1229`), nenhum sob demanda |
| DOMContentLoaded / FCP / LCP | 321 ms / 332 ms / 548 ms |
| **Primeiro registro visível no grid** | **1.236 ms** (1×) e 1.291 ms (4×) |
| `/api/cases` no navegador × curl | 208 ms × 1 ms (fila atrás dos módulos) |
| `/api/sources` (36 fontes) | 520 a 650 ms; a consulta SQL sozinha leva 480 ms |
| `/api/entries/page` e `/count` | 3 ms e 41 ms |
| **CLS na abertura** | **1,58** (maior deslocamento: 0,70, quando a barra quebra em 2 linhas) |
| INP (clique na linha, setas, filtro, busca) | ≤ 16 ms (1×) e ≤ 24 ms (4×), sem tarefa longa |
| Rolagem com roda (120 eventos) | 120 layouts forçados (1 por evento): 49 ms no total a 1× e 175 ms a 4×; maior layout 3,5 ms a 4× |
| Pular pela barra de rolagem (50%) | 92 ms (inclui o debounce de 80 ms) |
| Filtro "Error or worse" / busca "timeout" | linhas em 19 a 24 ms / 29 a 40 ms |
| Memória e DOM | heap de 2,3 a 3,4 MB; 3 a 4 mil nós; sem vazamento em 60 s de Follow tail |
| Estático servido pelo `.pyz` × `src/` | 1,8 ms × 0,6 ms por arquivo (Linux) |

Resumo: **a interação já é rápida**. O grid virtual com pool e keyset faz o trabalho, e nenhuma interação passou de 24 ms nem com CPU 4× mais lenta. Os problemas estão **antes** da primeira linha (cascata de requisições e SQL lento) e na **estabilidade** da tela (CLS 1,58, estados inconsistentes quando algo falha ou quando chegam dados novos).

### Verificação ponta a ponta (fluxo: abrir caso → ler → filtrar → selecionar)

| Fronteira | Estado | Evidência |
|---|---|---|
| Interface carrega | OK, lenta | 92 requisições; CLS 1,58 (`01-abertura-caso-3-4M.png`) |
| Interface → API (casos, fontes) | OK, em cascata | `/api/cases` (fila) → `/api/sources` (480 ms de SQL) → `/collections` → `/page` |
| API → dados (página, contagem) | OK | 3 ms e 41 ms com 3,47 milhões de registros |
| Resposta → interface (grid, detalhe) | OK | INP ≤ 24 ms |
| Servidor indisponível | **Quebra** | Grid esvaziado, filtro novo e trilha aplicados sem dados, timeline ainda com os dados antigos (VC-03) |
| Dados novos (Follow tail) | Inconsistente | Status "32,187 entries" enquanto a coleção mostra 45.637 e o botão "13,900 new records" (VC-06) |

## 4. Achados

### VC-01 (P1). A primeira linha espera uma cascata de 4 viagens e uma consulta de 480 ms

- **Tela/estado:** abrir o viewer com um caso (o caso é reaberto automaticamente).
- **Evidência:** `/api/cases` 318→526 ms (1 ms com curl: a requisição ficou na fila das 6 conexões HTTP/1.1, ocupadas pelos módulos). Depois, `/api/sources` 527→1.175 ms, `/collections` 1.178→1.220 ms (só começa depois de fontes, `main.js:501-508`) e então `/page`, que leva 3 ms. Primeira linha em 1.236 ms. Em `server/api.py:1045-1055`, a subconsulta correlacionada `SELECT e.line_no + e.nlines - 1 FROM entry e WHERE e.source_id = s.id ORDER BY e.id DESC LIMIT 1` usa `ix_entry_tid` + `TEMP B-TREE FOR ORDER BY`: lê todos os registros de cada fonte. São 480 ms com 3,47 milhões de registros, crescendo de forma linear. A variante `WHERE e.id = (SELECT max(x.id) FROM entry x WHERE x.source_id = s.id)` devolve o mesmo resultado em 80 ms.
- **Problema:** com o ERS Verbose de vários GB, a lista de fontes leva segundos, e o grid, que tem a página pronta em 3 ms, espera por ela. Durante a Follow tail, a mesma consulta roda a cada ~6 s (VC-05).
- **Proposta:** (a) gravar `last_line` em `source` na indexação (custo zero na leitura). Paliativo imediato: a variante com `max(id)`. (b) No cliente, pedir `/sources` e `/collections` com `Promise.all` e chamar `applyView` uma vez. O grid já aceita fonte sem nome (cai no `source_id`, `grid.js:579-580`), mas o certo é renderizar só quando os dois chegarem, para não piscar. (c) Começar `/api/cases` antes do grafo de módulos (um `boot.js` mínimo que faz o `fetch` e depois `import("./main.js")`) e cortar as requisições da abertura (VC-07, VC-08).
- **Aceite:** primeira linha ≤ 600 ms no caso de 3,5 milhões; `/api/sources` ≤ 50 ms; no máximo 2 viagens em série antes de `/page`.
- **Esforço:** M. **Arquivos:** `server/api.py`, `store/` (coluna nova + migração, registrar em `docs/ARQUITETURA.md`), `js/main.js`, `js/sources.js`, novo `js/boot.js`, `index.html`.
- **Análise 1:** não coberto (a seção 14 deixou o desempenho sem medição).

### VC-02 (P1). A tela pula durante o carregamento (CLS 1,58)

- **Tela/estado:** abertura, com ou sem caso.
- **Evidência (fontes dos deslocamentos):** 0,70 a 404 ms: `#panels` desce 36 px quando a barra (`#toolbar`, `flex-wrap: wrap`, `css/app.css:144-150`) ganha a 2ª linha, porque cada módulo tira o `hidden` do seu botão ao carregar (`main.js:749` e seguintes). 0,36 a 454 ms: `#views-stack-bar` é inserida acima do filtro (124 px). 0,24 a 320 ms: lateral e grid se rearrumam na primeira pintura. Em seguida há deslocamentos menores, de timeline, lista de fontes e banner de relógio. **23 módulos injetam o próprio `<link rel="stylesheet">`** depois de carregar (por exemplo `timeline.js`, `presets.js`, `clock-banner.js:9-13`), o que muda estilos depois da pintura.
- **Problema:** o usuário mira um controle e ele muda de lugar; o primeiro clique erra. Na análise de horas, a tela deve ser estável.
- **Proposta:** (a) botões presentes desde o HTML (ou dentro dos menus da barra de uma linha da análise 1, P1-1), nunca `hidden` → visível no carregamento; módulo indisponível = botão `disabled` com `title`. (b) Altura reservada para `#timeline`, `#views-stack-bar` e `#clock-banner` (o banner vai para a barra de status da P1-7). (c) As 23 folhas de estilo entram estáticas no `index.html`, ou numa única `css/panels.css`: com cache (VC-08), isso custa pouco.
- **Aceite:** CLS ≤ 0,05 na abertura e ≤ 0,1 na sessão (filtrar, abrir painel, Follow tail).
- **Esforço:** S (a, b) + S (c). **Arquivos:** `index.html`, `css/app.css`, `js/main.js`, módulos que injetam `<link>`.
- **Análise 1:** complementa P1-1 (barra de uma linha) e P1-7 (mensagens sem deslocar). Aqui está a medição e a causa no carregamento.

### VC-03 (P1). Uma falha apaga o que o usuário estava lendo e deixa a tela incoerente

- **Tela/estado:** servidor fechado (janela do console fechada, PC suspenso) ou qualquer erro numa recarga do grid. Captura `03-servidor-indisponivel-grid-vazio.png`.
- **Evidência:** `Grid.reload()` (`grid.js:253-271`) zera a janela e renderiza **antes** de pedir a página. Se o pedido falha, o grid fica vazio (0 linhas, status vazio) e só aparece "Could not reach the server." no topo. O filtro novo já foi aplicado ao formulário e à trilha (`sev <= 3`), enquanto a timeline continua com os dados antigos ("Timeline failed" ao lado de um histograma antigo). O `EventSource` tenta reconectar sem parar: 9 requisições falhas em 20 s. Não há `aria-busy` em nenhum lugar do grid.
- **Problema:** um erro transitório destrói o contexto da investigação. Mesmo sem erro, cada troca de filtro deixa o grid em branco até a resposta chegar. Com 3 ms não se vê, mas uma busca FTS ou uma árvore de filtros pesada em 10 milhões de registros deixa a tela em branco.
- **Proposta:** padrão "manter os dados anteriores": o grid só troca a janela quando a nova página chega. Enquanto isso, as linhas antigas ficam com `opacity: .6`, `aria-busy="true"` no `role="grid"` e, depois de 400 ms, uma barra fina de progresso (handoff da análise 1, seção de estados). Em erro, as linhas antigas e o filtro anterior voltam e a mensagem fica junto do grid, com "Retry". Erro de rede vira o estado "Disconnected": banner fixo, sem deslocar o layout, com o texto "dmslog is not running. Start it again, then Retry." e um "Retry" que chama `/api/health`. A reconexão do SSE para depois de 3 falhas seguidas e volta no "Retry".
- **Aceite:** com o servidor parado, as linhas visíveis e o filtro anterior continuam na tela; nenhuma visão nova entra na trilha; o teste `tests/web/` simula a falha de `api.page`.
- **Esforço:** M. **Arquivos:** `js/grid.js`, `js/grid-model.js`, `js/main.js` (aplicação do filtro e trilha), `js/views-stack.js`, `js/sources.js:422`, `js/follow-tail.js`, `js/strings.js`.
- **Análise 1:** o handoff prevê o banner de "servidor indisponível" (linha 268). Este achado acrescenta a preservação dos dados, a coerência do estado e o limite de reconexão.

### VC-04 (P1). No modo de alto contraste do Windows, a linha selecionada some

- **Tela/estado:** Windows com "Contrast themes" (forced colors), comum em PCs corporativos por acessibilidade ou ofuscamento. Captura `04-forced-colors-selecao-invisivel.png` (emulação `forced-colors: active` no Chrome).
- **Evidência:** a seleção é só `background: var(--selected)` (`css/app.css:383-385`), e o modo forçado troca o fundo pela cor do sistema. A linha 5 está selecionada e não se distingue das outras (fundo `rgb(0,0,0)`, sem contorno). As cores de severidade também somem. Nenhum CSS tem `@media (forced-colors: active)`.
- **Proposta:** bloco `@media (forced-colors: active)` em `css/app.css`: `.grid-row.selected { forced-color-adjust: none; background: Highlight; color: HighlightText; }` (ou `outline: 2px solid Highlight` com `outline-offset: -2px`). Severidade com marca não cromática na coluna Level (Error/Critical em negrito ou com prefixo `!`). Foco com `outline` (já existe) e botões `aria-pressed` com borda `ButtonText`. A barra de seleção em `box-shadow` proposta na P1-2 **não aparece** no modo forçado: usar `outline` nesse bloco.
- **Aceite:** captura antes/depois com `Emulation.setEmulatedMedia` (`forced-colors: active`, claro e escuro); conferir no Edge do Windows.
- **Esforço:** S. **Arquivos:** `css/app.css`, `css/highlight.css`, `css/bookmarks.css`.
- **Análise 1:** não coberto. Afeta a proposta da P1-2.

### VC-05 (P1). Follow tail reconstrói a lista de fontes a cada ~6 s e consulta o servidor mesmo com a aba oculta

- **Tela/estado:** Follow tail ligado com `serve --watch`, escritor sintético a 200 linhas/s, 60 s.
- **Evidência:** a cada atualização derivada (`DERIVED_DEBOUNCE_MS = 1500`, `DERIVED_MAX_WAIT_MS = 6000`) roda `panel.refreshSources(true)` (`main.js:1064-1067`). `renderSources()` faz `list.textContent = ""` e recria cada item, com 2 listeners por checkbox (`sources.js:206-246`). Em 60 s foram 8 `/api/sources`, 8 `/collections`, 16 `/events` e 53 `/count`. Os listeners oscilam entre 209 e 280 e os nós entre 3.014 e 4.253 (há churn, não vazamento: o heap fica em 2,2 a 2,7 MB). Num caso grande, cada `/api/sources` custa 480 ms de SQL (VC-01). Nenhum módulo trata `visibilitychange`.
- **Problema:** quem está marcando fontes com o teclado **perde o foco** a cada reconstrução (WCAG 2.4.3). A rolagem da lista pode voltar, e o servidor gasta CPU com uma aba que ninguém está olhando.
- **Proposta:** (a) reconciliação por `id`: se o conjunto de fontes não mudou, atualizar só os textos (contagem, intervalo) dos itens existentes; se mudou, inserir ou remover só os itens novos, preservando `document.activeElement`. (b) Um listener delegado de `change`/`keydown` no `<ul>`. (c) Com `document.hidden`, pausar as atualizações derivadas e a contagem; manter só o SSE e fazer uma atualização ao voltar (`visibilitychange`). (d) Pedir `/sources` só quando o SSE indicar fonte nova ou rotação; para contagens, um endpoint leve ou o próprio evento `watch`.
- **Aceite:** com Follow tail ligado e foco num checkbox da lista, o foco continua no mesmo checkbox depois de 3 ciclos; com a aba oculta por 30 s, nenhum `/api/sources`.
- **Esforço:** M. **Arquivos:** `js/sources.js`, `js/follow-tail.js`, `js/follow-tail-model.js` (+ teste), `js/main.js`.
- **Análise 1:** não coberto (a seção 14 deixou o Follow tail sem evidência).

### VC-06 (P2). Follow tail: ligar não leva ao fim, e a contagem e a timeline ficam desatualizadas

- **Tela/estado:** Follow tail ligado com o grid no começo do caso (o estado de abertura). Captura `02-follow-tail-contagem-desatualizada.png`.
- **Evidência:** ao marcar "Follow tail" fora do fim, `setOn` só registra a posição (`follow-tail-model.js`, `setOn`), e o grid fica no começo, contando "N new records". Depois de 60 s: status "32,187 entries", timeline "32187 dated entries", coleção "45,637 records" e botão "13,900 new records".
- **Problema:** três números diferentes para a mesma coisa. Quem liga "seguir" espera ver o fim, como no `tail -f`, no BareTail ou no klogg.
- **Proposta:** ligar Follow tail com o grid fora do fim chama `grid.goEnd()` (o comportamento de "parar de seguir ao rolar para cima" continua). Status com o total atual ("45,637 records (+13,450 since view)") ou, no mínimo, sem contagem antiga, que fica marcada como "updating". A timeline entra no `refreshDerived` com o mesmo debounce.
- **Esforço:** S. **Arquivos:** `js/follow-tail.js`, `js/follow-tail-model.js` (+ teste), `js/grid.js` (`updateStatus`), `js/main.js`.

### VC-07 (P2). Todos os 71 módulos carregam na abertura, mesmo os de painéis nunca abertos

- **Evidência:** 36 `import()` disparados no `start()` (`main.js:745-1229`), mais 35 módulos estáticos. uPlot (145 KB) entra pelo import estático de `timeline.js`. São 92 requisições e 1,0 MB a cada abertura, todas disputando as 6 conexões com a API (VC-01). O carregamento por `import()` existe para tolerar falha ("optional module"), não para adiar.
- **Proposta:** carregar sob demanda (`bundle-conditional`): o botão ou item de menu existe desde o início (VC-02); no `pointerenter`/`focus`, `<link rel="modulepreload">` (`bundle-preload`); no clique, `await import()` e abre o painel. Na abertura só ficam os módulos que agem sem clique: grid, detalhe, fontes, filtros, timeline, trilha de visões (se houver estado salvo), Follow tail, alertas e banner de relógio. `<link rel="modulepreload">` no `index.html` para o grafo estático de `main.js`, para achatar as 4 viagens em 1.
- **Aceite:** ≤ 45 requisições e ≤ 450 KB de JS na abertura; abrir um painel pela primeira vez ≤ 100 ms (pré-carregado no hover).
- **Esforço:** M. Fazer junto com a barra de menus da análise 1 (P1-1/L3), porque os dois mexem nos mesmos pontos de `main.js`. **Arquivos:** `js/main.js`, `index.html`, `js/strings.js` (texto de módulo indisponível).

### VC-08 (P2). Nenhum arquivo estático usa cache: cada F5 baixa tudo de novo

- **Evidência:** `_send` aplica `Cache-Control: no-store` a todas as respostas (`server/app.py:125`), inclusive aos estáticos (`_serve_static`, `app.py:373-383`), sem `ETag` nem `Last-Modified`. Ao recarregar: 92 requisições, 1,0 MB, 0 do cache. No `.pyz`, cada arquivo leva 1,8 ms para ser lido do zip (0,6 ms a partir de `src/`). No Windows com antivírus, esse custo não foi medido.
- **Proposta:** só para estáticos, `ETag` calculado uma vez por arquivo (hash do conteúdo, em memória no processo), `Cache-Control: no-cache` e resposta `304` a `If-None-Match`. API e SSE continuam com `no-store` (dados sensíveis). Não usar URL versionada com `immutable` sem garantir a mesma `?v=` em todo `import` e em toda `<link>`: uma URL sem versão ao lado de uma versionada instancia o módulo duas vezes (alerta do agente de desempenho). Cache em memória dos bytes lidos do `.pyz` (o app tem ~1 MB de estáticos).
- **Aceite:** F5 com 92 respostas `304` sem corpo (ou ≤ 45 com VC-07); teste unitário do servidor para `If-None-Match`.
- **Esforço:** S. **Arquivos:** `server/app.py`, `tests/unit/` (servidor). Revisar a decisão "nada em cache" em `docs/ARQUITETURA.md`: cachear o código do app no perfil do navegador não expõe dado de log.

### VC-09 (P2). Um layout forçado por evento de roda

- **Evidência:** o trace mostra 120 layouts forçados para 120 eventos `wheel`, todos dentro do listener do viewport. `syncScroll` escreve `scrollTop` e lê o valor de volta logo em seguida (`grid.js:430-431`), e `ensurePool` lê `clientHeight` a cada `render()` (`grid.js:491`). O custo é de 0,4 ms por evento a 1× e 1,5 ms a 4×, com o grid de 13 linhas. Com o grid de 24 ou mais linhas (meta da análise 1, P0-1) e células com `<mark>`, o custo cresce.
- **Proposta:** guardar o valor calculado (`const top = tsToScroll(...); this.viewport.scrollTop = top; this.lastScroll = top;`); usar a altura do `ResizeObserver` que já existe (`grid.js:115-117`, `entry.contentRect.height`) em vez de ler `clientHeight`; acumular os deltas da roda e renderizar uma vez por `requestAnimationFrame`. Contenção: `contain: strict` em `#grid-viewport` e `contain: layout paint style` em `.grid-row`, para que trocar o texto das linhas não recalcule a barra e a lateral. `content-visibility: auto` só nos painéis fechados e no detalhe, não nas linhas do pool.
- **Aceite:** 0 layouts forçados no trace de rolagem; ≤ 4 ms de script + estilo + layout por quadro a 4×. Este é o critério que a análise 1 pede no L6.
- **Esforço:** S. **Arquivos:** `js/grid.js`, `css/app.css`.

### VC-10 (P2). Paleta de comandos para as ações (padrão `Command` do shadcn)

- **Evidência:** 22 botões na barra (P1-1 da análise 1) e atalhos só por memória (P1-12). A análise 1 propõe menus e o diálogo `?`, mas não oferece busca de ações por nome.
- **Proposta:** `Ctrl+K` (e item "Command palette…" no menu View) abre um diálogo modal com um campo e uma lista filtrada de ações (Open Aggregate, Distinct values…, Export report…, Go to time…, Toggle Follow tail, Theme: Dark…), cada uma com o atalho ao lado. Padrão ARIA combobox + listbox, `aria-activedescendant`, setas, Enter e Esc com retorno de foco. Implementado em JS puro (sem a dependência `cmdk`): um registro único de ações (`id`, rótulo de `strings.js`, atalho, `run`, `enabled()`), o mesmo que alimenta os menus, o diálogo `?` e o menu de contexto da análise 1 (D6). Assim, a ação, o atalho e o texto ficam num lugar só.
- **Esforço:** M. Depois de L3 da análise 1. **Arquivos:** novos `js/commands.js` e `js/commands-model.js` (+ teste de filtro e de teclado), `css/commands.css`, `js/main.js`, `js/strings.js`.
- **Análise 1:** complementa P1-1, P1-12 e D6.

### VC-11 (P2). Orçamento de desempenho sem guarda automática

- **Evidência:** nenhum teste mede a abertura, o CLS ou a rolagem. Os números desta análise vieram de scripts descartáveis.
- **Proposta:** `tools/ui_perf.mjs` (Node, sem dependência: usa o `WebSocket` nativo do Node ≥ 22 e o Chrome/Edge instalado) que sobe `dmslog serve` com o `tools/bench.py --quick`, mede a primeira linha, as requisições, o CLS, os layouts forçados na rolagem e o INP, e compara com a tabela da seção 5. Uso manual antes de cada release e, opcionalmente, um job de CI só em Linux (o runner `ubuntu-latest` tem Chrome) que não bloqueia o merge no começo.
- **Esforço:** M. **Arquivos:** novo `tools/ui_perf.mjs`, `docs/RELEASE.md` (passo de verificação).

## 5. Orçamentos de desempenho sugeridos

Referência: caso de 3,5 milhões de registros, janela de 1600×900, CPU 4× mais lenta (aproxima um notebook corporativo).

| Medida | Hoje | Orçamento |
|---|---|---|
| Primeira linha do grid, abertura a frio | 1.291 ms | **≤ 600 ms** (meta 400) |
| Primeira linha, recarga com cache | 1,2 s (sem cache) | ≤ 300 ms |
| Requisições na abertura | 92 | ≤ 45; na recarga, só `304` |
| JS na abertura | 912 KB, 71 módulos + uPlot | ≤ 450 KB, ≤ 40 módulos |
| CSS na abertura | 19 arquivos, 23 injeções depois da pintura | ≤ 12 arquivos, 0 injeções depois da pintura |
| Viagens em série antes de `/page` | 4 | ≤ 2 |
| Cada chamada da abertura (servidor) | até 480 ms (`/sources`) | ≤ 50 ms |
| CLS da abertura / da sessão | 1,58 / n/d | ≤ 0,05 / ≤ 0,1 |
| INP (clique, teclas, filtro, busca) | ≤ 24 ms | ≤ 100 ms (alarme acima de 50) |
| Rolagem: trabalho por quadro | 1 layout forçado por evento | 0 layouts forçados; ≤ 4 ms por quadro; 0 tarefas > 50 ms |
| Filtro/busca até as linhas | 24 a 40 ms | ≤ 200 ms, sem grid em branco (VC-03) |
| Follow tail | lista recriada a cada 6 s | heap e nós estáveis em 10 min; ≤ 2 ms por atualização; 0 perdas de foco; 0 requisições com a aba oculta |

## 6. Ordem sugerida e encaixe nos lotes da análise 1

- **Junto com L0 (correções de layout, S):** VC-04 (forced colors), VC-09 (layout forçado) e a parte (b) de VC-02 (alturas reservadas). Todos em `css/app.css`/`js/grid.js`, sem conflito com L0 se forem do mesmo PR ou do PR seguinte.
- **PR próprio, servidor (S/M):** VC-01 (a) e VC-08. Ficam em `server/` e `store/`, arquivos disjuntos do front-end, e podem correr em paralelo com L1/L2.
- **Junto com L3 (barra de menus):** VC-02 (a, c), VC-07 e VC-01 (b, c). Todos mexem na inicialização de `main.js`.
- **Depois de L3:** VC-10 (registro único de ações).
- **Com L8 (barra de status) ou antes:** VC-03 (dados preservados e estado "Disconnected").
- **Independente:** VC-05 e VC-06 (Follow tail), VC-11 (guarda).

Pontos de integração com dono único (sessão principal): `js/main.js`, `js/strings.js`, `index.html`, `docs/ARQUITETURA.md` (coluna nova em `source` e cache dos estáticos).

## 7. Capturas (sintéticas)

| Arquivo | Estado |
|---|---|
| `vercel/01-abertura-caso-3-4M.png` | Caso de 3,47 milhões aberto, barra em 2 linhas depois dos deslocamentos |
| `vercel/02-follow-tail-contagem-desatualizada.png` | Follow tail: "32,187 entries" × coleção 45.637 × "13,900 new records" |
| `vercel/03-servidor-indisponivel-grid-vazio.png` | Servidor parado: grid vazio, filtro e trilha aplicados, timeline antiga |
| `vercel/04-forced-colors-selecao-invisivel.png` | `forced-colors: active`: a linha selecionada (5ª) não se distingue |

## 8. Limitações

- **Edge no Windows e antivírus:** medido só no Chrome do Linux. O custo de servir do `.pyz` com Defender e o modo de alto contraste real ficam para o PC de destino.
- **CPU 4× é emulação.** Não substitui um notebook corporativo real. Disco e SQLite não foram limitados.
- **A fila das 6 conexões é inferência:** `/api/cases` levou 1 ms no curl e 208 ms no navegador, com 92 estáticos em voo. Não houve trace de rede por conexão.
- **A rolagem com roda não entra no INP** (a Event Timing API não mede `wheel`). O custo veio do trace (`Layout` dentro do listener).
- **Leitor de tela:** fora do escopo desta análise (ver P1-11 da análise 1).
- **Caso maior que 3,5 milhões:** não gerado. A consulta de `/api/sources` cresce de forma linear e deve ser medida no caso de 2 GB do bench, depois de VC-01.

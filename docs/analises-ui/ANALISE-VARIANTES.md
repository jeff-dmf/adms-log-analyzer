# Análise 2 de UI/UX: variantes visuais do viewer (2026-10-14)

Exploração de variantes de direção visual para as telas críticas do viewer (`src/dmslog/web/`, `main` em `8969d94`), com protótipos estáticos renderizados e medidos. Complementa a análise 1 (`docs/REVISAO-UI-UX.md` e `docs/UI-HANDOFF.md` do branch `docs/ui-ux-review`); os achados de lá são citados pelo ID (P0-1, P1-5, D4...) e não são repetidos.

**Origem:** esta análise **não veio do plugin Superdesign**. O Superdesign exige login em serviço externo e foi descartado pelo Jefferson. Tudo foi feito localmente com a skill `frontend-design:frontend-design`, HTML/CSS estático e o Chrome headless do sistema, sem rede, sem CDN e sem conta.

## 1. Método e ferramentas usadas

- **Skill:** `frontend-design:frontend-design` (plano de tokens por variante, revisão do plano contra o briefing, crítica por captura).
- **Protótipos:** 4 arquivos HTML autocontidos em `docs/analises-ui/variantes/`, sem JS (os estados mudam por `:target` na URL: `#closed` fecha a doca, `#empty` mostra o estado sem caso, `#error` mostra falhas), tema claro/escuro por `prefers-color-scheme`, dados **sintéticos** inventados (adapters, mensagens e horários fictícios, ano 2030). Partem dos tokens de `css/app.css` e do handoff da análise 1.
- **Renderização e medição:** Chrome 155 headless do sistema dirigido pelo protocolo DevTools com um script Node local (perfil de navegador temporário próprio, encerrado no fim). Funcionou na primeira tentativa. Viewports de 1280×720 (150% num monitor 1080p), 1366×768, 1600×900 e 1920×1080; claro e escuro; 4 estados por combinação: **128 renderizações medidas**, 20 capturas salvas.
- **Medidas** com `getBoundingClientRect` e `getComputedStyle`: linhas de grid visíveis (`floor(altura do viewport / altura da linha)`), fatia da coluna central ocupada pelo grid, largura útil da coluna Message (px e caracteres, com a largura do caractere mono medida na página), linhas visíveis na doca e na lista de fontes, altura do cromo acima do grid, se o banner de erro cobre a faixa de filtro; contraste WCAG de **39 pares** (texto: cor computada do elemento contra o primeiro fundo opaco; componentes: tokens resolvidos pelo navegador).
- **Estado atual:** não renderizei o app real (exige gerar um ERS sintético grande). Os números do estado atual vêm das medições da análise 1; a largura da coluna Message no estado atual é **estimada por CSS** (larguras fixas de `css/app.css:418-440` e `css/collections.css:183`, `box-sizing: border-box`, padding de 8 px do painel, barra de rolagem de 15 px) e está marcada como estimativa.

## 2. As variantes

| Arquivo | Direção | Ideia central |
|---|---|---|
| `variante-a-console.html` | **A**: proposta da análise 1 | Tokens, tamanhos e layout do `UI-HANDOFF.md` como estão: linha de 22 px, barra de 40 px, timeline de 64 px, faixa de 32 px, horário com data, laterais de 280 e 400 px. |
| `variante-b-terminal.html` | **B**: denso-técnica (terminal/IDE) | Linha de 18 px, quase sem cor: severidade por peso (Error e Critical em negrito), nível em 3 letras (`ERR`, `WRN`), filtro como linha de consulta editável (`source:3/14 coll:C1 time:10:00..10:15`), timeline de 28 px que só marca onde há erro, abas da doca como IDE, raio de 2 px. |
| `variante-c-operacoes.html` | **C**: painel de operações (ISA-101) | Fundo cinza, cor só para o anormal em forma de **selo de alarme** (Critical e Error em vermelho cheio, Warning em âmbar), linha anormal com fundo tingido, seleção por barra lateral de 4 px, faixa de filtro inteira tingida com o rótulo "Filtered", timeline de 48 px como **faixa de alarme** (só Warning/Error/Critical em barras, Info como linha fina). Linha de 24 px. |
| `variante-r-recomendada.html` | **R**: combinação recomendada | Tokens e contraste de A; economia de colunas de B; sinais de anormal de C sem os selos; lateral recolhida em trilho abaixo de 1500 px; erro global no lugar da barra de status. Detalhe na seção 6. |

Capturas (todas sintéticas, < 400 KB cada):

| Captura | Estado |
|---|---|
| `img/a-console-1600x900-light-dock.png`, `img/a-console-1600x900-dark-dock.png` | A com doca aberta (Distinct values), claro e escuro |
| `img/a-console-1366x768-light-dock.png` | A em 1366×768: coluna Message com 8 caracteres; fichas do filtro cortadas |
| `img/evidencia-a-1600x900-selecao-fora-da-tela.png` | Primeira rodada de A: com a doca aberta a linha selecionada ficou fora do grid |
| `img/b-terminal-1600x900-light-dock.png`, `img/b-terminal-1600x900-dark-dock.png`, `img/b-terminal-1366x768-light-dock.png` | B com doca |
| `img/c-operacoes-1600x900-light-dock.png`, `img/c-operacoes-1600x900-dark-dock.png`, `img/c-operacoes-1366x768-light-dock.png` | C com doca |
| `img/r-recomendada-1600x900-light-dock.png`, `img/r-recomendada-1600x900-dark-dock.png`, `img/r-recomendada-1366x768-light-dock.png` | R com doca; em 1366 a lateral vira trilho |
| `img/a-console-1600x900-light-empty.png`, `img/b-terminal-1600x900-dark-empty.png`, `img/c-operacoes-1600x900-light-empty.png` | Estado vazio (sem caso) |
| `img/a-console-1600x900-dark-error.png`, `img/b-terminal-1600x900-light-error.png`, `img/c-operacoes-1600x900-dark-error.png`, `img/r-recomendada-1600x900-light-error.png` | Estado de erro: servidor caiu (banner), doca sem dados, arquivo do ZIP ilegível |

## 3. Números medidos

### 3.1 Densidade do grid (tema claro; o escuro dá os mesmos números)

Linhas visíveis sem doca / com doca a 40%. "Msg" = caracteres visíveis na coluna Message com a doca aberta (a largura não depende da doca).

| Variante | Linha | Cromo acima do grid | 1280×720 | 1366×768 | 1600×900 | 1920×1080 |
|---|---|---|---|---|---|---|
| **Atual** (análise 1) | 22 px | 397 px (barra, banner, timeline, visões, filtros; mais 76 px abaixo: status e ajuda) | 5 / 5 linhas; Msg ≈ 0 (estim.) | 5 / 5; Msg ≈ 12, ≈ 0 com coluna Collection (estim.) | 13 / 5; Msg ≈ 43, ≈ 31 com Collection (estim.) | não medido; Msg ≈ 86 (estim.) |
| **A** | 22 px | 160 px | 24 / 12; Msg **0** | 26 / 13; Msg **8** | 32 / 17; Msg 40 | 40 / 22; Msg 85 |
| **B** | 18 px | 106 px | 33 / 19; Msg 41 | 35 / 21; Msg 54 | 43 / 26; Msg 89 | 53 / 32; Msg 137 |
| **C** | 24 px | 148 px | 22 / 11; Msg 12 | 24 / 13; Msg 24 | 30 / 16; Msg 56 | 37 / 20; Msg 100 |
| **R** | 20 px | 128 px | 28 / 15; Msg **56** | 30 / 16; Msg **68** | 37 / 20; Msg 69 | 46 / 26; Msg 113 |

Outras medidas (1600×900): fatia do grid na coluna central sem/com doca: A 86/46%, B 91/55%, C 87/47%, R 89/49%. Linhas visíveis na doca: A 10, B 10, C 10, R 11 (em 1366: 8, 8, 8, 9). Linhas visíveis na lista de fontes: A 26, B 35, C 26, R 31 (em 1366 a lista de R fica no trilho). Texto do registro no detalhe: 108 a 113 px (6 linhas) em todas, "What it means" visível sem rolar em todas as combinações.

Contra os critérios de aceite da análise 1 (P0-1: ≥ 24 linhas em 1600×900, ≥ 18 em 1366×768 e ≥ 12 em 1280×720 sem painel; ≥ 8 com painel): **todas as variantes passam**. O que separa as variantes é a largura da mensagem, que a análise 1 não mediu: **A falha em ≤ 1366 px** (0 a 8 caracteres) e C também (12 a 24).

### 3.2 Contraste (WCAG 2.1, razão medida no navegador, 1600×900)

Os 39 pares passam em todas as variantes depois da segunda rodada. Mínimos por variante:

| Par | Mínimo | A claro / escuro | B | C | R |
|---|---|---|---|---|---|
| Verbose/Noise sobre painel | 4,5 | 5,67 / 6,82 | 6,51 / 6,18 | 6,45 / 6,63 | 5,67 / 6,82 |
| Texto mudo sobre linha selecionada | 4,5 | 5,02 / 5,21 | 5,58 / 5,00 | 5,39 / 4,99 | 5,02 / 5,21 |
| Critical sobre linha selecionada | 4,5 | 6,97 / 4,92 | 7,03 / 5,42 | 6,55 / 4,78 | 6,97 / 4,92 |
| Texto sobre destaque do usuário | 4,5 | 5,37 / 4,78 | 5,83 / 5,01 | 6,08 / 5,36 | 5,37 / 4,78 |
| Error sobre fundo de linha anormal | 4,5 | n/a | n/a | 5,89 / 6,89 | 5,46 / 7,23 |
| Selo de alarme Critical (texto/fundo) | 4,5 | n/a | n/a | 7,33 / 4,69 | n/a |
| Texto da faixa "Filtered" | 4,5 | n/a | n/a | 8,10 / 8,83 | 8,75 / 8,84 |
| Borda de controle sobre painel (1.4.11) | 3,0 | 3,53 / 3,84 | 3,90 / 3,62 | 3,69 / 4,07 | 3,53 / 3,84 |
| Barra de seleção sobre linha selecionada | 3,0 | 5,46 / 5,65 | 6,31 / 6,71 | 5,28 / 5,11 | 5,46 / 5,65 |
| Barras de Warning da timeline | 3,0 | 3,60 / 7,71 | 4,48 / 7,62 | 4,04 / 8,24 | 4,49 / 7,71 |
| Série normal da timeline | 3,0 | 3,10 / 3,21 | 3,55 / 3,62 | 4,02 / 4,18 | 3,10 / 4,10 |
| Fundo da seleção sozinho (informativo) | n/a | 1,13 / 1,31 | 1,17 / 1,24 | 1,20 / 1,33 | 1,13 / 1,31 |

Falhas encontradas na primeira rodada e corrigidas nos protótipos: âmbar de alarme da timeline de C no claro (`#d9a800` sobre `#e6e8eb`: **1,79:1**, trocado por `#8f6a00`: 4,04) e série normal de B (`#a3a9b2`: **2,29:1** no claro e `#4a505a`: 2,19 no escuro; trocados por `#7f8690` e `#6b717c`). Os pares mais justos estão no escuro: Critical sobre seleção (4,78 a 4,92) e texto sobre destaque (4,78); qualquer ajuste de `--row-selected` ou `--hl-bg` escuros precisa passar pelo teste de tokens proposto na análise 1 (UX-03).

### 3.3 Fontes efetivas

No Linux de teste, a pilha mono do handoff (`"Cascadia Mono", Consolas, "DejaVu Sans Mono", ...`) caiu em **BitstromWera Nerd Font** (via `ui-monospace`/`monospace`) e a de interface em **Liberation Sans**: Cascadia, Consolas, DejaVu e Segoe não estão instaladas. Largura do caractere a 12 px: 7,23 px; Cascadia Mono no Windows tem avanço de 0,6 em (7,2 px a 12 px), então as contagens de caracteres da tabela devem valer no Windows dentro de ±1 caractere. Isso não foi verificado no Windows.

## 4. Achados e recomendações

| ID | Prioridade | Tela/estado | Esforço |
|---|---|---|---|
| VR-01 | P0 | Caso aberto, ≤ 1366 px | M |
| VR-02 | P1 | Doca aberta com seleção | S |
| VR-03 | P1 | Faixa de filtro em 1366 px | M |
| VR-04 | P1 | Erro global | S |
| VR-05 | P1 | Timeline | M |
| VR-06 | P2 | Grid com Error/Critical | S |
| VR-07 | P2 | Densidade do grid | S |
| VR-08 | P2 | Larguras de coluna entre SOs | S |
| VR-09 | P2 | Altura inicial da doca | S |
| VR-10 | P2 | Arquivo ilegível no ERS | S |
| VR-11 | P2 | Estado vazio | S |

### VR-01 (P0). A coluna Message fica sem largura em telas de notebook

- **Tela/estado:** caso aberto, 1280×720 e 1366×768 (notebook corporativo e 150% de zoom em 1080p).
- **Evidência:** A, que é o layout do handoff, deixa **0 caracteres** de mensagem em 1280×720 e **8** em 1366×768 (`img/a-console-1366x768-light-dock.png`: "Polling…", "Heartbe…"). No estado atual a estimativa por CSS dá ≈ 12 caracteres em 1366 e ≈ 0 quando há a coluna Collection (90 px, caso com 2 coletas). A soma fixa das colunas (horário com data 190 px, PID e TID 72+72, Level 84, Source 160, Collection 90 = 668 px) mais as laterais (280 + 380/400) passa de 1366 px. A regra "três colunas a partir de 1280 px" do handoff (seção 3.1) não garante espaço para a mensagem, que é a coluna que se lê.
- **Problema:** com o grid alto (P0-1 resolvido), o usuário passa a ver 26 linhas sem conseguir ler nenhuma; a mensagem só aparece no detalhe, uma por vez.
- **Proposta (valores verificados em R):**
  - Horário `HH:MM:SS.mmm` em 100 px; a data aparece na coluna só quando o caso cobre mais de um dia (`MM-DD HH:MM:SS.mmm`, 136 px), decidido uma vez por caso, e fica sempre no detalhe, na faixa de filtro (intervalo) e no `title` da célula.
  - PID e TID 44 px, Level 72 px, Source 148 px (com as reticências no meio do P1-3), Collection 36 px como etiqueta `C1`.
  - Abaixo de 1500 px de largura: lateral recolhida num **trilho de 44 px** (botão vertical "Sources" com `3/14` selecionadas, abre a lateral como gaveta sobreposta) e detalhe com 360 px. Acima: lateral 248 px e detalhe 380 px.
  - Aceite: Message com ≥ 50 caracteres em 1280×720 e em 1366×768 com 2 coletas. R mediu 56 e 68.
- **Arquivos prováveis:** `css/app.css` (larguras e `@media (max-width: 1499px)`), `css/collections.css`, `js/grid.js` e `js/grid-model.js` (formato do horário por caso), `js/sources.js` e `index.html` (trilho), `js/column-resize.js` (os padrões mudam; larguras já salvas pelo usuário continuam valendo).
- **Relação:** aprofunda P0-1 e P0-5 (o breakpoint de 1280 px precisa considerar a largura da mensagem), complementa P1-3 e o layout da seção 3 do handoff.

### VR-02 (P1). Abrir a doca esconde a linha selecionada

- **Tela/estado:** registro selecionado na metade de baixo do grid; abre-se um painel na doca.
- **Evidência:** `img/evidencia-a-1600x900-selecao-fora-da-tela.png` (primeira rodada de A): a linha selecionada estava na posição 22, a doca de 40% deixou 17 linhas e a seleção sumiu, enquanto o detalhe continuava mostrando o registro. Com doca a 40%, o grid perde 45 a 50% das linhas em todas as variantes (tabela 3.1).
- **Problema:** o drill-down perde o ponto de partida; o usuário não sabe qual linha está no detalhe.
- **Proposta:** ao abrir, redimensionar ou maximizar a doca, e ao mudar de breakpoint, manter a linha selecionada visível: se ela estava visível, preservar sua distância do topo; se sairia da área, rolar para deixá-la a 1/3 da altura do grid. Implementar com um `ResizeObserver` no viewport do grid que chama a mesma função de "garantir visível" que a navegação por teclado já usa; nada roda durante a rolagem.
- **Arquivos prováveis:** `js/grid.js`; `js/dock.js` (novo, L4 da análise 1).
- **Relação:** entra no aceite do L4 (P0-2): "com a doca aberta, a linha selecionada continua visível".

### VR-03 (P1). Estado "filtrado" inconfundível e fichas que não cabem

- **Tela/estado:** filtro ativo com 3 condições, 1366×768.
- **Evidência:** `img/a-console-1366x768-light-dock.png`: a terceira ficha aparece cortada ("10:00 to 10") e a contagem fica espremida contra os botões. Em C e R, a faixa tingida (`--filtered-bg`) com o rótulo "Filtered" e a barra de 3 px se destaca do grid nos dois temas (texto 8,1 a 8,8:1).
- **Problema:** o P1-5 da análise 1 (filtro esquecido) volta quando as fichas transbordam sem aviso; e uma faixa de fichas na mesma cor do resto da barra não diz "você não está vendo tudo".
- **Proposta:**
  - Faixa com fundo `--filtered-bg` (claro `#e3ecfa`, escuro `#17304f`), `box-shadow: inset 3px 0 0 var(--accent)` e rótulo "Filtered" à esquerda **só quando há filtro**; sem filtro, fundo `--surface-panel` e texto "No filters".
  - Contagem `1,204 of 235,230 records` com `flex: none` (nunca cortada).
  - Fichas que não cabem viram um botão "+2 more" que abre o popover Filters; a medição das fichas acontece só quando o filtro muda.
- **Arquivos prováveis:** `css/views-stack.css`, novo `js/filter-chips.js` (UX-08), `js/strings.js` ("Filtered", "No filters", "+{n} more").
- **Relação:** aprofunda P1-5 e o componente 5.9 do handoff.

### VR-04 (P1). O banner de erro global cobre o filtro ativo

- **Tela/estado:** servidor local caiu (estado `#error`).
- **Evidência:** medido: o banner sobreposto "ao topo da coluna central" (P1-7) **cobre a faixa de filtro** em B e em C (`img/b-terminal-1600x900-light-error.png`) e a legenda da timeline em A (`img/a-console-1600x900-dark-error.png`). Em R o mesmo erro ocupa a barra de status e nada fica coberto (`img/r-recomendada-1600x900-light-error.png`).
- **Problema:** justamente quando o usuário precisa saber em que contexto está, o aviso tapa o filtro ativo.
- **Proposta:** erro global substitui o conteúdo da barra de status: mesma altura (`--status-height`), fundo `--status-error-bg`, texto `--status-error-ink` (7,8:1), borda superior de 1 px, botão "Retry now", `role="alert"`. Some quando a conexão volta. Erros de painel ficam no corpo do painel (componente `.ui-error`), erros de formulário junto do campo (como na análise 1).
- **Arquivos prováveis:** `js/status-bar.js` e `css/status-bar.css` (UX-15), `js/main.js`.
- **Relação:** corrige a posição proposta em P1-7.

### VR-05 (P1). Timeline como faixa de alarme

- **Tela/estado:** caso aberto, timeline.
- **Evidência:** em A (64 px, Info cinza empilhado sobre o anormal), o pico de erros sintético ocupa uma fração pequena da altura e a massa cinza domina (`img/a-console-1600x900-light-dock.png`). Em C e R (`img/r-recomendada-1600x900-light-dock.png`), Warning, Error e Critical usam a escala inteira e o pico salta aos olhos em 40 a 48 px; o volume normal vira uma linha fina com escala própria, e lacunas continuam visíveis.
- **Problema:** a timeline existe para achar picos de erro e lacunas; com a escala dominada pelo normal, mesmo cinza, o pico vira um traço.
- **Proposta:** altura 40 px (expansível a 120 px, lembrada); barras empilhadas só de Critical (`--chart-critical`), Error (`--chart-error`) e Warning (`--chart-warning`, claro `#9a6a00`, 4,49:1) com escala própria; Info+Verbose como linha de 1,5 px em `--chart-normal` (escala própria, até 40% da altura); legenda numa linha à direita com as contagens; o arraste para filtrar continua.
- **Arquivos prováveis:** `js/timeline.js`, `js/timeline-model.js`, `css/timeline.css`.
- **Relação:** aprofunda P1-4 (lá as séries continuam empilhadas na mesma escala) e substitui os 64 px do handoff.

### VR-06 (P2). Linha anormal com fundo tingido; selos de alarme só no detalhe

- **Tela/estado:** grid com Error e Critical.
- **Evidência:** C usa três sinais por linha anormal (selo cheio, fundo tingido, mensagem em negrito vermelho); no escuro (`img/c-operacoes-1600x900-dark-dock.png`) os selos dominam a tela e competem com a seleção. Em R, só o fundo `--abn-row` + texto da severidade (Error sobre o tingido: 5,46 / 7,23) bastam para achar as linhas ao rolar, e a seleção (barra de 3 px) continua a mais forte.
- **Proposta:** `--abn-row` claro `#f8eceb`, escuro `#2a1a1a` para severidade 1 e 2; prioridade dos fundos: seleção > destaque do usuário > anormal > hover. O selo cheio de C fica só no cabeçalho do detalhe.
- **Arquivos prováveis:** `css/app.css` (regras `data-sev`), `css/highlight.css`.
- **Relação:** complementa P1-2 e D5.

### VR-07 (P2). Linha de 20 px em vez de 22 px

- **Evidência:** tabela 3.1. Em 1366×768 com doca aberta: A 13 linhas, R 16, B 21. B (18 px) dá o maior ganho, mas com fonte de 12 px sobra 3 px de entrelinha e o negrito como único sinal de severidade fica fraco (B troca cor por peso; Warning sublinhado não é reconhecível sem legenda). 20 px rende +3 a +6 linhas sobre A sem perder a leitura.
- **Proposta:** `--row-height: 20px` (e `ROW_HEIGHT = 20` em `js/grid-model.js`, com o teste que confere os dois); cabeçalho do grid 22 px.
- **Relação:** contraria a recomendação (a) de D4 com números; a decisão é do Jefferson.

### VR-08 (P2). Larguras padrão de coluna em `ch`

- **Evidência:** seção 3.3: a fonte mono efetiva varia por SO; colunas em px cortam o horário ou sobram espaço quando a fonte muda.
- **Proposta:** larguras padrão em `ch` da fonte do grid (horário `12ch` + padding, PID/TID `5ch`, Level `8ch`); as larguras que o usuário arrasta continuam salvas em px (9.18).
- **Arquivos prováveis:** `css/app.css`, `js/column-resize.js`.
- **Relação:** complementa P2-2.

### VR-09 (P2). Altura inicial da doca pelo conteúdo

- **Evidência:** a doca a 40% mostra 8 a 14 linhas de tabela; Distinct values com 3 valores deixaria 70% da doca vazia enquanto o grid perde metade das linhas.
- **Proposta:** altura inicial `clamp(160px, conteúdo, 40%)`; depois do primeiro arraste vale a altura lembrada.
- **Arquivos prováveis:** `js/dock.js`, `css/dock.css` (L4).

### VR-10 (P2). Arquivo ilegível do ERS marcado na própria fonte

- **Evidência:** com o aviso em bloco no pé da lateral (A, B, C no estado `#error`), o arquivo citado fica 300 px acima, na lista. Em R, a fonte recebe o marcador `!` e o nome na cor de erro (`img/r-recomendada-1600x900-light-error.png`).
- **Proposta:** marcador `!` (`--status-error-ink` sobre `--status-error-bg`) antes do nome, `title` com o motivo, contagem "1 file unreadable" no cabeçalho de Collections.
- **Arquivos prováveis:** `js/sources.js`, `css/app.css`, `js/strings.js`.

### VR-11 (P2). Estado vazio sem lateral

- **Evidência:** na primeira rodada, a lateral com "Recent cases" e o centro com "Open an ERS to start" repetiam a mesma escolha; na segunda, a lateral some sem caso e os casos recentes ficam no centro, sob as ações (`img/a-console-1600x900-light-empty.png`).
- **Proposta:** sem caso, a coluna da lateral fica com 0 px, a do detalhe também, e a barra de status diz "No case open.".
- **Relação:** detalha P2-4.

## 5. Fluxos redesenhados

### 5.1 Achar a causa de um erro no ERS (caso já aberto)

| Passo | Hoje | Proposto (R) |
|---|---|---|
| 1 | Severity → "Error or worse" → Apply (3 cliques) | Clique no pico vermelho da faixa de alarme: filtro de horário vira ficha e o grid vai ao primeiro Error do intervalo (1 clique) |
| 2 | Clicar uma linha (o `n` exige seleção antes) | `n`/`N` percorrem Error e Critical a partir do topo; linhas anormais já tingidas |
| 3 | Ler a mensagem: em 1366 px ela está cortada no grid; ler no detalhe | Mensagem legível no grid (68 caracteres em 1366) e completa no detalhe |
| 4 | Rolar o detalhe até "What it means" | "What it means" visível sem rolar (medido em todas as combinações) |
| 5 | Rolar até "Keys" (abaixo do payload), clicar a ficha; o painel empurra o grid | Ficha de chave no bloco Keys; "Same key" abre na doca e a linha selecionada continua visível (VR-02) |
| 6 | Fechar o painel para voltar ao grid | "Show in grid" cria visão encadeada; a faixa "Filtered" mostra a trilha e as fichas |

Trocas de foco entre regiões: hoje 6 a 7 (barra de filtros, grid, detalhe com rolagem duas vezes, painel, grid); proposto 3 (timeline, grid, doca), com o teclado cobrindo os passos 2 e 5.

### 5.2 Comparar duas coletas

| Passo | Hoje | Proposto (R) |
|---|---|---|
| 1 | Rolar a lateral até "Add ERS files to this case", Add, Add files | "Add files…" no topo da lateral (ou no trilho em ≤ 1500 px) abre o diálogo |
| 2 | Marcar C1/C2 no painel de coletas, que empurra a lista de fontes para fora | Coletas compactas com caixa; o filtro vira ficha `Collection C1` na faixa "Filtered" |
| 3 | Coluna Collection pinta a célula inteira (90 px), e em 1366 px some a mensagem | Etiqueta `C1` com ponto de cor em 36 px; mensagem com 68 caracteres |
| 4 | Contar diferenças rolando o grid | Analyze → Aggregate com o exemplo existente "by collection" (`groupBy: collection, family`) na doca; o grid fica acima com a seleção visível |
| 5 | Sem caminho direto da contagem para os registros | Linha do resultado → Only: visão encadeada com as duas fichas; aviso de relógio entre coletas continua na faixa |

Trocas de foco: hoje 5 (lateral com rolagem, coletas, grid, rolagem do grid, detalhe); proposto 3 (diálogo, doca, grid).

## 6. Variante recomendada: R

**Por quê, pelos números:** R é a única que atende ao mesmo tempo (1) os aceites de altura da análise 1 com folga (28 a 46 linhas sem doca, 15 a 26 com doca), (2) mensagem legível em notebook (56 caracteres em 1280×720 e 68 em 1366×768, contra 0 e 8 de A), (3) todos os pares de contraste com os tokens já verificados de A, e (4) nenhum elemento cobrindo o filtro no estado de erro. B tem mais linhas (+5 a +7 sobre R) mas paga com severidade só por peso e linhas de 18 px; C tem a melhor leitura do anormal mas é a menos densa (24 px) e a mais pesada no escuro. R pega de cada uma o que os números sustentam.

### 6.1 Tokens (somam-se ou substituem os do handoff da análise 1)

| Token | Claro | Escuro | Origem |
|---|---|---|---|
| `--row-height` | `20px` | | VR-07 |
| `--grid-head` | `22px` | | |
| `--bar-height` | `36px` | | |
| `--strip-height` | `30px` | | |
| `--status-height` | `22px` | | |
| `--control-height` | `26px` (fichas e botões de linha 24 px) | | |
| `--timeline-h` | `40px` (expandida 120 px) | | VR-05 |
| `--side-w` / `--detail-w` | `248px` / `380px`; abaixo de 1500 px: `44px` (trilho) / `360px` | | VR-01 |
| `--w-time`, `--w-pid`, `--w-lvl`, `--w-src`, `--w-coll` | `100px`, `44px`, `72px`, `148px`, `36px` (ou em `ch`, VR-08) | | VR-01 |
| `--cell-pad` | `4px` | | |
| `--abn-row` | `#f8eceb` | `#2a1a1a` | VR-06 |
| `--filtered-bg` / `--filtered-ink` | `#e3ecfa` / `#163f78` | `#17304f` / `#b8d4ff` | VR-03 |
| `--chart-warning` | `#9a6a00` | `#d9a23a` | VR-05 |
| `--chart-normal` | `#848fa0` | `#6f7b8c` | VR-05 |

Os demais tokens de cor, tipografia, raio e camadas são os do handoff (seção 2), sem mudança.

### 6.2 Regras de layout (CSS puro)

```css
#app { height: 100dvh; display: flex; flex-direction: column; overflow: hidden; }
.cols { flex: 1 1 auto; min-height: 0; display: grid;
        grid-template-columns: var(--side-w) minmax(0, 1fr) var(--detail-w); }
@media (max-width: 1499px) { :root { --side-w: 44px; --detail-w: 360px; } /* lateral vira trilho + gaveta */ }
.center > .timeline, .center > .fstrip, .grid-head, .dock { flex: none; }
.grid { flex: 1 1 auto; min-height: calc(var(--row-height) * 8 + var(--grid-head)); }
.dock { height: clamp(160px, var(--dock-content, 40%), 40%); }      /* VR-09; depois, altura lembrada */
.row[data-sev="1"], .row[data-sev="2"] { background: var(--abn-row); }
.row.hl  { background: var(--hl-bg); box-shadow: inset 4px 0 0 var(--hl-bar); }
.row.sel { background: var(--row-selected); box-shadow: inset 3px 0 0 var(--accent); }
.row.sel.hl { box-shadow: inset 3px 0 0 var(--accent), inset 7px 0 0 var(--hl-bar); }
.fstrip.is-filtered { background: var(--filtered-bg); box-shadow: inset 3px 0 0 var(--accent); }
.fcount { flex: none; }
.status.is-error { background: var(--status-error-bg); color: var(--status-error-ink); } /* VR-04 */
```

Regras que valem para tudo: só regiões internas rolam; nada mede texto durante a rolagem (o corte das fichas e o formato do horário são calculados na mudança de filtro e de caso); conteúdo de log por `textContent`; textos novos em `js/strings.js`; tema só por variáveis; trilho, fichas, divisor da doca e "+N more" operáveis por teclado (Tab, Enter, setas, Esc), com `aria-expanded` no trilho e `aria-pressed` nos alternadores.

### 6.3 Onde entra nos lotes da análise 1

VR-01 e VR-07 no L0/L1 (layout e tokens), VR-03 no UX-08, VR-04 no UX-15, VR-05 no UX-09, VR-02 e VR-09 no L4, VR-06 no L6, VR-08 com P2-2, VR-10 e VR-11 no UX-16. Nenhum lote novo.

## 7. O que ficou sem evidência

- **App real:** os números de "Atual" vêm da análise 1; a largura da mensagem no estado atual é estimativa por CSS. Os protótipos reproduzem o layout proposto, não o código do app.
- **Windows/Edge e fontes:** Cascadia Mono, Consolas e Segoe UI não estão no Linux de teste (seção 3.3); conferir as contagens de caracteres e a renderização ClearType no PC de destino.
- **Interação:** protótipos sem JS; teclado, arraste do divisor, trilho como gaveta, "+N more" e a âncora da seleção (VR-02) estão descritos, não testados.
- **Zoom real e 900×800:** 1280×720 simula 150% em 1080p só pela viewport (sem fator de escala); 900×800 e 200% não foram renderizados (a análise 1 já cobre).
- **Leitor de tela:** não testado.
- **Dados:** sintéticos e pequenos (78 linhas por protótipo); nada de `local-samples/`.

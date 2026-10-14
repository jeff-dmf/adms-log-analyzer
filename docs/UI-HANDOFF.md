# Handoff de UI do viewer

Especificação para implementar a revisão de `docs/REVISAO-UI-UX.md` (achados, prioridades e lotes). Nomes de tokens, classes e textos em inglês, como o app; explicações em pt-BR. Restrições que valem para tudo: CSS e JS puros, sem build, sem CDN, sem dependência nova; conteúdo de log só por `textContent`; todo texto visível em `js/strings.js`; temas por variáveis CSS; nada que meça texto ou force layout durante a rolagem do grid.

## 1. Direção

**Console de sala de controle.** Fundo neutro de baixo brilho, texto de alto contraste, cor só para o que é anormal (Warning, Error, Critical) e um único azul para interação (foco, seleção, ação principal). Info e Noise não têm cor. Interface em fonte do sistema, dados em mono com números tabulares. Densidade alta e estável: nada muda de altura quando aparece uma mensagem.

Princípios:

1. O grid é a tela. Tudo o mais é auxiliar e cede espaço a ele (mínimo de 8 linhas com qualquer painel aberto).
2. Cor é informação. Se não é severidade, seleção, foco ou destaque escolhido pelo usuário, é cinza.
3. Um lugar para cada coisa: filtros ativos na faixa de filtro, mensagens na barra de status, análises na doca, o registro no detalhe.
4. Tudo o que tem atalho tem também um caminho visível (menu, menu de contexto, diálogo `?`).

## 2. Tokens

Arquivo único `css/tokens.css`, carregado primeiro em `index.html`. Dois níveis: primitivos (valores) e semânticos (uso). Os componentes só usam semânticos. O bloco escuro aparece duas vezes (`@media (prefers-color-scheme: dark) :root:not([data-theme="light"])` e `:root[data-theme="dark"]`), com conteúdo idêntico garantido por teste.

### 2.1 Cor (semânticos)

| Token | Claro | Escuro | Uso |
|---|---|---|---|
| `--surface-app` | `#eceef1` | `#0e1115` | Fundo da aplicação, entre painéis |
| `--surface-panel` | `#f8f9fa` | `#15191f` | Painéis, grid, detalhe |
| `--surface-raised` | `#e4e7eb` | `#1d222a` | Cabeçalho do grid, barras, botões secundários |
| `--surface-overlay` | `#ffffff` | `#20262f` | Menus, diálogos, gavetas |
| `--ink` | `#1b1f24` | `#dde3ea` | Texto principal |
| `--ink-muted` | `#5c6470` | `#98a2af` | Texto secundário, Noise/VERBOSE no grid |
| `--ink-disabled` | `#8f96a0` | `#6a7380` | Controles desabilitados (sem exigência de contraste; sempre com outro sinal: cursor e ausência de hover) |
| `--line` | `#d0d5dc` | `#2b323c` | Divisores decorativos |
| `--line-strong` | `#7d8591` | `#6c7685` | Borda de campos, botões e caixas (≥ 3:1) |
| `--accent` | `#1a5bb8` | `#7aa7ff` | Foco, seleção (barra), ação principal, links |
| `--accent-ink` | `#ffffff` | `#0e1115` | Texto sobre `--accent` |
| `--row-hover` | `#eef1f5` | `#1b2028` | Hover de linha |
| `--row-selected` | `#e3ecfa` | `#1c2f4b` | Fundo da linha selecionada (sempre com a barra de 3 px) |
| `--sev-critical` | `#a1001c` | `#ff6b80` | Texto Critical |
| `--sev-error` | `#b8261b` | `#ff8a70` | Texto Error |
| `--sev-warning` | `#875700` | `#e9b44c` | Texto Warning |
| `--sev-info` | `var(--ink)` | `var(--ink)` | Texto Info (sem cor) |
| `--sev-noise` | `var(--ink-muted)` | `var(--ink-muted)` | Texto Noise/VERBOSE |
| `--chart-critical` | `#8e0f3a` | `#ff4d7a` | Timeline e gráficos |
| `--chart-error` | `#c8352a` | `#e5574a` | |
| `--chart-warning` | `#b07800` | `#d9a23a` | |
| `--chart-normal` | `#848fa0` | `#5e6a7a` | Info |
| `--chart-noise` | `#c3c9d2` | `#3a424e` | Noise (série recessiva; legenda com texto) |
| `--status-info-bg` / `--status-info-ink` | `#e5eefb` / `#163f78` | `#17304f` / `#b8d4ff` | Avisos neutros |
| `--status-error-bg` / `--status-error-ink` | `#fbe7e7` / `#8a1c1c` | `#4a1d1d` / `#ffb4b4` | Erros |
| `--mark` | `#ffe58a` | `#7a6100` | Termo buscado (`<mark>`) |
| `--focus-ring` | `var(--accent)` | `var(--accent)` | Anel de foco |

Contrastes verificados (WCAG): `--ink` sobre painel 15,7 / 13,7; `--ink-muted` sobre painel 5,7 / 6,8 e sobre seleção 5,0 / 5,2; Critical, Error e Warning sobre painel ≥ 5,8 e sobre seleção ≥ 4,9 (claro) e ≥ 4,9 (escuro); `--accent-ink` sobre `--accent` 6,5 / 7,9; `--line-strong` sobre painel 3,5 / 3,8; barra de seleção sobre `--row-selected` 5,5 / 5,7; `--chart-normal` e `--chart-warning` ≥ 3,1 sobre painel. O fundo da seleção sozinho tem pouco contraste com o painel (1,13 / 1,31): por isso a seleção **sempre** tem a barra de 3 px.

Paletas categóricas (timeline por fonte, agregação, destaques, bookmarks, coletas) passam a derivar de **uma** paleta de 8 cores (`--cat-1` a `--cat-8`, claro e escuro), com variantes `-bg` (fundo de destaque, texto `--ink` ≥ 4,5:1) e `-fg` (traço e texto ≥ 3:1). As paletas atuais (`--tl-c*`, `--agg-c*`, `--hl-*`, `--bm-*`, `--col-*`) viram apelidos dessa paleta, para não quebrar os módulos de uma vez. O teste `highlight-contrast.test.mjs` continua valendo.

### 2.2 Tipografia

| Token | Valor |
|---|---|
| `--font-ui` | `"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif` |
| `--font-mono` | `"Cascadia Mono", Consolas, "DejaVu Sans Mono", "Liberation Mono", ui-monospace, monospace` |
| `--text-xs` | `11px` / `16px` (selos, contadores) |
| `--text-sm` | `12px` / `18px` (rótulos de campo, metadados, status, grid mono) |
| `--text-md` | `13px` / `20px` (texto da interface, padrão) |
| `--text-lg` | `15px` / `22px` (título de painel e de diálogo) |
| `--weight-regular` / `--weight-strong` | `400` / `600` |

Regras: tamanhos só em `px` por token (nada de `em` relativo, que acumula); números de contagem, PID, TID, horários e durações com `font-variant-numeric: tabular-nums`; nada em caixa alta (o título "SOURCES" atual vira "Sources" em `--text-sm` forte, `--ink-muted`); grid em `--font-mono` `--text-sm`.

### 2.3 Espaço, raio, elevação, densidade

| Token | Valor | Uso |
|---|---|---|
| `--space-1` a `--space-6` | `2, 4, 8, 12, 16, 24 px` | Todo `padding`, `margin` e `gap` |
| `--radius-sm` | `3px` | Selos, fichas, `<mark>` |
| `--radius-md` | `4px` | Campos, botões, linhas de lista |
| `--radius-lg` | `6px` | Menus, diálogos, gavetas |
| `--shadow-overlay` | `0 4px 16px rgb(0 0 0 / 0.18)` (escuro: `0.5`) | Menus, diálogos, gavetas |
| `--z-sticky` / `--z-dock` / `--z-drawer` / `--z-menu` / `--z-dialog` / `--z-toast` | `10 / 20 / 30 / 40 / 50 / 60` | Camadas |
| `--row-height` | `22px` | Linha do grid (mesmo valor de `ROW_HEIGHT` em `js/grid-model.js`; um teste confere os dois) |
| `--control-height` | `28px` | Botões e campos |
| `--bar-height` | `40px` | Barra superior |
| `--strip-height` | `32px` | Faixa de filtro, cabeçalho de painel |
| `--status-height` | `24px` | Barra de status |

Movimento: só em resposta a ação (abrir menu, gaveta, doca): `120ms ease-out`; `@media (prefers-reduced-motion: reduce)` zera todas as transições.

## 3. Layout

```
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│ dmslog │ Case ▾ incident-demo │ [ Search records…              ] │ Go to time │ Filter tree │ Highlights │ Bookmarks │ Analyze ▾ │ Export ▾ │ View ▾ │  40 px
├──────────────┬────────────────────────────────────────────────────────────┬───────────────┤
│ Sources      │ timeline (64 px; expandir 160)                    ▾        │ Detail        │
│ [Add files…] │────────────────────────────────────────────────────────────│ 10:00:00.014  │
│ Collections ▾│ All records › text "Validation" › View 2 │ [Sev ≥ Error ×] │ ERROR  AVL…S  │
│  ☐ C1 zone1  │   [3 sources ×] [C2 ×]           Filters ▾  Narrow  ⋯     │ [Ctx][Copy]…  │
│  ☐ C2 zone1  │────────────────────────────────────────────────────────────│ Message type  │
│ [filter…]    │ Time        PID  TID  Level  Source        Message         │ ┌ text ─────┐ │
│ ☐ AVLAdapter │ ▌10:00:00.014 …                                            │ └───────────┘ │
│ ☐ …          │  …  (grid: o resto da altura)                              │ What it means │
│              │════════════ divisor da doca (arrastável) ══════════════════│ Fields · Keys │
│              │ [Distinct values ×] [Operations ×]           ⤢  ×          │ Links         │
│              │ (painel ativo; 40% por padrão)                             │ Payload ▸     │
├──────────────┴────────────────────────────────────────────────────────────┴───────────────┤
│ 285,093 records · showing from 2030-01-15 10:00:00.014 · Indexed 49,863 records.   ? Keys │  24 px
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

- Colunas: lateral `280px` (redimensionável 220–420, lembrada), centro `minmax(0, 1fr)`, detalhe `400px` (320–640, lembrado). Divisores de 4 px, alvo de arraste de 8 px, operáveis por teclado (foco + setas, `Shift` = 32 px).
- Coluna central em coluna flex: timeline, faixa de filtro, cabeçalho do grid e barra da doca com `flex: none`; grid com `flex: 1 1 auto; min-height: calc(var(--row-height) * 8)`; doca com altura própria (`flex: none`, 160 px até `100% - 8 linhas`).
- Lateral: cabeçalho do caso fixo; coletas recolhíveis (abertas quando há mais de uma); filtro de fontes fixo; lista de fontes com rolagem e `min-height: 240px`. O painel lateral rola só por dentro.
- Detalhe: coluna flex com todos os filhos `flex: none` e rolagem do painel; o texto do registro tem `min-height: 6lh`, `max-height: 45vh` e `resize: vertical`.
- A página nunca rola (`#app { height: 100dvh; overflow: hidden }`); só regiões internas rolam.

### 3.1 Breakpoints (largura da viewport CSS)

| Largura | Comportamento |
|---|---|
| ≥ 1280 px | Três colunas como acima |
| 960–1279 px | Detalhe vira gaveta à direita (420 px, `--surface-overlay`, `--shadow-overlay`, `--z-drawer`), aberta por seleção com Enter ou pelo botão "Detail" na faixa de filtro, fechada por Esc; grid ocupa a largura |
| < 960 px | Lateral também vira gaveta (botão "Sources" no início da faixa de filtro); barra superior mantém busca e menus, os três toggles entram no menu View |
| Altura < 640 px | Timeline recolhida por padrão; doca abre maximizada (substitui o grid até fechar) |

Zoom 200% num monitor 1600 px resulta em 800 px CSS: cai na regra "< 960 px", sem sobreposição.

## 4. Grid

### 4.1 Estados da linha (prioridade, da maior para a menor)

| Estado | Visual | Implementação |
|---|---|---|
| Selecionada | Fundo `--row-selected` + barra 3 px `--accent` à esquerda | `box-shadow: inset 3px 0 0 var(--accent)` (sem mudar largura) |
| Foco do teclado no grid | Contorno 1 px `--focus-ring` na linha selecionada, só com `:focus-visible` no viewport | `outline` no `.grid-row.selected` quando o viewport tem foco visível; nada de anel azul em volta do grid inteiro |
| Destaque do usuário | Faixa 4 px na cor da regra à esquerda + fundo `--cat-n-bg` | Se a linha também está selecionada, o fundo vira `--row-selected` e a faixa do destaque fica **depois** da barra de seleção (`inset 3px 0 0 accent, inset 7px 0 0 cor`) |
| Bookmark | Marca ★ na margem esquerda (célula de 14 px) na cor do bookmark | Texto oculto "bookmarked" para leitor de tela |
| Hover | `--row-hover` | Só quando não selecionada |
| Severidade | Cor do **texto** de Level e Message (`--sev-*`); Info e Noise sem cor (Noise em `--ink-muted`) | Igual hoje via `data-sev` |

### 4.2 Células

- Horário: `--font-mono`, `tabular-nums`, formato atual.
- Level: texto em mono, cor da severidade; Critical e Error com peso 600.
- Source: nome do arquivo com **reticências no meio** quando não cabe (`AVLAdapter_10…sensitive.log.0`), calculadas pela largura da coluna em caracteres (largura do caractere mono medida uma vez por mudança de fonte ou largura de coluna, e cache por `source_id`); selo `S` (`--text-xs`, borda `--line-strong`) antes do nome nas fontes sensitive; `title` com o caminho completo.
- Collection: etiqueta `C1` com ponto da cor `--cat-n-fg`, célula sem fundo.
- Message: como hoje (`+N lines` em selo `--text-xs`).

### 4.3 ARIA

`#grid-viewport` com `role="grid"`, `aria-rowcount` = total (ou `-1` quando estimado), `aria-colcount`, `aria-activedescendant` = id da linha selecionada; cabeçalho dentro do grid em `role="rowgroup"`; cada linha `role="row"` com `aria-rowindex` e **sem** `aria-label` (as células falam); estado de bookmark e destaque em texto oculto numa célula. `#grid-status` deixa de ser live; a barra de status anuncia só mudança de contagem e de filtro.

## 5. Componentes

Arquivo `css/components.css`. Classes com prefixo `ui-` para não colidir com os módulos.

### 5.1 Botão `.ui-button`

| Variante | Uso | Visual |
|---|---|---|
| (padrão) secundário | Ações comuns | Fundo `--surface-raised`, borda `--line-strong`, texto `--ink` |
| `.is-primary` | Uma por contexto (Create case, Add files, Export) | Fundo `--accent`, texto `--accent-ink`, borda `--accent` |
| `.is-ghost` | Ações de linha e de cabeçalho de painel (Close, Refresh, Only, Hide) | Sem fundo nem borda; hover com `--surface-raised` |
| `.is-danger` | Remove collection, Delete case | Texto `--sev-error`, borda `--sev-error`; confirmação em dois passos |
| `[aria-pressed="true"]`, `[aria-expanded="true"]` | Alternâncias e botões que abrem painel | Fundo `--row-selected`, borda `--accent`, texto `--ink` |

Estados: hover = borda `--accent` (secundário) ou fundo 8% mais escuro (primário, `color-mix(in srgb, var(--accent), black 8%)`); foco = `outline: 2px solid var(--focus-ring); outline-offset: 1px`; desabilitado = `--ink-disabled`, fundo `--surface-raised`, borda `--line`, `cursor: not-allowed`, sem `opacity`; carregando = rótulo trocado por texto de `strings.js` ("Exporting…") + `aria-busy="true"` + `disabled`. Altura `--control-height`, `padding: 0 var(--space-3)`, `--radius-md`, `--text-md`. Com ícone: ícone 14 px em SVG inline vendorizado, `aria-hidden="true"`, rótulo sempre presente (visível ou `aria-label`).

### 5.2 Campo `.ui-input`, `.ui-select`

Altura `--control-height`, borda `--line-strong`, fundo `--surface-panel` (no escuro `--surface-app`), `--radius-md`. Foco: borda `--accent` + anel. Erro: `aria-invalid="true"` → borda `--sev-error` + mensagem abaixo (`.ui-field-error`, `--text-sm`, `--sev-error`, ligada por `aria-describedby`). Rótulo visível acima (`--text-sm`, `--ink-muted`) sempre que o campo não for autoexplicativo; placeholder nunca substitui rótulo em formulários.

### 5.3 Selo e ficha

- `.ui-badge`: `--text-xs`, `padding: 0 var(--space-2)`, `--radius-sm`, borda `--line-strong`, fundo transparente. Variantes `.is-sensitive` (borda e texto `--accent`), `.is-curated`, `.is-estimate`. Substitui `.badge`, `.pl-badge`, `.cfg-badge`, `.ev-badge`, `.kn-badge`, `.pr-badge`.
- `.ui-chip` (interativo): como selo, mas `--text-sm`, altura 24 px, botão real; com `×` opcional (botão separado, `aria-label` "Remove {label}"). Substitui `.pl-chip`, `.lnk-chip`, `.hl-chip`, `.key-tag`, `.col-tag`.

### 5.4 Painel `.ui-panel` (doca e diálogos)

```
.ui-panel
  .ui-panel-head   (flex: none; altura --strip-height; título --text-lg 600, resumo --text-sm muted, ações à direita, Close)
  .ui-panel-tools  (flex: none; filtros do painel numa linha que quebra)
  .ui-panel-body   (flex: 1; overflow: auto)
  .ui-panel-foot   (flex: none; ação principal à direita; só quando há)
```

Todo painel tem Close (`Esc` com foco dentro fecha e devolve o foco ao botão que abriu). Títulos são `h2` dentro da doca. Tabelas de painel (`.ui-table`): cabeçalho fixo (`position: sticky`), `--text-sm`, números à direita com `tabular-nums`, células com `text-overflow: ellipsis` e `title`; colunas de texto com `min-width` (nada de quebra letra por letra).

### 5.5 Estados de conteúdo

| Componente | Estrutura | Texto (exemplos de chave) |
|---|---|---|
| `.ui-status` | Linha `--text-sm` `--ink-muted`, `role="status"` | "Loading operations…" |
| `.ui-empty` | Bloco centrado no corpo do painel: frase do que é + o que fazer, com botão se houver ação | "No operations in this filter. Clear filters to see all." |
| `.ui-error` | Faixa `--status-error-bg`/`--status-error-ink`, ícone, mensagem + "Try again" | "Could not load operations. {reason}" |
| `.ui-estimate` | Selo `.is-estimate` + texto | "Estimate: rare values may be missing." |

Carregando: nada de spinner animado; texto e, depois de 400 ms sem resposta, barra indeterminada fina (2 px) no topo do corpo do painel (com `prefers-reduced-motion`, sem animação).

### 5.6 Menu `.ui-menu`

Botão com `aria-haspopup="menu"` e `aria-expanded`; lista `role="menu"` com itens `role="menuitem"`/`menuitemcheckbox`/`menuitemradio`; setas movem, `Home`/`End`, letra inicial pula, `Enter`/`Espaço` ativam, `Esc` fecha e devolve o foco; `--surface-overlay`, `--shadow-overlay`, `--radius-lg`, `--z-menu`; item com atalho mostra a tecla à direita em `--ink-muted` mono. Usado em Analyze, Export, View, menu do caso, menu `⋯` das coletas e menu de contexto do grid.

### 5.7 Diálogo `.ui-dialog`

`<dialog>` nativo com `showModal()` (foco preso e Esc nativos), cabeçalho, corpo com rolagem, rodapé com ação principal à direita e Cancel à esquerda dela; largura `min(720px, 92vw)`, `max-height: 88vh`. Usado em New case, Add files, Export report, Copy as, Shortcuts. O navegador de pastas atual já segue o padrão e migra para `<dialog>`.

### 5.8 Doca `.ui-dock`

Barra de abas (`role="tablist"`, abas `role="tab"` com `aria-controls`, `×` em cada aba), ações à direita (Maximize/Restore, Close all). Divisor `role="separator"` com `aria-orientation="horizontal"`, `aria-valuenow` (altura em %), setas ajustam 5%, `Home`/`End` mínimo/máximo, duplo clique alterna 40%/maximizado. Altura lembrada em `localStorage` (`dmslog.dockHeight`, com `try/catch`). Abrir um painel pelo menu: cria ou ativa a aba e move o foco para o título do painel.

### 5.9 Faixa de filtro `.ui-filterstrip`

Esquerda: trilha de visões (`nav` com `aria-label="Views"`, `ol` de botões; atual com `aria-current="page"`); `Back`/`Forward` viram ícones com `aria-label`. Direita: fichas do filtro efetivo, cada uma com remover; botão "Filters ▾" (popover com Severity, Level, From, To, Hide noise, Unclassified only, Apply); "Narrow" e menu `⋯` (Rename, Show in parent, Show in root). Sem filtro: texto "No filters" em `--ink-muted`.

### 5.10 Barra de status `.ui-statusbar`

Altura fixa `--status-height`, `--text-sm`. Segmentos: contagem ("285,093 records" ou "about 1.2M records"), posição ("from 2030-01-15 10:00:00.014"), indexação (barra fina + "Indexing 3 of 28 files" com `progress`), mensagem (`role="status"`, `aria-live="polite"`; sucesso some em 8 s; erro fica com botão fechar), botão `?` (Shortcuts).

## 6. Detalhe do registro

Ordem e regras:

1. Cabeçalho: horário (mono, 600), selo de nível na cor da severidade, fonte (nome do arquivo + selo `sensitive`), PID, TID, coleta. Sem separadores " - ".
2. Ações (uma linha, botões `.is-ghost` com ícone + rótulo curto): Context ▾ (Same source, Same thread, All sources), Copy, Compare, Bookmark (★, alternância), Highlight ▾ (this message type, cada campo).
3. Título do tipo de mensagem (texto `--text-md` 600; código no `title`), com ação "Filter by this type" no menu `⋯`.
4. Texto do registro (`pre`, mono, `min-height: 6lh`, `max-height: 45vh`, `resize: vertical`).
5. What it means (conhecimento) e nota do vault.
6. Fields (tabela) e Keys (fichas "Same key").
7. Links (normal ↔ sensitive, callbacks, respostas).
8. Payload (recolhível; aberto por padrão quando o registro é o próprio payload), com Formatted/Raw/Wrap lines/Copy.
9. Nota do bookmark (quando há).

Seções 5 a 9 com cabeçalho `h3` recolhível (`button` com `aria-expanded`), estado lembrado por seção. Sem registro selecionado: `.ui-empty` "Select a record to see its full text, meaning and links."

## 7. Textos

- Termos: **record** (nunca entry), **message type** (nunca template na UI), **operation**, **collection**, **source**.
- Plural com duas chaves (`fooOne`, `fooOther`) e `format` escolhe; nada de "(s)".
- Reticências sempre "…"; botões que abrem diálogo terminam com "…".
- Botões com verbo + objeto ("Add files", "Export report", "Remove C2"); a mensagem de resultado repete o verbo ("2 files added.").
- Erro = o que aconteceu + motivo + o que fazer. Estado vazio = o que é + por que está vazio + como começar.
- Códigos internos (`no_operation_for_key`, `bad_field`) nunca aparecem crus: cada um tem frase em `strings.js`.
- Frase em caixa de sentença; nada de caixa alta.

## 8. Teclado

| Tecla | Ação | Onde |
|---|---|---|
| `F6` / `Shift+F6` | Próxima/anterior região (barra, lateral, faixa de filtro, grid, doca, detalhe; ordem do DOM, WCAG 2.4.3, decidida na UI-11) | Global |
| `/` | Foca a busca | Global, fora de campo de texto |
| `?` | Abre o diálogo de atalhos | Global, fora de campo de texto |
| `Esc` | Fecha menu, diálogo, gaveta ou painel com foco; no grid, limpa a seleção de intervalo da timeline | Contextual |
| `↑ ↓ PgUp PgDn Home End` | Navegação | Grid |
| `Enter` | Abre o detalhe (gaveta em janela estreita) | Grid |
| `Shift+F10` / menu | Menu de contexto da linha | Grid |
| `n`/`N`, `t`/`T`, `b`/`B`, `c`, `[`/`]`, `Alt+←/→` | Como hoje; `n`/`N` sem seleção começam do primeiro/último registro visível | Grid e global (desligáveis no diálogo `?`) |

Skip link "Skip to log entries" como primeiro elemento focável, visível só no foco.

## 9. Casos de borda

- Nome de fonte de 120 caracteres: reticências no meio no grid e no detalhe quebra com `overflow-wrap: anywhere`.
- Caso sem horário em parte dos registros: timeline mostra "N undated records" na legenda.
- Contagem estimada: prefixo "about" e selo `.is-estimate` na barra de status.
- Indexação em andamento: barra de status com progresso; ações que exigem caso parado ficam desabilitadas com `title` explicando.
- Sem pack de conhecimento: seção "What it means" some (não mostra vazio).
- Servidor indisponível ou 403: banner de erro fixo sobre a coluna central com "Reload" (não desloca o grid).
- Tema: alternância imediata, sem transição de cor.

## 10. Verificação

- `node --test tests/web/` e `pytest` verdes; novos testes puros para: corte no meio do nome, modelo da doca (alturas mínimas, persistência), modelo de atalhos (desligar letras), fichas do filtro (do filtro para fichas e da remoção de ficha para filtro), contraste dos tokens e igualdade dos blocos escuros.
- Capturas sintéticas antes/depois em 1366×768, 1600×900, 900×800 e 200%, claro e escuro, para cada lote.
- Perfil de rolagem no caso grande do `tools/bench.py` sem *forced reflow* nem *long task* acima de 50 ms durante a rolagem contínua.

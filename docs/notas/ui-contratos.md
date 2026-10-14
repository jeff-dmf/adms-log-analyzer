# Contratos de interface do viewer (UI-11, issue #151)

Documento do Opus para a sequência de UI do M9 (`docs/analises-ui/CONSOLIDADO.md`, PRs 10 a 17, UI-18, UI-24, UI-28 e UI-29). Define cinco interfaces pequenas e estáveis: **registro de ações**, **doca inferior**, **barra de status**, **fichas de filtro** e **regiões de teclado (F6) com ordem de tab**. Identificadores, assinaturas e textos do app em inglês; o resto em pt-BR.

Referências de linha: `main` em `2a1d5cb`, caminhos relativos a `src/dmslog/web/`. Os PRs 5 a 9 (tokens, CSS pelo HTML, seleção, linha de 20 px, textos) deslocam linhas e removem as funções `ensureStylesheet`; na dúvida, procurar pelo símbolo citado.

## 0. Decisões gerais

1. **Quem registra é a sessão principal, de forma síncrona, na abertura.** Ações e painéis da doca são registrados num arquivo novo, `js/app-actions.js`, chamado uma vez por `main.js`. Nenhum módulo se registra sozinho: com o carregamento sob demanda (UI-14) o módulo ainda não foi carregado quando o menu é desenhado. `js/app-actions.js` passa a ser **ponto compartilhado com dono único** (sessão principal), como `main.js`, `strings.js`, `api.js` e `index.html`. Ele tira de `main.js` a parte que mais cresce (um bloco `import().then(...)` por painel, hoje `main.js:742-1241`).
2. **Módulos de painel só renderizam num `host` que recebem.** Nenhum módulo insere nada em `#toolbar`, `#grid-panel`, no rodapé ou no `document`; nenhum registra `keydown` em `document`; nenhum mexe em `host.hidden` de um host da doca.
3. **Um contexto só, `AppContext`, criado uma vez em `main.js`.** Só getters e funções (nunca valores copiados, que envelhecem). Adicionar membro ao `AppContext` é mudança de contrato (registrar em `docs/ARQUITETURA.md`, seção 15).
4. **Modelo puro onde há regra; DOM fino em volta.** Tudo o que decide (atalhos, alturas, fichas, regiões, mensagens) fica em arquivo `*-model.js` ou equivalente sem DOM, testado com `node --test`. O DOM só aplica.
5. **Migração em dois passos para a doca.** A UI-12 introduz a API final da doca sobre os hosts atuais acima do grid (`js/dock-legacy.js`, provisório); a UI-15 troca a implementação por `js/dock.js` (abas) sem mudar o catálogo de `app-actions.js`. Assim cada módulo de painel muda a montagem uma vez só.

### 0.1 `AppContext`

```js
/**
 * @typedef {Object} AppContext            // main.js; criado uma vez, passado por referência
 * @property {() => number|null} caseId    // caso aberto
 * @property {() => object|null} filter    // filtro efetivo do grid (ancestrais AND visão atual) = currentFilter
 * @property {() => object|null} userFilter
 * @property {() => GridRow|null} selectedRow   // grid.selectedRow()
 * @property {() => Entry|null} entry      // detail.entry (registro mostrado no detalhe)
 * @property {(filter: object|null) => Promise<void>} setUserFilter
 * @property {(row: {id: number, ts: number|null}) => Promise<void>} reveal   // revealEntry de main.js:817
 * @property {ActionRegistry} actions
 * @property {Dock} dock
 * @property {StatusBar} status            // até a UI-13: adaptador sobre showMessage (seção 3.5)
 */
```

O que não entra no `AppContext` (lojas de bookmarks e destaques, `derivedApi`, instâncias de painéis, `viewsStack`) chega a `app-actions.js` por um objeto `deps` montado em `main.js`, visível só ali.

## 1. Registro de ações

### 1.1 Tipos

```js
/** @typedef {"case"|"toggle"|"analyze"|"export"|"view"|"views"|"navigate"|"record"|"help"} ActionGroup */

/**
 * @typedef {Object} ActionDef
 * @property {string} id            // "analyze.distinct"; inglês, estável (testes e diálogo ? usam)
 * @property {string} labelKey      // chave de S (strings.js); rótulo = S[labelKey]
 * @property {ActionGroup} group    // decide onde a ação aparece (tabela 1.3)
 * @property {string|null} [shortcut]   // KeySpec canônica (1.2) ou null
 * @property {"global"|"grid"} [scope]  // padrão "global"; "grid" = só com foco no #grid-viewport
 * @property {(ctx: AppContext) => boolean} [enabled]   // padrão: ctx.caseId() !== null
 * @property {string} [disabledKey]     // chave de S do motivo (title do item); padrão "needCase"
 * @property {(ctx: AppContext) => boolean} [pressed]   // só alternâncias: aria-pressed / menuitemcheckbox
 * @property {(ctx: AppContext) => ActionChild[]} [children]  // submenus dinâmicos (Theme, Same key, Context)
 * @property {(ctx: AppContext, args?: any) => void|Promise<void>} run
 * @property {() => Promise<unknown>} [load]   // gancho da UI-14: import() do módulo; chamado no máximo 1x
 */

/**
 * @typedef {Object} ActionChild
 * @property {string} label     // já formatado; pode conter texto de log -> sempre textContent
 * @property {any} args         // passado a run(ctx, args)
 * @property {boolean} [checked]   // radio (Theme)
 */
```

O núcleo é o pedido da issue (`id, labelKey, group, shortcut, enabled(ctx), run(ctx), load?()`). Os opcionais `scope`, `disabledKey`, `pressed` e `children` existem porque a barra (alternâncias com `aria-pressed`), o menu de contexto ("Same key ▸") e o menu View ("Theme ▸") não funcionam sem eles; nada além disso.

### 1.2 Atalhos (KeySpec)

- Forma: modificadores na ordem fixa `Ctrl+`, `Alt+`, `Shift+`, seguidos de `KeyboardEvent.key`: `"b"`, `"B"`, `"?"`, `"/"`, `"]"`, `"Alt+ArrowLeft"`, `"Ctrl+k"`, `"F6"`, `"Shift+F6"`, `"Shift+F10"`.
- Caractere imprimível único **não** leva `Shift+` (a maiúscula ou o símbolo já o codificam: `"B"`, `"?"`). `Meta` nunca é usado.
- **Atalho de uma letra** = sem `Ctrl`/`Alt` e `key.length === 1`. Obedece à preferência `dmslog.singleKeys` (UI-28) e nunca dispara com foco em campo de texto (`isTypingTarget`, `js/filters.js`).
- `register` recusa atalho repetido no mesmo `scope` (erro), para que dois módulos nunca disputem a mesma tecla.

### 1.3 Grupos × consumidores

| Grupo | Barra (UI-12) | Diálogo `?` (UI-28) | Menu de contexto (UI-29) | Paleta (UI-29) |
|---|---|---|---|---|
| `case` | menu do caso | se tiver atalho | não | sim |
| `toggle` | botões com `aria-pressed` | idem | não | sim |
| `analyze` | menu Analyze | idem | não | sim |
| `export` | menu Export | idem | não | sim |
| `view` | menu View | idem | não | sim |
| `views` | menu `⋯` da faixa de filtro (UI-18) | idem | não | sim |
| `navigate` | não aparece em menu | sim | não | sim |
| `record` | botões do detalhe (UI-20) | idem | sim | sim (exige registro selecionado) |
| `help` | menu View e botão `?` da barra de status | idem | não | sim (exceto a própria paleta) |

Ordem dentro do grupo = ordem de registro em `app-actions.js` (é a ordem dos menus do handoff).

### 1.4 API (`js/actions.js`, puro, sem DOM)

```js
export function createActionRegistry({ onError, singleKeysOn }) -> ActionRegistry
//   onError(err, id): reporta falha de run/load (main.js liga em ctx.status.message(..., {kind: "error"}))
//   singleKeysOn(): boolean, preferência dmslog.singleKeys (até a UI-28: () => true)

ActionRegistry = {
  register(def): void                 // síncrono; lança TypeError em id repetido, grupo desconhecido,
                                      // labelKey ausente em S, KeySpec inválida ou atalho repetido no scope
  get(id): ActionDef | undefined
  list({ group?, withShortcut? } = {}): ActionDef[]          // ordem de registro
  state(id, ctx): { enabled, pressed, reasonKey, loaded, failed }
                                      // enabled=false e reasonKey="actionUnavailable" quando load() falhou
  preload(id): Promise<void>          // chama load() uma vez (memo); falha vira failed=true; nunca rejeita
  invoke(id, ctx, args?): Promise<{ ok: true } | { ok: false, reason: "disabled"|"loadFailed"|"error" }>
                                      // await preload(id); confere enabled; run(); erros vão a onError; nunca rejeita
  match(event, { typing, inGrid }): string | null   // id da ação do atalho, ou null
  onChange(fn): () => void            // avisa depois de register e de preload com falha (menus redesenham)
}

export function keySpec(event): string | null                 // KeyboardEvent-like -> KeySpec
export function shortcutLabel(spec, keyNames): string         // "Alt+ArrowLeft" -> "Alt+Left" (S.keyNames)
export function toMenuItems(registry, defs, ctx, S): MenuItem[]   // ponte com o .ui-menu da UI-10
```

`MenuItem` é o formato que o `.ui-menu` da UI-10 (`js/ui-menu.js`, já entregue) aceita; `toMenuItems` produz exatamente isto:

```js
/** @typedef {Object} MenuItem          // = typedef de js/ui-menu.js (UI-10)
 * @property {string} id
 * @property {string} label             // textContent
 * @property {string|null} [shortcut]   // texto à direita, já formatado (shortcutLabel)
 * @property {"menuitem"|"menuitemcheckbox"|"menuitemradio"} [role]
 * @property {boolean|null} [checked]
 * @property {boolean} [pressed]        // atalho de ui-menu para item checkbox (alternâncias)
 * @property {boolean} [disabled]
 * @property {string|null} [title]      // motivo quando desabilitado
 * @property {MenuItem[]} [children]    // submenu
 * @property {boolean} [separator]      // divisor entre grupos; os demais campos são ignorados
 * @property {() => void} [run]         // ui-menu fecha o menu e chama run
 * @property {() => void} [preload]     // ui-menu chama em pointerenter e focus do item
 */
```

Os consumidores usam a API da UI-10 como ela é:

```js
// js/ui-menu.js (UI-10)
createMenu({ trigger, getItems, label = null, onOpenChange = null, doc = trigger.ownerDocument })
  -> { open, close, toggle, isOpen }
//   getItems() é chamado a cada abertura: é ali que entra toMenuItems(registry, defs, ctx, S),
//   e por isso enabled/pressed são calculados na hora de mostrar (1.5)
createChip({ doc, label, removeLabel = null, onRemove = null, title = null }) -> HTMLElement
//   removeLabel = format(S.chipRemove, { label }); usado pelas fichas de filtro (seção 4)
```

### 1.5 Estados e erros

- `enabled` é calculado **na hora de mostrar** (abrir menu, abrir paleta, abrir menu de contexto, abrir `?`); nada de polling. As três alternâncias da barra redesenham em `dock.on("open"|"close")` e em `registry.onChange`.
- Item indisponível fica **visível e desabilitado** com `title` (nunca `hidden`, UI-12).
- `load()` falhou (módulo ausente ou com erro): `state().failed = true`, item desabilitado com `S.actionUnavailable`, mensagem de erro na barra de status uma vez. É a forma nova da tolerância "optional module" de hoje (`main.js:765`, `:791`, `:878` etc.).
- `run` que lança ou rejeita: `onError(err, id)`; o registro continua utilizável.
- O mesmo especificador em `load()` e no `mount()` do painel (`"./distinct-panel.js"`), sem `?v=` (risco de instanciar o módulo duas vezes, CONSOLIDADO §8).

### 1.6 Catálogo inicial (o que cada PR registra)

Rótulos novos com prefixo `act` em `strings.js` (já nos termos da UI-09: record, message type, "…" em quem abre diálogo). As chaves antigas dos botões saem quando o botão sai.

| id | labelKey (texto) | Grupo | Atalho | Hoje (arquivo:linha) | PR |
|---|---|---|---|---|---|
| `case.open` | `actCaseOpen` ("Open case", `children` = casos) | case | | `index.html:57-60`, `sources.js:46` | UI-12 |
| `case.new` | `actCaseNew` ("New case…") | case | | `index.html:61-65` | UI-12 (diálogo na UI-22) |
| `case.addFiles` | `actCaseAddFiles` ("Add files…") | case | | `main.js:567-574` | UI-12 (diálogo na UI-22) |
| `case.close` | `actCaseClose` ("Close case") | case | | (novo) | UI-12 |
| `toggle.filterTree` | `actFilterTree` ("Filter tree") | toggle | | `index.html:39`, `main.js:711-740` | UI-12 |
| `toggle.highlights` | `actHighlights` ("Highlights") | toggle | | `highlight-panel.js:77-80` | UI-12 |
| `toggle.bookmarks` | `actBookmarks` ("Bookmarks") | toggle | `B` | `index.html:40`, `main.js:970-1001`, `:1033-1036` | UI-12 |
| `toggle.alerts` | `actAlerts` ("Alerts") | toggle | | `index.html:46`, `main.js:1074-1101` | UI-12 (indicador só com `--watch`) |
| `analyze.distinct` | `actDistinct` ("Distinct values") | analyze | | `distinct-panel.js:376` | UI-12 |
| `analyze.groupByType` | `actGroupByType` ("Group by message type") | analyze | | `distinct-panel.js:376` | UI-12 |
| `analyze.aggregate` | `actAggregate` ("Aggregate") | analyze | | `aggregate-panel.js:581` | UI-12 |
| `analyze.operations` | `actOperations` ("Operations") | analyze | | `index.html:42`, `main.js:1103-1142` | UI-12 |
| `analyze.dashboard` | `actDashboard` ("Adapter dashboard") | analyze | | `index.html:44`, `main.js:1204-1227` | UI-12 |
| `analyze.payloads` | `actPayloads` ("Payloads") | analyze | | `index.html:43`, `main.js:863-880` | UI-12 |
| `analyze.unknownTypes` | `actUnknownTypes` ("Unknown message types") | analyze | | `index.html:41`, `main.js:742-767` | UI-12 |
| `analyze.config` | `actConfig` ("Effective configuration") | analyze | | `index.html:45`, `main.js:769-793` | UI-12 |
| `export.report` | `actExportReport` ("Export report…") | export | | `index.html:47`, `main.js:1144-1186` | UI-12 (diálogo na UI-23) |
| `export.copyAs` | `actCopyAs` ("Copy as…") | export | | `copy-export.js:117` | UI-12 (diálogo na UI-23) |
| `view.goToTime` | `actGoToTime` ("Go to time…") | view | | `index.html:29-33`, `main.js:615-624` | UI-12 |
| `view.columns` | `actColumns` ("Columns…") | view | | `index.html:34-37`, `main.js:627-698` | UI-12 |
| `view.followTail` | `actFollowTail` ("Follow tail") | view | | `index.html:38`, `main.js:1057-1072` | UI-12 (`pressed`) |
| `view.theme` | `actTheme` ("Theme", `children` System/Light/Dark) | view | | `index.html:48`, `main.js:153-161` | UI-12 |
| `views.back` / `views.forward` | `actViewBack` / `actViewForward` | views | `Alt+ArrowLeft` / `Alt+ArrowRight` | `views-stack.js:215-226` | UI-12 (despacho na UI-28) |
| `views.narrow` | `actNarrow` ("Narrow this view") | views | | `views-stack.js:139-157` | UI-12 |
| `views.rename` | `actRenameView` ("Rename view…") | views | | `views-stack.js:192-198` | UI-12 |
| `views.showInParent` / `views.showInRoot` | `actShowInParent` / `actShowInRoot` | views | | `views-stack.js:160-187` | UI-12 |
| `navigate.nextError` / `prevError` | `actNextError` / `actPrevError` | navigate | `n` / `N` | `main.js:1243-1280` | UI-12 (despacho na UI-28) |
| `navigate.nextType` / `prevType` | `actNextType` / `actPrevType` | navigate | `t` / `T` | `main.js:1243-1280` | UI-12 (despacho na UI-28) |
| `navigate.nextHighlight` / `prevHighlight` | `actNextHighlight` / `actPrevHighlight` | navigate | `]` / `[` | `highlight-panel.js:331-366` | UI-12 (despacho na UI-28) |
| `navigate.focusSearch` | `actFocusSearch` ("Go to search") | navigate | `/` | (novo) | UI-28 |
| `navigate.nextRegion` / `prevRegion` | `actNextRegion` / `actPrevRegion` | navigate | `F6` / `Shift+F6` | (novo) | UI-28 |
| `record.bookmark` | `actBookmark` ("Bookmark") | record | `b` | `main.js:1044-1049` | UI-12 (`pressed`) |
| `record.compare` | `actCompare` ("Compare…") | record | `c` | `index.html:143`, `main.js:1003-1020` | UI-12 |
| `record.copy` | `actCopyRecord` ("Copy text") | record | | `index.html:142` | UI-20 |
| `record.context` | `actContext` ("Context", `children` Same source/Same thread/All sources) | record | | `index.html:139-141` | UI-20 |
| `record.filterType` | `actOnlyThisType` ("Only this message type") | record | | `main.js:343` (`onTemplate`) | UI-20 |
| `record.highlightType` | `actHighlightType` ("Highlight this message type") | record | | `highlight-panel.js:318-327` | UI-20 |
| `record.sameKey` | `actSameKey` ("Same key", `children` = chaves do registro) | record | | `links.js:83`, `main.js:839` | UI-24 |
| `record.sideBySide` | `actSideBySide` ("Normal and sensitive side by side") | record | | `main.js:838` | UI-24 |
| `record.onlySource` | `actOnlySource` ("Only this source") | record | | (novo) | UI-29 |
| `record.hideType` | `actHideType` ("Hide this message type") | record | | (novo) | UI-29 |
| `record.contextMenu` | `actContextMenu` ("Record menu") | record | `Shift+F10` (scope grid) | (novo) | UI-29 |
| `help.shortcuts` | `actShortcuts` ("Keyboard shortcuts") | help | `?` | `index.html:132` | UI-13 (lista simples), UI-28 (diálogo) |
| `help.palette` | `actPalette` ("Command palette…") | help | `Ctrl+k` | (novo) | UI-29 |

Regra de despacho na transição: **o campo `shortcut` é só declarativo até a UI-28.** Os ouvintes antigos (`main.js:1028`, `main.js:1244`, `highlight-panel.js:332`, `views-stack.js:215`) continuam funcionando até a UI-28 trocá-los, no mesmo PR, pelo despachante único. Nunca dois despachantes para a mesma tecla.

## 2. Doca inferior

### 2.1 Tipos

```js
/**
 * @typedef {Object} PanelDef
 * @property {string} id                 // "distinct"; igual ao sufixo da ação que abre
 * @property {string} titleKey           // chave de S; rótulo da aba (padrão: o mesmo labelKey da ação)
 * @property {(host: HTMLElement, ctx: AppContext) => PanelInstance|Promise<PanelInstance>} mount
 * @property {Array<"case"|"view"|"indexed"|"derived">} [listens]   // padrão []
 * @property {boolean} [closeOnCase]     // padrão false: fecha a aba quando o caso muda
 * @property {boolean} [needsCase]       // padrão true: sem caso, open() só mostra S.needCase
 */

/**
 * @typedef {Object} PanelInstance       // o que mount devolve; tudo opcional
 * @property {() => void|Promise<void>} [refresh]        // 1ª abertura, reabertura, ativação com dado velho
 * @property {(reason: string) => void} [update]         // aviso de listens com a aba ativa; padrão = refresh()
 * @property {(args: any) => void|Promise<void>} [show]  // abertura com argumentos (Compare, Same key, Operation)
 * @property {() => void} [closed]                       // aba fechada: abortar pedidos (flow.close/reset)
 * @property {HTMLElement} [focusTarget]                 // padrão: 1º h2/h3 do host (tabindex=-1)
 */

/** @typedef {"case"|"view"|"indexed"|"derived"} DockReason
 *  case    = caso mudou (onCase, main.js:438)
 *  view    = filtro ou visão mudou (applyView, main.js:398)
 *  indexed = indexação terminou ou coleta removida (onDone, main.js:512)
 *  derived = eventos derivados mudaram (refreshDerived, main.js:251: Follow tail, coleta escolhida)
 */
```

### 2.2 API (`js/dock.js`, DOM; regras em `js/dock-model.js`, puro)

```js
// Inicialização em duas etapas, IDÊNTICA em js/dock-legacy.js (UI-12) e js/dock.js (UI-15):
// a doca existe antes do AppContext (que a contém) e recebe o contexto depois.
export function createDock({ root, before = null, storage, onFocusOut }) -> Dock     // etapa 1: create(deps)
//   root: contêiner da doca. dock.js (UI-15): #dock; dock-legacy.js (UI-12): #grid-panel, com os hosts
//         inseridos antes de `before` (#filter-form). dock.js ignora `before`.
//   storage: localStorage ou null; onFocusOut(): devolve o foco ao grid
// dock.bind(ctx)                                                                    // etapa 2: bind(ctx)
//   uma vez, logo depois de criar o AppContext; register() pode vir antes ou depois;
//   open()/notify() antes de bind() lançam Error (erro de programação, não de uso)

Dock = {
  bind(ctx: AppContext): void          // etapa 2; chamar duas vezes lança
  register(def: PanelDef): void        // síncrono, na abertura; id repetido lança
  open(id, args?, { focus = true } = {}): Promise<boolean>
      // sem caso e needsCase: ctx.status.message(S.needCase) e false
      // 1ª vez: cria a aba e o host (role=tabpanel), await mount(host, ctx); falha do mount -> .ui-error no host
      // depois: show(args) se args !== undefined e show existe; senão refresh()
      // aba já aberta: só ativa (e show(args) se houver args)
      // focus: move o foco para focusTarget; guarda o elemento que tinha o foco para devolver ao fechar
  close(id): void                      // closed(); remove a aba; foco volta ao opener se ainda conectado, senão onFocusOut()
  closeAll(): void
  maximize(on?: boolean): void         // sem argumento alterna
  isOpen(id): boolean
  active(): string | null
  notify(reason: DockReason): void     // ver 2.4
  on(event: "open"|"close"|"activate"|"resize", fn): () => void
      // open/close/activate: {id}; resize: {height, fraction, maximized}
}
```

As instâncias ficam em cache durante a sessão: fechar a aba não destrói o estado do painel (como hoje, em que cada `init*` roda uma vez); reabrir chama `refresh()`.

### 2.3 Alturas, teclado e persistência (`js/dock-model.js`)

```js
export const DOCK_MIN_PX = 160;
export const GRID_MIN_ROWS = 8;           // + cabeçalho do grid
export const DOCK_DEFAULT_FRACTION = 0.40;
export const DOCK_STEP_FRACTION = 0.05;   // setas no divisor
export const SMALL_WINDOW_PX = 640;       // abaixo disso, a doca abre maximizada
export const STORAGE_KEY = "dmslog.dockHeight";

export function limits({ available, rowHeight, gridHead }) -> { min, max, maximizedOnly }
//   available = altura do contêiner grid+doca; max = available - (gridHead + GRID_MIN_ROWS * rowHeight)
//   maximizedOnly = max < DOCK_MIN_PX
export function initialHeight({ storedFraction, contentHeight, available, limits }) -> px
//   sem altura lembrada: clamp(DOCK_MIN_PX, contentHeight, DOCK_DEFAULT_FRACTION * available) (VR-09)
//   com altura lembrada: clamp(min, storedFraction * available, max)
export function keyStep(height, key, { available, limits }) -> px   // ArrowUp/Down ±5%, Home=min, End=max
export function valueNow(height, available) -> int                 // aria-valuenow, em %
export function parseStored(text) -> number | null                  // fração 0.1..1; inválido = null
export function shouldStartMaximized(windowHeight) -> boolean
export function tabsAfterClose(tabs, active, id) -> { tabs, active }  // ativa a vizinha da direita, senão a da esquerda
```

- `rowHeight` vem de `ROW_HEIGHT` (`js/grid-model.js:4`), nunca de um número literal; `gridHead` é lido uma vez de `--grid-head` (UI-05) ao montar.
- Persistência: só `dmslog.dockHeight` (fração da altura disponível, como texto `"0.42"`), gravada no fim do arraste ou da tecla, com `try/catch` (sem armazenamento = vale na sessão). Abas abertas e estado maximizado **não** são persistidos.
- Divisor: `role="separator"`, `aria-orientation="horizontal"`, `aria-valuemin`/`max`/`now` em %, `tabindex="0"`, `aria-label` = `S.dockResize`; setas, Home/End, duplo clique alterna 40% ↔ maximizada.
- Abas: `role="tablist"` com roving tabindex (só a ativa com `tabindex=0`; setas mudam, Home/End); o `×` de cada aba é botão irmão com `tabindex=-1` e `aria-label` = `format(S.dockCloseTab, {title})` (botão dentro de `role="tab"` é interativo aninhado); Delete com foco na aba fecha. À direita: Maximize/Restore (`aria-pressed`) e Close all.
- Esc com foco dentro do painel fecha a aba e devolve o foco, a menos que o evento já tenha sido tratado (`defaultPrevented`) por um menu, popover ou campo do painel.

### 2.4 Avisos (`notify`)

`notify(reason)` roda em duas passadas, nesta ordem:

1. **Só com `reason === "case"`: fechamento, independente de `listens`.** Todo painel montado com `closeOnCase: true` e aba aberta é fechado (`close(id)`, que chama `closed()`). Aba já fechada: nada (o `closed()` já rodou ao fechar). Painéis fechados nesta passada não recebem a passada 2.
2. **Aviso, só para painéis montados cujo `listens` inclui o motivo:**

| Situação da aba | Ação |
|---|---|
| aberta e ativa | `update(reason)` (padrão `refresh()`) |
| aberta e inativa | marca "velha"; ao ativar, `refresh()` uma vez |
| fechada | nada (reabrir sempre chama `refresh()`) |

`closeOnCase` e `listens: ["case"]` são independentes: `payloads`, `compare`, `sideBySide` e `sameKey` fecham na troca de caso sem declarar `"case"`.

#### 2.4.1 Inventário das chamadas de `main.js`: o que vira `notify` e o que fica

`dock.notify` só substitui chamadas a **painéis da doca**. Tudo o que não é painel da doca continua chamado diretamente por `main.js`, nos mesmos ganchos, e continua executando na troca de caso e depois da indexação. A ordem dentro de cada gancho é a de hoje; o `dock.notify(...)` entra no lugar da 1ª chamada de painel substituída.

| Gancho (`main.js`) | Chamada hoje | Depois |
|---|---|---|
| `refreshDerived` (`:251-261`) | `clockBanner.refresh()` (`:252-254`) | **fica** (aviso de relógio, barra de status) |
| | `eventsPanel.refresh()` se visível (`:255-257`) | `dock.notify("derived")` (`operations`) |
| | `dashboard.refresh()` se visível (`:258-260`) | idem (`dashboard`) |
| `applyView` (`:398-425`) | `grid.setTerms`, `detail.setTerms`, `detail.clear`, `grid.setView` (`:408-411`) | **ficam** |
| | `timeline.refresh()` (`:412-414`) | **fica** |
| | `distinctUi.viewChanged()` (`:415-417`) | `dock.notify("view")` (`distinct`, `groupByType`) |
| | `aggregateUi.viewChanged()` (`:418-420`) | idem (`aggregate`) |
| | `viewsUi.changed()` (`:421-423`) | **fica** (trilha; na UI-18 também `paintStrip()`) |
| `onCase` (`:438-495`) | `collectionsPanel.setCase()`, `add-files-host`, `detail.clear()`, `bookmarkStore.reset()`, `compareSelection.reset()` (`:440-447`) | **ficam** |
| | `bookmarksPanel.reset()` (`:448-450`) | `dock.notify("case")` (`bookmarks`: `update("case")`→`reset()`) |
| | `compare.reset()` (`:451-453`) | idem (`compare`, `closeOnCase`) |
| | `userFilter = null`, `filterTree.setCase/setFilter` (`:454-458`) | **ficam** (`filterTree` é guardado por `main`) |
| | `viewsUi.onCase` + `restoreLive` (`:459-464`) | **fica** |
| | `loadTemplates(id)`, `knowledge.reset()` (`:465-466`) | **ficam** |
| | `keyPanel.reset()` (`:467-469`), `sideBySide.reset()` (`:476-478`), `payloadList.close()` (`:479-481`) | `dock.notify("case")` (`closeOnCase`) |
| | `exportPanel.reset()` (`:470-472`) | idem (`export`: `update("case")`→`reset()`) |
| | `copyExport.reset()` (`:473-475`) | **fica** (não é painel da doca; popover e depois diálogo) |
| | `unknownPanel.refresh()`, `configPanel.refresh()` (`:482-487`) | `dock.notify("case")` (`listens` com `"case"`) |
| | `refreshDerived()` (`:488`) | **fica** (que chama `dock.notify("derived")` por dentro) |
| | `followTail.onCase()` (`:489-491`) | **fica** |
| | `alertsUi.onCase()` (`:492-494`) | **fica**, como `alertsFlow.onCase()` (SSE e banner, `startAlerts`) |
| `onDone` (`:512-528`) | `bookmarkStore.invalidate()`, `highlightStore.invalidateMarks()` (`:513-514`) | **ficam** |
| | `bookmarksPanel.refresh()` se aberto (`:515-517`) | `dock.notify("indexed")` (`bookmarks`) |
| | `loadTemplates(caseId)`, `knowledge.reset()` (`:518-519`) | **ficam** |
| | `unknownPanel.refresh()`, `configPanel.refresh()` (`:520-525`) | `dock.notify("indexed")` |
| | `refreshDerived()`, `applyView()` (`:526-527`) | **ficam** |

Os painéis com que `main.js` conversa diretamente (árvore de filtros: `setFilter`, `setCase`; destaques: `storeChanged`; bookmarks: `refresh` em edição, `main.js:231-233`) continuam com a variável em `main.js`, atribuída dentro do `mount`. Teste de fluxo da UI-12: trocar de caso com cada painel aberto e conferir que fecha ou recarrega conforme a tabela.

### 2.5 Contrato com o grid

- O grid é dono da regra, não a doca: o `ResizeObserver` que já existe (`grid.js:115-117`) passa a guardar `entry.contentRect.height` e chama `ensureSelectedVisible()`. Isso cobre doca (abrir, arrastar, maximizar), gavetas e trilho (UI-17) e mudança de breakpoint sem acoplamento entre `dock.js` e `grid.js`.
- Função pura em `js/grid-model.js`:

```js
export function topAfterResize({ selected, top, visibleBefore, visibleAfter, length }) -> number
//   selected < 0, ou fora de [top, top + visibleBefore): top inalterado (só limitado a maxTop)
//   selecionada visível e selected - top < visibleAfter: top inalterado (preserva a distância do topo)
//   selecionada sairia: max(0, selected - floor(visibleAfter / 3)), limitado a maxTop (1/3 da altura)
```

- `Grid.ensureSelectedVisible()`: público e idempotente. **Altura 0** (doca maximizada esconde o grid) não recalcula nada e não mexe em `top`. Nada roda durante a rolagem (o observer só dispara com mudança de tamanho). A altura guardada é a mesma que a UI-27 usa no `ensurePool` (hoje `grid.js:491` lê `clientHeight`).

### 2.6 Catálogo de painéis e migração de cada módulo

| Painel (`id`) | Módulo e montagem nova | `listens` / outros | Hoje (inserção) | Mudança no módulo |
|---|---|---|---|---|
| `filterTree` | `filter-tree.js:244` `initFilterTree(host, …)` (sem mudança) | `[]`; `main` guarda `filterTree` | `index.html:90`, `main.js:711-740` | nenhuma |
| `highlights` | `highlight-panel.js`: `mountHighlightPanel(host, opts)` | `[]`; `main` chama `storeChanged` | botão `:80`, painel `:138`, ajuda `:270` | UI-12 separa `mountHighlights` (`:72`) em painel, seção do detalhe (`:274-278`, fica) e navegação `[`/`]` (`:331-366`, sai na UI-28) |
| `bookmarks` | `bookmarks-panel.js:174` `initBookmarksPanel(host, {…, onRequestClose})` | `["case","indexed"]`; `update("case")`→`reset()`; `refresh`→`open()`, `closed`→`close()` | `index.html:100` | UI-15: `:287` (`host.hidden = true` no estado `idle`) passa a `onRequestClose()` |
| `distinct` / `groupByType` | `distinct-panel.js`: `mountDistinctPanel(host, opts)` e `mountGroupPanel(host, opts)` | `["view"]`; `closed`→`reset()` | botões e hosts `:376-377` | UI-12 troca `mountDistinct` (`:365-407`) pelas duas montagens; `initDistinct`/`initGroup` não mudam |
| `aggregate` | `aggregate-panel.js`: `mountAggregatePanel(host, opts)` | `["view"]`; `update("view")`→`markStale`; `refresh`→`open()`; `closed`→`close()` | `:581-582` | UI-12 troca `mountAggregate` (`:571-601`); `initPanel` não muda |
| `operations` | `events-panel.js:57` `initEventsPanel(host, …)` (sem mudança) | `["derived"]`; `show({eventId})`→`open(eventId)` | `index.html:93`, `main.js:1103-1142` | nenhuma; `openOperation` (`main.js:1130`) vira `ctx.dock.open("operations", {eventId})` |
| `dashboard` | `dashboard.js:64` `initDashboard(host, …)` | `["derived"]` | `index.html:95`, `main.js:1204-1227` | nenhuma |
| `payloads` | `payload-list.js:39` `initPayloadList(host, {…, onRequestClose})` | `[]`; `closeOnCase` | `index.html:94`, `main.js:863-880` | UI-15: `:209-213` (`host.hidden` + evento `payload-list-closed`) vira `onRequestClose()`; o ouvinte `main.js:867` sai |
| `unknownTypes` | `unknown-panel.js:57` `initUnknownPanel(host, …)` | `["case","indexed"]` | `index.html:91`, `main.js:742-767` | nenhuma |
| `config` | `config-panel.js:55` `initConfigPanel(host, …)` | `["case","indexed"]` | `index.html:92`, `main.js:769-793` | nenhuma |
| `alerts` | `alerts-panel.js`: `startAlerts({slot, …}) -> flow` (abertura) e `mountAlertsPanel(host, {flow, …})` (doca) | `[]` | `index.html:102`, `:52`, `main.js:1074-1101` | UI-12 separa `initAlerts` (`:99-425`): banner e SSE ficam na abertura; painel vai para a doca; `:394-411` (`host.hidden`, Esc próprio) saem na UI-15 |
| `export` | `export-panel.js:79` + `redaction-panel.js:42`, num host com dois filhos | `["case"]`; `update("case")`→`reset()` | `index.html:98-99`, `main.js:1144-1186` | UI-15: `redaction-panel.js:165` vira `onRequestClose`; vira diálogo na UI-23 e sai da doca |
| `compare` | `compare.js:32` `initCompare(host, {…, onRequestClose})` | `closeOnCase`; `show({a, b})` | `index.html:101`, `main.js:1021-1025` | UI-15: `:110-116` |
| `sideBySide` | `side-by-side.js:32` `initSideBySide(host, {…, onRequestClose})` | `closeOnCase`; `show({entryId})` | `index.html:96`, `main.js:833` | UI-15: `:76-82` |
| `sameKey` | `key-panel.js:34` `initKeyPanel(host, {…, onRequestClose})` | `closeOnCase`; `show({type, value})` | `index.html:97`, `main.js:834` | UI-15: `:103-110`; colunas e "Show in grid" na UI-24 |

Fora da doca: `copy-export.js` (popover ancorado no menu Export a partir da UI-12, diálogo na UI-23; exporta `open()`/`close()`), o banner de relógio (`clock-banner.js`, vai para a barra de status), a seção de payload do detalhe (`payload.js`), conhecimento, links e bookmark do detalhe (UI-20).

`onRequestClose` é uma opção nova com padrão compatível (`() => { host.hidden = true; }`), para que os testes de fluxo atuais continuem válidos até a UI-15 trocá-la por `() => ctx.dock.close(id)`.

### 2.7 Doca provisória da UI-12 (`js/dock-legacy.js`)

Mesma API da seção 2.2, inclusive a inicialização em duas etapas (`createDock({ root: byId("grid-panel"), before: byId("filter-form"), storage, onFocusOut })` e depois `dock.bind(ctx)`), sobre o layout atual: `register` guarda o `PanelDef`; o 1º `open` cria `<section class="panel host-panel" id="<id>-host">` em `#grid-panel` antes do formulário de filtro (onde ficam hoje, `index.html:90-102`); `open`/`close` alternam `host.hidden`; `isOpen` lê `!host.hidden` (por isso os módulos que ainda se escondem sozinhos continuam coerentes); `notify` com as regras de 2.4; `maximize` e `on("resize")` não fazem nada. A UI-15 apaga o arquivo e troca, em `main.js`, só o caminho do `import { createDock }` e as opções de `createDock` (`root: byId("dock")`, sem `before`); `bind(ctx)` e `app-actions.js` não mudam.

## 3. Barra de status

### 3.1 Tipos e API (`js/status-bar.js`, DOM; `js/status-bar-model.js`, puro)

```js
export function createStatusBar(root, { S }) -> StatusBar      // root = #status-bar (index.html, UI-13)

StatusBar = {
  count(value: { n: number, exact: boolean } | "loading" | null, { announce = false } = {}): void
      // "285,093 records" / "about 1.2M records" (.is-estimate); "loading" mantém o último número esmaecido
      // announce=true só quando a contagem chega depois de mudança de filtro ou visão
  position(ts: number | null): void                    // "from 2030-01-15 10:00:00.014"; nunca anunciado
  progress(value: { value: number|null, max: number|null, text: string } | null): void
      // indexação: <progress> (value null = indeterminado) + texto já formatado; null esconde
  message(text: string, { kind = "info", timeoutMs } = {}): { dismiss(): void }
      // kind: "info"|"success"|"warning"|"error"; text "" limpa; a última mensagem substitui a anterior
      // timeoutMs padrão: info/success/warning 8000; error 0 (fica, com botão fechar S.sbDismiss)
  connection(state: "online"|"offline"|"forbidden", { onRetry } = {}): void
      // offline/forbidden trocam o conteúdo da própria barra (.is-error, role="alert", --status-error-*),
      // com "Retry now" (offline) ou só o texto (forbidden); "online" restaura os segmentos
  slot(id: "clock"|"alerts"): HTMLElement               // segmento de largura limitada para módulos que
      // desenham o próprio aviso (clock-banner.js, alerts-panel.js); escondido quando vazio
}
```

Puro (`status-bar-model.js`): `formatCount(value, S, plural)`, `messageTimeout(kind)`, `createMessageState()` com `show(text, opts, now)`, `expire(now)` (testável sem relógio real), `compactNumber(n)` ("1.2M").

### 3.2 Regiões vivas

Uma só região viva de mensagem (`role="status"`, `aria-live="polite"`) e um anunciador escondido para a contagem com `announce`. `#grid-status` (`index.html:131`) deixa de existir; `position` nunca anuncia (WCAG 4.1.3, achado §8 #8). No modo `offline`/`forbidden`, `role="alert"`.

### 3.3 Pontos de chamada (o que substitui o quê)

| Hoje | Depois (UI-13) |
|---|---|
| `#message` (`index.html:51`) e `showMessage`/`showError` (`main.js:144-151`) | `ctx.status.message(text, {kind})` via adaptador (3.5); nenhum módulo muda |
| `onForbidden` (`main.js:151`) | `ctx.status.connection("forbidden")` |
| `onStatus` do grid (`main.js:298-300`; texto montado em `grid.js:303-313`, `S.loading` em `:321`) | `Grid` ganha `onCount(value, {announce})` e `onPosition(ts)`; `refreshCount` (`grid.js:275`) passa `announce` = veio de `reload()` |
| Barra de progresso da lateral (`index.html:76-79`, `sources.js:389-431`) | `SourcesPanel` ganha `handlers.onProgress(value|null)` → `ctx.status.progress` |
| `#clock-banner` (`index.html:88`, `main.js:1229-1241`) | `initClockBanner(ctx.status.slot("clock"), …)` (sem mudança no módulo além do contêiner) |
| `#alert-banner` (`index.html:52`) | `startAlerts({slot: ctx.status.slot("alerts"), …})` |
| `.shortcuts-help` (`index.html:132`, `highlight-panel.js:268-271`, `views-stack.js:75-78`) | sai; botão `?` da barra chama `help.shortcuts` (lista simples gerada do registro; diálogo na UI-28) |
| Erros de formulário longe do campo (`sources.js:366`, `case-files.js:208-232`, `main.js:597`, `:619`) | junto do campo: `setFieldError(input, text|null)` em `js/field-error.js` (UI-13; `aria-invalid`, `.ui-field-error` da UI-10, `aria-describedby`) |

### 3.4 Erros

- A barra só mostra texto; traduzir código interno (`no_operation_for_key`) é de quem chama, pelo auxiliar da UI-09.
- Erro de painel fica no corpo do painel (`.ui-error`, UI-10/UI-23), não na barra.
- `connection("offline")` só passa a ser usado na UI-25 (falha de rede e "Retry" com `/api/health`); antes dela, só `forbidden`.

### 3.5 Adaptador de transição

Até a UI-13 existir, `ctx.status` é um objeto mínimo em `main.js` com a mesma API, sobre o `#message` atual:

```js
const status = {
  message: (text, { kind = "info" } = {}) => { showMessage(text, kind === "error"); return { dismiss: () => showMessage("", false) }; },
  count() {}, position() {}, progress() {}, connection() {}, slot: () => null,
};
```

Depois da UI-13, o inverso: `showMessage = (text, isError) => status.message(text, { kind: isError ? "error" : "info" })`, e todos os `onMessage`/`showMessage(text, isError)` dos módulos (`sources.js`, `case-files.js`, `collections-panel.js`, `views-stack.js`, `highlight-panel.js`) continuam funcionando sem mudança.

## 4. Fichas de filtro

### 4.1 Entrada: estado vivo, não o JSON composto

O filtro enviado ao servidor (`composeFilter`, `filters.js:165-174`; `applyView`, `main.js:398-425`) perde a origem de cada condição (`{sev:{le:4}}` pode ser "Hide noise" ou "Severity at least Warning"). Remover uma ficha precisa saber de onde ela veio. Por isso o modelo trabalha sobre o **estado vivo** que `views-stack-model.js` já define (`emptyState()`, `:15-23`; `captureLive`/`restoreLive`, `main.js:369-394`) mais a seleção da lateral:

```js
/** @typedef {Object} LiveState            // = views-stack-model emptyState()
 * @property {string} searchText
 * @property {{sev: string, level: string, from: string, to: string}} form          // textos crus dos campos
 * @property {{sevMax: number|null, levelMax: number|null, from: number|null, to: number|null}} formState
 * @property {{hideNoise: boolean, unclassified: boolean}} toggles
 * @property {object|null} userFilter      // árvore + faixa da timeline + cliques de tipo + drill-down
 */
/** @typedef {{sourceIds: number[], collectionIds: number[]}} Selection   // lateral; vale para todas as visões */
/** @typedef {Object} ChipLookups
 * @property {(id: number) => string} sourceName
 * @property {(group: number) => string} collectionTag      // "C2"
 * @property {(tplId: number|string) => string} typeLabel  // título do tipo de mensagem
 * @property {(ts: number) => string} formatTs              // time.js
 */
/** @typedef {Object} Chip
 * @property {string} id         // estável para um mesmo estado: "sources", "collections", "text", "sev", "level",
 *                               // "time", "noise", "unclassified", "node:<i>", "tree"
 * @property {"sources"|"collections"|"text"|"sev"|"level"|"time"|"noise"|"unclassified"|"node"|"tree"} kind
 * @property {string} label      // formatado com S; pode conter texto do usuário -> sempre textContent
 * @property {string} title      // texto completo (o label é cortado em 32 caracteres com "…")
 * @property {boolean} removable
 */
```

### 4.2 Funções (`js/filter-chips-model.js`, puro)

```js
export function chipsFromState(live, selection, lookups, S) -> Chip[]
//   ordem: sources, collections, text, sev, level, time, noise, unclassified, node:0..n, tree
export function removeChip(live, selection, chipId) -> { live: LiveState, selection: Selection }
//   nunca muta a entrada; remove de form (texto cru) E de formState juntos
export function chipLabel(node, lookups, S) -> string | null   // folha simples -> rótulo; null = não é simples
export function fitChips(widths, available, moreWidth) -> number   // quantas cabem antes de "+N more"
export function isFiltered(live, selection, parentsFilter) -> boolean   // estado "Filtered" da faixa
```

### 4.3 Regras

| Origem | Ficha | Remover faz |
|---|---|---|
| `selection.sourceIds` | `S[pluralKey("chipSource", n)]`: 1 fonte = `Source {name}`, várias = `{count} sources` | `sourceIds = []` |
| `selection.collectionIds` | `C1, C2` (tags) | `collectionIds = []` |
| `searchText` | `text "{text}"` | `searchText = ""` |
| `formState.sevMax` | `Severity at least {name}` | `sevMax = null`, `form.sev = ""` |
| `formState.levelMax` | `Level at least {name}` | `levelMax = null`, `form.level = ""` |
| `formState.from/to` | `{from} to {to}` / `from {from}` / `until {to}` | ambos `null`, `form.from = form.to = ""` |
| `toggles.hideNoise` / `unclassified` | `Hide noise` / `Unclassified only` | `false` |
| nó `i` do topo de `userFilter` (AND), folha simples ou `not` de folha simples | `chipLabel(node)` / `not {label}` | tira o nó `i` (lista do topo, como `topNodes`/`wrapNodes` de `filters.js:97-107`) |
| resto de `userFilter` (`or`, `and` aninhado, `not` de grupo) | **uma** ficha `Filter tree ({n} conditions)`, **sem ×** | clicar abre `toggle.filterTree` |

- Folhas simples: os tipos de `store/filters.py:12-22` e `_LEAVES` (`:676-698`): `source`, `stream`, `family`, `sensitive`, `sev`, `level`, `ts`, `tid`, `pid`, `event`, `tpl`, `text`, `key`, `event_status`, `event_name`, `event_kind`, `flags`, `field`, `payload`, `collection`. Cada uma tem um modelo em `strings.js` (`chipLeaf.*`); tipo sem modelo cai na ficha agregada (nunca quebra).
- `tpl` mostra o título do tipo (`typeLabel`), nunca o código (decisão da 9.19); `source` e `collection` no `userFilter` (drill-down, "Only this source") usam `sourceName`/`collectionTag`.
- Fichas cobrem só a visão ativa e a seleção da lateral. As condições dos ancestrais aparecem na trilha (`stack.crumbs()`, `views-stack-model.js:227-229`), não como fichas.
- A remoção é aplicada por `main.js` numa passada só: `restoreLive(live)`; se a seleção mudou, `panel.clearSelection()`/`collectionsPanel.clearSelection()` (`collections-panel.js:159` já existe; `SourcesPanel.clearSelection()` é novo, UI-18); depois **um** `applyView()`.
- `describeNode` de `views-stack-model.js:40-91` monta nomes com palavras fixas em inglês fora do `strings.js` (`"template "`, `" in "`); a UI-18 faz `describeNode` delegar a `chipLabel`, para trilha e fichas usarem o mesmo texto.
- Medição: só quando as fichas ou a largura da faixa mudam (`ResizeObserver` na faixa), nunca durante a rolagem do grid. `fitChips` recebe larguras já medidas.

## 5. Regiões (F6) e ordem de tab

### 5.1 Regiões

```js
/** @typedef {Object} Region
 * @property {"bar"|"side"|"strip"|"grid"|"dock"|"detail"} id
 * @property {string} labelKey                        // aria-label do marco
 * @property {() => HTMLElement|null} root
 * @property {() => HTMLElement|null} focusTarget     // onde o F6 pousa na 1ª vez
 * @property {() => boolean} available                // visível e não recolhida
 */
```

| Ordem | `id` | Raiz | Pouso do F6 | Disponível quando |
|---|---|---|---|---|
| 1 | `bar` | `#toolbar` (`header role=banner`, `index.html:21`) | seletor de caso | sempre |
| 2 | `side` | `#sources-panel` (`aside`, `index.html:55`) | 1º controle; com trilho (UI-17), o botão do trilho | sempre (trilho conta) |
| 3 | `strip` | faixa de filtro (UI-18; antes, `#filter-form`, `index.html:103`) | trilha ou 1ª ficha | há caso |
| 4 | `grid` | `#grid-viewport` (`index.html:126`) | o próprio viewport | há caso |
| 5 | `dock` | `#dock` (UI-15) | aba ativa | alguma aba aberta |
| 6 | `detail` | `#detail-panel` (`aside`, `index.html:135`) | título do detalhe | coluna visível ou gaveta aberta (UI-17) |

- **Decisão:** a ordem do F6 segue a do DOM e a do Tab (doca antes do detalhe), para o F6 e o Tab andarem na mesma ordem (WCAG 2.4.3). A versão anterior (detalhe antes da doca) foi corrigida nas outras fontes para ficarem iguais a esta: `docs/analises-ui/CONSOLIDADO.md` (UI-28, Proposta) e `docs/UI-HANDOFF.md` (seção 8).
- `F6`/`Shift+F6` vão para a próxima/anterior região **disponível**, com volta ao início. Ao voltar a uma região, o foco pousa no último elemento focado nela, se ainda conectado e visível; senão em `focusTarget`.
- Com `<dialog>` modal aberto, F6 não age (o diálogo prende o foco). Com menu ou popover aberto, F6 fecha-o e então muda de região.
- Puro (`js/shortcuts-model.js`, UI-28): `nextRegion(order, availableIds, currentId, dir) -> id`; `currentRegion` é decidido no DOM por `root.contains(document.activeElement)`. As definições (`js/regions.js`, DOM) são da UI-28.

### 5.2 Ordem de tab

- Ordem do DOM = ordem visual; nada de `tabindex` > 0.
- 1º focável: skip link "Skip to log entries" (`S.skipToGrid`), visível só no foco, leva ao `#grid-viewport` (UI-28).
- **Uma parada por composto, com roving tabindex:** cada `.ui-menu` (UI-10); a `tablist` da doca; a lista de fichas (setas andam entre fichas e seus `×`; Delete/Backspace remove a ficha focada); o grid (um viewport, linhas não focáveis; `aria-activedescendant` na UI-26). A lista de fontes (hoje uma parada por checkbox, `sources.js:206-285`) é da UI-21.
- Esc fecha o que tiver foco (menu, popover, gaveta, aba da doca) e devolve o foco a quem abriu.

## 6. Modelo puro × DOM

| Arquivo | Natureza | Testes (`tests/web/`) | PR |
|---|---|---|---|
| `js/actions.js` | puro | `actions.test.mjs`: registro, duplicatas, KeySpec, `match` com letras desligadas e campo de texto, `preload` com falha, `invoke` nunca rejeita, `toMenuItems` | UI-12 |
| `js/toolbar-menu-model.js` | puro | menus a partir do registro, desabilitado sem caso | UI-12 |
| `js/dock-model.js` | puro | `dock-model.test.mjs`: mínimos, `clamp`, VR-09, passo 5%, `parseStored`, `tabsAfterClose`, janela < 640 | UI-15 |
| `topAfterResize` em `js/grid-model.js` | puro | `grid-model.test.mjs`: visível preservada, 1/3, sem seleção, altura 0 | UI-15 |
| `js/status-bar-model.js` | puro | contagem exata/estimada, plural, expiração por tipo, `""` limpa | UI-13 |
| `js/filter-chips-model.js` | puro | todos os tipos de 4.3 viram ficha e a remoção tira a condição; ficha agregada; `fitChips` | UI-18 |
| `js/shortcuts-model.js` | puro | letras desligadas, foco em campo, `n` sem seleção, `nextRegion` | UI-28 |
| `js/commands-model.js` | puro | filtro por rótulo, ordem, desabilitados, toda ação de menu na paleta | UI-29 |
| `js/toolbar-menu.js`, `js/dock-legacy.js` | DOM | fluxo: cada painel abre pelo menu | UI-12 |
| `js/status-bar.js`, `js/field-error.js` | DOM | mensagem não move o grid; erro de campo com `aria-invalid` | UI-13 |
| `js/dock.js` | DOM | fluxo: abrir, Esc, foco, `notify` | UI-15 |
| `js/filter-chips.js` | DOM | fluxo: remover ficha recarrega uma vez | UI-18 |
| `js/shortcuts.js`, `js/regions.js` | DOM | fluxo do diálogo `?` e F6 | UI-28 |
| `js/commands.js`, `js/context-menu.js` | DOM | teclado do menu de contexto e da paleta | UI-29 |

## 7. Trechos-guia por PR para a sessão principal

Os agentes entregam os arquivos exclusivos no worktree e, no relatório, o trecho de cada ponto compartilhado; a sessão principal aplica. Abaixo, o que cada PR precisa nesses pontos.

### PR 10 · UI-11 (este)

Só documentação: este arquivo e o bloco da seção 10 deste documento em `docs/ARQUITETURA.md` (seções 11 e 15). Acrescentar `js/app-actions.js` à lista de pontos compartilhados (CONSOLIDADO §5 e CLAUDE.md, se a sessão principal mantiver a lista lá).

### PR 11 · UI-10 (componentes)

- `js/ui-menu.js` (já entregue): `createMenu({ trigger, getItems, label, onOpenChange, doc })` aceita `MenuItem[]` (seção 1.4) e chama `item.preload()` em `pointerenter`/`focus`; `createChip({ doc, label, removeLabel, onRemove, title })`.
- `.ui-chip` com `×` de `aria-label` = `removeLabel` = `format(S.chipRemove, {label})`.
- `index.html`: `<link rel="stylesheet" href="/css/components.css">` depois de `tokens.css` e antes de `app.css`.
- `strings.js`: `chipRemove: "Remove {label}"`.

### PR 12 · UI-12 (barra, registro de ações, doca provisória)

Arquivos do agente: `js/actions.js`, `js/toolbar-menu.js`, `js/toolbar-menu-model.js`, `js/dock-legacy.js`, `css/toolbar.css`, `js/theme.js`, montagens de `distinct-panel.js`, `aggregate-panel.js`, `highlight-panel.js`, `copy-export.js`, separação de `alerts-panel.js`, testes de fluxo ajustados. A sessão principal aplica:

```js
// main.js, em start(), depois de grid/detail e antes dos painéis
const actions = createActionRegistry({ onError: (err) => showError(err), singleKeysOn: () => true });
// etapa 1: create(deps); a doca existe antes do contexto que a contém
const dock = createDock({ root: byId("grid-panel"), before: byId("filter-form"), storage: safeLocalStorage(),
  onFocusOut: () => byId("grid-viewport").focus() });            // import de ./dock-legacy.js
const ctx = Object.freeze({
  caseId: () => caseId, filter: () => currentFilter, userFilter: () => userFilter,
  selectedRow: () => grid.selectedRow(), entry: () => detail.entry,
  setUserFilter, reveal: revealEntry, actions, dock, status,
});
dock.bind(ctx);                                                  // etapa 2: bind(ctx)
registerAppPanels(dock, deps);
registerAppActions(actions, deps);
mountToolbar(byId("toolbar"), { actions, dock, ctx });
// substituir SÓ as chamadas marcadas como dock.notify no inventário 2.4.1; as marcadas "fica"
// (clockBanner, copyExport, followTail, alertsFlow, filterTree, viewsUi, timeline...) continuam
```

```js
// app-actions.js (novo, ponto compartilhado): um bloco por painel
dock.register({ id: "distinct", titleKey: "actDistinct", listens: ["view"],
  mount: async (host, ctx) => (await import("./distinct-panel.js")).mountDistinctPanel(host, deps.distinctOptions(ctx)) });
actions.register({ id: "analyze.distinct", labelKey: "actDistinct", group: "analyze",
  run: (ctx) => ctx.dock.open("distinct") });
```

- `index.html`: barra de uma linha (36 px) no lugar de `:21-49`; remover os hosts `:90-102` (a doca provisória os cria).
- `main.js`: remover os blocos de botão e host de `:742-767`, `:769-793`, `:863-880`, `:882-936` (montagens trocadas), `:970-1001`, `:1074-1101`, `:1103-1142`, `:1144-1202`, `:1204-1227`; `:153-161` (tema) e `:627-698` (Columns vira diálogo, com o mesmo `buildColumnMenu`).
- `strings.js`: as chaves `act*` da tabela 1.6 que a UI-12 registra, `actGroup*` (cabeçalhos), `actionUnavailable`, `keyNames` (`ArrowLeft: "Left"`, …), `themeSystem`; tirar as chaves dos botões removidos (`toolbarUnknown`, `toolbarEvents`, `toolbarDashboard`, `toolbarConfig`, `toolbarExport`, `dstButton`, `grpButton`, `aggButton`, `hlButton`, `bmButton`, `plgButton`, `alToolbar`, `themeToggle`, `themeLight`, `themeDark`), conferindo antes que nenhum outro uso resta.

### PR 13 · UI-13 (barra de status)

Arquivos do agente: `js/status-bar.js`, `js/status-bar-model.js`, `css/status-bar.css`, `js/field-error.js`, `js/clock-banner.js`, `js/sources.js` e `js/case-files.js` (erros de campo e `onProgress`), `js/views-stack.js` (mensagem junto do Narrow), `js/grid.js` (`onCount`/`onPosition`). A sessão principal aplica:

```js
const status = createStatusBar(byId("status-bar"), { S });
const showMessage = (text, isError) => status.message(text, { kind: isError ? "error" : "info" });
onForbidden(() => status.connection("forbidden"));
// Grid: onStatus (:298) -> onCount: (v, o) => status.count(v, o), onPosition: (ts) => status.position(ts)
// SourcesPanel handlers: onProgress: (p) => status.progress(p)
// clock: initClockBanner(status.slot("clock"), …); alerts: startAlerts({ slot: status.slot("alerts"), … })
actions.register({ id: "help.shortcuts", labelKey: "actShortcuts", group: "help", shortcut: "?",
  enabled: () => true, run: () => deps.openShortcutList() });   // lista simples até a UI-28
```

- `index.html`: remover `:51`, `:52`, `:76-79`, `:88`, `:131`, `:132`; acrescentar `<footer id="status-bar" class="ui-statusbar"></footer>` como último filho de `#app`.
- `strings.js`: `statusRecordsOne/Other`, `statusRecordsApprox`, `statusFrom`, `statusIndexing`, `sbDismiss`, `sbRetry`, `sbOffline`; `shortcutsHelp`, `hlShortcutsHelp`, `vsShortcutsHelp` saem.

### PR 14 · UI-14 (abertura rápida)

Arquivos do agente: `js/boot.js`, `js/sources.js` (pedidos em paralelo). A sessão principal aplica:

- Em `app-actions.js`, `load` em cada ação de painel, com o mesmo especificador do `mount`: `load: () => import("./distinct-panel.js")`. O menu da UI-12 já chama `preload` no hover e no foco.
- `main.js`: nenhum `import()` de painel no `start()`. Ficam na abertura: grid, detalhe e suas seções (conhecimento, links, payload, bookmark, destaque), fontes, filtros, timeline, trilha (se houver estado salvo), Follow tail, `startAlerts` e aviso de relógio.
- `index.html`: `<script type="module" src="/js/boot.js">` no lugar de `main.js`; `<link rel="modulepreload">` do grafo estático de `main.js`.
- `strings.js`: nada além de `actionUnavailable` (já da UI-12).

### PR 15 · UI-15 (doca)

Arquivos do agente: `js/dock.js`, `js/dock-model.js`, `css/dock.css`, `js/grid.js` e `js/grid-model.js` (2.5), `onRequestClose` em `bookmarks-panel.js`, `payload-list.js`, `compare.js`, `side-by-side.js`, `key-panel.js`, `redaction-panel.js`, e retirada do Esc e do `host.hidden` de `alerts-panel.js`. A sessão principal aplica:

```js
// main.js: só muda o import (./dock-legacy.js -> ./dock.js) e as opções; bind(ctx) e app-actions.js não mudam
const dock = createDock({ root: byId("dock"), storage: safeLocalStorage(), onFocusOut: () => byId("grid-viewport").focus() });
// ... (ctx criado como antes)
dock.bind(ctx);
// app-actions.js: nas opções desses módulos, onRequestClose: () => ctx.dock.close("<id>")
```

- `index.html`: `<section id="dock" class="ui-dock" data-i18n-aria-label="dockLabel" hidden></section>` depois de `#grid-viewport` e do `#follow-new`, dentro de `#grid-panel`.
- Apagar `js/dock-legacy.js` e o ouvinte `payload-list-closed` (`main.js:867`).
- `strings.js`: `dockLabel`, `dockResize`, `dockCloseTab`, `dockMaximize`, `dockRestore`, `dockCloseAll`, `panelLoadFailed`.

### PR 16 · UI-16 (colunas)

Nenhuma das cinco interfaces muda. `strings.js`: rótulo acessível do selo `S` das fontes sensitive. `view.columns` continua abrindo o mesmo diálogo.

### PR 17 · UI-17 (responsivo)

Arquivos do agente: `js/drawers.js` (+ modelo e teste), seção responsiva de `css/app.css`. A sessão principal aplica:

```js
const drawers = createDrawers({ side: byId("sources-panel"), detail: byId("detail-panel") });
actions.register({ id: "view.sources", labelKey: "actSources", group: "view", enabled: () => true,
  pressed: () => drawers.isOpen("side"), run: () => drawers.toggle("side") });
actions.register({ id: "view.detail", labelKey: "actDetail", group: "view", enabled: () => true,
  pressed: () => drawers.isOpen("detail"), run: () => drawers.toggle("detail") });
```

- `drawers` expõe `isOpen(id)`, `open(id)`, `close(id)`, `toggle(id)` e `on("change", fn)`; a UI-28 usa isso em `Region.available()`.
- O grid não precisa de nada: o `ResizeObserver` da 2.5 cobre a troca de breakpoint e a gaveta.
- `index.html`: trilho e botão "Detail"; `strings.js`: `actSources`, `actDetail`, rótulo do trilho com contagem.

### UI-18 (faixa de filtro)

Arquivos do agente: `js/filter-chips.js`, `js/filter-chips-model.js` (+ testes), `js/views-stack.js` (monta num `host` da faixa, não mais em `parent`/`beforePanel`), `js/views-stack-model.js` (`describeNode` delega a `chipLabel`), `css/views-stack.css`, `js/filters.js`, `js/sources.js` (`clearSelection()`). A sessão principal aplica:

```js
const chipLookups = { sourceName: (id) => sourceNames.get(id) || String(id), collectionTag: (g) => tagOf(g),
  typeLabel: (t) => templateLabels.get(t) || String(t), formatTs };
const paintStrip = () => strip.update(chipsFromState(captureLive(), selectionNow(), chipLookups, S),
  { filtered: isFiltered(captureLive(), selectionNow(), viewsStack && viewsStack.parentsFilter()) });
// applyView(): chamar paintStrip() no fim
strip.onRemove((chipId) => {
  const { live, selection } = removeChip(captureLive(), selectionNow(), chipId);
  restoreLive(live);
  if (selection.sourceIds.length === 0) { panel.clearSelection({ quiet: true }); }
  if (selection.collectionIds.length === 0) { collectionsPanel.clearSelection({ quiet: true }); }
  applyView();   // uma recarga só
});
```

- Os `clearSelection` ganham `{ quiet }` para não disparar o próprio `onSelection` (senão a remoção recarrega duas vezes).
- `index.html`: faixa (`--strip-height`) no lugar de `:103-124`; os campos de Severity, Level, From, To, Hide noise e Unclassified vão para o popover "Filters ▾" (Level em "Advanced").
- Ações do grupo `views` passam a aparecer no menu `⋯` da faixa.
- `strings.js`: `stripFiltered`, `stripNoFilters`, `chipMore`, `chipSourceOne`/`chipSourceOther` (par de `pluralKey("chipSource", n)`: One = `Source {name}`, Other = `{count} sources`), `chipText`, `chipSev`, `chipLevel`, `chipTimeRange`, `chipTimeFrom`, `chipTimeUntil`, `chipHideNoise`, `chipUnclassified`, `chipTreeOne`/`chipTreeOther`, `chipNot`, `chipLeaf` (um modelo por tipo de folha), `actFilters`.

### UI-24 (seguir uma chave)

Arquivos do agente: `js/key-panel.js`, `js/key-panel-model.js`, `css/key-panel.css`, `js/links.js` e `js/views-stack-model.js` (`openChild`). A sessão principal aplica:

```js
actions.register({ id: "record.sameKey", labelKey: "actSameKey", group: "record",
  enabled: (ctx) => Boolean(ctx.entry() && (ctx.entry().keys || []).length),
  children: (ctx) => keyChildren(ctx.entry(), S),          // label = "{type} {value}", via textContent
  run: (ctx, key) => ctx.dock.open("sameKey", key) });
// "Show in grid" do painel: deps.openChildView({ ...emptyState(), userFilter: { key: { type, eq: value } } })
```

- `views-stack-model.js` ganha `openChild(state)`: com filtro próprio na visão ativa, empilha uma filha com `state`; sem filtro próprio, aplica `state` na visão ativa (nível vazio não vale a pena). Retorna `{ ok, reason, state, pushed }`; o limite `maxDepth` vale igual.
- `main.js`: `deps.openChildView` faz `openChild` → `restoreLive` → `applyView` → `viewsUi.changed()`.
- `links.js:83`: `onSameKey` passa a `ctx.actions.invoke("record.sameKey", ctx, {type, value})`.

### UI-28 (teclado)

Arquivos do agente: `js/shortcuts.js`, `js/shortcuts-model.js` (+ testes), `js/regions.js`, `js/highlight-panel.js` (sai o ouvinte `:332`). A sessão principal aplica, no mesmo PR:

```js
const prefs = createShortcutPrefs(safeLocalStorage());          // dmslog.singleKeys, padrão on
// actions criado com singleKeysOn: () => prefs.singleKeys()
installShortcuts({ actions, ctx, regions: createRegions({ dock, drawers }), prefs });
// remover os ouvintes antigos: main.js:1028-1055 e :1243-1280; views-stack.js:215-226
```

- `navigate.nextError`/`prevError` sem seleção começam do primeiro/último registro visível (UI-HANDOFF, seção 8).
- `index.html`: skip link como 1º filho de `<body>`.
- `strings.js`: `skipToGrid`, `scTitle`, `scSingleKeys`, `scOn`, `scOff`, `actFocusSearch`, `actNextRegion`, `actPrevRegion`, nomes de região para `aria-label`.

### UI-29 (menu de contexto e paleta)

Arquivos do agente: `js/commands.js`, `js/commands-model.js`, `css/commands.css`, `js/context-menu.js` (+ testes). A sessão principal aplica:

```js
actions.register({ id: "help.palette", labelKey: "actPalette", group: "help", shortcut: "Ctrl+k",
  enabled: () => true, run: () => palette.open() });
actions.register({ id: "record.contextMenu", labelKey: "actContextMenu", group: "record",
  shortcut: "Shift+F10", scope: "grid", run: (ctx) => contextMenu.openForSelection() });
actions.register({ id: "record.onlySource", labelKey: "actOnlySource", group: "record",
  enabled: (ctx) => Boolean(ctx.selectedRow()),
  run: (ctx) => ctx.setUserFilter(andFilters(ctx.userFilter(), { source: [ctx.selectedRow().source_id] })) });
```

- O menu de contexto lista `actions.list({ group: "record" })` **sem** `record.contextMenu` (o próprio lançador não aparece no menu que ele abre), com `args = { row }`; a paleta lista `actions.list()` menos `help.palette`. Teste do `commands-model`: nenhum dos dois lançadores aparece na própria lista.
- O grid precisa de `onContextMenu({ row, x, y })` (clique direito e tecla Menu selecionam a linha antes de abrir). O trecho de `grid.js` entra **no próprio PR da UI-29** (que vem antes da UI-30 na sequência canônica); se a UI-30 for desenvolvida em paralelo, ela incorpora a `main` resultante (com o `onContextMenu`) antes de abrir o seu PR.
- `strings.js`: `actPalette`, `actContextMenu`, `actOnlySource`, `actHideType`, `cmdPlaceholder`, `cmdEmpty`.

## 8. Compatibilidade

### 8.1 Textos (UI-09)

- O registro só guarda **chaves**; a UI-09 muda valores e não afeta contratos. As chaves novas (`act*`, `chip*`, `status*`, `dock*`, `sc*`, `cmd*`) já nascem nos termos da UI-09: **record**, **message type**, **operations**, **collection**, **source**; "…" em toda ação que abre diálogo; nada de "(s)"; severidade como "At least {name}".
- Plural sempre pelo auxiliar da UI-09 (`fooOne`/`fooOther`): contagem da barra de status, `{n} sources`, `Filter tree ({n} conditions)`, `+{n} more`.
- Teste novo (UI-12): toda `labelKey`, `titleKey` e `disabledKey` registrada existe em `S` e é texto não vazio.

### 8.2 Tokens (UI-05)

| Peça | Tokens |
|---|---|
| Barra | `--bar-height` (36), `--control-height` (26), `--z-menu`, `--surface-overlay`, `--shadow-overlay`, `--radius-lg` (menus) |
| Doca | `--z-dock`, `--surface-panel`, `--line`, `--focus-ring`; mínimo 160 px como `--dock-min` **e** `DOCK_MIN_PX`, com teste de igualdade no padrão de `--row-height` × `ROW_HEIGHT` |
| Grid × doca | `--row-height` = `ROW_HEIGHT`; `--grid-head` (22) lido uma vez |
| Barra de status | `--status-height` (22), `--status-error-bg`/`--status-error-ink`, `--text-sm` |
| Faixa e fichas | `--strip-height` (30), `--filtered-bg`/`--filtered-ink`, `--accent`; fichas com 24 px (alvo ≥ 24×24) |
| Gavetas, diálogos, paleta | `--z-drawer`, `--z-dialog`, `--shadow-overlay` |

Nenhum módulo novo declara cor ou tamanho literal: tudo por token.

## 9. Riscos e achados

| Risco ou achado | Mitigação |
|---|---|
| Dois despachantes da mesma tecla durante a transição | Seção 1.6: `shortcut` declarativo até a UI-28, que troca todos os ouvintes no mesmo PR |
| `dock-legacy.js` divergir de `dock.js` | Mesma API e mesmos testes de fluxo por painel (abrem pelo registro) rodam nas duas |
| Carregamento sob demanda quebra a tolerância a módulo opcional | `preload`/`invoke` nunca rejeitam; falha = item desabilitado com motivo e mensagem (1.5); teste de fluxo por painel |
| `AppContext` virar saco de tudo | Dez membros fixos (0.1); o resto por `deps`, só em `app-actions.js`; membro novo = registro na ARQUITETURA |
| `app-actions.js` como novo ponto disputado | Dono único (sessão principal), um bloco por painel; agentes entregam o bloco no relatório |
| Grid com altura 0 (doca maximizada) recalcula o topo | Regra explícita em 2.5 e teste de `topAfterResize` |
| Remoção de ficha recarregar duas vezes (seleção + filtro) | `clearSelection({ quiet })` e um `applyView()` só (UI-18) |
| Foco perdido ao fechar aba ou painel | `close()` devolve ao opener conectado, senão ao grid (`onFocusOut`) |
| `localStorage` indisponível ou com valor ruim | `parseStored` devolve `null`; tudo com `try/catch`; vale a sessão |
| Ficha agregada sem `×` incomodar quem usa a árvore | Abre a árvore em um clique; revisar se o UAT pedir remoção direta |

## 10. O que NÃO fazer

- Módulo inserir botão, host ou texto em `#toolbar`, `#grid-panel`, rodapé, `document.body` ou fora do `host` recebido.
- Módulo registrar a própria ação ou o próprio painel (quebra a UI-14), ou registrar `keydown` em `document`.
- Módulo escrever `host.hidden` num host da doca; fechar é `onRequestClose()`.
- Usar `CustomEvent` em `document` como barramento entre módulos. Os únicos eventos de integração são `dock.on(...)` e `actions.onChange(...)`.
- Persistir abas abertas, estado maximizado ou a última ação; só `dmslog.dockHeight` (e `dmslog.singleKeys` na UI-28).
- Pôr texto de log ou do usuário em chave de `strings.js` ou em `innerHTML`; rótulos dinâmicos (`children`, fichas) vão por `textContent`.
- Medir texto ou largura durante a rolagem do grid (fichas e cortes só na mudança de filtro ou de largura).
- Mais de uma região viva além da mensagem da barra de status e do anunciador da contagem.
- Misturar `import("./x.js")` com `import("./x.js?v=…")`.
- `hidden` em item de menu indisponível (fica desabilitado, com motivo).
- A doca chamar `grid.render()` ou mexer em `grid.window`; o grid reage ao próprio tamanho.
- Criar `<link rel="stylesheet">` por JS (proibido desde a UI-06).

## 11. Texto para `docs/ARQUITETURA.md` (a sessão principal aplica)

### Seção 11, acrescentar ao fim da seção

```markdown
### 11.1 Estrutura da tela e contratos de interface (UI-11, issue #151)

Desenho completo em `docs/notas/ui-contratos.md`. Resumo do que é contrato:

- **Registro de ações** (`web/js/actions.js`, puro): `{ id, labelKey, group, shortcut, enabled(ctx), run(ctx), load?() }`, mais `scope`, `disabledKey`, `pressed` e `children` opcionais. Alimenta a barra (menus Case, Analyze, Export, View e as alternâncias), o diálogo `?`, o menu de contexto da linha e a paleta (`Ctrl+K`). Atalho em KeySpec canônica; atalhos de uma letra obedecem a `dmslog.singleKeys` e nunca disparam em campo de texto; atalho repetido no mesmo escopo é erro de registro. `load()` é o gancho do carregamento sob demanda: chamado no hover/foco do item e antes de `run`; falha deixa o item desabilitado com motivo.
- **Doca inferior** (`web/js/dock.js`; regras em `dock-model.js`, puro): inicialização em duas etapas, idêntica na doca provisória da UI-12 (`dock-legacy.js`) e na definitiva da UI-15: `createDock({ root, before?, storage, onFocusOut })` e depois `dock.bind(ctx)`. API: `register({ id, titleKey, mount(host, ctx), listens, closeOnCase, needsCase })`, `open(id, args)`, `close`, `closeAll`, `maximize`, `isOpen`, `active`, `notify(case|view|indexed|derived)`, eventos `open`/`close`/`activate`/`resize`. `notify("case")` primeiro fecha todo painel aberto com `closeOnCase` (independente de `listens`) e depois avisa os que têm o motivo em `listens`. `notify` substitui só as chamadas a painéis da doca; o que não é painel (aviso de relógio, Copy as, Follow tail, alertas, árvore de filtros, trilha, timeline) continua chamado por `main.js` nos mesmos ganchos (inventário em `docs/notas/ui-contratos.md`, 2.4.1). Doca ≥ 160 px; grid ≥ 8 linhas + cabeçalho; janela < 640 px abre maximizada; altura inicial `clamp(160 px, conteúdo, 40%)`; altura lembrada em `localStorage` `dmslog.dockHeight` (fração). O grid mantém a linha selecionada visível em qualquer redimensionamento pelo próprio `ResizeObserver` (`Grid.ensureSelectedVisible()`, `topAfterResize` em `grid-model.js`), nunca durante a rolagem e nunca com altura 0.
- **Barra de status** (`web/js/status-bar.js`): `count`, `position`, `progress`, `message(text, { kind, timeoutMs })`, `connection(state)`, `slot(id)`; substitui `#message`, `#grid-status`, o banner de relógio, o banner de alertas e a ajuda fixa de atalhos. Uma região viva de mensagem e um anunciador de contagem; erro global troca o conteúdo da própria barra (`role="alert"`).
- **Fichas de filtro** (`web/js/filter-chips-model.js`, puro): `chipsFromState(live, selection, lookups, S)` e `removeChip(live, selection, chipId)` sobre o estado vivo da visão (o mesmo de `views-stack-model.js`) e a seleção da lateral, não sobre o JSON composto. Folhas simples do topo do `userFilter` viram fichas removíveis; o resto vira uma ficha "Filter tree (n conditions)" que abre a árvore.
- **Regiões e foco**: F6/Shift+F6 na ordem do DOM: barra, lateral, faixa de filtro, grid, doca, detalhe (só as disponíveis); última posição de foco lembrada por região; uma parada de Tab por componente composto (menus, abas, fichas, grid) com roving tabindex; skip link para o grid.
- **Quem registra**: ações e painéis são registrados de forma síncrona por `web/js/app-actions.js`, chamado por `main.js`; módulos não se registram nem inserem nada fora do `host` recebido. `app-actions.js` é ponto de integração com dono único (sessão principal), como `main.js`, `strings.js`, `api.js` e `index.html`. `AppContext` (`caseId`, `filter`, `userFilter`, `selectedRow`, `entry`, `setUserFilter`, `reveal`, `actions`, `dock`, `status`) só muda com registro nesta seção e na 15.
```

### Seção 15, nova linha

```markdown
| 2026-10-14 | UI-11 (issue #151): contratos de interface do viewer (registro de ações, doca inferior, barra de status, fichas de filtro, regiões F6 e ordem de tab); `web/js/app-actions.js` como ponto de integração com dono único; `AppContext` fixo; doca provisória sobre os hosts atuais na UI-12 e definitiva na UI-15 (seção 11.1, `docs/notas/ui-contratos.md`) | Opus |
```

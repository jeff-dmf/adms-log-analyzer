# R2b e R2c: instâncias do FunctionHost e cadeia do job (desenho)

Desenho (Opus) de duas mudanças da R2 da frente RT (`docs/ROADMAP.md`, achados 3 e 5 de `docs/notas/rt-r1-achados.md`), para implementação por agentes Sonnet. Medições feitas no caso local do ERS RT Primary, aberto só para leitura (`mode=ro&immutable=1`). Nenhum dado real aqui: exemplos sintéticos (`job_id` `100001`, instância `7`).

## 1. R2b: agrupar as instâncias do FunctionHost

### 1.1 Como está hoje

- `families.toml`, regra `DMSFH`: `(?P<process>DMS_FunctionHost_\d+)`, sem grupo `pid` (o número é da instância do pool, não PID). Resultado no caso: 28 streams `DMSFH`, `process` = `DMS_FunctionHost_1..28`, `stream.pid` NULL, 1 fonte por stream, todas na mesma pasta de serviço.
- Identidade do stream: `UNIQUE(collection_group, family, process, pid, sensitive)` no esquema, `SourceInfo.stream_key` em `ingest/discover.py`, a cadeia de rotação em `partial-open-model.js`. A derivação é por stream: `FunctionHostJob` pareia por `tid` (TIDs se repetem entre instâncias), o reinício do FH é detectado por troca de PID dentro do stream, e `derive/links.py:1032` agrupa por `(process, pid)`.
- Onde as 28 aparecem: lista de fontes (plana) e árvore de arquivos (`sources.js`, `source-tree-model.js`, 28 folhas irmãs, ordem alfabética: `_1, _10, _11, ..., _2`), coluna "Family / process" do painel de operações e do painel de passos que faltaram (`events-model.js`, `missing-model.js`: `DMSFH / DMS_FunctionHost_7`), painel de comparação no modo processo (`compare-views-model.js`, 28 opções), dashboard. O grid mostra o nome do arquivo (não muda). Não há um filtro "processo" no filtro em árvore: filtrar processo é marcar fontes (folha `source`) ou usar `stream`/`family`.

### 1.2 Decisão: agrupamento só na leitura, sem mudar o esquema

O nome guardado (`stream.process = DMS_FunctionHost_7`) continua como está. O pool é uma **anotação de leitura**: uma tabela nova em `families.toml` diz quais nomes de processo são instâncias de um pool, e o servidor acrescenta `pool` e `instance` a todo objeto JSON que já leva `process`.

Alternativa descartada: gravar `process = DMS_FunctionHost` e uma coluna `instance`. Exigiria refazer a tabela `stream` (o `UNIQUE` não se altera em SQLite), mudar a chave do stream em `discover.py`, no writer e na cadeia de rotação, e migrar os casos existentes. Com `pid` NULL, as 28 instâncias colapsariam num único stream se qualquer um desses pontos esquecesse a instância, e aí o pareamento por `tid` do `FunctionHostJob` e a detecção de reinício por PID dariam resultado errado sem erro visível. Usar `pid` para guardar a instância também foi descartado: `pid` tem semântica própria (envelope, reinício, `pid_field` do handoff).

Consequências: **nenhuma migração, nenhuma reindexação**. Casos antigos ganham o agrupamento ao abrir com a versão nova. Mudar a tabela de pools não muda nenhum dado derivado.

### 1.3 `families.toml`

```toml
# Pools of identical processes: one stream per instance (derivation stays per instance); the viewer groups them.
[[pool]]
name = "DMS_FunctionHost"            # displayed name of the group (literal, like a process name)
family = "DMSFH"
process = 'DMS_FunctionHost_(?P<instance>[1-9]\d{0,3})'   # fullmatch over stream.process
```

Validação na carga (`FamiliesConfigError`, como as demais): `name` `[A-Za-z][A-Za-z0-9_]{0,63}`; `family` existe em alguma `[[rule]]`; `process` compila, tem exatamente o grupo `instance` e nenhum outro, sem quantificador aninhado; dois pools com o mesmo `(family, name)` são erro. `RuleSet.from_dict` hoje ignora tabelas desconhecidas; ele passa a ler `pool` (lista, padrão vazio) e expõe `RuleSet.pools` e `pool_of(family, process) -> (name, instance) | None` (fullmatch, `instance` inteiro). Cache por `(family, process)`.

### 1.4 API (contrato)

Regra única: **todo objeto que leva `process` passa a levar `pool` (texto ou `null`) e `instance` (inteiro ou `null`)**. Campos novos, nada removido, então clientes antigos não quebram.

| Rota | Onde | Dono |
|---|---|---|
| `GET /api/sources` | cada fonte; também `stream` (id do stream), que hoje falta e a UI precisa para mapear eventos | sessão principal (`api.py`) |
| `GET /api/events`, `/api/events/<id>`, `/api/events/missing` | `store/events._event_json` | agente |
| `GET /api/keys/<type>/<value>` | `store/keys._record` | agente |
| `GET /api/entries/<id>/links` | `store/keys.links_of` | agente |

Exemplo sintético: `{"family": "DMSFH", "process": "DMS_FunctionHost_7", "pool": "DMS_FunctionHost", "instance": 7, ...}`; fora de pool, `"pool": null, "instance": null`.

O filtro não ganha folha nova: "o FunctionHost inteiro" é `{"family": ["DMSFH"]}` (a família só tem o pool; é o que os atalhos da R2a usam), e "a instância 7" é `{"source": [...]}`/`{"stream": [...]}`, que é o que marcar a fonte já produz. A distribuição por instância já existe no painel de valores distintos pelo campo `source`.

### 1.5 UI

Um módulo puro novo, `js/process-label.js`:

- `processLabel({family, process, pool, instance})`: `"DMS_FunctionHost #7"` quando há pool (modelo `S.poolInstance = "{pool} #{instance}"`), senão `process`.
- `familyProcessText(obj)`: substitui `familyText` de `events-model.js` e `missing-model.js` (`DMSFH / DMS_FunctionHost #7`).
- `compareInstance(a, b)`: ordem numérica da instância.

Telas:

1. **Árvore de arquivos** (`source-tree-model.js`): dentro da pasta onde estão, as folhas com o mesmo `pool` viram filhas de uma **pasta virtual** com o nome do pool (`S.poolFolder = "{pool} ({count} instances)"`, plural pelo `pluralKey`), ordenadas pela instância. É uma pasta como as outras: marca as 28 de uma vez, estado misto, contadores de arquivos e registros, abre e fecha pelo teclado. Chave estável e que não colide com pasta real: `<prefixo da pasta>\u0000pool:<name>/` (NUL não ocorre em nome de membro). A folha continua com o nome do arquivo; `aria-label` da folha: `"DMS_FunctionHost #7, DMS_FunctionHost_7.log"`.
2. **Lista plana** (`sources.js`): sem mudança de estrutura nesta entrega (D2). Só a ordem das fontes de um pool passa a ser numérica.
3. **Operações, passos que faltaram, dashboard, comparação (modo processo)**: rótulo por `processLabel`; no modo processo as opções de um pool ficam juntas e em ordem numérica.
4. Detalhe do evento (`buildDetailView`): o campo "Process" mostra o nome guardado (`DMS_FunctionHost_7`), sem mudança: é o dado cru.

### 1.6 Custos e riscos

- Custo de execução desprezível (60 streams no caso; `pool_of` em cache).
- Risco: um nome de processo de outra família casar com o padrão. Mitigado pelo `family` obrigatório e pelo fullmatch.
- Risco: apelidos locais (`aliases.toml`) renomearem o processo e o padrão deixar de casar. O FH não tem apelido hoje; o teste de pool roda também com o processo apelidado e o resultado aceito é "sem pool" (nunca erro).
- Risco: chave da pasta virtual guardada no `sessionStorage` das pastas abertas; um caso sem pool simplesmente não tem essa chave.

### 1.7 Testes

- `tests/unit/test_discover.py`: `[[pool]]` válido; cada erro de validação de 1.3; `pool_of` com instância 1, 28, `0` (não casa), sufixo extra (não casa), outra família (não casa); `families.toml` empacotado carrega com o pool.
- `tests/unit/test_store_events.py`, `test_store_keys.py`: `pool`/`instance` presentes em evento, registro de chave e ligação; `null` fora do pool.
- `tests/unit/test_api_*` da rota de fontes (sessão principal): `pool`, `instance` e `stream` em `/api/sources`.
- Web: `process-label.test.mjs` (novo); `source-tree-model.test.mjs` (pasta virtual, ordem numérica, estado misto, chave, várias coletas, pool com uma só instância também agrupa); `events-model.test.mjs`, `missing-model.test.mjs`, `dashboard-model.test.mjs`, `compare-views-model.test.mjs` (rótulo).
- Nada muda em `schema.sql`, nas migrações nem nos testes de derivação.

### 1.8 ARQUITETURA

Seção 9.1a: tabela `[[pool]]` de `families.toml` (anotação de leitura; a identidade do stream não muda). Seção 10: campos `pool`, `instance` e `stream` nas rotas da tabela 1.4. Seção 11: árvore com pasta virtual por pool. Seção 15: linha. `docs/FAMILIA-NOVA.md`: como declarar um pool.

## 2. R2c: cadeia do job por `job_id`

### 2.1 O que já existe e o que falta

- Painel "Records with the same key" (`sameKey`, `key-panel.js`): dado `(type, value)`, lista em ordem `(ts, id)` todo registro com a chave, em qualquer fonte, com paginação por keyset, "Show in grid" e seleção no grid. Abre pela ação `record.sameKey` (menu de contexto e aba de ligações).
- Aba de ligações do registro (`links.js`): mostra `handoff`/`response` de **um** registro.
- Painel de operações: lista e detalhe de eventos; `q` filtra `event.key` por prefixo, mas sem índice (varre `event`).
- Falta: ver, numa tela, os passos do job (as três operações com status e duração) e as passagens entre processos, em ordem.

Decisão: **estender o painel `sameKey`** com uma seção "Operations and handoffs" acima da tabela de registros, preenchida por uma rota nova que devolve as operações e as passagens da chave. Sem tela nova, sem aba nova, sem mudança no esquema. A seção vale para qualquer tipo de chave (D4): aparece quando a chave tem operação ou passagem, e some quando não tem (ex.: `errcode`).

### 2.2 Medição (caso RT Primary)

- `job_id`: até 30 registros por valor (média 16); 73.846 valores.
- Operações por valor: 1 (só `FunctionHostJob`), 2 (`LauncherJob` + `FunctionHostJob`) ou 3 (as três). Todo job com 3 operações tem 2 ligações `handoff` entre registros da chave; com 2 operações, 1.
- Caminho indexado `entry_key` (PK) → `entry.event_id` → `event` (PK): encontra **100%** das 134.492 operações cujo `event.key` é o valor (0 faltando), e tudo o que ele encontra é operação com `event.key` igual ao valor. Dispensa índice novo em `event.key`.
- Protótipo da consulta inteira (registros + operações + ligações), 1.000 `job_id` sorteados: p50 0,07 ms, p95 0,3 ms, máximo 0,55 ms (cache quente).
- `FunctionHostJob` tem até 454 registros (média 108), a maioria sem a chave (pareado por `tid`): a cadeia mostra a operação com a contagem, e os registros dela abrem no grid.

### 2.3 Rota nova

`GET /api/keys/<type>/<value>/chain?case=&collection=`

Mesma validação de `type`, `value` (percent-decode, limite) e `collection` da rota de registros; mesmos erros (`400 invalid_field`, caso desconhecido, tempo de consulta). Não conflita com `/api/keys/<type>/<value>` (o padrão desta termina em `\Z`).

Leitura (`store/chain.py`, novo, só leitura, sob `read_guard`):

1. `ids` = `entry_id` de `entry_key` com `key_type`, `value`, `origin = 0` (e o grupo, se `collection`), `LIMIT CHAIN_SCAN + 1` (`CHAIN_SCAN = 5000`).
2. Operações: `DISTINCT entry.event_id` desses `ids` → `event` com `kind = 'operation'`, ordem `(ts_start, id)`, no máximo `CHAIN_OPS = 200`.
3. Passagens: `link` com `kind IN CHAIN_LINK_KINDS` (`("handoff",)`) e as duas pontas em `ids` (prefixo da PK por `a_entry_id`, `b_entry_id` conferido em memória), no máximo `CHAIN_LINKS = 500`.

Resposta:

```json
{
  "key": {"type": "job_id", "value": "100001"},
  "operations": [<event> + {"collection": 1}],
  "handoffs": [{
    "kind": "handoff", "confidence": 3, "dt_ms": 4,
    "from": {"entry_id": 11, "ts": 0, "stream_id": 3, "event_id": 51, "family": "DMSJOB",
             "process": "DMS_JobLauncher", "pool": null, "instance": null, "tpl_code": "DJB-050", "collection": 1},
    "to":   {"entry_id": 92, "ts": 4, "stream_id": 9, "event_id": null, "family": "DMSFH",
             "process": "DMS_FunctionHost_7", "pool": "DMS_FunctionHost", "instance": 7, "tpl_code": "DFH-001", "collection": 1}
  }],
  "scanned": 16,
  "truncated": false
}
```

- `<event>` é o mesmo objeto de `/api/events` (com `pool`/`instance` da R2b).
- `event_id` de uma ponta pode ser `null` (o `DFH-001` vem antes do início do `FunctionHostJob`).
- `dt_ms = to.ts - from.ts` (ou `null` se faltar um ts).
- `truncated = true` quando qualquer um dos três limites foi atingido; `scanned` é quantos registros da chave foram lidos.
- Sem `collection`, junta todos os grupos; cada item leva `collection` para a UI separar (U21: cada coleta tem o seu relógio).

### 2.4 UI: seção "Operations and handoffs" do painel `sameKey`

Ao abrir uma chave, o painel pede em paralelo os registros (como hoje) e a cadeia. Uma falha de um não bloqueia o outro.

Linhas (uma tabela, ordem por tempo; operação por `ts_start`, passagem por `from.ts`; empate: passagem antes da operação que ela inicia):

| Coluna | Operação | Passagem |
|---|---|---|
| Time | `ts_start` | `from.ts` |
| Process | `processLabel` (com `C<n>` quando há mais de uma coleta) | `processLabel(from) → processLabel(to)` |
| Step | nome da operação (`SchedulerJob`, ...) | `S.chainHandoff` ("Handoff") |
| Status | `statusLabel` + tom (texto, nunca só cor) | confiança (`S.lnkConfidenceNames`) |
| Duration | `duration_ms` (`open` sem duração) | `dt_ms` |
| Records | `n_entries` | vazio |
| (ação) | botão "Show in grid" (`deps.showEventInGrid(gridTarget(event))`, com o helper `gridTarget` de `events-model.js`, que monta `{from, to, entryId}`; o mesmo do painel de operações) e botão "Open operation" (`ctx.dock.open("operations", {eventId})`) | botão "Go to record" (seleciona o `to.entry_id` no grid, `onSelect`) |

Resumo acima da tabela (`S.chainSummary`, com plurais): `"3 operations, 2 handoffs, 310 ms from first to last"` (a duração do primeiro ao último item só aparece quando todos os itens são da mesma `collection`; com várias coletas os relógios podem diferir, então o resumo omite a duração agregada e cada operação mostra a sua própria duração e o rótulo `C<n>`), e, se houver operação com `fail`/`timeout`/`business_error`, `"Failed at FunctionHostJob (DMS_FunctionHost #7)"` (primeira operação com falha em ordem de tempo).

Estados:

- Carregando: linha de status `S.chainLoading`; a tabela de registros segue independente.
- Sem operação nem passagem: a seção inteira fica oculta (`hidden`), sem texto.
- Erro: `S.chainError + ": " + mensagem`, só na seção, com `role="status"` e o padrão de `setStatusLine` (erro marcado).
- Grande: `truncated` → aviso `S.chainTruncated` ("Only the first {n} records with this key were read; the list may be incomplete."); a tabela mostra no máximo 200 operações + 500 passagens, renderizadas de uma vez (sem paginação: o teto é baixo).
- Troca de caso, nova chave, painel fechado: nova geração (mesma técnica de `createKeyFlow`), respostas antigas descartadas, pedido abortado.

Acessibilidade e segurança:

- `<section>` com `<h3>` (`S.chainTitle`) e `aria-labelledby`; tabela com `<caption class="visually-hidden">`, `<th scope="col">`; ações são `<button type="button">` com `aria-label` próprio (`"Show operation {name} in grid"`); um único ouvinte delegado no `tbody`.
- Ordem de tab: resumo → botões da cadeia → tabela de registros (já existente) → "Load more". Nenhum `keydown` em `document`; o foco ao abrir continua no título do painel (`focusTarget`).
- Todo texto vindo do log (nomes de processo, de operação, código) só por `textContent`. Nenhum `innerHTML`.
- Strings em inglês, todas em `strings.js` (2.6).

Entradas no painel:

- Já existentes: `record.sameKey` (menu de contexto e aba de ligações).
- Nova: no detalhe do painel de operações, botão "Same key" quando o evento tem `key`. O tipo da chave não está no evento: o painel lê `GET /api/entries/<start_entry_id>` (já existe; traz `keys`) e usa o primeiro tipo, em ordem alfabética, cujo valor é `event.key`; sem nenhum, o botão fica desabilitado com `title` explicando (`S.evSameKeyNone`).

### 2.5 Limites para jobs com milhares de registros

- A cadeia nunca lista registros: lista operações (≤ 200) e passagens (≤ 500); o custo é limitado por `CHAIN_SCAN = 5000` registros da chave lidos pelo índice.
- A tabela de registros continua paginada por keyset (200 por página, máx. 1000), como hoje.
- Os registros de uma operação grande abrem no grid ("Show in grid"), não no painel.
- `truncated` é calculado pelos três limites e mostrado; nada é estimado.

### 2.6 Strings novas (`strings.js`, sessão principal)

`poolInstance`, `poolFolder_one`/`poolFolder_other`, `chainTitle` ("Operations and handoffs"), `chainCaption`, `chainColTime`, `chainColProcess`, `chainColStep`, `chainColStatus`, `chainColDuration`, `chainColRecords`, `chainColActions`, `chainHandoff`, `chainSummary*` (plurais de operações e passagens), `chainFailedAt` ("Failed at {name} ({process})"), `chainLoading`, `chainError`, `chainTruncated`, `chainShowInGrid`, `chainShowInGridLabel`, `chainOpenOperation`, `chainOpenOperationLabel`, `chainGoToRecord`, `chainGoToRecordLabel`, `evSameKey`, `evSameKeyNone`. Os agentes usam `S.<chave>` e entregam a lista com os textos; a sessão principal grava.

### 2.7 Testes

- `tests/unit/test_store_chain.py` (novo, dados sintéticos com `handoff_helpers.py`): job de 3 passos (3 operações, 2 passagens, ordem e `dt_ms`); job só com FH; chave sem operação (listas vazias); `collection` filtra; mesmo valor em dois grupos (itens com `collection` distintos, nenhuma passagem entre grupos); `response` dentro da mesma operação não aparece; `truncated` com 5001 registros da chave, com 201 operações e com 501 passagens; ponta com `event_id` nulo; valor com `/` e espaço.
- `tests/unit/test_api_chain.py` (novo): validação de `type`, `value`, `collection`; caso desconhecido; rota de registros continua respondendo para um valor literal `chain` (`/api/keys/job_id/chain`).
- Web: `key-chain-model.test.mjs` (novo: linhas, ordem e empate, resumo e plurais, falha, várias coletas, truncado, geração descartando resposta velha); `key-panel-chain-flow.test.mjs` (novo: seção oculta sem operação, erro da cadeia não afeta os registros, botões chamam `showEventInGrid`/`dock.open`/`onSelect`, só `textContent`, ordem de tab); `events-flow.test.mjs` (botão "Same key", com e sem chave encontrada).

## 3. Arquivos a tocar

| Arquivo | R2b | R2c | Dono |
|---|---|---|---|
| `src/dmslog/families.toml` | `[[pool]]` | | T1 |
| `src/dmslog/ingest/discover.py` | `RuleSet.pools`, `pool_of`, validação | | T1 |
| `src/dmslog/store/events.py` | `pool`/`instance` em `_event_json` | | T1 |
| `src/dmslog/store/keys.py` | `pool`/`instance` em `_record` e `links_of` | | T1 |
| `docs/FAMILIA-NOVA.md` | como declarar um pool | | T1 |
| `src/dmslog/store/chain.py` (novo) | | leitura da cadeia | T2 |
| `src/dmslog/web/js/process-label.js` (novo) | rótulos e ordem | | T3 |
| `src/dmslog/web/js/source-tree-model.js`, `source-tree.js` | pasta virtual | | T3 |
| `src/dmslog/web/js/events-model.js`, `missing-model.js`, `dashboard-model.js`, `compare-views-model.js` | rótulo | | T3 |
| `src/dmslog/web/js/key-chain-model.js` (novo) | | modelo e fluxo | T4 |
| `src/dmslog/web/js/key-panel.js`, `css/key-panel.css` | | seção | T4 |
| `src/dmslog/web/js/events-panel.js` | | botão "Same key" | T4 |
| `src/dmslog/server/api.py` | `pool`, `instance`, `stream` em `/api/sources` | rota `chain` | sessão principal |
| `src/dmslog/web/js/api.js` | | `keyChain(caseId, type, value, params, signal)` | sessão principal |
| `src/dmslog/web/js/strings.js` | 2.6 | 2.6 | sessão principal |
| `src/dmslog/web/js/app-actions.js` | | deps do `sameKey` (`showEventInGrid`, `openOperation`) e do painel de operações (`onSameKey`) | sessão principal |
| `src/dmslog/web/css/bundle.css` | | `python tools/build_web_css.py` | sessão principal |
| `tests/unit/test_web_assets.py` | `process-label.js` em `EXPECTED_MODULES` | `key-chain-model.js` | sessão principal |
| `docs/ARQUITETURA.md`, `docs/ROADMAP.md` | 1.8 | seção 10 (rota), 11 (painel), 15 | sessão principal |

`messages.py`, `index.html` e `main.js`: sem mudança prevista (a rota reusa as mensagens de validação da rota de registros; o painel monta o próprio DOM no host da doca). Se um agente precisar de algo neles, pede à sessão principal.

## 4. Tarefas (Sonnet) e ordem

Lote 1, em paralelo (arquivos disjuntos):

- **T1 (R2b backend):** 1.3 e 1.4 nos arquivos de T1; testes de 1.7 (`test_discover.py`, `test_store_events.py`, `test_store_keys.py`). Entrega também o trecho de `list_sources` para a sessão principal.
- **T2 (R2c backend):** `store/chain.py` com o contrato de 2.3, constantes `CHAIN_SCAN`, `CHAIN_OPS`, `CHAIN_LINKS`, `CHAIN_LINK_KINDS`; `tests/unit/test_store_chain.py`; `tests/unit/test_api_chain.py` escrito contra o contrato (passa depois que a sessão principal registra a rota). Entrega o texto do handler para `api.py`. Usa `events._event_json` como está; `pool`/`instance` aparecem quando T1 estiver na `main` (o teste de T2 não depende deles).
- **T3 (R2b frontend):** 1.5 nos arquivos de T3; testes web de 1.7. Lista as chaves de `strings.js` que usa.

Lote 2, depois do merge do lote 1 (usa `process-label.js` e o contrato da rota):

- **T4 (R2c frontend):** 2.4 nos arquivos de T4; testes web de 2.7. Lista as chaves de `strings.js` e as deps que o `sameKey` e o painel de operações passam a receber.

Integração (sessão principal, um PR por lote): `api.py`, `api.js`, `strings.js`, `app-actions.js`, `bundle.css`, `test_web_assets.py`, `ARQUITETURA`, `ROADMAP`; `ruff check` na raiz, suíte completa, CI Linux e Windows; revisão cruzada T1 (Codex, `gpt-6.1-sol`).

## 5. Critérios de aceite (medidos no caso RT local, relatório só com números)

R2b:

- `/api/sources`: exatamente 28 fontes com `pool = "DMS_FunctionHost"` e `instance` cobrindo 1..28 sem repetição; nenhuma outra fonte com `pool`.
- Abrir o caso com a versão nova não altera `stream` nem `source` (hash das duas tabelas igual antes e depois) e não reindexa nada (nenhuma fonte reprocessada).
- Árvore: uma pasta virtual com 28 folhas em ordem 1..28; marcar a pasta seleciona as 28 fontes e o grid mostra a mesma contagem que o filtro `{"family": ["DMSFH"]}`.
- Painel de operações: coluna de processo de um `FunctionHostJob` = `DMSFH / DMS_FunctionHost #<n>`.
- ERS INT de exemplo: nenhum pool; telas iguais às de antes (testes existentes intactos).

R2c:

- Para todo `job_id` do caso: o conjunto de operações da rota `chain` = eventos `kind = 'operation'` com `event.key` igual ao valor (100%), e as passagens = ligações `handoff` entre registros da chave (100%).
- Jobs com 3 operações mostram 2 passagens; com 2, 1.
- Tempo da rota (servidor, 1.000 `job_id` sorteados, cache quente): p95 ≤ 5 ms; nenhum pedido acima de 50 ms.
- Teclado: abrir a chave por `record.sameKey`, chegar ao botão "Open operation" e acioná-lo sem mouse; teste de fluxo cobre a ordem de tab.
- `test_web_assets.py` verde (sem `innerHTML`, texto só de `strings.js`); suíte completa e `ruff check` na raiz verdes; CI Linux e Windows verdes.

## 6. Riscos

- **`job_id` reaproveitado** num ERS longo (contador que volta): a cadeia mistura execuções diferentes do mesmo valor, em ordem de tempo. No caso medido não há repetição; a seção mostra as datas, então a mistura fica visível. Ver D5.
- **Ordem entre coletas**: com dois servidores no caso, a ordem entre grupos é aproximada (relógios distintos); a UI separa por `C<n>`.
- **Operação sem registro com a chave**: o caminho `entry_key → event_id` não a encontraria. Medido: 0 casos nas três operações RT; o teste de aceite 100% acima vigia isso para catálogos futuros.
- **Truncamento** por `entry_id` (ordem de arquivo, não de tempo): com mais de 5.000 registros da chave, as operações mais tardias podem faltar; o aviso diz isso.
- **Pasta virtual** muda a contagem de linhas da árvore e a navegação por teclado; coberta por `source-tree-model.test.mjs` e `source-tree-flow.test.mjs`.

## 7. Decisões (padrões adotados em 2026-10-24)

O Jefferson pediu para não parar a frente RT enquanto está fora: a sessão principal adotou os padrões abaixo (D2, D5 e D6 também). Todos são revisáveis sem refazer o desenho.

Marcadas com **[padrão]** as que este desenho já resolve; mudam só se ele pedir.

- D1 **[padrão]** Nome exibido do pool: o nome literal declarado no TOML, `DMS_FunctionHost`, com a instância como `#7`.
- D2 Lista plana de fontes: agrupar também (linha de grupo com estado misto e expansão) ou manter só a árvore agrupada. **[padrão]** só a árvore, mais a ordem numérica na lista plana; agrupar a lista plana custa mexer em `sources.js` (1.600 linhas, tabulação itinerante) e fica para depois se fizer falta na verificação visual.
- D3 **[padrão]** Sem folha de filtro `instance` portátil para atalhos e visões salvas: o FunctionHost inteiro é a família `DMSFH`; uma instância específica não tem uso em atalho.
- D4 **[padrão]** A seção da cadeia vale para qualquer tipo de chave com operação ou passagem (não só `job_id`); nas chaves do ERS INT ela aparece quando há operação com a chave.
- D5 Separar execuções de um `job_id` repetido (por início do `SchedulerJob` ou por intervalo). **[padrão]** não separar agora; revisitar se um ERS longo mostrar repetição.
- D6 Detalhe por instância no dashboard (`FunctionHostJob` por instância, para ver qual instância falha mais). **[padrão]** fora da R2; candidato depois da verificação visual.

# Arquitetura do dmslog

Analyzer + viewer dos logs dos adapters (online interfaces) do ADMS GridOps 3.9. Entrada: um ERS (zip, normalmente o par Enterprise + Sensitive) ou a pasta `Log` de uma instalação. Saída: índice local consultável e um viewer no navegador, apoiado na base de conhecimento do vault `adms-v3.9`.

Fatos de formato vêm de `docs/formato/*.md`, a referência de UX vem de `docs/REFERENCIA-UX.md` e as restrições de portabilidade estão no `CLAUDE.md`. Mudanças de estrutura, interfaces ou esquema passam pelo Opus e ficam registradas aqui (seção 15).

## 1. Visão geral

```
 ERS.zip / pasta Log / arquivo                        vault (somente leitura, só em dev)
        │                                                     │ tools/kb_build.py
        ▼                                                     ▼
 ┌──────────────┐  SourceInfo   ┌──────────────┐      knowledge/pack/*.json  (versionado no repo)
 │ ingest       │──────────────▶│ parse        │                 │
 │ descoberta,  │  bytes em     │ 1 envelope   │  Record          │
 │ fingerprint, │  streaming    │ 2 templates  │──────┐           │
 │ leitura zip  │               │ 3 severidade │      │           │
 └──────────────┘               └──────────────┘      ▼           ▼
        │ XMLs do ERS (Registry, WebServiceConfiguration, ErrorConfiguration)
        └──────────────▶ knowledge.installation ──▶ ┌──────────────────────────┐
                                                    │ store (SQLite, 1 por caso)│
 derive (por stream, com checkpoint) ◀─────────────▶│ entry, entry_body, fts5,  │
   operações, startup, estatística, janelas         │ event, link, stat_sample, │
   de nível, gaps, retries, correlação              │ bucket, config, bookmark  │
                                                    └────────────┬─────────────┘
                                                                 │ consultas parametrizadas
                                               server (http.server em 127.0.0.1, JSON + SSE)
                                                                 │
                                               web (HTML/JS estático vendorizado, sem build)
                                               grid virtual, filtros, timeline, painéis
```

Três modos de uso, mesmo núcleo:
- `dmslog serve [ERS.zip|pasta|caso]`: sobe o servidor local, abre o navegador e indexa em segundo plano (a UI mostra o progresso e libera cada arquivo assim que termina).
- `dmslog ingest ...` / `dmslog info` / `dmslog export`: linha de comando, sem UI (scripts e CI).
- `dmslog watch <pasta>`: modo instalação ao vivo, com reindexação incremental por polling.

## 2. Decisões e alternativas descartadas

| # | Decisão | Alternativas descartadas e por quê |
|---|---|---|
| D1 | **Viewer web local**: `http.server` (stdlib) em 127.0.0.1 + página estática (HTML/JS/CSS vendorizados, sem framework e sem build). Gráficos com uPlot (MIT, 1 arquivo, vendorizado). | **TUI**: `curses` não existe no CPython de Windows; `textual`/`rich` funcionam, mas grid de 900 mil linhas com colunas dinâmicas, timeline e drill-down em terminal ficam bem abaixo da referência (LogViewPlus). **Desktop tkinter**: o Python embeddable de Windows **não traz tkinter/Tcl** (verificado no `python-3.13.7-embed-amd64.zip`); teria que empacotar Tcl/Tk à mão, e o widget de grid é fraco. **Qt/PySide, Electron, Tauri**: dependências nativas grandes, contra a regra de stdlib/wheels puros. **SPA com framework (React/Vue) e bundler**: exige build e toolchain Node; o ganho não paga para uma UI de uma pessoa. O navegador (Edge/Chrome/Firefox) existe em qualquer PC Windows corporativo e no Omarchy. |
| D2 | **Python ≥ 3.11, só stdlib em runtime** (`sqlite3`, `zipfile`, `http.server`, `tomllib`, `json`, `re`, `xml.etree`). Dev pode usar pytest e hypothesis. | Wheels de terceiros em runtime: nenhum é necessário hoje. Se for preciso, só wheel puro vendorizado em `src/dmslog/_vendor/`. |
| D3 | **SQLite, um arquivo por caso**, com WAL (um escritor, vários leitores). | DuckDB/Parquet: nativos. Índice em memória: 2,4 M registros não cabem bem em RAM de PC corporativo e não persistem. Um banco global: misturaria casos com dados sensíveis e dificultaria apagar um caso. |
| D4 | **Texto do registro guardado no SQLite** (`entry.msg` + `entry_body.cont`) e **FTS5 `unicode61` com conteúdo externo**. | Só offsets + leitura do arquivo original: no zip, cada `seek` refaz a descompressão desde o início (23–97 ms por acesso aleatório num membro de 50–167 MB, medido), inviável para rolar o grid. FTS5 `trigram`: índice 3,2× o arquivo e 3× mais lento que `unicode61` (medido); busca por substring sai via `LIKE`/`REGEXP` restrito pelos filtros (116 ms para 2,4 M registros, medido). FTS5 contentless: menor, mas sem `snippet()` e exige SQLite ≥ 3.43 para apagar linhas, e o Python de algumas distros Linux traz SQLite mais antigo. |
| D5 | **Unidade = registro** (cabeçalho + continuações), não a linha física. Linha física é recuperável por `line_no` + `nlines`. | Linha física como unidade: o Wfm tem 776 mil continuações em 913 mil linhas (dumps de mapa de até 1.646 linhas); filtros, templates e severidade valem para o registro inteiro. |
| D6 | **Templates declarativos em TOML por adapter** (`tomllib` é stdlib), com regex nomeadas sobre a 1ª linha da mensagem (após `strip()`), classe, severidade, campos-chave, regras de ciclo de vida e pares normal↔sensitive. | Templates em código Python: misturam dado e lógica e dificultam revisar/estender para adapters sem amostra. YAML: não é stdlib. Mineração automática pura (Drain etc.): útil para descobrir, mas não dá IDs estáveis nem semântica de início/fim. Fica como ferramenta auxiliar (seção 6.6). |
| D7 | **Severidade inferida do texto**, nunca do nível do envelope: nos adapters, tudo é `DEBUGLOG`, inclusive exceções e erros reais (fato de `docs/formato/`). | Usar o nível do envelope: classificaria 100% como debug. |
| D8 | **Eventos derivados persistidos** (tabela `event`), recalculados por stream a partir de um checkpoint. | Calcular na consulta: durações e pareamentos de centenas de milhares de operações a cada clique. |
| D9 | **Histogramas por tabela de buckets por minuto** preenchida na ingestão. | `GROUP BY` sobre `entry`: 300–414 ms em 2,4 M registros; com buckets, 14–18 ms (medido). |
| D10 | **Conhecimento em pacote JSON versionado no repo**, gerado do vault por ferramenta de dev, mais a configuração efetiva lida dos XMLs do próprio ERS. | Ler o vault em runtime: o PC Windows não tem o vault. Copiar o vault inteiro: 7 MB de Markdown só no módulo 12, com conteúdo que não serve ao analisador. |
| D11 | **Distribuição como zipapp** (`dmslog.pyz`, um arquivo, roda em Windows e Linux). No Windows, pasta portátil com Python embeddable 3.13 + `dmslog.pyz` + `.bat`. No Linux, `python3 dmslog.pyz`. | PyInstaller/Nuitka: binário por SO, antivírus corporativo costuma bloquear executável não assinado, e o build fica mais complexo. Pacote instalável (pip): exige internet ou wheelhouse e permissões. Copiar `src/dmslog` solto (rascunho atual): funciona, mas o zipapp é um único arquivo, igual nos dois SOs, e `python -I dmslog.pyz` foi testado. |
| D12 | **Polling** (os.stat) para logs vivos, abrindo e fechando o arquivo a cada leitura. | inotify/ReadDirectoryChangesW: nativos e diferentes por SO. Manter o handle aberto: no Windows, o `open()` do Python não usa `FILE_SHARE_DELETE`; manter o arquivo aberto **impede o adapter de renomear `.log` → `.log.0` na rotação**. |

Sugestões iniciais confirmadas: ingestão → parser em 3 camadas → conhecimento → SQLite incremental → web local. Ajustes em relação à sugestão: a camada 3 do parser ficou dividida em severidade (no parser, por registro) e eventos derivados (no `derive`, por stream, porque precisa de estado entre registros e de checkpoint para a indexação incremental).

## 3. Experimentos (números de referência)

Máquina de dev: Linux, 20 núcleos, SSD, Python 3.14.7, SQLite 3.53.4. Scripts no scratchpad da sessão (`bench.py`, `bench2.py`, `zipbench.py`). Tudo em um único processo e uma única thread.

| Experimento | Resultado |
|---|---|
| FTS5 no Python do sistema | Presente (`ENABLE_FTS5`, tokenizer `trigram` disponível), SQLite 3.53.4 |
| FTS5 no Python embeddable 3.13.7 (Windows) | `sqlite3.dll` = SQLite 3.50.4 (fonte 2025-07-30), com `ENABLE_FTS5`, `trigram`, `unicode61`, JSON nativo. **Sem tkinter/Tcl.** `http.server`, `wsgiref`, `asyncio` presentes no `python313.zip`. (Inspeção estática da DLL; a execução real fica no smoke test do CI Windows.) |
| zipapp com `python -I` | Funciona; `sys.path[0]` = o `.pyz`. JSON do SQLite funciona no Python do sistema. |
| Só parse do envelope (Osr, 49 MB, 305 mil linhas, 270 mil registros) | 0,3 s (154 MB/s), 12 MB de RSS |
| Parse + classificação ingênua (≈100 regex em sequência, `catalog.py`) | Osr: 1,4 s (≈5 µs/registro). BLTHost sensitive 167 MB: 1,0 s |
| Índice por arquivo, Osr 49 MB: texto na tabela, sem FTS | 0,7 s, banco 66 MB (1,35×) |
| idem + FTS5 `unicode61` conteúdo externo | 1,0 s, 81 MB (1,64×), RSS 109 MB; busca FTS 0,1 ms; página de 200 por `ts` 0,1 ms |
| idem com FTS5 `trigram` | 3,2 s, 160 MB (3,24×), RSS 191 MB |
| FTS5 contentless (só offsets) | 1,0 s, 47 MB (0,94×) |
| BLTHost sensitive 167 MB (2,2 M linhas, 97 mil registros) com FTS5 | 1,8 s, 233 MB (1,39×), RSS 313 MB (lote de 20 mil registros grandes; limitar lote por bytes) |
| **Caso inteiro**: 30 arquivos (adapters normal + sensitive + BLTHost), 572 MB, 2,39 M registros, um só banco, `entry` estreita + `entry_body` + FTS5 + 3 índices | inserção 17,9 s + índices 1,9 s (≈29 MB/s), banco 907 MB (1,59×), RSS 270 MB |
| Consultas nesse banco (conexão nova) | `count(*)` 4 ms; página de 200 no merge por `(ts,id)` < 1 ms; merge de 3 fontes < 1 ms; página só severidade ≤ erro 10 ms; FTS + join 200 linhas < 1 ms; `LIKE '%xception%'` em `msg` (varredura) 116 ms; histograma por hora em `entry` 304 ms (× severidade 414 ms) |
| Tabela `bucket` (fonte × minuto × severidade × template) | criação 0,64 s, 107 mil linhas; histograma por hora 14 ms, × severidade 18 ms, filtrado 0,2 ms |
| Zip do ERS Enterprise (117 MB, 3.095 membros, deflate, sem zip64) | abrir + listar 6 ms; 2.272 membros `.log*` = 1.095 MB descomprimidos (45 MB comprimidos, ≈24×); 32 nomes de arquivo repetidos em pastas diferentes |
| Leitura em streaming de membro do zip | Osr 49 MB: 0,14 s (340 MB/s); BLTHost 167 MB: 0,69 s (240 MB/s); RSS ≈ 50 MB |
| `seek` em membro do zip | ir ao fim 47–97 ms; voltar ao meio 23–49 ms (rebobina e descomprime). Conclusão: zip só em leitura sequencial; acesso aleatório vem do SQLite |

Projeção: um ERS típico, no escopo padrão (seção 9.1), tem 400–600 MB de logs relevantes. Indexação inicial: 20–35 s com templates, banco de 0,7–1 GB. O grid fica utilizável por arquivo já nos primeiros segundos.

## 4. Layout do repositório

```
src/dmslog/
  __init__.py            versão
  __main__.py            python -m dmslog / zipapp
  cli.py                 subcomandos: serve, ingest, watch, info, export, templates, kb
  paths.py               pastas de dados por SO, nome de caso, checagem de FTS5
  ingest/
    discover.py          varre zip/pasta/arquivo → SourceInfo (família, PID, sensitive, rotação, conta, serviço)
    ers.py               identidade do ERS (GUID, timestamp, variante), pareamento Enterprise↔Sensitive, máquina/zona
    reader.py            leitura sequencial de arquivo ou membro de zip a partir de um offset
    fingerprint.py       impressão digital (head/tail hash, CRC do zip) e detecção de rotação
    pipeline.py          orquestra: fontes → parse → store → derive, com progresso
  parse/
    envelope.py          envelopes (adms, oaserr, plain) e detecção
    records.py           agrupamento linha→registro, seguro para retomada
    templates.py         carrega TOML, compila, classifica, extrai campos
    severity.py          severidade a partir de template, código de erro e marcadores de texto
    signature.py         normalização para assinatura de registro desconhecido
  adapters/              DADOS: _common.toml, awfm.toml, aont.toml, aosr.toml, asmn.toml, aavl.toml,
                         asss.toml, acrm.toml, aami.toml, asmr.toml, awdi.toml, aclc.toml, afup.toml,
                         afmo.toml, blthost.toml, oaserr.toml, service.toml (dnaService/ProcessMonitor/arb*)
  derive/
    engine.py            roda derivadores por stream a partir do checkpoint
    operations.py        início/fim/duração/status de operação
    stats.py             blocos [Communication<n>] → stat_sample (acumulado e delta)
    lifecycle.py         startup, janelas de nível (toggleDebug), gaps, restarts, recarga de mapas
    correlate.py         normal↔sensitive, chaves (msgId, GID, INC…), callbacks, retries
  knowledge/
    pack/                DADOS gerados: manifest.json, adapters.json, error_codes.json,
                         operations.json, parameters.json
    curated/             DADOS escritos à mão: hints.toml (template → explicação, parâmetros ligados)
    pack.py              carrega o pacote
    installation.py      lê XMLs do ERS/instalação (Registry, WebServiceConfiguration, ErrorConfiguration)
    resolve.py           código → severidade/detalhe; parâmetro efetivo; nota do vault
  store/
    schema.sql           DDL (seção 5)
    db.py                conexões, pragmas, migrações (user_version)
    writer.py            inserção em lote, remoção de cauda para retomada
    filters.py           árvore de filtro (JSON) → SQL parametrizado
    query.py             páginas, contagem, histograma, contexto, eventos, estatísticas
  server/
    app.py               ThreadingHTTPServer, roteador, token, cabeçalhos de segurança
    api.py               handlers JSON
    sse.py               progresso da ingestão e novidades do modo watch
  web/                   index.html, css/, js/ (módulos ES: grid, filtros, timeline, painéis),
                         vendor/uplot/ (+ LICENSE)
  report/
    export.py            relatório Markdown/HTML de uma seleção (eventos, bookmarks, trechos)
    redact.py            anonimização na exportação
tools/                   só dev, fora do pacote: kb_build.py, make_fixture.py, mine_templates.py, bench.py
tests/
  fixtures/              logs sintéticos pequenos (seção 13) e mini-vault para kb_build
  unit/  property/  integration/  perf/  samples/ (smoke opcional com local-samples, pulado no CI)
packaging/
  build_pyz.py           gera dist/dmslog.pyz (comum aos 2 SOs)
  build_portable.py      Windows: embeddable + dmslog.pyz + dmslog.bat
  dmslog.sh              Linux: launcher
```

## 5. Modelo de dados (SQLite por caso)

Arquivo: `<dados>/cases/<case_id>/index.sqlite` (seção 12). Pragmas: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `cache_size=-131072`, `temp_store=MEMORY`. Versão do esquema em `PRAGMA user_version`. Tempo: `ts` é **inteiro em ms** do horário local ingênuo do log (sem fuso; o log não informa fuso), codificado como se fosse UTC. Deslocamento por máquina fica em `collection.clock_offset_ms`, para alinhar ERS de máquinas diferentes.

```sql
-- Caso e origem -------------------------------------------------------------
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);          -- case_id, nome, criado_em, versão do pack, versão do dmslog

CREATE TABLE collection(                                       -- um ERS (zip), uma pasta Log ou um arquivo solto
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('ers_zip','folder','file')),
  path TEXT NOT NULL,                                          -- caminho absoluto local
  ers_guid TEXT, ers_ts TEXT,                                  -- de ERS_CL_<GUID>_<yyyymmddHHMMSS>_...
  ers_variant TEXT CHECK(ers_variant IN ('enterprise','sensitive')),
  ers_group INTEGER,                                           -- mesmo valor para o par Enterprise/Sensitive da mesma coleta
  machine TEXT, system TEXT, label TEXT,                       -- ex.: DSPMPDMZINT01 / mpdmz / rótulo do usuário
  clock_offset_ms INTEGER NOT NULL DEFAULT 0,
  added_at TEXT NOT NULL);

CREATE TABLE stream(                                           -- fluxo lógico: um processo (família+PID+sensitivity) numa coleta
  id INTEGER PRIMARY KEY,
  collection_group INTEGER NOT NULL,                           -- ers_group (ou collection.id); une normal e sensitive da mesma coleta
  family TEXT NOT NULL,                                        -- AWFM, AONT, ..., BLTHOST, OASERR, SERVICE, UNKNOWN
  process TEXT, pid INTEGER, account TEXT, service TEXT,       -- WfmAdapter, 37228, dnaApp_mpdmz, DMS_INTEGRATION
  sensitive INTEGER NOT NULL,
  derive_checkpoint INTEGER NOT NULL DEFAULT 0,                -- entry.id a partir do qual eventos são recalculados
  UNIQUE(collection_group, family, process, pid, sensitive));

CREATE TABLE source(                                           -- arquivo físico (ou membro de zip)
  id INTEGER PRIMARY KEY,
  collection_id INTEGER NOT NULL REFERENCES collection(id),
  stream_id INTEGER REFERENCES stream(id),
  member TEXT NOT NULL,                                        -- caminho relativo (no zip ou na pasta), separador '/'
  file_name TEXT NOT NULL,
  rotation INTEGER,                                            -- NULL = .log atual; 0,1,... = .log.N
  envelope TEXT NOT NULL,                                      -- adms | oaserr | plain
  size INTEGER, mtime REAL, zip_crc INTEGER,
  head_hash TEXT,                                              -- sha1 dos primeiros 4 KiB (identidade sob rotação)
  indexed_bytes INTEGER NOT NULL DEFAULT 0,
  tail_hash TEXT,                                              -- sha1 dos 4 KiB antes de indexed_bytes
  first_ts INTEGER, last_ts INTEGER, n_entries INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',                      -- pending|indexing|ready|error|skipped
  error TEXT,
  UNIQUE(collection_id, member));

-- Registros ------------------------------------------------------------------
CREATE TABLE entry(                                            -- estreita de propósito: é a tabela varrida pelo grid
  id INTEGER PRIMARY KEY,                                      -- crescente na ordem do arquivo
  source_id INTEGER NOT NULL,
  line_no INTEGER NOT NULL, byte_off INTEGER NOT NULL, nlines INTEGER NOT NULL,
  ts INTEGER,                                                  -- ms; NULL só em envelope 'plain'
  pid INTEGER, tid INTEGER,                                    -- tid NULL quando a linha não tem [TID=...]
  level INTEGER,                                               -- 1 FATAL .. 8 VERBOSE (envelope)
  sev INTEGER NOT NULL,                                        -- 1 critical, 2 error, 3 warning, 4 info, 5 noise (inferido)
  tpl_id INTEGER NOT NULL,                                     -- template.id; 0 = desconhecido
  event_id INTEGER,                                            -- operação/bloco a que pertence (nullable)
  flags INTEGER NOT NULL DEFAULT 0,                            -- bits: 1 partial, 2 has_cont, 4 ref_sensitive, 8 startup,
                                                               --       16 janela de nível elevado, 32 ts fora de ordem
  fields TEXT,                                                 -- JSON compacto dos campos extraídos (colunas dinâmicas)
  msg TEXT NOT NULL);                                          -- 1ª linha após o envelope, sem CRLF (espaços preservados)
CREATE INDEX ix_entry_ts      ON entry(ts, id);
CREATE INDEX ix_entry_src_ts  ON entry(source_id, ts, id);
CREATE INDEX ix_entry_sev_ts  ON entry(sev, ts, id);
CREATE INDEX ix_entry_tpl_ts  ON entry(tpl_id, ts, id);
CREATE INDEX ix_entry_event   ON entry(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX ix_entry_tid     ON entry(source_id, tid, id);

CREATE TABLE entry_body(entry_id INTEGER PRIMARY KEY, cont TEXT NOT NULL);  -- continuações, '\n' como separador

CREATE VIEW entry_text AS
  SELECT e.id AS rowid, e.msg AS msg, b.cont AS cont FROM entry e LEFT JOIN entry_body b ON b.entry_id = e.id;
CREATE VIRTUAL TABLE entry_fts USING fts5(msg, cont, content='entry_text', content_rowid='rowid',
  tokenize="unicode61 remove_diacritics 2");

CREATE TABLE entry_key(                                        -- chaves de correlação extraídas pelos templates
  key_type TEXT NOT NULL,                                      -- msgid, request_id, gid, inc, mrid, errcode, exc_type, op, client_id
  value TEXT NOT NULL,
  entry_id INTEGER NOT NULL,
  PRIMARY KEY(key_type, value, entry_id)) WITHOUT ROWID;
CREATE INDEX ix_key_entry ON entry_key(entry_id);

-- Templates ------------------------------------------------------------------
CREATE TABLE template(
  id INTEGER PRIMARY KEY,                                      -- 0 reservado para "desconhecido"
  code TEXT NOT NULL UNIQUE,                                   -- WFM-010, GEN-STA-02, ...
  family TEXT NOT NULL, class TEXT NOT NULL,                   -- start|end|req_resp|validation|error|statistics|startup|lifecycle|other
  sev INTEGER, title TEXT, pattern TEXT NOT NULL,
  def_version TEXT NOT NULL);                                  -- hash do TOML que o definiu
CREATE TABLE unknown_sig(                                      -- registros sem template, agrupados por assinatura
  family TEXT NOT NULL, signature TEXT NOT NULL,
  n INTEGER NOT NULL, first_entry_id INTEGER NOT NULL,
  PRIMARY KEY(family, signature)) WITHOUT ROWID;

-- Eventos derivados ------------------------------------------------------------
CREATE TABLE event(
  id INTEGER PRIMARY KEY,
  stream_id INTEGER NOT NULL,
  kind TEXT NOT NULL,          -- operation|startup|stats_block|level_window|gap|restart|retry_seq|map_reload|exception|sensitive_window
  name TEXT,                   -- operação (GetIncidents, Changed crews, SendWorks...) ou subtipo
  key TEXT,                    -- msgId/request_id/GUID principal, quando há
  tid INTEGER,
  start_entry_id INTEGER, end_entry_id INTEGER,
  ts_start INTEGER NOT NULL, ts_end INTEGER, duration_ms INTEGER,
  status TEXT,                 -- ok|business_error|fail|timeout|open|truncated
  sev INTEGER NOT NULL,
  n_entries INTEGER,
  detail TEXT);                -- JSON: códigos de erro, exceção, tentativas, de→para do nível...
CREATE INDEX ix_event_kind ON event(kind, name, ts_start);
CREATE INDEX ix_event_stream ON event(stream_id, ts_start);
CREATE INDEX ix_event_status ON event(status, ts_start) WHERE status <> 'ok';

-- Correlação --------------------------------------------------------------------
CREATE TABLE link(
  a_entry_id INTEGER NOT NULL, b_entry_id INTEGER NOT NULL,
  kind TEXT NOT NULL,          -- normal_sensitive|callback|retry|response|same_key
  confidence INTEGER NOT NULL, -- 3 chave idêntica, 2 ts+TID exatos, 1 mesmo segundo+TID/ordem, 0 heurística por janela
  PRIMARY KEY(a_entry_id, b_entry_id, kind)) WITHOUT ROWID;
CREATE INDEX ix_link_b ON link(b_entry_id);

-- Estatística periódica e agregados ------------------------------------------------
CREATE TABLE stat_sample(
  stream_id INTEGER NOT NULL, ts INTEGER NOT NULL,
  side TEXT NOT NULL,          -- client|service
  method TEXT NOT NULL,        -- '[TOTAL]' ou Contrato.Método
  calls INTEGER, req_bytes INTEGER, resp_bytes INTEGER,          -- acumulados desde o start do processo
  d_calls INTEGER, d_req_bytes INTEGER, d_resp_bytes INTEGER,     -- delta para a amostra anterior; NULL após queda (restart)
  PRIMARY KEY(stream_id, side, method, ts)) WITHOUT ROWID;

CREATE TABLE bucket(           -- contagens por minuto para timeline e painéis
  source_id INTEGER NOT NULL, minute INTEGER NOT NULL, sev INTEGER NOT NULL, tpl_id INTEGER NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY(source_id, minute, sev, tpl_id)) WITHOUT ROWID;

-- Configuração efetiva da instalação (lida do ERS) -------------------------------
CREATE TABLE config_param(
  collection_group INTEGER NOT NULL, product TEXT NOT NULL,     -- AdapterWfm, ...
  scope TEXT NOT NULL,                                          -- registry | ws:<Operação> | err:<Interface>
  name TEXT NOT NULL, value TEXT, source_file TEXT NOT NULL,
  effective INTEGER NOT NULL,                                   -- 1 = valor que vale após overrides
  PRIMARY KEY(collection_group, product, scope, name, source_file)) WITHOUT ROWID;
CREATE TABLE error_config(
  collection_group INTEGER NOT NULL, adapter TEXT NOT NULL, interface TEXT NOT NULL,
  adms_id TEXT NOT NULL, adms_severity TEXT, ext_code TEXT, ext_severity TEXT, reason TEXT, details TEXT,
  eventing_threshold TEXT, source_file TEXT NOT NULL,
  PRIMARY KEY(collection_group, adapter, interface, adms_id)) WITHOUT ROWID;

-- Do usuário (sobrevive a reindexação) --------------------------------------------
CREATE TABLE bookmark(
  id INTEGER PRIMARY KEY,
  collection_id INTEGER NOT NULL, member TEXT NOT NULL, line_no INTEGER NOT NULL,  -- localizador estável
  entry_id INTEGER,                                                                -- cache, refeito na reindexação
  color TEXT, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE saved_view(id INTEGER PRIMARY KEY, name TEXT NOT NULL, filter TEXT NOT NULL, columns TEXT, created_at TEXT NOT NULL);
```

Como cada requisito fica representado:
- **Linha física × registro × continuação**: `entry` é o registro; `msg` é a linha do cabeçalho; `entry_body.cont` guarda as continuações (só existe se houver); `line_no`/`nlines`/`byte_off` localizam as linhas físicas. Registro incompleto no fim do arquivo (ERS copia o arquivo em uso) recebe `flags & 1`.
- **Fontes/arquivos/ERS/máquina/zona**: `collection` (ERS ou pasta, com máquina/sistema e par Enterprise/Sensitive por `ers_group`) → `stream` (processo lógico, une `.log.0` + `.log`) → `source` (arquivo). A identidade do log inclui a coleta, como pede o PAIN 5.9 (há 32 nomes repetidos em pastas diferentes no ERS de exemplo).
- **Normal ↔ sensitive**: streams distintos (`sensitive` 0/1) da mesma `collection_group` e mesmo `pid`; ligações em `link(kind='normal_sensitive')`. O registro normal que remete ao sensitive tem `flags & 4`.
- **Templates**: `template` + `entry.tpl_id`; campos extraídos em `entry.fields` (JSON, para colunas dinâmicas via `json_extract`) e as chaves de correlação também em `entry_key` (indexadas).
- **Eventos derivados**: `event`, com `entry.event_id` apontando a operação dona do registro (registros sem chave na mesma TID entre início e fim, como `Detected errors:`).
- **Correlação entre fontes e máquinas**: `entry_key` (mesmo GUID/GID/INC em qualquer fonte do caso) + `link`.
- **Mudança de nível de log**: `event(kind='level_window')` a partir de `Debug level changed from X (n) to Y (m).` e `flags & 16` nos registros dentro da janela.

Ordem global (merge): `ORDER BY ts, id`. Dentro de uma fonte o `id` respeita a ordem do arquivo; timestamps que voltam no tempo (3 ms no Wfm) recebem `flags & 32` e continuam na posição do arquivo dentro da fonte.

## 6. Parsing

### 6.1 Camada 1: envelope

```python
@dataclass(slots=True)
class Header:
    ts_ms: int | None; pid: int | None; tid: int | None; level: int | None
    msg_start: int                       # offset do texto da mensagem na linha (bytes)
    process: str | None = None           # só no oasErrLog

class Envelope(Protocol):
    name: str
    def match(self, line: bytes) -> Header | None: ...

ENVELOPES: dict[str, Envelope]           # adms, oaserr, plain
def detect_envelope(head: bytes) -> Envelope   # testa as primeiras ~50 linhas; maioria vence; senão 'plain'
```

- `adms`: `^(?:\ufeff)?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) \[PID=(\d+)\](?:\[TID=0x([0-9a-fA-F]+)\])? (FATAL|EXIT|ERROR|ALWAYS|DEBUGLOG|WARNING|INFO|VERBOSE) - `. Aplicada em **bytes** (o decode só acontece no registro final). TID opcional (há uma linha sem TID no Ont).
- `oaserr`: `^(?:\ufeff)?(ts) (?P<proc>\S+)_(?P<pid>\d+) \[PID=(\d+)\]\[TID=0x(hex)\] - \((LEVEL)\) `.
- `plain`: cada linha é um registro sem `ts` (arquivos desconhecidos continuam navegáveis).
- O sufixo de fonte do BLTHost/serviço (` {E:\...\Main.cs:373}`) vira campo `src` e é removido do `msg` exibido apenas na coluna "mensagem" (o texto original continua no registro).
- Timestamp: cache por segundo (`AAAA-MM-DD HH:MM:SS` → ms base) e soma dos milissegundos. Evita `strptime` por linha.

### 6.2 Agrupamento em registros (seguro para retomada)

```python
@dataclass(slots=True)
class Record:
    byte_off: int; line_no: int; nlines: int
    ts_ms: int | None; pid: int | None; tid: int | None; level: int | None
    msg: str; cont: str | None; partial: bool

def iter_records(stream: BinaryIO, env: Envelope, *, start_off: int = 0, start_line: int = 1,
                 final: bool) -> Iterator[Record]
```

- Leitura por linha em bytes (`for raw in stream`), CRLF/LF aceitos, BOM só no início. Decode `utf-8` com `errors='replace'`.
- Registro = cabeçalho + linhas seguintes que não casam com o envelope. Linhas antes do primeiro cabeçalho formam um registro órfão (`ts` herdado = NULL, `sev` 5).
- **TAB no começo de `msg` não é continuação** (é cabeçalho com mensagem iniciada por TAB).
- `final=False` (arquivo vivo): o último registro não é emitido como definitivo; o pipeline guarda seu `byte_off` para retomar. `final=True` (zip ou arquivo parado): o último registro é emitido; se o arquivo não termina em `\n`, recebe `partial=True`.
- Propriedade garantida e testada: indexar o arquivo inteiro de uma vez produz os mesmos registros que indexá-lo em pedaços cortados em qualquer byte (seção 13).

### 6.3 Camada 2: templates declarativos por adapter

Um arquivo TOML por família em `src/dmslog/adapters/`. Exemplo (abreviado):

```toml
family = "AWFM"
process = ['^WfmAdapter$']                       # nome do processo no arquivo <Processo>_<pid>.log
extends = ["_common"]                            # GEN-*: estatística, conexões, Detected errors, SystemEventSender...

[[template]]
code = "WFM-010"
class = "start"
regex = '''^(?P<op>.+?) request received\. Request with ID "(?P<request_id>[0-9a-f-]{36})"\. Message content wr\w+ in sensitive log\.$'''
keys = ["request_id"]
refs_sensitive = true

[[template]]
code = "WFM-081"
class = "error"
sev = 2
regex = '''^ERROR - Processing "(?P<event_type>\w+)" message failed\. ExceptionType: "(?P<exc_type>[^"]+)" Reason: (?P<reason>.*)$'''
keys = ["exc_type"]

[[operation]]                                    # ciclo de vida (seção 6.5)
name_field = "op"
start = ["WFM-010", "WFM-011"]
end_ok = ["WFM-012"]
fail = ["WFM-081"]                               # falha dentro da operação aberta na mesma TID
pair_by = ["tid"]                                # e confere por request_id quando existir
check_key = "request_id"
unclosed = "fail_if_fail_seen"                   # sem fim + WFM-081 = falha; sem nada = open
timeout_params = ["WaitForCRSCallback", "WaitForCallback"]

[[sensitive_pair]]                               # normal → sensitive (seção 8)
normal = "WFM-010"
sensitive = "WFM-S010"
match = ["pid", "tid", "request_id"]
max_dt_ms = 1000
```

```python
@dataclass(frozen=True, slots=True)
class TemplateDef:
    code: str; family: str; cls: str; sev: int | None; regex: re.Pattern[str]
    keys: tuple[str, ...]; refs_sensitive: bool; literal_prefix: str | None

@dataclass(slots=True)
class TplMatch:
    tpl: TemplateDef | None; fields: dict[str, str]

class TemplateSet:
    @classmethod
    def for_family(cls, family: str, *, sensitive: bool) -> "TemplateSet": ...
    def classify(self, msg: str) -> TplMatch          # sobre msg.strip()
    def operations(self) -> list[OperationRule]: ...
    def sensitive_pairs(self) -> list[SensitivePairRule]: ...

def load_all(dirpath: Traversable) -> dict[str, TemplateSet]   # valida esquema, códigos únicos, regex compiláveis
```

- Ordem de teste: templates da família, depois `_common`. Despacho por prefixo literal (o trecho fixo no início da regex, como `[GetIncidents start]`) num dicionário; só os templates sem prefixo literal vão para a lista sequencial. A versão ingênua já custa ~5 µs por registro (medido), então o despacho é otimização opcional.
- Templates só olham a 1ª linha (`msg`). Para templates multilinha (`Detected errors:`, estatística, stack trace), um `cont_parser` nomeado extrai campos das continuações: `codes_per_line`, `stats_csv`, `dotnet_stack` (tipo e mensagem da exceção, 1º frame), `kv_tab` (dump de mapa: conta itens, não guarda pares).
- Registro sem template recebe `tpl_id=0` e entra em `unknown_sig` com a assinatura normalizada (GUID, timestamps, números, aspas, caminhos → marcadores; lógica de `minelib.norm` dos analistas).
- Famílias com amostra (19 arquivos, cobertura de 100% medida pelos analistas): AVL, Osr, Smn, SiteSwitch, Wfm, Ont, Crm, Ami, Smr, WDI, ClaimCheck, FileUpload, FileMonitor, BLTHost. Os IDs de `docs/formato/` viram os `code` dos templates. Os IDs genéricos dos três documentos são unificados em `_common` (`COM-*` e `WFM-090`/`ONT-030` = `GEN-STA-*`; `WFM-020..022` = `GEN-CON-*`; `WFM-050..052` = `GEN-ERR-*`/`GEN-EVT-01`), mantendo o código antigo como alias no TOML.

### 6.4 Camada 3a: severidade

```python
def infer_severity(level: int | None, tm: TplMatch, cont: str | None,
                   codes: CodeResolver, family: str) -> int
```

Ordem (o primeiro que decide vence):
1. Nível do envelope FATAL/EXIT → 1; ERROR → 2; `ALWAYS - (ERROR)` → 2.
2. `sev` declarado no template.
3. Códigos de erro do registro (`Detected errors:`, `[Validation X]: Código`): maior severidade entre os códigos, resolvida por `error_config` do ERS (fonte de verdade) e, na falta, pelo pacote do vault. Mapeamento: Critical/FATAL → 1, Error → 2, Warning → 3, Info/INFORM/Ok → 4.
4. Marcadores no texto (`msg` e 1ª linha de exceção): `Non Fatal Exception`, `Exception:`, `failed`, `unsuccessfully`, `[ERROR]`, `ERROR -`, `Errors occurred`, `[Validation Critical]` → 2; `WARNING`, `[Validation Warning]`, `not received within`, `timed out` → 3.
5. Exceções conhecidas de falso positivo (lista no `_common.toml`): `[Configuration][LoadingError]` seguido de `[Default]`, `[ERROR] - Validation error is detected during validation process of error configuration` no startup do Ami, `can be found adapters sensistive log`.
6. Caso contrário: 4 (info); estatística, dumps de mapa, conexões e health check → 5 (ruído, escondido por padrão).

### 6.5 Camada 3b: eventos derivados

```python
class Deriver(Protocol):
    kind: str
    def feed(self, e: StoredEntry) -> Iterable[Event]: ...   # StoredEntry = Record + id, tpl, fields, sev
    def flush(self, *, eof: bool) -> Iterable[Event]: ...
    def low_watermark(self) -> int: ...                      # menor entry.id ainda necessário (evento aberto)

def derive_stream(db: Store, stream_id: int, rules: TemplateSet, kb: Knowledge) -> None
```

`derive_stream` apaga os eventos com `start_entry_id >= stream.derive_checkpoint`, reprocessa os registros do stream a partir dali (em ordem de `id`, unindo `.log.0` e `.log`) e grava o novo checkpoint = menor `low_watermark()` dos derivadores. Assim a indexação incremental refaz só a cauda.

| Derivador | Regra (vinda do TOML e de `docs/formato/`) | Evento |
|---|---|---|
| `operations` | início/fim por `(op, tid)` no stream, conferido pela chave (`request_id`, `msgId`, GUID); `fail` dentro da operação aberta; fim `failed`/`unsuccessfully` → `fail`; `Detected errors` + `SystemEventSender` dentro → `business_error`; sem fim no EOF → `open` (ou `truncated` se o arquivo foi cortado pelo ERS); duração = `ts_end - ts_start` (resolução real de ~15,6 ms). Registros sem chave na mesma TID entre início e fim recebem `entry.event_id`. | `operation` |
| `retries` | tentativas com o mesmo `msgId` (`Error: Sending ... failed` do Smn/Ont), contando tentativas e intervalo; liga à operação. | `retry_seq` |
| `startup` | do primeiro `[Configuration]`/`[ServiceUrl]`/`=====` até 5 s após `Server state changed. New state: HOT` (ou 120 s). Coleta porta, URLs, `[Configuration]` com defaults. Registros recebem `flags & 8` e são colapsados no grid. | `startup` |
| `stats` | bloco `[Communication<n>]`: totais e CSV das 3 seções → `stat_sample` (acumulado e delta). Queda em qualquer total → `restart`. | `stats_block`, `restart` |
| `level` | `Debug level changed from X (n) to Y (m).` → janela até a próxima mudança; nível configurado (`LogLevel` do Registry) é o estado inicial. | `level_window` |
| `gaps` | intervalo sem registros maior que o batimento esperado da família (estatística horária: > 61 min; SiteSwitch: > 5 s; parâmetro no TOML), só dentro do período coberto pelo arquivo. | `gap` |
| `maps` | `"X" map closed` → `New "X" map content:` (+ itens) → `map is now open`. | `map_reload` |
| `exceptions` | agrupa exceções pela 1ª linha (tipo + mensagem normalizada), não pelos frames; versão do build sai do caminho `F:\PD\<build>\`. | `exception` (um por ocorrência; agrupamento na consulta) |
| `service` | `ProcessMonitor` (processo morto/reiniciado), `dnaService` (start/stop do serviço), `arb*` (transições HOT/STANDBY). | `restart`, `service_state` |

## 7. Camada de conhecimento

### 7.1 Do vault (pacote versionado)

Ferramenta de dev: `python tools/kb_build.py --vault ~/Projects/adms-v3.9/adms-knowledge-base/Concluído --out src/dmslog/knowledge/pack`. Lê só arquivos `.md` (somente leitura), não executa nada, gera JSON ordenado e determinístico (diff legível em PR).

| Arquivo do pack | Origem no vault | Conteúdo |
|---|---|---|
| `adapters.json` | `12 Interfaces online/Interfaces online.md` + `<SIGLA> Index.md` | sigla, nome, adapter, tipo (SOAP/Arquivos/REST), operações, versão do documento, caminho da nota; **mapa processo → sigla** (`WfmAdapter` → AWFM; complementado à mão em `curated/hints.toml` quando o vault não diz) |
| `error_codes.json` | `Catálogos/Catálogo de códigos de erro.md` | por adapter e bloco: ID/DMS Reason, ADMS Severity, Code, CIM Reason/Details |
| `operations.json` | `Catálogos/Catálogo de operações.md` | operação, serviço, direção, adapter |
| `parameters.json` | `Catálogos/Catálogo de parâmetros de configuração.md` + `<SIGLA> Configuration.md` | parâmetros com descrição e default; marcação dos que importam para log e tempo (`LogLevel`, `MessageLogLevel`, `AuditingPeriod`, `NumOfRetries`, `RetryTimeout`, `WaitForCRSCallback`, `SendTimeout`, ...) |
| `manifest.json` | — | data, commit do vault (se for repositório git), sha256 de cada arquivo lido, versão do gerador e do esquema do pack |

`curated/hints.toml` (à mão, revisado): por template, explicação curta, parâmetros relacionados e nota do vault a abrir. Exemplo: `WFM-080` → `WaitForCRSCallback` (default 35 s). Só conhecimento do produto; nada sobre os ambientes das amostras.

Atualização: quando o vault muda, rodar `kb_build`, revisar o diff do pack e commitar. Teste de CI valida o esquema do pack e roda o gerador num mini-vault de fixture.

### 7.2 Da instalação (por caso, na ingestão)

`knowledge/installation.py` lê do ERS ou da instalação (sempre por `xml.etree.ElementTree`, sem resolver entidades externas, com limite de tamanho por arquivo):
- `OASyS/Servers/config/Registry/Adapter*.xml`: `LogLevel`, `LogFileName`, `WebServiceConfiguration`, `<Op>_ErrorConfigurationPath`, parâmetros de tempo. Overrides (`AdapterX.xml`, `AdapterX_1_<Projeto>.xml`, `AdapterX_2.xml`) resolvidos por ordem de sufixo; quando a regra não for certa, a UI mostra todos os valores e marca o presumido.
- O `WebServiceConfiguration` **apontado pelo Registry** (ex.: `DMSCustomIntegrationPROD`, não o `DMSIntegration`): `MessageLogLevel`, `AuditingPeriod`, `NumOfRetries`, `RetryTimeout` por operação. `%OASINSTALLDATADIR%` mapeia para a raiz `SchneiderElectricData` da coleta.
- `*_ErrorConfiguration.xml` efetivos: `Adms/Id`, `Adms/Severity`, `ExternalSystem/Code|Severity|Reason|Details`, `ErrorSeverityEventingThreshold`.

```python
class Knowledge:
    def adapter_for_process(self, process: str) -> AdapterInfo | None
    def error(self, family: str, code: str, operation: str | None = None) -> ErrorInfo | None   # ERS > vault
    def param(self, group: int, product: str, name: str, scope: str = "registry") -> ParamValue | None
    def hint(self, tpl_code: str) -> Hint | None
    def vault_note(self, family: str) -> str | None        # caminho relativo da nota, para exibir/copiar
```

Precedência: configuração do ERS > pack do vault. Divergências (código no log que não existe no vault, como `ImsCallbackNotReceived`, ou no XML) aparecem como "fora do vault" no painel de conhecimento.

## 8. Correlação

| Tipo | Regra | Confiança |
|---|---|---|
| normal ↔ sensitive | Para cada registro normal com `refs_sensitive`, buscar no stream sensitive da mesma coleta e PID, na **mesma TID**, o próximo registro do template par com `|Δt| ≤ 1 s`, respeitando a ordem. Se houver chave comum (Wfm `request_id`), ela decide. Restringir à interseção das janelas dos dois arquivos (as coberturas diferem muito). | 3 por chave; 2 ts+TID exatos; 1 mesmo segundo + TID |
| callback | `GID` (Crm `OMS_REQUEST`, Osr) entre registro de espera e callback, em outra TID. Wfm `Callback received` sem chave na linha de início: só por janela de tempo, marcado como heurístico. | 3 / 0 |
| retry e resposta | mesmo `msgId` (Smn/Ont `Sending ... message: <guid>` → `finished` → `Request message with MessageId ... sent`). | 3 |
| mesma chave | qualquer valor de `entry_key` repetido em fontes diferentes do caso (INC, GID, mRID, GUID): consulta sob demanda, não materializada. | — |
| entre máquinas | por chave e por tempo com `clock_offset_ms` da coleta. | — |

`correlate.py` roda por stream após `derive` e também refaz só a cauda (mesmo checkpoint).

## 9. Ingestão, indexação incremental e desempenho

### 9.1 Descoberta e escopo

`discover(path) -> Iterator[SourceInfo]` aceita zip de ERS, pasta (instalação ou ERS extraído) e arquivo. Classifica pelo caminho `.../Log/<conta>/<serviço>/<Processo>_<pid>[.sensitive].log[.N]`, `oasErrLog.log[.N]`, `BLTHost.<serviço>[.sensitive].log`, `ProcessMonitor.<serviço>.log`, `dnaService.<serviço>.log`, `dnaHealthMonitor.*`, `arb*.<serviço>.log`.

Escopo padrão: adapters (normal e sensitive) + BLTHost + oasErrLog + ProcessMonitor/dnaService/arb do serviço do adapter. NMC, PSengine, arbIP e o resto do `Log/` (≈ 800 MB no ERS de exemplo) ficam listados e são indexados só se o usuário marcar. O par Enterprise/Sensitive é reconhecido pelo nome (`ERS_CL_<GUID>_<timestamp>_[Sensitive_]Enterprise_<desc>.zip`) e vira um único `ers_group`.

### 9.2 Leitura

- Zip: `zipfile.ZipFile.open(member)` em streaming, nunca extraído em disco, nunca `seek` aleatório. Proteção contra zip bomb: limite de tamanho descomprimido por membro (configurável, padrão 4 GB) e razão máxima de compressão (padrão 200×; logs dão ~24×).
- Arquivo: `open(path, 'rb')`, `seek(indexed_bytes)`, ler até o EOF, **fechar**. Nunca manter handle aberto entre ciclos (rotação no Windows, D12).
- Lotes de inserção limitados por bytes (~8 MB) e por registros (50 mil), numa transação por lote. RSS medido: 270 MB no caso inteiro.

### 9.3 Incremental

Estado por `source`: `size`, `mtime`, `zip_crc`, `head_hash` (4 KiB iniciais), `indexed_bytes`, `tail_hash` (4 KiB antes de `indexed_bytes`), id e offset do último registro.

| Situação | Detecção | Ação |
|---|---|---|
| Membro de zip já indexado | mesmo `zip_crc` + `size` | nada |
| Arquivo cresceu | `size > indexed_bytes` e `tail_hash` confere | apagar o último registro (pode ter ganho continuações), retomar do `byte_off` dele, recontar `bucket`, refazer derivação/correlação a partir do checkpoint do stream |
| Rotação | `head_hash` do `.log` mudou e um `.log.0` novo tem o `head_hash` antigo | reapontar o `source` antigo para `.log.0` (mantém registros), criar `source` novo para `.log` e indexar do zero |
| Truncado/reescrito | `size < indexed_bytes` ou `tail_hash` difere sem rotação | reindexar o arquivo (apaga registros dele em `entry`, `entry_body`, FTS, `entry_key`, `bucket`; bookmarks são reassociados por `member` + `line_no`) |
| Arquivo novo (novo PID após restart) | não há `source` | novo `stream`/`source` |

Modo `watch`: polling a cada 2 s (configurável) com `os.stat`; novidades viram eventos SSE para a UI ("+342 registros em WfmAdapter"). Exclusões do FTS de conteúdo externo usam o comando `'delete'` com o texto antigo, lido antes de apagar.

### 9.4 Consultas e UI

- Paginação **keyset** por `(ts, id)` nas duas direções (`WHERE (ts,id) > (?,?) ORDER BY ts,id LIMIT 200`); nunca `OFFSET`. Ir para um instante = buscar o primeiro `(ts,id) >= t`.
- Barra de rolagem do grid: posição aproximada pela soma acumulada de `bucket` (contagem por minuto); o grid busca a página do minuto correspondente.
- Contagem total do filtro: `count(*)` com timeout de 2 s; se estourar, a UI mostra estimativa por `bucket` e continua.
- Busca: termo → FTS5 (com `snippet()`); substring/regex → `LIKE`/`REGEXP` (função Python registrada) **sempre junto de um filtro de fonte ou de tempo**, em thread com cancelamento e limite de resultados (busca por regex não tem timeout nativo em `re`).
- Texto grande: a API devolve `msg` + até 200 linhas de `cont` e um link para o resto.
- Indexação em thread própria com conexão própria (escritor único); requisições HTTP em threads com conexões de leitura (WAL). Fontes prontas ficam navegáveis enquanto as outras indexam.

## 10. API (servidor ↔ viewer)

JSON sobre HTTP em `127.0.0.1`. Todas as rotas exigem o cookie de sessão (seção 12). Cursor = `"<ts>:<id>"`.

| Rota | Descrição |
|---|---|
| `GET /api/health` | versão, versão do pack, SQLite, FTS5 ok |
| `GET /api/cases` · `POST /api/cases` `{name}` · `DELETE /api/cases/{id}` | casos locais; apagar remove a pasta do caso |
| `POST /api/cases/{id}/collections` `{path, scope}` | adiciona ERS/pasta/arquivo e inicia a ingestão |
| `GET /api/cases/{id}/progress` (SSE) | progresso por fonte; novidades do modo watch |
| `GET /api/sources` | coleções, streams e fontes (família, PID, sensitive, janela, contagens, status) |
| `GET /api/templates?family=` | templates com contagem; `GET /api/unknown?family=` assinaturas sem template |
| `POST /api/entries/page` `{filter, cursor, dir, limit, columns}` | página do grid; colunas dinâmicas vêm de `fields` |
| `POST /api/entries/count` `{filter}` | contagem (ou estimativa) |
| `GET /api/entries/{id}` | registro completo: texto, campos, template, evento, links, conhecimento ligado |
| `GET /api/entries/{id}/context?before=50&after=50&same=tid\|source\|merge` | vizinhança |
| `POST /api/histogram` `{filter, bucket_ms, split: sev\|source\|tpl}` | timeline |
| `POST /api/events/page` `{kind, name, status, filter, cursor}` · `GET /api/events/{id}` | eventos e seus registros |
| `POST /api/events/stats` `{kind, name, group_by}` | n, p50, p90, p99, máx. de duração; contagem por status |
| `GET /api/stats/series?stream=&side=&method=` | série de `stat_sample` (delta por hora) |
| `GET /api/keys/{type}/{value}` | todos os registros com a chave, em todas as fontes |
| `GET /api/knowledge/error/{family}/{code}` · `/param/{product}/{name}` · `/adapter/{family}` | conhecimento (ERS > vault) |
| `GET /api/config?group=` | configuração efetiva da instalação |
| `GET/POST/DELETE /api/bookmarks` · `GET/POST/DELETE /api/views` | bookmarks com nota; filtros salvos |
| `POST /api/export` `{what, filter, redact}` | relatório Markdown/HTML (download) |

Filtro (árvore JSON, compilada para SQL **parametrizado** em `store/filters.py`; nada do usuário vira SQL literal):

```json
{"and": [
  {"source": [3, 4]}, {"sev": {"le": 2}}, {"tpl": ["WFM-081", "GEN-EXC-01"]},
  {"ts": {"ge": 1791374400000, "lt": 1791378000000}}, {"tid": 17604},
  {"text": {"fts": "timeout"}}, {"key": {"type": "request_id", "eq": "a0b0..."}},
  {"not": {"tpl": ["GEN-STA-01", "GEN-STA-02", "GEN-STA-03"]}},
  {"or": [{"event_status": "fail"}, {"flags": "partial"}]}
]}
```

A caixa de busca do viewer aceita a forma curta `sev:<=error tpl:WFM-081 tid:0x44c4 src:Wfm "texto exato" /regex/`, convertida para essa árvore no front-end.

```python
# store/query.py
def page(conn, flt: Filter, cursor: Cursor | None, direction: Literal["fwd", "back"], limit: int,
         columns: list[str]) -> Page
def count(conn, flt: Filter, timeout_s: float = 2.0) -> CountResult          # exact | estimate
def histogram(conn, flt: Filter, bucket_ms: int, split: str | None) -> list[Bin]
def context(conn, entry_id: int, before: int, after: int, same: str) -> list[Row]
def event_stats(conn, kind: str, name: str | None, flt: Filter | None, group_by: str | None) -> list[Stat]
```

## 11. Viewer: funcionalidades × LogViewPlus, por marco

| Funcionalidade | Conceito LogViewPlus | Marco |
|---|---|---|
| Abrir ERS (zip ou par), pasta ou arquivo; lista de fontes com família, PID, normal/sensitive, janela temporal | Open Zip, partial open | M1 |
| Grid virtual com colunas fixas (hora, fonte, PID, TID, nível, mensagem) e painel de detalhe multilinha | Log Entry Grid, multiline | M1 |
| Busca de texto (FTS) e filtro por fonte/nível/tempo/TID | Filters | M1 |
| Templates: coluna e filtro por template, severidade inferida com cores, ocultar ruído (estatística, dumps) | Message Templates, Color Rules | M2 |
| Merge de fontes numa timeline única com indicador de origem | Merge Files | M2 |
| Árvore de filtros AND/OR/NOT, exclusões, filtros salvos | Filter tree | M2 |
| Colunas dinâmicas a partir dos campos do template (op, msgId, GID, código) | Dynamic columns | M2 |
| Timeline (histograma) com seleção de intervalo, empilhado por severidade/fonte | Graph Reports | M2 |
| Painel "templates desconhecidos" (assinatura, contagem, exemplo) | — (necessário para adapters novos) | M2 |
| Códigos de erro e templates com explicação, severidade do ERS e do vault, link para nota; painel de configuração efetiva (LogLevel, MessageLogLevel, timeouts) | — (diferencial do projeto) | M3 |
| Operações como linhas: duração, status, drill-down até os registros; p50/p90/p99 por operação | Session Rules, elapsed time | M4 |
| Painéis por adapter: throughput, erros, durações, estatísticas `[Communication]`, gaps, startups, janelas de nível | Dashboards com drill-down, Statistics View | M4 |
| Normal ↔ sensitive lado a lado; "ver no sensitive" a partir do registro; tudo de uma chave (INC, GID, GUID) | Correlação, Session Rules | M5 |
| Bookmarks com nota e cor; comparar dois registros; exportar relatório para chamado com anonimização | Bookmarks, Compare, Export | M6 |
| Pasta de instalação ao vivo (polling incremental, "seguir o fim") | tail | M6 |
| Tema escuro/claro, atalhos de teclado (próximo erro, próximo do mesmo template, ir para hora) | Dark mode, navegação | M1 (tema), M2 (atalhos) |

## 12. Segurança e privacidade

- Servidor só em `127.0.0.1`, porta aleatória (ou `--port`). Token aleatório (`secrets.token_urlsafe(32)`) na URL aberta pelo `webbrowser`; trocado por cookie `HttpOnly; SameSite=Strict`. Sem token/cookie válido: 403.
- Checagem de `Host` (`127.0.0.1:<porta>`/`localhost:<porta>`) contra DNS rebinding; checagem de `Origin` em POST/DELETE.
- Cabeçalhos: `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` nas respostas da API.
- **Conteúdo de log é dado não confiável**: o front-end só usa `textContent`/nós de texto, nunca `innerHTML` com conteúdo de log (XML, stack traces e mensagens podem conter `<script>`). Destaques de busca montados com `Text` + `<mark>` criados por DOM.
- Nada sai da máquina: sem telemetria, sem verificação de atualização, sem CDN, sem fontes externas. Teste de CI garante que o pacote não abre sockets fora de loopback.
- Caminhos: só os que o usuário informa; leitura de zip sem extrair em disco (sem zip slip); XML via `ElementTree` sem entidades externas, com limite de tamanho.
- SQL só parametrizado; regex do usuário executada em thread com limite de linhas e cancelamento.
- Dados em pasta do usuário: Windows `%LOCALAPPDATA%\dmslog\`, Linux `${XDG_DATA_HOME:-~/.local/share}/dmslog/`; `--data-dir` para outro lugar (ex.: pendrive cifrado). Um caso = uma pasta; apagar o caso apaga o índice. A UI mostra onde estão os dados e o tamanho.
- Exportação: `redact.py` substitui GUIDs, GIDs, INC/PROB, mRIDs, placas, hosts, usuários, URLs e caminhos por marcadores estáveis dentro do relatório (o mesmo valor vira o mesmo marcador). Exportar sem anonimizar exige marcar a opção explicitamente.
- Repositório: nada de log real, índice ou export em git; `.gitignore` cobre `dist/`, `local-samples/_extracted/`, `*.sqlite`.

## 12a. Idioma do app (i18n)

Requisito firme: **todo o app é em inglês**. Vale para nomes de pacote, módulo, função, variável, tabela e coluna; comentários e docstrings; mensagens de CLI, log, API e erro; textos da UI; rótulos do pacote de conhecimento (chaves e textos gerados/curados); nomes de testes e fixtures; e a documentação de uso do app (`README.md` e ajuda da CLI). Os documentos de projeto em `docs/` (este, `ROADMAP.md`, `docs/formato/`) continuam em pt-BR; por isso os nomes `ARQUITETURA.md` e `ROADMAP.md` ficam como estão (o `CLAUDE.md` já aponta para eles).

- Os comentários em português dentro do DDL e dos exemplos deste documento são explicação para o leitor; o `schema.sql`, os TOML e o código reais são escritos em inglês. Identificadores, valores de enum e nomes de esquema mostrados aqui já são os definitivos (inglês).
- Só inglês por enquanto, mas **textos visíveis ao usuário ficam centralizados**: `src/dmslog/messages.py` (CLI, API, erros) e `src/dmslog/web/js/strings.js` (UI), acessados por chave. Nada de texto de UI espalhado em código, para que um locale futuro seja só mais um catálogo.
- **Texto dos logs dos adapters é exibido literalmente** (sem tradução nem correção, inclusive `sensistive`, `wirtten`). O conteúdo do vault é em português; o pack guarda os campos de origem como vieram (por exemplo, `Details` do catálogo de erros, que já está em inglês) e as explicações curadas (`curated/hints.toml`) são escritas em inglês.

## 13. Testes

- **Fixtures sintéticas pequenas** em `tests/fixtures/logs/`, escritas a partir dos templates documentados, com valores fictícios: um arquivo por família (10–200 registros) cobrindo cada template, mais casos de borda: BOM, CRLF, linha sem TID, mensagem iniciada por TAB, mensagem terminando em espaço, dump de 1.600 linhas, stack trace .NET com inner exception, último registro truncado, timestamp que volta 3 ms, operação dividida entre `.log.0` e `.log`, janela `Debug level changed`, bloco de estatística com queda de contador (restart), envelope `oaserr`. Os zips de teste são montados em tempo de teste (`zipfile`) a partir dessas fixtures.
- `tools/make_fixture.py` ajuda a criar fixtures a partir de um log real (amostra por template + substituição determinística de GUID, números de ID, nomes, hosts, caminhos e URLs). Toda fixture derivada passa por varredura automática de vazamento (lista de termos proibidos mantida fora do repo + regex de hostname/usuário/URL/placa/GUID real) e revisão humana antes do commit.
- **Propriedades** (hypothesis, só dev):
  - toda linha gerada a partir de um cabeçalho válido casa com o envelope; nenhuma continuação gerada casa;
  - `join(records) == bytes originais` (reconstrução exata);
  - indexar inteiro == indexar em pedaços cortados em qualquer byte (garantia da retomada incremental);
  - a classificação não depende da ordem de carga dos TOML; cada template casa com seus próprios exemplos e com nenhum exemplo de outro template da mesma família (exemplos positivos/negativos ficam no próprio TOML).
- **Unitários**: envelope, records, templates (cobertura por família = 100% nas fixtures), severidade, cada derivador (durações e status esperados), correlação, filtros → SQL (inclusive tentativa de injeção), incremental (crescer, rotacionar, truncar), `installation.py` com XMLs fictícios, `kb_build` com mini-vault.
- **Integração**: `dmslog ingest` de zip de fixture → banco → API (servidor em porta efêmera, cliente `urllib`) → respostas esperadas; cabeçalhos de segurança; 403 sem token.
- **Amostras reais (opcional, local)**: `tests/samples/` só roda se `local-samples/_extracted/` existir (pulado no CI). Confere **invariantes de formato** registradas em `docs/formato/` (cobertura 100%, contagens de início = fim + falha), nunca diagnóstico dos ambientes.
- **Desempenho**: gerador sintético de 50 MB; meta ≥ 20 MB/s de indexação completa e página do grid < 50 ms. Roda no CI como informativo (não bloqueia), e localmente antes de cada marco.
- **CI** (GitHub Actions): matriz `ubuntu-latest` × `windows-latest` × Python 3.11 e 3.13; lint (`ruff`, dev); testes; build do `dmslog.pyz`; job Windows monta a pasta portátil e roda o smoke test com o Python embeddable (`--version`, FTS5, ingest de fixture, `GET /api/health`); artefatos `dmslog.pyz`, `dmslog-portable-win64.zip`, `dmslog-linux.tar.gz`.

## 14. Empacotamento e distribuição

- **Comum**: `packaging/build_pyz.py` gera `dist/dmslog.pyz` (zipapp de `src/dmslog`, inclusive `web/`, `adapters/`, `knowledge/`; recursos lidos por `importlib.resources`, que funciona dentro do zip).
- **Windows (portátil)**: `packaging/build_portable.py` (o rascunho atual fica, com ajustes): baixa o Python embeddable 3.13.x com SHA-256 fixado, extrai em `python/`, copia `dmslog.pyz`, gera `dmslog.bat` (`"%~dp0python\python.exe" -I "%~dp0dmslog.pyz" serve %*`) e `LEIA-ME.txt`. O `._pth` fica como vem (sem `import site`, isolado); a linha `..\app` sai, porque o `.pyz` entra no `sys.path` sozinho. A janela de console mostra a URL e fecha o servidor com Ctrl+C. Sem instalador, sem admin, sem registro do Windows, sem internet. Tamanho estimado: ~12 MB zipado.
- **Linux**: `dmslog-linux.tar.gz` com `dmslog.pyz` + `dmslog` (shell: `exec python3 -I "$(dirname "$0")/dmslog.pyz" "$@"`). Requisito: Python ≥ 3.11 com `sqlite3` e FTS5 (verificado na inicialização, com mensagem clara se faltar). No Omarchy, o Python do sistema serve.
- Versão: `dmslog.__version__` + versão do pack + hash do commit gravados no `.pyz` e em `GET /api/health`.
- `.github/workflows/ci.yml` (rascunho atual) é mantido e ampliado conforme a seção 13.

## 15. Registro de mudanças da arquitetura

| Data | Mudança | Autor |
|---|---|---|
| 2026-10-08 | Versão inicial | Opus |
| 2026-10-08 | App inteiro em inglês (seção 12a); classes de template e severidades com nomes em inglês | Opus |

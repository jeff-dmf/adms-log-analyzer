# Arquitetura do dmslog

Analyzer + viewer dos logs do ERS do ADMS GridOps 3.9, com foco da release 1.0 nos adapters (online interfaces). Entrada: **um** ERS por caso (zip, normalmente o par Enterprise + Sensitive de um servidor) ou a pasta `Log` de uma instalação. Saída: índice local consultável e um viewer no navegador, apoiado na base de conhecimento do vault `adms-v3.9`.

Fatos de formato vêm de `docs/formato/*.md`, a referência de UX vem de `docs/REFERENCIA-UX.md` e as restrições de portabilidade estão no `CLAUDE.md`. As decisões do usuário que fixam o escopo estão na seção 1a. Mudanças de estrutura, interfaces ou esquema passam pelo Opus e ficam registradas aqui (seção 15).

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

## 1a. Decisões do usuário (2026-10-08)

Respostas do Jefferson às perguntas em aberto da versão inicial. Valem como requisito; o restante do documento já está ajustado a elas.

| # | Decisão | Consequência na arquitetura | Onde |
|---|---|---|---|
| U1 | Repositório **privado**; o pacote de conhecimento derivado do vault pode ser commitado | `src/dmslog/knowledge/pack/` versionado. O conteúdo é documentação Schneider Electric: o repositório (e qualquer fork ou artefato com o pack, inclusive `dmslog.pyz`) deve permanecer privado | D10, 7.1 |
| U2 | PC Windows de destino tem **Edge e Chrome**; o antivírus não bloqueia o `python.exe` | Front-end mira Chromium (Edge/Chrome atuais) nos dois SOs; Firefox é melhor esforço. A pasta portátil com Python embeddable fica confirmada | D1, D11, 14 |
| U3 | Índices em `%LOCALAPPDATA%` (Linux: diretório de dados XDG) | `%LOCALAPPDATA%\dmslog\` e `${XDG_DATA_HOME:-~/.local/share}/dmslog/`; `--data-dir` continua opcional | 12 |
| U4 | A ferramenta deve analisar **todos** os logs do ERS; a meta da 1.0 é cobrir os adapters | Na 1.0, qualquer log de texto do ERS abre, indexa, busca e entra na timeline (envelope + `_common`); templates, eventos e painéis específicos só para os adapters. Esquema e descoberta sem nada específico de adapter, para os demais logs entrarem depois só com dados (TOML) e derivadores novos | 9.1a |
| U5 | **Um ERS por caso**, de um servidor | Caso = uma coleta (par Enterprise/Sensitive ou uma pasta de instalação). Múltiplos ERS, correlação entre máquinas e ajuste de relógio saem da 1.0; `collection.clock_offset_ms` e `collection_group` ficam no esquema (porta aberta), sem UI | 5, 8, 10 |
| U6 | Timestamps em **horário local**, sem conversão de fuso | `ts` guarda o horário do log como está; UI e exportação mostram sem conversão; salto de horário de verão é detectado e sinalizado, não corrigido | 5 (Tempo) |
| U7 | Suporte a **payload** (request/response) é obrigatório na 1.0 | Amostra Verbose recebida (ERS de 2026-10-08 14:53, Wfm/Ont/Smn elevados ~2 h) e formato descoberto em `docs/formato/payloads.md` (tarefa 6.1). Esquema `payload` definitivo, visualização XML formatada, ligação ao evento por chave ou por tempo, cuidado com dados sensíveis | 5, 6.6, 10, 12 |
| U8 | Adapters sem amostra não existem no ambiente produtivo: **fora do escopo** | Escopo de adapters: Wfm, Ont, AVL, Osr, Smn, SiteSwitch, Crm, Ami, Smr, WDI, ClaimCheck, FileUpload, FileMonitor + BLTHost, com inclusão de `CustomAdapterChangeIncidentDevice` confirmada em U11. O antigo M7 ("adapters sem amostra") foi removido do roadmap | 6.3, ROADMAP |
| U9 | Precedência do Registry: **sufixo maior vence** onde há sobreposição; `AdapterX_1_<Projeto>` configura o **mesmo processo**, no escopo do projeto, com base em `AdapterX` | Camadas resolvidas por chave. Os arquivos de projeto são distribuídos nos pacotes de atualização MSI/MSU específicos daquele projeto ADMS; não representam instâncias independentes | 7.2 |
| U10 | Exportação para chamado (ServiceNow): **`.txt`** colável, anonimização configurável | `report/export.py` gera só texto simples; perfis de anonimização por categoria + termos do usuário | 10, 12 |
| U11 | `CustomAdapterChangeIncidentDevice` entra na **1.0 com análise específica**; Jefferson ainda criará sua base de conhecimento no `adms-v3.9` ([issue #22](the ADMS knowledge-base repository)) | Descobrir formato e definir templates/eventos específicos, apoiados na base. Leitura genérica provisória é permitida e a pendência não bloqueia M0; base e cobertura específica são requisitos de release | 6.3, ROADMAP |
| U12 | (2026-10-09, issue #18 do repositório anterior) Bancos `LocalPersistenceStore/*.db3` (fila/cache dos adapters) entram como **fonte opcional somente leitura depois da 1.0** (P3); a proposta do registro de mudanças de 2026-10-08 vira decisão. Fora da 1.0 | Seções 9.1a e 15; ROADMAP Pós-1.0 | 9.1a, 15 |
| U13 | (2026-10-09) Wfm sensitive: o **resumo de 3 linhas é mantido** junto do payload completo | Nenhuma remoção de registros; o payload é ligado ao anúncio (6.6) | 6.6 |
| U14 | (2026-10-09) **Info basta** nas próximas coletas (payload em todas as operações, cerca de 7x menos volume que Verbose) | Metas de volume do M6/M8 partem de Info; Verbose só por pedido | 9.3a |
| U15 | (2026-10-09) O texto `ResultSet` do `BLT-011` **não é indexado no FTS** (`fts = false`); o registro continua no grid e nas contagens | `blthost.toml` BLT-011 com `fts = false`; reindexar casos antigos | 6.3, 9.3a |
| U16 | (2026-10-09) Confirmadas as decisões tomadas pela sessão: banco do caso `case.sqlite`; BLTHost no escopo padrão; UTF-16 com BOM transcodificado na leitura; razão máxima de compressão (200x) isenta até 1 GiB descomprimido; mesmo GUID com timestamp diferente = 409 (duas coletas = dois casos); até 4 agentes em paralelo | Registradas aqui para não voltarem a ser perguntadas | 5, 9.2 |
| U17 | (2026-10-10) A **primeira release será a 0.1.0**; a 1.0.0 só quando tudo estiver muito maduro. Nos documentos e no código, "1.0" ("escopo da 1.0", "release 1.0", "aceite da 1.0") passa a significar o escopo e o aceite da **0.1.0**; o que ficar fora (P1, P2, `.db3`) não bloqueia a 0.1.0, e a 1.0.0 será definida depois | Versão do pacote continua `0.1.0` (`pyproject.toml`, `dmslog.__version__`); M8 entrega a 0.1.0 | 15, ROADMAP M8 |
| U18 | (2026-10-11) O **app final não anonimiza nem mascara dados**: roda local, na máquina do usuário, sem exposição. A redação/anonimização (`redact.py`, `redaction.toml`, `aliases.toml`) é ferramenta de **desenvolvimento**, por causa do repo público e dos ERS reais. Em desenvolvimento ela segue obrigatória e fail-closed: o strict mantém a rede padrão-nega mesmo com todas as categorias desligadas, e só a opção explícita de não anonimizar entrega texto intacto | A forma de entrega (remover o recurso da release ou entregá-lo desligado) fica para o desenho do M8; a U10 deve ser revista nesse marco | 10.2, ROADMAP M8 |

## 2. Decisões e alternativas descartadas

| # | Decisão | Alternativas descartadas e por quê |
|---|---|---|
| D1 | **Viewer web local**: `http.server` (stdlib) em 127.0.0.1 + página estática (HTML/JS/CSS vendorizados, sem framework e sem build). Gráficos com uPlot (MIT, 1 arquivo, vendorizado). | **TUI**: `curses` não existe no CPython de Windows; `textual`/`rich` funcionam, mas grid de 900 mil linhas com colunas dinâmicas, timeline e drill-down em terminal ficam bem abaixo da referência (LogViewPlus). **Desktop tkinter**: o Python embeddable de Windows **não traz tkinter/Tcl** (verificado no `python-3.13.7-embed-amd64.zip`); teria que empacotar Tcl/Tk à mão, e o widget de grid é fraco. **Qt/PySide, Electron, Tauri**: dependências nativas grandes, contra a regra de stdlib/wheels puros. **SPA com framework (React/Vue) e bundler**: exige build e toolchain Node; o ganho não paga para uma UI de uma pessoa. O PC Windows de destino tem Edge e Chrome (U2); o alvo do front-end é Chromium atual, Firefox é melhor esforço. |
| D2 | **Python ≥ 3.11, só stdlib em runtime** (`sqlite3`, `zipfile`, `http.server`, `tomllib`, `json`, `re`, `xml.etree`). Dev pode usar pytest e hypothesis. | Wheels de terceiros em runtime: nenhum é necessário hoje. Se for preciso, só wheel puro vendorizado em `src/dmslog/_vendor/`. |
| D3 | **SQLite, um arquivo por caso**, com WAL (um escritor, vários leitores). | DuckDB/Parquet: nativos. Índice em memória: 2,4 M registros não cabem bem em RAM de PC corporativo e não persistem. Um banco global: misturaria casos com dados sensíveis e dificultaria apagar um caso. |
| D4 | **Texto do registro guardado no SQLite** (`entry.msg` + `entry_body.cont`) e **FTS5 `unicode61` com conteúdo externo**. | Só offsets + leitura do arquivo original: no zip, cada `seek` refaz a descompressão desde o início (23–97 ms por acesso aleatório num membro de 50–167 MB, medido), inviável para rolar o grid. FTS5 `trigram`: índice 3,2× o arquivo e 3× mais lento que `unicode61` (medido); busca por substring sai via `LIKE`/`REGEXP` restrito pelos filtros (116 ms para 2,4 M registros, medido). FTS5 contentless: menor, mas sem `snippet()` e exige SQLite ≥ 3.43 para apagar linhas, e o Python de algumas distros Linux traz SQLite mais antigo. |
| D5 | **Unidade = registro** (cabeçalho + continuações), não a linha física. Linha física é recuperável por `line_no` + `nlines`. | Linha física como unidade: o Wfm tem 776 mil continuações em 913 mil linhas (dumps de mapa de até 1.646 linhas); filtros, templates e severidade valem para o registro inteiro. |
| D6 | **Templates declarativos em TOML por adapter** (`tomllib` é stdlib), com regex nomeadas sobre a 1ª linha da mensagem (após `strip()`), classe, severidade, campos-chave, regras de ciclo de vida e pares normal↔sensitive. | Templates em código Python: misturam dado e lógica e dificultam revisar/estender para novas famílias (os demais logs do ERS, U4). YAML: não é stdlib. Mineração automática pura (Drain etc.): útil para descobrir, mas não dá IDs estáveis nem semântica de início/fim. Fica como ferramenta auxiliar (seção 6.7). |
| D7 | **Severidade inferida do texto**, nunca do nível do envelope: nos adapters, tudo é `DEBUGLOG`, inclusive exceções e erros reais (fato de `docs/formato/`). | Usar o nível do envelope: classificaria 100% como debug. |
| D8 | **Eventos derivados persistidos** (tabela `event`), recalculados por stream a partir de um checkpoint. | Calcular na consulta: durações e pareamentos de centenas de milhares de operações a cada clique. |
| D9 | **Histogramas por tabela de buckets por minuto** preenchida na ingestão. | `GROUP BY` sobre `entry`: 300–414 ms em 2,4 M registros; com buckets, 14–18 ms (medido). |
| D10 | **Conhecimento em pacote JSON versionado no repo**, gerado do vault por ferramenta de dev, mais a configuração efetiva lida dos XMLs do próprio ERS. O pack deriva de documentação Schneider Electric: o repositório é privado e deve continuar privado (U1). | Ler o vault em runtime: o PC Windows não tem o vault. Copiar o vault inteiro: 7 MB de Markdown só no módulo 12, com conteúdo que não serve ao analisador. |
| D11 | **Distribuição como zipapp** (`dmslog.pyz`, um arquivo, roda em Windows e Linux). No Windows, pasta portátil com Python embeddable 3.13 + `dmslog.pyz` + `.bat` (o antivírus do PC de destino não bloqueia o `python.exe`, U2). No Linux, `python3 dmslog.pyz`. | PyInstaller/Nuitka: binário por SO, antivírus corporativo costuma bloquear executável não assinado, e o build fica mais complexo. Pacote instalável (pip): exige internet ou wheelhouse e permissões. Copiar `src/dmslog` solto (rascunho atual): funciona, mas o zipapp é um único arquivo, igual nos dois SOs, e `python -I dmslog.pyz` foi testado. |
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

ERS com nível elevado (medido em 2026-10-08, `docs/formato/payloads.md`, seção 8; contagem de bytes por script, sem indexar):

| Medida | Resultado |
|---|---|
| Escrita do log normal, fora × dentro da janela Info/Verbose | Wfm 1,2 → 32 MB/h; Ont 0,4 → 30 MB/h; Smn 0,3 → 37 MB/h. Só Info (Smn): 5 MB/h |
| Composição da janela elevada | 92% no log normal; rastreio `Communication2` = 46–62% das linhas Info/Verbose; payload ≈ 8% dos bytes (3–4 MB/h por adapter, no sensitive) |
| Escopo padrão, mesmo servidor (adapters normal + sensitive + BLTHost, com rotacionados) | ERS 12:40: 793 MiB; ERS 14:53 (Wfm/Ont/Smn elevados ~2 h): 1.041 MiB (+31%) |
| Banco estimado (× 1,59) | 1,26 GB e 1,65 GB; pior caso projetado (14 adapters em Verbose até o limite da rotação): logs ≈ 2,6 GB, banco ≈ 4,1 GB |

A meta de indexação do M6 parte desses números (ROADMAP, M6); o pior caso fica para o M8.

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
    payload.py           localiza payload XML/JSON no texto do registro (offsets, formato, raiz); seção 6.6
  families.toml          DADOS: padrão de caminho/nome → família, envelope esperado, escopo padrão (seção 9.1a)
  adapters/              DADOS: _common.toml, awfm.toml, aont.toml, aosr.toml, asmn.toml, aavl.toml,
                         asss.toml, acrm.toml, aami.toml, asmr.toml, awdi.toml, aclc.toml, afup.toml,
                         afmo.toml, blthost.toml, oaserr.toml, service.toml (dnaService/ProcessMonitor/arb*)
  derive/
    engine.py            roda derivadores por stream a partir do checkpoint
    operations.py        início/fim/duração/status de operação
    stats.py             blocos [Communication<n>] → stat_sample (acumulado e delta)
    lifecycle.py         startup, janelas de nível (toggleDebug), gaps, restarts, recarga de mapas, saltos de relógio
    payloads.py          liga payload ao evento/operação, direção request/response, chaves do payload
    correlate.py         normal↔sensitive, chaves (msgId, GID, INC…), callbacks, retries
  knowledge/
    pack/                DADOS gerados: manifest.json, adapters.json, error_codes.json,
                         operations.json, parameters.json
    curated/             DADOS escritos à mão: hints.toml (template → explicação, parâmetros ligados)
    pack.py              carrega o pacote
    installation.py      lê XMLs do ERS/instalação (Registry em camadas, WebServiceConfiguration, ErrorConfiguration)
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
  payload/
    pretty.py            formatação tolerante de XML/JSON para exibição (sem resolver entidades, aceita truncado)
  report/
    export.py            relatório .txt de uma seleção (eventos, bookmarks, trechos, payloads opcionais)
    redact.py            anonimização configurável na exportação (perfis por categoria + termos do usuário)
tools/                   só dev, fora do pacote: kb_build.py, make_fixture.py, mine_templates.py (a criar no P1), bench.py
tests/
  fixtures/              logs sintéticos pequenos (seção 13) e mini-vault para kb_build
  unit/  property/  integration/  perf/  samples/ (smoke opcional com local-samples, pulado no CI)
packaging/
  build_pyz.py           gera dist/dmslog.pyz (comum aos 2 SOs)
  build_portable.py      Windows: embeddable + dmslog.pyz + dmslog.bat
  dmslog.sh              Linux: launcher
```

## 5. Modelo de dados (SQLite por caso)

Arquivo: `<dados>/cases/<case_id>/index.sqlite` (seção 12). Pragmas: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `cache_size=-131072`, `temp_store=MEMORY`. Versão do esquema em `PRAGMA user_version` (hoje **3**: v2 = `bucket_sev`; v3 = `payload.reason` e `ix_payload_ref`; migrações aditivas em `store/db.py`). Caso = **uma coleta** (U5): um `collection_group` (o par Enterprise/Sensitive de um ERS, ou uma pasta de instalação). O esquema continua aceitando várias coletas por caso, mas a 1.0 recusa adicionar uma segunda (seção 10).

Tempo (U6): `ts` é **inteiro em ms** do horário local do log, **preservado como está** (sem fuso e sem conversão; o log não informa fuso). Por conveniência de cálculo ele é codificado como se fosse UTC, e por isso o front-end e a exportação formatam sempre com `timeZone: 'UTC'` / `getUTC*` (senão o navegador "converteria" para o fuso do PC). Premissas e efeitos:
- Todas as fontes de um caso vêm do mesmo servidor e do mesmo relógio; o merge por `ts` é válido sem ajuste.
- **Horário de verão**: o log não marca o salto. Na volta do relógio (ex.: 00:00 → 23:00), a mesma hora aparece duas vezes: dentro de uma fonte os registros seguem a ordem do arquivo, recebem `flags & 32` (ts fora de ordem) e o derivador `lifecycle` cria `event(kind='clock_jump')` quando o retrocesso passa de 30 min; no merge entre fontes, a hora repetida fica intercalada e a UI mostra o aviso do `clock_jump` sobre a timeline. No avanço (ex.: 00:00 → 01:00), aparece como lacuna de ~1 h em todas as fontes ao mesmo tempo; `gaps` marca `detail.possible_clock_jump` quando a lacuna é de 60 ± 1 min e simultânea em todos os streams. Operação que atravessa o salto recebe `detail.clock_jump=true` (duração não confiável). Nada é corrigido.
- `collection.clock_offset_ms` fica no esquema, sempre 0 na 1.0 (porta aberta para múltiplos ERS/máquinas, pós-1.0).

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
  clock_offset_ms INTEGER NOT NULL DEFAULT 0,                  -- sempre 0 na 1.0 (um ERS por caso); reservado
  added_at TEXT NOT NULL);

CREATE TABLE stream(                                           -- fluxo lógico: um processo (família+PID+sensitivity) numa coleta
  id INTEGER PRIMARY KEY,
  collection_group INTEGER NOT NULL,                           -- ers_group (ou collection.id); une normal e sensitive da mesma coleta
  family TEXT NOT NULL,                                        -- AWFM, AONT, ..., BLTHOST, OASERR, SERVICE, UNKNOWN
                                                               -- (aberto: famílias novas vêm de families.toml, seção 9.1a)
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
                                                               --       16 janela de nível elevado, 32 ts fora de ordem,
                                                               --       64 has_payload
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
  SELECT e.id AS rowid, e.msg AS msg, substr(b.cont, 1, 65536) AS cont   -- limite do FTS por registro (seção 6.6)
  FROM entry e LEFT JOIN entry_body b ON b.entry_id = e.id
  WHERE e.tpl_id NOT IN (SELECT id FROM template WHERE fts = 0);         -- rastreio Verbose fora do FTS (seção 6.6)
CREATE VIRTUAL TABLE entry_fts USING fts5(msg, cont, content='entry_text', content_rowid='rowid',
  tokenize="unicode61 remove_diacritics 2");

CREATE TABLE entry_key(                                        -- chaves de correlação extraídas pelos templates
  key_type TEXT NOT NULL,                                      -- msgid, request_id, gid, inc, mrid, errcode, exc_type, op, client_id
                                                               -- (também chaves extraídas de payload, seção 6.6)
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
  fts INTEGER NOT NULL DEFAULT 1,                              -- 0 = registros deste template não entram no FTS
  def_version TEXT NOT NULL);                                  -- hash do TOML que o definiu
CREATE TABLE unknown_sig(                                      -- registros sem template, agrupados por assinatura
  family TEXT NOT NULL, signature TEXT NOT NULL,
  n INTEGER NOT NULL, first_entry_id INTEGER NOT NULL,
  PRIMARY KEY(family, signature)) WITHOUT ROWID;

-- Eventos derivados ------------------------------------------------------------
CREATE TABLE event(
  id INTEGER PRIMARY KEY,
  stream_id INTEGER NOT NULL,
  kind TEXT NOT NULL,          -- operation|startup|stats_block|level_window|gap|restart|retry_seq|map_reload|exception|
                               -- sensitive_window|clock_jump|service_state (texto aberto: famílias novas podem criar kinds)
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

-- Payloads (formato descoberto em docs/formato/payloads.md; seção 6.6) ---------------------------
CREATE TABLE payload(          -- o texto NÃO é duplicado: offsets dentro de msg + '\n' + cont do registro
  id INTEGER PRIMARY KEY,
  entry_id INTEGER NOT NULL,   -- registro que contém o payload (sensitive, ou normal: Wfm Content being sent to CRS/WMS)
  event_id INTEGER,            -- operação a que pertence (preenchido por derive/payloads.py)
  event_conf INTEGER,          -- confiança da ligação ao evento: 3 chave, 2 TID dentro da operação, 1 tempo
  ref_entry_id INTEGER,        -- registro normal que anuncia o payload (GEN-MSG-01, WFM-112, ...), se houver
  pair_id INTEGER,             -- payload par (request <-> response/fault), se houver
  role TEXT NOT NULL,          -- request|response|fault|delta|content|publication|other
  direction TEXT NOT NULL,     -- in (recebido pelo adapter) | out (enviado pelo adapter)
  peer TEXT,                   -- outro lado, do TOML: WFM, SAP_PI, CRS, IMS, WMS, DMS_PUBSUB
  operation TEXT,              -- nome da operação (do evento, do template ou do elemento raiz)
  msg_id TEXT, corr_id TEXT,   -- Header/MessageID e Header/CorrelationID, quando existem
  fmt TEXT NOT NULL,           -- xml|xml_seq (vários elementos irmãos, sem raiz)|html|json|text
  root TEXT,                   -- 1º elemento, com prefixo (ex.: ns0:SwitchingStepsResponseMessage, s:Fault)
  char_off INTEGER NOT NULL, char_len INTEGER NOT NULL,   -- sem o '.' final que o adapter acrescenta
  well_formed INTEGER,         -- 1 ok, 0 malformado, NULL não verificado
  truncated INTEGER NOT NULL DEFAULT 0,
  reason TEXT);                -- v3: por que não há operação (órfão): no_message_id|no_operation_for_key|ambiguous|scan_limit|too_many_payloads|...
CREATE INDEX ix_payload_entry ON payload(entry_id);
CREATE INDEX ix_payload_ref   ON payload(ref_entry_id) WHERE ref_entry_id IS NOT NULL;   -- v3: payloads anunciados por um registro
CREATE INDEX ix_payload_event ON payload(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX ix_payload_op ON payload(operation, role);
CREATE INDEX ix_payload_msg ON payload(msg_id) WHERE msg_id IS NOT NULL;

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

CREATE TABLE bucket_sev(       -- rollup de bucket sem template (migração 2, `user_version` = 2)
  source_id INTEGER NOT NULL, minute INTEGER NOT NULL, sev INTEGER NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY(source_id, minute, sev)) WITHOUT ROWID;
CREATE INDEX bucket_sev_minute ON bucket_sev(minute, sev, n);   -- cobertura: histograma de todas as fontes lê em ordem
-- Mantida pelo writer junto de `bucket` (soma na inserção, subtrai na remoção da cauda/reindexação, desfeita no rollback).
-- A migração 2 cria a tabela e o índice e preenche a partir de `bucket`. `query.histogram` a usa (exata) quando o filtro não
-- usa `tpl`/`ts` e a divisão é `sev` ou `source`; divisão `tpl` continua em `bucket`. Medido (2 M registros, 20 fontes, 2 dias,
-- 10 min, 13 templates): 148 ms por `bucket` -> 16 ms por `bucket_sev`. Mudança aditiva decidida pela sessão principal (sem Opus).

-- Configuração efetiva da instalação (lida do ERS) -------------------------------
CREATE TABLE config_param(                                     -- uma linha por (camada, chave): todas as camadas ficam visíveis
  collection_group INTEGER NOT NULL, product TEXT NOT NULL,     -- AdapterWfm, ... (atributo Name de <Product>)
  scope TEXT NOT NULL,                                          -- registry | ws:<Operação> | err:<Interface>
  key_path TEXT NOT NULL DEFAULT '',                            -- '' ou nome do <Key> (lista: CallbackNotificationContactTypeIds)
  name TEXT NOT NULL, value TEXT, source_file TEXT NOT NULL,
  layer INTEGER NOT NULL,                                       -- -1 = AdapterX.xml; N = sufixo _N (seção 7.2)
  layer_label TEXT,                                             -- '' | Product | Custom | ... (texto após _N_)
  effective INTEGER NOT NULL,                                   -- 1 = valor que vale após a precedência
  status TEXT NOT NULL,                                         -- only|wins|overridden|ambiguous
  PRIMARY KEY(collection_group, product, scope, key_path, name, source_file)) WITHOUT ROWID;
CREATE TABLE config_observed(                                   -- valor que o processo registrou no startup
  stream_id INTEGER NOT NULL, name TEXT NOT NULL,               -- nome como aparece no log ([PathToWebServiceConfiguration])
  value TEXT, is_default INTEGER NOT NULL,                      -- 1 = linha "[Configuration] [Default] [X]: [v]"
  entry_id INTEGER NOT NULL,
  PRIMARY KEY(stream_id, name, entry_id)) WITHOUT ROWID;
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
- **Correlação entre fontes**: `entry_key` (mesmo GUID/GID/INC em qualquer fonte do caso) + `link`. Entre máquinas fica para depois da 1.0 (U5); o esquema já comporta (`collection_group`, `clock_offset_ms`).
- **Mudança de nível de log**: `event(kind='level_window')` a partir de `Debug level changed from X (n) to Y (m).` e `flags & 16` nos registros dentro da janela.
- **Payload**: `payload` aponta trechos do texto do registro (sem duplicar), com papel, direção, outro lado, IDs do header, par request↔response, formato e ligação ao evento (com confiança); registros com payload têm `flags & 64`. Formato descoberto nas amostras Verbose (seção 6.6).
- **Configuração em camadas**: `config_param` guarda todas as camadas e marca a efetiva; `config_observed` guarda o que o processo registrou no startup, para conferência (seção 7.2).
- **Demais logs do ERS**: nada no esquema é específico de adapter; família, kinds de evento e chaves são texto aberto, e os campos específicos ficam em `fields` (JSON) (seção 9.1a).
- **Chaves de `meta`** (controle de idempotência; todas recalculáveis, nunca fonte de verdade): `entries_epoch` (contador que o writer incrementa quando apaga registros; entra nas assinaturas abaixo, porque apagar registros apaga os links e payloads deles); `derive.<stream>.<cadeia>` (checkpoint, estado dos derivadores e assinatura por cadeia de rotação, 6.5); `correlate.<grupo>` (assinatura e estatísticas de pareamento normal↔sensitive), `correlate.links.<grupo>` (assinatura das regras `[[link_rule]]`) e `payloads.<grupo>` (assinatura da ligação de payloads), todas por collection group, seção 8; `payload_extraction_version.<source_id>` (versão da extração de payload com que a fonte foi indexada) e `payload_extraction_version` (marca global: todas as fontes indexadas estão na versão atual), seção 6.6.

Ordem global (merge): `ORDER BY ts, id`. Dentro de uma fonte o `id` respeita a ordem do arquivo; timestamps que voltam no tempo (até 16 ms nos logs normais e até 2,1 s no sensitive em nível elevado, `docs/formato/payloads.md`) recebem `flags & 32` e continuam na posição do arquivo dentro da fonte.

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
    # opcional: scan(buf, pos, endpos) -> Iterator[(offset, Header)]  (caminho rápido, finditer)

ENVELOPES: dict[str, Envelope]           # adms, oaserr, plain; register_envelope() acrescenta outros
def detect_envelope(head: bytes, *, max_lines: int = 50) -> Envelope
    # conta cabeçalhos reconhecidos nas primeiras 50 linhas; maior contagem vence
    # (empate: o registrado primeiro); nenhuma linha reconhecida → 'plain'
```

- Contrato de todo envelope: a decisão "é cabeçalho" depende só de um **prefixo** da linha (sem olhar o fim). Acrescentar bytes a uma linha nunca muda a decisão nem os campos. É isso que torna o agrupamento (6.2) seguro para pedaços e retomada.
- Nível numérico em `entry.level`, na ordem da alternância da regex: `FATAL`=1, `EXIT`=2, `ERROR`=3, `ALWAYS`=4, `DEBUGLOG`=5, `WARNING`=6, `INFO`=7, `VERBOSE`=8.
- `adms`: `^(?:\ufeff)?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d{3}) \[PID=(\d{1,10})\](?:\[TID=0x([0-9a-fA-F]{1,15})\])? (FATAL|EXIT|ERROR|ALWAYS|DEBUGLOG|WARNING|INFO|VERBOSE) - `. Aplicada em **bytes** (o decode só acontece no registro final). TID opcional: as linhas do logger nativo (`PSAttach::…`, `arb_pubSubLookup: …`) saem sem TID em nível Info (271 no ERS Verbose; 1 no ERS em DebugLog). Data impossível (ex.: mês 13) não é cabeçalho.
- `oaserr`: `^(?:\ufeff)?(ts)\.(ms) (?P<proc>\S+?) \[PID=(\d{1,10})\](?:\[TID=0x(hex)\])? - (?:\((LEVEL)\) )?`. **Nível e TID opcionais**; o token de processo é `\S+?` (não exige o sufixo `_<pid>`), e `process` = token sem o sufixo `_\d+` final, quando houver. A versão anterior (`(?P<proc>\S+)_(?P<pid>\d+) … \[TID=…\] - \((LEVEL)\) `, tudo obrigatório) deixava sem casar 25 de 63 linhas com data nos 6 `oasErrLog` das amostras.
- `plain`: cada linha é um registro sem `ts` (arquivos desconhecidos continuam navegáveis).
- **UTF-16 (decisão de tratamento)**: 67 dos 130 logs que a detecção classifica como `plain` nas amostras são UTF-16 com BOM (logs de instaladores); lidos como bytes, viram lixo com `\x00` a cada caractere. A leitura (tarefa 1.6) detecta o BOM UTF-16 (`FF FE`/`FE FF`) antes de `detect_envelope` e **transcodifica para UTF-8** em streaming (decodificador incremental da stdlib); o arquivo é tratado como `final` (sem retomada incremental: qualquer mudança reindexa o arquivo inteiro) e `byte_off`/`indexed_bytes` contam bytes do fluxo transcodificado. Enquanto a 1.6 não transcodifica, a fonte fica `status='skipped'` com o motivo em `error`; nunca é indexada como `plain` corrompido.
- O sufixo de fonte do BLTHost/serviço (` {E:\...\Main.cs:373}`) vira campo `src` e é removido do `msg` exibido apenas na coluna "mensagem" (o texto original continua no registro).
- Timestamp: cache por segundo (`AAAA-MM-DD HH:MM:SS` → ms base) e soma dos milissegundos. Evita `strptime` por linha.

### 6.2 Agrupamento em registros (seguro para retomada)

```python
@dataclass(slots=True)
class Record:
    byte_off: int; line_no: int; nlines: int
    ts_ms: int | None; pid: int | None; tid: int | None; level: int | None
    msg: str; cont: str | None; partial: bool
    nbytes: int = 0                  # tamanho bruto no arquivo, fim de linha incluído
    process: str | None = None       # do envelope (só oasErrLog)
    orphan: bool = False             # linhas antes do primeiro cabeçalho

class RecordGrouper:                 # parser "push": feed(chunk) -> list[Record]; finish(final=) -> list[Record]
    def __init__(self, envelope, *, start_off=0, start_line=1): ...
    pending_offset: int              # offset do que ainda não foi devolvido

def iter_records(stream: BinaryIO, env: Envelope, *, start_off: int = 0, start_line: int = 1,
                 final: bool, chunk_size: int = 1 << 20) -> Iterator[Record]
def parse_bytes(data, env, *, start_off=0, start_line=1, final=True) -> list[Record]   # testes/entradas pequenas
```

- Leitura em **pedaços de 1 MiB** (`chunk_size`), em bytes; só linhas completas são examinadas até `finish`. CRLF/LF aceitos (`\r` antes do `\n` pertence ao fim de linha), BOM só no início. Decode `utf-8` com `errors='replace'`.
- `iter_records` **não faz seek**: o stream já deve estar posicionado em `start_off` (início de registro, ou 0); `start_off`/`start_line` só servem para calcular offsets e números de linha.
- Registro = cabeçalho + linhas seguintes que não casam com o envelope. Linhas antes do primeiro cabeçalho formam um registro órfão (`orphan=True`, campos do envelope NULL, `sev` 5).
- **TAB no começo de `msg` não é continuação** (é cabeçalho com mensagem iniciada por TAB).
- Retomada: todos os registros, menos o último, são definitivos. O último sai com `partial=True` quando `final=False` (arquivo vivo) ou quando a entrada não termina em `\n`. Para retomar, o pipeline guarda `byte_off`/`line_no` desse último registro, descarta-o e volta a ler a partir desse offset. `final=True` (zip ou arquivo parado): o último registro só é `partial` se faltar o `\n` final.
- Propriedade garantida e testada: indexar o arquivo inteiro de uma vez produz os mesmos registros que indexá-lo em pedaços cortados em qualquer byte, e a concatenação dos registros reconstrói os bytes originais (seção 13).

### 6.3 Camada 2: templates declarativos por adapter

Um arquivo TOML por família em `src/dmslog/adapters/`. Referência completa do esquema (em inglês, junto dos arquivos): `src/dmslog/adapters/_schema.md`; exemplo mínimo com todos os recursos: `_example.toml` (não é carregado). O mapeamento processo → família fica só no `families.toml` (seção 9.1a); o TOML de templates não repete o processo. Exemplo (abreviado):

```toml
schema = 1
family = "AWFM"                                  # código de families.toml
extends = ["_common"]                            # GEN-*: estatística, conexões, Detected errors, SystemEventSender...

[[template]]
code = "WFM-010"
class = "start"
regex = '''^(?P<op>[A-Z][\w ]+?) request received\. Request with ID "(?P<request_id>[0-9a-fA-F-]{36})"\. Message content w\w+ in sensitive log\.$'''
fields = { request_id = "guid" }                 # tipos: str|int|hex|guid|float|list
keys = ["request_id"]                            # ou { field = "message_id", type = "msgid" }
refs_sensitive = true
examples = ['Changed crews request received. Request with ID "0b5c…". Message content written in sensitive log.']

[[template]]                                     # texto fixo: busca exata, antes de qualquer regex
code = "WFM-030"
class = "req_resp"
text = "Callback received:"
cont = { parser = "line_regex", regex = '''^(?P<cb_type>\w+) GID: (?P<gid>\d+), Errors count: (?P<errors>\d+)\.''' }
fields = { gid = "int", errors = "int" }
keys = ["gid"]

[[template]]
code = "WFM-081"
class = "error"
sev = 2
regex = '''^ERROR - Processing "(?P<event_type>\w+)" message failed\. ExceptionType: "(?P<exc_type>[^"]+)" Reason: (?P<reason>.*)$'''
cont = "dotnet_stack"                            # frame, build e inner_type das continuações
keys = [{ field = "exc_type", type = "exc_type" }]

[[template]]                                     # anúncio no normal (comum; aqui só para ilustrar)
code = "GEN-MSG-01"
aliases = ["WFM-100", "ONT-100"]                 # IDs antigos continuam resolvíveis
class = "req_resp"
regex = '''^Message with MessageID: '(?P<msgid>[^']*)' will be logged in sensitive log\.$'''
keys = ["msgid"]
refs_sensitive = true

[[template]]                                     # payload no sensitive (seção 6.6)
code = "GEN-PLD-01"
class = "req_resp"
stream = "sensitive"                             # normal | sensitive | any (padrão)
regex = '''^Message(?: content)?: (?=<)'''
payload = { fmt = "xml", start = "match_end", strip_suffix = ".", role = "by_root" }

[[template]]                                     # rastreio Verbose: ruído, fora do FTS
code = "GEN-C2T-02"
class = "other"
sev = 5
fts = false
regex = '''^\[Communication2\] - TCP client connection id = (?P<conn_id>\d+) (?P<dir>sending|receiving) message msgId = (?P<c2_msgid>\d+), msgType = (?P<msg_type>\w+), msgSize = (?P<size>\d+)\.$'''

[payload_defaults]                               # por família: papel → direção e outro lado
request = { direction = "in", peer = "WFM" }     # Ont/Smn: request = out, peer = "SAP_PI"
response = { direction = "out", peer = "WFM" }
fault = { direction = "out", peer = "WFM" }

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
class TemplateDef:                                    # parse/templates.py
    code: str; family: str; cls: str; sev: int | None
    regex: re.Pattern[str] | None; text: str | None   # exatamente um dos dois
    keys: tuple[KeySpec, ...]; refs_sensitive: bool; literal_prefix: str | None
    fts: bool = True; payload: PayloadSpec | None = None
    aliases: tuple[str, ...] = (); stream: str = "any"; cont: ContSpec | None = None
    field_types: Mapping[str, str]; sev_field: str | None; sev_map: Mapping[str, int] | None
    examples / counter_examples; source: str; def_version: str  # sha1 (12 hex) do arquivo

@dataclass(frozen=True, slots=True)
class PayloadSpec:
    fmt: str                                          # xml | xml_seq | html | json | text
    start: str                                        # match_end | msg | first_cont_lt (1ª continuação que começa com '<')
    strip_suffix: str | None                          # "." nos request/response
    role: str                                         # request|response|fault|delta|content|publication|other|by_root
    direction: str | None = None; peer: str | None = None   # senão vem de [payload_defaults] da família

@dataclass(slots=True)
class TemplateMatch:                                  # resultado de classify()
    tpl_id: int; code: str; cls: str; sev_hint: int | None; fts: bool; payload: PayloadSpec | None
    fields: dict[str, Any]                            # tipados; vão para entry.fields (JSON)
    keys: list[tuple[str, str]]                       # (key_type, valor) para entry_key
    refs_sensitive: bool; match_end: int              # offset no msg original (não no strip)

class TemplateSet:                                    # uma família × tipo de stream (normal/sensitive)
    def classify(self, record) -> TemplateMatch | None        # record.msg/.cont; nunca lança
    def classify_text(self, msg, cont=None) -> TemplateMatch | None
    def operations(self) / sensitive_pairs(self)              # regras com códigos canônicos
    payload_defaults: Mapping[str, Mapping[str, str]]

class Catalog:
    def for_family(self, family, *, sensitive: bool) -> TemplateSet   # sem arquivo → _common
    def resolve(self, code_or_alias) -> str | None; def template(code) -> TemplateDef
    def tpl_id(self, code) -> int; def bind_ids(self, {code: template.id})  # ids do banco do caso
    def check_examples(self) -> list[str]

def load_catalog(*, check_examples=False) -> Catalog  # adapters/*.toml via importlib.resources (menos _example.toml)
def load_dir(dir) / load_texts({nome: texto})         # testes e ferramentas
# erros tipados (TemplateError): SchemaError, DuplicateError, InvalidRegexError, UnsafeRegexError,
# TemplateReferenceError, ExampleError; mensagem com arquivo e código do template
```

- Prioridade: textos exatos (`text`) primeiro, por dicionário; depois as regex na ordem da família e de cada biblioteca de `extends` (bibliotecas = famílias com `_`, ex.: `_common`; não estendem outras). A primeira que casa vence. Templates casam o `msg` completo, sem espaços/TABs nas pontas, e só se ele **couber na janela** do template: no máximo 4.096 caracteres (padrão e máximo) ou menos, se o template declara `window = N` (128 a 4.096); um texto maior não casa esse template. Registros nunca têm quebra de linha. Continuações: o limite de 256 KiB descarta a linha que ele corta (nenhum parser vê linha parcial).
- Despacho: chave = primeiros K caracteres do `msg` (K escolhido por conjunto); cada template entra pelo prefixo literal da regex (ou um por alternativa de uma alternação inicial, `(?:Creating new|New dal) proxy`), e os sem prefixo são filtrados pelo 1º caractere possível e pelo maior literal obrigatório (`needle`, teste com `in`). A lista de candidatos por chave é memorizada (até 8.192 chaves). Medido (1 M de registros sintéticos, 57 templates): classify 1,25 µs/registro (0,8 M/s), contra 2,3 µs do parse do M1 e 9,2 µs da ingestão completa do M1 (+14% na ingestão). Uma propriedade testada garante que o despacho dá o mesmo resultado da varredura linear.
- Segurança das regex (na carga, `UnsafeRegexError`; regras completas em `adapters/_schema.md`, "Regex safety"). **Modelo de ameaça:** as regex são conteúdo confiável do catálogo, escrito pelos mantenedores; o dado não confiável é o texto do log. O objetivo é impedir que uma regex patológica por descuido congele a ingestão, não defender contra um autor hostil. **Estratégia (decisão final após 5 rodadas de revisão): limitar o dano por construção**, não provar ausência de explosão por análise fina de caminhos. Cada rodada achou um canto novo da análise (alternativas, lookarounds, classes); por isso a regra passou a ser um **orçamento de trabalho em passos numa janela conhecida**, com um modelo de custo simples e conservador. **Regras:** (1) lista branca (literais, classes, âncoras, grupos sem flags, alternação, repetições gulosas/preguiçosas com limites até 64, lookarounds; nada de flags, possessivos, grupos atômicos, retrorreferências, condicionais); (2) repetição que itera mais de uma vez só repete tokens de um caractere; (3) cota de trabalho: `work(janela) ≤ MAX_REGEX_STEPS` (2²¹ ≈ 2,1 M passos). `work` é um polinômio em N = n+1: em sequência `work(PR) = work(P) + out(P)·work(R)`, onde `out(P)` conta as saídas de P cujo próximo caractere R aceita (conjuntos **exatos** de code points; `\d \w \s` calculados com o próprio `re`); alternativas **sempre somam** seu trabalho (cada uma pode ser tentada) e só o `out` usa máximo quando nenhuma começa pelo mesmo caractere; repetição de token lê ≤ N caracteres e tem ≤ N contagens; um lookaround custa o trabalho inteiro do corpo a cada visita e conta como "pode começar por qualquer caractere", de modo que nenhuma escolha antes dele (em sequência ou entre alternativas) é descontada pela discriminação (achado da rodada 5); uma cauda que não pode falhar (`.*$` guloso, texto de uma linha) é paga uma vez por casamento. **Janela por template:** padrão 4.096; um template cuja regex tem pior caso quadrático declara `window = N` menor, e a cota é avaliada nessa janela. No catálogo, 19 dos 335 templates declaram janela (lista gerada dos TOML): 2320 (SMN-S016), 1008 (FMO-002, GEN-C2L-01, GEN-CCH-02, GEN-MAP-02, SMR-004), 992 (SMN-007, SMN-008, WFM-010), 704 (CLC-S01, FMO-001, GEN-C2O-02, GEN-SUB-05), 576 (BLT-001, BLT-005), 400 (GEN-STU-06), 352 (BLT-022), 320 (FUP-S02, SVC-017). **Linha maior que a janela (rodada 7, regra exata):** um template só se aplica a mensagens que **cabem** na sua janela: `len(msg sem espaços) > janela` ⇒ o template não casa e o registro segue para os candidatos seguintes (ou fica desconhecido). A regex nunca vê texto cortado (o corte dava resultado errado em construções gulosas, p. ex. `^(?P<x>.*)b` em 129 caracteres), então não há tratamento especial de `$`, lookahead ou borda; uma mensagem com exatamente `janela` caracteres casa normalmente. Consequência documentada: um template de prefixo também não casa uma mensagem longa. Medido nas amostras reais: nenhum registro a mais ficou sem template. **Limites por lista branca (rodada 7):** o modelo não conta fatores constantes, então certas construções são limitadas em vez de modeladas: classe de caracteres com no máximo 32 itens (faixas, literais e categorias contam 1 cada), grupos de captura aninhados até profundidade 3 e no máximo 24 grupos por regex (o catálogo precisa de 4 itens, profundidade 2 e 8 grupos; nenhum template foi reescrito). **Garantia (honesta):** limitado é o número de passos de **um** `re.match` de uma regex do catálogo sobre o `msg` sem espaços nas pontas, que cabe na janela (texto maior não casa o template): ≤ 2,1 M passos contados, qualquer que seja o grau do polinômio: o grau só decide que janela cabe (grau 1 em 4.096, grau 2 entre 320 e 2.320, grau 3 só em 128). **Calibração empírica (rodadas 6 e 7):** o pior custo observado foi ~19 ns por passo contado (classes com muitas faixas e capturas profundas: 70 a 110 ms), contra 0,8 a 5 ns no catálogo e no fuzz; essas formas agora são recusadas pelos limites acima. Medição **local**: as formas aceitas no limite custam ~20 a 40 ms (um runner de CI chegou a medir 87 ms numa forma artificial). Formas aceitas e artificiais podem custar mais por passo que a média; o orçamento inclui essa folga de segurança, não é uma prova. Os testes não dependem da velocidade do runner: só falham num limiar de catástrofe de 0,25 s por sonda (tolera um runner 5x mais lento). Medido localmente: regex do catálogo na própria janela ≤ 4 ms; fuzz com ~1.000 regex aceitas (de 2.500 geradas) com alternações de lookarounds, lookarounds aninhados, alternações em opcionais, Unicode, preguiçosos e repetições limitadas encadeadas, pior medido ~3 a 20 ms conforme a semente. O tempo por linha é esse limite vezes o número de candidatos tentados. **Não coberto:** `re.search`, texto sem `strip` ou com quebra de linha, regex fora do carregador do catálogo, e a velocidade de outra máquina (o limite é em passos; o tempo é medido). O modelo superestima (há regex seguras recusadas ou que precisam de janela menor); a afirmação antiga "grau ≤ 1 por registro" valia para caminhos, não para tempo, e foi retirada (`^a*(?!a*$)b$` é quadrática: recusada na janela de 4.096 e aceita, por exemplo, com 720). **Defesa em profundidade:** sonda de ataque de toda regex do catálogo na própria janela, num processo filho com timeout (igual no Windows), mais a tabela de achados R1 a R5 (recusados em toda janela ou aceitos só numa janela pequena, e então medidos), e o fuzz com tamanho crescente (passo de 2 caracteres até 32, depois ×1,25) que aborta na 1ª sonda > 0,25 s, para nada travar. Sonda dinâmica na carga e contabilidade de custo em tempo de execução **não** foram adotadas (catálogo empacotado e estático; resultado não pode depender de tempo). Regex que gera aviso de compilação é recusada.
- Campos: grupos nomeados, com tipo opcional (`int`, `hex`, `guid` em minúsculas, `float`, `list` separada por vírgula); valor que não converte fica texto; número com mais de 18 dígitos fica texto. `keys` copia campos (inclusive listas, um por item) para `entry_key`. `sev_field` + `sev_map` dão o `sev_hint` a partir de um campo (ex.: `[Validation Critical]` do ClaimCheck); a decisão final continua na 6.4.
- Templates só olham a 1ª linha (`msg`). Para templates multilinha, um `cont_parser` nomeado extrai campos das continuações (lê no máximo 256 KiB; campo da regex vence): `codes_per_line` (`codes`), `stats_csv` (`rows`, `n_rows`), `dotnet_stack` (`exc_type`, `exc_msg`, `frame`, `build`, `inner_type`), `kv_tab` (dump de mapa: só conta `items`), `kv_colon`/`kv_dash` (`kv`, para no 1º `<`) e `line_regex` (regex validada como as outras, numa linha dada da continuação). A lista é fechada: formato novo = parser novo em Python, nunca código no TOML.
- `[[operation]]` e `[[sensitive_pair]]` ficam livres para os derivadores; a carga só confere que os códigos citados (`start`, `end`, `end_ok`, `end_fail`, `fail`, `normal`, `sensitive`, `codes`) existem para a família, e troca aliases pelo código canônico.
- `tpl_id`: provisório (1..N na ordem dos códigos) até o writer chamar `bind_ids` com os ids da tabela `template` do caso.
- Registro sem template recebe `tpl_id=0` e entra em `unknown_sig` com a assinatura de `parse/signature.py` (seção 6.7).
- Famílias já analisadas nas amostras (19 arquivos, cobertura de 100% medida pelos analistas): AVL, Osr, Smn, SiteSwitch, Wfm, Ont, Crm, Ami, Smr, WDI, ClaimCheck, FileUpload, FileMonitor, BLTHost. A **1.0 inclui também `CustomAdapterChangeIncidentDevice` com templates e eventos específicos** (U11). Sua base de conhecimento ainda será criada por Jefferson no `adms-v3.9` ([issue #22](the ADMS knowledge-base repository)); formato, identificador da família e regras de análise serão definidos a partir das fontes e amostras, sem extrapolar a cobertura já medida. Até essa implementação, seu log pode ser indexado provisoriamente como `UNKNOWN`, preservando o processo, com envelope + `_common` + assinaturas desconhecidas. Essa pendência não bloqueia M0, mas base e análise específica são obrigatórias para a release 1.0. Os demais adapters do vault sem amostra continuam fora do escopo (U8). Os IDs de `docs/formato/` viram os `code` dos templates. Os IDs genéricos dos três documentos são unificados em `_common` (`COM-*` e `WFM-090`/`ONT-030` = `GEN-STA-*`; `WFM-020..022` = `GEN-CON-*`; `WFM-050..052` = `GEN-ERR-*`/`GEN-EVT-01`), mantendo o código antigo como alias no TOML.

### 6.4 Camada 3a: severidade

```python
def infer_severity(match: TemplateMatch | None, record: Record,
                   codes: CodeResolver | None = None) -> int
```

Implementado em `parse/severity.py` (`generic_severity` continua exportado: passos 1, 4 e 6 sem template). O `sev_field`/`sev_map` do template é resolvido pelo motor em `TemplateMatch.sev_hint`. O nível do envelope só conta como severidade em FATAL/EXIT/ERROR e no prefixo `(ERROR)`; `DEBUGLOG`/`ALWAYS`/`INFO` não indicam gravidade e `VERBOSE` sem template vale 5. Um código resolvido (passo 3) só pode escalar o `sev` declarado pelo template, nunca rebaixá-lo.

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

**Como construído (M4): a unidade de derivação é a cadeia de rotação**, não o stream. Um stream pode reunir arquivos independentes (o BLTHost tem `BLTHost.Common.log`, `BLTHost.Arbitration.log`, ...); a cadeia agrupa as fontes cujo `file_name` é igual depois de remover o `.N` final (`X.log`, `X.log.0`, ...). Cada cadeia tem derivadores novos, checkpoint, assinatura e estado de derivadores com estado (estatísticas) em `meta['derive.<stream>.<cadeia>']`; os eventos mantêm o `stream_id` e ganham `detail.chain` só quando o stream tem mais de uma cadeia. `stream.derive_checkpoint` é o mínimo entre as cadeias (a ingestão o reduz ao apagar registros, e o motor lê isso como reinício).

Dentro da cadeia a ordem é da fonte mais antiga (`.log.N` com maior `N`) até `.log.0` e depois o `.log` atual, e por `entry.id` dentro da fonte; o checkpoint só tem sentido se os ids crescem nessa ordem, a ingestão adiciona as fontes da cadeia na ordem certa e o motor confere (se violada, deriva a cadeia inteira a cada mudança). A derivação incremental, por cadeia, apaga os eventos com `start_entry_id >= checkpoint`, reprocessa os registros dali em diante e grava o novo checkpoint = menor `low_watermark()` dos derivadores (ou último `id` + 1 se nada está aberto), tudo numa transação (SAVEPOINT por stream). Sem mudança nas fontes, regras e definições de template (assinatura igual), nada é escrito. Assim a indexação incremental refaz só a cauda. Ao fim, `derive_pending` roda o pós-passe de `possible_clock_jump` (lacuna de 1 h simultânea em todas as cadeias da coleta).

| Derivador | Regra (vinda do TOML e de `docs/formato/`) | Evento |
|---|---|---|
| `operations` | início/fim por `(op, tid)` no stream, conferido pela chave (`request_id`, `msgId`, GUID); `fail` dentro da operação aberta; fim `failed`/`unsuccessfully` → `fail`; `Detected errors` + `SystemEventSender` dentro → `business_error`; sem fim no EOF → `open` (ou `truncated` se o arquivo foi cortado pelo ERS); duração = `ts_end - ts_start` (resolução real de ~15,6 ms). Registros sem chave na mesma TID entre início e fim recebem `entry.event_id`. | `operation` |
| `retries` | tentativas com o mesmo `msgId` (`Error: Sending ... failed` do Smn/Ont), contando tentativas e intervalo; liga à operação. | `retry_seq` |
| `startup` | do primeiro `[Configuration]`/`[ServiceUrl]`/`=====` até 5 s após `Server state changed. New state: HOT` (ou 120 s). Coleta porta, URLs, `[Configuration]` com defaults (as linhas `[Configuration] [Nome]: [valor]. Type: [ConfigurationRegistry]` e `[Configuration] [Default] [Nome]: [valor]` também alimentam `config_observed`, preenchida na ingestão pelos campos do template do `_common`, sem depender deste derivador; seção 7.2.1). Registros recebem `flags & 8` e são colapsados no grid. | `startup` |
| `stats` | bloco `[Communication<n>]`: totais e CSV das 3 seções → `stat_sample` (acumulado e delta). Queda em qualquer total → `restart`. | `stats_block`, `restart` |
| `level` | `Debug level changed from X (n) to Y (m).` → janela até a próxima mudança; nível configurado (`LogLevel` do Registry) é o estado inicial. | `level_window` |
| `gaps` | intervalo sem registros maior que o batimento esperado da família (estatística horária: > 61 min; SiteSwitch: > 5 s; parâmetro no TOML), só dentro do período coberto pelo arquivo. Lacuna de 60 ± 1 min simultânea em todos os streams → `detail.possible_clock_jump` (seção 5, Tempo). | `gap` |
| `clock` | retrocesso de `ts` > 30 min dentro de um stream (provável fim de horário de verão); marca operações que atravessam o salto. | `clock_jump` |
| `maps` | `"X" map closed` → `New "X" map content:` (+ itens) → `map is now open`. | `map_reload` |
| `exceptions` | agrupa exceções pela 1ª linha (tipo + mensagem normalizada), não pelos frames; versão do build sai do caminho `F:\PD\<build>\`. | `exception` (um por ocorrência; agrupamento na consulta) |
| `service` | `ProcessMonitor` (processo morto/reiniciado), `dnaService` (start/stop do serviço), `arb*` (transições HOT/STANDBY). | `restart`, `service_state` |
| `payloads` | liga cada `payload` ao anúncio, ao par e à operação pelas regras da seção 6.6 (chave do header, TID dentro da operação aberta, ou fim da operação ±16 ms). A extração de chaves do payload para `entry_key` **não foi construída** (fica fora da 0.1.0; as chaves do header já estão em `payload.msg_id`/`corr_id`). Roda depois de `operations` e da correlação, por collection group (seção 6.6, "Como construído"). | — (preenche `payload.event_id`, `event_conf`, `ref_entry_id`, `pair_id`, `reason`) |

### 6.6 Payloads (request/response)

Requisito da 1.0 (U7). Formato descoberto nas amostras Verbose de 2026-10-08 (tarefa 6.1): fatos, contagens e catálogo em `docs/formato/payloads.md`. Resumo do que vale para o desenho:
- **Quando**: o payload é gravado se o nível **corrente** ≥ `MessageLogLevel` da operação (Info no ERS). O nível corrente vem das janelas `level_window` (linhas `Debug level changed`), não só do `LogLevel` configurado. Verbose não acrescenta payload, só rastreio.
- **Onde**: um payload = um registro inteiro, nunca truncado pelo adapter, nunca intercalado. Request, response e fault de Wfm/Ont/Smn, delta ao CRS, conteúdo ao IMS e publicações do Smn ficam no **sensitive**, em INFO; o conteúdo do Wfm ao CRS/WMS fica no **log normal**. Cada payload do sensitive tem um registro de anúncio no normal (GEN-MSG-01, WFM-112, WFM-121, SMN-110) na mesma TID e no mesmo ms.
- **Formato**: só XML do corpo da mensagem (sem envelope SOAP; `s:Fault` solto), indentado com 2 espaços, UTF-8, CRLF; texto de elemento pode ter quebras de linha (continuação sem `<`); request/response terminam com `.` colado ao fechamento da raiz; publicações do Smn são uma sequência de `ResourceDescription` sem raiz (`xml_seq`); um corpo HTML de erro HTTP aparece dentro de mensagem de falha (`html`). Nenhum JSON na amostra; `json` fica no esquema para adapters REST futuros.

Desenho:
- `parse/payload.py`, na ingestão, só para registros cujo template declara `payload` (sem heurística em registros sem template): o início vem de `start` (`match_end` = logo após o match da regex no `msg`; `msg` = o `msg` inteiro; `first_cont_lt` = 1ª continuação que começa com `<`); o fim é o fim do registro, menos `strip_suffix`. Grava offsets, `fmt`, `root`, `msg_id`/`corr_id` (primeiros `Header/MessageID` e `Header/CorrelationID`, por regex barata, sem parser), `role` (`by_root`: raiz terminada em `ResponseMessage` → response, `Fault` → fault, senão request) e `direction`/`peer` de `[payload_defaults]` da família. `well_formed` é verificado sob demanda, não na ingestão (custo); `truncated` = registro `partial`. Não copia o texto.
- `derive/payloads.py` (depois de `operations`, por coleta), em ordem de confiança:
  1. anúncio: registro normal do template anunciante, mesmo PID e TID, Δt mínimo ≤ 1 s, conferido pelo MessageID quando não vazio → `ref_entry_id` + `link(normal_sensitive)`;
  2. request → operação: `msg_id` = chave da operação (`request_id` do Wfm, MessageId de ONT-011/SMN-007) → `event_conf=3`;
  3. response → par: `corr_id` = `msg_id` de um request (Wfm) → `pair_id`, evento do request, `event_conf=3`;
  4. response sem IDs (Ont/Smn): operação do mesmo tipo (`<Noun>EventMessageType` ↔ `<Noun>ResponseMessage`) cujo fim está a ≤ 16 ms; candidato único → `event_conf=1`; mais de um → órfão com motivo `ambiguous`;
  5. fault sem IDs (Wfm): falha WFM-081 no mesmo ms → `event_conf=1`;
  6. payload em registro do normal ou anunciado por WFM-112/121 dentro de operação aberta na mesma TID → `event_conf=2`;
  7. resto (publicações do Smn, janelas antigas do sensitive fora do normal): sem evento, motivo registrado.
  Nunca parear request/response por TID ou ordem: a thread de logging difere da thread da operação.
- `payload/pretty.py` formata sob demanda (na API, nunca na ingestão): XML por tokenizador tolerante próprio (sem DTD, sem resolver entidades, aceita truncado e `xml_seq`, preserva texto literal); HTML exibido cru; JSON por `json.loads` com limite de tamanho. Se não formatar, devolve o texto cru com o motivo.
- Busca: o payload já está no `cont` e entra no FTS; a view `entry_text` indexa até 65.536 caracteres de `cont` por registro (2 registros acima disso na amostra). Templates com `fts = false` (rastreio `Communication2`, ModeTracker, PSAttach, proxies, filtros: sev 5) ficam fora do FTS; continuam no grid e na busca por `LIKE`/regex com filtro de fonte ou tempo.
- Configuração: o painel "Effective configuration" mostra, por operação, se o payload **deveria** aparecer pela configuração (`LogLevel` × `MessageLogLevel`, ordem DebugLog < Warning < Info < Verbose; `None` = nunca) e, no tempo, em quais janelas `level_window` ele é esperado. Operação dentro de janela ≥ `MessageLogLevel` sem payload ligado vira aviso; fora dela, a UI explica o motivo.
- Dados sensíveis: payload é tratado sempre como sensível (nomes, endereços, textos livres, placas, INC/PROB, GIDs). O conteúdo ao CRS/WMS do Wfm está no log **normal**, e o nível Info também expõe no normal chaves de mapa (`entry added to the map`) e URLs/hosts; a anonimização da exportação (seção 12) vale para esses templates também.

**Como construído (M6) e o que difere do desenho acima:**
- **Candidato único.** A ligação nunca escolhe entre candidatos: a regra 3 (response → request por `corr_id`) só pareia quando há **um** request; vários requests com o mesmo `msg_id` deixam a response sem par (sem escolher por ordem). Nas regras 4 e 5 (por tempo) só um candidato liga; dois ou mais viram órfão `ambiguous`. A busca por candidatos examina no máximo `MAX_SCAN` (256) operações por payload; se a unicidade não pode ser provada dentro desse limite, o payload fica sem evento com motivo **`scan_limit`** (em vez de ligar ao primeiro encontrado).
- **Motivos de órfão** (`payload.reason`, esquema v3; também contados em `PayloadSummary.orphans`): `no_message_id`, `no_operation_for_key`, `no_request_for_correlation_id`, `no_operation_ending_nearby`, `no_failed_operation_nearby`, `ambiguous`, `not_in_operation`, `scan_limit` e **`too_many_payloads`** (processo com mais de `MAX_PAYLOADS` = 300.000 payloads: o excedente não é ligado, para limitar a memória). Os limites `MAX_OPS` (1.000.000) e `MAX_ANNOUNCERS` (500.000) por processo descartam candidatos (contados em `dropped`).
- **Execução** como a de `derive/links.py`: uma unidade por collection group, assinatura em `meta['payloads.<grupo>']` (fontes dos streams, eventos, `entries_epoch`, linhas de payload, templates e mapa de anúncios), tudo num SAVEPOINT; grupo inalterado não escreve nada. Os links `normal_sensitive` de anúncio antigos (de payload cujo anúncio mudou) são removidos na recomputação.
- **Reextração única.** Casos indexados antes do M6 (ou por uma extração mais antiga) têm registros sem linhas de `payload`, e fontes inalteradas não são relidas. A versão da extração (`PAYLOAD_EXTRACTION_VERSION`, hoje 1) é registrada **por fonte** em `meta['payload_extraction_version.<source_id>']`; uma fonte selecionada com marca menor (ou sem marca) é reindexada uma vez, e a marca global `payload_extraction_version` só diz que todas as fontes indexadas estão atuais.
- **Formatador** (`payload/pretty.py`): tokenizadores lineares e iterativos, com orçamento de saída; `check_xml` com sintaxe estrita de tag (teste diferencial contra o expat). Falha de formatação nunca é erro HTTP: volta `ok=false` com motivo.
- **Flag 64** (`has_payload`) só quando a linha de `payload` foi de fato extraída.
- Fora do M6: chaves de payload em `entry_key`, números de linha no viewer, testes de DOM.

### 6.7 Mineração auxiliar de templates

`parse/signature.py`: `signature(msg) -> str` normaliza a 1ª linha para agrupar registros sem template (`unknown_sig`, painel de desconhecidos, mineração). Ordem: espaços colapsados; timestamp → `<ts>`, GUID (com ou sem chaves) → `<guid>`, URL → `<url>`, caminho Windows/UNC/POSIX → `<path>`, IP → `<ip>`, `0x…` e hex longo com dígito → `<hex>`, texto entre aspas simples ou duplas → `<s>`, números (inclusive decimais) → `<n>`. Lê no máximo 2.048 caracteres e devolve no máximo 240. Pura, determinística e idempotente (`signature(signature(x)) == signature(x)`, testado por propriedade): nenhum marcador tem dígito, aspas ou barra. ~0,4 µs por registro.

`tools/mine_templates.py` (dev) agruparia registros desconhecidos de um ERS por essa assinatura e proporia esboço de TOML com exemplos. **Ainda não existe** (decisão de 2026-10-11): será criada no P1, para a inclusão dos demais logs do ERS (seção 9.1a); a descoberta Verbose do M6.1 foi feita por scripts descartáveis. Até lá, o fluxo é `dmslog ingest` mais `unknown_sig` / `GET /api/unknown` (ver `docs/FAMILIA-NOVA.md`).

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

O pack é commitado (U1). Ele é derivado de documentação Schneider Electric: o repositório é privado e precisa continuar privado, assim como os artefatos que embutem o pack (`dmslog.pyz`, pasta portátil, tarball). Não publicar release pública nem tornar o repositório público sem remover o pack. `manifest.json` leva o campo `notice` com esse aviso.

### 7.2 Da instalação (por caso, na ingestão)

`knowledge/installation.py` lê do ERS ou da instalação (sempre por `xml.etree.ElementTree`, sem resolver entidades externas, com limite de tamanho por arquivo):
- `OASyS/Servers/config/Registry/<Produto>*.xml`: `LogLevel`, `LogFileName`, `WebServiceConfiguration`, `<Op>_ErrorConfigurationPath`, parâmetros de tempo. Resolução em camadas pela regra abaixo (7.2.1).
- O `WebServiceConfiguration` **efetivo** (ex.: `DMSCustomIntegrationPROD`, não o `DMSIntegration`): `MessageLogLevel`, `AuditingPeriod`, `NumOfRetries`, `RetryTimeout` por operação.
- `*_ErrorConfiguration.xml` efetivos: `Adms/Id`, `Adms/Severity`, `ExternalSystem/Code|Severity|Reason|Details`, `ErrorSeverityEventingThreshold`.
- Caminhos: `%OASINSTALLDATADIR%` mapeia para a raiz `SchneiderElectricData` da coleta. Os XMLs usam as duas formas (`%OASINSTALLDATADIR%\ADMS\...` e `%OASINSTALLDATADIR%ADMS\...`) e o próprio adapter registra `C:\SchneiderElectricData\\ADMS\...`: normalizar inserindo o separador que falta e colapsando separadores repetidos antes de procurar o arquivo na coleta.

#### 7.2.1 Regra de precedência do Registry (U9)

Validada no ERS de exemplo (leitura por script, `python3 -I`): 96 arquivos em `Registry/`; todos os do escopo usam `<OASYSDNARegistry><Product Name="..."><Value Name="...">` e, em alguns (Crm, Osr), `<Key Name="..."><Value Name="..."/></Key>` (listas).

1. **Agrupamento por produto**: os arquivos se agrupam pelo atributo `Product/@Name`, não pelo nome do arquivo. Nome do arquivo = `<Produto>[_<N>[_<rótulo>]].xml`; no exemplo: `AdapterOnt.xml`, `AdapterOnt_1_Custom.xml`, `AdapterOnt_2.xml`, `AdapterSiteSwitch_0_Product.xml`. Arquivo de outro produto (ex.: `CustomAdapterChangeIncidentDevice*.xml`) é resolvido à parte.
2. **Cada arquivo é uma camada separada do mesmo processo**; nenhuma camada substitui outra por inteiro. `AdapterX_1_<Projeto>` é definido no escopo do projeto com base em `AdapterX` e distribuído nos pacotes de instalação de atualização MSI/MSU específicos desse projeto ADMS. O sufixo de projeto não identifica uma instância independente. `layer` = -1 para o arquivo sem sufixo, `N` para `_N`. Ordem: `AdapterX.xml` (-1) < `AdapterX_0_Product` (0) < `AdapterX_1_<Projeto>` (1) < `AdapterX_2` (2).
3. **Chave só em uma camada** vale como está (`status='only'`), com a camada de origem visível na UI. É o caso da maioria das chaves dos `_1_<Projeto>`: são o contexto do projeto, usado junto do `AdapterX.xml`, não no lugar dele.
4. **Chave em mais de uma camada** (sobreposição): vence a de **maior sufixo** (`wins`); as outras ficam `overridden`. A unidade de comparação é `(produto, key_path, name)`; um `<Key>` é comparado como lista inteira (camada maior substitui a lista; presumido, porque o exemplo não tem `<Key>` repetido entre camadas).
5. **Empate** (dois arquivos com o mesmo `N` e rótulos diferentes definindo a mesma chave): `ambiguous`, sem efetivo; a UI mostra os dois.
6. **Conferência com o log**: o adapter registra no startup `[Configuration] [Nome]: [valor]. Type: [ConfigurationRegistry]` (223 linhas no ERS de exemplo). `config_observed` guarda esses valores; a UI compara com o resolvido e destaca divergências. Os nomes no log nem sempre são os do XML (`WebServiceConfiguration` → `PathToWebServiceConfiguration`; `CreatedMeterReadings_ErrorConfigurationPath` → `CreatedMeterReadingsErrorConfigurationPath`): comparação por nome normalizado (sem `_`, sem diferença de caixa) mais tabela de aliases no `_common.toml`. Quando há valor observado, ele é o que vale para o diagnóstico; o resolvido explica de onde veio.

Exemplo real (`AdapterAmi`, três camadas):

| Chave | `AdapterAmi.xml` (-1) | `AdapterAmi_1_Custom.xml` (1) | `AdapterAmi_2.xml` (2) | Efetivo | No log de startup |
|---|---|---|---|---|---|
| `WebServiceConfiguration` | `...\DMSIntegration\AdapterAmi_WebServiceConfiguration.xml` | `...\DMSCustomIntegration\CustomAdapterAmi_WebServiceConfiguration.xml` | `...\DMSCustomIntegrationPROD\AdapterAmi_WebServiceConfiguration.xml` | camada 2 | `[PathToWebServiceConfiguration]` = `...\DMSCustomIntegrationPROD\AdapterAmi_WebServiceConfiguration.xml` ✔ |
| `CreatedMeterReadings_ErrorConfigurationPath` | `...\DMSIntegration\AmiAdapter_CreatedMeterReadings_ErrorConfiguration.xml` | `...\DMSCustomIntegration\Custom_AmiAdapter_CreatedMeterReadings_ErrorConfiguration.xml` | — | camada 1 | `[CreatedMeterReadingsErrorConfigurationPath]` = arquivo `Custom_...` ✔ |
| `CreatedEndDeviceEvents_ErrorConfigurationPath` | `...\DMSIntegration\AmiAdapter_CreatedEndDeviceEvents_ErrorConfiguration.xml` | — | — | camada -1 (`only`) | mesmo arquivo ✔ |

Outros casos conferidos no exemplo: `AdapterFileUpload` `FileUploadInterval` = `01:00:00` (-1) e `00:01:00` (2) → efetivo `00:01:00`, igual ao registrado no log; `AdapterSiteSwitch` `SiteSwitchService` em -1, 0 e 2 → efetivo camada 2. Nos 13 adapters já conferidos na amostra, todos têm mais de uma camada e as sobreposições são poucas (1 a 8 chaves por produto), quase sempre `WebServiceConfiguration`, projeções e textos localizados.

```python
@dataclass(frozen=True, slots=True)
class RegistryLayer:
    product: str; file: str; layer: int; label: str
    values: dict[tuple[str, str], str | tuple[str, ...]]     # (key_path, name) → valor ou lista

def resolve_registry(layers: list[RegistryLayer]) -> list[ConfigParam]   # todas as linhas, com effective/status
```

```python
class Knowledge:
    def adapter_for_process(self, process: str) -> AdapterInfo | None
    def error(self, family: str, code: str, operation: str | None = None) -> ErrorInfo | None   # ERS > vault
    def param(self, group: int, product: str, name: str, scope: str = "registry") -> ParamValue | None
    def hint(self, tpl_code: str) -> Hint | None
    def vault_note(self, family: str) -> str | None        # caminho relativo da nota, para exibir/copiar
```

Precedência: configuração do ERS > pack do vault. Divergências (código no log que não existe no vault, como `ImsCallbackNotReceived`, ou no XML) aparecem como "fora do vault" no painel de conhecimento.

### 7.3 Apelidos locais (`aliases.toml`)

**O quê.** Arquivo opcional `<data dir>/aliases.toml` (fora do git) que mapeia a grafia real de um nome (como aparece nos logs e configs reais do ambiente) para o token neutro usado no repositório (`Custom`). Implementação: `src/dmslog/aliases.py` (`load(data_dir)` -> `Aliases` com `.apply(texto)` e `.apply_name(nome)`; `get(data_dir)` guarda em cache por processo e recarrega se o arquivo mudar).

**Por quê.** Para tornar o repositório público, o nome do adapter específico do cliente foi trocado por `Custom` em tudo. Os ERS reais ainda trazem o nome original, então sem o mapa a família não seria reconhecida (`families.toml`, Registry, `process_map`).

**Esquema.**
```toml
version = 1
[[alias]]
public = "Custom"        # 1-64 caracteres: letras, dígitos, _ . -
real = "<texto original>" # 1-128 caracteres, sem caracteres de controle
```
Várias entradas podem repetir o mesmo `public` (uma grafia real por forma: CamelCase, com underscore etc.). O mesmo `real` com dois `public` diferentes é erro. Arquivo ausente = sem apelidos; arquivo inválido é ignorado inteiro, com mensagem em inglês (sem os valores) em `Aliases.error`, exibida por `dmslog info`, que mostra apenas a contagem.

**Onde se aplica (fronteiras de ingestão, `real` -> `public`, mais longo primeiro).** O objeto `Aliases` é injetado (`IngestOptions.aliases`; CLI e API carregam do data dir), nunca lido por global dentro dos módulos:
- `ingest/discover.py`, `discover(..., aliases=)`: o caminho lógico é normalizado só para classificar (`families.toml`); `process`, `family` e `stream_key` já saem com o token público. `member` e `file_name` continuam reais, pois o arquivo é relido por eles.
- `knowledge/registry.py`, `parse_registry_xml(..., aliases=)`: nome do arquivo (camada e rótulo `_N_<rótulo>`) e nome do produto.
- `knowledge/installation.py`, `read_installation(..., aliases=)`: nomes das operações do `WebServiceConfiguration`, caminhos dos arquivos de configuração exibidos (`web_service_path`, erros) e mensagens de `problems`. A busca do arquivo na coleta continua usando o caminho real.

**Semântica.** Uma passada sobre o texto original, mais longo primeiro (alternância de regex), sem reescanear a saída: `AcmeWidget->Custom` com `Custom->Other` não vira `Other`. `apply` é sensível a maiúsculas; `apply_name`/`sanitize` casam a string `real` inteira em qualquer caixa (sem misturas parciais). Validação: `version` inteiro 1 (não 1.0, não bool); `real` e `public` sem caracteres de controle (C0, DEL, C1) nem de formatação; `real` iguais ignorando caixa com `public` diferente são erro. Diagnósticos são mensagens fixas (no máximo o número da entrada ou da linha), nunca conteúdo do arquivo.

**Também normalizados (texto derivado de nomes):** `source.error`, `SourceResult.member`, eventos de progresso/SSE, `result.warnings`, saída de erro da CLI, mensagens de job da API e erros de diagnóstico do Registry (`Aliases.sanitize`, `_Run._shown`).

**Diagnósticos (`src/dmslog/diagnostics.py`).** Nunca embutem `str(exc)` de OSError, zip, XML ou codec (o texto pode trazer o nome escapado, por exemplo `\\\\`, que o matcher não reconhece). Cada mensagem é montada com texto fixo escolhido pela CLASSE da exceção (`describe_error`: "file not found", "permission denied", "bad zip file or member", "invalid XML at line N, column M", "unknown encoding", ...), inteiros (linha, coluna, errno) e componentes (caminho, membro) normalizados uma única vez. Exceções do app declaram `diag_reason`/`diag_component` ou `diag_safe`. Com `aliases.toml` ativo, avisos de derivação/correlação mostram só o nome da classe. O matcher também casa a variante com `/` de um `real` que contém `\\` (e vice-versa).

**Limites.** Só nomes: não reescreve texto de mensagem nem payload (os templates não contêm o nome). O banco mantém a grafia real em `source.member` e `source.file_name` (são a chave para reabrir o arquivo; a lista de fontes da API/UI os exibe), em valores de parâmetros do Registry (por exemplo o caminho do `WebServiceConfiguration`) e no nome do zip/pasta ingerido (`collection.path`, `label`). Uma coluna de nome de exibição seria a evolução natural, mas não foi feita. Não compartilhar índice ou exportação gerados com nomes reais. Mudou o `aliases.toml`: reingerir o caso.

## 8. Correlação

| Tipo | Regra | Confiança |
|---|---|---|
| normal ↔ sensitive | Para cada registro normal com `refs_sensitive`, buscar no stream sensitive da mesma coleta e PID, na **mesma TID**, o próximo registro do template par com `|Δt| ≤ 1 s`, respeitando a ordem. Se houver chave comum (Wfm `request_id`), ela decide. Restringir à interseção das janelas dos dois arquivos (as coberturas diferem muito). | 3 por chave; 2 ts+TID exatos; 1 mesmo segundo + TID |
| callback | `GID` (Crm `OMS_REQUEST`, Osr) entre registro de espera e callback, em outra TID. Wfm `Callback received` sem chave na linha de início: só por janela de tempo, marcado como heurístico. | 3 / 0 |
| retry e resposta | mesmo `msgId` (Smn/Ont `Sending ... message: <guid>` → `finished` → `Request message with MessageId ... sent`). | 3 |
| mesma chave | qualquer valor de `entry_key` repetido em fontes diferentes do caso (INC, GID, mRID, GUID): consulta sob demanda, não materializada. | — |
| entre máquinas | **fora da 1.0** (U5: um ERS por caso). Pós-1.0: por chave e por tempo com `clock_offset_ms` da coleta. | — |
| payload ↔ anúncio, par e operação | anúncio no normal por PID + TID + Δt ≤ 1 s (+ MessageID); request ↔ operação por MessageID; response ↔ request por CorrelationID (Wfm) ou pelo fim da operação ±16 ms (Ont/Smn, sem ID); conteúdo do Wfm pela TID da operação aberta (seção 6.6). | 3 por chave; 2 TID dentro da operação; 1 tempo |
| callback CRS/IMS (Wfm, só com nível ≥ Info) | GID do pedido (WFM-123/124) = GID do callback (WFM-126, WFM-030). | 3 |

**Como construído (M5 e M6), onde difere do desenho original:** a correlação **não** roda por stream com o checkpoint da derivação; roda **por collection group** depois de `derive`, em três motores independentes, cada um com assinatura em `meta` e um SAVEPOINT por grupo (grupo com assinatura igual não escreve nada; grupo alterado apaga e recria só os links do próprio tipo, e a recomputação também remove links órfãos do motor):
- `derive/correlate.py` (`meta['correlate.<grupo>']`): pares normal↔sensitive pelas regras `[[sensitive_pair]]` (`key`, `order` = `after`|`any`, `max_dt_ms`), consumindo cada registro sensitive uma vez, só na interseção das janelas dos dois arquivos; lê candidatos por `ix_entry_tpl_ts`, sem varredura completa, com buffer limitado (`MAX_BUFFER`, `MAX_CANDIDATES_PER_RULE`; o descartado é contado).
- `derive/links.py` (`meta['correlate.links.<grupo>']`): `callback`, `response` e `retry` pelas regras `[[link_rule]]` (chave de `entry_key` ou campo, `scope` stream|group, `window_only` com confiança 0 só quando há exatamente um `a` aberto); memória limitada (`MAX_OPEN_KEYS`, `MAX_B_PER_A`).
- `derive/payloads.py` (`meta['payloads.<grupo>']`): anúncio, par e operação dos payloads (seção 6.6). **Regra do candidato único** em toda ligação por tempo ou por chave repetida: se há mais de um candidato (ou a unicidade não foi provada dentro de `MAX_SCAN`), nada é ligado e o motivo fica em `payload.reason` (`ambiguous`, `scan_limit`).
As assinaturas incluem tamanho, mtime, CRC, hashes e contagens das fontes, as regras, os templates envolvidos e `meta['entries_epoch']`, que o writer incrementa quando apaga registros (isso também apaga os links incidentes). O pareamento Ont/Smn pelo MessageID dentro do XML ficou no motor de payloads (M6), não em `correlate.py`.

## 9. Ingestão, indexação incremental e desempenho

### 9.1 Descoberta e escopo

`discover(path) -> Iterator[SourceInfo]` aceita zip de ERS, pasta (instalação ou ERS extraído) e arquivo. Classifica pelo caminho `.../Log/<conta>/<serviço>/<Processo>_<pid>[.sensitive].log[.N]`, `oasErrLog.log[.N]`, `BLTHost.<serviço>[.sensitive].log`, `ProcessMonitor.<serviço>.log`, `dnaService.<serviço>.log`, `dnaHealthMonitor.*`, `arb*.<serviço>.log`.

Escopo padrão: adapters do escopo (normal e sensitive) + BLTHost + oasErrLog + ProcessMonitor/dnaService/arb do serviço do adapter. NMC, PSengine, arbIP e o resto do `Log/` (≈ 800 MB no ERS de exemplo) ficam listados e são indexados só se o usuário marcar. O par Enterprise/Sensitive é reconhecido pelo nome (`ERS_CL_<GUID>_<timestamp>_[Sensitive_]Enterprise_<desc>.zip`) e vira um único `ers_group`. Um caso aceita um ERS (ou o par) ou uma pasta de instalação (U5); abrir o par em qualquer ordem completa a mesma coleta, e um ERS de outro GUID pede um caso novo.

### 9.1a Todos os logs do ERS (U4)

A ferramenta precisa analisar qualquer log do ERS; a 1.0 entrega profundidade nos adapters. O que vale para cada nível:

| Nível | O que o usuário tem | Famílias na 1.0 |
|---|---|---|
| **Genérico** | abrir, indexar, grid, busca FTS/regex, filtros por fonte/tempo/PID/TID, timeline por volume, severidade só por marcadores de texto (seção 6.4, passos 1, 4 e 6), painel de desconhecidos | qualquer arquivo de texto do ERS (`UNKNOWN`, NMC, PSengine, arbIP, ...), com envelope detectado (`adms`, `oaserr`) ou `plain` |
| **Com templates** | + templates, severidade por template/código, colunas dinâmicas | adapters do escopo, BLTHost, oasErrLog, serviço (dnaService/ProcessMonitor/arb) |
| **Com eventos** | + operações, estatísticas, correlação, payloads, painéis | adapters do escopo, BLTHost |

Regras para que os demais logs entrem depois **sem retrabalho**:
- Descoberta dirigida por dados: `src/dmslog/families.toml` mapeia padrão de caminho/nome → família, envelope esperado e se entra no escopo padrão. Família nova = linha nova nesse arquivo + um TOML de templates; nada no `discover.py` é específico de adapter.
- Envelope novo = uma classe a mais em `parse/envelope.py` registrada em `ENVELOPES`; a detecção por maioria já escolhe entre os registrados.
- Esquema sem colunas de adapter: família, `event.kind`, `entry_key.key_type`, `template.class` são texto aberto; campos específicos ficam em `entry.fields` (JSON); `config_param.product` aceita qualquer produto do Registry.
- Derivadores são habilitados por regra no TOML da família (`[[operation]]`, `gap_heartbeat`, ...); família sem regras simplesmente não gera eventos.
- Arquivos que não são texto de log (XML de configuração, binários) ficam fora; o Registry/WebServiceConfiguration já é lido pela camada de conhecimento. Os bancos `LocalPersistenceStore/*.db3` (fila/cache de mensagens dos adapters, com conteúdo sensível) aparecem na lista de fontes como "não indexado"; uso como fonte opcional é proposta pós-1.0 (seção 15, P3 no ROADMAP).

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

### 9.3a Volume com nível elevado (Info/Verbose)

Medido no ERS de 2026-10-08 14:53 (seção 3; `docs/formato/payloads.md`, seção 8):
- Uma janela Info/Verbose multiplica a escrita do log normal por 27–120× (≈ 30–37 MB/h por adapter em Verbose; 5 MB/h só em Info). O sensitive cresce 2–4 MB/h, quase tudo payload.
- O rastreio `Communication2` (GEN-C2T/C2O/C2M) é 46–62% das linhas Info/Verbose e 56% dos bytes dos logs normais na amostra: vai para `entry`/`entry_body` (grid e contagens), mas fora do FTS (`template.fts = 0`), e entra no `bucket` como sev 5 (oculto por padrão).
- A rotação limita cada processo a `.log` + `.log.0` no ERS (`.log.0` de 59–81 MB), então o pior caso é limitado pelo número de adapters em Verbose, não pelo tempo de elevação: ≈ 2,6 GB de logs no escopo padrão com os 14 adapters elevados (projeção). Lotes por bytes (9.2) já cobrem registros grandes (payload de até 164 mil caracteres).
- Metas: M6 (ERS Verbose real, 1.041 MiB no escopo padrão) ≤ 60 s e banco ≤ 1,7 GB; M8 (pior caso projetado) no ROADMAP.

### 9.4 Consultas e UI

- Paginação **keyset** por `(ts, id)` nas duas direções (`WHERE (ts,id) > (?,?) ORDER BY ts,id LIMIT 200`); nunca `OFFSET`. Ir para um instante = buscar o primeiro `(ts,id) >= t`.
- Barra de rolagem do grid: posição aproximada pela soma acumulada de `bucket` (contagem por minuto); o grid busca a página do minuto correspondente.
- Contagem total do filtro: `count(*)` com timeout de 2 s; se estourar, a UI mostra estimativa por `bucket` e continua.
- Busca: termo → FTS5 (com `snippet()`); substring/regex → `LIKE`/`REGEXP` (função Python registrada) **sempre junto de um filtro de fonte ou de tempo**, em thread com cancelamento e limite de resultados (busca por regex não tem timeout nativo em `re`).
- Texto grande: a API devolve `msg` + até 200 linhas de `cont` e um link para o resto. Payload tem rota própria (formatado sob demanda, seção 6.6).
- Horário exibido sem conversão (seção 5, Tempo): o front-end formata `ts` sempre em UTC.
- Indexação em thread própria com conexão própria (escritor único); requisições HTTP em threads com conexões de leitura (WAL). Fontes prontas ficam navegáveis enquanto as outras indexam.

## 10. API (servidor ↔ viewer)

JSON sobre HTTP em `127.0.0.1`. Todas as rotas exigem o cookie de sessão (seção 12). Cursor = `"<ts>:<id>"`. Rotas de um caso recebem `case=<id>` na query (rotas `GET`/`DELETE`) ou `case` no corpo (rotas `POST`); caso desconhecido ou id inválido → erro 4xx com `error` estável. Erros de validação são `400` com código estável (`invalid_field`, `bad_limit`, `bad_scope`, `path_not_absolute` e outros, conforme a rota); recurso inexistente é `404 *_not_found`. O estado abaixo é o da `main` após o M6; o que depende dos PRs do M7 ainda em revisão aparece marcado como previsto.

| Rota | Descrição |
|---|---|
| `GET /api/health` | versão, versão do pack, SQLite, FTS5 ok |
| `GET /api/cases` · `POST /api/cases` `{name}` · `DELETE /api/cases/{id}` | casos locais; apagar remove a pasta do caso |
| `POST /api/cases/{id}/collections` `{path, scope}` | adiciona ERS/pasta/arquivo e inicia a ingestão; na 1.0, `409` se o caso já tem outra coleta (aceita só a outra metade do par Enterprise/Sensitive) |
| `GET /api/cases/{id}/progress` (SSE) | progresso por fonte; novidades do modo watch |
| `GET /api/sources` | coleções, streams e fontes (família, PID, sensitive, janela, contagens, status) |
| `GET /api/templates?family=` | templates com contagem; `GET /api/unknown?family=` assinaturas sem template |
| `POST /api/entries/page` `{filter, cursor, dir, limit, columns}` | página do grid; colunas dinâmicas vêm de `fields` |
| `POST /api/entries/count` `{filter}` | contagem (ou estimativa) |
| `GET /api/entries/{id}` | registro completo: texto, campos, template, evento, links, conhecimento ligado e `payload_count` (quantos payloads o registro contém ou anuncia) |
| `GET /api/entries/{id}/context?before=50&after=50&same=tid\|source\|merge` | vizinhança |
| `POST /api/histogram` `{filter, bucket_ms, split: sev\|source\|tpl}` | timeline |
| `GET /api/events?kind=&name=&status=&stream=&source=&q=&from=&to=&limit=&after=` | eventos (paginação por keyset `(ts_start, id)`; listas separadas por vírgula; `q` = chave exata ou prefixo) |
| `GET /api/events/{id}` | evento, seus primeiros 200 registros e eventos relacionados (`retry_seq`/`exception` com a mesma chave) |
| `GET /api/events/stats?kind=&stream=&from=&to=` | por (família, nome): n, contagem por status, p50, p90, p99, máx. e média (percentil exato por posto) |
| `GET /api/events/histogram?kind=&name=&bucket=minute\|hour` | contagem por balde e status (até 5.000 baldes) |
| `GET /api/stats/methods?stream=` · `GET /api/stats/series?stream=&side=&method=&from=&to=` | métodos com chamadas acumuladas; série de `stat_sample` (acumulado e delta, até 5.000 amostras) |
| `GET /api/knowledge?template=` · `GET /api/knowledge/error?family=&code=&operation=` | dica, parâmetros e nota do vault; severidade e razão do código (ERS > vault, `out_of_vault`) |
| `GET /api/keys/types` · `GET /api/keys/{type}/{value}?limit=&after=` | tipos de chave com contagem; todos os registros com a chave em todas as fontes do caso (keyset por `(ts, id)`; valor decodificado como UTF-8 estrito, 1 a 256 caracteres imprimíveis) |
| `GET /api/entries/{id}/links` · `GET /api/entries/{id}/side-by-side` | links do registro nas duas direções (`kind`, `confidence`, `direction`); melhor contraparte (prefere `normal_sensitive`, maior confiança, mais próxima no tempo) com os dois registros completos; `GET /api/entries/{id}?cont_lines=N` limita as linhas de continuação |
| `GET /api/knowledge/error/{family}/{code}` · `/param/{product}/{name}` · `/adapter/{family}` | previsto, **não construído**: o M3 entregou só as duas rotas por query de cima (`/api/knowledge?template=` e `/api/knowledge/error?family=&code=&operation=`) |
| `GET /api/config?group=&product=` | configuração por camada (`config_param`) com efetivo, status e valor observado no log (`config_observed`); por operação, se o payload é esperado (`LogLevel` × `MessageLogLevel`) |
| `GET /api/entries/{id}/payloads?case=` | `{payloads: [meta]}`: payloads contidos no registro (`entry_id`) mais os que ele anuncia (`ref_entry_id`), por id; `404 entry_not_found`. `meta` = `{id, entry_id, event_id, event_conf, ref_entry_id, pair_id, role, direction, peer, operation, msg_id, corr_id, fmt, root, well_formed, truncated, reason, char_len, ts, sensitive, member}` (`ts`, `sensitive` e `member` descrevem o registro que contém o payload) |
| `GET /api/payloads/{id}?case=&format=pretty\|raw` | `{payload: meta, text, truncated_display, formatted, pair, entry}`. `text` é sempre o trecho cru, cortado em `TEXT_CAP` (2.000.000 caracteres; `truncated_display`); `formatted` só com `format=pretty`: `{text, ok, kind, reason, truncated_display}` (falha de formatação = `ok=false, reason="format_error"`, nunca status de erro); `pair` = payload par; `entry` = `{id, ts, tid, member, sensitive}`; `404 payload_not_found`; `format` inválido → `400` |
| `GET /api/payloads/page?case=&role=&direction=&peer=&operation=&fmt=&event=&orphan=&q=&limit=&after=` | `{payloads: [meta], next}`; `role`, `direction`, `peer`, `operation`, `fmt` aceitam listas separadas por vírgula; `event` = id de evento; `orphan=1` só os sem operação (o `reason` diz por quê), `orphan=0` só os ligados; `q` = `msg_id` ou `corr_id` exato. Ordem `(ts do registro, id do payload)` (registros sem hora primeiro), keyset (nunca OFFSET), limite padrão 200, máximo 1.000. É `GET` (o desenho original previa `POST`) |
| `GET/POST /api/views` · `DELETE /api/views/{id}` | filtros salvos do caso (`saved_view`). Os de bookmarks (`/api/bookmarks`), comparar registros e renomear/sobrescrever filtros salvos vêm no PR #70 (M7, em revisão) e não estão na `main` |
| `GET/PUT /api/redaction` · `POST /api/export` | previstos para o M7 (PR #25, em revisão), ainda não construídos na `main`; o formato do relatório está em 10.1 |

Filtro (árvore JSON, compilada para SQL **parametrizado** em `store/filters.py`; nada do usuário vira SQL literal):

```json
{"and": [
  {"source": [3, 4]}, {"sev": {"le": 2}}, {"tpl": ["WFM-081", "GEN-EXC-01"]},
  {"ts": {"ge": 1791374400000, "lt": 1791378000000}}, {"tid": 17604},
  {"text": {"fts": "timeout"}}, {"key": {"type": "request_id", "eq": "a0b0..."}},
  {"not": {"tpl": ["GEN-STA-01", "GEN-STA-02", "GEN-STA-03"]}},
  {"or": [{"event_status": "fail"}, {"flags": "partial"}]},
  {"payload": {"roles": ["response", "fault"], "fmt": "xml", "orphan": true}}
]}
```

Folha `payload`: `{"payload": {<atributo>: str | [str, ...], ..., "orphan": bool}}`, compilada como `entry.id IN (SELECT entry_id FROM payload WHERE ...)` com os atributos em AND. Atributos aceitos: `role` (ou `roles`), `direction`, `peer`, `operation`, `fmt`, `msg_id`, `corr_id`, `root`; `orphan: true` = payload sem operação (`event_id IS NULL`), `false` = com operação. Atributo desconhecido → erro `unknown_operator`; valores sempre parametrizados.

A caixa de busca do viewer aceita a forma curta `sev:<=error tpl:WFM-081 tid:0x44c4 src:Wfm "texto exato" /regex/`, convertida para essa árvore no front-end.

```python
# store/query.py
def page(conn, flt: Filter, cursor: Cursor | None, direction: Literal["fwd", "back"], limit: int,
         columns: list[str]) -> Page
def count(conn, flt: Filter, timeout_s: float = 2.0) -> CountResult          # exact | estimate
def histogram(conn, flt: Filter, bucket_ms: int, split: str | None) -> list[Bin]
def context(conn, entry_id: int, before: int, after: int, same: str) -> list[Row]
def event_stats(conn, kind: str, name: str | None, flt: Filter | None, group_by: str | None) -> list[Stat]
# store/payloads.py (só leitura, sob read_guard); a formatação é feita pela API via payload/pretty.py
def payloads_of_entry(conn, entry_id: int) -> list[dict]
def get_payload(conn, payload_id: int) -> dict | None            # meta + texto cru + par + registro
def list_payloads(conn, flt: PayloadFilter, limit: int, after: Cursor | None) -> tuple[list[dict], str | None]
```

### 10.1 Formato da exportação (`.txt`, U10)

Texto simples para colar em chamado do ServiceNow: UTF-8 sem BOM, fim de linha CRLF (cola igual em Windows e Linux), só ASCII na moldura (sem caracteres de desenho de caixa), linhas de log literais (sem quebra forçada). Textos fixos em inglês (`messages.py`).

```
dmslog report - <case label>                       generated <local time>, dmslog <ver>, pack <ver>
Source: ERS <ers_ts> (<machine>), Enterprise+Sensitive        Redaction: ON (profile "default")
Time shown as logged (local server time, no conversion).

== Summary ==
<n> selected events, <n> bookmarks, window <ts_start> .. <ts_end>
<adapter> <operation>  count=<n>  fail=<n>  p90=<ms>  ...

== Events ==
[1] <ts_start> .. <ts_end>  <adapter> <operation>  status=fail  duration=<ms>  key=<GUID-1>
    error codes: <code> (<severity>) ...
    note: <bookmark note>
    --- log excerpt (<source file>, lines <a>-<b>) ---
    <linhas literais, já anonimizadas>

== Payloads ==                (só com include_payloads; anonimização ciente de XML/JSON)
== Effective configuration ==  (parâmetros ligados aos eventos: LogLevel, MessageLogLevel, timeouts)
== Redaction legend ==         (categorias substituídas e contagens; nunca os valores originais)
```

- Tamanho: limite configurável (padrão 30.000 caracteres); acima disso, o resumo vem inteiro e os trechos são cortados com aviso, ou o usuário divide em partes numeradas (`part 1/3`).
- O mapa marcador → valor original fica só local (pasta do caso, `exports/<data>.map.json`), nunca no `.txt`, para o Jefferson reverter um marcador se precisar.
- CLI: `dmslog export <caso> --what events|bookmarks|selection --out x.txt [--no-redact] [--include-payloads]`.

### 10.2 Anonimização: modo strict e invariantes

O padrão é o modo **strict** (`dmslog export --redaction-mode strict`): tudo o que não é provadamente inofensivo é substituído (lista de permissão, "default-deny"). O modo **standard** aplica só as regras de padrões (GUID, IP, e-mail etc.) e é mais legível, porém menos seguro. Código: `report/redact.py`, `report/export.py`; testes: `tests/unit/test_redact*.py`.

**Regiões XML/JSON.** Payloads e textos com marcação ou JSON são cortados em regiões. Uma região só mantém o esqueleto (nomes permitidos) se passar numa validação estrita: XML aceito pelo `xml.parsers.expat` do stdlib (sem DTD, sem declarações de entidades e sem resolução externa; as entidades predefinidas, como `&amp;`, continuam aceitas) mais o validador próprio (nomes de token inteiro, atributos separados por espaço, `<?xml` só no início); JSON aceito pelo validador linear próprio e confirmado por `json`. Região inválida, ambígua ou sem fim determinável vira **um único** `<VALUE-n>` até o fim do texto. JSON dentro de texto XML só mantém o esqueleto se aquele trecho for JSON válido.

**Invariante de texto.** Em texto com indício de estrutura, cada caractere cai em exatamente uma categoria: (a) região validada (só esqueleto com nomes permitidos); (b) conteúdo de string completa entre regiões (substituído); (c) tudo depois de string ambígua ou sem fechamento, de região forte inválida, de candidato JSON que falhou (inclusive os fracos, como `[0, x`) ou de `<`/`>` solto (substituído até o fim do texto, por um único caminho `to_end`); (d) texto sem aspas entre regiões, antes de qualquer `<`/`>` solto: só este vai para a lista de permissão de texto livre. Aspa aberta colada a uma palavra conta como ambígua. Valores rotulados (`user: x`, `mRID: x`) são substituídos até o fim do valor documentado; uma palavra que só parece rótulo não toma nem encerra um valor.

**Estado único por registro.** Primeira linha e continuação são lidas como um texto só (`record_spans`); os parsers de continuação só liberam o que o estado do registro inteiro deixa seguro; um registro cortado pelo limite de leitura tem estado desconhecido (continuação e payloads opacos); cada payload recebe os spans do registro inteiro (união com a redação default-deny do próprio payload); pedaços em volta de payloads herdam o que o registro inteiro substitui (`replaced_spans`). A entrada que não codifica em UTF-8 (surrogate isolado) falha fechada (a região vira valor; a saída é sempre UTF-8 estrito). Nenhum ponto do export pode produzir um pedaço menos anonimizado que o registro inteiro; isso é propriedade testada via `build_report`.

**Limites declarados.** No strict, texto livre sem estrutura mantém palavras do catálogo e compostos delas (`Communication2`, `WFM-081`), mas não palavra de identificador (crew, server, node, meter, user, token, inc...) seguida de dígitos (`Crew1234`), que é substituída com a categoria ligada ou desligada; categoria desligada tira só o marcador próprio: no strict o valor rotulado ou de campo de template vira `<VALUE-n>`, no standard ele passa; nomes de elemento e chaves JSON formados só por palavras do catálogo aparecem em regiões validadas; payload truncado ou malformado perde o esqueleto. No standard, um formato ou rótulo colado a letra ou dígito não é reconhecido. Revisar o texto antes de compartilhar continua sendo necessário.

**Testes.** Perfil `dmslog-redact` do hypothesis (determinístico, sem banco de exemplos, sem deadline) para comportar-se igual no CI e local; exploração local com `DMSLOG_HYPOTHESIS_SEED=<n>` e `DMSLOG_HYPOTHESIS_SCALE=<k>`. Os testes de tempo verificam sobretudo a linearidade (razão entre tamanhos, melhor de 3 execuções) e mantêm apenas um teto absoluto generoso (15 s, escalado pela velocidade da máquina) para pegar explosões.

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
| Normal ↔ sensitive lado a lado; "ver no sensitive" a partir do registro; tudo de uma chave (INC, GID, GUID) entre as fontes do caso | Correlação, Session Rules | M5 |
| Payloads: XML/JSON formatado, par request↔response, ligado à operação; lista de payloads por operação; aviso de payload esperado e ausente | — (requisito U7) | M6 |
| Bookmarks com nota e cor; comparar dois registros; exportar relatório `.txt` para chamado com anonimização configurável | Bookmarks, Compare, Export | M7 |
| Pasta de instalação ao vivo (polling incremental, "seguir o fim") | tail | M7 |
| Configuração em camadas com valor observado no startup | — (diferencial do projeto) | M3 |
| Tema escuro/claro, atalhos de teclado (próximo erro, próximo do mesmo template, ir para hora) | Dark mode, navegação | M1 (tema), M2 (atalhos) |

## 12. Segurança e privacidade

- Servidor só em `127.0.0.1`, porta aleatória (ou `--port`). Token aleatório (`secrets.token_urlsafe(32)`) na URL aberta pelo `webbrowser`; trocado por cookie `HttpOnly; SameSite=Strict`. Sem token/cookie válido: 403.
- Checagem de `Host` (`127.0.0.1:<porta>`/`localhost:<porta>`) contra DNS rebinding; checagem de `Origin` em toda requisição com método que altera estado (`POST`, `PUT`, `PATCH`, `DELETE`): o cabeçalho `Origin` precisa ser exatamente `http://127.0.0.1:<porta>` ou `http://localhost:<porta>` (comparação sem diferenciar maiúsculas); ausente ou diferente → 403, depois da checagem do cookie. Na `main` a lista do código é `POST`/`DELETE`; `PUT` e `PATCH` entram junto das rotas do M7 (PR #25, em revisão).
- Cabeçalhos: `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` nas respostas da API.
- **Conteúdo de log é dado não confiável**: o front-end só usa `textContent`/nós de texto, nunca `innerHTML` com conteúdo de log (XML, stack traces e mensagens podem conter `<script>`). Destaques de busca montados com `Text` + `<mark>` criados por DOM.
- Nada sai da máquina: sem telemetria, sem verificação de atualização, sem CDN, sem fontes externas. Teste de CI garante que o pacote não abre sockets fora de loopback.
- Caminhos: só os que o usuário informa; leitura de zip sem extrair em disco (sem zip slip); XML via `ElementTree` sem entidades externas, com limite de tamanho.
- SQL só parametrizado; regex do usuário executada em thread com limite de linhas e cancelamento.
- Dados em pasta do usuário (U3, decidido): Windows `%LOCALAPPDATA%\dmslog\`, Linux `${XDG_DATA_HOME:-~/.local/share}/dmslog/`; `--data-dir` para outro lugar (ex.: pendrive cifrado). Um caso = uma pasta; apagar o caso apaga o índice. A UI mostra onde estão os dados e o tamanho.
- Exportação (U10): `redact.py` substitui valores por marcadores estáveis dentro do relatório (o mesmo valor vira o mesmo marcador, ex.: `<HOST-1>`, `<GUID-3>`). **Configurável**: categorias liga/desliga (GUIDs, GIDs, INC/PROB, mRIDs, placas, hosts, IPs, usuários, URLs, caminhos, e-mails, telefones, nomes/endereços de pessoa em payload), termos do usuário (ex.: nome do cliente, siglas de subestação) e lista de exceções (ex.: nomes de operação, códigos de erro, que nunca são trocados). O perfil fica em `<dados>/redaction.toml`, fora dos casos e do repo; padrão = tudo ligado. Exportar sem anonimizar exige marcar a opção explicitamente a cada exportação.
- **Payloads** (U7): sempre tratados como sensíveis. Ficam fora da exportação por padrão; quando incluídos, a anonimização também atua por nome de elemento/propriedade XML/JSON (lista no `redaction.toml`, ex.: `Name`, `Phone`, `Email`, `Address`, `Document`), além das regex. A UI marca payloads com selo "sensitive". XML de payload nunca passa por parser que resolva DTD/entidades (`payload/pretty.py` é tokenizador próprio).
- Amostras Verbose em `local-samples/` seguem a convenção já usada (Git LFS, repositório privado) e só são lidas por script; fixtures de payload no `tests/` são sempre sintéticas.
- Nível elevado também põe dado sensível no **log normal**: conteúdo XML enviado ao CRS/WMS (WFM-120), chaves de mapa (GEN-MAP-02), URLs, hosts e IPs (GEN-C2L-02, GEN-PSA-02). Esses templates entram nas categorias de anonimização da exportação como os payloads.
- Bancos `LocalPersistenceStore/*.db3` do ERS (fila de mensagens dos adapters): sensíveis (corpo das mensagens, conta do processo). Na 1.0 não são lidos nem copiados; se um dia forem lidos (proposta P3), só com opção explícita, abertos somente leitura (`mode=ro&immutable=1`) a partir de cópia temporária na pasta do caso, apagada ao fim, e nunca indexados por padrão.
- Repositório privado (U1): o pack do vault é documentação Schneider. Nada de índice ou export em git; `.gitignore` cobre `dist/`, `local-samples/_extracted/`, `*.sqlite`.

## 12a. Idioma do app (i18n)

Requisito firme: **todo o app é em inglês**. Vale para nomes de pacote, módulo, função, variável, tabela e coluna; comentários e docstrings; mensagens de CLI, log, API e erro; textos da UI; rótulos do pacote de conhecimento (chaves e textos gerados/curados); nomes de testes e fixtures; e a documentação de uso do app (`README.md` e ajuda da CLI). Os documentos de projeto em `docs/` (este, `ROADMAP.md`, `docs/formato/`) continuam em pt-BR; por isso os nomes `ARQUITETURA.md` e `ROADMAP.md` ficam como estão (o `CLAUDE.md` já aponta para eles).

- Os comentários em português dentro do DDL e dos exemplos deste documento são explicação para o leitor; o `schema.sql`, os TOML e o código reais são escritos em inglês. Identificadores, valores de enum e nomes de esquema mostrados aqui já são os definitivos (inglês).
- Só inglês por enquanto, mas **textos visíveis ao usuário ficam centralizados**: `src/dmslog/messages.py` (CLI, API, erros) e `src/dmslog/web/js/strings.js` (UI), acessados por chave. Nada de texto de UI espalhado em código, para que um locale futuro seja só mais um catálogo.
- **Texto dos logs dos adapters é exibido literalmente** (sem tradução nem correção, inclusive `sensistive`, `wirtten`). O conteúdo do vault é em português; o pack guarda os campos de origem como vieram (por exemplo, `Details` do catálogo de erros, que já está em inglês) e as explicações curadas (`curated/hints.toml`) são escritas em inglês.

## 13. Testes

- **Fixtures sintéticas pequenas** em `tests/fixtures/logs/`, escritas a partir dos templates documentados, com valores fictícios: um arquivo por família (10–200 registros) cobrindo cada template, mais casos de borda: BOM, CRLF, linha sem TID, mensagem iniciada por TAB, mensagem terminando em espaço, dump de 1.600 linhas, stack trace .NET com inner exception, último registro truncado, timestamp que volta 3 ms, operação dividida entre `.log.0` e `.log`, janela `Debug level changed`, bloco de estatística com queda de contador (restart), envelope `oaserr`, retrocesso de 1 h (fim de horário de verão) e lacuna de 1 h simultânea. Os zips de teste são montados em tempo de teste (`zipfile`) a partir dessas fixtures.
- **Registry**: árvore `Registry/` fictícia com camadas -1/0/1/2, chave só em uma camada, sobreposição, `<Key>` lista, empate de sufixo e as duas formas de `%OASINSTALLDATADIR%`; mais log de startup com `[Configuration]` para conferir resolvido × observado.
- **Payload**: fixtures sintéticas de SOAP/XML e JSON (uma linha, indentado, truncado, malformado, com `<!DOCTYPE>` e entidades, com `<script>`), escritas a partir do formato descoberto nas amostras Verbose; o `<script>` aparece literal na UI.
- **Exportação**: `.txt` de fixture comparado byte a byte com o esperado (CRLF, sem BOM); com anonimização ligada, nenhum termo da lista sensível da fixture aparece; com categoria desligada, o valor aparece; payload só com `include_payloads`.
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
- **Windows (portátil)**: `packaging/build_portable.py` (o rascunho atual fica, com ajustes): baixa o Python embeddable 3.13.x com SHA-256 fixado, extrai em `python/`, copia `dmslog.pyz`, gera `dmslog.bat` (`"%~dp0python\python.exe" -I "%~dp0dmslog.pyz" serve %*`) e `LEIA-ME.txt`. O `._pth` fica como vem (sem `import site`, isolado); a linha `..\app` sai, porque o `.pyz` entra no `sys.path` sozinho. A janela de console mostra a URL e fecha o servidor com Ctrl+C. Sem instalador, sem admin, sem registro do Windows, sem internet. Tamanho estimado: ~12 MB zipado. O navegador aberto é o padrão do sistema (Edge ou Chrome no PC de destino, U2); teste manual nos dois.
- **Linux**: `dmslog-linux.tar.gz` com `dmslog.pyz` + `dmslog` (shell: `exec python3 -I "$(dirname "$0")/dmslog.pyz" "$@"`). Requisito: Python ≥ 3.11 com `sqlite3` e FTS5 (verificado na inicialização, com mensagem clara se faltar). No Omarchy, o Python do sistema serve.
- Versão: `dmslog.__version__` + versão do pack + hash do commit gravados no `.pyz` e em `GET /api/health`.
- `.github/workflows/ci.yml` (rascunho atual) é mantido e ampliado conforme a seção 13.

## 15. Registro de mudanças da arquitetura

| Data | Mudança | Autor |
|---|---|---|
| 2026-10-08 | Versão inicial | Opus |
| 2026-10-08 | App inteiro em inglês (seção 12a); classes de template e severidades com nomes em inglês | Opus |
| 2026-10-08 | Decisões do usuário U1–U10 incorporadas (seção 1a). Escopo: um ERS por caso; 1.0 = adapters com amostra + BLTHost, demais logs em nível genérico e extensíveis sem retrabalho (9.1a, `families.toml`); adapters sem amostra fora do escopo; correlação entre máquinas e `clock_offset_ms` pós-1.0 | Opus |
| 2026-10-08 | Tempo local preservado, UI formata em UTC; detecção de salto de horário de verão (`event.kind='clock_jump'`, `gap.detail.possible_clock_jump`) | Opus |
| 2026-10-08 | Payload obrigatório na 1.0: tabela `payload` (provisória até as amostras Verbose), `flags & 64`, `parse/payload.py`, `payload/pretty.py`, `derive/payloads.py`, rotas `/api/payloads*`, filtro `payload`; FTS limitado a 65.536 caracteres de `cont` por registro (seções 5, 6.6, 10) | Opus |
| 2026-10-08 | Registry em camadas: precedência por chave, maior sufixo vence, `_1_<Projeto>` como camada separada; `config_param` ganha `key_path`, `layer`, `layer_label`, `status`; nova tabela `config_observed` (startup do log); regra validada no ERS de exemplo (7.2.1) | Opus |
| 2026-10-08 | Exportação só `.txt` (CRLF, sem BOM) com anonimização configurável (`redaction.toml`, `/api/redaction`); payload fora por padrão (10.1, 12) | Opus |
| 2026-10-08 | Pack do vault commitado em repositório privado, com aviso em `manifest.json`; alvo do front-end = Edge/Chrome; índices em `%LOCALAPPDATA%`/XDG confirmados | Opus |
| 2026-10-08 | Decisão U11: `CustomAdapterChangeIncidentDevice` obrigatório na 1.0 com base de conhecimento, templates e eventos específicos; genérico apenas provisório, sem bloquear M0. U9 esclarecida: `_1_<Projeto>` configura o mesmo processo e é distribuído em MSI/MSU de atualização do projeto ADMS; precedência por chave preservada | Arquiteto (Codex) |
| 2026-10-08 | Tarefa 6.1 (formato dos payloads, `docs/formato/payloads.md`): tabela `payload` deixa de ser provisória; `direction` passa a `in`/`out` e ganha `role`, `peer`, `msg_id`, `corr_id`, `pair_id`, `event_conf`; `fmt` = `xml`/`xml_seq`/`html`/`json`/`text` (sai `soap`); `entry_id` pode ser do log normal (seções 5, 6.6) | Opus |
| 2026-10-08 | Atributo `payload` dos TOML definitivo (`PayloadSpec`: `fmt`, `start`, `strip_suffix`, `role`, `direction`/`peer` ou `[payload_defaults]` da família); localização só por template, sem heurística; `well_formed` sob demanda (seções 6.3, 6.6) | Opus |
| 2026-10-08 | Regras de ligação de payload: anúncio por PID+TID+Δt, operação por MessageID/CorrelationID, response Ont/Smn pelo fim da operação ±16 ms (`event_conf=1`), fault pelo WFM-081 no mesmo ms; nunca por TID/ordem; callback CRS/IMS por GID (seções 6.5, 6.6, 8) | Opus |
| 2026-10-08 | `template.fts` + filtro na view `entry_text`: rastreio Verbose (sev 5) fora do FTS; volume Verbose medido e metas do M6 fixadas (seções 3, 5, 9.3a) | Opus |
| 2026-10-08 | Expectativa de payload por janela `level_window` (nível corrente ≥ `MessageLogLevel`), não pelo `LogLevel` configurado; Verbose não é necessário para payload (seção 6.6) | Opus |
| 2026-10-08 | Envelope: TID ausente é comum em Info (logger nativo); recuos de `ts` até 2,1 s no sensitive (seções 5, 6.1) | Opus |
| 2026-10-08 | **Proposta, aceita em 2026-10-09 (U12):** bancos `LocalPersistenceStore/*.db3` (fila/cache de mensagens por adapter: estado, tipo, MessageId, instante, corpo serializado em binário) fora da 1.0, listados como "não indexado". Pós-1.0 (P3), fonte opcional por caso: leitura somente leitura de cópia temporária, tabela `queue_item(collection_group, cache, item_id, message_id, msg_type, status, ts, size)` sem o corpo; painel "fila pendente/não enviada" e ligação ao evento por `message_id` (`entry_key msgid`); corpo nunca indexado e só decodificado sob demanda se o formato for documentado. Escopo segue U4–U11; sensível por padrão (seções 9.1a, 12) | Opus |
| 2026-10-08 | Tarefa 1.2 conferida no código: regex do `oaserr` corrigida (nível e TID opcionais, processo `\S+?` sem exigir `_<pid>`; a anterior não casava 25 de 63 linhas com data nos 6 `oasErrLog`); nível numérico 1..8 na ordem da regex; contrato "decisão por prefixo da linha"; `detect_envelope` com empate para o primeiro registrado (seção 6.1) | Opus |
| 2026-10-08 | `Record` ganha `nbytes`, `process` e `orphan` (com default); `RecordGrouper` (feed/finish) e `parse_bytes`; `iter_records` lê em pedaços de 1 MiB, não faz seek (stream já em `start_off`) e devolve o último registro com `partial=True` para retomada (seção 6.2) | Opus |
| 2026-10-08 | Decisão: logs UTF-16 com BOM (67 de 130 classificados como `plain` nas amostras; instaladores) são transcodificados para UTF-8 na leitura (tarefa 1.6), tratados como `final` e sem retomada incremental; até lá, `source.status='skipped'` com motivo (seção 6.1) | Opus |
| 2026-10-08 | `.hypothesis/` no `.gitignore` (banco de exemplos do hypothesis, só dev) | Opus |
| 2026-10-09 | Tarefa 2.1 (motor de templates): esquema TOML 1 documentado em `adapters/_schema.md` (+ `_example.toml`, não carregado); `process` sai do TOML de templates (fica no `families.toml`); novos atributos `text` (busca exata, antes das regex), `fields` (tipos `str/int/hex/guid/float/list`), `keys` com tipo de chave, `stream` (`normal/sensitive/any`), `sev_field`+`sev_map`, `aliases`, `examples`/`counter_examples`, `cont` com opções e `line_regex`, `kv_colon`/`kv_dash`; bibliotecas `_*` sem `extends`; `[[operation]]`/`[[sensitive_pair]]` livres, só com códigos conferidos. API: `Catalog`/`load_catalog` (substitui `load_all`), `TemplateSet.classify(record) -> TemplateMatch` (substitui `TplMatch`; nunca lança; `tpl_id` provisório até `bind_ids`), erros tipados. Regras de segurança das regex e limite de 4.096 caracteres casados. Despacho por prefixo/alternativas/1º caractere com memo (seção 6.3). `parse/signature.py` definido (seção 6.7) | Opus |
| 2026-10-09 | Tarefa 2.5 (severidade): `infer_severity(match, record, codes)` em `parse/severity.py` (6.4, `TplMatch` -> `TemplateMatch`); tabela aditiva `bucket_sev` (+ índice de cobertura `bucket_sev_minute`) e migração 2 com preenchimento a partir de `bucket`; `query.histogram` a usa para `sev`/`source` sem `tpl`/`ts` (seção 5). Decidido pela sessão principal para evitar uma rodada de Opus; revisar | Sonnet (sessão principal) |
| 2026-10-09 | Validador de regex dos templates redesenhado após 3 rodadas de revisão (ReDoS): lista branca + análise de pontos de escolha com conjuntos exatos de code points + polinômio de caminhos limitado por `MAX_BACKTRACK_PATHS` (substitui `MAX_DOT_REPEATS`, `MAX_ADJACENT_UNITS`, `MAX_ADJACENT_REPEATS`); lookarounds e preguiçosos analisados, não ignorados; modelo de ameaça explícito; sonda de ataque do catálogo em processo filho com timeout nos testes; janela de 4.096 mantida; sem sonda na carga nem contabilidade de custo em runtime (seção 6.3) | Opus |
| 2026-10-09 | Validador de regex, rodada 4 de revisão: o trabalho do corpo de um lookaround (reexecutado a cada tentativa da repetição anterior, aninhamento incluído) entra no polinômio antes de qualquer discriminação pelo próximo caractere; garantia da seção 6.3 e de `_schema.md` reescrita com precisão | Sonnet (sessão principal) |
| 2026-10-09 | Validador de regex, rodada 5 (decisão final): troca de estratégia para **limitar o dano por construção**: orçamento de trabalho em passos (`MAX_REGEX_STEPS` = 2²²) avaliado na janela do template, com alternativas sempre somando trabalho e lookarounds sem discriminação antes deles (substitui `MAX_BACKTRACK_PATHS` e o "grau ≤ 1"); atributo `window = N` (128..4.096, padrão 4.096) declarado por 19 templates do catálogo; texto casado cortado na 1ª quebra de linha; sondas e fuzz exigem < 50 ms na janela (seção 6.3) | Opus |
| 2026-10-09 | Validador de regex, rodada 6: casamento em texto maior que a janela (borda descartada, `$`/lookahead nunca casam texto cortado, linhas de exatamente a janela não são cortadas); `MAX_REGEX_STEPS` = 2²¹ recalibrado com o pior fator observado (~19 ns/passo); 18 janelas do catálogo reduzidas (a maior aceita) e SMN-S016 passa a declarar 2.320 (seção 6.3) | Sonnet (sessão principal) |
| 2026-10-09 | Validador de regex, rodada 7: semântica exata "o template só se aplica a mensagens que cabem na janela" (remove o casamento em texto cortado e os casos especiais de `$`/lookahead); limites por lista branca (classe ≤ 32 itens, capturas com profundidade ≤ 3 e ≤ 24 por regex) no lugar de modelar fatores constantes; testes sem dependência de velocidade absoluta (limiar de catástrofe 0,25 s) (seção 6.3) | Sonnet (sessão principal) |
| 2026-10-09 | Validador de regex, rodada 8: `check_examples` aplica a `other` a mesma normalização (`strip`) e a mesma regra de caber na janela antes de acusar conflito; `_cont_lines` descarta a linha cortada pelo limite de 256 KiB (sem capturas parciais); texto desta seção e de `_schema.md` sem resquícios do casamento em texto cortado | Sonnet (sessão principal) |
| 2026-10-10 | M4: contrato dos derivadores em `derive/base.py`; derivação por **cadeia de rotação** (`.log` + `.log.N`; checkpoint, estado e assinatura por cadeia em `meta['derive.<stream>.<cadeia>']`) porque o stream do BLTHost reúne arquivos independentes; recargas de mapa do Wfm logam `closed` duas vezes (duplicata em até 1 s não reinicia a recarga); `fail` visto vence `truncated`; rotas de eventos e de estatísticas como `GET` (seção 10) | Sessão principal (decisões baseadas em medições nas amostras) |
| 2026-10-10 | M5: correlação por collection group, recalculada quando um stream do grupo muda (assinatura em `meta['correlate.<grupo>']`, SAVEPOINT por grupo, apaga e recria só os links do próprio tipo); `[[sensitive_pair]]` ganha `key`, `order` (`after`\|`any`) e `max_dt_ms`; nova tabela de regras `[[link_rule]]` (`kind`, `a`, `b`, `key`, `scope`, `max_dt_ms`, `window_only`, confiança 0 só por janela); pareamento Ont/Smn pelo MessageID dentro do XML fica no M6 | Sessão principal (especificação já em 8; formato das regras definido antes dos agentes) |
| 2026-10-10 | Decisão U17: primeira release 0.1.0; "1.0" nos documentos = escopo da 0.1.0; 1.0.0 só com maturidade | Jefferson (decisão), sessão principal (registro) |
| 2026-10-10 | M6 (esquema v3): `payload.reason` (motivo do órfão) e `ix_payload_ref` (migração aditiva guardada por `PRAGMA table_info`); chaves de `meta` documentadas na seção 5 (`entries_epoch`, `payload_extraction_version[.<source_id>]`, assinaturas `correlate.`/`correlate.links.`/`payloads.` por grupo, `derive.<stream>.<cadeia>`) | Sessão principal (decisões do PR #24) |
| 2026-10-10 | M6 (ligação de payload como construída): regra do **candidato único** (vários candidatos ou unicidade não provada em `MAX_SCAN` = 256 não ligam; motivos `ambiguous`, `scan_limit`, `too_many_payloads` com `MAX_PAYLOADS` = 300.000 por processo); links de anúncio antigos removidos na recomputação; correlação de payload por collection group com SAVEPOINT e assinatura, depois de `operations` e da correlação (seções 6.5, 6.6, 8) | Sessão principal (achados da revisão cruzada do PR #24) |
| 2026-10-10 | M6 (extração): reextração única por fonte (`PAYLOAD_EXTRACTION_VERSION` em `meta['payload_extraction_version.<source_id>']`) para casos anteriores ao M6; `flags & 64` só com linha de `payload` extraída; extração de chaves do payload para `entry_key` **adiada** (fora da 0.1.0); `payload/pretty.py` com tokenizadores lineares, orçamento de saída e `check_xml` estrito (seção 6.6) | Sessão principal (achados da revisão cruzada do PR #24) |
| 2026-10-10 | API de payloads como construída: `GET /api/entries/{id}/payloads`, `GET /api/payloads/{id}?format=pretty\|raw`, `GET /api/payloads/page` (**`GET`**, não `POST`; keyset, `orphan`, `event`, `q`), `payload_count` no registro e folha `{"payload": {roles, fmt, orphan, ...}}` da árvore de filtros; rotas `/api/knowledge/error/{family}/{code}`, `/param` e `/adapter` não construídas (seção 10) | Sessão principal (registro do que a `main` implementa) |
| 2026-10-10 | Checagem de `Origin` documentada para métodos que alteram estado (`POST`/`DELETE` na `main`; `PUT`/`PATCH` com o PR #25 do M7) e formato do erro de validação das rotas (seções 10 e 12) | Sessão principal (registro do que a `main` implementa) |

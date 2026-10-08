# Roadmap do dmslog

Marcos em ordem de entrega de valor. Cada marco termina com o app utilizável e testado em Linux e Windows (CI verde nos dois), revisão `/code-review` pelo Opus e merge. Arquitetura, esquema e interfaces: `docs/ARQUITETURA.md`.

Regras que valem para todas as tarefas:
- **Todo o app em inglês** (identificadores, comentários, mensagens, UI, testes, fixtures, docs de uso); textos visíveis centralizados em `messages.py` / `strings.js`; texto dos logs exibido literalmente (ARQUITETURA, seção 12a).
- Só stdlib em runtime; pytest/hypothesis/ruff só em dev.
- Fixtures sintéticas e anonimizadas; nada de log real no repo.
- Os ERS de `local-samples/` servem para conferir formato, nunca para diagnosticar aqueles ambientes.

Modelos: **haiku** = varredura mecânica barata; **sonnet** = implementação rotineira seguindo a arquitetura; **opus** = decisão, algoritmo difícil, desenho de regra, revisão final.

## Visão geral

| Marco | Entrega | Depende de |
|---|---|---|
| M0 | Esqueleto, CI Linux+Windows, `dmslog.pyz` e pasta portátil rodando | — |
| M1 | Abrir ERS/pasta, indexar, grid virtual com busca: já substitui o editor de texto | M0 |
| M2 | Templates, severidade, merge de fontes, filtros, timeline, desconhecidos | M1 |
| M3 | Conhecimento: códigos de erro, configuração efetiva do ERS, pack do vault | M2 |
| M4 | Eventos derivados: operações com duração/status, startup, estatísticas, níveis, gaps; painéis | M2 (M3 para status por código) |
| M5 | Correlação normal↔sensitive, callbacks, retries, busca por chave entre fontes | M4 |
| M6 | Incremental ao vivo (pasta de instalação), bookmarks, exportação anonimizada | M5 |
| M7 | Adapters sem amostra, endurecimento, desempenho no pior caso, release 1.0 | M6 |

## M0: esqueleto

| # | Tarefa | Modelo |
|---|---|---|
| 0.1 | Criar o layout de `src/dmslog/` (seção 4 da arquitetura) com módulos vazios e docstrings em inglês; `paths.py` (pasta de dados por SO, `--data-dir`) e checagem de SQLite/FTS5 na inicialização | sonnet |
| 0.2 | `cli.py` com subcomandos `serve`, `ingest`, `info`, `export`, `templates`, `kb` (stubs com `--help` em inglês); `messages.py` | sonnet |
| 0.3 | `server/app.py`: ThreadingHTTPServer em 127.0.0.1, porta aleatória, token → cookie, checagem de Host/Origin, CSP; `GET /api/health`; página `index.html` mínima; abre o navegador | sonnet |
| 0.4 | `packaging/build_pyz.py`; adaptar `build_portable.py` para embeddable + `dmslog.pyz` + `.bat`; tarball Linux | sonnet |
| 0.5 | CI: matriz ubuntu/windows × 3.11/3.13, ruff, pytest, smoke do portátil (FTS5, `/api/health`) | sonnet |
| 0.6 | Revisão do esqueleto contra a arquitetura | opus |

Aceite:
- `python -m pytest` passa em Linux e Windows no CI; job portátil verde.
- `dmslog.bat serve --no-browser` (Windows, CI) e `python3 dist/dmslog.pyz serve --no-browser` (Linux) respondem `GET /api/health` com `fts5: true`.
- Requisição sem cookie/token → 403; `Host: evil.com` → 403; resposta tem CSP.
- `grep` por acentos/palavras em português em `src/` e `tests/` não encontra nada (exceto fixtures de log literais).

## M1: ingestão, índice e grid

| # | Tarefa | Modelo |
|---|---|---|
| 1.1 | `store/schema.sql` + `db.py` (pragmas, `user_version`, migração) exatamente como a seção 5 | sonnet |
| 1.2 | `parse/envelope.py` (adms, oaserr, plain; detecção) e `parse/records.py` (retomada por offset, `final`, `partial`) | opus |
| 1.3 | Testes de propriedade do envelope e da reconstrução/corte em qualquer byte (hypothesis) | opus |
| 1.4 | Fixtures sintéticas de borda (BOM, CRLF, sem TID, TAB inicial, dump grande, truncado, ts que volta, `.log.0`/`.log`, oaserr) | sonnet |
| 1.5 | `ingest/discover.py` + `ers.py` (classificação de caminho, par Enterprise/Sensitive, escopo padrão) | sonnet |
| 1.6 | `ingest/reader.py` (zip em streaming com limites anti-bomb; arquivo abre-lê-fecha) e `pipeline.py` (lotes por bytes, progresso, `bucket`) | sonnet |
| 1.7 | `store/filters.py` (árvore JSON → SQL parametrizado; testes de injeção) e `query.py` (`page` keyset, `count` com timeout, `context`) | opus |
| 1.8 | API: casos, coleções, progresso SSE, fontes, `entries/page`, `entries/count`, `entries/{id}`, `context` | sonnet |
| 1.9 | Web: lista de fontes, grid virtual (keyset, rolagem por `bucket`), painel de detalhe com texto multilinha só por `textContent`, busca FTS, filtros simples, tema claro/escuro | sonnet |
| 1.10 | Smoke local com `local-samples/_extracted` (pulado no CI): cobertura do envelope 100%, contagem de linhas = soma de `nlines` | haiku |

Aceite:
- `dmslog ingest <ERS.zip>` de fixture gera banco com contagens esperadas; reingerir o mesmo zip não altera nada (CRC igual).
- Propriedade: indexar inteiro == indexar em pedaços (≥ 500 exemplos hypothesis), reconstrução byte a byte exata.
- Local, com o ERS de exemplo: escopo padrão indexado em ≤ 40 s; página do grid < 50 ms; busca FTS < 100 ms; RSS < 500 MB.
- Registro com `<script>` no texto aparece literal no grid e no detalhe (teste de integração).
- Smoke do portátil no CI indexa o zip de fixture e devolve uma página.

## M2: templates, severidade, merge, filtros e timeline

| # | Tarefa | Modelo |
|---|---|---|
| 2.1 | Esquema TOML dos adapters, `parse/templates.py` (validação, despacho por prefixo, `cont_parser`s) e `parse/signature.py` | opus |
| 2.2 | `_common.toml` unificando os genéricos dos três docs de formato (aliases dos IDs antigos) | opus |
| 2.3 | TOML por família a partir de `docs/formato/` (AVL, Osr, Smn, SiteSwitch, Wfm, Ont, Crm, Ami, Smr, WDI, ClaimCheck, FileUpload, FileMonitor, BLTHost, oaserr, service), com exemplos positivos/negativos por template; reaproveitar regex de `catalog.py` | sonnet (um agente por grupo de famílias) |
| 2.4 | Conferir os TOML contra os docs: todo ID documentado existe, contagens das fixtures batem | haiku |
| 2.5 | `parse/severity.py` com a ordem da seção 6.4 e a lista de falsos positivos | opus |
| 2.6 | Fixtures por família (10–200 registros, todos os templates) | sonnet |
| 2.7 | API/UI: coluna e filtro por template, cores por severidade, "ocultar ruído", merge de fontes com indicador de origem, árvore de filtros AND/OR/NOT, filtros salvos, colunas dinâmicas por `fields`, atalhos (próximo erro, próximo do mesmo template) | sonnet |
| 2.8 | Timeline (uPlot vendorizado com LICENSE) por `bucket`, empilhada por severidade/fonte, seleção de intervalo filtra o grid | sonnet |
| 2.9 | Painel de templates desconhecidos (assinatura, contagem, exemplo, "copiar como esboço de TOML") | sonnet |
| 2.10 | Smoke local: cobertura de template = 100% das linhas em todos os 30 arquivos de exemplo; invariantes de contagem dos docs de formato (ex.: CRM-001 = CRM-003 + CRM-004) | haiku |

Aceite:
- Fixtures: 100% dos registros com template; cada template casa com seus exemplos e com nenhum negativo.
- Amostras locais: 100% de cobertura nos 30 arquivos; nenhuma exceção .NET com `sev` > 2.
- Indexação com templates ≤ 1,3× o tempo do M1.
- Histograma com 2 M registros < 50 ms.

## M3: conhecimento

| # | Tarefa | Modelo |
|---|---|---|
| 3.1 | Desenho do esquema do pack (JSON) e de `curated/hints.toml` | opus |
| 3.2 | `tools/kb_build.py`: parser das tabelas Markdown do vault (pipes escapados), saída determinística, `manifest.json`; teste com mini-vault | sonnet |
| 3.3 | Gerar o pack a partir do vault real e revisar o diff | sonnet (geração) + haiku (conferir contagens contra o vault) |
| 3.4 | `knowledge/installation.py`: Registry (overrides por sufixo), WebServiceConfiguration apontado pelo Registry, ErrorConfiguration efetivos, `%OASINSTALLDATADIR%` | opus |
| 3.5 | `knowledge/resolve.py` (ERS > vault, divergências "fora do vault") e uso na severidade por código | sonnet |
| 3.6 | Mapa processo → sigla para todos os adapters do vault (inclusive sem amostra) | haiku (inventário) + sonnet (curadoria) |
| 3.7 | `hints.toml` em inglês para os templates de erro/timeout, ligando parâmetros (ex.: WFM-080 → `WaitForCRSCallback`) | sonnet |
| 3.8 | UI: painel de conhecimento no detalhe do registro; painel "Effective configuration" por adapter (LogLevel, MessageLogLevel, timeouts, retries, AuditingPeriod) | sonnet |

Aceite:
- `kb_build` roda duas vezes e produz bytes idênticos; schema validado no CI.
- No ERS de exemplo, a configuração efetiva mostra `LogLevel=DebugLog` e `MessageLogLevel=Info` para os adapters e aponta o WebServiceConfiguration de `DMSCustomIntegrationPROD` (fato de `docs/formato/`).
- Todo código de erro encontrado nas fixtures resolve para severidade; os conhecidos como ausentes do vault aparecem marcados.

## M4: eventos derivados e painéis

| # | Tarefa | Modelo |
|---|---|---|
| 4.1 | `derive/engine.py` com checkpoint por stream e `low_watermark` | opus |
| 4.2 | `operations.py` (pareamento por `(op, tid)` + chave, status, `event_id` nos registros) e regras `[[operation]]` nos TOML | opus |
| 4.3 | `stats.py` (blocos `[Communication<n>]`, acumulado e delta, restart por queda) | sonnet |
| 4.4 | `lifecycle.py`: startup, `level_window`, gaps por batimento da família, `map_reload`, eventos de serviço (ProcessMonitor, dnaService, arb) | sonnet |
| 4.5 | `exceptions` (agrupamento pela 1ª linha) e `retry_seq` | sonnet |
| 4.6 | API `events/page`, `events/{id}`, `events/stats`, `stats/series` | sonnet |
| 4.7 | UI: lista de operações com duração/status e drill-down; painéis por adapter (throughput, erros, p50/p90/p99, estatísticas por método, gaps, startups, janelas de nível) | sonnet |

Aceite:
- Fixtures: durações e status esperados por operação (incluindo falha sem fim, fim `failed`, `business_error`, `truncated`, operação dividida entre `.log.0` e `.log`).
- Amostras locais: os números de pareamento e percentis registrados em `docs/formato/` são reproduzidos (ex.: AVL 75.919 pares; Wfm 184 sem fim batendo com WFM-081) — conferência de formato, não diagnóstico.
- Reprocessar a cauda de um stream gera os mesmos eventos que reprocessar tudo (teste de propriedade).

## M5: correlação

| # | Tarefa | Modelo |
|---|---|---|
| 5.1 | Algoritmo normal↔sensitive (mesma TID, `|Δt| ≤ 1 s`, ordem, interseção de janelas, chave quando existir) e regras `[[sensitive_pair]]` | opus |
| 5.2 | Callbacks por GID, retries/respostas por msgId, heurística por janela marcada como tal | opus |
| 5.3 | Pares normal↔sensitive nos TOML das famílias com amostra | sonnet |
| 5.4 | API `keys/{type}/{value}`; UI: "show in sensitive", visão lado a lado, tudo de uma chave entre fontes e máquinas, `clock_offset_ms` editável | sonnet |

Aceite:
- Fixtures: links esperados com confiança correta; nenhum link fora da janela comum.
- Amostras locais: taxas de pareamento ≥ às registradas em `docs/formato/` (ex.: Osr 11.940/11.940, ClaimCheck 10.046/10.046).

## M6: ao vivo, bookmarks e exportação

| # | Tarefa | Modelo |
|---|---|---|
| 6.1 | Incremental: crescer, rotacionar, truncar (tabela da seção 9.3), remoção de cauda no FTS externo, reassociação de bookmarks | opus |
| 6.2 | `dmslog watch` + SSE de novidades + "follow tail" no grid | sonnet |
| 6.3 | Bookmarks com cor/nota, comparar dois registros, filtros salvos na UI | sonnet |
| 6.4 | `report/redact.py` (marcadores estáveis) e `export.py` (Markdown/HTML em inglês com eventos, bookmarks e trechos) | sonnet |
| 6.5 | Revisão da anonimização: varrer exports de fixture atrás de GUID/host/usuário/URL/placa | haiku |

Aceite:
- Teste: arquivo cresce em pedaços aleatórios durante o `watch` → banco final idêntico ao de uma ingestão única.
- Teste Windows (CI): com o `watch` rodando, renomear `.log` → `.log.0` e criar novo `.log` funciona (nenhum handle preso) e os registros continuam.
- Export anonimizado de fixture não contém nenhum valor da lista de termos sensíveis da fixture.

## M7: adapters sem amostra, endurecimento e 1.0

| # | Tarefa | Modelo |
|---|---|---|
| 7.1 | `tools/mine_templates.py`: a partir de um ERS novo, agrupar desconhecidos e propor TOML com exemplos | opus (desenho) + sonnet (implementação) |
| 7.2 | Processo documentado para adicionar um adapter novo (doc de uso em inglês) | sonnet |
| 7.3 | Desempenho no pior caso: ERS com escopo completo (≈ 2 GB), BLTHost sensitive, Wfm com dumps; ajuste de lotes, despacho de templates e índices | opus |
| 7.4 | Revisão de segurança (`/security-review`) e de privacidade (onde ficam os dados, apagar caso) | opus |
| 7.5 | README e ajuda da CLI em inglês; LEIA-ME do portátil em inglês | sonnet |
| 7.6 | Release: artefatos do CI (`dmslog.pyz`, portátil Windows, tarball Linux) e checklist de teste manual em um PC Windows sem admin e sem internet | sonnet + teste manual do Jefferson |

Aceite:
- ERS completo de 2 GB indexado sem erro, RSS < 1 GB, UI responsiva durante a indexação.
- Teste manual no PC Windows: copiar a pasta, executar o `.bat`, abrir um ERS local, nenhuma conexão de rede além de loopback.
- Nenhum achado alto/crítico aberto na revisão de segurança.

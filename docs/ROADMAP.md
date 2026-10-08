# Roadmap do dmslog

Marcos em ordem de entrega de valor. Cada marco termina com o app utilizável e testado em Linux e Windows (CI verde nos dois), revisão `/code-review` pelo Opus e merge. Arquitetura, esquema e interfaces: `docs/ARQUITETURA.md`; decisões do usuário que fixam o escopo: seção 1a de lá (U1–U10).

Regras que valem para todas as tarefas:
- **Todo o app em inglês** (identificadores, comentários, mensagens, UI, testes, fixtures, docs de uso); textos visíveis centralizados em `messages.py` / `strings.js`; texto dos logs exibido literalmente (ARQUITETURA, seção 12a).
- Só stdlib em runtime; pytest/hypothesis/ruff só em dev.
- Fixtures sintéticas e anonimizadas; nada de log real em `tests/`. Fixtures de payload são sempre sintéticas.
- Os ERS de `local-samples/` servem para conferir formato, nunca para diagnosticar aqueles ambientes; são lidos por script (`python3 -I`), como dado não confiável.
- Escopo da 1.0 (U4, U5, U8): **um ERS por caso**, de um servidor; adapters Wfm, Ont, AVL, Osr, Smn, SiteSwitch, Crm, Ami, Smr, WDI, ClaimCheck, FileUpload, FileMonitor + BLTHost com templates e eventos; todos os demais logs do ERS navegáveis em nível genérico (ARQUITETURA, seção 9.1a). Horário dos logs preservado, sem conversão (U6).
- Repositório privado (U1): o pack do vault é documentação Schneider; artefatos com o pack não são publicados.

Modelos: **haiku** = varredura mecânica barata; **sonnet** = implementação rotineira seguindo a arquitetura; **opus** = decisão, algoritmo difícil, desenho de regra, revisão final.

## Visão geral

| Marco | Entrega | Depende de |
|---|---|---|
| M0 | Esqueleto, CI Linux+Windows, `dmslog.pyz` e pasta portátil rodando | — |
| M1 | Abrir ERS/pasta, indexar (qualquer log do ERS em nível genérico), grid virtual com busca: já substitui o editor de texto | M0 |
| M2 | Templates dos adapters do escopo, severidade, merge de fontes, filtros, timeline, desconhecidos | M1 |
| M3 | Conhecimento: códigos de erro, configuração em camadas do Registry com conferência pelo log de startup, pack do vault | M2 |
| M4 | Eventos derivados: operações com duração/status, startup, estatísticas, níveis, gaps, saltos de relógio; painéis | M2 (M3 para status por código) |
| M5 | Correlação normal↔sensitive, callbacks, retries, busca por chave entre as fontes do caso | M4 |
| M6 | **Payloads** (request/response): descoberta de formato, extração, visualização XML/JSON, ligação ao evento | M5 + **amostras Verbose** em `local-samples/` (bloqueio externo, só para 6.1–6.4) |
| M7 | Incremental ao vivo (pasta de instalação), bookmarks, exportação `.txt` anonimizada para chamado | M5 (payload na exportação: M6) |
| M8 | Endurecimento, desempenho no pior caso (inclusive ERS Verbose), release 1.0 | M6, M7 |
| Pós-1.0 | P1: templates/eventos para os demais logs do ERS. P2: múltiplos ERS por caso, correlação entre máquinas, ajuste de relógio | M8 |

M6 e M7 podem andar em paralelo: o M7 não espera as amostras Verbose.

## M0: esqueleto

| # | Tarefa | Modelo |
|---|---|---|
| 0.1 | Criar o layout de `src/dmslog/` (seção 4 da arquitetura) com módulos vazios e docstrings em inglês; `paths.py` (`%LOCALAPPDATA%\dmslog` / `${XDG_DATA_HOME:-~/.local/share}/dmslog`, `--data-dir`) e checagem de SQLite/FTS5 na inicialização | sonnet |
| 0.2 | `cli.py` com subcomandos `serve`, `ingest`, `info`, `export`, `templates`, `kb` (stubs com `--help` em inglês); `messages.py` | sonnet |
| 0.3 | `server/app.py`: ThreadingHTTPServer em 127.0.0.1, porta aleatória, token → cookie, checagem de Host/Origin, CSP; `GET /api/health`; página `index.html` mínima; abre o navegador padrão | sonnet |
| 0.4 | `packaging/build_pyz.py`; adaptar `build_portable.py` para embeddable + `dmslog.pyz` + `.bat`; tarball Linux | sonnet |
| 0.5 | CI: matriz ubuntu/windows × 3.11/3.13, ruff, pytest, smoke do portátil (FTS5, `/api/health`) | sonnet |
| 0.6 | Revisão do esqueleto contra a arquitetura | opus |

Aceite:
- `python -m pytest` passa em Linux e Windows no CI; job portátil verde.
- `dmslog.bat serve --no-browser` (Windows, CI) e `python3 dist/dmslog.pyz serve --no-browser` (Linux) respondem `GET /api/health` com `fts5: true`.
- Requisição sem cookie/token → 403; `Host: evil.com` → 403; resposta tem CSP.
- Pasta de dados padrão é `%LOCALAPPDATA%\dmslog` no Windows e `~/.local/share/dmslog` no Linux (teste por SO).
- `grep` por acentos/palavras em português em `src/` e `tests/` não encontra nada (exceto fixtures de log literais).

## M1: ingestão, índice e grid

| # | Tarefa | Modelo |
|---|---|---|
| 1.1 | `store/schema.sql` + `db.py` (pragmas, `user_version`, migração) exatamente como a seção 5, inclusive `payload`, `config_param` em camadas e `config_observed` (vazias até M3/M6) | sonnet |
| 1.2 | `parse/envelope.py` (adms, oaserr, plain; detecção; registro extensível de envelopes) e `parse/records.py` (retomada por offset, `final`, `partial`) | opus |
| 1.3 | Testes de propriedade do envelope e da reconstrução/corte em qualquer byte (hypothesis) | opus |
| 1.4 | Fixtures sintéticas de borda (BOM, CRLF, sem TID, TAB inicial, dump grande, truncado, ts que volta 3 ms, retrocesso de 1 h, `.log.0`/`.log`, oaserr) | sonnet |
| 1.5 | `families.toml` (padrão de caminho → família, envelope, escopo padrão) + `ingest/discover.py` + `ers.py` (classificação dirigida por dados, par Enterprise/Sensitive, um ERS por caso, escopo padrão; demais logs listados e indexáveis em nível genérico) | sonnet |
| 1.6 | `ingest/reader.py` (zip em streaming com limites anti-bomb; arquivo abre-lê-fecha) e `pipeline.py` (lotes por bytes, progresso, `bucket`) | sonnet |
| 1.7 | `store/filters.py` (árvore JSON → SQL parametrizado; testes de injeção) e `query.py` (`page` keyset, `count` com timeout, `context`) | opus |
| 1.8 | API: casos, coleções (409 para segunda coleta), progresso SSE, fontes, `entries/page`, `entries/count`, `entries/{id}`, `context` | sonnet |
| 1.9 | Web: lista de fontes, grid virtual (keyset, rolagem por `bucket`), painel de detalhe com texto multilinha só por `textContent`, busca FTS, filtros simples, tema claro/escuro; horário formatado em UTC (= como está no log); alvo Edge/Chrome | sonnet |
| 1.10 | Smoke local com `local-samples/_extracted` (pulado no CI): cobertura do envelope 100%, contagem de linhas = soma de `nlines`; um log fora do escopo padrão (NMC ou PSengine) indexa e abre | haiku |

Aceite:
- `dmslog ingest <ERS.zip>` de fixture gera banco com contagens esperadas; reingerir o mesmo zip não altera nada (CRC igual).
- Propriedade: indexar inteiro == indexar em pedaços (≥ 500 exemplos hypothesis), reconstrução byte a byte exata.
- Local, com o ERS de exemplo: escopo padrão indexado em ≤ 40 s; página do grid < 50 ms; busca FTS < 100 ms; RSS < 500 MB.
- Registro com `<script>` no texto aparece literal no grid e no detalhe (teste de integração).
- O horário exibido no grid é idêntico ao texto do log, com o fuso do PC de teste diferente de UTC (teste de UI/integração).
- Adicionar um segundo ERS (outro GUID) ao caso devolve 409; a outra metade do par é aceita.
- Smoke do portátil no CI indexa o zip de fixture e devolve uma página.

## M2: templates, severidade, merge, filtros e timeline

| # | Tarefa | Modelo |
|---|---|---|
| 2.1 | Esquema TOML dos adapters (inclusive atributo `payload` provisório), `parse/templates.py` (validação, despacho por prefixo, `cont_parser`s) e `parse/signature.py` | opus |
| 2.2 | `_common.toml` unificando os genéricos dos três docs de formato (aliases dos IDs antigos) e tabela de aliases de nomes de `[Configuration]` (para o M3) | opus |
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
- Família `UNKNOWN` (ex.: `CustomAdapterChangeIncidentDevice`) indexa com `_common` e aparece no painel de desconhecidos, sem erro.
- Indexação com templates ≤ 1,3× o tempo do M1.
- Histograma com 2 M registros < 50 ms.

## M3: conhecimento

| # | Tarefa | Modelo |
|---|---|---|
| 3.1 | Desenho do esquema do pack (JSON, com `notice` de documentação Schneider/repo privado no `manifest.json`) e de `curated/hints.toml` | opus |
| 3.2 | `tools/kb_build.py`: parser das tabelas Markdown do vault (pipes escapados), saída determinística, `manifest.json`; teste com mini-vault | sonnet |
| 3.3 | Gerar o pack a partir do vault real, revisar o diff e commitar (repo privado, U1) | sonnet (geração) + haiku (conferir contagens contra o vault) |
| 3.4 | `knowledge/installation.py`: Registry em camadas pela regra 7.2.1 (agrupamento por `Product/@Name`, camada por sufixo, precedência só em chave sobreposta, `<Key>` como lista, empate = `ambiguous`), WebServiceConfiguration e ErrorConfiguration efetivos, normalização de `%OASINSTALLDATADIR%` | opus |
| 3.5 | `config_observed` a partir das linhas `[Configuration] ... Type: [ConfigurationRegistry]` do startup; comparação resolvido × observado por nome normalizado + aliases | sonnet |
| 3.6 | `knowledge/resolve.py` (ERS > vault, divergências "fora do vault") e uso na severidade por código | sonnet |
| 3.7 | Mapa processo → sigla para os adapters do escopo (U8) | haiku (inventário) + sonnet (curadoria) |
| 3.8 | `hints.toml` em inglês para os templates de erro/timeout, ligando parâmetros (ex.: WFM-080 → `WaitForCRSCallback`) | sonnet |
| 3.9 | UI: painel de conhecimento no detalhe do registro; painel "Effective configuration" por adapter com camadas (arquivo de origem, `only`/`wins`/`overridden`/`ambiguous`), valor observado no log, divergências, e por operação se o payload é esperado (`LogLevel` × `MessageLogLevel`) | sonnet |

Aceite:
- `kb_build` roda duas vezes e produz bytes idênticos; schema validado no CI.
- Fixture de Registry: todos os casos da regra 7.2.1 (só uma camada, sobreposição -1/0/1/2, `<Key>`, empate, as duas formas de `%OASINSTALLDATADIR%`) dão o efetivo esperado.
- No ERS de exemplo (smoke local): `AdapterAmi` `WebServiceConfiguration` efetivo = camada `_2` (`DMSCustomIntegrationPROD`), `CreatedMeterReadings_ErrorConfigurationPath` = camada `_1_Custom`, `AdapterFileUpload` `FileUploadInterval` = `00:01:00`; todos batem com `config_observed`; nenhuma divergência resolvido × observado nos 13 adapters, salvo as explicadas por alias faltante (que viram tarefa).
- A configuração efetiva mostra `LogLevel=DebugLog` e `MessageLogLevel=Info` para os adapters e marca "payload not expected" em todas as operações (fato do ERS de exemplo).
- Todo código de erro encontrado nas fixtures resolve para severidade; os conhecidos como ausentes do vault aparecem marcados.

## M4: eventos derivados e painéis

| # | Tarefa | Modelo |
|---|---|---|
| 4.1 | `derive/engine.py` com checkpoint por stream e `low_watermark` | opus |
| 4.2 | `operations.py` (pareamento por `(op, tid)` + chave, status, `event_id` nos registros) e regras `[[operation]]` nos TOML | opus |
| 4.3 | `stats.py` (blocos `[Communication<n>]`, acumulado e delta, restart por queda) | sonnet |
| 4.4 | `lifecycle.py`: startup, `level_window`, gaps por batimento da família, `map_reload`, eventos de serviço (ProcessMonitor, dnaService, arb), `clock_jump` e `possible_clock_jump` (seção 5, Tempo) | sonnet |
| 4.5 | `exceptions` (agrupamento pela 1ª linha) e `retry_seq` | sonnet |
| 4.6 | API `events/page`, `events/{id}`, `events/stats`, `stats/series` | sonnet |
| 4.7 | UI: lista de operações com duração/status e drill-down; painéis por adapter (throughput, erros, p50/p90/p99, estatísticas por método, gaps, startups, janelas de nível); aviso de salto de relógio na timeline | sonnet |

Aceite:
- Fixtures: durações e status esperados por operação (incluindo falha sem fim, fim `failed`, `business_error`, `truncated`, operação dividida entre `.log.0` e `.log`).
- Fixture de horário de verão: retrocesso de 1 h gera `clock_jump`, a operação que o atravessa tem `detail.clock_jump=true`; lacuna de 1 h simultânea gera `possible_clock_jump`; nenhum `ts` alterado.
- Amostras locais: os números de pareamento e percentis registrados em `docs/formato/` são reproduzidos (ex.: AVL 75.919 pares; Wfm 184 sem fim batendo com WFM-081) — conferência de formato, não diagnóstico.
- Reprocessar a cauda de um stream gera os mesmos eventos que reprocessar tudo (teste de propriedade).

## M5: correlação

| # | Tarefa | Modelo |
|---|---|---|
| 5.1 | Algoritmo normal↔sensitive (mesma TID, `|Δt| ≤ 1 s`, ordem, interseção de janelas, chave quando existir) e regras `[[sensitive_pair]]` | opus |
| 5.2 | Callbacks por GID, retries/respostas por msgId, heurística por janela marcada como tal | opus |
| 5.3 | Pares normal↔sensitive nos TOML das famílias do escopo | sonnet |
| 5.4 | API `keys/{type}/{value}`; UI: "show in sensitive", visão lado a lado, tudo de uma chave entre as fontes do caso | sonnet |

Aceite:
- Fixtures: links esperados com confiança correta; nenhum link fora da janela comum.
- Amostras locais: taxas de pareamento ≥ às registradas em `docs/formato/` (ex.: Osr 11.940/11.940, ClaimCheck 10.046/10.046).

## M6: payloads

Situação: **não há amostra de payload** (no ERS de exemplo, `LogLevel=DebugLog` e `MessageLogLevel=Info` em todos os adapters). O Jefferson está coletando ERS com `LogLevel=Verbose` para `local-samples/`. As tarefas 6.1–6.4 ficam **bloqueadas até as amostras chegarem**; 6.5–6.7 podem começar antes, com fixtures sintéticas no formato provisório (ARQUITETURA, seção 6.6).

| # | Tarefa | Modelo | Bloqueio |
|---|---|---|---|
| 6.1 | **Descoberta de formato dos payloads** nas amostras Verbose, por script: onde aparecem (normal/sensitive, registro próprio ou continuação), marcadores de início/fim, SOAP/XML × JSON, uma linha × indentado, truncamento, ligação com o registro que anuncia e com a operação, chaves úteis (msgId, mRID, INC); também mensagens novas que só existem em Verbose. Saída: `docs/formato/payloads.md` e ajuste do esquema `payload` e do atributo `payload` dos TOML (registrar na seção 15 da arquitetura) | opus | amostras |
| 6.2 | Templates Verbose novos nas famílias do escopo (mineração com `tools/mine_templates.py` + revisão) | opus (desenho) + sonnet (TOML) | 6.1 |
| 6.3 | `parse/payload.py` (localização, offsets, formato, raiz, `well_formed`, `truncated`) e `derive/payloads.py` (direção, par request↔response, evento, chaves) | opus | 6.1 |
| 6.4 | Fixtures sintéticas de payload no formato descoberto, por família com payload; smoke local nas amostras Verbose | sonnet + haiku (varredura de vazamento nas fixtures) | 6.1 |
| 6.5 | `payload/pretty.py`: tokenizador XML tolerante (sem DTD/entidades, aceita truncado) e JSON com limite de tamanho; testes com malformados, `<!DOCTYPE>`, entidades, `<script>` | sonnet | — |
| 6.6 | API `entries/{id}/payloads`, `payloads/{id}`, `payloads/page`; filtro `payload` na árvore | sonnet | — (dados reais após 6.3) |
| 6.7 | UI: aba "Payload" no detalhe (formatado/cru, copiar, selo "sensitive"), par request↔response lado a lado, ir para a operação, lista de payloads por operação/direção, aviso de payload esperado e ausente | sonnet | — |

Aceite:
- Fixtures: todos os payloads localizados com offsets exatos; request e response ligados à operação certa; truncado e malformado exibidos crus com motivo, sem erro.
- Amostras Verbose locais: 100% dos payloads das operações com payload esperado são localizados e ligados a um evento (ou listados como órfãos com motivo); cobertura de template continua 100%.
- Segurança: `<!DOCTYPE>` com entidade externa ou expansão não é resolvida; `<script>` no payload aparece literal.
- Indexação de um ERS Verbose dentro da meta registrada em 6.1 (definida após medir o tamanho real).

## M7: ao vivo, bookmarks e exportação

| # | Tarefa | Modelo |
|---|---|---|
| 7.1 | Incremental: crescer, rotacionar, truncar (tabela da seção 9.3), remoção de cauda no FTS externo, reassociação de bookmarks | opus |
| 7.2 | `dmslog watch` + SSE de novidades + "follow tail" no grid | sonnet |
| 7.3 | Bookmarks com cor/nota, comparar dois registros, filtros salvos na UI | sonnet |
| 7.4 | `report/redact.py` configurável (categorias, termos do usuário, exceções; `redaction.toml`; regras por elemento XML/JSON para payload) e API/UI `/api/redaction` | opus (regras de payload) + sonnet |
| 7.5 | `report/export.py`: `.txt` no formato da seção 10.1 (UTF-8 sem BOM, CRLF, moldura ASCII, limite de tamanho, partes numeradas, legenda de anonimização, mapa local fora do `.txt`); botão "copy to clipboard"; `dmslog export` | sonnet |
| 7.6 | Revisão da anonimização: varrer exports de fixture atrás de GUID/host/usuário/URL/placa/e-mail/telefone, inclusive com payload | haiku |

Aceite:
- Teste: arquivo cresce em pedaços aleatórios durante o `watch` → banco final idêntico ao de uma ingestão única.
- Teste Windows (CI): com o `watch` rodando, renomear `.log` → `.log.0` e criar novo `.log` funciona (nenhum handle preso) e os registros continuam.
- `.txt` de fixture idêntico byte a byte ao esperado (CRLF, sem BOM, só ASCII fora das linhas de log).
- Export anonimizado de fixture não contém nenhum valor da lista de termos sensíveis da fixture; desligar uma categoria faz o valor dela aparecer; payload só entra com `include_payloads` e, quando entra, sai anonimizado por elemento.
- O `.txt` colado num campo de texto simples mantém a formatação (teste manual do Jefferson num chamado de teste).

## M8: endurecimento e 1.0

| # | Tarefa | Modelo |
|---|---|---|
| 8.1 | Desempenho no pior caso: ERS com escopo completo (≈ 2 GB), ERS Verbose com payloads, BLTHost sensitive, Wfm com dumps; ajuste de lotes, despacho de templates, índices e limite do FTS | opus |
| 8.2 | Revisão de segurança (`/security-review`) e de privacidade (onde ficam os dados, apagar caso, payloads, mapa de anonimização) | opus |
| 8.3 | Processo documentado para adicionar uma família nova (`families.toml` + TOML + `mine_templates`), preparando o P1 (doc de uso em inglês) | sonnet |
| 8.4 | README e ajuda da CLI em inglês; LEIA-ME do portátil em inglês (com aviso de uso interno/privado do pack) | sonnet |
| 8.5 | Release: artefatos do CI (`dmslog.pyz`, portátil Windows, tarball Linux) em release privada, e checklist de teste manual em um PC Windows sem admin e sem internet, no Edge e no Chrome | sonnet + teste manual do Jefferson |

Aceite:
- ERS completo de 2 GB indexado sem erro, RSS < 1 GB, UI responsiva durante a indexação; ERS Verbose dentro da meta do M6.
- Teste manual no PC Windows: copiar a pasta, executar o `.bat`, abrir um ERS local no Edge e no Chrome, nenhuma conexão de rede além de loopback.
- Nenhum achado alto/crítico aberto na revisão de segurança.

## Pós-1.0

| # | Entrega | Observação |
|---|---|---|
| P1 | Templates e eventos para os demais logs do ERS (NMC, PSengine, arbIP, processos `UNKNOWN` como `CustomAdapterChangeIncidentDevice`, ...) | Só dados (`families.toml` + TOML) e derivadores novos; esquema e descoberta já preparados (ARQUITETURA, 9.1a) |
| P2 | Vários ERS por caso, correlação entre máquinas, ajuste de relógio (`clock_offset_ms`) | Esquema já comporta (`collection_group`, `clock_offset_ms`); falta UI, regra de 409 e correlação por tempo entre coletas |
| — | Adapters do vault sem amostra | Fora do escopo (não existem no ambiente produtivo, U8); entram só se surgirem amostras, pelo mesmo processo do P1 |

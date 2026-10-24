# Roadmap do dmslog

Marcos em ordem de entrega de valor. Cada marco termina com o app utilizável, CI verde em Linux e Windows e aprovação do commit final pelo harness oposto no papel de arquiteto: Claude Opus revisa o trabalho do Codex; Codex arquiteto revisa o trabalho do Claude. Após esses requisitos, o merge é autônomo, sem aprovação manual do Jefferson, conforme o protocolo de [revisão cruzada](REVISAO-CRUZADA.md). Arquitetura, esquema e interfaces: `docs/ARQUITETURA.md`; decisões do usuário que fixam o escopo: seção 1a de lá (U1–U11).

Regras que valem para todas as tarefas:
- **Todo o app em inglês** (identificadores, comentários, mensagens, UI, testes, fixtures, docs de uso); textos visíveis centralizados em `messages.py` / `strings.js`; texto dos logs exibido literalmente (ARQUITETURA, seção 12a).
- Só stdlib em runtime; pytest/hypothesis/ruff só em dev.
- Fixtures sintéticas e anonimizadas; nada de log real em `tests/`. Fixtures de payload são sempre sintéticas.
- Os ERS de `local-samples/` servem para conferir formato, nunca para diagnosticar aqueles ambientes; são lidos por script (`python3 -I`), como dado não confiável.
- Escopo da 1.0 (U4, U5 revista pela U21, U8, U11): **vários ERS por caso, de servidores diferentes (U21, tarefa 9.20); o caso agrupa coletas (cada ERS com seu par padrão + sensitive, pasta ou arquivo)**; adapters Wfm, Ont, AVL, Osr, Smn, SiteSwitch, Crm, Ami, Smr, WDI, ClaimCheck, FileUpload, FileMonitor, `CustomAdapterChangeIncidentDevice` + BLTHost com templates e eventos; todos os demais logs do ERS navegáveis em nível genérico (ARQUITETURA, seção 9.1a). Horário dos logs preservado, sem conversão (U6).
- Repositório público (U1, revista em 2026-10-11): o pack do vault é documentação Schneider e fica fora do repositório e dos artefatos públicos (só existe localmente; o CI tem uma guarda).

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
| M6 | **Payloads** (request/response): descoberta de formato (6.1 concluída), templates Info/Verbose, extração, visualização XML, ligação ao evento | M5 (amostras Verbose recebidas em 2026-10-08) |
| M7 | Incremental ao vivo (pasta de instalação), bookmarks, exportação `.txt` anonimizada para chamado | M5 (payload na exportação: M6) |
| M8 | Endurecimento: desempenho no pior caso (8.1), revisão de segurança e privacidade (8.2), docs (8.3, 8.4) e workflow de release (8.5). **Desenvolvimento concluído (2026-10-12)**; o teste manual no Windows e a tag `v0.1.0` ficam **depois do M9** (decisão de 2026-10-12, U19) | M7 |
| M9 | **Features do estudo de referência (U19), ajustes de UI de uso (U20) e casos com vários ERS (U21):** destaques e regras de cor, valores distintos e agrupamento, presets por família, copiar/exportar a visão, visões encadeadas, agregação declarativa, comparação por template, extrator ad hoc, abertura parcial, preferências versionadas e os itens P2 (painéis, duplicados e sequências, alertas locais, detecção de formato, offset entre coletas). A **0.1.0 só sai com o M9 concluído** | M8 |
| Pós-1.0 | P1: templates/eventos para os demais logs do ERS. P2 (correlação entre máquinas além do que o M9 entrega; vários ERS por caso e ajuste de relógio já entram no M9: 9.20 e 9.15) | M9 |

M6 e M7 podem andar em paralelo.

**Versão (U17, 2026-10-10):** a primeira release será a **0.1.0**; a 1.0.0 só quando tudo estiver muito maduro. Neste documento, "1.0" ("antes da 1.0", "aceite da 1.0", "pós-1.0") significa o escopo da 0.1.0 e o que vem depois dela.

## Estado em 2026-10-10

| Marco | Estado | Onde |
|---|---|---|
| M0 | concluído | commit `7ce01fb` |
| M1 | concluído | commits `b8d91fa` e `c767018` (revisão cruzada; 3 achados bloqueantes corrigidos no segundo) |
| M2 | concluído (2.11 feita no commit da família AEDP, `f4fec43`) | commits `fd0b55f` (dez rodadas de revisão do validador de regex) e `2b31830` (UI verificada no Chrome, lacunas fechadas, 2.4) |
| M6.1 | concluído (descoberta de formato dos payloads) | commit `4533ff5` (aprovação manual do Jefferson) |
| M3 | concluído, exceto a parte por janelas de 3.9 (depende do M4); 3.10 feita com o pack regenerado (29 adapters, 715 códigos, 80 operações, 1.510 parâmetros) | commit `2b31830` (pack do vault, Registry em camadas, `config_observed`, `/api/config`, `/api/knowledge`, dicas curadas, mapa processo → sigla, UI) |
| M4 | implementado, com pendências abaixo (4.1 a 4.7 e regras de operação de todas as famílias com operações; 4.8 feita no commit de pendências do M4/M5, `dc91dae`) | commit `99cc8a8` (motor de derivação por cadeia de rotação, operações, estatísticas, ciclo de vida, exceções, retries, API de eventos, painéis Operations e Adapter dashboard) |
| M5 | **concluído** (5.1 a 5.4), mergeado | commit `b025edf` (pares normal↔sensitive, callbacks/retries/respostas por chave, regras nos TOML, API de chaves e UI de links) |
| M6 | **concluído** (6.1 a 6.7), mergeado; taxas medidas abaixo | commit `c3e9ea5` (extração e ligação de payloads, esquema v3, formatador, API, UI; revisão cruzada `gpt-6.1-sol` em 3 rodadas, aprovada) |
| M7 | **concluído** (7.1 fechada no PR #76 em 2026-10-11). 7.2 feita (`dmslog watch`, `serve --watch` com SSE, "Follow tail"; `8b7df26`); 7.3 feita (bookmarks, comparar registros, renomear/sobrescrever filtros salvos; `00b5297`); 7.4 e 7.5 feitas (redação configurável, exportação `.txt`, `dmslog export`, checagem de `Origin` para `PUT`/`PATCH`; `12702ae`). 7.1 feita (PR #76: a reassociação de bookmarks passou a ser mantida na transação do writer, esquema v4 com `bookmark.fp`, `uid`, `source_id` e `orphan_seq`, migração transacional, exportação v2 e um teste de propriedade com estados). 7.6 feita como varredura de vazamento nas exportações sintéticas (`docs/notas/varredura-anonimizacao.md`, PR #52); a conferência com dados reais fica com o Jefferson, localmente | commits `00b5297`, `8b7df26`, `12702ae`, PR #76 |
| M8 | **desenvolvimento concluído (2026-10-12)**: 8.1 a 8.5 e os itens da 0.1.0 (U18, #65, #66, #67, Crm) na `main`; a pré-release `v0.1.0` **não** foi criada: por decisão de 2026-10-12 (U19) ela sai depois do M9 | PRs #75, #77, #78, #80, #81, #82, #84, #85, #88, #89, #91, #92, #94, #95, #96, #97 |
| M9 | **desenvolvimento concluído (2026-10-19)**: as 20 tarefas (9.1 a 9.20) e a frente de interface UI-01 a UI-30 estão na `main`, com revisão de segurança 8.2 do delta e o CHANGELOG da 0.1.0; as issues #98 a #123 e #166 a #170 foram encerradas. Depois do M9 entraram a árvore de pastas nas fontes (PR #235), as alças de largura das colunas laterais (PR #233) e a altura do gráfico superior (PR #234), e a fila de mensagens (#74, abaixo) passou a fazer parte da 0.1.0. Falta a conferência manual (Windows real, leitor de tela, UAT a recriar) | PRs #118 a #235 |
| Testes de usuário (UAT) | 43 issues (#7 a #49; #49 é o guia e índice), milestones A a H, rótulos `uat`, `uat:pendente`, prioridade e área; quadro em https://github.com/users/jeff-dmf/projects/3. Todos os testes estão disponíveis na `main`, inclusive bookmarks (G1, G2), exportação (G3 a G6) e o modo ao vivo | issues #7 a #49 |
| Dependabot | ativo desde o commit `e972f6a`: atualizações **semanais** (segunda, 06:00 America/Sao_Paulo) de GitHub Actions e de ferramentas de dev (pip), com permissões mínimas nos workflows e ações fixadas por SHA; PR #1 (atualização do `upload-artifact`) aberto | commit `e972f6a` |
| Release 0.1.0 (U17) | `pyproject.toml` já em 0.1.0; a release sai depois de M7, 8.1 a 8.5, **do M9 (U19)** e dos UAT; artefatos do CI (`dmslog.pyz`, portátil Windows, tarball Linux) construídos pelo CI sem o pack (o pack só entra em build interno com `--pack`) | M8 |

**M1, medido no ERS de exemplo (escopo padrão):** ingestão de 13,8 s só do Enterprise e 25,5 s com o Sensitive (meta 40 s), pico de 236 a 255 MB (meta 500 MB), reingestão idempotente em 0,3 s, página do grid e busca FTS com p95 de 0,8 ms. Interface verificada no Chrome com ERS sintético (horário igual ao do log com fuso de São Paulo, `<script>` literal no grid e no detalhe).

**M2, medido:** 335 templates em 17 famílias; 99,86% dos 4.054.391 registros dos arquivos de amostra de escopo padrão têm template (os demais são a família `CustomAdapterChangeIncidentDevice`, tarefa 2.11, e 2 mensagens de 17.904 caracteres numa linha só); zero exceção .NET com severidade acima de 2 em 3.707 linhas; custo de ingestão 1,22x o do M1 com um catálogo realista (1,40x com 50 templates); histograma de 2,1 milhões de registros em 16 ms (tabela aditiva `bucket_sev`, esquema v2); cinco invariantes de contagem dos documentos passam.

Tarefas do M2: todas feitas; a 2.11 (família `CustomAdapterChangeIncidentDevice`, código `AEDP`, acrônimo do vault `ACID`) saiu com `aedp.toml` (AEDP-001..003, operação `ReceiveIncidentDevice`), `docs/formato/aedp.md` e cobertura de 100% da família na coleta 1 (antes 46,3%). A UI do M2 foi verificada no Chrome com ERS sintético (timeline, árvore de filtros, filtro salvo, template, cores nos dois temas, `n`, detalhe e contexto, painel de desconhecidos). As três lacunas de template já estavam cobertas por templates existentes; a 2.4 virou `tests/unit/test_docs_ids_exist.py` (achou 5 IDs sem alias, corrigidos). Em aberto: o texto do site backup do SiteSwitch não existe nas amostras; decisão do Jefferson sobre indexar o texto `ResultSet` do `BLT-011` (97 mil registros sensitive). Resta da AEDP: o erro "dispositivo inexistente no modelo" da amostra não bate com nenhum código documentado do ACID e o log não imprime `Error.code` (anotado em `docs/formato/aedp.md`); os eventos específicos são a tarefa 4.8.

**M4, medido no ERS de exemplo:** a derivação cobre as quatro regras de operação documentadas e os números batem com `docs/formato/` (Wfm: 13.696 requisições de crews, 184 sem fim = `WFM-081`, 881 com erro de negócio; Ont: 3.771 `SendIncident` ok e 7 falhas; AVL 75.919 pares; Osr `GetIncidents` 53.764 + 176 falhas; Smn `SendWorks` 1.526 ok + 751 falhas; SiteSwitch 76.623 pares). Descobertas das amostras que mudaram o desenho (ARQUITETURA seção 15): o stream do BLTHost reúne arquivos independentes, então a derivação é **por cadeia de rotação**; o Wfm registra `closed` do mapa duas vezes (duplicata em até 1 s). Desempenho (idle, escopo padrão, Enterprise + Sensitive): indexação 34,4 s (meta 40 s) e derivação 13,7 s (meta 20 s, medida à parte desde o M4); a derivação ficou cerca de 40% mais rápida com `RowEntry` e saídas antecipadas, com saída idêntica ao banco de referência. **Pendências do M4 (fechadas no commit `dc91dae`):** 4.8 (`AEDP-003` fecha a operação `ReceiveIncidentDevice` como `business_error`: hoje todo `AEDP-003` conta assim, mesmo com outra razão; separar razões exigiria casar a continuação no template), `level_window` no painel "Effective configuration" (`payload_expected_windows`), `flags & 16` em janelas Info ou acima, `side` e o CSV completo das estatísticas. **Ainda abertas:** `business_error` declarado só onde a documentação o define (Wfm, AVL, Osr); derivação em paralelo por cadeia fica como otimização futura.

**M6, medido no ERS Verbose de exemplo (só contagens; iguais a `docs/formato/payloads.md`, seção 5):** anúncios do Wfm 4.716/4.716; requests do Wfm por chave 2.359/2.359; responses do Wfm por CorrelationID 2.339/2.340; faults 17/17; Ont: requests 943/943 e responses 943/943 (por tempo, `event_conf` 1); Smn: requests 1.426/1.426 e responses 1.389/1.389; publicações do Smn anunciadas 1.490/1.490; janelas antigas do Ont (674 requests e 354 responses) listadas como órfãs com motivo. Desempenho (smoke local): indexação 36,7 s (meta 60 s), derivação 13,8 s, pico de 301 MB (meta 600 MB), ligação de payloads 0,7 s. Testes no commit: 1.997 de unidade passando e 1 pulado, 186 testes de front-end. **Fora do M6:** chaves de payload em `entry_key`, números de linha no viewer e testes de DOM (sem harness). Desvios do desenho original: candidato único na ligação, motivos `scan_limit` e `too_many_payloads`, reextração única por fonte, `GET /api/payloads/page` (ARQUITETURA 6.6, 10 e 15).

**M5, medido no ERS de exemplo:** pareamento normal↔sensitive igual ao documentado: AVL-002 75.540 de 75.540 (99,5% mesmo ts+TID), AVL-007 20 de 20, Osr `Detected errors` 11.940 de 11.940, ClaimCheck CLC-003 9.990 de 9.990, Crm CRM-010 3.436 de 3.436, Ami 23 de 23, Wfm WFM-010 22.073 de 22.073, Smn 765 de 765 (SMN-016); callbacks do Crm por GID 3.437 e respostas 3.426; Wfm por GID só nas janelas Info e por janela de tempo (confiança 0) para callbacks sem chave. A correlação soma cerca de 2 s à ingestão e o pico de memória subiu de 261 para 303 MB (meta 500 MB). **Fora do M5 / pendências:** (o pareamento Ont/Smn pelo MessageID dentro do XML e GEN-MSG-01↔GEN-PLD-01 foram feitos no M6); callbacks do Osr sem regra (OSR-031 não tem origem com o mesmo GID; resolvido na issue #66 com ligação heurística ao CreatedIncidentHazards, confiança 0, única nos dois sentidos: `docs/formato/avl-osr-smn-siteswitch.md`, seção 3); `WFM-030↔WFM-S030` perde 2 e 18 registros nas duas capturas (não investigado); `ONT-020>ONT-S020` removido (sensitive só em janela Info anterior). Verificação visual no Chrome com dados sintéticos: links, "Show in sensitive", visão lado a lado e chaves.

Decisões de desenho do M2 que valem para os próximos marcos: templates só se aplicam a mensagens que cabem na janela (padrão 4096; `window` por template); validador de regex por orçamento de passos (2^21) mais limites de construção (classe até 32 itens, capturas até 3 níveis); mudar o catálogo, a severidade ou o `fts` de um template exige reindexar o caso.

A base de conhecimento de `CustomAdapterChangeIncidentDevice` (ACID) foi criada no `adms-v3.9` e incorporada ao pack (3.10); as tarefas 2.11 e 3.10 estão feitas e **a 4.8 (eventos específicos) continua aberta** até a tarefa de acompanhamento que a entrega; ela bloqueia a release 0.1.0.

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
| 1.5 | `families.toml` (padrão de caminho → família, envelope, escopo padrão) + `ingest/discover.py` + `ers.py` (classificação dirigida por dados, par Enterprise/Sensitive, um ERS por caso (histórico: revisto na U21, tarefa 9.20), escopo padrão; demais logs listados e indexáveis em nível genérico) | sonnet |
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
- Adicionar um segundo ERS (outro GUID) ao caso devolve 409; a outra metade do par é aceita. (Histórico do M1: a U21 e a tarefa 9.20 substituem a regra, aceitando coletas de servidores diferentes e recusando só a duplicata da mesma coleta.)
- Smoke do portátil no CI indexa o zip de fixture e devolve uma página.

## M2: templates, severidade, merge, filtros e timeline

| # | Tarefa | Modelo |
|---|---|---|
| 2.1 | Esquema TOML dos adapters (inclusive `fts` e o atributo `payload` (`PayloadSpec`, ARQUITETURA 6.3 e 6.6)), `parse/templates.py` (validação, despacho por prefixo, `cont_parser`s) e `parse/signature.py` | opus |
| 2.2 | `_common.toml` unificando os genéricos dos três docs de formato (aliases dos IDs antigos) e tabela de aliases de nomes de `[Configuration]` (para o M3) | opus |
| 2.3 | TOML por família a partir de `docs/formato/` (AVL, Osr, Smn, SiteSwitch, Wfm, Ont, Crm, Ami, Smr, WDI, ClaimCheck, FileUpload, FileMonitor, BLTHost, oaserr, service), com exemplos positivos/negativos por template; reaproveitar regex de `catalog.py` | sonnet (um agente por grupo de famílias) |
| 2.4 | Conferir os TOML contra os docs: todo ID documentado existe, contagens das fixtures batem | haiku |
| 2.5 | `parse/severity.py` com a ordem da seção 6.4 e a lista de falsos positivos | opus |
| 2.6 | Fixtures por família (10–200 registros, todos os templates) | sonnet |
| 2.7 | API/UI: coluna e filtro por template, cores por severidade, "ocultar ruído", merge de fontes com indicador de origem, árvore de filtros AND/OR/NOT, filtros salvos, colunas dinâmicas por `fields`, atalhos (próximo erro, próximo do mesmo template) | sonnet |
| 2.8 | Timeline (uPlot vendorizado com LICENSE) por `bucket`, empilhada por severidade/fonte, seleção de intervalo filtra o grid | sonnet |
| 2.9 | Painel de templates desconhecidos (assinatura, contagem, exemplo, "copiar como esboço de TOML") | sonnet |
| 2.10 | Smoke local: cobertura de template = 100% das linhas em todos os 30 arquivos de exemplo; invariantes de contagem dos docs de formato (ex.: CRM-001 = CRM-003 + CRM-004) | haiku |
| 2.11 | `CustomAdapterChangeIncidentDevice`: descobrir formato por script e documentar; definir família e templates específicos com fixtures positivas/negativas, apoiados na base do `adms-v3.9`. Cobertura nova medida separadamente das contagens históricas | opus (descoberta e desenho) + sonnet (TOML e fixtures) |

Aceite:
- Fixtures: 100% dos registros com template; cada template casa com seus exemplos e com nenhum negativo.
- Amostras locais: 100% de cobertura nos 30 arquivos; nenhuma exceção .NET com `sev` > 2.
- Família `UNKNOWN` indexa com `_common` e aparece no painel de desconhecidos, sem erro.
- A tarefa 2.11 valida os templates específicos de `CustomAdapterChangeIncidentDevice` nas fixtures do formato descoberto, antes da 1.0.
- Indexação com templates ≤ 1,3× o tempo do M1.
- Histograma com 2 M registros < 50 ms.

## M3: conhecimento

**Estado:** 3.1 a 3.9 feitas (3.1 e 3.4 foram implementadas por Sonnet seguindo as seções 7.1 e 7.2.1, sem decisão nova de desenho). 3.10 feita (pack com o ACID). Pendente, na 3.9, a expectativa de payload por janelas `level_window` no painel "Effective configuration" (que hoje usa só `LogLevel` × `MessageLogLevel`); o aviso "payload esperado e ausente" por janela já existe no detalhe da operação (M6, 6.7). Smoke no ERS de exemplo: 196 valores observados, 14 produtos em `config_param`, 0 divergências resolvido × observado depois de comparar TimeSpans por valor; sete adapters sem linha `[Configuration]` nos logs indexados (startup já rotacionado). `CoreSDK` fica fora do `config_param` (não é adapter). Códigos de família que diferem do acrônimo do vault (AWDI↔AWMI, AEDP↔ACID) são traduzidos por `Knowledge.acronym_for_family` a partir de `curated/process_map.toml` (chave `family`).

| # | Tarefa | Modelo |
|---|---|---|
| 3.1 | Desenho do esquema do pack (JSON, com `notice` de documentação Schneider no `manifest.json`; histórico: na época o repo era privado, hoje o pack não é commitado) e de `curated/hints.toml` | opus |
| 3.2 | `tools/kb_build.py`: parser das tabelas Markdown do vault (pipes escapados), saída determinística, `manifest.json`; teste com mini-vault | sonnet |
| 3.3 | Gerar o pack a partir do vault real, revisar o diff e commitar (histórico: o pack saiu do repo em 2026-10-11, U1 revista) | sonnet (geração) + haiku (conferir contagens contra o vault) |
| 3.4 | `knowledge/installation.py`: Registry em camadas pela regra 7.2.1 (agrupamento por `Product/@Name`, camada por sufixo, precedência só em chave sobreposta, `<Key>` como lista, empate = `ambiguous`), WebServiceConfiguration e ErrorConfiguration efetivos, normalização de `%OASINSTALLDATADIR%` | opus |
| 3.5 | `config_observed` a partir das linhas `[Configuration] ... Type: [ConfigurationRegistry]` do startup; comparação resolvido × observado por nome normalizado + aliases | sonnet |
| 3.6 | `knowledge/resolve.py` (ERS > vault, divergências "fora do vault") e uso na severidade por código | sonnet |
| 3.7 | Mapa processo → sigla para os adapters do escopo (U8, U11) | haiku (inventário) + sonnet (curadoria) |
| 3.8 | `hints.toml` em inglês para os templates de erro/timeout, ligando parâmetros (ex.: WFM-080 → `WaitForCRSCallback`) | sonnet |
| 3.9 | UI: painel de conhecimento no detalhe do registro; painel "Effective configuration" por adapter com camadas (arquivo de origem, `only`/`wins`/`overridden`/`ambiguous`), valor observado no log, divergências, e por operação se o payload é esperado (`LogLevel` × `MessageLogLevel`, e no tempo pelas janelas `level_window`, ARQUITETURA 6.6) | sonnet |
| 3.10 | Incorporar ao pack a base de `CustomAdapterChangeIncidentDevice`, após sua criação no `adms-v3.9`, e validar a ligação de operações, parâmetros e mensagens/códigos documentados à análise específica | sonnet |

Aceite:
- `kb_build` roda duas vezes e produz bytes idênticos; schema validado no CI.
- Arquivos `AdapterX_1_<Projeto>` são camadas do mesmo processo, definidas com base em `AdapterX` e distribuídas em MSI/MSU de atualização do projeto ADMS; não geram instâncias separadas.
- Fixture de Registry: todos os casos da regra 7.2.1 (só uma camada, sobreposição -1/0/1/2, `<Key>`, empate, as duas formas de `%OASINSTALLDATADIR%`) dão o efetivo esperado.
- No ERS de exemplo (smoke local): `AdapterAmi` `WebServiceConfiguration` efetivo = camada `_2` (`DMSCustomIntegrationPROD`), `CreatedMeterReadings_ErrorConfigurationPath` = camada `_1_Custom`, `AdapterFileUpload` `FileUploadInterval` = `00:01:00`; todos batem com `config_observed`; nenhuma divergência resolvido × observado nos 13 adapters já analisados na amostra, salvo as explicadas por alias faltante (que viram tarefa).
- A configuração efetiva mostra `LogLevel=DebugLog` e `MessageLogLevel=Info` para os adapters e marca "payload not expected" pela configuração em todas as operações (fato dos dois ERS de exemplo); no ERS de 14:53, Wfm/Ont/Smn mostram payload esperado dentro das janelas Info/Verbose.
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
| 4.8 | Definir e implementar eventos específicos de `CustomAdapterChangeIncidentDevice` a partir da descoberta 2.11 e da base 3.10; validar resultados esperados em fixtures sintéticas | opus (regras) + sonnet (implementação e fixtures) |

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

Situação: **M6 concluído e mergeado (commit `c3e9ea5`, 2026-10-09)**; 6.1 a 6.7 feitas. Amostra Verbose: ERS de 2026-10-08 14:53 (Wfm/Ont/Smn elevados por ~2 h); formato, ligações, catálogo Info/Verbose e volume em `docs/formato/payloads.md`. Taxas medidas no resumo do estado acima. Fatos que guiam o desenho: payload só XML, um por registro, nunca truncado; sai com nível corrente ≥ Info; fica no sensitive (e no normal para Wfm→CRS/WMS); anúncio no normal na mesma TID; ligação à operação por MessageID/CorrelationID ou pelo fim da operação, nunca por TID.

| # | Tarefa | Modelo | Situação |
|---|---|---|---|
| 6.1 | Descoberta de formato dos payloads e das mensagens Info/Verbose; ajuste do esquema e do atributo `payload` | opus | **concluída** (2026-10-08) |
| 6.2 | Templates Info/Verbose do catálogo de `payloads.md`, seção 9 (≈ 60 entradas; regex já testadas em 100% das linhas da amostra): `GEN-*` em `_common.toml` (GEN-MSG-01 com aliases WFM-100/ONT-100; GEN-MAP-01 e GEN-SUB-01 estendidos sem trocar ID), `WFM-110..130`, `ONT-110/111`, `SMN-110..113`, sensitive `GEN-PLD-01`, `WFM-S112/S121`, `SMN-S110`, `ONT-S020`, `GEN-PRX-S01`; `fts = false` e sev 5 no rastreio; `cont_parser`s `kv_dash` (Publication info) e `kv_colon` (EventData) | opus (revisão) + sonnet (TOML) | **concluída** |
| 6.3 | `parse/payload.py` (`PayloadSpec`: `start`, `strip_suffix`, `fmt`, `role` por raiz, `msg_id`/`corr_id` por regex) e `derive/payloads.py` com as 7 regras de ligação da ARQUITETURA 6.6 e `event_conf`; callback CRS/IMS por GID | opus | **concluída** (`c3e9ea5`) |
| 6.4 | Fixtures sintéticas por família com payload (Wfm request/response/fault/delta/CRS/WMS/IMS; Ont e Smn request/response; publicação `xml_seq` do Smn; HTML de erro), cobrindo: `>.` final, texto multilinha dentro de elemento, `&amp;` e `&#xD;&#xA;`, WMS em uma linha, registro > 65.536 caracteres, linhas sem TID do PSAttach, `ts` voltando 2 s no sensitive, response sem IDs com duas operações concorrentes; smoke local nas amostras | sonnet + haiku (varredura de vazamento nas fixtures) | **concluída** (`c3e9ea5`) |
| 6.5 | `payload/pretty.py`: tokenizador XML tolerante (sem DTD/entidades, aceita truncado e `xml_seq`), HTML cru, JSON com limite; testes com malformados, `<!DOCTYPE>`, entidades, `<script>` | sonnet | **concluída** (`c3e9ea5`) |
| 6.6 | API `entries/{id}/payloads`, `payloads/{id}`, `payloads/page` (filtros `role`, `direction`, `peer`, `operation`, `fmt`); filtro `payload` na árvore | sonnet | **concluída** (`c3e9ea5`) |
| 6.7 | UI: aba "Payload" no detalhe (formatado/cru, copiar, selo "sensitive"), par request↔response lado a lado com a confiança da ligação, ir para a operação e para o anúncio, lista de payloads por operação/papel, aviso de payload esperado e ausente pela janela `level_window` | sonnet | **concluída** (`c3e9ea5`) |

Meta de indexação (medida em 6.1, `payloads.md` seção 8; **atingida**: 36,7 s, 301 MB, ligação de payloads 0,7 s no smoke local): o ERS de 14:53 no escopo padrão (60 arquivos, 1.041 MiB de log, ~31% a mais que o ERS em DebugLog) indexado com templates, FTS e extração de payload em **≤ 60 s** na máquina de dev, banco **≤ 1,7 GB**, RSS < 600 MB; a extração de payload acrescenta ≤ 10% ao tempo sem ela.

Aceite:
- Fixtures: todos os payloads localizados com offsets exatos (sem o `.` final); request e response ligados à operação certa com a confiança esperada; truncado, malformado e `xml_seq` exibidos sem erro (crus com motivo quando não formatam).
- Amostras Verbose locais: payloads ligados ao anúncio e à operação nas taxas de `payloads.md` seção 5 (Wfm 4.716/4.716 anúncios, 2.359/2.359 requests por chave, 2.339/2.340 responses por CorrelationID, 17/17 faults; Ont 943/943 e Smn 1.426/1.426 requests por chave, 943/943 e 1.389/1.389 responses por tempo) ou listados como órfãos com motivo; cobertura de template continua 100% (inclusive Info/Verbose).
- Segurança: `<!DOCTYPE>` com entidade externa ou expansão não é resolvida; `<script>` no payload aparece literal.
- Indexação do ERS Verbose dentro da meta acima.

## M7: ao vivo, bookmarks e exportação

**Estado (2026-10-11):** concluído. A 7.1 fechou no PR #76 (ver a seção 9.3 da ARQUITETURA: identidade do bookmark em três partes, impressão completa do registro, religação só dentro da linhagem da fonte, migração em `BEGIN IMMEDIATE`, exportação v2 com `uid`); os demais itens estão na `main` em três commits. `00b5297`: bookmarks (`store/bookmarks.py`, `/api/bookmarks*`, `/api/compare`, `PUT /api/views/<id>`, painel e comparação na UI), comparação de registros e renomear/sobrescrever filtros salvos (7.3). `8b7df26`: `dmslog watch`, `serve --watch` com SSE e "Follow tail" no grid (7.2; `ingest/watch.py`, `tests/unit/test_watch.py`, `tests/web/follow-tail-*.test.mjs`). `12702ae`: `report/redact.py`, `report/export.py`, `dmslog export`, `GET/PUT /api/redaction`, `POST /api/export`, checagem de `Origin` para `PUT`/`PATCH` (7.4 e 7.5; a varredura de vazamento sobre as exportações sintéticas está em `tests/unit/test_redact_leaks.py` e `test_export.py`, mas a conferência final da 7.6 segue como etapa posterior). O teste de rotação no Windows roda na matriz do CI (verde). Os testes de usuário G1 a G6 estão liberados; G1, A6 e H5 devem ser reavaliados com a reassociação nova.

| # | Tarefa | Modelo |
|---|---|---|
| 7.1 | Incremental: crescer, rotacionar, truncar (tabela da seção 9.3), remoção de cauda no FTS externo, reassociação de bookmarks | opus |
| 7.2 | `dmslog watch` + SSE de novidades + "follow tail" no grid | sonnet |
| 7.3 | Bookmarks com cor/nota, comparar dois registros, filtros salvos na UI (feito no commit `00b5297`) | sonnet |
| 7.4 | `report/redact.py` configurável (categorias, termos do usuário, exceções; `redaction.toml`; regras por elemento XML/JSON para payload) e API/UI `/api/redaction` | opus (regras de payload) + sonnet |
| 7.5 | `report/export.py`: `.txt` no formato da seção 10.1 (UTF-8 sem BOM, CRLF, moldura ASCII, limite de tamanho, partes numeradas, legenda de anonimização, mapa local fora do `.txt`); botão "copy to clipboard"; `dmslog export` | sonnet |
| 7.6 | Revisão da anonimização: varrer exports de fixture atrás de GUID/host/usuário/URL/placa/e-mail/telefone, inclusive com payload | haiku |

Aceite:
- Teste: arquivo cresce em pedaços aleatórios durante o `watch` → banco final idêntico ao de uma ingestão única.
- Teste Windows (CI): com o `watch` rodando, renomear `.log` → `.log.0` e criar novo `.log` funciona (nenhum handle preso) e os registros continuam.
- `.txt` de fixture idêntico byte a byte ao esperado (CRLF, sem BOM, só ASCII fora das linhas de log).
- Export anonimizado de fixture não contém nenhum valor da lista de termos sensíveis da fixture; desligar uma categoria faz o valor dela aparecer; payload só entra com `include_payloads` e, quando entra, sai anonimizado por elemento.
- O `.txt` colado num campo de texto simples mantém a formatação (teste manual do Jefferson num chamado de teste).

## M8: endurecimento e release 0.1.0

| # | Tarefa | Modelo |
|---|---|---|
| 8.1 | Desempenho no pior caso: ERS com escopo completo (≈ 2 GB), ERS com os 14 adapters em Verbose (projeção ≈ 2,6 GB de log, banco ≈ 4,1 GB; ARQUITETURA 9.3a), BLTHost sensitive, Wfm com dumps; ajuste de lotes, despacho de templates, índices e limite do FTS. Ferramenta de medição pronta: `tools/bench.py` (PR #82) | opus |
| 8.2 | Revisão de segurança (`/security-review`) e de privacidade (onde ficam os dados, apagar caso, payloads, mapa de anonimização) | opus |
| 8.3 | Processo documentado para adicionar uma família nova (`families.toml` + TOML + leitura das assinaturas desconhecidas; a ferramenta `mine_templates` foi criada no PR #77), preparando o P1 (doc de uso em inglês) | sonnet |
| 8.4 | README e ajuda da CLI em inglês (README real em 2026-10-10; `dmslog export` já existe desde o commit `12702ae`; README feito, PR #51); README.txt do portátil em inglês (feito, PR #78; o aviso de uso privado vale só para builds que contenham o pack) | sonnet |
| 8.5 | Release: artefatos do CI (`dmslog.pyz`, portátil Windows, tarball Linux) no GitHub, sem o pack (o pack só entra em build interno com `--pack`), e checklist de teste manual em um PC Windows sem admin e sem internet, no Edge e no Chrome | sonnet + teste manual do Jefferson |

Aceite:
- ERS completo de 2 GB indexado sem erro, RSS < 1 GB, UI responsiva durante a indexação; ERS Verbose dentro da meta do M6; pior caso Verbose sintético (≈ 2,6 GB) indexado sem erro com RSS < 1 GB.
- Teste manual no PC Windows: copiar a pasta, executar o `.bat`, abrir um ERS local no Edge e no Chrome, nenhuma conexão de rede além de loopback.
- Nenhum achado alto/crítico aberto na revisão de segurança.
- U18 (issue #61): exportação (CLI, API e UI) sai com o texto original por padrão, sem legenda de redação e sem ler o perfil; a anonimização só atua com a opção explícita (`--anonymize`, `options.anonymize: true`, seletor "On"); `--redaction-mode` ou perfil sem a opção é erro; com a opção, strict continua o padrão e mantém a rede padrão-nega mesmo com todas as categorias desligadas (ARQUITETURA 1a e 10.2).
- Issue #67 (chaves de payload em `entry_key`, deixada fora do M6; entra na 0.1.0 por decisão de 2026-10-11): **feito**. `msgid`, `mrid`, `vehicle`, `gid` e `incident_gid` lidos do XML na ingestão (`entry_key.origin = 1`, esquema v5), fora da derivação; casos existentes ganham as chaves sem reindexar. ERS Verbose: +30.794 linhas (+2,7%), banco +0,27%, tempo igual dentro do ruído (ARQUITETURA 6.6, "Chaves do payload"; `docs/formato/payloads.md`, 6.1).
- `CustomAdapterChangeIncidentDevice`: base de conhecimento incorporada e análise específica com templates e eventos validada (2.11, 3.10 e 4.8); suporte apenas genérico não atende ao aceite da 1.0.

## M9: features do estudo de referência (0.1.0 só depois deste marco)

Origem: estudo de funcionalidades de visualizadores de log (conceitos de uso; a nota de análise é local e não é versionada: repo público). Decisão do Jefferson em 2026-10-12 (U19): **a 0.1.0 só sai depois de implantar tudo o que o estudo encontrou**. Todas as features seguem as regras do projeto: stdlib, web local sem build, UI em inglês com textos centralizados, conteúdo de log só por `textContent`, filtros e consultas sempre parametrizados, nada de rede, desempenho dentro das metas do M8 e implementação própria. O que **não** entra (decidido): atualização/licença/telemetria pela internet, SQL livre do usuário, apagar pastas de trabalho, tudo em memória.

Ordem e paralelismo (Opus uma tarefa por vez; Sonnet em paralelo com arquivos disjuntos; um PR por vez na revisão):

- **Onda 1 (P0):** 9.2 abre (o endpoint `distinct` é base de 9.1, 9.5, 9.6 e 9.8); depois 9.1 e 9.3; 9.4 é independente e roda em paralelo. A frente de **UI de uso (9.16 a 9.19)** anda em paralelo: 9.16 antes de 9.17, 9.18 antes de 9.19.
- **Onda 2 (P1):** 9.5 antes de 9.7; 9.6 e 9.8 dependem de 9.2; 9.9 depende só do M8; 9.10 depende de 9.1 e 9.3.
- **Onda 3 (P2):** 9.11 depende de 9.6; 9.13 depende de 9.3; 9.12 e 9.14 são independentes; **9.20 (casos com vários ERS) abre a onda** e 9.15 (offset entre coletas) depende dela. A 9.20 pode começar já na onda 1 (só depende do M7), porque também muda a regra de criação de caso de 9.17.

| # | Tarefa | Modelo | Depende de |
|---|---|---|---|
| 9.1 | **Destaques e regras de cor do usuário** (P0): regras por caso, ordenadas, com a mesma linguagem da árvore de filtros, paleta fixa com contraste nos dois temas, ação "highlight this key" (GID/MessageID no normal e no sensitive) e navegação `n`/`N` por keyset no servidor | opus (desenho curto) + sonnet | 9.2 |
| 9.2 | **Valores distintos e agrupamento** (P0): endpoint `distinct` (campo, chave, template, TID, fonte) com top N, outros, total e drill-down que vira filtro; "agrupar por template" no grid | opus (SQL e política de contagem) + sonnet | M7 |
| 9.3 | **Presets de filtro por família** (P0): arquivo do usuário `presets.toml` versionado, presets curados por família, import/export, oferecidos ao abrir o caso | sonnet | M7 |
| 9.4 | **Copiar e exportar a visão** (P0): valores de uma coluna/chave, linhas originais, CSV da visão e CSV de eventos, com limites e aviso | sonnet | M7 |
| 9.5 | **Visões encadeadas** (P1): pilha de visões (filho filtra o pai), migalhas, voltar/avançar, "mostrar no pai/na raiz" | sonnet | 9.2 |
| 9.6 | **Agregação declarativa segura** (P1): construtor em JSON (filtro, group_by, medidas count/min/max/avg/p50/p90/p99/distinct), compilado para SQL parametrizado com colunas permitidas, timeout e limite; pack de conhecimento como tabela de consulta; saída em tabela, gráfico e CSV | opus | 9.2 |
| 9.7 | **Comparação de visões por template** (P1): alinhamento (LCS/Myers) sobre IDs de template, modos janela, operação e processo, limite de tamanho | opus | 9.5 |
| 9.8 | **Extrator ad hoc por regex** (P1): colunas temporárias sob o filtro, validador de regex por orçamento de passos, "promote to template" | opus | 9.2 |
| 9.9 | **Abertura parcial por janela de tempo e por fonte** (P1): indexar só o escolhido primeiro e o resto em segundo plano; reabrir continua rápido | opus | M8 |
| 9.10 | **Preferências versionadas** (P1): pacote de preferências (presets, regras, painéis) com `schema`, migração, import/export com relatório, escrita atômica | sonnet | 9.1, 9.3 |
| 9.11 | **Painéis montáveis pelo usuário** (P2): lista de consultas de 9.6 com tipo de gráfico, JSON versionado, atualização pelo SSE do `watch` | sonnet | 9.6 |
| 9.12 | **Duplicados e sequências esperadas** (P2): marcar repetidos por (template, campos-chave) em janela; passos do ciclo de vida que faltaram | opus | M4 |
| 9.13 | **Alertas locais no `watch`** (P2): banner e notificação opcional do navegador quando um filtro/preset casa registros novos; sem SMTP | sonnet | 9.3 |
| 9.14 | **Detecção automática de formato** para logs fora do escopo (P2, liga ao P1 do pós-1.0): envelope genérico parametrizado em `families.toml` | opus | M8 |
| 9.15 | **Offset de relógio entre coletas** (P2, liga ao P2 do pós-1.0): offset por coleção a partir de dois registros correlatos escolhidos pelo usuário, com o horário original sempre visível | opus | 9.20 |
| 9.16 | **Seletor de arquivos nativo e navegador de pastas embutido** (UI de uso): botão ao lado do caminho dos ZIPs que abre o seletor do sistema (Windows e Linux) via servidor local, e navegador de pastas embutido (lista diretórios e `.zip` do usuário local, seleção múltipla) quando não houver diálogo nativo; caminhos sempre validados | opus (desenho e segurança) + sonnet | M7 |
| 9.17 | **Criar caso com um ou vários ZIPs, de forma clara**: texto "1 ou mais ZIPs", seleção múltipla, lista escolhida com nome, tamanho e tipo, remover itens, criar só com pelo menos 1, erros por arquivo | sonnet | 9.16 |
| 9.18 | **Colunas do grid redimensionáveis**: arrastar a borda do cabeçalho, duplo clique ajusta ao conteúdo, largura mínima e máxima, largura lembrada por usuário, sem quebrar a virtualização | sonnet | M7 |
| 9.19 | **Coluna Source com o nome do arquivo e filtro de Source na lateral**: a coluna de template **sai do grid** (decisão de 2026-10-12: não faz sentido para o usuário; o agrupamento por tipo de mensagem continua como visão, com o título legível); a coluna Source mostra o nome do arquivo; filtro dinâmico por substring sem diferenciar maiúsculas e minúsculas, com contagem por fonte | sonnet | 9.18 |
| 9.20 | **Casos com vários ERS de servidores diferentes** (U21, issue #123): o caso agrupa coletas (grupo = `collection.ers_group`), cada uma com seu par padrão + sensitive em qualquer ordem, pasta ou arquivo, servidor/GUID e horário da captura; a regra 409 (recusar outra coleta) vira "aceitar coletas diferentes, recusar duplicata da mesma coleta e mistura inválida"; épocas de derivação por grupo, remoção transacional de uma coleta, filtro/coluna/parâmetro `collection`, rotas `GET/DELETE /api/cases/{id}/collections`, exportação com a coleta de cada registro (ARQUITETURA 9.1b); casos antigos continuam abrindo. **Implementada em branch (backend e testes); falta a UI da lista de coletas** (especificação na 9.1b) | opus (esquema, correlação, regra) + sonnet (UI) | M7 |

Aceite do M9:
- Cada tarefa tem testes unitários (e de propriedade onde houver algoritmo), UAT novo ou atualizado em `uat`, documentação em `docs/ARQUITETURA.md` e revisão cruzada pelo nível T0/T1/T2 do `docs/REVISAO-CRUZADA.md`.
- Nenhuma consulta nova monta SQL com texto do usuário; nenhuma tela nova usa `innerHTML` com conteúdo de log; nenhuma conexão de rede nova.
- As metas do M8 (desempenho no pior caso e responsividade) continuam valendo com as features ligadas; revisão de segurança e privacidade (8.2) refeita sobre o que o M9 acrescentou.
- Casos com vários ERS (9.20): um caso aceita coletas de servidores diferentes, cada uma com seu par padrão + sensitive; duplicata da mesma coleta é recusada com mensagem clara; casos antigos continuam abrindo.
- UI de uso (9.16 a 9.19): em Windows e Linux o botão abre o seletor do sistema (ou o navegador de pastas embutido quando não houver diálogo), a seleção múltipla de ZIPs aparece como lista clara, as colunas do grid se redimensionam e a largura persiste, e a coluna Source mostra o nome do arquivo com filtro lateral que não diferencia maiúsculas e minúsculas.
- Depois da **frente RT** (seção abaixo, decisão de 2026-10-22): teste manual no Windows, UAT do Jefferson e a tag `v0.1.0` (pré-release).

## Frente RT: servidores RealTime (antes da 0.1.0)

Decisão do Jefferson em 2026-10-22: a frente RT vem antes da 0.1.0. Estado em 2026-10-22: a base está na `main` (PRs #241 a #250, desenho em `docs/notas/rt-templates-desenho.md` e `docs/notas/rt-c2-c4-desenho.md`).

| # | Tarefa | Modelo | Estado |
|---|---|---|---|
| R0 | Entrada do ERS RT, famílias, templates (lotes A e B, biblioteca, PSengine, arbIP, NMC, ERS), operações por `job_id`, reinício do FunctionHost, ligações `handoff`, parser `kv_at` | opus (desenho), sonnet | **feito** (PRs #241 a #250); cobertura ~99,3% nos 3 ERS RT de exemplo, banco 9,2 GB (baseline 9,65 GB) |
| R1 | Ver o ERS RT na interface e listar os defeitos | sonnet (verificação), opus (priorização) | **feito pela API e pelo código** em 2026-10-24 (`docs/notas/rt-r1-achados.md`, 6 achados); falta a conferência visual no navegador |
| R2 | Telas e eventos por serviço: rótulos neutros no lugar de "adapter", atalhos (presets) das famílias RT, agrupamento das 28 instâncias do FunctionHost, visão da cadeia do job por `job_id` (achados 1, 2, 3 e 5 da R1) | sonnet; `strings.js` e integração com a sessão principal | em andamento: R2a (PR #253) e o lote 1 (pool do FunctionHost e rota `chain`, PR #255) feitos; lote 2 (frontend da cadeia do job) em revisão; falta a ordem numérica das instâncias na lista plana de fontes |
| R3 | Volume: `PSengine.log` (7 GB) e `PSengine.ALTERNATE_1` (1,5 GB): amostrar a cauda, definir limite, janela do fim do arquivo e a ordem dos timestamps | opus (desenho), sonnet | pendente |
| R4 | Arquivos fora de escopo (`.blg`, XML de configuração, demais): decidir entre só listar ou um visualizador simples | opus (decisão), sonnet | pendente |
| R5 | Parte RT do pack de conhecimento (64 notas do vault citam o DMS_RealTime); o pack continua fora do repositório | sonnet | pendente |
| R6 | Despacho de templates: média de 10 a 17 candidatos por linha contra a meta de 4 (causa: regex sem prefixo do `_common`); só otimização | sonnet | pendente, por último |

Aceite da frente: o ERS RT de exemplo abre e se analisa na interface sem ruído de INT, cada serviço RT com tela ou evento útil, `PSengine` com tratamento definido, o pack RT gerado localmente, vazão e banco dentro das metas do 8.1 e nenhum dado real no repositório.

## Pós-1.0

| # | Entrega | Observação |
|---|---|---|
| P1 | Templates e eventos para os demais logs do ERS (NMC, PSengine, arbIP, outros processos `UNKNOWN`, ...) | Só dados (`families.toml` + TOML) e derivadores novos; esquema e descoberta já preparados (ARQUITETURA, 9.1a) |
| P1a | **Feito (PR #77):** criar `tools/mine_templates.py` (dev, sonnet): agrupa registros desconhecidos pela `signature` e propõe esboço de TOML com exemplos; vem antes de minerar os demais logs do P1 | Decisão de 2026-10-11. A assinatura (`parse/signature.py`) e a tabela `unknown_sig` já existem; ver ARQUITETURA 6.7 e `docs/FAMILIA-NOVA.md` |
| P2 | ~~Vários ERS por caso~~ (**9.20**, U21) e ~~ajuste de relógio~~ (**9.15**, `clock_offset_ms`) passaram para o M9; resta a correlação automática entre máquinas por chave e por tempo | Esquema já comporta (`collection.ers_group`, `clock_offset_ms`); a 9.20 entrega o backend, a regra de 409 e, em seguida, a UI; a correlação por tempo entre coletas fica pós-0.1.0 |
| P3 | **Feito na 0.1.0 (decisão do Jefferson, 2026-10-19, issue #74; PRs #237 e #238 e a interface):** bancos `LocalPersistenceStore/*.db3` do ERS, só a tabela `CURRENT_INTEGRATION_MESSAGE` (fila do Ont e do Smn Unsent): painel de itens da fila, ligação ao evento do log por MessageId e **prévia do XML** do que seria enviado (o BLOB é XML binário do .NET/WCF, MS-NBFX, decodificado e traduzido para o XML de rede, rotulado como reconstrução). Opção explícita por caso, desligada por padrão; o `.db3` é copiado com o `-wal` e o `-shm` para um temporário na pasta do caso só quando a opção está ligada, e o temporário é apagado ao fim | Sensível, nunca indexado por padrão e sem o corpo guardado; desenho em `docs/notas/fila-mensagens-desenho.md` (ARQUITETURA, seção 15; `payloads.md`, seção 10) |
| — | Adapters do vault sem amostra | Fora do escopo (não existem no ambiente produtivo, U8); entram só se surgirem amostras, pelo mesmo processo do P1 |

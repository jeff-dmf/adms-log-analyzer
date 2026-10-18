# Revisão de segurança e de privacidade do delta do M9 (ROADMAP 8.2, refeita)

Data: 2026-10-18. Código: `main` em `e41d264` (esquema v7), delta desde `afcfee0` (revisão anterior, `docs/notas/revisao-seguranca-8-2.md`): 72 commits, 186 arquivos em `src/`. Revisor: Opus. Só dados sintéticos (`tools/make_fixture.py`, GUIDs `00000000-0000-4000-8000-…`): nenhum valor, host ou nome de arquivo dos ERS reais aparece aqui.

**Resultado:** nenhum achado crítico, alto ou médio. 6 baixos (2 corrigidos nesta entrega, 4 para decisão) e 9 informativos. O aceite do 8.2 ("nenhum alto/crítico aberto") está atendido.

## Escopo e modelo de ameaça

- Modelo mantido: servidor HTTP só em `127.0.0.1`, porta aleatória, um operador; ERS e pastas de Log são **dados não confiáveis**; PC Windows corporativo sem admin e sem internet, e Linux. Fora do modelo: processo malicioso do mesmo usuário e admin local.
- Superfície nova lida: rotas novas de `server/api.py` (94 rotas no total) e `server/app.py` (ETag/304, `HEAD` nos estáticos, download em streaming `Download`, `decode_body` estrito); `server/filepick.py`; `store/` (`preferences`, `panels`, `panelrun`, `clock`, `extract`, `aggregate`, `distinct`, `alerts`, `presets`, `highlights`, `viewcompare`, `repeats`, `sequences`, `collections`, `align`); `parse/` (`extractor`, `promote`, `formats`, diff de `envelope` e `templates`); `ingest/` (`partial`, diff de `discover`, `reader`, `ers`, `pipeline`); `report/` (`viewexport`, `aggexport`, diff de `export` e `redact`); `paths.py`; `derive/` (diff); migrações v6 e v7; todo o `web/` novo (135 arquivos); `.github/workflows`, `packaging/`, `pyproject.toml`.

## Método

1. **Estático:** `ruff check --isolated --select S src`: 149 ocorrências (eram 107). As 123 S608 foram triadas; as novas (`aggregate`, `clock`, `distinct`, `alerts`, `extract`, `highlights`, `viewcompare`, `api.py:1293`) só interpolam constantes, apelidos posicionais (`g{i}`, `m{j}`, `p{r}`), colunas de listas fechadas (`ENTRY_DIMS`, `_SPLIT_COLUMNS`), `"?"` repetidos e o SQL do compilador de filtros; todo valor do cliente vai como parâmetro (inclusive caminhos JSON `$.<campo>` validados por regex). S324 (sha1) é identidade de conteúdo; S101/S110/S112 sem impacto; S314 é o XML do Registry já tratado. Nenhum `eval`, `exec`, `pickle`, `shell=True`, `os.system` novo; nenhum cliente de rede em `src/`.
2. **Front-end:** grep em todo `web/js`, `css` e `index.html`: 0 `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`, `srcdoc`, `DOMParser`, `createContextualFragment`, `new RegExp`, `setTimeout` com texto, `postMessage`, `cssText`, `url(`, atribuição de `location`, `window.open`, `.src =`, `setAttribute` de `on*`/`href`/`src`/`style`; os 5 `.href =` são `URL.createObjectURL(blob)`; `import()` dinâmico só com caminhos fixos ou do registro interno de ações.
3. **Dinâmico (servidor real em porta efêmera, ERS sintéticos):** varredura de todas as 94 rotas (490 verificações): sem cookie → 403, `Host` errado → 403, métodos inseguros sem `Origin`, com `null`, outra porta ou outra origem → 403, corpo acima do teto → 413, `HEAD` em `/api/*` → 405. Sondas dirigidas: relógio (offsets e ids nos limites de int64), abertura parcial (`first` hostil), `plan` com caminhos hostis, download de visão (cabeçalhos), apagar caso (o que sobra no disco e no export de preferências), permissões com `umask 022`.
4. **Fuzz:** 3.000 regex aleatórias contra o validador do filtro `text.regex` (744 aceitas; pior busca 4 ms em sujeitos adversariais de 4.096 caracteres) e contra o validador do extrator; padrões patológicos clássicos no extrator (`(a+)+$`, `(a|aa)*b`, `(.*a){20}`, `(?:\w+\s?)*$`, `(a*)\1*` etc.): todos recusados com o deslocamento; pior busca de um padrão aceito: 8,6 ms. Preferências: `parse_json_bytes` e `import_package` com hypothesis (500 exemplos por teste) e pacotes construídos (1e6 níveis, 8 MB, 600 mil chaves, chaves duplicadas, NaN, BOM, UTF-16, surrogate solto, inteiros enormes): nenhuma exceção não tratada, pior caso 1,16 s e 42 MB. Painéis: `validate_panel` com hypothesis (500) e `panels.json` hostil. TOML do promote: todo code point de 0 a 0x10FFFF (exceto surrogates) faz ida e volta exata por `tomllib`.
5. **Testes:** linha de base `pytest -n 2`: 8578 passaram, 24 pulados; depois das correções: 8591 passaram, 24 pulados (13 testes novos; mutações conferidas: desligar a checagem da geração, o incremento, o `pop` do cache ou o `forget` do hub faz falhar o teste correspondente); `ruff check` e `ruff format --check` limpos.

## Achados

| ID | Severidade | Componente | Descrição | Evidência sintética | Correção proposta | Status |
|---|---|---|---|---|---|---|
| N1 | Baixa | `store/presets.py:329` (`_atomic_write`, usado também por `store/alerts.py:307`), `report/redact.py:381` | As escritas de `presets.toml`, `alerts.toml` e `redaction.toml` tinham cópias próprias da escrita atômica sem `fsync` e sem a repetição do `os.replace` em `PermissionError` que `paths.atomic_write_bytes` tem. No Windows, antivírus ou indexador segurando o arquivo virava `save_failed`; numa queda de energia podia sobrar arquivo vazio, que a leitura aceita como documento vazio: presets e regras do usuário somem sem aviso. | `os.replace` sintético que recusa a 1ª chamada com `PermissionError`: `presets.create` falhava sem repetir, enquanto `atomic_write_bytes` repete. | As três cópias passam a chamar `paths.atomic_write_bytes` (novo parâmetro `create_parents=False` para o mapa de valores, que nunca recria a pasta de um caso apagado). | corrigido |
| N2 | Baixa (privacidade) | `server/api.py:1157` (`delete_case`), cache `_case_knowledge` (`:1351-1386`), `IngestJobs._hubs` / `server/sse.py` (`ProgressHub`) | Apagar o caso limpava `alerts` e `panel_watch`, mas não (a) o cache de conhecimento do caso, que guarda os caminhos do ERS e os parâmetros da instalação (Registry) lidos dele, nem (b) o hub de progresso do caso, que retém até 5.000 eventos (`MAX_EVENTS`: progresso com nomes de fontes, cópias de alertas, `done`) e o último evento `watch`. Tudo ficava em memória até o servidor reiniciar (não vai ao disco). Além disso, uma carga de conhecimento que já tinha passado pela consulta ao cache quando o `DELETE` chegava terminava depois e republicava o caso no cache. | Teste com o cache preenchido e `DELETE /api/cases/<id>`; teste determinístico da corrida (a carga começa, o `DELETE` acontece no meio, a carga termina); hub com eventos de progresso e `watch` antes do `DELETE`. | `delete_case` remove a entrada do cache e incrementa uma geração por caso sob `_knowledge_lock`; `_knowledge` lê a geração junto com a consulta ao cache e só publica se ela não mudou. `IngestJobs.forget_case` chama `ProgressHub.forget()`, que descarta os eventos retidos e o último `watch` (não faz nada enquanto um job roda; assinantes continuam conectados e recebem só o que vier depois). | corrigido |
| N3 | Baixa (privacidade) | `store/preferences.py:61` (`SECTIONS`), `:807` (`export_package`) | O pacote de preferências exporta por padrão a seção `redaction`, com os termos de anonimização (`terms.word`, `terms.literal`, `exceptions`) em texto claro. Esses termos são, por natureza, nomes reais (cliente, host), o cabeçalho do próprio `redaction.toml` diz para não compartilhar, e o pacote foi feito para circular entre máquinas. Nenhum valor de registro, caminho de ERS ou nome de caso vai no pacote (confirmado). | Termo `acme` no perfil: `GET /api/preferences/export` devolve `"terms": {"word": ["acme"], ...}`. | Tirar `redaction` do padrão (opt-in explícito em `sections`) e/ou um aviso em `warnings[]` ("contains redaction terms") que a UI mostre antes de salvar. Muda o contrato da rota e a UI: decisão. | aberto (decisão) |
| N4 | Baixa | `store/presets.py:312`, `store/alerts.py:278`, `report/redact.py:378`, `store/preferences.py` (import) | O ler-modificar-gravar desses arquivos usa só `threading.Lock`; os painéis têm lock de arquivo entre processos (`panels.py:447`, para duas instâncias do servidor), os outros não. Com dois `dmslog serve` sobre a mesma pasta de dados, atualizações se perdem. Sem impacto de confidencialidade. | 2 processos criando 40 regras de alerta cada: 40 gravadas (80 esperadas). | Generalizar `panels._exclusive` num `paths.file_lock(data_dir, name)` (um só lock, para não inverter ordem) e usá-lo em presets, alertas, `save_profile` e `import_package`. | aberto (sugestão) |
| N5 | Baixa | `store/alerts.py:596` (`process_cycle`) | A avaliação dos alertas por ciclo não tem orçamento total: até 100 regras × 5 s (`threshold` faz duas consultas) rodam em série no job do caso, que fica preso (bloqueia nova ingestão e o apagar) por vários minutos num caso grande. Causado só pelo próprio usuário; os painéis já têm orçamento total. | Leitura de código (não medido: exige banco grande). | `Q.deadline_ceiling(monotonic() + orçamento)` em volta do ciclo e pular as regras restantes. Exige decidir o que fazer com as regras puladas (o `baseline` avança e elas perderiam os registros do ciclo): decisão. | aberto (decisão) |
| N6 | Baixa | `store/extract.py:55`, `:326-368` | `MAX_RESULT_CHARS` (8 M caracteres) limita os caracteres capturados, mas os valores ficam em duas cópias (brutos e convertidos) e são serializados de novo na resposta; com caracteres astrais o pico chega a ~100 MB por requisição, multiplicado por requisições paralelas. | Cálculo sobre o código (8 M × 4 bytes por cópia). | Manter uma só cópia (converter sob demanda ou liberar os brutos depois de `_column`) ou baixar o teto. | aberto (sugestão) |
| I10 | Info | `ingest/discover.py:483` (`_scan_folder`) | A varredura de pasta não tem teto de entradas (o zip tem 200 mil membros). `plan` ou `collections` apontados para uma pasta enorme (ou `/`) percorrem tudo numa thread. Só o operador escolhe o caminho. | `POST .../collections/plan` com `/etc` → 200 após varrer a árvore. | Opcional: aplicar `max_members` também à pasta, com mensagem própria. | aceito (sugestão) |
| I11 | Info | `server/app.py:35`, `web/index.html:6` | A CSP não define `base-uri`, `form-action` nem `object-src` (não cobertos por `default-src` nos dois primeiros). Sem impacto hoje: nenhum `<base>`, nenhum script inline, formulários tratados por JS. | n/a | Opcional: `base-uri 'none'; form-action 'none'; object-src 'none'` no cabeçalho e no `<meta>` (o `<meta>` é ponto de integração). | aceito (sugestão) |
| I12 | Info (privacidade) | `web/js/main.js:261` (`dmslog.fieldNames`), `views-stack-model.js:107` (sessionStorage por caso), `source-filter-model.js:116` | O `localStorage` guarda nomes de campos de template (só `[A-Za-z_][A-Za-z0-9_]{0,63}`, até 200, sem valores), já aceito na revisão anterior; o `sessionStorage` guarda a pilha de visões do caso (texto de busca e filtro do usuário, até 600 caracteres cada: pode conter um valor que o usuário digitou ou fixou a partir do log) e o texto do filtro de fontes. Some ao fechar a aba. Nenhuma outra chave guarda dado de log (paleta, extrator e follow tail não usam armazenamento). | Inventário das chaves gravadas. | Manter; registrado no mapa de dados abaixo. | aceito |
| I13 | Info | `parse/promote.py:178` | O texto de erro do promote (`TemplateError`, até 2.000 caracteres) inclui o texto de exemplo do log. É dado do operador, local, limitado e mostrado por `textContent`. `toml_str` emitiria `\ud800` para surrogate solto (inválido em TOML), mas a entrada já é filtrada (`_surrogate_free`); se chegasse, vira 500 genérico, nunca arquivo corrompido (o promote não grava nada). | n/a | Opcional: recusar surrogate dentro de `toml_str`. | aceito |
| I14 | Info | `store/preferences.py:820-831`, `store/panels.py:459` | Fora do modelo (mesmo usuário): um symlink no lugar de um arquivo de preferências é lido através do link e, na restauração depois de falha, o conteúdo do alvo vira arquivo comum na pasta de dados; o `os.open` do `panels.json.lock` segue symlink. | Symlink sintético: `presets.toml` restaurado como arquivo comum com o conteúdo de fora. | Opcional: `os.lstat` e recusar link com `file_invalid`; `O_NOFOLLOW` no POSIX. | aceito |
| I15 | Info | `store/preferences.py:40`, `:735-740` | Um `redaction.toml` inválido é sobrescrito pelo import (com aviso `stored_profile_invalid`), como a ARQUITETURA 10.12 item 7 documenta; a docstring do módulo diz o contrário ("invalid files are never overwritten"). | `redaction.toml` inválido + import: `applied`, termo antigo perdido. | Corrigir a docstring ou fazer `.bak` antes. | aceito (doc) |
| I16 | Info | `store/presets.py:49` (`CASE_LEAVES`) | Presets, alertas e painéis (por usuário, valem para qualquer caso) recusam folhas de caso, mas aceitam `tid` e `pid`, que são ids do ambiente. São valores digitados pelo usuário, não do log. | n/a | Avaliar incluir `tid`/`pid` em `CASE_LEAVES`. | aceito (sugestão) |
| I17 | Info | `paths.py` (`private_mkdir`) | Pastas que já existiam antes da correção do S3 mantêm o modo; arquivos dentro do caso são criados com a `umask` (`case.sqlite` 0644), protegidos pela pasta 0700. Numa instalação anterior à correção, a raiz pode continuar 0755. | `umask 022`, pasta nova: raiz, `cases/` e `cases/<id>/` 0700; `case.sqlite`, `-wal`, `-shm` 0644. | Opcional: `chmod 0700` na raiz e em `cases/` na inicialização (pré-release: poucos afetados). | aceito |
| I18 | Info | `server/app.py:371` (`_stream_download`), `report/viewexport.py` | O download da visão mantém uma conexão de leitura (um snapshot) aberta enquanto o cliente lê; um cliente lento segura o checkpoint do WAL durante um `watch`. Só o próprio navegador do operador. | n/a | Nenhuma ação. | aceito |

## Verificado e OK

**Servidor HTTP (sonda dinâmica, 490 verificações, 0 falhas reais):**

- As 94 rotas exigem cookie de sessão e `Host` válido; `POST`/`PUT`/`PATCH`/`DELETE` (inclusive `PUT`/`DELETE .../clock`, `DELETE .../collections/<g>`, `/api/panels*`, `/api/alerts/*`, `/api/preferences/import`, `/api/fs/pick`) exigem `Origin` igual à origem do app. Nenhum cabeçalho `Access-Control-*`; todo `GET` novo é só leitura (invariante I9 mantida). As duas "falhas" da varredura foram `DELETE` com corpo grande respondendo 204: o corpo é ignorado e drenado, comportamento correto.
- Corpo: teto de 256 KiB (preferências 8,4 MB, presets e favoritos com teto próprio), checado antes de ler; `/api/preferences/import` com parser estrito (`parse_json_bytes`: só UTF-8 sem BOM, profundidade e tamanho checados durante a leitura, chaves duplicadas e NaN/Infinity recusados). JSON com 100 mil níveis → 400 `package_too_complex`.
- Download em streaming (`Transfer-Encoding: chunked`) com CSP, `nosniff`, `no-store` e cabeçalhos próprios só com números; falha no meio aborta a conexão sem o bloco final (o cliente não recebe arquivo truncado como se fosse completo). ETag/304 só nos estáticos; `If-None-Match` validado por regex linear.
- Erros das rotas novas: códigos e textos fixos; o caminho digitado pelo usuário volta normalizado por `aliases` (como antes); nenhum texto de `OSError` nos painéis e preferências; fallback `internal_error`.

**Rotas novas:**

- Relógio: `offset_ms` só inteiro em ±31 dias (bool, float, texto, 2^63 → 400); `anchor`/`target` inteiros positivos ≤ INT64_MAX; grupo só `\d{1,18}`; `dry_run` só booleano; filtros `ts` em INT64_MIN/INT64_MAX com offset máximo aplicado → 200 em página, contagem e histograma (aritmética em inteiros Python, valores fora de int64 viram constante), 2^63 → 400. Escrita numa transação `BEGIN IMMEDIATE`.
- Abertura parcial: `first` validado (tipos, int64, `from < to`, 1 a 500 nomes, sem NUL); os nomes só selecionam membros listados (nunca viram caminho: `../../etc/passwd` e `C:\CON` como nome não abrem nada). Estado em `meta` no mesmo commit dos registros.
- Seletor e navegador de pastas: nada do cliente vira argumento de processo (linhas de comando fixas); caminhos revalidados (absoluto, sem controle, sem prefixos `\\?\`/`\\.\`, sem UNC no navegador, sem nomes reservados nem ponto/espaço final no Windows); `list_dir` só lê nomes e `stat`, com tetos de 5.000 entradas devolvidas e 100 mil varridas; um diálogo por vez com prazo.
- Extrator: gramática própria (sem alternância, look-around, back-reference, flags, repetições aninhadas; ≤ 512 caracteres, ≤ 16 grupos), checagem de determinismo, orçamento de passos que fixa a janela de caracteres, prazo checado antes de cada busca (estouro máximo de uma busca, ≤ ~10 ms), tetos de linhas, ids e caracteres. O filtro `text.regex` usa o validador do catálogo com janela de 512 caracteres e prazo por linha. Promote não grava arquivo; TOML com escape correto; nomes de grupo, `code` e `family` por regex.
- Formatos por `[[envelope]]`: `families.toml` é recurso empacotado (não vem do usuário), e mesmo assim validado (ASCII, `^` inicial, sem `*`/`+`/`.`, determinístico, ≤ 32 envelopes); custo linear.
- Painéis: widgets validados por `aggregate.parse_request` + `compile_filter` e revalidados na execução; ≤ 16 widgets, soma de `limit` ≤ 4.000, orçamento de 15 s com teto em todo `read_guard`; folhas de caso, `text` e `regex` recusadas; `panels.json` hostil vira `invalid` e nunca é sobrescrito; lock entre processos (`fcntl`/`msvcrt`) com timeout de 5 s (`LOCK_TIMEOUT_S`, `store/panels.py:411`; depois, `busy`), liberado se o processo morre; evento SSE `panels` só com `seq`, `to_id` e `throttled`.
- Agregação, valores distintos, comparação, repetidos: dimensões e medidas de listas fechadas, nomes de campo por regex, tetos de grupos e linhas, `read_guard` com prazo.
- CSV (visão, operações, agregação): gerado no servidor, RFC 4180, guarda de fórmula (`=`, `+`, `-`, `@`, TAB, CR recebem apóstrofo).

**Ingestão:** S1, S2, S3 e S5 corrigidos e conferidos (abaixo). Zip continua sem extração para disco, nomes normalizados, limites de membro/razão/total; a isenção de razão tem teto agregado de 8 GiB e a ingestão recusa com menos de 3× o espaço livre.

**Front-end:** sem HTML montado a partir de dados (contagens no Método); CSP do cabeçalho e do `<meta>` idênticas (fora `frame-ancestors`, que só vale no cabeçalho), sem `unsafe-inline`/`unsafe-eval`; `style.*` só com números de layout; nomes de download fixos ou sanitizados (`[A-Za-z0-9._-]`, 60 caracteres); a cor do destaque vira classe, e o servidor só aceita cores da paleta; notificação do navegador é opt-in e leva só severidade, nome da regra e contagem; vendor só uPlot 1.6.32, SHA-256 de `vendor/uplot/VENDOR.md` conferem, sem mudança desde `afcfee0`; nenhuma URL externa.

**Supply chain e CI:** nenhum `pull_request_target`; `permissions: contents: read` no topo, `contents: write` só no `publish` (tag); as 18 `uses:` fixadas por SHA de 40 caracteres; contexto do evento só por `env:`; `check_no_pack.py` no repo e em cada artefato (CI e release); `.pyz` determinístico; Python embeddable 3.13.16 com SHA-256 da página do python.org conferido a cada build; `SHA256SUMS` gerado e reconferido. O pulo dos jobs pesados em PR só de docs falha fechado.

## Privacidade

### Mapa de dados (atualizado)

| Artefato | Conteúdo | Apagado junto com o caso? |
|---|---|---|
| `cases/<id>/case.sqlite` (+ `-wal`, `-shm`) | Log inteiro, FTS, payloads, chaves, eventos, links, coleções (caminhos e GUIDs), offsets de relógio e sua origem (`meta['clock.<g>']`, com membro e linha dos dois registros), estado da abertura parcial, configuração da instalação, filtros salvos, favoritos, **regras de destaque** (v6, podem ter valores digitados) | sim |
| `cases/<id>/redaction-maps/*.map.json` | Marcador → valor real de cada export anonimizado | sim |
| `presets.toml` | Presets do usuário (filtros digitados, sem folhas de caso) | não (por usuário) |
| `alerts.toml` | Regras de alerta (filtros digitados, sem folhas de caso) | não (por usuário) |
| `panels.json` (+ `.lock`) | Painéis (consultas declarativas, sem folhas de caso) | não (por usuário) |
| `redaction.toml`, `aliases.toml` | Perfil de anonimização e pares nome real → token | não (global) |
| Pacote de preferências exportado | Presets, regras de alerta e perfil de anonimização (inclui os termos: N3); nenhum valor de registro, caminho de ERS ou nome de caso | n/a (arquivo do usuário) |
| Memória do servidor | Histórico e cooldown de alertas e avisos de painéis; cache de conhecimento (caminhos do ERS, parâmetros do Registry); hub de progresso (até 5.000 eventos: nomes de fontes, cópias de alertas, último `watch`) | sim, tudo esquecido no apagar (N2); o objeto do hub e seu contador continuam, vazios |
| Navegador | `localStorage`: tema, layout, colunas, larguras, nomes de campos, pasta navegada, preferências de UI; `sessionStorage`: pilha de visões e filtro de fontes por aba (I12) | n/a |

Sem telemetria, sem log do app, sem arquivo temporário fora das escritas atômicas (mesma pasta). Servidor só em `127.0.0.1`.

## Status dos achados do relatório anterior

| Achado | Status | Conferência |
|---|---|---|
| P1 | corrigido | Mapa gravado em `cases/<id>/redaction-maps/`; apagar o caso remove a pasta e os mapas antigos de `<dados>/redaction-maps/`; sondado: depois do `DELETE` sobra só `alerts.toml` (por usuário). |
| S1 | corrigido | `_scan_folder` aceita só `S_ISREG` (`discover.py:491`); teste com FIFO. |
| S2 | corrigido | `RATIO_EXEMPT_MAX_TOTAL_BYTES` = 8 GiB e `check_free_space` (3×) em `reader.py`. |
| S3 | corrigido | `private_mkdir` cria raiz, `cases/` e o caso com 0700 (sondado com `umask 022`); pastas preexistentes mantêm o modo (I17). |
| S4 | aceito (limitação da 0.1.0) | Inalterado: token de início reutilizável enquanto o processo roda. Revisitar pós-0.1.0. |
| S5 | corrigido | `is_reserved_case_name` compartilhado entre CLI, API e seletor de arquivos (que acrescenta `CONIN$`, `COM¹`…). |
| I1 | aberto (sugestão) | Ainda sem `timeout` de socket no `RequestHandler`. |
| I2 | aberto (sugestão) | Ainda sem `handle_error` próprio. |
| I3 | melhorado | Python 3.13.16 com hash da página oficial; manter a conferência do último 3.13.x antes de cada tag. |
| I4 | aberto (sugestão) | Dependências de dev sem versão (agora também `pytest-xdist`); cache do pip por ref fecha o envenenamento entre PRs. |
| I5 | aberto (sugestão) | `persist-credentials` ainda no padrão nos 11 checkouts; o job `publish` não faz checkout. |
| I6 | aberto (sugestão) | `delete_case` ainda não segura o hub (`try_begin`); a remoção de coleta e o relógio já usam o hub ou transação própria. |
| I7 | aceito | Sem mudança. |
| I8 | aberto (sugestão) | Controles bidirecionais ainda exibidos como estão. |
| I9 | mantido | Nenhum `GET` com efeito colateral e nenhum CORS nas rotas novas; a invariante ainda não está escrita na ARQUITETURA. |

## Correções desta entrega

- `src/dmslog/paths.py`: `atomic_write_bytes(..., create_parents=True)`; com `False` a pasta precisa existir (nunca recriada).
- `src/dmslog/store/presets.py` (e por ele `store/alerts.py`) e `src/dmslog/report/redact.py`: `_atomic_write` delega a `paths.atomic_write_bytes` (N1).
- `src/dmslog/server/api.py` (ponto de integração), N2: novo `Api._knowledge_gen: dict[str, int]`; em `_knowledge`, a geração do caso é lida sob `_knowledge_lock` junto com a consulta ao cache, e a publicação no cache só acontece se ela não mudou; em `delete_case`, depois de `panel_watch.forget_case(cid)`: `self.jobs.forget_case(cid)` e, sob `_knowledge_lock`, `self._case_knowledge.pop(cid, None)` e incremento de `_knowledge_gen[cid]`; novo `IngestJobs.forget_case`. Nenhuma outra mudança em `api.py`; `messages.py` intocado.
- `src/dmslog/server/sse.py`: `ProgressHub.forget()` (descarta eventos retidos e o último `watch`; recusa enquanto um job roda).
- Testes: `tests/unit/test_security_8_2_m9.py` (novo: repetição com alvo bloqueado e `fsync` nos três arquivos, 0600, mapa sem recriar pasta, cache esquecido no apagar, corrida determinística carga × `DELETE`, carga depois do apagar volta a ser cacheada, hub sem eventos depois do apagar, hub com job rodando mantém os eventos); `tests/unit/test_security_8_2.py` passa a interceptar `paths.tempfile.mkstemp` (a escrita do mapa agora passa por `paths`).
- Nenhum ponto de integração do front-end tocado; nenhum patch compartilhado necessário.

## Limites da revisão

- Sem rede: não consultei CVEs (Python 3.13.16 embeddable, SQLite, expat, uPlot 1.6.32) nem conferi o SHA-256 do embeddable contra o python.org.
- Windows não executado: `msvcrt.locking` dos painéis, repetição do `os.replace` com antivírus real, diálogo nativo (`GetOpenFileNameW`), junções NTFS e caminhos `\\?\` foram analisados pelo código e por testes com as regras do `ntpath`.
- Navegador não automatizado (CSP e `SameSite` pelos cabeçalhos); front-end revisado por leitura e grep, sem rodar a suíte node (nenhum arquivo do front-end mudou).
- N5 (orçamento dos alertas) e o pico de memória do N6 não foram medidos num banco grande.
- O conteúdo real dos ERS em `local-samples/` não foi lido.

## Decisões para o Jefferson

1. **N3:** tirar a seção `redaction` do export padrão de preferências (opt-in) e/ou mostrar aviso antes de salvar o pacote. Muda o contrato de `GET /api/preferences/export` e a UI.
2. **N5:** orçamento total por ciclo de alertas (proposta: 20 s) e o que fazer com as regras que não couberem (pular e perder os registros do ciclo, ou não avançar o `baseline` delas).
3. **N4:** lock de arquivo para presets, alertas, anonimização e import de preferências, se duas instâncias do `serve` na mesma pasta de dados forem um uso a suportar.
4. **N6, I10, I11, I14, I16, I17:** endurecimentos opcionais, sem efeito no uso normal; dá para juntar num PR pós-0.1.0.
5. **I9:** registrar na ARQUITETURA as invariantes "nenhum `GET` com efeito colateral, nenhum CORS".

## Linha para a seção 15 da ARQUITETURA

Formato da tabela: `| Data | Mudança | Autor |`.

| 2026-10-18 | Revisão de segurança 8.2 do delta do M9 (`docs/notas/revisao-seguranca-8-2-m9.md`): sem achado crítico, alto ou médio. Correções: `presets.toml`, `alerts.toml` e `redaction.toml` passam a usar `paths.atomic_write_bytes` (fsync e repetição do `os.replace` no Windows; novo parâmetro `create_parents=False` para o mapa de valores, que nunca recria a pasta de um caso apagado); apagar o caso também esquece o cache de conhecimento da instalação dele (`Api._case_knowledge`, com geração por caso para que uma carga em andamento não o republique) e os eventos retidos no hub de progresso (`ProgressHub.forget`). Ficam para decisão: seção `redaction` fora do export padrão de preferências (N3), orçamento por ciclo dos alertas (N5), lock de arquivo nos demais arquivos do usuário (N4). Seções 10.7, 10.12 e 12. | Opus |

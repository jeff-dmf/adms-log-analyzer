# Revisão de segurança e de privacidade (ROADMAP 8.2, issue #62)

Data: 2026-10-12. Código: `main` em `afcfee0` (esquema v5), sem mudança no `src/`. Revisor: Opus. Só dados sintéticos: nenhum valor, host ou nome de arquivo dos ERS reais aparece aqui.

**Resultado:** nenhum achado crítico ou alto. 1 médio (privacidade), 5 baixos, 9 informativos. O aceite do 8.2 ("nenhum alto/crítico aberto") está atendido; os médios e baixos ficam para decisão (seção "Decisões").

## Escopo e modelo de ameaça

- App local: servidor HTTP em `127.0.0.1`, porta aleatória, usado por um único operador; lê ERS (zip) e pastas de Log, que são **dados não confiáveis**; PC Windows corporativo sem admin e sem internet, e Linux.
- Fora do modelo: processo malicioso rodando como o mesmo usuário (já lê os bancos direto), admin local.
- U18: o export padrão é íntegro (sem anonimização); vazamento por export padrão é por desenho e não conta como achado, desde que o export fique local. Confirmado: o export da UI é um download do navegador (`Blob` + `<a download>`), e o da CLI grava só em `--out` ou na saída padrão.
- Código lido: `src/dmslog/server/`, `ingest/`, `parse/` (envelope, payload, templates), `store/` (db, filters, query, writer, bookmarks, views), `report/` (export, redact), `knowledge/` (pack, registry, installation), `aliases.py`, `paths.py`, `cli.py`, `web/` (todo o JS, `index.html`, vendor), `packaging/`, `.github/workflows/`, `tools/` (só ferramentas de desenvolvimento, fora do `.pyz`).

## Método

1. **Estático:** `ruff check --isolated --select S` (regras do bandit), sem rede: 107 ocorrências, todas triadas. 68 S608 (SQL montado com f-string) conferidas uma a uma: só constantes, placeholders `?` e nomes validados por regex; nenhum texto do cliente entra no SQL. S603/S607 só em `packaging/` e `tools/` (argumentos fixos, sem `shell=True`). S324 (sha1) é identidade de conteúdo, não segurança. S314 (XML) tratado em `knowledge/registry.py` (abaixo). Grep dirigido: nenhum `eval`, `exec`, `pickle`, `marshal`, `shell=True`, `os.system`, `yaml.load`; nenhum cliente de rede em `src/` (`urllib.request` só em `packaging/build_portable.py`, no build).
2. **Front-end:** grep de `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`, `srcdoc`, `DOMParser`, atribuição de `href`/`location`: nenhum uso com dado de log. Os helpers de elemento usam `textContent`; os `href` atribuídos são as folhas de estilo do próprio app e URLs `blob:` de download. O uPlot vendorizado usa só `textContent`.
3. **Dinâmico (servidor real, porta efêmera, dados sintéticos de `tools/make_fixture.py`):** 74 verificações, 0 falhas. Detalhe na seção "Verificado e OK".
4. **Fuzz de ReDoS:** 3.000 linhas adversariais (repetições de espaço, `a`, `<a>`, aspas, `{`/`[`, URLs, UNC, IPs, GUIDs, `chave: valor`, tags aninhadas em 300 níveis) contra a mesma quantidade de linhas benignas: ingestão 0,85 s (15,3 MB) contra 0,39 s (6,8 MB), e export anonimizado de 1.000 registros 5,3 s contra 2,1 s. Proporcional ao tamanho: sem retrocesso exponencial.
5. **Zip bomb e arquivos especiais:** zips sintéticos com zip-slip, nome absoluto, letra de unidade, UNC, duplicata por caixa, caractere de controle, tamanho declarado mentiroso, membro de 256 MiB numa linha só e membro de 256 MiB em linhas curtas; FIFO dentro de uma pasta de Log.
6. **Testes existentes:** `pytest -q`: 6889 passaram, 18 pulados.

## Achados

| ID | Severidade | Componente | Descrição | Evidência sintética | Correção proposta | Status |
|---|---|---|---|---|---|---|
| P1 | **Média** (privacidade) | `server/api.py:212` (`CaseStore.delete`), `report/redact.py:403` (`write_value_map`) | Apagar um caso não apaga os mapas de anonimização dele. Os mapas ficam em `<dados>/redaction-maps/<aleatório>.map.json`, fora da pasta do caso, e guardam o **valor real** de cada marcador e o nome do caso. Quem apaga o caso esperando remover os dados reais deixa esses valores no disco, sem prazo. Só existe quando o usuário escolheu exportar anonimizado (opção explícita, U18). | Reproduzido pela `Api` com o ERS sintético: caso `p1case`, `POST /api/export` com `selection.ids` de 50 registros e `options.anonymize: true` (200, `map_saved: true`), `DELETE /api/cases/p1case` (204): a pasta do caso some e sobra 1 `.map.json` com `"case": "p1case"` e 21 valores. | Gravar o mapa dentro da pasta do caso: `Path(data_dir) / "cases" / case / MAP_FOLDER / f"{token}.map.json"` em `write_value_map` (o `rmtree` do caso passa a levá-lo junto). Para mapas antigos, `CaseStore.delete` varre `<dados>/redaction-maps/*.map.json` e apaga os que têm `case` igual ao id (comparação `casefold`). | aberto |
| S1 | Baixa | `ingest/discover.py:466-477` (`_scan_folder`) | A varredura de pasta aceita qualquer entrada que não seja link, inclusive FIFO, socket ou dispositivo. Um FIFO com nome de log de adapter trava a ingestão para sempre (o `open` bloqueia): no `serve`, o caso fica "ingerindo" até reiniciar o processo. Exige escrita local na pasta de Log; só em Linux (no Windows um pipe nomeado não fica dentro de uma pasta). | `mkfifo <pasta>/SchneiderElectricData/OASyS/Servers/Log/dnaApp_x/DMS_INTEGRATION/WfmAdapter_1111.log`; `run_ingest` na pasta: não termina (`timeout 8` encerra com 124). | Depois do `full.stat()`: `if not stat.S_ISREG(st.st_mode): continue`. O mesmo critério vale para o arquivo único (`_single_file` já exige `is_file()`). | aberto |
| S2 | Baixa | `ingest/reader.py:49` (`RATIO_EXEMPT_MAX_BYTES`), `ingest/discover.py:31-35` | Amplificação de disco: membros com razão de compressão > 200× são aceitos até 1 GiB **cada** (decisão do 8.1, logs reais chegam lá), com teto de 64 GiB por ingestão e 200 mil membros. Um zip pequeno de linhas repetidas vira um banco grande; não há verificação de espaço livre nem teto agregado para os membros isentos. Efeito: disco cheio (negação de serviço local), sem execução nem vazamento. | Zip de 763 KiB com 256 MiB de linhas curtas: 4,19 milhões de registros, banco de 715 MiB, 44 s. Um membro de 256 MiB numa linha só falha em 0,2 s (limite de 64 MiB por registro). | Teto agregado para os membros isentos (por exemplo, soma ≤ 8 GiB por ingestão) e/ou checar `shutil.disk_usage(<pasta do caso>)` antes e a cada lote, abortando com mensagem quando o livre ficar abaixo de um mínimo. Valor do teto é decisão do Jefferson (o pior caso real medido no 8.1 tem 2,2 GB descomprimidos). | aberto |
| S3 | Baixa | `store/db.py:238` (`create_case_db`), `paths.py` | Em POSIX, a pasta de dados e os casos são criados com as permissões da `umask` (tipicamente `0755`/`0644`): `case.sqlite`, `-wal` e `-shm` ficam legíveis por outros usuários da máquina quando o `home` não é `0700` (Ubuntu antigo) ou quando `--data-dir` aponta para pasta compartilhada. O perfil e os mapas de anonimização já são gravados com `0600`; os bancos, que têm o log inteiro, não. No Windows, `%LOCALAPPDATA%` já tem ACL privada. | `umask 022`; ingestão sintética; `stat -c %A` nos arquivos: `drwxr-xr-x` na pasta, `-rw-r--r--` em `case.sqlite`, `-wal`, `-shm`. | Em POSIX, criar a raiz de dados com `mkdir(mode=0o700)` e aplicar `chmod(0o700)` à raiz `<dados>` (e a `cases/`) na inicialização de `serve`, `ingest`, `watch` e `export`; os arquivos dentro herdam a proteção da pasta. Nada muda no Windows. | aberto |
| S4 | Baixa | `server/app.py:69`, `:412-415` | O token de início vale enquanto o processo roda e pode ser trocado por sessão quantas vezes se quiser. Ele aparece no console, no histórico do navegador e, no Linux, na linha de comando do navegador aberto por `webbrowser.open` (`/proc/<pid>/cmdline` é legível por outros usuários sem `hidepid`). Como `127.0.0.1` é compartilhado entre usuários da mesma máquina, outro usuário local que veja o token abre uma sessão completa. No Windows, ler a linha de comando de processo de outro usuário exige admin. | `GET /?token=<t>` repetido: cada chamada devolve `302` e um cookie novo, sem limite. | Opção A (simples): token de uso único (invalidar em `_exchange_token`); o console passa a dizer "use a aba aberta; reinicie para nova URL". Opção B: token válido por poucos minutos e uso único depois da primeira troca. As duas mudam o fluxo de "reabrir a URL do console" e precisam de decisão. | aberto (decisão) |
| S5 | Baixa | `ingest/pipeline.py:207-211` (`validate_case_name`) | A CLI aceita como nome de caso `CON`, `NUL`, `COM1`, `nul.txt` e outros nomes reservados do Windows, que a API rejeita (`WINDOWS_RESERVED` em `server/api.py:67`). No Windows, `dmslog ingest ... --case CON` grava em um caminho de dispositivo (falha ou comportamento indefinido); em Linux o caso é criado e não aparece na UI (`CaseStore.ids` o descarta). Robustez, sem impacto de confidencialidade. | `validate_case_name("CON")` aceita; `CaseStore.validate_id("CON")` rejeita (`invalid_case_id`). | Uma única regra para os dois: `validate_case_name` rejeita `name.split(".", 1)[0].lower() in WINDOWS_RESERVED` (mover o conjunto para `ingest/pipeline.py` e importá-lo na API). | aberto |
| I1 | Info | `server/app.py:54` | O servidor não tem `timeout` de socket nem teto de threads: um cliente local que abre conexões e não manda o cabeçalho segura uma thread cada (slowloris). Só alcançável a partir da própria máquina. O corpo de requisição já tem prazo (`DRAIN_DEADLINE_SECONDS`) e teto. | Conexão aberta sem cabeçalho fica pendurada indefinidamente. | Opcional: `timeout = 60` em `RequestHandler` e `settimeout(None)` no início de `_stream_progress` (o SSE é longo). | aceito (sugestão) |
| I2 | Info | `server/app.py` (herdado de `socketserver.BaseServer.handle_error`) | Exceções não tratadas no handler (ex.: `BrokenPipeError` quando o navegador fecha a aba no meio da resposta) imprimem traceback completo no stderr do console. Contém só caminhos do código, não dados de log, mas polui o console que o usuário lê. | Observado durante a sonda dinâmica (cliente que fecha cedo). | Sobrescrever `handle_error` em `DmslogServer` para ignorar `ConnectionError` e imprimir uma linha fixa nos demais. | aceito (sugestão) |
| I3 | Info | `packaging/build_portable.py:22-25` | Python embeddable fixado em 3.13.7 com SHA-256 TOFU. Correto para reprodutibilidade; as correções de segurança da série 3.13.x (expat, `zipfile`, SQLite embutido) só entram quando a versão sobe. Não verifiquei CVEs (sem rede). | n/a | No checklist de release (`docs/RELEASE.md`): conferir o último 3.13.x e atualizar versão e hash antes de cada tag. | aceito (processo) |
| I4 | Info | `.github/workflows/ci.yml`, `release.yml`; `pyproject.toml:10` | `pip install -e ".[dev]"` instala `pytest`, `ruff` e `hypothesis` sem versão fixada. Não chega aos artefatos (o build só usa stdlib, em jobs separados), mas um pacote comprometido rodaria no job de verificação. | n/a | Opcional: fixar versões (e hashes) num `requirements-dev.txt` usado pelo CI. | aceito (sugestão) |
| I5 | Info | workflows (`actions/checkout`) | `persist-credentials` no padrão (`true`): o token só de leitura fica no `.git/config` do runner durante o job. As ações estão fixadas por SHA e as permissões são mínimas (`contents: read`; `write` só no `publish`). | n/a | Opcional: `with: persist-credentials: false` nos checkouts. | aceito (sugestão) |
| I6 | Info | `server/api.py:890-895` | Corrida entre `DELETE /api/cases/<id>` e `POST .../collections` do mesmo caso: a checagem `is_running` e o `rmtree` não são atômicos em relação ao início de um job. Só o próprio operador dispara as duas ações; no pior caso a pasta do caso reaparece parcialmente. | Não reproduzido (janela estreita). | Opcional: segurar o `hub` do caso (`try_begin`) durante o `delete`, e liberar depois. | aceito (sugestão) |
| I7 | Info | `server/app.py:62` | No Windows o socket não usa `SO_EXCLUSIVEADDRUSE`. Sequestro da porta exigiria processo do mesmo usuário (fora do modelo); `allow_reuse_address` já está desligado no Windows. | n/a | Nenhuma ação. | aceito |
| I8 | Info | `web/js` (grade, detalhe) | Caracteres de controle bidirecionais (U+202E etc.) de um log aparecem como estão. Não executam nada (`textContent`), mas podem inverter visualmente um trecho e confundir a leitura. | n/a | Opcional: na exibição, marcar U+202A–U+202E e U+2066–U+2069 com um símbolo visível. | aceito (sugestão) |
| I9 | Info | `server/app.py:224-234` | O cookie de sessão de `127.0.0.1` é enviado também a outras portas de `127.0.0.1`, e o navegador trata outra porta local como o mesmo "site" para `SameSite`. Hoje está mitigado: métodos inseguros exigem `Origin` exatamente igual à origem do app, nenhum `GET` tem efeito colateral e não há CORS (resposta não é legível por outra origem). | Sonda: `POST` com `Origin: http://127.0.0.1:<outra porta>` → 403; `OPTIONS` → 501; nenhum cabeçalho `Access-Control-*`. | Manter como invariantes (registrar em `docs/ARQUITETURA.md`): nunca criar `GET` com efeito colateral, nunca responder CORS. | aceito |

## Verificado e OK

**Servidor HTTP** (sonda dinâmica, todas passaram):

- Bind só em `127.0.0.1`; porta 0 (aleatória) por padrão; `SO_REUSEADDR` desligado no Windows.
- `Host` validado contra `127.0.0.1:<porta>` e `localhost:<porta>`: rejeitados `evil.example`, `evil.example:<porta>`, sem porta, outra porta, `localhost.`, `0.0.0.0`, `[::1]`, vazio (proteção contra DNS rebinding). A validação vem antes de tudo, inclusive da troca do token.
- Token de 256 bits (`secrets.token_urlsafe(32)`), comparação em tempo constante; troca por cookie `HttpOnly; SameSite=Strict; Path=/` e `302` que tira o token da barra de endereço; `//evil` colapsado para caminho local, `\` rejeitado (sem redirecionamento aberto).
- Sem cookie de sessão válido: 403 em tudo, inclusive `/`, estáticos e `/api/health`.
- CSRF: `POST`/`PUT`/`PATCH`/`DELETE` exigem `Origin` igual à origem do app; rejeitados sem `Origin`, `null`, outra origem, outra porta, `https`, barra final.
- Corpo: exige `Content-Length` (chunked → 411), negativo → 400, acima do teto → 413 sem ler o corpo; drenagem limitada a 8 MiB e 2 s; corpo travado não bloqueia outras requisições; JSON com 100 mil níveis → 400 (sem `RecursionError` vazando); corpo precisa ser objeto.
- Cabeçalhos em todas as respostas, inclusive 403/404: CSP `default-src 'self'; script-src 'self'; ... frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`. Nenhum `Access-Control-*`; `OPTIONS` e `TRACE` → 501.
- Estáticos: só arquivos do pacote `dmslog.web` com extensão da lista; rejeitados `..`, `%2e%2e`, `..%2f`, `\`, `:` (unidade e ADS), NUL, `//`, nomes iniciados por `_` ou `.`.
- Ids de caso: regex estrita, sem ponto/hífen final, sem nomes reservados do Windows, sem `..`, `\` ou `/`; casamento sem diferenciar caixa resolve para a pasta real; pastas que são links simbólicos são ignoradas.
- Erros: mensagens fixas e componentes normalizados por `aliases`; exceções internas viram `internal_error` sem texto da exceção; nenhum caminho do código vaza na resposta. O caminho informado pelo próprio usuário volta na mensagem de "não existe" (aceitável: é dele e é local).
- Rotas `GET` são todas de leitura; as conexões de leitura abrem com `mode=ro` e `query_only=ON`, e as consultas de filtro rodam sob `read_guard` (autorizador que só permite leitura e prazo por handler de progresso).

**Front-end:** sem `innerHTML` nem equivalentes; todo texto de log por `textContent`; CSP no cabeçalho e em `<meta>`; assets vendorizados (uPlot 1.6.32, MIT; os três SHA-256 de `VENDOR.md` conferem com os arquivos); nenhuma CDN, nenhuma requisição para fora (`connect-src 'self'`); downloads por `blob:`; `localStorage` guarda só preferências (colunas, nomes de campos de template, tema), nunca valores de log.

**Ingestão:**

- Zip nunca é extraído para disco; leitura em streaming. Nomes normalizados (`normalize_member_name`): rejeitados absoluto, unidade, UNC, `..`, controle, vazio, nome > 1.024; duplicatas por caixa abortam a listagem. Na sonda, os seis zips maliciosos geraram 0 fontes e nada foi escrito fora da pasta do caso. Membros com link simbólico são lidos como dado (nunca seguidos).
- Limites: 200 mil membros, 64 GiB declarados e lidos por ingestão, 4 GiB por membro, razão 200× (isenção até 1 GiB, ver S2), membro cifrado marcado, 64 MiB por registro. `zipfile` não entrega mais que o tamanho declarado (tamanho mentiroso → `BadZipFile`).
- Pastas: `os.walk(followlinks=False)`, links simbólicos ignorados (exceto o caso de S1); arquivos lidos com abrir-ler-fechar por bloco (não segura handle).
- XML do Registry: teto de tamanho, DTD/ENTITY rejeitados em qualquer codificação, profundidade ≤ 32, `ElementTree` não resolve entidades externas, falhas viram `RegistryError`.
- Payload XML/JSON: tokenizador linear com tetos (1 MiB para chaves, 256 chaves, profundidade 256, atributos 4.096, checagem até 2 milhões de caracteres).
- Templates: regex validadas na carga (tamanho, grupos, aninhamento, classes, repetições aninhadas proibidas), janela de 4.096 caracteres por mensagem; fuzz sem regressão.
- SQL: tudo parametrizado; filtros compilados de uma árvore validada (profundidade e número de nós limitados); FTS com sintaxe restrita (`fts_query`), prefixo mínimo e teto de termos; tentativas de injeção FTS (`OR coluna:*`, `NEAR`, parênteses, NUL, 5.000 caracteres) devolveram 200 ou 400, nunca 500; colunas pedidas validadas por regex e o caminho JSON vai como parâmetro.
- Caminho de coleção: precisa ser absoluto, ≤ 4.096 caracteres, sem NUL, existente. Pode ser UNC no Windows (gera acesso SMB, por desenho: é o operador quem digita).

**Export:** UI devolve o texto e o navegador baixa; CLI grava só em `--out` (cria a pasta pai, sobrescreve o arquivo indicado; partes com sufixo `-partNofM`) ou no stdout. Formato `.txt` (sem CSV, sem fórmula). Anonimização opcional: perfil inválido falha fechado (409), perfil e mapas gravados de forma atômica com `0600`, nome do mapa aleatório (sem valor nem caso no nome).

**Empacotamento e CI:** ações fixadas por SHA; `permissions: contents: read` no topo, `contents: write` só no job `publish`, que roda só em tag; variáveis de contexto passadas por `env:` e citadas (sem injeção de expressão no shell); guarda do pack (`check_no_pack.py`) no repo e em cada artefato; `SHA256SUMS` publicado e conferido; launchers com `python -I` (`dmslog.bat`, `dmslog.sh`); Python embeddable com SHA-256 fixado e `._pth` isolado; `.pyz` determinístico, sem `.pyc` e sem o pack. Sem dependências de runtime (`dependencies = []`), sem wheels vendorizados.

**Saídas de rede:** nenhuma no app. Só `webbrowser.open` na URL local. O pack é JSON local, lido só de `--pack-dir`, `DMSLOG_PACK_DIR`, `<dados>/knowledge/pack` ou ao lado do `.pyz`; não traz dado de cliente.

**Concorrência:** um job de ingestão por caso (`try_begin`), escrita em WAL, leituras em conexões curtas por requisição; arquivos temporários só no `_atomic_write` (mesma pasta, `mkstemp`, `os.replace`); `temp_store=MEMORY` no SQLite.

## Privacidade

### Mapa de dados

Pasta de dados: `%LOCALAPPDATA%\dmslog` (Windows), `${XDG_DATA_HOME:-~/.local/share}/dmslog` (Linux), ou `--data-dir`.

| Artefato | Conteúdo | Apagado junto com o caso? |
|---|---|---|
| `cases/<id>/case.sqlite` | Log inteiro (mensagens e continuações), índice FTS, payloads, `entry_key` (identificadores de negócio extraídos dos payloads no esquema v5: GIDs, ids de ordem etc.), eventos, links, templates, fontes (caminho do membro), coleção (caminho absoluto do ERS ou da pasta, GUID e horário do ERS), configuração da instalação (parâmetros do Registry), filtros salvos, favoritos (com notas do usuário), `meta` | sim |
| `cases/<id>/case.sqlite-wal`, `-shm` | Páginas recentes do mesmo banco (mesmo conteúdo) | sim (mesma pasta) |
| `redaction-maps/<aleatório>.map.json` | Marcador → valor real de cada export anonimizado, nome do caso, data | **não** (P1) |
| `redaction.toml` | Perfil de anonimização e termos que o usuário mandou esconder (podem ser nomes reais) | não (é global, por desenho) |
| `aliases.toml` | Pares nome real → token público mantidos pelo usuário | não (global, por desenho) |
| `knowledge/pack/` | Pack derivado da documentação; nenhum dado de log | n/a |

Fora da pasta de dados (o app não controla):

- Exports `.txt` e o JSON de favoritos baixados pelo navegador (pasta Downloads) ou gravados pela CLI em `--out`.
- `localStorage` do navegador, por origem `127.0.0.1:<porta>`: só colunas, nomes de campos de template e tema.
- Histórico do navegador: a URL de início (com o token, que morre com o processo) e `http://127.0.0.1:<porta>/`. As chamadas de API não entram no histórico; `Cache-Control: no-store` evita cache em disco.
- Console: a URL de início e, em erro, caminhos informados pelo usuário.
- O ERS ou a pasta de origem continuam onde estavam (o app só lê).
- Não há arquivo temporário, log do app, crash dump nem telemetria.

### Como apagar um caso por completo

1. Pela UI, apagar o caso (ou fechar o `dmslog serve` e remover a pasta `cases/<id>` inteira). Confirmar que a pasta sumiu, com `-wal` e `-shm`: no Windows um arquivo aberto faz o `rmtree` parar no meio (a UI devolve 409); nesse caso, fechar o `dmslog` e apagar a pasta à mão.
2. Enquanto P1 estiver aberto: em `<dados>/redaction-maps/`, apagar os `.map.json` cujo campo `"case"` é o id do caso (ou a pasta inteira, se nenhum mapa precisa ser revertido).
3. Se o caso usou termos próprios: revisar `redaction.toml` e `aliases.toml`.
4. Apagar os exports e o JSON de favoritos baixados (Downloads ou a pasta passada em `--out`).
5. O SQLite não sobrescreve as páginas apagadas e o `rmtree` não faz apagamento seguro: num disco sem criptografia o conteúdo pode ser recuperado até ser sobrescrito. Em PC corporativo com BitLocker isso fica coberto pela criptografia do volume.

## Limites da revisão

- Sem rede: não consultei bases de CVE (Python 3.13.7 embeddable, SQLite, expat, uPlot 1.6.32).
- Windows não testado nesta revisão: ACL de `%LOCALAPPDATA%`, junções NTFS em pastas de Log, `rmtree` com arquivo aberto, nomes reservados e caminhos `\\?\` foram analisados pelo código, não executados. Os itens entram no `docs/CHECKLIST-WINDOWS.md` se o Jefferson quiser.
- Navegador não automatizado: a política de CSP e `SameSite` foi verificada pelos cabeçalhos, não com um navegador real atacando de outra origem.
- O fuzz de ReDoS cobriu o envelope, os templates dos adapters e a anonimização com entradas construídas, não com mineração exaustiva de cada regex.
- O conteúdo real dos ERS em `local-samples/` não foi lido (fora do escopo e da regra do projeto).

## Decisões para o Jefferson

1. **P1:** mover os mapas para dentro da pasta do caso (proposta) e limpar os antigos no `delete`. Sem decisão de produto envolvida; só confirmar.
2. **S2:** valor do teto agregado para membros isentos da razão e/ou mínimo de espaço livre em disco antes de abortar a ingestão.
3. **S4:** token de uso único (A) ou com validade curta (B), aceitando que a URL do console deixe de servir para reabrir o app; ou manter como está (risco só em máquina Linux multiusuário).
4. **S1, S3, S5:** correções pequenas e sem efeito no uso normal; dá para juntar num PR só.

## Decisões sobre os achados (2026-10-12)

Aceite do 8.2 (ROADMAP): nenhum achado alto ou crítico. Decisões da sessão principal, dentro da autorização do Jefferson ("sem me perguntar"):

| Achado | Decisão |
|---|---|
| P1 | Corrigir na 0.1.0: mapas de anonimização dentro de `cases/<id>/` e removidos junto com o caso; limpeza dos mapas antigos órfãos no apagar. |
| S1 | Corrigir na 0.1.0: aceitar só arquivo regular. |
| S2 | Corrigir na 0.1.0: teto agregado de bytes descomprimidos dos membros isentos da razão de compressão (8 GiB por ERS) e verificação de espaço livre antes de indexar (recusa com mensagem clara se o livre for menor que 3× os bytes a indexar). |
| S3 | Corrigir na 0.1.0: raiz de dados criada com 0700 no POSIX. |
| S4 | Aceito para a 0.1.0 e registrado como limitação (alvo: PC Windows de usuário único; no Linux o token aparece no `cmdline` do navegador, visível a outros usuários locais). Revisitar pós-0.1.0 (token de uso único ou validade curta). |
| S5 | Corrigir na 0.1.0: a CLI rejeita os mesmos nomes de caso reservados do Windows que a API. |
| I1–I9 | Informativos: sem ação, salvo a conferência do último Python 3.13.x antes de cada tag. |

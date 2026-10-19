# Fila de mensagens dos adapters (`LocalPersistenceStore/*.db3`): desenho (issue #74)

Autor: Opus (arquiteto), 2026-10-19. Documento de processo (pt-BR); o app continua todo em inglês. Integra-se à ARQUITETURA pela sessão principal (lista no fim, seção 10).

Base: `docs/formato/payloads.md`, seção 10 (esquema e contagens), ARQUITETURA 1a (U12), 5, 9.1a, 9.1b, 9.3b, 10, 12 e 14. Código lido: `store/db.py` (migrações até a 7), `store/collections.py`, `store/writer.py` (`remove_group`), `ingest/discover.py`, `ingest/reader.py`, `ingest/pipeline.py` (`run_ingest`, `build_plan`), `server/api.py` (`CaseStore`, `IngestJobs._run`, `add_collection`, `get_payload`, `_pretty`), `payload/pretty.py`, `families.toml` (regra `NONLOG`), `web/js/app-actions.js` (painel `sameKey`), `case-dialogs.js`, `api.js`.

## 0. Decisões do Jefferson (fechadas, não reabrir)

1. Entra na 0.1.0.
2. Só a tabela `CURRENT_INTEGRATION_MESSAGE` (fila do Ont e do Smn Unsent). `CURRENT_CHANGE_REQUEST` (Smn Created) fica de fora.
3. A prévia do XML entra na primeira versão: o app decodifica o BLOB e mostra o XML do que seria enviado.
4. Opção explícita por caso, desligada por padrão.
5. O `.db3` só é extraído para um temporário na pasta do caso quando a opção está ligada; o temporário é apagado ao fim, inclusive em erro.

Isso substitui, na ARQUITETURA, a U12 ("depois da 1.0"), a frase da seção 12 (`mode=ro&immutable=1`) e a linha de 2026-10-08 (proposta `queue_item` sem corpo); ver seção 10.

## 1. Modelo de dados

### 1.1 Semântica: o que a fila representa

O banco mostra o **estado da fila no instante em que o ERS copiou os arquivos**. O adapter usa o SQLite em modo WAL: as transações recentes (inclusive os `DELETE` dos itens já enviados) ficam no `-wal` até o próximo checkpoint. Ler só o arquivo principal (`immutable=1`) mostra o estado do **último checkpoint**, que pode ser bem anterior; numa amostra isso mostrou itens "pendentes" que, com o WAL aplicado, já tinham saído da fila. O que o adapter via é o estado **com o WAL aplicado**. Decisão:

- **Semântica oficial = WAL aplicado.** É o único estado que corresponde ao processo.
- `-shm` **não é copiado**: é só um índice em memória compartilhada do WAL, reconstruído pelo SQLite na primeira conexão (quando nenhuma outra conexão tem o arquivo, o SQLite trunca o `-shm` e refaz o índice a partir do `-wal`). Copiá-lo só gasta disco e traz um índice de outra máquina.
- Cada banco lido registra como foi lido (`snapshot`):
  - `wal_applied`: `.db3` + `-wal` presentes e abertos com o WAL aplicado (caso normal).
  - `no_wal`: o `-wal` não existe no ERS (ou tem 0 byte). O estado é o do último checkpoint, que é também o que o adapter via se não havia WAL pendente. A UI mostra um aviso curto ("No WAL file: the state may be older than the collection").
  - `wal_failed`: abrir ou ler com o `-wal` falhou (`sqlite3.DatabaseError`: arquivo corrompido, `-wal` de outro banco). O app relê **só o principal**, em cópia nova e com `immutable=1`, e marca o banco. A UI avisa: "The WAL file could not be applied: this is the state of the last checkpoint, older than the collection".
- O SQLite valida sozinho as molduras do WAL (sal e checksum) e só aplica as transações **comitadas** e válidas; moldura inválida encerra o WAL ali sem erro. Um WAL truncado pelo ERS (cópia de arquivo em uso) aparece, portanto, como "menos transações", nunca como dado inventado. Não há como o app distinguir isso; é um limite declarado (seção 9).

### 1.2 Tabelas (migração 8)

Migração aditiva `_migrate_to_8` em `store/db.py` (a próxima livre é a 8, ARQUITETURA 5), só `CREATE TABLE IF NOT EXISTS` e índices; não toca linha nenhuma existente. Sem mudança em `schema.sql` (que é o esquema da v1).

```sql
CREATE TABLE IF NOT EXISTS queue_db(            -- um .db3 de fila lido numa coleta
  id INTEGER PRIMARY KEY,
  collection_id INTEGER NOT NULL,               -- collection(id) de onde veio (zip ou pasta); grupo = collection.ers_group
  member TEXT NOT NULL,                         -- caminho relativo do .db3 no zip/pasta, separador '/'
  cache TEXT NOT NULL,                          -- nome do cache: '__~<cache>~__<adapter>__LPS.db3' -> <cache>
  adapter TEXT,                                 -- <adapter> do nome (OntAdapter, SmnAdapter); NULL se o nome fugir do padrão
  db_size INTEGER NOT NULL, wal_size INTEGER,   -- bytes do .db3 e do -wal (NULL sem -wal)
  fingerprint TEXT NOT NULL,                    -- identidade dos arquivos lidos (seção 2.6): pula releitura igual
  snapshot TEXT,                                -- wal_applied | no_wal | wal_failed (NULL quando status <> 'ready')
  status TEXT NOT NULL,                         -- ready | not_a_queue | error
  error TEXT,                                   -- código estável (seção 2.5) quando status = 'error'
  items INTEGER NOT NULL DEFAULT 0,
  read_at TEXT NOT NULL,                        -- ISO 8601 local do PC, só informativo
  UNIQUE(collection_id, member));

CREATE TABLE IF NOT EXISTS queue_item(          -- uma linha de CURRENT_INTEGRATION_MESSAGE
  id INTEGER PRIMARY KEY,
  db_id INTEGER NOT NULL REFERENCES queue_db(id),
  item_id INTEGER NOT NULL,                     -- _INTEGRATION_MESSAGE_ID (= im_id do GEN-CCH-01)
  message_id TEXT,                              -- _INTEGRATION_MESSAGE_FINALSTATE normalizado (seção 1.4); NULL se não for GUID
  msg_type TEXT,                                -- _INTEGRATION_MESSAGE_TYPE (ex. WorksEventMessageType)
  status TEXT,                                  -- _INTEGRATION_MESSAGE_STATUS cru ('' no Smn Unsent)
  state TEXT NOT NULL,                          -- sent | pending | other (derivado de status, seção 1.4)
  file_location TEXT,                           -- _INTEGRATION_MESSAGE_FILE_LOCATION cru
  ts INTEGER,                                   -- ms do horário local (U6), de _INTEGRATION_MESSAGE_TIMESTAMP; NULL fora da faixa
  size INTEGER NOT NULL,                        -- bytes do BLOB (length(_INTEGRATION_MESSAGE_CONTENT))
  body_stored INTEGER NOT NULL,                 -- 1 = há linha em queue_body; 0 = BLOB acima do limite ou vazio
  UNIQUE(db_id, item_id));
CREATE INDEX IF NOT EXISTS ix_queue_item_order ON queue_item(db_id, ts, id);
CREATE INDEX IF NOT EXISTS ix_queue_item_state ON queue_item(state, ts, id);
CREATE INDEX IF NOT EXISTS ix_queue_item_msg   ON queue_item(message_id) WHERE message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS queue_body(          -- BLOB cru, só para a prévia; nunca indexado (seção 1.3)
  item_id INTEGER PRIMARY KEY REFERENCES queue_item(id),
  body BLOB NOT NULL);
```

Nada vai para `entry`, `entry_fts`, `entry_key`, `payload`, `event` ou `link`: a fila é uma fonte paralela, ligada ao log só por consulta (seção 4.3). A chave `meta['queue.enabled']` (`"1"` ou ausente) guarda a opção do caso (seção 2.1).

**Nunca guardados:** `_INTEGRATION_MESSAGE_USERNAME` (a coluna nem é selecionada: a consulta lista colunas fixas), `CURRENT_METADATA`, `CURRENT_CHANGE_REQUEST`, as flags `_IMPORT_CMD`/`_RESET_PROGRESS_BAR`/`_IS_REPAIR_MODE_ACTIVE`, o caminho local do temporário, o XML decodificado ou reconstruído (gerado a cada pedido, nunca persistido).

### 1.3 O corpo: decisão

Opções avaliadas:

| Opção | Custo | Problema |
|---|---|---|
| (a) recriar o temporário a cada prévia (extrair de novo o `.db3` + `-wal`) | 340 MB de disco e segundos por clique no Ont (mais o antivírus do PC corporativo varrendo o arquivo); exige o ERS ainda no mesmo caminho | lento, frágil (ERS movido ou apagado = prévia some), e repete a operação mais cara e mais arriscada (extração) a cada clique |
| (b) cifrar o BLOB no banco do caso | sem AES na stdlib; a chave ficaria ao lado do dado | segurança só aparente |
| (c) **guardar o BLOB cru em `queue_body`**, fora de qualquer índice | da ordem de 1 MB num banco grande (alguns KB por mensagem); limitado por item e por banco | o corpo passa a existir na pasta do caso |

**Decisão: (c).** Justificativa:
- A pasta do caso já guarda, em texto claro, os logs sensitive inteiros, inclusive o XML que o adapter registrou dessas mesmas mensagens (GEN-PLD-01). O BLOB não cria uma classe nova de dado sensível no disco; fica sob as mesmas proteções (pasta do usuário, `private_mkdir` 0700 no POSIX, ACL privada do `%LOCALAPPDATA%`, `--data-dir` cifrado se o usuário quiser; ARQUITETURA 12).
- A regra "corpo nunca indexado" continua valendo ao pé da letra: `queue_body` não entra em FTS, `entry_key`, `payload`, filtros, `distinct`, agregação, exportação `.txt` nem busca. Só a rota da prévia lê a tabela, um item por vez.
- Cumpre a decisão 5 sem exceção: o temporário existe só durante a leitura do banco e é apagado no `finally`; a prévia nunca toca no ERS nem cria arquivo.
- Guarda o BLOB **cru** (não o XML): uma correção no decodificador ou no tradutor vale para casos já lidos, sem reler o ERS.
- Some com o resto: remover a coleta, apagar os dados da fila (rota `DELETE`, seção 4.1) ou apagar o caso.

Limites: BLOB de até `MAX_BODY_BYTES = 8 MiB` por item é guardado; acima disso grava `size` e `body_stored = 0` (a prévia diz "too large"). Soma por banco limitada a `MAX_BANK_BODY_BYTES = 256 MiB`; itens além do limite ficam com `body_stored = 0`. Limite de itens por banco `MAX_ITEMS = 200_000` (o excedente é ignorado e o banco recebe o aviso `items_truncated` no resumo). Esta decisão consta da seção 9 como confirmação do Jefferson, porque o pedido original listava o corpo entre o que nunca é guardado.

### 1.4 Normalização dos valores (dado não confiável)

- `item_id`: `INTEGER` do SQLite; recusado (linha ignorada, contada em `skipped_rows`) se não for inteiro em `[0, 2^63)`. Na API vai como **string** decimal (13 dígitos cabem no `Number` do JS, mas string evita surpresa se outra versão usar 19).
- `message_id`: texto; aceito só se casar com `^\{?[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\}?$`; guardado **minúsculo, sem chaves** (é a forma que os templates gravam em `entry_key` com o tipo de campo `guid`, `parse/templates.py`). Outro formato: `NULL`.
- `msg_type`, `status`, `file_location`: texto decodificado como UTF-8 estrito (bytes inválidos → `NULL`), sem caracteres de controle, cortado em 128/64/256 caracteres. `BLOB` ou número nessas colunas → `str()` do número, ou `NULL` para BLOB.
- `state`: `sent` se `status` (sem espaços, sem diferença de maiúsculas) for `Sent`; `pending` se for vazio ou `NULL`; `other` para qualquer outro valor (mostrado cru; é contado como "não enviado").
- `ts`: ticks .NET (100 ns desde 0001-01-01). `ms = (ticks - 621_355_968_000_000_000) // 10_000`, sem conversão de fuso (U6: o valor é tratado como horário local do servidor, como o log). Se o valor tiver os 2 bits altos de `DateTime.ToBinary` (kind), eles são removidos antes (`ticks & 0x3FFF_FFFF_FFFF_FFFF`). Fora de `[2000-01-01, 2100-01-01)` → `NULL`. A leitura aplica o offset de relógio da coleta (9.15) como nas outras rotas (`ts_adjusted`). Se as amostras mostrarem que o adapter grava UTC, isso aparece na verificação da tarefa Q2 (seção 8) e vira uma nota na ARQUITETURA; nada é corrigido em silêncio.

## 2. Fluxo de ingestão

### 2.1 Onde a opção vive

- **Por caso:** `meta['queue.enabled'] = "1"`. Ausente = desligada (padrão; casos antigos ficam desligados sem migração de dado).
- **API:** `POST /api/cases/{case}/collections` aceita `message_queue: true|false` (opcional). `true` liga a opção do caso e lê os bancos desta coleta ao fim do job. `false` desliga a opção do caso (adições futuras não leem) e **não apaga** o que já foi lido. Ausente = usa a opção atual do caso. Valor não booleano → `400 bad_option`. A UI sempre envia o valor da caixa.
- **Coleta já adicionada sem a opção:** `POST /api/cases/{case}/queue/read {group}` lê os bancos daquela coleta (job, seção 4.1) e liga a opção do caso.
- **CLI:** `dmslog ingest --message-queue PATH` (liga e lê); sem a flag a CLI não lê bancos, mesmo com a opção do caso ligada (a CLI é explícita por execução). `dmslog watch` e `serve --watch` **nunca** leem bancos (seguem pasta `Log`, onde os bancos não estão).

### 2.2 Descoberta

`msgqueue/lps.py: find_banks(path) -> list[BankFiles]`:
- Reaproveita `discover()` (listagem por `infolist()`/`os.stat`, sem descomprimir, com a checagem de zip slip e limites estruturais que já existem). Os `.db3`, `-wal` e `-shm` já aparecem hoje como `NONLOG`, `indexable = false`.
- Um membro é banco de fila candidato se o caminho (POSIX, sem diferença de maiúsculas) casar com `(^|/)ADMS/data/save_cases/LocalPersistenceStore/[^/]+/[^/]+\.db3$`. O `-wal` é o membro de mesmo caminho + `-wal` (comparação sem diferença de maiúsculas; o ERS vem do Windows).
- Pasta `Log` solta não tem bancos: resultado vazio, sem erro.
- `BankFiles = (member, db: SourceInfo, wal: SourceInfo | None, cache, adapter)`. `cache`/`adapter` vêm do nome por `^__~(?P<cache>[^~]{1,128})~__(?P<adapter>[A-Za-z0-9_]{1,64})__LPS\.db3$`; nome fora do padrão: `cache` = nome do arquivo sem extensão (cortado em 128), `adapter = NULL`.
- Membro cifrado no zip (`limit_violation = "encrypted"`) → banco com `error = "encrypted_member"`.

### 2.3 Extração para o temporário

Para cada banco, um diretório próprio: `<dados>/cases/<case>/.queue-tmp-<uuid4 hex>/`, criado com `private_mkdir` (0700 no POSIX). Dentro, nomes **fixos** `q.db3` e `q.db3-wal`: o nome do membro nunca vira caminho no disco (zip slip impossível por construção).

1. **Espaço:** `reader.check_free_space(case_dir, db_size + wal_size)` com o fator existente; falta de espaço → `error = "insufficient_space"` para o banco (os outros seguem).
2. **Tamanho:** `MAX_DB_BYTES = 2 GiB`, `MAX_WAL_BYTES = 1 GiB` (pelo tamanho declarado **e** contado durante a cópia; o tamanho do cabeçalho do zip não é confiável). Acima → `error = "too_large"`. A regra de razão de compressão do anti zip-bomb **não** se aplica a bancos (um `.db3` com 99,5% de páginas livres zeradas comprime muito acima de 200:1); no lugar dela vale o teto absoluto. A leitura conta no `ReadBudget` da execução (64 GiB).
3. **Cópia:** zip → `ZipFile.open(member)` em blocos de 1 MiB; pasta → `open(..., "rb")` em blocos, abrir/ler/fechar (regra dos logs vivos). Arquivo de saída aberto com `os.open(..., O_WRONLY|O_CREAT|O_EXCL|O_BINARY, 0o600)`. A cada bloco checa cancelamento (seção 2.7).
4. **Pasta viva** (instalação, o adapter pode estar escrevendo): `stat` (tamanho, `mtime_ns`) do `.db3` e do `-wal` antes e depois da cópia; se algo mudou, repete (até 3 vezes); na terceira, segue com o que copiou e marca o aviso `live_copy_unstable` no banco. Ordem: `.db3` primeiro, `-wal` depois. Zip: os membros são imutáveis, sem repetição.

O original nunca é aberto pelo SQLite: só por `zipfile` ou leitura de bytes. Não há como "alterar o original".

### 2.4 Abertura e leitura

`msgqueue/lps.py: read_bank(tmp_db: Path, limits) -> BankRead`, numa conexão **própria** (nunca `store/db.connect`, que registra funções SQL do app):

```python
conn = sqlite3.connect(
    tmp_db.as_uri() + "?mode=rw", uri=True, isolation_level=None, check_same_thread=True, cached_statements=0
)
```

- **Modo `rw` na cópia privada**, não `ro`: é o único jeito portátil de o SQLite rodar a recuperação do WAL e criar o `-shm` na primeira conexão (com `ro` e sem `-shm` a abertura falha). A cópia é do app e é apagada em seguida; nada volta ao ERS.
- PRAGMAs e limites, nesta ordem, antes de qualquer leitura de tabela:
  - `conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)` (sem `ATTACH`), `SQLITE_LIMIT_LENGTH` = `MAX_BODY_BYTES + 1`, `SQLITE_LIMIT_SQL_LENGTH` = 10.000.
  - Python ≥ 3.12: `conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)`, `SQLITE_DBCONFIG_TRUSTED_SCHEMA` falso, `SQLITE_DBCONFIG_ENABLE_TRIGGER` falso, `SQLITE_DBCONFIG_ENABLE_VIEW` falso; em 3.11 (sem `setconfig`) só os PRAGMAs abaixo.
  - `PRAGMA trusted_schema=OFF`, `PRAGMA cell_size_check=ON`, `PRAGMA mmap_size=0` (sem mapeamento: no Windows um arquivo mapeado não se apaga), `PRAGMA query_only=ON` **depois** da primeira leitura (`SELECT count(*) FROM sqlite_schema`, que dispara a recuperação do WAL).
  - `enable_load_extension` nunca é chamado (fica desligado).
  - `conn.set_progress_handler(cb, 10_000)` aborta a consulta depois de `READ_TIMEOUT_S = 60` s ou no cancelamento do job.
- **Checagem do esquema** com nomes fixos (nada vindo do banco vira SQL): `SELECT type FROM sqlite_schema WHERE name = 'CURRENT_INTEGRATION_MESSAGE'` precisa ser `'table'` (uma `view` com esse nome é recusada: `not_a_queue`); `PRAGMA table_info("CURRENT_INTEGRATION_MESSAGE")` precisa ter as 7 colunas usadas. Sem a tabela → `status = 'not_a_queue'` (é o caso do Smn Created). Falta coluna → `error = "missing_columns"`.
- **Consulta única, colunas fixas** (o `USERNAME` nunca é lido):

```sql
SELECT _INTEGRATION_MESSAGE_ID, _INTEGRATION_MESSAGE_STATUS, _INTEGRATION_MESSAGE_FILE_LOCATION,
       _INTEGRATION_MESSAGE_TYPE, _INTEGRATION_MESSAGE_FINALSTATE, _INTEGRATION_MESSAGE_TIMESTAMP,
       length(_INTEGRATION_MESSAGE_CONTENT),
       CASE WHEN length(_INTEGRATION_MESSAGE_CONTENT) <= :max_body THEN _INTEGRATION_MESSAGE_CONTENT END
FROM CURRENT_INTEGRATION_MESSAGE ORDER BY _INTEGRATION_MESSAGE_ID LIMIT :max_items_plus_1
```
- `sqlite3.DatabaseError` na abertura ou na consulta com o WAL presente: fecha, apaga o `-wal`/`-shm` da cópia, copia de novo só o `.db3` para um diretório novo e abre com `?mode=ro&immutable=1` (`snapshot = wal_failed`). Falha também assim → `error = "corrupt"` (`file is not a database` → `error = "not_sqlite"`).
- `conn.close()` explícito em `finally`, **antes** de apagar o diretório.

### 2.5 Gravação e erros por banco

`store/queue.py: replace_bank(conn, collection_id, member, bank: BankResult) -> int` numa transação `BEGIN IMMEDIATE` por banco: apaga as linhas antigas daquele `(collection_id, member)` (corpo, itens, banco) e grava as novas. Um banco com erro grava só a linha de `queue_db` (`status = 'error'`, `error = <código>`, `items = 0`) e apaga os itens antigos dele, para a tela nunca misturar uma leitura velha com um erro novo.

Códigos de erro (estáveis, `messages.py` traduz): `encrypted_member`, `too_large`, `insufficient_space`, `extract_failed`, `not_sqlite`, `corrupt`, `missing_columns`, `read_timeout`, `read_failed`. Avisos (não impedem `ready`, vão no resumo): `live_copy_unstable`, `items_truncated`, `bodies_truncated`, `skipped_rows`.

**Um banco com erro nunca derruba o job nem os outros bancos**; o `done` do job continua `ok` se os logs foram bem, e traz o resumo da fila (seção 2.7).

### 2.6 Releitura

`fingerprint` = zip: `crc32:size` do `.db3` e do `-wal`; pasta: `size:mtime_ns` dos dois. Reabrir o mesmo ERS com a opção ligada compara o `fingerprint` guardado: igual e `status <> 'error'` → não extrai nada (custo zero, sem temporário). Diferente, ou erro anterior → relê.

### 2.7 Onde roda, ordem, progresso e cancelamento

- `msgqueue/ingest.py: read_collection(conn, case_dir, path, collection_id, *, limits, progress, cancelled) -> QueueSummary`. Não entra no `run_ingest` (que fica intocado): é chamado **depois** dele pelo `IngestJobs._run` (`server/api.py`) e pelo `cmd_ingest` (`cli.py`) quando a opção vale para aquela execução.
- **Ordem:** depois da segunda passada (ou depois da primeira, com `defer_rest`). Assim a fila nunca atrasa o `first_ready` da abertura parcial (9.3b). O job `resume` não lê bancos.
- **Progresso:** eventos `progress` com `stage: "queue"`, `source` = nome do arquivo do banco (só o nome), `bytes_done/bytes_total` da cópia; `status: "queue_done"` por banco. O `done` ganha `queue: {dbs, items, pending, errors}` (ou `null` quando não houve leitura).
- **Cancelamento:** o callback de progresso já levanta `JobCancelledError` quando o servidor para; a cópia o chama a cada bloco e o `progress_handler` do SQLite consulta `cancelled()`. O cancelamento passa pelo mesmo `finally` de limpeza.
- **Concorrência:** um job por caso (o `ProgressHub` do caso já garante). As rotas de leitura da fila usam o `CaseStore.reader` somente leitura e não dependem do job.
- **Tempo:** por banco, cópia limitada pelo tamanho (340 MB ≈ 1–4 s em SSD; o antivírus do Windows pode somar) + leitura limitada a 60 s. Sem limite global além do orçamento de bytes da execução.

### 2.8 Limpeza do temporário (sempre)

```python
tmp = make_tmp_dir(case_dir)  # .queue-tmp-<hex>, 0700
try:
    copy_files(...)
    result = read_bank(...)  # read_bank fecha a conexão no seu próprio finally
finally:
    remove_tmp_dir(tmp)
```

- `remove_tmp_dir`: `shutil.rmtree` com até 5 tentativas espaçadas de 50 ms se vier `PermissionError` (Windows: antivírus ou indexador segurando o arquivo por instantes; mesma ideia de `paths.atomic_write_bytes`). Se ainda falhar, deixa o diretório para a varredura e anota o aviso `tmp_left` no log do servidor (sem caminho na UI).
- **Varredura de sobras (queda do processo):** `sweep_tmp(case_dir, older_than_s=3600)` apaga todo `.queue-tmp-*` com `mtime` de mais de 1 hora. Roda no início de cada `read_collection` e quando o servidor lista os casos pela primeira vez (`CaseStore.ids()` não muda; chamada no início do `Api`). O limite de 1 hora evita apagar o temporário de uma CLI rodando ao mesmo tempo (a leitura de um banco leva segundos). Só casa nomes `^\.queue-tmp-[0-9a-f]{32}$` dentro da pasta do caso, sem seguir symlink (`os.scandir`, `is_dir(follow_symlinks=False)`).
- O tamanho do caso (`CaseStore.summary`) já conta tudo da pasta; um temporário esquecido aparece ali.

## 3. Decodificador NBFX

### 3.1 Módulo e API pública

`src/dmslog/msgqueue/nbfx.py`, Python puro, sem dependência do resto do app.

```python
@dataclass(frozen=True)
class Node:
    name: str                                   # nome local
    prefix: str                                 # '' sem prefixo
    xmlns: tuple[tuple[str, str], ...]          # declarações (prefixo, uri) neste elemento; prefixo '' = padrão
    attrs: tuple[tuple[str, str, str], ...]     # (prefixo, nome, valor texto)
    children: tuple["Node | Text", ...]

@dataclass(frozen=True)
class Text:
    value: str                                  # já convertido para o texto XML canônico (seção 3.3)
    kind: str                                   # chars|int|bool|double|float|decimal|datetime|guid|uniqueid|timespan|bytes|empty

class NbfxError(ValueError):
    code: str     # bad_header|truncated|unknown_record|dictionary_record|bad_length|bad_name|bad_utf8|
                  # too_large|too_deep|too_many_nodes|text_too_large|unbalanced|trailing_data
    offset: int   # posição do byte no BLOB; a mensagem nunca leva conteúdo do BLOB

def decode(blob: bytes, limits: Limits = Limits()) -> Node     # raiz; levanta NbfxError
def to_xml(root: Node, *, max_chars: int = 4_000_000) -> str  # XML 1.0, escapado, sem declaração <?xml?>
```

`Limits`: `max_bytes = 8 MiB`, `max_depth = 256`, `max_nodes = 200_000` (elementos + textos + atributos), `max_text = 1 MiB` por texto, `max_total_text = 16 MiB`, `max_attrs = 256` por elemento. Laço iterativo com pilha explícita (sem recursão), uma passada, O(n).

### 3.2 Cabeçalho

Os 12 primeiros bytes não são NBFX. Aceitos só se começarem por `00 04 00 00 01 06 00 01` (assinatura observada); os 4 seguintes são ignorados; o byte 12 tem de ser um registro de elemento. Outra assinatura → `bad_header`. O significado exato dos 12 bytes é desconhecido (risco R3); a assinatura fica numa constante e o teste sintético usa a mesma.

### 3.3 Registros suportados ([MS-NBFX])

| Registro | Bytes | Tratamento |
|---|---|---|
| `EndElement` | `0x01` | fecha o elemento atual; sem elemento aberto → `unbalanced` |
| `Comment` | `0x02` | lido e descartado |
| `Array` | `0x03` | elemento + `EndElement` + tipo + contagem (int31) + valores; tipos `Int16/32/64`, `Float`, `Double`, `Decimal`, `DateTime`, `Guid`, `TimeSpan`, `Bool` (os que o `DataContractSerializer` emite como array binário); gera N elementos irmãos |
| `ShortAttribute` / `Attribute` | `0x04` / `0x05` | nome (e prefixo) + um registro de texto |
| `ShortXmlnsAttribute` / `XmlnsAttribute` | `0x08` / `0x09` | declaração de namespace |
| `PrefixAttributeA..Z` | `0x26`–`0x3F` | prefixo `a`..`z` + nome + texto |
| `ShortElement` / `Element` | `0x40` / `0x41` | nome (e prefixo) |
| `PrefixElementA..Z` | `0x5E`–`0x77` | prefixo `a`..`z` + nome |
| textos | `0x80`–`0xBD` | par = texto; ímpar = texto + `EndElement` |

Textos: `Zero`/`One`/`False`/`True` (`0`, `1`, `false`, `true`); `Int8/16/32/64` e `UInt64` (decimal); `Float`/`Double` (`repr` do Python, com `INF`, `-INF`, `NaN` como no XML Schema); `Decimal` (16 bytes do `System.Decimal` → texto exato); `DateTime` (int64: 62 bits de ticks + 2 de kind; texto `yyyy-MM-ddTHH:mm:ss[.fffffff]` e sufixo `Z` para UTC; para kind local, **sem** offset, porque o fuso do servidor é desconhecido, nota `datetime_local`); `Chars8/16/32` (UTF-8 estrito); `UnicodeChars8/16/32` (UTF-16LE); `Bytes8/16/32` (base64); `EmptyText`; `UniqueId`/`Guid` (16 bytes na ordem do .NET → `8-4-4-4-12`); `TimeSpan` (duração ISO 8601, `P…T…S`, como o `XmlConvert`); `Bool`; `StartListText` … `EndListText` (itens separados por espaço).

Comprimentos e contagens: `MultiByteInt31` (7 bits por byte, no máximo 5 bytes, valor ≤ 2^31−1; mais que isso → `bad_length`). Nomes: UTF-8 estrito e precisam casar com o `Name` do XML 1.0 (regex), senão `bad_name`.

**Dicionário:** todos os registros com dicionário (`0x06`, `0x07`, `0x0A`, `0x0B`, `0x0C`–`0x25`, `0x42`–`0x5D`, `0xAA`, `0xBC`) → `NbfxError("dictionary_record")`. Não há dicionário estático na 0.1.0: as amostras usam só strings inline. Se outra versão do ADMS gravar com dicionário, a prévia mostra o erro claro e a lista continua funcionando.

**Registro desconhecido** (`0x00`, `0xBE`–`0xFF` e o que sobrar) → `unknown_record` com o offset. Bytes depois do fechamento da raiz → `trailing_data`. Fim do BLOB com elementos abertos → `truncated`.

### 3.4 Saída

`decode` devolve a árvore (`Node`); o tradutor (seção 3.6) trabalha nela. `to_xml` serializa qualquer árvore (a decodificada ou a traduzida) com escape de `& < > "` e troca de caractere proibido no XML 1.0 por `U+FFFD` (nota `invalid_chars`); o resultado passa por `payload/pretty.format_payload(text, "xml")` (indentação e validação de boa formação; nunca resolve entidade nem DTD).

### 3.5 Testes sem dado real

`tests/support/nbfx_writer.py`: escritor NBFX **sintético** (o inverso do decodificador, só para testes), com o cabeçalho de 12 bytes, todos os registros da tabela, arrays e, para os testes de erro, registros de dicionário e bytes inválidos. Monta mensagens no formato observado (raiz `WorksEventMessageType` com `xmlns` do datacontract e `xmlns:i`, `headerField`/`payloadField`, pares `…FieldSpecified`, `i:nil`), com valores inventados (`<guid>` gerado por `uuid.UUID(int=n)`, textos `text-1`). Nenhum byte vem das amostras.

### 3.6 Tradução para o XML de rede

`src/dmslog/msgqueue/translate.py`. O BLOB é o objeto .NET serializado pelo `DataContractSerializer`; o adapter envia o mesmo objeto pelo `XmlSerializer`. A tradução reconstrói o segundo a partir do primeiro.

```python
@dataclass(frozen=True)
class Reconstruction:
    root: Node                      # árvore no formato de rede
    basis: str                      # exemplar | catalog | none (de onde vieram nomes, ordem e namespaces)
    exemplars: int                  # quantos payloads do caso foram usados como exemplar
    notes: tuple[str, ...]          # códigos estáveis (abaixo), ordenados

def reconstruct(decoded: Node, catalog: Catalog, shape: Shape | None) -> Reconstruction
```

Regras, em ordem:

1. **Raiz.** O nome do elemento raiz do BLOB é o tipo CLR (`WorksEventMessageType`). O nome na rede é `Capitalize(verbo) + substantivo`, com verbo e substantivo lidos de `headerField/verbField` e `headerField/nounField` (ex.: `changed` + `Works` → `ChangedWorks`, que é a raiz que o log mostra em GEN-PLD-01). Sem verbo ou substantivo: raiz = tipo sem o sufixo `Type` e nota `root_guessed`.
2. **Sufixo `Field`.** Todo filho cujo nome termina em `Field` vira o membro sem o sufixo. Filho sem o sufixo (itens de array, nomeados pelo tipo CLR) mantém o nome.
3. **`Specified`.** Para cada `xField` com irmão `xFieldSpecified`: emite `x` só se `xFieldSpecified` for `true`; o elemento `…Specified` nunca é emitido.
4. **Nulos.** Elemento com `i:nil="true"` (namespace `http://www.w3.org/2001/XMLSchema-instance`) é omitido. O atributo `i:nil` e as declarações `xmlns` do datacontract e de `i` nunca vão para a saída.
5. **`any`.** `anyField` e `anyAttrField` nulos: omitidos. Não nulos: o conteúdo é emitido no lugar, como veio, e nota `any_content`.
6. **Primeira letra.** O `xsd.exe` gera o campo com a primeira letra minúscula, então `crewField` pode vir de `Crew` (associação, maiúscula no CIM) ou de `crew`; `mRIDField` vem de `mRID`. A caixa é resolvida, nesta ordem: (a) pelo exemplar do caso (6.1); (b) pelo catálogo estático; (c) heurística: maiúscula se o nome no catálogo de classes CIM conhecidas existir com maiúscula, senão mantém minúscula; e nota `names_guessed`.
7. **Arrays.** Um campo cujos filhos são todos itens (sem sufixo `Field`, todos com o mesmo nome de tipo, ou do namespace `http://schemas.microsoft.com/2003/10/Serialization/Arrays`) é array. Forma na rede: pelo exemplar (embrulhado `<SwitchingSteps><SwitchingStep>…` ou plano `<Property>…<Property>…`); sem exemplar, o catálogo; sem nenhum dos dois, embrulhado (espelha o BLOB, que é o caso observado em `switchingStepsField > SwitchingStep`) e nota `array_form_guessed`. Itens primitivos (`string`, `int` do namespace de arrays) recebem o nome do membro na forma plana.
8. **Ordem.** O `DataContractSerializer` grava os campos em **ordem alfabética**; o `XmlSerializer`, na ordem do XSD. A ordem vem do exemplar (ordem dos filhos observada) e, para os tipos do cabeçalho IEC 61968-100 (`Header`, `Payload`, `Reply`, `Request`), do catálogo; membro sem posição conhecida vai para o fim, na ordem do BLOB, e nota `order_partial`. Sem nenhuma informação de ordem num elemento: alfabética, nota `order_alphabetical`.
9. **Namespaces.** Raiz e filhos diretos: o namespace da mensagem (`http://iec.ch/TC57/2011/<Noun>Message`, do catálogo por `msg_type`); filhos de `Header` (e de `Reply`/`Request`): `http://iec.ch/TC57/2011/schema/message`; conteúdo de negócio dentro de `Payload`: do exemplar; sem exemplar e sem entrada no catálogo, sem namespace e nota `payload_namespace_unknown`. Emissão no estilo do `XmlSerializer`: `xmlns="…"` padrão no elemento onde o namespace muda (como no exemplo do Wfm em `payloads.md`, seção 4); prefixos não são reproduzidos.
10. **Valores.** Texto como decodificado (3.3). Valores de enum ficam como gravados (nome do membro CLR; o `XmlEnum` da rede pode diferir), nota `enum_as_stored` quando o catálogo marca o campo como enum ou o valor tem forma de identificador gerado (`Item\d+`). `DateTime` local sai sem offset (nota `datetime_local`). Campos `xs:date` saem com hora (o tipo do XSD não está no BLOB), nota `datetime_format` quando o exemplar mostra só data naquele campo.
11. **Atributos.** Membro que o exemplar mostra como atributo XML é emitido como atributo; sem exemplar, tudo é elemento, nota `attributes_unknown` só se o catálogo listar atributos para o tipo.

#### 3.6.1 Exemplar do próprio caso

`src/dmslog/msgqueue/exemplar.py`: aprende a **forma** (não os valores) dos payloads que o adapter registrou no log do mesmo caso.

- Seleção (`store/payloads.py`, só leitura): até `EXEMPLARS = 5` payloads mais recentes com `fmt = 'xml'`, `well_formed = 1`, `role = 'request'`, `direction = 'out'` e `root` (sem prefixo) igual à raiz reconstruída (`ChangedWorks`); se não houver, a mesma busca por qualquer verbo do mesmo substantivo (`%Works`). Texto limitado a 512 KB por exemplar.
- Leitura só por **tokens de tag** com o tokenizador próprio de `payload/pretty.py` (exposto como `pretty.tokenize`, um invólucro público de `_tokenize`): nenhum parser de XML, nenhuma entidade resolvida.
- Saída `Shape`: por caminho de nomes locais (`ChangedWorks/Payload/Works/Work`), a ordem dos filhos (fusão estável da ordem vista), o namespace de cada elemento, os nomes com a caixa exata, os atributos, e a forma de cada array (filho repetido direto = plano; elemento único que só contém repetições = embrulhado).
- Cache em memória por `(caso, raiz, ingest_generation)` no processo do servidor, limitado a 64 entradas. Os valores nunca são guardados; a `Shape` só tem nomes, URIs e ordem.
- Sem exemplar (log sem nível de payload, ou caso sem o sensitive), `basis = 'catalog'` ou `'none'`.

#### 3.6.2 Catálogo estático

`src/dmslog/msgqueue/catalog.toml` (no repositório; só nomes e namespaces **públicos** da IEC 61968-100/CIM, nenhum dado de cliente nem da documentação Schneider):

```toml
schema = 1
[header]                                   # IEC 61968-100 (2011), namespace http://iec.ch/TC57/2011/schema/message
order = ["Verb", "Noun", "Revision", "ReplayDetection", "Context", "Timestamp", "Source", "AsyncReplyFlag",
         "ReplyAddress", "AckRequired", "User", "MessageID", "CorrelationID", "Comment", "Property"]
flat_arrays = ["Property"]
[payload]
order = ["OperationSet", "Compressed", "Format"]          # o conteúdo de negócio vem antes (posição 0)

[[message]]
type = "WorksEventMessageType"
noun = "Works"
namespace = "http://iec.ch/TC57/2011/WorksMessage"
# payload_namespace = "..."  # só quando confirmado por exemplar (nome público da IEC)
```

Entradas iniciais: `WorksEventMessageType`, `SwitchingStepsEventMessageType`, `SwitchingPlansEventMessageType`, `IncidentsEventMessageType`, `IncidentTroubleTicketsEventMessageType`. A ordem do `[header]` acima vem do XSD público e é **conferida** na tarefa Q4 contra a ordem aprendida dos exemplares das amostras (só nomes, por script, sem valores); divergência corrige o catálogo antes do PR. `msg_type` fora do catálogo: namespace da mensagem pela regra `http://iec.ch/TC57/2011/<Noun>Message`, nota `catalog_missing`.

#### 3.6.3 O que não é garantido

A prévia **não** é o byte enviado: ordem de elementos do negócio sem exemplar, caixa da primeira letra, forma dos arrays, enums, formato de data/hora e de `double`, prefixos e posição das declarações de namespace, espaços, declaração `<?xml?>` e envelope SOAP (que o log também não mostra) podem diferir. A UI sempre rotula (seção 5.3) e lista as notas. Quando o log tem o payload real da mesma mensagem (`payload.msg_id = message_id`), a UI oferece abri-lo: esse é o texto que de fato foi registrado.

## 4. API HTTP e CLI

Texto de erro em `messages.py`; códigos estáveis. Todas as rotas exigem o cookie (ARQUITETURA 12), leem pelo `CaseStore.reader` e respondem com `Cache-Control: no-store`. Caso v7 ainda não migrado (servidor só leitura): as rotas de leitura respondem como "opção desligada e vazio" (tabelas ausentes não são erro).

### 4.1 Rotas

| Rota | Resposta / erros |
|---|---|
| `POST /api/cases/{case}/collections` `{path, scope, first, message_queue}` | como hoje; `message_queue` opcional (seção 2.1); não booleano → `400 bad_option` |
| `POST /api/cases/{case}/queue/read` `{group}` | `202 {job_id}`: job que lê os bancos da coleta (todos os membros do grupo) e liga a opção do caso; `404 collection_not_found`; `409 job_running`; `400 invalid_field`; `409 collection_path_missing` (zip/pasta da coleta não existe mais no caminho registrado) |
| `DELETE /api/cases/{case}/queue?group=` | apaga os dados de fila (`queue_body`, `queue_item`, `queue_db`) da coleta, ou de todas sem `group`, numa transação; não muda a opção. `200 {dbs, items}`; `409 job_running`; `404 collection_not_found` |
| `GET /api/queue/summary?case=` | `{enabled, dbs: [<db>], totals: {items, pending, sent, other}}`, `<db> = {id, collection, tag, name, cache, adapter, snapshot, status, error, warnings, items, pending, sent, other, read_at}`. `name` = só o nome do arquivo (nunca o caminho); `collection` = grupo, `tag` = `C<n>` (`collections.group_tags`) |
| `GET /api/queue/items?case=&collection=&db=&state=&type=&q=&limit=&after=` | `{items: [<item>], next}`. `collection`/`db`: ids (listas separadas por vírgula, até 64); `state` ⊂ `pending,sent,other`; `type`: lista de `msg_type` exatos (até 32, ≤ 128 caracteres); `q`: `message_id` (GUID, com ou sem chaves, qualquer caixa) ou `item_id` (só dígitos) exatos. Ordem `(ts, id)` crescente (FIFO; `ts` nulo primeiro), keyset com o cursor `"<ts>:<id>"` de sempre (nunca `OFFSET`), `limit` padrão 200, máximo 1.000. Erros `400 bad_limit`, `bad_cursor`, `bad_state`, `bad_type`, `bad_query`, `invalid_field` |
| `GET /api/queue/items/{id}/preview?case=&form=wire\|stored` | `{item: <item>, form, available, reason, decode: {ok, error, offset}, reconstruction: {basis, exemplars, notes}, formatted: {text, ok, kind, reason, truncated_display}}`. `form=wire` (padrão) = reconstrução da rede; `stored` = XML decodificado como gravado (sem tradução, `reconstruction = null`). `available = false` com `reason` `too_large` \| `no_body` quando não há corpo; falha de decodificação = `200` com `decode.ok = false` e `error` (código NBFX), nunca 5xx. `404 queue_item_not_found`; `400 bad_form`; id não numérico `400 bad_queue_item_id` |

`<item> = {id, db, collection, tag, item_id, message_id, msg_type, status, state, file_location, ts, ts_adjusted, size, preview: "available"|"too_large"|"no_body", log: {msgid, im_id, payload_id}}`.

`log` é calculado por página (≤ 1.000 itens, buscas indexadas): `msgid` = registros com `entry_key(key_type='msgid', value=message_id)` (contagem limitada a 1.000; também a forma maiúscula, porque chaves vindas de payload preservam a caixa, `parse/payload._ident`); `im_id` = registros com `entry_key('im_id', str(item_id))` (GEN-CCH-01); `payload_id` = o `payload.id` mais recente com `msg_id` igual ao `message_id` (sem diferenciar caixa) e `direction = 'out'`, ou `null`. O pior caso (1.000 itens × 3 buscas no índice) cabe no `PAGE_TIMEOUT_S` atual.

A rota da prévia: lê o corpo de `queue_body`, `nbfx.decode`, (se `wire`) `exemplar` + `translate.reconstruct`, `nbfx.to_xml` e o mesmo `_pretty(text, "xml", …)` já usado por `GET /api/payloads/{id}`. Tudo sob `read_guard` com tempo limite; nada é gravado.

SSE: além do que já existe, `progress` com `stage: "queue"` e o `done` com `queue` (seção 2.7). A UI recarrega o painel da fila no `done`.

### 4.2 CLI

- `dmslog ingest --message-queue PATH [--case NAME] [--all-logs]`: depois da indexação, lê os bancos e imprime uma linha `Message queue: <dbs> databases, <items> items, <pending> not sent, <errors> errors`.
- Sem comando de listagem nem prévia na CLI (a 0.1.0 entrega isso no viewer).

## 5. UI

Tudo em inglês, strings em `strings.js` (prefixo `mq`), só `textContent`/nós de texto (os valores vêm de banco não confiável). Módulos carregados sob demanda (`import()`), fora do grafo estático: `js/queue-model.js` (puro), `js/queue-panel.js` (DOM), `css/queue.css` (entra no `css/bundle.order.txt`).

### 5.1 Opção na criação do caso e no Add files

- Caixa no diálogo New case e no Add files (`js/case-dialogs.js`), abaixo da escolha do escopo: **"Read message queue databases (LocalPersistenceStore)"**, desmarcada no New case; no Add files, marcada conforme `GET /api/queue/summary` → `enabled`.
- Texto de ajuda sob a caixa (sempre visível, `aria-describedby`): "Reads the adapters' pending message queues from the ERS. The databases are copied to a temporary file in the case folder and deleted after reading; this can take a few seconds and up to the size of the database on disk. Message bodies are sensitive: they stay in the case folder and are never searched or exported."
- A caixa envia `message_queue` no `POST .../collections`. Desmarcar num caso ligado mostra a linha "Queues already read stay in the case. Remove them in Message queue."
- Menu ⋯ da coleta: "Read message queue…" (`POST .../queue/read`), habilitado sem job rodando.
- Progresso: a barra de status mostra "Reading message queue databases… (<name>)" durante `stage: "queue"`.

### 5.2 Aba da doca "Message queue"

Painel `messageQueue` da doca (registrado em `app-actions.js` pela sessão principal; ação `analyze.messageQueue` no menu Analyze; `listens: ["case", "derived"]`; `closeOnCase: false`).

- **Cabeçalho:** contador "<n> not sent" (pendentes + outros) e "<m> items" no total, por `summary.totals`; botão "Remove queue data…" (confirmação em dois passos como em Remove collection; chama o `DELETE`).
- **Bancos:** uma linha por banco: `C<n>`, adapter, cache, itens, não enviados, e os avisos (`no_wal`, `wal_failed`, `live_copy_unstable`, `items_truncated`) ou o erro, com texto de `strings.js` por código. Bancos `not_a_queue` ficam numa linha recolhida "Other databases (not queues): <k>".
- **Filtros:** estado (Not sent [padrão] / Sent / All), tipo (select com os `msg_type` presentes), coleta (quando há mais de uma), busca exata por Message ID ou item ID. Filtros vão para a query da rota de itens.
- **Lista:** `<table>` com `<caption>` visualmente oculto, colunas Time (formatada como o grid, UTC lido como local, U6, com o offset da coleta), Collection (só com mais de uma coleta), Type, State, Message ID (abreviado, completo no `title`), Item ID, Size, In log (contagem `msgid`). Paginação "Load more" pelo `next`. Linha focável; Enter ou clique abre a prévia; setas movem a seleção.
- **Ações por item** (botões na linha selecionada e no menu de contexto):
  - **"Show in log"**: abre o painel `sameKey` com `{type: "msgid", value: message_id}` (mesmo caminho de "Records with the same key", UI-24). Desabilitado com `log.msgid = 0` (`title`: "This message ID is not in the indexed logs").
  - **"Show cache records"**: idem com `{type: "im_id", value: item_id}` (GEN-CCH-01). Desabilitado com `log.im_id = 0`.
  - **"Open logged payload"**: abre o visualizador de payload existente (`js/payload.js`) com `payload_id`. Só aparece quando há.
- **Prévia (painel à direita da lista, ou abaixo em tela estreita):**
  - Faixa fixa no topo, sempre visível: **"Reconstruction, may differ from the bytes sent."** e a base (`Based on <k> payloads logged in this case` / `Based on the built-in catalog` / `No reference`), mais as notas como lista curta (texto por código em `strings.js`).
  - Alternância "Reconstructed message" / "Stored form" (`form=wire|stored`), `role="radiogroup"`.
  - `<pre>` com o `formatted.text`, `tabindex="0"`, região com `aria-label` "Message preview"; selo "sensitive" (o mesmo dos payloads).
  - Botão "Copy" (área de transferência; status "Copied"); a prévia não é salva em nenhum lugar.
  - Erros: `available = false` → "The body is larger than the preview limit (<size>)." ou "No body stored for this item."; `decode.ok = false` → "This message body could not be decoded (<code>)." (o código NBFX, sem dados).
- **Estados vazios:**
  - opção desligada: "Message queue databases are not read for this case." + botão "Read message queue…" (pede a coleta quando há mais de uma);
  - ligada, sem bancos: "No message queue databases were found in this case's collections.";
  - bancos lidos, nenhum item: "The queues were empty when the ERS was collected.";
  - filtro sem resultado: "No items match the filters.";
  - job lendo: "Reading message queue databases…".
- **Acessibilidade:** contador anunciado uma vez por carga (região viva `polite` do painel, não a global); botões com nome acessível completo ("Show item 1234567890123 in log"); foco volta à linha ao fechar a prévia; contraste do selo e dos avisos nos dois temas por variáveis CSS existentes.

### 5.3 Rótulo da reconstrução

O texto "Reconstruction, may differ from the bytes sent." é obrigatório em toda exibição da forma `wire`, inclusive na cópia: o botão Copy copia só o XML, e o status mostra "Copied (reconstruction)".

## 6. Privacidade e segurança

1. **Sensível por padrão:** opção desligada; bancos nunca lidos sem pedido explícito por caso (API, UI) ou por execução (CLI).
2. **Username nunca lido:** a consulta tem lista fixa de colunas; não há `SELECT *`. Atenção: o corpo da mensagem pode conter `Header/User` (o usuário da mensagem): ele aparece na prévia, como já aparece no payload do log sensitive.
3. **Corpo nunca indexado:** `queue_body` fora de FTS, `entry_key`, `payload`, filtros, `distinct`, agregação, painéis, comparação, exportação e busca; lido só pela rota da prévia, um item por vez.
4. **Exportação `.txt` e anonimização:** a fila não entra no `.txt` (nenhuma seleção de exportação a inclui na 0.1.0); a redação (U18, ferramenta de desenvolvimento) não se aplica. O app não anonimiza a prévia. A cópia da prévia é ação do usuário, como copiar um payload.
5. **Fixtures:** só sintéticas (`nbfx_writer`, `lps_fixtures`); nenhum `.db3`, BLOB, GUID, tipo de cliente ou nome de cache real em `tests/`, docs, PRs ou issues. A checagem de anonimização (Haiku) roda sobre o delta.
6. **Temporário:** dentro da pasta do caso (privada), diretório `0700` com nome aleatório, arquivos `0600` criados com `O_EXCL`, nomes fixos (nada do zip vira caminho), apagado no `finally`, varredura de sobras com mais de 1 hora; nunca em `%TEMP%`/`/tmp`.
7. **Zip slip e bombas:** listagem pelo `discover()` (que já recusa nomes inseguros); o membro só é lido, nunca extraído pelo nome; teto absoluto de tamanho contado na cópia; orçamento de bytes da execução.
8. **SQLite seguro:** conexão própria (sem as funções do app); `ATTACH` impossível (`SQLITE_LIMIT_ATTACHED = 0`); `load_extension` nunca habilitado; `trusted_schema=OFF`; `DEFENSIVE`, sem gatilhos e sem views quando o Python tem `setconfig`; `query_only` após a recuperação do WAL; `mmap_size=0`; `cell_size_check=ON`; tabela e colunas com nomes fixos no SQL e a tabela conferida como `type = 'table'`; `progress_handler` com tempo limite; nada vindo do banco é concatenado em SQL; banco do usuário nunca recebe escrita.
9. **Decodificador:** limites de tamanho, profundidade, nós, texto e atributos; sem recursão; erro sem conteúdo do BLOB; XML de saída escapado; `pretty.py` nunca resolve entidades.
10. **Viewer:** só `textContent`; nenhum valor da fila em `innerHTML`, `title` montado com string concatenada é texto puro; `Cache-Control: no-store` nas rotas.
11. **Logs do servidor e mensagens de erro:** só códigos e nomes de arquivo (com os apelidos locais, `describe_error`), nunca valores de coluna nem caminho do temporário.

## 7. Testes

**Fixtures sintéticas** (`tests/support/`):
- `nbfx_writer.py` (seção 3.5).
- `lps_fixtures.py`: `make_bank(dir, items, *, wal_items=None, extra_free_pages=0)` cria um `.db3` com `CURRENT_INTEGRATION_MESSAGE` (todas as colunas, `USERNAME` preenchido com `user-<n>` para provar que nunca é lido), `CURRENT_METADATA`, `journal_mode=WAL`, `wal_autocheckpoint=0`; grava as linhas base, faz checkpoint, aplica `wal_items` (inserções e deleções) e **copia `.db3` e `-wal` com a conexão ainda aberta** (no fechamento o SQLite faria checkpoint e apagaria o WAL). Isso reproduz sinteticamente o caso "14 no principal, 0 com WAL". `make_change_request_bank` (só `CURRENT_CHANGE_REQUEST`, para `not_a_queue`). `make_ers_zip(...)` monta o zip com os bancos em `SchneiderElectricData/ADMS/data/save_cases/LocalPersistenceStore/0000000000000000/` com nomes inventados no padrão `__~TestQueueSmartCache~__TestAdapter__LPS.db3`, mais um log sintético.

**Casos:**
- NBFX: ida e volta de todos os registros e tipos de texto; arrays; listas; `i:nil`; `Specified`; cada código de erro (`bad_header`, `truncated` em cada ponto, `unknown_record`, `dictionary_record`, `bad_length`, `bad_name`, `bad_utf8`, `too_deep`, `too_many_nodes`, `text_too_large`, `unbalanced`, `trailing_data`); propriedade (hypothesis): árvore aleatória → escritor → `decode` devolve a mesma árvore; bytes aleatórios nunca levantam outra exceção que não `NbfxError` e terminam dentro do limite. Exemplos no CI: 50 (variável de ambiente aumenta localmente); sem laço sem fim e sem árvore gigante (limite de RAM da suíte, memória `feedback_heavy-suites-oom`).
- Tradução: cada regra da 3.6 (raiz por verbo/substantivo, sufixo, `Specified`, nil, `any`, caixa por exemplar/catálogo/heurística, array plano e embrulhado, ordem por exemplar/catálogo/alfabética, namespaces, notas); exemplar construído de um payload sintético no formato do log (`payloads.md`, seção 4); a saída passa no `format_payload` com `ok = true`.
- Leitura de banco: WAL aplicado × `no_wal` × `wal_failed` (WAL corrompido de propósito, WAL de outro banco); `not_a_queue`; `view` com o nome da tabela; colunas faltando; banco que não é SQLite; `too_large` (limites injetados, nunca um arquivo grande no CI); `insufficient_space` (injeção em `check_free_space`); membro cifrado; normalização (GUID com chaves e maiúsculas, ticks fora da faixa, textos com controle, BLOB acima do limite → `body_stored = 0`); `USERNAME` ausente do que foi gravado (busca no banco do caso inteiro pelo valor sintético); `items_truncated`.
- Temporário: apagado após sucesso, após exceção injetada na cópia, na abertura, na consulta e na gravação, e após cancelamento; varredura apaga só `.queue-tmp-*` antigo e não segue symlink. **Windows:** os mesmos testes rodam no CI do Windows; a conexão é fechada antes do `rmtree` (um teste espião confirma a ordem `close` → `rmtree`); `rmtree` com `PermissionError` simulado tenta de novo e, se persistir, deixa para a varredura sem falhar o job.
- Store: migração 7 → 8 (idempotente, transacional, não mexe em nada existente; caso v7 lido só para leitura responde "desligado"); `replace_bank` substitui; `remove_group` apaga a fila da coleta e não a das outras; `DELETE` da fila.
- API: contrato de cada rota, paginação por keyset estável, filtros, códigos de erro, `log` (contagens e `payload_id`), prévia `wire`/`stored`, prévia sem corpo, decodificação falha → 200; job `queue/read` e `409 job_running`; `done.queue`.
- Ingestão: reabrir o mesmo zip com o mesmo `fingerprint` não cria temporário (espião em `make_tmp_dir`); a leitura da fila não atrasa `first_ready` (ordem dos eventos); job com banco em erro termina `ok` com `queue.errors = 1`.
- Web (`node --test`): `tests/web/queue-model.test.mjs` (estado vazio por resposta, montagem da query dos filtros, rótulos das notas, texto dos erros por código, habilitação das ações por `log`); `test_web_assets.py` cobre o módulo novo (sessão principal).

## 8. Decomposição em tarefas

Regras do projeto: implementadores Sonnet, arquivos disjuntos declarados, sem commit; a sessão principal é dona de `strings.js`, `api.js`, `api.py`, `messages.py`, `index.html`, `main.js`, `app-actions.js`, `tests/unit/test_web_assets.py`, `tests/web/boot-graph.test.mjs`, `css/bundle.order.txt` (e o `bundle.css` gerado), `cli.py` (usa `messages.py`), tabela de mudanças e numeração da ARQUITETURA. Um PR por vez a partir da `main` atual.

**Lote 1 (paralelo, 4 Sonnet; nenhuma dependência entre eles: a árvore `Node`/`Text` está fixada na seção 3.1)**

| Tarefa | Arquivos (exclusivos) | Aceite |
|---|---|---|
| **Q1 Decodificador NBFX** | `src/dmslog/msgqueue/__init__.py`, `msgqueue/nbfx.py`, `tests/support/nbfx_writer.py`, `tests/unit/test_nbfx.py`, `tests/property/test_nbfx_properties.py` | seção 3.1–3.5 integral; todos os códigos de erro testados; propriedade de ida e volta; cobertura de linhas ≥ 95% do módulo; `ruff` limpo |
| **Q2 Leitura do banco e temporário** | `msgqueue/lps.py`, `tests/support/lps_fixtures.py`, `tests/unit/test_lps_read.py` | seções 2.2–2.4, 2.8 e 1.4; WAL aplicado reproduzido sinteticamente; temporário sempre apagado (sucesso, 4 pontos de falha, cancelamento); `USERNAME` nunca lido; script local (não commitado) imprime, nas amostras, só contagens por banco e a diferença mediana entre `ts` do item e o 1º registro do log com o mesmo `msgid` em minutos (verifica a hipótese de horário local, 1.4); resultado em uma linha no PR |
| **Q3 Esquema e store** | `store/db.py` (só `_migrate_to_8` e `MIGRATIONS`), `store/queue.py` (novo: `replace_bank`, `delete_queue`, `summary`, `list_items`, `get_body`, `log_links`), `store/writer.py` (só `remove_group` + a docstring), `tests/unit/test_store_queue.py`, `tests/unit/test_db.py` (só casos da migração 8) | seções 1.2, 2.5, 2.6, 4.1 (lado do banco); keyset estável; `remove_group` limpa a fila; caso v7 só leitura tolerado |
| **Q4 Tradução e exemplar** | `msgqueue/translate.py`, `msgqueue/exemplar.py`, `msgqueue/catalog.toml`, `payload/pretty.py` (só o invólucro público `tokenize`), `tests/unit/test_queue_translate.py`, `tests/unit/test_queue_exemplar.py` | seção 3.6 integral, com árvores montadas à mão (sem depender de Q1); ordem do `[header]` conferida por script local nas amostras (só nomes) e divergências corrigidas; `format_payload` ok em todas as saídas de teste |

Q1 e Q4 tocam `msgqueue/` em arquivos diferentes; `__init__.py` é de Q1 (vazio, só docstring).

**PR A** (depois do lote 1): Q1 + Q4 (módulos puros). Sessão principal: integra, lint e suíte completa, revisão cruzada T1. Q2 e Q3 ficam no worktree aguardando.

**Lote 2 (paralelo, 2 Sonnet; depende do lote 1 concluído)**

| Tarefa | Arquivos (exclusivos) | Aceite |
|---|---|---|
| **Q5 Orquestração e serviço** | `msgqueue/ingest.py` (`read_collection`, `sweep_tmp`), `msgqueue/service.py` (funções puras chamadas pelas rotas: validação de parâmetros com `QueueParamError(code)`, montagem de `summary`/`items`/`preview` a partir de `store/queue.py`, `nbfx`, `exemplar`, `translate`), `tests/unit/test_queue_ingest.py`, `tests/unit/test_queue_service.py` | seções 2.1 (lado do serviço), 2.5–2.8, 4.1 (formato JSON, erros por código); fingerprint pula releitura; erro por banco não derruba; ordem `first_ready` antes da fila (teste com `run_ingest` real + `read_collection`) |
| **Q6 UI** | `web/js/queue-model.js`, `web/js/queue-panel.js`, `web/css/queue.css`, `web/js/case-dialogs.js` (caixa e texto de ajuda), `web/js/collections-panel.js` (item "Read message queue…"), `tests/web/queue-model.test.mjs` | seção 5 contra o contrato da 4.1 (com um `api` falso nos testes); entrega no PR a lista de strings novas (`mq*`) e as funções de `api.js` necessárias, como texto para a sessão principal colar |

**Sessão principal, depois do lote 2** (pontos de integração): rotas e `IngestJobs._run`/`start` com `message_queue` em `api.py` (e `sweep_tmp` no início do `Api`); `--message-queue` em `cli.py`; textos em `messages.py`; `api.js` (`queueSummary`, `queueItems`, `queuePreview`, `queueRead`, `queueRemove`, `addCollection` com `message_queue`); `strings.js`; registro do painel e da ação em `app-actions.js`; `css/bundle.order.txt` + `tools/build_web_css.py`; `test_web_assets.py` e `boot-graph.test.mjs` (módulo fora do grafo estático); ARQUITETURA e ROADMAP (seção 10 abaixo).

**PR B**: Q2 + Q3 + Q5 + integração do backend (rotas, job, CLI, `messages.py`). Revisão cruzada T2 (toca ingestão, esquema e arquivo não confiável).

**PR C**: Q6 + integração da UI. Revisão cruzada T1. Antes de abrir o PR C: **revisão de segurança curta do delta** (Sonnet, `docs/notas/revisao-seguranca-fila.md`, roteiro da seção 6 item a item, mais a varredura Haiku de anonimização das fixtures e dos textos do PR); achados críticos/altos corrigidos no próprio PR C.

## 9. Riscos e pergunta ao Jefferson

**Resposta do Jefferson (2026-10-19): sim, guardar o BLOB cru em `queue_body` (decisão fechada).** A pergunta original segue abaixo, só como registro.

**Pergunta (indispensável):**
1. **Guardar o BLOB cru no banco do caso (`queue_body`)?** O pedido listava o corpo entre o que nunca é guardado; a decisão 3 (prévia) e a 5 (temporário apagado) juntas só se cumprem bem guardando-o (seção 1.3). **Recomendação: sim**, fora de qualquer índice e da exportação, removido com a coleta, com a rota "Remove queue data". Se a resposta for não, a alternativa é a (a) da 1.3 (reextrair a cada prévia, lenta e dependente do ERS no lugar), que muda Q3, Q5 e a UI da prévia.

**Riscos:**
- R1. **Par `.db3`/`-wal` inconsistente no ERS** (o ERS copia arquivos em uso): o SQLite só aplica molduras válidas, então o pior caso é um estado mais antigo, nunca inventado; `wal_failed` cai para o checkpoint e avisa. Não detectável além disso.
- R2. **Horário dos itens** (ticks local × UTC): verificado por script em Q2. Se for UTC, o app continua sem converter fuso (U6): entra uma nota na ARQUITETURA e um aviso fixo no painel ("Queue times may be in UTC"), sem ajuste automático.
- R3. **Cabeçalho de 12 bytes desconhecido** e **dicionário**: outra versão do ADMS pode mudar ambos; o efeito é a prévia com erro claro, a lista continua.
- R4. **Fidelidade da reconstrução** sem exemplar no caso (log sem nível de payload): ordem e caixa podem divergir; mitigado pelo rótulo, pelas notas e pelo link ao payload logado quando existe.
- R5. **Disco e tempo no Windows corporativo:** 340 MB temporários por alguns segundos, mais a varredura do antivírus; mitigado por checagem de espaço, `fingerprint` (não relê o mesmo ERS) e progresso visível.
- R6. **Esquema do banco em outras versões** (nomes de coluna): `missing_columns` por banco, sem quebrar o caso.

## 10. O que a sessão principal integra na documentação

- ARQUITETURA 1a: U12 revista (2026-10-19: entra na 0.1.0, decisões 1–5 da seção 0 deste documento).
- ARQUITETURA 5: tabelas `queue_db`, `queue_item`, `queue_body`; migração 8; chave `meta['queue.enabled']`.
- ARQUITETURA 9.1a: a frase dos `.db3` ("não indexado", "proposta pós-1.0") passa a remeter a esta nota; 9.1b: `remove_group` também apaga a fila.
- ARQUITETURA 10: as rotas da 4.1 e os campos novos do SSE.
- ARQUITETURA 12: troca a frase `mode=ro&immutable=1` pelo resumo da seção 6 (cópia de `.db3` + `-wal`, WAL aplicado, conexão endurecida, BLOB fora de índice).
- ARQUITETURA 15: linha da mudança (substitui a proposta de 2026-10-08).
- ROADMAP: P3 sai de "aguarda decisão" e vira itens Q1–Q6 na 0.1.0, com os modelos da seção 8.
- `docs/formato/payloads.md`: seção 10 (nota sobre o WAL: a contagem "Smn Unsent 3" foi feita sem o WAL) e seção 12 item 2 (codificação descoberta: NBFX).

# R3 e R4: arquivos gigantes e arquivos fora de escopo (desenho)

Desenho (Opus) da R3 ("volume") e da R4 ("arquivos fora de escopo") da frente RT (`docs/ROADMAP.md`, achado 4 de `docs/notas/rt-r1-achados.md`), para implementação por agentes Sonnet. Medições de 2026-10-24, por script, com leitura em streaming dos membros dos zips RT de `local-samples/` (nada extraído para o repositório; RAM limitada a 6 GB; RSS de pico dos scripts ≈ 130 MB). Nenhum dado real aqui: só números agregados; exemplos sintéticos.

## 1. R3: o que foi medido

### 1.1 Como o app trata hoje os dois arquivos

| Arquivo (ERS RT Secondary) | Descomprimido | Comprimido | Classificação hoje | Com "All logs" |
|---|---|---|---|---|
| `PSengine.log` | 7.669 MB (7,14 GiB) | 157 MB (≈ 49×) | `PSENGINE`, `limit_violation = too_large` (passa do limite de 4 GiB por membro, `DEFAULT_MAX_MEMBER_BYTES`) | **não entra**: fica listado e não é selecionável |
| `PSengine.ALTERNATE_1.log` | 1.614 MB | 33 MB (≈ 49×) | **`SERVERLOG`** (a regra `PSENGINE` usa o layout `single`, que só aceita `PSengine.log`) | entra inteiro e **sem os templates PSE** (genérico) |

No ERS RT Primary o `PSengine.log` tem 65 KB e não é problema. Nos 10 zips de `local-samples/` (INT e RT), a maior fonte indexável fora o PSengine tem 166,8 MB; **só os dois arquivos PSengine passam de 256 MiB**.

### 1.2 Forma dos arquivos

| Medida | `PSengine.log` | `PSengine.ALTERNATE_1.log` |
|---|---|---|
| Registros | 30.538.852 | 6.425.757 |
| Linhas de continuação | 0 | 0 |
| Bytes por registro | ≈ 251 | ≈ 251 |
| Período coberto | 371,6 h (15,5 dias) | 80,9 h |
| Crescimento | 20,5 MB/h (mediana por hora; p90 22,1; máx. 22,1), linear do início ao fim | 20,0 MB/h (p50 = p90) |
| Registros por hora | ≈ 82 mil (≈ 23/s) | ≈ 79 mil |
| Horário que volta (registro com `ts` menor que o anterior) | **0** | **0** |
| Lacunas > 5 min | 1 (6,4 min, no primeiro minuto do arquivo: partida) | 0 |
| Registros sem template (PSENGINE) | 0 | 1 |
| Assinaturas distintas (texto com dígitos normalizados) | 37 | 20 |

O `ALTERNATE_1` começa no mesmo minuto em que o `PSengine.log` para de ser escrito: o PSengine alterna entre os dois arquivos. A causa da troca (tamanho, reinício) não se vê nesta amostra; não importa para o desenho, porque cada arquivo é tratado como fonte própria.

Leitura no zip: descomprimir o membro de 7,67 GB e só contar as quebras de linha leva **7,4 s (≈ 1.034 MB/s)**. Pular o começo de um membro de zip custa isso; não há `seek` em deflate.

### 1.3 O laço e onde está a informação útil

- **99,978 % dos registros são o laço de falha de peer**: os templates `PSE-001`, `PSE-002` e `PSE-003` (falha de `recv`, em 3 linhas por tentativa), cada um com ≈ 10,18 milhões de registros, de 3 peers (60 %, 37 % e 3 % do volume). Nível do envelope `DEBUGLOG`; os templates têm `sev = 2` e já estão fora do FTS (`fts = false`).
- **Não são linhas idênticas consecutivas**: as 3 linhas de cada peer se alternam com as dos outros peers (sequências de assinatura igual de tamanho 1 em 99,99999 % dos casos). Uma janela das últimas 64 assinaturas contém a assinatura do registro em 97,4 % dos registros. Ou seja: um laço de ciclo curto, não uma rajada de linhas iguais.
- **O resto (0,022 %, 6.867 registros)**: o trio `PSE-005`/`PSE-021`/`PSE-022` (peek falhou, socket seguro não criado, cabeçalho não reconhecido), 2.271 vezes cada, ≈ a cada 9,8 min, dois deles com nível `ERROR`; `PSE-014` (peer novo, 16), `PSE-015` (thread iniciada, 32), `PSE-010` (máquina declarada morta, 3), configuração da partida (`PSE-030`, `PSE-031`) e 19 registros `ALWAYS`.
- **Distribuição no arquivo**: os registros fora do laço estão espalhados de forma uniforme (por décimo do arquivo: de 549 a 903, crescendo um pouco para o fim). A única informação concentrada é a **partida**: 13 dos 19 `ALWAYS` e a configuração estão nos primeiros 10 KiB.
- Conclusão: **o fim do arquivo tem os mesmos tipos de evento que o meio**; o começo tem a partida. Nada no meio justifica indexar 15 dias de laço por padrão.

### 1.4 Custo de indexar

Medido: os últimos 256 MiB do `ALTERNATE_1`, como arquivo solto num caso novo em disco (`/home`), família `PSENGINE`:

| Medida | Valor |
|---|---|
| Registros | 1.068.837 |
| Tempo total | 19,2 s (14,0 MB/s), dos quais derivação 7,6 s (0 eventos gerados) |
| RSS de pico | 234 MB |
| Banco | 419 MB (1,56× o log; o laço não vai ao FTS) |

Projeção linear (o custo por registro é estável, nota do 8.1):

| Cenário | Tempo | Banco | Espaço livre exigido (3×) |
|---|---|---|---|
| `PSengine.log` inteiro (se o limite de 4 GiB caísse) | ≈ 550 s | ≈ 12 GB | ≈ 23 GB |
| Os dois arquivos inteiros | ≈ 670 s | ≈ 14,5 GB | ≈ 28 GB |
| `ALTERNATE_1` inteiro (o que "All logs" faz hoje, sem templates) | ≈ 115 s | ≈ 2,5 GB | ≈ 5 GB |
| **Proposta: 1 MiB do começo + 256 MiB do fim, em cada um** | **≈ 47 s** (2 × 19 s + 7,4 s para pular o membro grande + 1,6 s para o menor) | **≈ 0,85 GB** | ≈ 1,6 GB |

Contra as metas do 8.1: o ERS RT com "All logs" e os arquivos inteiros fica ≈ 4× acima do maior ERS medido (170 s) e o banco passa de 14 GB; com a proposta o acréscimo é de ≈ 47 s e < 1 GB, RSS na faixa já medida (< 0,5 GB). O escopo padrão não muda (PSENGINE continua opcional).

Observação lateral: a derivação de 1 M registros PSENGINE leva 7,6 s sem gerar evento (40 % do tempo). Fica para a R6 (só otimização): pular derivadores sem regra para a família.

## 2. R3: decisão

### 2.1 Fonte reduzida: começo + fim, com teto por fonte

Uma fonte indexável cujo tamanho passa do **teto por fonte** (`source_cap_bytes`, padrão **256 MiB**) é indexada **reduzida**: os registros que começam no primeiro **1 MiB** (`head_bytes`, a partida) e os registros do fim do arquivo a partir do primeiro cabeçalho em `tamanho − teto`. O trecho do meio é **pulado**: lido só para contar linhas (para que `line_no` continue sendo a linha real do arquivo), nunca analisado nem gravado.

- **Genérico**: vale para qualquer família (não só PSENGINE). Com os dados medidos, só os dois arquivos PSengine são afetados; nenhum log do escopo padrão chega perto (máx. 166,8 MB).
- **Configurável**: CLI `--max-source-mb N` (0 = sem teto, arquivo inteiro), opção `source_cap_mb` no `POST /api/cases/{case}/collections` e na rota `plan`, e um seletor no diálogo de abertura (seção 2.4). O teto vale para a fonte **na primeira indexação** e fica gravado; mudar o teto depois não reprocessa (seção 2.3).
- **Por que bytes e não horas**: o custo (tempo, banco, espaço livre) é proporcional a bytes, e o teto em bytes dá custo previsível para qualquer arquivo. 256 MiB do PSengine são ≈ 12,5 h do laço, a janela típica de um ERS tirado logo depois do incidente. O intervalo de horário do que foi pulado é mostrado na interface (seção 2.4).
- **Amostragem do laço com contagem (descartada para a 0.1.0)**: guardar só os N primeiros registros de cada assinatura por hora reduziria mais (≈ 500×), mas quebra "o banco tem todas as linhas do trecho indexado" e exige estado por chave que atravessa o checkpoint da derivação (o mesmo motivo que fez a 10.9 calcular repetidos na consulta). O filtro `repeat` (10.9) e o atalho da seção 2.5 já escondem o laço na leitura.
- **Expansão no lugar (descartada)**: reservar ids para o buraco (como a 9.3b) permitiria "indexar o resto" depois, mas um buraco de 29 milhões de ids para 1 milhão de registros estraga a estimativa por fatias de id (`query._sample_estimate`, `distinct`, `aggregate`: quase toda fatia cairia no buraco). Para ter o arquivo inteiro: remover a coleta e adicioná-la de novo com "Whole file".

### 2.2 Algoritmo de leitura (uma passada)

Entrada: fonte com `size > source_cap_bytes`, UTF-8 (fonte UTF-16 acima do teto continua `too_large`, como hoje; não há nenhuma nas amostras).

1. `T = size − source_cap_bytes`. Se `T ≤ head_bytes`, a fonte é indexada inteira (caminho normal).
2. **Começo**: registros cujo cabeçalho começa em `[0, head_bytes)`, cada um com as suas continuações (o último registro do começo termina no próximo cabeçalho, mesmo que passe de `head_bytes`). Fim do começo = `h`.
3. **Meio pulado** `[h, lo)`: `lo` = início da primeira linha de **cabeçalho** em `≥ max(T, h)` (linhas de continuação antes dele pertencem a um registro que começou no meio e são puladas com ele). No meio só se faz `bytes.count(b"\n")` por bloco e se guarda o horário do último cabeçalho visto (para o intervalo pulado); no zip, a descompressão continua (é o custo de 7,4 s por 7,67 GB); em pasta, o arquivo é lido por blocos com abrir, ler e fechar a cada bloco (regra dos logs vivos: `_file_chunks` já faz isso), sem `seek` sobre o meio, porque a contagem de linhas exige ler.
4. **Fim**: indexação normal de `[lo, EOF)`, com `line_no` = linhas do começo + linhas puladas + 1 em diante. O primeiro registro do fim recebe o bit novo `flags & 128` (`FLAG_AFTER_SKIP`, "primeiro registro depois de um trecho pulado"); `flags & 32` (horário volta) é calculado contra o último registro do começo, como sempre.
5. Ids **contíguos**: os do fim vêm logo depois dos do começo (a ordem do arquivo se mantém).

**Estado** (sem migração de esquema): `meta['capped.<source_id>']` = JSON `{cap, head_bytes, h, h_line, lo, lo_line, skipped_bytes, skipped_lines, skipped_from_ts, skipped_to_ts, size_at_cap}`, gravado **na mesma transação do primeiro lote do fim** (antes disso a fonte está em `indexing` e uma queda recomeça a fonte do zero, como hoje). Depois, a fonte é uma fonte `ready` normal: `indexed_bytes = EOF lido`, `head_hash` dos 4 KiB iniciais e `tail_hash` dos 4 KiB antes de `indexed_bytes`, como em 9.3.

**Retomada no meio do fim**: igual à indexação normal (apaga o último registro gravado e retoma do `byte_off` dele); o `line_no` vem do registro, não de recontagem.

**Depois da primeira indexação** (decisão: o teto vale só na primeira vez):
- arquivo que cresce (pasta viva, `watch`): `append` normal a partir de `indexed_bytes`; não se reaplica o teto (o banco cresce ≈ 20 MB/h × 1,56 no PSengine ao vivo; aceitável, PSENGINE é opcional);
- truncado, reescrito ou novo arquivo depois de rotação: a fonte é indexada do zero **e o teto é reaplicado** com o valor gravado na coleta (`meta['source_cap.<grupo>']`), não o da execução;
- membro de zip igual (`zip_crc` + `size`): nada.

**Derivação**: o detector de lacunas (`derive/lifecycle.py`, `gap`) não emite lacuna que termina num registro com `flags & 128`; janelas e operações abertas que atravessam o buraco ganham `detail.skipped_range = true` (duração não confiável). Nenhum outro derivador muda.

**Limites de leitura** (`ingest/reader.py`): o limite por membro (4 GiB) passa a valer para os **bytes indexados**; os bytes pulados de uma fonte reduzida contam num limite próprio, `max_skipped_member_bytes` (padrão 64 GiB), e todos contam no orçamento da execução (64 GiB) e na regra da razão de compressão, como hoje. `discover` só marca `too_large` uma fonte reduzível se ela passar desse limite próprio. A checagem de espaço livre (3×) usa os bytes que serão indexados.

**Abertura parcial (9.3b)**: fonte reduzida nunca é aberta por janela de tempo (a janela é ignorada para ela e ela entra inteira, reduzida, na primeira passada se estiver entre as fontes escolhidas, senão na segunda). Evita juntar dois esquemas de ids.

### 2.3 Garantias (testes obrigatórios)

1. **Determinismo**: para o mesmo arquivo e o mesmo teto, o resultado (registros com `line_no`, `byte_off`, `flags`, chaves, eventos, `bucket`, `unknown_sig`, linhas de `source`, `meta['capped.*']`) é o mesmo com qualquer tamanho de bloco e com queda em qualquer leitura de bloco ou commit do writer, retomada depois. Teste de propriedade (hypothesis) no molde de `tests/property/test_partial_open_properties.py`.
2. **Incremental = completo**: arquivo reduzido que depois cresce K bytes dá o mesmo resultado que a referência "começo `[0,h)` + fim `[lo, tamanho novo)` com os mesmos `h` e `lo`" construída num caso novo; corte em todos os pontos do crescimento.
3. **Linhas reais**: `line_no` de cada registro do fim = linha real no arquivo (comparado com a contagem direta no fixture).
4. **Reabrir não escreve nada.** Remover a coleta apaga `capped.*` e `source_cap.*`.
5. **Sem lacuna falsa**: família sintética com `gap_heartbeat` e buraco de dias: 0 eventos `gap` no buraco.
6. **Fonte pequena não muda**: abaixo do teto, banco idêntico ao de hoje (teste com os fixtures existentes).
7. **Windows**: suíte no CI de Windows; fixtures sintéticos de poucos MB com teto e começo pequenos (o teto é parâmetro).

### 2.4 Como a interface avisa

Textos em inglês, centralizados (`strings.js` no front-end, `messages.py` na CLI e na API). Chaves sugeridas:

| Onde | Chave | Texto |
|---|---|---|
| Diálogo de abertura, seletor | `capLabel` | `Large files` |
| | `capDefault` | `First 1 MB and last 256 MB (default)` |
| | `capLarge` | `First 1 MB and last 1 GB` |
| | `capWhole` | `Whole file (slow, large database)` |
| Pré-visualização das fontes (`plan`) | `previewCapped` | `Over the {cap} per-file limit: only the first {head} and the last {cap} will be indexed.` |
| Lista e árvore de fontes, selo | `srcReducedBadge` | `Reduced` |
| Selo, `title` | `srcReducedTitle` | `Indexed the first {head} and the last {tail} of {size}. Skipped {skipped} in between ({lines} lines, {from} to {to}). Per-file limit: {cap}.` |
| Barra de status ao fim da ingestão | `ingestReducedNote` | `{n} large files were indexed in part (limit {cap} per file). See Sources.` |
| Detalhe do registro com `flags & 128` | `detailAfterSkip` | `Lines before this record were skipped: {lines} lines ({skipped}), {from} to {to}.` |
| Seletor de escopo, ajuda de "All logs" | `scopeAllHint` | `Includes PSengine, NMC and the other logs. Files over the per-file limit are reduced.` |
| CLI (`messages.py`) | `ingest_reduced` | `  [reduced] {name}: indexed the first {head} and the last {tail} of {size} (--max-source-mb)` |

Formatação de tamanho e hora pelas funções já usadas (horas como `UTC`, U6). Na exportação `.txt` (10.1), uma linha no cabeçalho por fonte reduzida que tenha registro no relatório: `Reduced: {name} (first {head} and last {tail} of {size})`.

API: `GET /api/sources` ganha `reduced: null | {cap, head_end, tail_start, skipped_bytes, skipped_lines, skipped_from_ts, skipped_to_ts}` (lido de `meta['capped.<id>']`); a pré-visualização (`SourcePreview`) ganha `capped: bool` e `index_bytes`.

### 2.5 PSengine: classificação e atalho

- `families.toml`: a regra `PSENGINE` passa a aceitar `PSengine.ALTERNATE_<n>.log` (padrão `(?:^|/)(?P<process>PSengine(?:\.ALTERNATE_\d+)?)\.log(?:\.(?P<rotation>\d+))?$`). O nome do processo mantém o sufixo: cada arquivo é um stream próprio (nada de duas fontes sem rotação no mesmo stream; a derivação e a ordem de cadeia ficam como estão). Pool no visualizador: não (a instância do `[[pool]]` é numérica e o principal não tem número; 2 entradas no filtro bastam).
- Atalho embutido (`presets_builtin.toml`): "PSengine: failures without the peer loop" = família `PSENGINE`, sev ≤ 2, exceto `PSE-001`, `PSE-002` e `PSE-003`. No arquivo medido isso dá ≈ 6,8 mil registros em vez de 30,5 milhões.
- Severidade do laço: fica `sev = 2` (é falha real e a regra da frente proíbe falha em sev 4/5); o atalho resolve o ruído.

## 3. R4: arquivos fora de escopo

### 3.1 O que há (ERS RT Primary, 1.870 fontes)

| Grupo | Fontes | Volume | Hoje |
|---|---|---|---|
| Escopo padrão (selecionadas) | 96 | ≈ 1,64 GB | indexadas |
| Logs opcionais indexáveis (`INSTALL` 396, `REPORT` 73, `PROCESS` 37, `SERVERLOG` 11, outros) | 538 | ≈ 0,70 GB | listadas, `out_of_scope`; entram com "All logs" |
| **Não indexáveis** (`NONLOG`) | **1.236** | ≈ 3,03 GB | listadas, `not_indexable` |

Os 1.774 "não selecionados" da R1 são os dois últimos grupos. Os não indexáveis, por tipo:

| Tipo | Arquivos | Volume | Natureza |
|---|---|---|---|
| `.xml` (configuração do ADMS, Registry e afins dos servidores) | 607 | 24,8 MB | texto |
| `.csc` (scripts dos servidores) | 161 | 1,2 MB | texto |
| `.sql` e `.init` (banco) | 125 | 0,9 MB | texto |
| `.config` (configuração dos executáveis .NET) | 63 | 0,1 MB | texto |
| `.xsd`, `.xsl`, `.html`, `.old`, `.example`, `.xmlold` | 19 | 0,3 MB | texto |
| `.db3` + `-wal` + `-shm` (fila de mensagens, P3) | 216 | 856 MB | binário, já tem leitor próprio opcional |
| `.blg` (contadores de desempenho) | 15 | 955 MB | binário |
| `.etl` (rastreio de eventos) | 9 | 790 MB | binário |
| `.evtx` (log de eventos do Windows) | 5 | 372 MB | binário |
| `.dll`, `.jpg`, `.dat` | 16 | 29 MB | binário |

Todos os arquivos de texto têm até 4 MiB (2 XML entre 1 e 4 MiB, 49 entre 64 KiB e 1 MiB, dos quais 47 XML, e o resto até 64 KiB).

**Privacidade**: palavra de credencial seguida de `=`, `:`, aspas ou `>` (`password`, `pwd`, `secret`, `token`, `connectionString`, `apikey`, `credential`) aparece em **62 de 63** `.config`, 38 de 607 XML, 1 `.csc` e 1 `.txt` (só a presença foi contada; nenhum valor lido). Os XML do Registry já são lidos pela camada de conhecimento e aparecem no painel de configuração (`/api/config`).

### 3.2 Decisão: visualizador de texto somente leitura, com valores de credencial ocultos

Só listar (como hoje) deixa o Jefferson sem ver `.config` (endpoints, tempos limite, bindings), scripts `.csc` e a configuração XML do ADMS sem sair do app e abrir o zip. Um visualizador de texto é barato (uma rota, uma função de leitura, um painel) e não toca no índice. Os binários continuam só listados.

**O mínimo útil:**
- **Quais arquivos**: fonte `skipped` com motivo `not_indexable` e extensão de texto de uma lista fechada: `.xml .xsd .xsl .xslt .xmlold .config .csc .sql .init .ini .html .htm .old .example .csv .json`. Outros (binários) mostram só tipo e tamanho, com o rótulo do tipo (`Performance counter log (.blg)`, `Event trace (.etl)`, `Windows event log (.evtx)`, `SQLite database (.db3)`, `Binary file`).
- **Leitura sob demanda**, do zip ou da pasta registrada na coleta (`collection.path` + `source.member`, nunca caminho vindo do cliente), pelo `SourceReader` (mesmos limites de zip; arquivo aberto, lido e fechado). **Nada vai para o banco**: nem `entry`, nem FTS, nem exportação, nem cache (`Cache-Control: no-store`, como toda a API).
- **Tamanho máximo**: 4 MiB descomprimidos (cobre 100 % dos medidos); acima, recusa com mensagem. Binário disfarçado (byte NUL nos primeiros 8 KiB, depois do BOM): recusa.
- **Codificação**: BOM UTF-8/UTF-16 respeitado; sem BOM, UTF-8 com substituição de bytes inválidos; fim de linha normalizado para `\n`.
- **Credenciais ocultas por padrão** (regras exatas, por regex no texto; nenhum parser XML no conteúdo):
  - atributo XML cujo nome casa com o padrão de credencial: o valor vira `••••`;
  - elemento XML de texto simples cujo nome casa: o texto vira `••••`;
  - `<add key="K" value="V"/>` (e `name=`/`value=` em qualquer ordem) com `K` casando: `V` vira `••••`;
  - dentro de cadeia de conexão (qualquer valor de atributo ou texto): os pares `Password=...;`, `Pwd=...;` e `Secret=...;` têm o valor oculto, o resto fica;
  - linhas `nome = valor` ou `nome: valor` (`.ini`, `.init`, `.csc`, `.sql`) com `nome` casando: valor oculto.
  - Padrão de nome (sem diferenciar maiúsculas): `pass(word)?|pwd|secret|token|api[_-]?key|credential|private[_-]?key|shared[_-]?key|client[_-]?secret|connection[_-]?string` (para `connectionString`, só os pares de senha dentro dele).
  - A resposta diz quantos valores foram ocultados. Hosts, contas e IPs **não** são ocultados (são o mesmo tipo de dado que já aparece nos logs).
- **Mostrar o original**: botão "Show hidden values" com o aviso "Hidden values may be passwords. They are shown only here and never stored or exported."; vale só para aquela abertura do arquivo (não persiste). O app é local e quem o usa já tem o zip; ocultar por padrão protege captura de tela e cópia.
- **Interface**: na árvore e na lista de fontes, os não indexados ficam num grupo recolhido "Not indexed ({n})", subdividido por motivo ("Other logs (not selected)", "Text files", "Binary files", "Over the limit"), cada subgrupo por extensão com contagem. Arquivo de texto ganha a ação "View"; abre um painel com o nome, tamanho, contagem de ocultos e o conteúdo num `<pre>` montado só com `textContent`; buscar com Ctrl+F do navegador; "Copy" copia o texto como exibido (oculto, salvo se o usuário revelou).

API (rota nova, `api.py` com a sessão principal):
`GET /api/sources/{id}/text?reveal=0|1` → `200 {id, name, member, kind, size, encoding, lines, hidden, text}`; erros `404 source_not_found`, `409 not_viewable` (log indexável ou extensão fora da lista), `413 file_too_large`, `422 binary_content`, `410 container_missing` (o zip ou a pasta não existe mais), com textos em `messages.py`. Strings novas no `strings.js` com prefixo `fv` (`fvView`, `fvTitle`, `fvHiddenCount` = `{n} values hidden`, `fvReveal` = `Show hidden values`, `fvRevealWarning`, `fvTooLarge` = `This file is larger than {limit} and is not shown.`, `fvBinary` = `This is not a text file.`, `fvMissing` = `The collection file is no longer available.`, `fvCopy`, `ntGroup` = `Not indexed ({n})`, e os rótulos dos subgrupos e tipos acima).

Fora do mínimo (pós-0.1.0, só se o Jefferson pedir): leitor de `.evtx` (formato binário com BinXML, custo alto), conversão de `.blg` (no Windows, `relog` faz isso fora do app), realce de sintaxe XML.

## 4. O que muda (resumo para a ARQUITETURA)

| Seção | Mudança |
|---|---|
| 5 (esquema) | nenhuma migração; bit `flags & 128` (`FLAG_AFTER_SKIP`); chaves de `meta`: `capped.<fonte>`, `source_cap.<grupo>` |
| 9.1a / `families.toml` | regra `PSENGINE` com `ALTERNATE_<n>`; tabela "Não indexáveis: visualizador de texto" |
| 9.2 (leitura) | teto por fonte, `head_bytes`, `max_skipped_member_bytes`; `too_large` só acima deste |
| 9.3c (nova) | fonte reduzida: algoritmo, estado, retomada, crescimento, garantias (seção 2 desta nota) |
| 9.3b | fonte reduzida nunca é aberta por janela |
| 10 (API) | `reduced` em `/api/sources`; `capped`/`index_bytes` na pré-visualização; `source_cap_mb` na abertura; `GET /api/sources/{id}/text` |
| 10.1 (exportação) | linha `Reduced:` no cabeçalho |
| 12 (segurança) | visualizador de texto: lista fechada, 4 MiB, nada gravado, credenciais ocultas, revelação por abertura |

## 5. Tarefas

Pontos de integração com a sessão principal (agentes não editam): `strings.js`, `api.js`, `api.py`, `messages.py`, `index.html`, `main.js`, `app-actions.js`, `test_web_assets.py`, `css/bundle.order.txt`, ARQUITETURA, ROADMAP. Cada agente devolve, no relatório, as chaves e textos que a sessão deve acrescentar nesses arquivos.

### Lote 1 (paralelo, 3 agentes Sonnet)

| # | Tarefa | Arquivos (exclusivos) | Aceite |
|---|---|---|---|
| R3a | Classificação do `PSengine.ALTERNATE_<n>.log` e atalho "PSengine: failures without the peer loop" | `src/dmslog/families.toml` (só a regra `PSENGINE`), `src/dmslog/store/presets_builtin.toml` (só o atalho novo), `tests/unit/test_psengine_rule.py` (novo) | caminhos sintéticos `.../PSengine.log`, `.../PSengine.ALTERNATE_1.log`, `.../PSengine.ALTERNATE_2.log.1` viram `PSENGINE` com o processo certo; nenhuma outra fonte das amostras muda de família (script de comparação antes/depois, só contagens no relatório); atalho válido no carregador de presets; ruff e suíte verdes |
| R3b | Fonte reduzida no backend (seções 2.1 a 2.3) | `src/dmslog/ingest/capped.py` (novo), `ingest/discover.py`, `ingest/reader.py`, `ingest/pipeline.py`, `store/writer.py` (`FLAG_AFTER_SKIP`, limpeza em `remove_group`), `derive/lifecycle.py` (só a regra da lacuna), `cli.py` (`--max-source-mb`), `tests/unit/test_capped_source.py` (novo), `tests/property/test_capped_properties.py` (novo) | as 7 garantias da seção 2.3 com teste; suíte completa verde em Linux e Windows; nenhuma mudança de resultado em fonte abaixo do teto. É indexação incremental: se o teste de propriedade achar um caso que o desenho não cobre, parar e trazer ao Opus (não improvisar a regra) |
| R4b | Leitura de texto de fonte não indexável, com ocultação | `src/dmslog/store/filetext.py` (novo), `tests/unit/test_filetext.py` (novo, fixtures sintéticos de `.config`, XML com atributo, elemento, `<add key value>`, cadeia de conexão, `.ini`, UTF-16, binário, 4 MiB + 1) | função `read_source_text(conn, source_id, *, reveal, limits) -> FileText` com os erros da seção 3.2 como exceções tipadas; toda regra de ocultação com caso positivo e negativo; nada escrito no banco (comparar o arquivo do caso antes e depois); mediana < 300 ms para os arquivos de texto do ERS RT Primary (medido por script, só números no relatório) |

Disjunção: R3b é o único que toca `ingest/` e `derive/`; R3a só dados; R4b usa `SourceReader` sem alterá-lo (se precisar de algo novo nele, pede à sessão).

Integração do lote 1 pela sessão: rota `GET /api/sources/{id}/text` e campo `reduced` em `list_sources` (`api.py`), opção `source_cap_mb` na abertura e no `plan`, textos de `messages.py`, um PR por vez (R3a, depois R3b, depois R4b), cada um com revisão cruzada (R3b é T2 por mexer em indexação incremental).

### Lote 2 (depois do merge do lote 1; 2 agentes Sonnet em paralelo)

| # | Tarefa | Arquivos (exclusivos) | Aceite |
|---|---|---|---|
| R3c+R4c | Interface: selo "Reduced" e `title`, grupo "Not indexed" com subgrupos, ação "View" e painel do visualizador, seletor "Large files" e nota da pré-visualização, nota no detalhe do registro com `flags & 128` | `web/js/sources.js`, `web/js/source-tree.js`, `web/js/source-tree-model.js`, `web/js/case-files.js`, `web/js/case-files-model.js`, `web/js/detail.js`, `web/js/file-viewer.js` (novo), `web/js/file-viewer-model.js` (novo), `web/css/sources.css`, `web/css/file-viewer.css` (novo), testes node dos modelos (novos) | conteúdo só por `textContent` (guarda de `test_web_assets.py`, mantida pela sessão); Esc fecha o painel; foco volta à fonte; revelar exige o clique e some ao fechar; strings só via `strings.js` (chaves da seção 2.4 e 3.2 entregues à sessão) |
| R3d | Linha `Reduced:` no cabeçalho da exportação `.txt` | `src/dmslog/report/export.py`, `src/dmslog/report/viewexport.py`, `tests/unit/test_export_reduced.py` (novo) | linha presente só quando o relatório tem registro de fonte reduzida; CRLF e ASCII mantidos |

### Lote 3 (medição, Haiku ou Sonnet; depois do lote 2)

| # | Tarefa | Aceite |
|---|---|---|
| R3e | Medir com caso novo em disco (`/home`), ERS RT Secondary, "All logs", antes (`main` anterior) e depois | as duas fontes PSengine `ready` e reduzidas, 0 `failed`; acréscimo das duas ≤ 60 s e ≤ 1,0 GB de banco; RSS < 1 GB; escopo padrão dos dois ERS RT com as mesmas contagens de fontes e registros e tempo dentro de ±5 %; números agregados acrescentados a esta nota |

## 6. Riscos

| Risco | Mitigação |
|---|---|
| O começo do problema (o início do laço) fica no meio pulado | o intervalo pulado aparece com horário no selo e no detalhe; "Whole file" na abertura; 1 MiB do começo guarda a partida |
| Erro de `line_no` depois do meio (bookmarks e "lines a-b" da exportação dependem dele) | garantia 3 com teste direto contra a contagem do fixture |
| Retomada ou crescimento divergindo da indexação de uma vez | garantias 1 e 2 com teste de propriedade e corte em todos os pontos |
| Teto atingir log importante de outra família no futuro | o teto é por fonte e configurável; o aviso é visível; nenhuma amostra atual chega a 256 MiB fora do PSengine |
| Ocultação deixar passar credencial com nome fora do padrão | lista de regras testada; oculto por padrão só reduz exposição em tela; nada é gravado nem exportado; o Jefferson pode pedir nome novo |
| XML malicioso no visualizador | sem parser (só regex sobre texto), sem `innerHTML`, sem executar nada; CSP do app já bloqueia script |
| Derivação lenta em família sem regra (7,6 s por milhão de registros) | fora desta tarefa; anotado para a R6 |

## 7. Decisões (padrões adotados em 2026-10-24)

O Jefferson pediu para não parar a frente RT enquanto está fora: a sessão principal adotou os padrões abaixo, inclusive nas duas decisões 8 e 9. Todas são revisáveis.

Resolvidas com um padrão (o Jefferson pode mudar qualquer uma):

1. **Teto por fonte em bytes, padrão 256 MiB + 1 MiB do começo**, genérico para toda família. *(padrão adotado)*
2. **Sem amostragem do laço na ingestão**; o laço se esconde na leitura (atalho e filtro `repeat`). *(padrão adotado)*
3. **Teto aplicado só na primeira indexação**; crescimento ao vivo não é reduzido de novo; truncamento ou rotação reaplicam o teto gravado. *(padrão adotado)*
4. **Sem "indexar o resto" no lugar**; para o arquivo inteiro, remover a coleta e abrir com "Whole file". *(padrão adotado)*
5. **`PSengine.ALTERNATE_<n>` como stream próprio da família `PSENGINE`**, sem pool. *(padrão adotado)*
6. **Laço com `sev = 2`**, ruído resolvido pelo atalho. *(padrão adotado)*
7. **R4: visualizador de texto somente leitura**, lista fechada de extensões, 4 MiB, binários só listados. *(padrão adotado)*

Pedem confirmação do Jefferson (seguem com o padrão até ele dizer o contrário):

8. **Revelar valores ocultos** no visualizador (botão por abertura) ou nunca revelar. Padrão: permitir, com aviso.
9. **Opções do seletor "Large files"**: 256 MB (padrão), 1 GB e arquivo inteiro. Se ele preferir janela por horas ("últimas 24 h"), o algoritmo muda só no cálculo de `T` (busca do primeiro cabeçalho com horário ≥ fim − N h, possível porque o horário é monotônico nos dois arquivos medidos).

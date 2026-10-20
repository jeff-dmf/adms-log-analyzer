# C2 e C4: passagem do job entre processos e despejo `@Nome = valor` do AlarmsEvents (desenho)

Desenho (Opus) de duas mudanças de código previstas em `docs/notas/rt-templates-desenho.md`, seção 5.3, para implementação por agentes Sonnet. Números medidos no caso local dos ERS RT de exemplo (dois collection groups, um por servidor), lido só para leitura. Nenhum dado real aqui; exemplos sintéticos.

## 1. C2: `[[link_rule]] kind = "handoff"`

### 1.1 Medição (base do desenho)

Cadeia por `job_id`: Scheduler `DJB-003` (envio ao Launcher) → Launcher `DJB-051` (job recebido) → Launcher `DJB-050` (Execute enviado ao FH, com `fh_pid`) → FunctionHost `DFH-001` (job recebido para execução).

| Medida (por grupo) | DJB-003 → DJB-051 | DJB-050 → DFH-001 |
|---|---|---|
| `a` / `b` | 17 mil / 44 mil; 32 mil / 42 mil | 44 mil / 74 mil; 42 mil / 91 mil |
| `job_id` repetido entre os `a` ou entre os `b` do grupo | 0 | 0 |
| `b` sem `a` **dentro da cobertura** do lado `a` | 0 | 0 |
| `b` sem `a` fora da cobertura (o arquivo do lado `a` cobre menos horas) | 61% e 23% dos `b` | 41% e 55% dos `b` |
| `a` sem `b` dentro da cobertura do lado `b` | 0 | 0,13% e 0,36% |
| `b` com 2 ou mais `a` do mesmo valor em 10 min | 0 | 0 |
| Δt `a`→`b` (p50 / p99,9 / máx.) | 0 / 16 / 78 ms | 0 / 47 / 140 ms |
| `b` com Δt < 0 | 0 | 0 |
| `fh_pid` do `a` = PID do envelope do `b` | n/a | 100% (nenhuma divergência) |

Conclusões: a chave é única por grupo; todo desencontro vem de cobertura diferente entre os arquivos (rotação), então o contador precisa separar "fora da cobertura" de "órfão", senão a regra seria reprovada pelo critério de 1% sem erro algum. Nenhum `job_id` aparece nos dois grupos (servidores) do caso.

### 1.2 Semântica

```toml
# dmsjob.toml
[[link_rule]]          # Scheduler -> Launcher (mesma família, outro processo)
kind = "handoff"
a = "DJB-003"
b = "DJB-051"
key = "job_id"
scope = "collection"
max_dt_ms = 60000

[[link_rule]]          # Launcher -> FunctionHost (outra família)
kind = "handoff"
a = "DJB-050"
b = "DFH-001"
key = "job_id"
scope = "collection"
families = ["DMSFH"]
pid_field = "fh_pid"
max_dt_ms = 60000
```

- **`scope = "collection"`**: uma única partição por collection group com todos os streams (normal e sensitive, qualquer processo e PID) cujas famílias estão em `{família da regra} ∪ families`. Nunca cruza collection groups (U21 continua valendo).
- **`families`** (lista, padrão vazio = só a própria família): famílias cujos registros podem ser o `b`. Só vale com `scope = "collection"`. Cada nome precisa existir no catálogo e não pode ser biblioteca (`_*`). Os códigos de `a` precisam ser visíveis na família da regra; os de `b`, na família da regra ou numa das `families` (cadeia própria + bibliotecas).
- **`kind = "handoff"`**: exige `key` e `scope = "collection"`; não aceita `window_only` nem `origin`; `a` e `b` disjuntos. `scope = "collection"` só é aceito com `kind = "handoff"` nesta entrega.
- **`pid_field`** (opcional): nome de campo presente em todos os templates de `a`; comparado com `entry.pid` (PID do envelope) do `b`.
- **Regra de ligação: única nos dois sentidos, sem desempate por ordem.**
  - Candidatos do `b` (ts `t`, valor `v`): os `a` com valor `v` e ts em `[t − max_dt_ms, t]`. Com `pid_field`, sai o `a` cujo campo e o PID do `b` são ambos conhecidos e diferentes.
  - 0 candidatos: `pid_mismatch` se havia candidato antes do filtro de PID; senão `unmatched` se `t` está dentro da cobertura do lado `a` (1.3), senão `out_of_coverage`.
  - 2 ou mais candidatos: `ambiguous`, sem ligação.
  - 1 candidato: o `b` fica pendente nesse `a`.
  - Quando a varredura passa de `a.ts + max_dt_ms`, o `a` é decidido: 1 `b` pendente gera a ligação; 2 ou mais geram `ambiguous` para cada um, sem ligação; nenhum conta `unanswered` (só se o `a` está dentro da cobertura do lado `b`).
- **Confiança**: 3 quando não há `pid_field` ou os dois PIDs são conhecidos e iguais; 2 quando há `pid_field` e um dos lados não tem o valor. PID divergente **não liga** (o desenho anterior dava 2; a medição não tem nenhum caso e uma divergência indica outro FH ou PID reciclado; ver pergunta P3).
- A ligação vai de `a` para `b` (`link(a_entry_id, b_entry_id, 'handoff', conf)`), então a cadeia aparece nos dois registros pela consulta bidirecional já existente (`store/keys.links_of`).

Por que não reaproveitar `response` ("último `a` anterior"): entre processos, o desempate por ordem depende de `entry.id`, e os ids dependem da ordem em que as fontes foram indexadas. A regra "única nos dois sentidos" é função só do conjunto `(ts, valor, pid)`; empates de ts entre dois `a` ou dois `b` do mesmo valor viram `ambiguous` qualquer que seja a ordem. É isso que garante retomada = execução completa (1.4).

### 1.3 Cobertura

- Lado `a`: streams da partição com pelo menos um registro de código `a`; cobertura = `[min(source.first_ts), max(source.last_ts)]` das fontes desses streams. Lado `b`: o mesmo com os códigos `b`.
- Calculada por consulta antes da varredura (`SELECT DISTINCT` de `source_id` pelos registros dos templates via `ix_entry_tpl_ts`, depois `min`/`max` em `source`). Lacunas internas (arquivo de rotação ausente no meio) não são modeladas.
- Só afeta contadores, nunca ligações.

### 1.4 Execução, incremental e retomada

- Sem estado incremental: o motor de `links.py` continua recalculando o collection group inteiro quando a assinatura muda. Não há watermark nem checkpoint a retomar; a armadilha do C3 (estado parcial entre execuções) não existe aqui, **desde que** o resultado não dependa de ordem de ids nem de limite de memória atingido em ponto diferente.
- A assinatura atual já cobre o necessário: fontes de **todos** os streams do grupo (inclui os FunctionHost), regras (o dict da regra, com `families` e `pid_field`), `tpl_id` e `def_version` dos códigos usados (inclui os `b` de outra família, porque `ids` é global) e a época de registros do grupo. Mudanças: `KINDS` ganha `"handoff"` (o DELETE do recálculo passa a apagar esse tipo) e `_SIGNATURE_VERSION` vai para `"4"` (cada grupo recalcula uma vez).
- O recálculo só roda se a família da regra (DMSJOB) tem stream no grupo; um grupo só com FunctionHost não tem regra e não escreve nada.
- Limite de memória determinístico: estado = `a` ainda não decididos (valores dos últimos `max_dt_ms`). O teto `MAX_OPEN_KEYS` é conferido **só quando o ts avança** (depois de todos os eventos de um mesmo ts). Estourou: a partição para ali, os `b` restantes e os pendentes contam `dropped`, nada é adivinhado. Assim o ponto de parada não depende da ordem dentro de um ts.
- Leitura: dois cursores por código via `ix_entry_tpl_ts` (como hoje), juntando `entry_key` (origin 0) pela chave; o `b` traz `e.pid`; o `a`, `json_extract(e.fields, '$.<pid_field>')` quando há `pid_field`. Merge por `(ts, rank, id)` com `a` antes de `b` no mesmo ts.

### 1.5 Custo

- Por grupo RT do caso: ~60 mil `a` e ~135 mil `b` nas duas regras, lidos pelo índice; estado máximo = jobs de 60 s (centenas). Estimativa: ~1 s por grupo, memória desprezível.
- Ressalva conhecida (não nova): no acompanhamento ao vivo, qualquer crescimento de fonte do grupo recalcula todas as regras do grupo. Num ERS de 7 dias (~10× o caso) a regra lê ~2 M linhas por ciclo. Se o aceite (1.8) estourar, a correção é assinatura por regra (só as fontes dos streams da partição da regra), fora deste escopo.

### 1.6 Contadores (`link_stats`)

`linked`, `ambiguous`, `unmatched`, `out_of_coverage`, `pid_mismatch`, `unanswered`, `dropped` (novos: `out_of_coverage`, `pid_mismatch`, `unanswered`; entram em `_COUNTERS` e em `LinkSummary`). Rótulo: `DMSJOB/handoff:DJB-050>DFH-001`.

Taxa de rejeição da regra = `(unmatched + ambiguous + pid_mismatch + dropped) / (b dentro da cobertura)`. Acima de 1% a regra sai do TOML (critério 8.2.8 do desenho RT). `unanswered / (a dentro da cobertura do b)` é informativo (esperado ~0,1–0,4%).

### 1.7 UI e documentos (sessão principal)

- `strings.js`: `lnkKindNames.handoff` (proposta: `"handoff"`; P1). Sem o rótulo a UI mostra o nome cru do tipo, então nada quebra antes.
- `links-model.js`: `KIND_ORDER` com `"handoff"` depois de `"response"` (sem isso o tipo vai para o fim da lista; funciona).
- `ARQUITETURA`: seção 5, comentário de `link.kind` com `handoff` (texto aberto, **sem migração**; `schema.sql` fica intocado); seção 8, linha nova na tabela ("passagem do job entre processos", confiança 3/2) e o parágrafo "Como construído" de `derive/links.py` (escopo `collection`, `families`, `pid_field`, regra única nos dois sentidos, cobertura, contadores novos, versão 4 da assinatura); seção 15, linha.
- `docs/notas/rt-templates-desenho.md`: linha C2 da tabela 5.3 aponta para esta nota.

### 1.8 Testes e aceite

Testes exigidos (dados sintéticos, `load_texts` com dois arquivos de família):

1. Unidade (`tests/unit/test_links_handoff.py`): Scheduler → Launcher na mesma família em processos distintos; Launcher → FH com `families`; `families` ausente não enxerga a outra família; confiança 3 e 2; PID divergente conta `pid_mismatch`; dois `a` na janela do `b`; dois `b` na janela do `a`; empate de ts `a`/`b` (liga), `a`/`a` e `b`/`b` (ambíguo); Δt negativo e acima de `max_dt_ms`; `out_of_coverage` vs `unmatched`; `unanswered`; grupos independentes (mesmo `job_id` em dois grupos não liga); recálculo apaga só `handoff` e os tipos do motor; `normal_sensitive` e payload não apagam `handoff`; regra inválida reportada, não lançada; limite de memória conta `dropped` e nunca liga.
2. Carga do catálogo (`test_links_handoff.py` ou `test_templates.py`): `families` com família inexistente, biblioteca ou sem `scope = "collection"`; código de `b` invisível; `pid_field` ausente num template de `a`; `scope = "collection"` com outro `kind` → erro tipado na carga.
3. Propriedade (`tests/property/test_links_handoff_properties.py`, hypothesis, oráculo por força bruta sobre todos os pares): (a) o motor bate com o oráculo; (b) **retomada em todos os pontos de corte**: para cada prefixo de cada arquivo (Scheduler, Launcher, 2 FH), indexar o prefixo, rodar `link_pending`, completar e rodar de novo dá o mesmo conjunto de ligações e os mesmos contadores da execução completa, comparando por `(member, line_no)` e não por `entry.id`; inclui o caso em que o `b` (FH) é indexado antes do `a` (Launcher); (c) **permutação da ordem das fontes**: indexar os arquivos em outra ordem (ids diferentes) dá o mesmo conjunto por `(member, line_no)`; (d) apagar e reindexar uma fonte (época do grupo) volta ao resultado completo.
4. Integração pelo pipeline: pasta sintética com os três processos, crescimento do arquivo em passos (caminho do acompanhamento ao vivo e da retomada) igual à ingestão de uma vez.

Aceite medido (agente, no caso local, aplicando o catálogo; relatório só com números):

- Rejeição ≤ 1% por regra e por grupo (esperado 0%); `linked` = número de `b` dentro da cobertura (esperado 100%); confiança 3 em 100% da regra do FH.
- Tempo do passo `link rules` por grupo RT ≤ 2 s acima do atual; memória de pico do passo + ≤ 50 MB.
- Suíte completa e `ruff check` na raiz verdes; nenhuma mudança de resultado nas regras existentes (`test_links.py` e propriedades intactos).

## 2. C4: parser de continuação `kv_at`

### 2.1 Medição

- Stream sensitive do AlarmsEvents: 587 mil registros, todos `DOM-S140`; continuação de 1 linha em 99,97% (as demais têm quebras dentro de um valor de texto livre); tamanho p50 ~880, p99 ~1.040, máximo ~1.480 caracteres.
- Sempre 33 itens, sempre na mesma ordem, nenhum nome repetido no registro; separador entre itens: espaço(s).
- Forma dos valores por nome (amostra de 1 em 20, só a classe do valor foi lida):

| Classe | Nomes |
|---|---|
| Inteiro pequeno, enumeração (≤ 22 valores distintos) | `severity`, `alarmCategory`, `alarmClass`, `alarmSourceTypeId`, `alarmInhibitTypeId`, `operActivityTypeId` |
| Palavra, enumeração (≤ 3 valores) | `type` |
| Booleano | `isSTGZone` (`testModeActive` sempre vazio) |
| GID decimal de 18–19 dígitos | `elementGid` (vazio ou `0` em ~2%) |
| Data e hora | `time`, `fieldtime` |
| Nome de processo ADMS (≤ 7 valores) | `process` |
| Identificação de pessoa, console ou máquina | `userName`, `consoleName`, `host` |
| Texto livre ou nome da rede elétrica do cliente | `message`, `description`, `note`, `regionName`, `subregionName`, `substationName`, `feederName`, `locationName`, `deviceName`, `elementName`, `customTypeName`, `rtuName`, `aorGroupName`, `voltageLevel`, `reasonSource`, `reasonId`, `eventCustomId` |

### 2.2 O que o parser extrai

- `n_items` (int): número de itens `@Nome =` na janela.
- Só os nomes da lista branca, cada um com tipo obrigatório e nome de campo de saída definidos no catálogo. **Nenhum valor fora da lista é lido para fora do texto**, e a lista não admite tipo texto livre.
- `kv_rejected` (int, só quando > 0): itens da lista branca descartados (valor fora do tipo ou nome repetido).
- `kv_cut` (`true`, só quando a continuação passa da janela).

Tipos aceitos (fechados):

| Tipo | Aceita | Grava |
|---|---|---|
| `int` | `-?\d{1,18}` | inteiro |
| `bool` | `true`/`false`, sem diferenciar maiúsculas | booleano JSON |
| `token` | `[A-Za-z][A-Za-z0-9_]{0,31}` | texto (até 32 caracteres, sem espaço nem pontuação) |
| `gid` | `\d{1,20}`, diferente de `0` | inteiro até 2^63−1, acima disso texto decimal (mesma regra do C0) |

Valor vazio: campo ausente, sem contar rejeição.

### 2.3 Gramática e limites

- Janela: os primeiros `window` caracteres da continuação (padrão e máximo 4096, mínimo 256). Medido: nenhum registro passa de 1.500, então nada é cortado no caso.
- Início de item: `@` no começo da janela ou depois de espaço em branco, nome `[A-Za-z][A-Za-z0-9_]{0,63}`, um espaço opcional, `=`, um espaço opcional. Regex `(?:^|(?<=\s))@([A-Za-z][A-Za-z0-9_]{0,63}) ?= ?`, linear (sem quantificadores aninhados).
- Valor: do fim do início do item até o início do próximo item (ou o fim da janela), sem espaços nas pontas. Quebra de linha dentro do valor é aceita (o tipo a rejeita, se for o caso).
- Com corte (continuação maior que a janela), o último item da janela é descartado, porque o valor pode estar truncado (mesma regra de `_cont_lines`).
- Nome da lista branca que aparece duas vezes: **os dois** são descartados e contam em `kv_rejected`. Um `@severity = 1` escrito dentro de `message` (texto livre do alarme, controlado por quem configura o ADMS) apaga o campo, nunca o substitui.

### 2.4 Configuração da lista branca

No próprio template, dentro de `cont` (o TOML do adapter é o arquivo de conhecimento curado do repositório):

```toml
[[template]]
code = "DOM-S140"
# ... (como hoje: text, stream = "sensitive", class, sev = 4, fts = false)
cont = { parser = "kv_at", allow = { severity = "int:ev_severity", type = "token:ev_type", alarmCategory = "int:alarm_category", alarmClass = "int:alarm_class", alarmSourceTypeId = "int:alarm_source_type", alarmInhibitTypeId = "int:alarm_inhibit_type", operActivityTypeId = "int:oper_activity_type", isSTGZone = "bool:stg_zone" } }
examples = [
    { msg = "(AES) [EventReporting] [RecordQueues] of eventRecord. EnqueueEvent: Event log =>", cont = "@time = 2026-01-01 00:00:00  @type = Alarm  @message = Example text @severity = 3  @alarmCategory = 12  @isSTGZone = False", fields = { n_items = 6, ev_severity = 3, ev_type = "Alarm", alarm_category = 12, stg_zone = false } },
]
```

- `allow`: tabela `nome no log = "tipo:campo"`, de 1 a 32 entradas; nome no log `[A-Za-z][A-Za-z0-9_]{0,63}`; campo `[a-z][a-z0-9_]{0,31}`, único, diferente de `n_items`, `kv_rejected` e `kv_cut` e dos grupos da regex do template.
- `outputs` do parser = `{n_items, kv_rejected, kv_cut}` ∪ campos de `allow` (calculado na carga, como o `line_regex`), então `fields.<campo>` funciona no grid, na agregação e nos filtros sem outra mudança. Declarar esses campos em `fields` do template é erro de carga (o tipo vem do `allow`).
- Por que no template e não em `knowledge/curated/`: o `def_version` do template muda junto com a lista (o caso sabe que precisa reindexar), `check_examples` valida a lista com exemplos, e o parser não passa a depender da camada de conhecimento. (P9)

### 2.5 Segurança

- Conteúdo de log é dado não confiável. Nomes de campo vêm só do catálogo, nunca do log (diferente de `kv_colon`, que guarda as chaves lidas num objeto). Valores gravados são números, booleanos ou palavras de até 32 caracteres `[A-Za-z0-9_]`.
- Nada do despejo vai ao FTS (`fts = false` fica) nem a `entry_key` (salvo se P5 for aprovada, e então só o GID numérico). O texto bruto continua em `entry_body` como hoje (é o registro); `kv_at` não acrescenta cópia de texto.
- Na UI, campos e texto só por `textContent` (regra vigente; nada muda no front).
- Exemplos e fixtures: só os nomes de esquema e valores sintéticos; nenhum valor com 9+ dígitos (guarda de `test_rt_catalog.py`), então o exemplo de `elementGid`, se entrar, usa até 8 dígitos.

### 2.6 Custo

- Medido em protótipo sobre uma linha sintética de 33 itens e 1.070 caracteres: ~21 µs por registro (um `finditer` e consulta a dicionário por item). 587 mil registros: ~12 s de CPU, ~2–3% do tempo de ingestão dos 3 ERS.
- `entry.fields` hoje é nulo nesse template. Com a lista proposta: ~150 bytes de JSON por registro, ~85–90 MB no caso (~1% do banco). Com `elementGid` como chave: +~575 mil linhas em `entry_key`.

### 2.7 Testes e aceite

Testes (`tests/unit/test_cont_kv_at.py`, sintéticos): extração e tipos; vazio; valor fora do tipo (`kv_rejected`); nome repetido descarta os dois; item injetado dentro de um valor de texto; janela com corte (último item descartado, `kv_cut`); `@` sem espaço antes não abre item; nome fora da lista nunca aparece em `fields` (property test com hypothesis: nomes e valores aleatórios, o conjunto de chaves de `fields` ⊆ outputs e todo valor casa o seu tipo); continuação de 256 KiB com milhares de `@` (tempo limitado e linear); erros de carga (`allow` vazio, tipo desconhecido, campo duplicado ou reservado, `window` fora da faixa, `fields` redeclarando campo do `allow`); `check_examples` do `DOM-S140`.

Aceite medido (no caso local, aplicando o catálogo às 587 mil continuações; relatório só com números):

- `n_items = 33` em 100%; cada campo da lista presente na fração de registros com valor não vazio medida em 2.1 (100% para os inteiros, `ev_type` e `stg_zone`); `kv_rejected` = 0 em ≥ 99,9%.
- Custo do parser ≤ 30 µs por registro (p50) e vazão da reingestão ≥ 90% da medida antes (critério do lote RT); banco no máximo +1,5%.
- `denylist.py check` → `CLEAN`; suíte completa e `ruff check` verdes.

## 3. Arquivos a tocar

| Arquivo | C2 | C4 | Dono |
|---|---|---|---|
| `src/dmslog/derive/links.py` | motor `handoff`, partição `collection`, cobertura, contadores, versão 4 | — | agente C2 |
| `src/dmslog/parse/templates.py` | só `Catalog._resolve_rules` (+ auxiliar logo abaixo): `families`, visibilidade dos `b`, `pid_field`, `scope`/`kind` | só o bloco dos parsers de continuação (`_p_kv_at`, `CONT_PARSERS`, `_OPTION_RANGES`) e `_parse_cont` (outputs e validação de `allow`) | fatias reservadas, agentes C2 e C4 em worktrees separados |
| `src/dmslog/adapters/_schema.md` | só o parágrafo `[[link_rule]]` de "Correlation rules" | só a tabela "Continuation parsers" | idem |
| `src/dmslog/adapters/dmsjob.toml` | as duas regras `handoff` (e a de P2, se aprovada) | — | agente C2 |
| `src/dmslog/adapters/dmsoms.toml` | — | `cont` e exemplo do `DOM-S140` | agente C4 |
| `tests/unit/test_links_handoff.py`, `tests/property/test_links_handoff_properties.py`, teste de integração pelo pipeline | novos | — | agente C2 |
| `tests/unit/test_cont_kv_at.py` | — | novo | agente C4 |
| `strings.js`, `links-model.js`, `docs/ARQUITETURA.md`, `docs/notas/rt-templates-desenho.md`, fixtures regeneradas | rótulo, ordem, seções 5/8/15 | seções 6.3/15 | sessão principal |

## 4. Tarefas para agentes Sonnet

Um lote de 2 em paralelo, cada um no seu worktree, sem commit; fatias de `templates.py` e `_schema.md` reservadas como na tabela 3 (a sessão principal junta).

| Tarefa | Arquivos (exclusivos) | Entrega |
|---|---|---|
| **S-C2** | `derive/links.py`; fatia `_resolve_rules` de `parse/templates.py`; parágrafo `[[link_rule]]` de `_schema.md`; `dmsjob.toml` (só as regras novas); os 3 testes novos | código, testes verdes, medição do aceite 1.8 com o script local (adaptar `<scratchpad>/mine/c2measure.py`, fora do repo) |
| **S-C4** | fatia dos parsers de `parse/templates.py`; tabela de parsers de `_schema.md`; `DOM-S140` em `dmsoms.toml`; `tests/unit/test_cont_kv_at.py` | código, testes verdes, medição do aceite 2.7 (adaptar `<scratchpad>/mine/c4measure.py`) |

Depois do lote, a sessão principal: junta as fatias, regenera fixtures (`tools/make_family_fixtures.py --check`), atualiza `strings.js`, `links-model.js` e `ARQUITETURA`, roda `ruff check` na raiz e a suíte completa, `denylist.py check`, abre **um** PR (T2; revisor cruzado padrão, com atenção a 1.4 e 2.3).

Prompt de cada agente: este documento, as respostas do Jefferson às perguntas da seção 6, a fatia reservada e a proibição de editar fora dela.

## 5. Riscos

| Risco | Mitigação |
|---|---|
| Retomada ≠ execução completa (o que derrubou o C3 três vezes) | sem estado entre execuções; regra única nos dois sentidos, independente de `entry.id`; teto de memória conferido só na troca de ts; testes de prefixo em todos os cortes e de permutação de fontes |
| Contador de órfãos inflado pela cobertura diferente dos arquivos | `out_of_coverage` separado de `unmatched`; taxa só sobre `b` dentro da cobertura |
| Job despachado para Launcher de outro servidor | não liga (U21), conta `unmatched` só se dentro da cobertura; medido 0 no caso |
| Reuso de `job_id` (reinício do Scheduler) | janela de 60 s e unicidade: reuso dentro da janela vira `ambiguous`, fora dela é outro job |
| Recalcular o grupo inteiro a cada ciclo ao vivo | custo medido no aceite; assinatura por regra fica como evolução |
| Fatias de `templates.py` editadas em paralelo | regiões distantes, worktrees separados, junção pela sessão principal e suíte completa antes do PR |
| Valor de alarme vazar para campo, chave ou FTS | lista branca com tipos fechados, nomes só do catálogo, item duplicado descartado, `fts = false`, property test |
| Banco maior com `fields` em 587 mil registros | ~1% medido; nomes de campo curtos; teto de +1,5% no aceite |

## 6. Decisões das perguntas (2026-10-20)

O Jefferson pediu para não parar a mineração e não perguntar; a sessão principal adotou os padrões propostos abaixo. Todos são revisáveis sem mudança de código (rótulo em `strings.js`, regra no `dmsjob.toml`, lista branca no `cont` do `DOM-S140`).

1. **P1.** Rótulo do tipo novo na UI: `handoff`.
2. **P2.** Incluir `DJB-051 -> DJB-050` (`response`, `scope = "stream"`, dentro do Launcher): sim.
3. **P3.** `fh_pid` diferente do PID do FunctionHost: não liga (medido, 0 casos).
4. **P4.** Lista branca do AlarmsEvents: a proposta (`severity`, `type`, `alarmCategory`, `alarmClass`, `alarmSourceTypeId`, `alarmInhibitTypeId`, `operActivityTypeId`, `isSTGZone`), só campos de esquema; o conteúdo do alarme em si fica fora. Pendente de revisão do Jefferson.
5. **P5.** `elementGid` como campo e chave `gid`: não nesta etapa (+575 mil linhas de chave).
6. **P6.** `process`: não nesta etapa.
7. **P7.** `time`/`fieldtime`: fora nesta etapa.
8. **P8.** A `severity` do alarme não muda o `sev` do registro.
9. **P9.** Lista branca no próprio template do `dmsoms.toml`.

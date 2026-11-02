# R6: despacho de templates nos ERS RT (medição e decisão)

Data: 2026-11-02. Base: `main` em `5212ca0` (#265). Escopo: frente RT, item R6 do `docs/ROADMAP.md`.

**Decisão recomendada: não otimizar. Fechar a R6 como "medido, não vale".** O teto de ganho de qualquer mudança no despacho (um oráculo que tenta só o template vencedor) é de **1,3% a 1,9% do tempo de indexação**, abaixo do limiar de 5%. A classificação inteira custa 14% a 16% da indexação, e a maior parte disso é casar a regex vencedora e montar o resultado, não o despacho.

## 1. Como o despacho funciona hoje

`TemplateSet` (`src/dmslog/parse/templates.py`, ARQUITETURA 6.3):

- `text` exato: dicionário, antes de qualquer regex.
- Chave = primeiros K caracteres do `msg` sem espaços; `_choose_k` escolhe K entre 2 e 12 minimizando (templates sem prefixo ≥ K) + (maior bucket). Nos conjuntos RT, **K = 4** em todos.
- Bucket da chave (`_keyed`) mais os templates de prefixo curto ou vazio (`_short`, filtrados pelo 1º caractere possível com `can_start`), em ordem de prioridade, memorizados por chave (até 8.192).
- No laço, cada candidato passa por quatro filtros, do mais barato ao mais caro: `startswith(prefix)` completo, `needle in s`, janela (`len(s) <= window`) e só então `regex.match`.

Consequência: o número que a R6 registrou (`candidates()`, tamanho da lista) **não mede custo**. Um candidato rejeitado por `startswith` ou `needle` custa dezenas de ns; o custo real é o número de `regex.match` executados.

## 2. Medição

Método (scripts fora do repositório, sob `systemd-run ... MemoryMax=6G`, removidos ao fim junto da amostra e dos casos):

- Amostra: dos dois ERS RT de exemplo (primário e secundário, normal e sensitive), até 300 mil registros por família e fluxo, metade do começo e metade do meio de cada arquivo, lidos do zip em streaming com `RecordGrouper`. Total: 2,17 M registros.
- Laço de despacho instrumentado (cópia exata do laço de `_classify`) para contar lista, aprovados por prefixo, por `needle` e chamadas de regex; tempo de `classify_text` real; e um **oráculo** que, para cada registro, tenta só o candidato vencedor (teto de qualquer otimização do despacho que não mude o resultado).
- Ingestão real com `TemplateSet.classify` cronometrado, em caso novo em disco (`/home`): (a) ERS RT primário inteiro (escopo padrão, 96 fontes); (b) subconjunto equilibrado de 10 arquivos (2 por família, 279 MB), sem cronômetro, com cronômetro e com cProfile.

### 2.1 Candidatos por linha (amostra)

| Família / fluxo | Registros | Com template | Lista (`candidates`) média / p50 / p95 / máx | Regex executadas média / p95 / máx | `classify` (µs/reg) |
|---|---:|---:|---|---|---:|
| DMSCORE normal | 286.774 | 97,5% | 3,55 / 3 / 12 / 31 | 1,37 / 4 / 12 | 1,49 |
| DMSCORE sensitive | 12.272 | 99,99% | 1,00 / 1 / 1 / 11 | 1,00 / 1 / 7 | 1,21 |
| DMSFH normal | 299.396 | 99,7% | 3,27 / 3 / 9 / 28 | 1,04 / 2 / 8 | 1,58 |
| DMSJOB normal | 291.662 | 99,6% | 8,89 / 10 / 14 / 32 | 1,16 / 2 / 7 | 1,95 |
| DMSJOB sensitive | 151.188 | 99,99% | 1,00 / 1 / 1 / 22 | 1,00 / 1 / 7 | 1,61 |
| DMSMODEL normal | 248.750 | 98,9% | 3,95 / 3 / 10 / 27 | 2,15 / 7 / 13 | 1,52 |
| DMSMODEL sensitive | 126.838 | 99,95% | 3,94 / 4 / 5 / 16 | 1,97 / 3 / 8 | 2,21 |
| DMSOMS normal | 298.102 | 99,2% | 3,69 / 2 / 20 / 27 | 1,81 / 3 / 13 | 1,52 |
| DMSOMS sensitive | 230.824 | 100% | 0,46 / 0 / 3 / 10 | 0,34 / 2 / 8 | 17,91 |
| PSENGINE normal | 225.391 | 100% | 1,90 / 2 / 3 / 8 | 1,00 / 1 / 5 | 1,38 |

- A média de 10 a 17 (ou ~20, na tabela de mudanças de 2026-10-20) é de antes dos lotes seguintes do catálogo. Hoje a média da lista fica entre 0,5 e 8,9; só o DMSJOB normal passa da meta de 4 do desenho, e lá 80% dos candidatos caem no `needle` (prefixo vazio, `needle` presente).
- Regex executadas por registro: 1,0 a 2,2 de média. Os p95 e máximos altos são cauda de poucas assinaturas.
- O DMSOMS sensitive custa 17,9 µs/reg por causa do parser de continuação `kv_at` do `DOM-S140` (0,79 µs/reg sem a continuação), não do despacho (ver seção 5).

### 2.2 Teto de ganho do despacho (oráculo)

| Família / fluxo | Laço real (µs/reg) | Oráculo (µs/reg) | Ganho máximo (µs/reg) | % do `classify` |
|---|---:|---:|---:|---:|
| DMSCORE normal | 0,57 | 0,39 | 0,18 | 12,1% |
| DMSCORE sensitive | 0,48 | 0,47 | 0,00 | 0,4% |
| DMSFH normal | 0,82 | 0,70 | 0,12 | 7,4% |
| DMSJOB normal | 0,96 | 0,48 | 0,49 | 24,9% |
| DMSJOB sensitive | 0,52 | 0,52 | 0,00 | 0,0% |
| DMSMODEL normal | 0,59 | 0,32 | 0,27 | 17,6% |
| DMSMODEL sensitive | 1,40 | 0,81 | 0,59 | 26,6% |
| DMSOMS normal | 0,60 | 0,39 | 0,21 | 13,8% |
| DMSOMS sensitive | 0,17 | 0,10 | 0,08 | 0,4% |
| PSENGINE normal | 0,83 | 0,78 | 0,06 | 4,2% |

Média na amostra: 0,21 µs/reg de ganho máximo. A indexação RT custa ~15 a 16 µs/reg (140 a 290 bytes por registro a 11 a 12 MB/s), então o teto por família vai de 0,4% (PSENGINE) a 3,1% (DMSJOB normal) da indexação.

### 2.3 Fatia na ingestão real

| Caso | Registros | Dados | Total | Derivação + correlação | Indexação (sem derivação) | `classify` | Teto do despacho |
|---|---:|---:|---:|---:|---|---:|---:|
| ERS RT primário inteiro (escopo padrão) | 11,17 M | 1.638 MB | 198,0 s | 49,8 s | 148,2 s (11,1 MB/s) | 21,2 s (14% da indexação) | ~1,9 s = **1,3%** |
| Subconjunto equilibrado (2 arquivos por família) | 1,79 M | 279 MB | 30,7 s | 7,4 s | 23,3 s (12,0 MB/s) | 3,7 s (16%) | ~0,44 s = **1,9%** |

- O tempo de `classify` inclui o próprio cronômetro (~0,1 µs por chamada). O teto é a soma, por família, de registros × ganho máximo do oráculo.
- No caso inteiro, o DMSFH tem 72% dos registros e o menor teto (0,12 µs/reg): é por isso que o ganho total fica em 1,3%.
- cProfile do subconjunto (64,6 s com o perfilador): `executemany`/`execute` do SQLite 10,3 s, `_classify` 6,0 s próprios (11,2 s acumulados), derivação 15,5 s, envelope e agrupamento ~9 s, severidade 5,0 s, JSON dos campos 4,7 s. `re.Pattern.match`: 2,41 M chamadas para 1,79 M registros (1,34 por registro), 1,27 s no total.
- Ganho de vazão se o despacho fosse perfeito: de 11,1 para ~11,25 MB/s no caso inteiro; de 12,0 para ~12,2 MB/s no subconjunto.

### 2.4 Templates que mais geram candidatos extras

Por entradas extras na lista (por registro, sobre os 2,17 M da amostra):

| Template | Extras por registro | Formato | Custo real |
|---|---:|---|---|
| DJB-007, DJB-144, DJB-145 | 0,111 cada | sem prefixo, com `needle` | rejeitados pelo `needle`; regex quase nunca roda |
| DFH-008 | 0,110 | prefixo de 50 caracteres no bucket de 4 | rejeitado pelo `startswith` |
| DJB-009, DJB-146, DJB-147 | 0,107 a 0,108 | sem prefixo, com `needle` | rejeitados pelo `needle` |
| DJB-041, DJB-148, DJB-149 | 0,084 a 0,085 | sem prefixo, com `needle` | rejeitados pelo `needle` |

Por regex executadas sem casar (o que custa de fato):

| Template | Regex falhas por registro | Formato |
|---|---:|---|
| DOM-123 | 0,074 (0,51 no DMSOMS normal) | sem prefixo, sem `needle` |
| DMD-057 | 0,045 (0,77 no DMSMODEL sensitive) | prefixo longo, com `needle` |
| GEN-FLT-02 | 0,039 | sem prefixo, sem `needle` (`_common`) |
| DMD-151 | 0,030 | sem prefixo, sem `needle` |
| DMD-171 | 0,025 | prefixo de 1 caractere, sem `needle` |
| RTL-047 | 0,020 | prefixo de 14, com `needle` (`_dmsrt`) |
| GEN-C2L-02, GEN-STU-06, GEN-STU-07, GEN-STU-09 | 0,018 a 0,020 | sem prefixo, sem `needle` (`_common`) |

A causa apontada na R6 (regex sem prefixo do `_common` e chave de 4 caracteres) existe, mas pesa pouco: as cinco do `_common` (GEN-FLT-02, GEN-C2L-02, GEN-STU-06, GEN-STU-07, GEN-STU-09) somam ~0,11 regex falhas por registro.

## 3. O que se faria e quanto renderia

Todas as opções ficam abaixo do teto do oráculo (1,3% a 1,9% da indexação):

| Opção | Efeito | Ganho estimado |
|---|---|---|
| Prefixo literal ou `needle` em DOM-123, DMD-151, DMD-171, DMD-191 e nas 5 do `_common` | corta a maior parte das regex falhas | ≤ 0,2 µs/reg no DMSOMS e DMSMODEL; ~0,5% a 1% da indexação dessas famílias, ~0,3% no caso |
| Segundo critério no bucket (comprimento ou K maior por bucket) | encurta a lista do DMSJOB e do DFH-008 | os rejeitados por `startswith`/`needle` custam ~40 a 60 ns cada: ≤ 0,3 µs/reg no DMSJOB, ~0,2% no caso |
| Cache por assinatura do registro | evita o laço | calcular a assinatura custa mais que o laço atual (1,0 a 2,2 regex por registro) |
| Reordenar candidatos por frequência | menos regex até a vencedora | muda o resultado quando duas regex casam o mesmo texto; viola a ordem de prioridade (ARQUITETURA 6.3) |

Mudar regex do catálogo exige rodar de novo a análise de segurança (`UnsafeRegexError`, janelas), os testes de exclusividade e o verificador de vazamento: custo de revisão desproporcional para < 1% de vazão.

## 4. Decisão

- **R6: medido, não vale.** Teto de 1,3% (ERS RT inteiro) a 1,9% (subconjunto equilibrado) da indexação, abaixo do limiar de 5%. Vazão de 11,1 a 12,0 MB/s sem derivação, dentro da meta.
- Critério do desenho (`docs/notas/rt-templates-desenho.md`, "≤ 12 candidatos e média ≤ 4"): proposta de trocar a métrica para **regex executadas por registro** (hoje 1,0 a 2,2 de média), porque o tamanho da lista não mede custo. Se aceito, registrar na tabela de mudanças da ARQUITETURA (sessão principal).
- Nenhuma tarefa de implementação.

## 5. Achado fora da R6: parser `kv_at` do DOM-S140

No DMSOMS sensitive, o `classify` custa 17,9 µs/reg, 17,4 µs deles no parser de continuação `kv_at` do `DOM-S140` (79% dos registros desse fluxo). Isso não é despacho e não muda a decisão da R6. Estimativa: num fluxo sensitive de DMSOMS com ~860 bytes por registro a ~12 MB/s, o `kv_at` responde por ~25% do tempo de indexação desse fluxo. Se valer a pena, é item separado (Opus para desenho, porque a lista branca do `DOM-S140` só muda com pedido do Jefferson; a otimização não pode mudar campos nem tipos), com a mesma régua: medir antes, aceitar só com ganho ≥ 5% na vazão do caso.

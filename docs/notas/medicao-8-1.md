# Medição do pior caso (ROADMAP 8.1)

Data: 2026-10-11. Código: `main` em `9d2ceaf` (esquema v4), sem mudança no `src/`. Ferramenta: `tools/bench.py` (PR #82) e scripts de apoio fora do repositório (servidor em processo para a responsividade, ciclo de `Watcher` para o incremental). Só números: nenhum nome de arquivo, host ou valor de log dos ERS reais.

## Máquina e método

- Linux (Omarchy), 20 núcleos, 46 GB de RAM, SSD; Python 3.14.7, SQLite 3.53.4. Ingestão de uma thread, como no produto.
- Dados gerados e bancos em disco real (`~/.cache/dmslog-bench/`, fora do repo; o `/tmp` desta máquina é tmpfs), apagados no fim.
- Pico de RSS: `wait4`/`ru_maxrss` do subprocesso `dmslog ingest` (na responsividade, do processo que roda servidor + ingestão juntos, como o `dmslog serve`).
- Repetições: 3 ou mais por cenário; mediana e faixa. Carga: parte das rodadas pegou outro processo pesado na máquina (≈ 2,5 núcleos ocupados); a mesma ingestão de referência (ERS Verbose real, Enterprise) levou 53,0 s sem ele e 58,9–60,2 s com ele, cerca de **+11 a 14 %**. As linhas marcadas com † foram medidas com essa carga.
- Sintético: `bench.py` com semente 20301, 14 famílias de adapter, BLTHost normal e sensitive, ProcessMonitor e oasErrLog; perfil Verbose com rastreio `Communication2`, payloads XML de 4 mil a 164 mil caracteres no Wfm e dumps de mapa de 200, 600 e 1.600 linhas. Zip deflate (razão ≈ 21×).

## Metas

| # | Meta | Origem |
|---|---|---|
| 1 | ERS completo (≈ 2 GB) sem erro, RSS < 1 GB, UI responsiva durante a indexação | ROADMAP M8 |
| 2 | ERS Verbose dentro da meta do M6: ≤ 60 s, banco ≤ 1,7 GB, RSS < 600 MB | ROADMAP M6 e M8 |
| 3 | Pior caso Verbose sintético (≈ 2,6 GB) sem erro, RSS < 1 GB | ROADMAP M8 |
| R | Responsividade: p95 < 1 s em cada uma das 3 consultas típicas durante a indexação | definida aqui (abaixo) |

**Por que p95 < 1 s.** 1 s é o limite em que a interação ainda parece contínua para quem navega (o usuário percebe a espera, mas não perde o fio); 0,1 s é a sensação de resposta imediata. Durante a indexação aceitamos o primeiro patamar, porque o escritor disputa CPU e disco com as leituras; parado, o grid e o detalhe ficam no segundo. p95 em vez de média porque o que incomoda é o travamento ocasional.

## Resultados por cenário

| Cenário | Tempo total (mediana, faixa) | Derivação | Pico de RSS | Banco | Registros | Logs indexados | MB/s | Repetições | Meta |
|---|---|---|---|---|---|---|---|---|---|
| ERS real, escopo completo (`--all-logs`), o maior dos 3 (2.224 MB descomprimidos) † | 169,9 s (169,8–171,7) | 28,2 s | 471 MB | 2.764 MB | 6.186.302 | 1.230 MB | 7,2 | 3 | 1: PASS |
| ERS real, escopo completo, os outros 2 (2.122 e 2.162 MB descomprimidos) † | 143,1 s e 142,1 s | 21,7 e 21,1 s | 380 e 363 MB | 2.283 e 2.265 MB | 4,80 e 4,91 milhões | 1.015 e 1.014 MB | 7,1 | 1 cada | 1: PASS |
| Sintético Info, 2.000 MB, 14 adapters, zip, `--all-logs` | 316,0 s (299,8–320,8) | 71,0 s | 395 MB | 5.600 MB | 14.371.870 | 2.000 MB | 6,3 | 4 (1 com †) | 1: PASS |
| ERS Verbose real (Enterprise + Sensitive), escopo padrão | 73,3 s (73,2–73,6) | 18,1 s | 394 MB | 1.776 MB | 3.727.017 | 785 MB | 10,7 | 3 | 2: ver abaixo |
| Sintético Verbose, 2.600 MB, 14 adapters, zip | 346,3 s (342,2–359,4) | 83,4 s | 445 MB | 6.949 MB | 16.922.025 | 2.600 MB | 7,5 | 3 | 3: PASS |

Nenhuma rodada teve fonte com erro (`0 failed`) nem código de saída diferente de 0.

Detalhe das fases no sintético Verbose de 2.600 MB (mediana): indexação 245 s, derivação 83,4 s (2.224.982 eventos), correlação 4,6 s, ligação de payloads 13,0 s (92.942 payloads). Vazão por registro estável entre 300 MB e 2.600 MB (46–49 mil registros/s): o custo cresce linearmente com o volume, sem degradação com o banco grande.

### Meta 2 (ERS Verbose real × meta do M6)

| Medida | Enterprise | Sensitive | Par |
|---|---|---|---|
| Tempo total da ingestão (mediana de 3) | 53,0 s | 20,4 s | 73,3 s |
| Só indexação (sem derivação, correlação e ligação de payloads) | 37,7 s | 14,1 s | 51,8 s |
| Derivação + correlação + payloads | 15,1 s | 6,1 s | 21,2 s |
| Pico de RSS | 394 MB | 267 MB | 394 MB |
| Banco ao fim | | | 1.776 MB (1,65 GiB) |

- O número do M6 (36,7 s) corresponde à indexação do Enterprise sem a derivação (hoje 37,7 s). O código do merge do M6 (`c3e9ea5`), medido na mesma máquina, dá o mesmo resultado (55,6 s + 21,8 s de tempo total): **não há regressão desde o M6**.
- Contando só a indexação do par, 51,8 s ≤ 60 s: PASS (critério adotado pelo Jefferson em 2026-10-11). A ingestão inteira do par, 73,3 s (13 s acima de 60 s), fica registrada como informação. RSS (394 MB < 600 MB): PASS. Banco: 1,78 GB decimais contra 1,7 GB, desvio de 4,5 % (1,65 GiB se a meta for lida em GiB): aceito pelo Jefferson em 2026-10-11.

### Projeção de 14 adapters Verbose com densidade real

O sintético tem densidade de registros próxima da real (6,5 mil registros/MB no sintético, 6,1 mil no log normal real e 4,7 mil no par normal + sensitive). Na vazão medida no ERS real (10,7 MB/s na ingestão inteira), 2.600 MB levariam ≈ 245 s; a vazão do sintético dá 346 s. Proporcional à meta do M6 (1.041 MiB em 60 s ≈ 17,4 MiB/s), o alvo seria ≈ 143 s: **nenhuma das duas leituras chega lá**. O ROADMAP não pede tempo para o pior caso sintético (só sem erro e RSS < 1 GB); o tempo medido (346 s) foi aceito pelo Jefferson para a 0.1.0 em 2026-10-11 (seção "Decisões").

## Responsividade (meta R)

Servidor real em processo (como `dmslog serve`): o caso é criado e a coleção adicionada pela API; a ingestão roda no job do servidor enquanto um cliente HTTP repete, a cada 0,5 s, três consultas: (a) primeira página do grid (200 linhas, sem filtro), (b) busca FTS de um termo frequente, primeira página, (c) detalhe de um registro (`GET /api/entries/{id}`). Depois da ingestão, 30 ciclos com o caso parado.

| Caso | Fase | Página (mediana / p95) | FTS (mediana / p95) | Detalhe (mediana / p95) | Erros | Pico de RSS do processo |
|---|---|---|---|---|---|---|
| Sintético Verbose 2.600 MB, sem carga externa (1 rodada) | durante | 45 / 112 ms | 308 / 889 ms | 1,6 / 3,1 ms | 0 em 1.319 | 576 MB |
| idem | parado | 2,4 / 3,1 ms | 347 / 353 ms | 1,6 / 1,8 ms | 0 | |
| Sintético Verbose 2.600 MB † (3 rodadas, faixa dos p95) | durante | 65–67 / 127–130 ms | 390–412 / 1.030–1.052 ms | 1,9 / 2,3–3,0 ms | 0 | 567–570 MB |
| ERS real, escopo completo, o maior (2 rodadas; a 1ª com †) | durante | 4–11 / 168–193 ms | 6–7 / 259–293 ms | 1,8–1,9 / 129–140 ms | 0 | 459–460 MB |
| idem | parado | 2,2–2,7 / 2,7–3,0 ms | 5,5 / 5,7–6,2 ms | 1,5–1,6 / 1,7–2,0 ms | 0 | |

- Meta R: PASS sem carga externa nos dois casos. Com a carga externa, a FTS do sintético passou de 1 s no p95 (1,03–1,05 s).
- A FTS depende da frequência do termo, não do tamanho do caso: a página filtrada materializa todos os `rowid` que casam e ordena por `ts` (plano: `LIST SUBQUERY` + `TEMP B-TREE FOR ORDER BY`). No caso de 16,9 milhões de registros, parado: termo com 730 mil ocorrências 484 ms, 532 mil 321 ms, 12,6 mil 5,6 ms, sem ocorrência 0,1 ms. A busca usada no sintético ("error", 532 mil) é o pior caso realista; IDs, códigos e GUIDs são raros.
- Custo das consultas sobre a ingestão: 375,7 s com o cliente consultando contra 346,3 s sem ele (+8,5 %).

## Cenários extras

| Cenário | Resultado |
|---|---|
| BLTHost sensitive grande | No sintético Verbose de 2.600 MB o BLTHost sensitive tem ≈ 77 MB e o BLTHost do ERS real entra nas medições acima; sem erro e sem pico próprio de memória (o lote é limitado por bytes) |
| Wfm com dumps de até 1.600 linhas e payloads de até 164 mil caracteres | Presentes no sintético Verbose (92.942 payloads); sem erro, RSS de pico 445 MB |
| Reabrir o mesmo ERS | 0,7 s (47 fontes `unchanged`, 0 indexadas), RSS 58 MB; contagem de registros igual antes e depois (3.727.017): sem duplicação. 3 repetições, mesmo número |
| Derivação e correlação no volume do pior caso | Derivação 81–87 s (24 % do total), correlação 4,5–4,7 s, ligação de payloads 12,6–13,6 s; tudo dentro do mesmo processo e do mesmo pico de RSS |
| Incremental (`Watcher`, pasta) sem mudança | 0,00 s por ciclo (só `stat`) |
| Incremental com 5 MB a mais num arquivo, caso de 2.600 MB (16,9 milhões de registros) † | 20,5–20,6 s por ciclo (3 ciclos), dos quais derivação 1,3 s; RSS 424 MB |
| Incremental com 5 MB a mais, caso de 800 MB (5,2 milhões de registros, tamanho do ERS Verbose real) | 6,4–6,6 s por ciclo (3 ciclos), derivação 0,4–0,5 s; RSS 273 MB |

**Incremental: o custo de um ciclo cresce com o caso, não com o que chegou.** Perfil de um ciclo no caso de 2.600 MB: ligação de payloads 60 %, correlação normal↔sensitive e ligações por chave 30 %, derivação 10 %. Causa: crescer um arquivo apaga e reprocessa o último registro (seção 9.3), o que incrementa `meta['entries_epoch']`; esse contador entra na assinatura de **todos** os grupos em `derive/payloads.py`, `derive/correlate.py` e `derive/links.py`, então todos os processos do caso são recalculados a cada ciclo. Não há meta de tempo para o ciclo no ROADMAP; com o polling de 2 s, o Follow fica 6 a 20 s atrás do arquivo nesses tamanhos. Correção proposta e aceita como item pós-0.1.0 (seção "Decisões"); não feita aqui.

## Onde está o tempo (perfil do sintético Verbose de 300 MB)

Sem ponto quente único. Ingestão: SQLite (inserção em `entry` com 6 índices, `entry_body`, FTS, `bucket`, `COMMIT`) ≈ 60 % do tempo de indexação; o resto é Python por registro (envelope, classificação por template, severidade, montagem das linhas). Derivação ≈ 25 % do total. Experimentos na mesma entrada (uma rodada cada, só diagnóstico): `wal_autocheckpoint=0` −6 %, `synchronous=OFF` −6 %, sem inserção no FTS −3 %, `cache_size` de 512 MB +4 % (RSS 648 MB), sem os índices de `entry` +94 % (a derivação depende deles). Nenhum ajuste de lote, pragma ou índice dá o ganho de 2× que a meta proporcional pediria; isso exige paralelismo (derivação por cadeia em processos, ou parse e classificação em processos com um escritor único).

## Não medido

- Windows: os números são do Linux. O `bench.py` mede o pico no Windows (`GetProcessMemoryInfo`); a rodada no PC de destino fica para o roteiro manual do 8.5.
- ERS real com os 14 adapters em Verbose: não existe amostra; coberto pelo sintético.
- Rotação durante o `watch` com o banco grande (só crescimento de arquivo).

## Decisões

Decididas pelo Jefferson em 2026-10-11, com os números acima mantidos como medidos:

1. **Meta de tempo do ERS Verbose (M6): conta só a indexação.** Indexação do par em 51,8 s (PASS). A ingestão inteira, 73,3 s, fica registrada como informação, sem valer como meta.
2. **Banco do ERS Verbose real: desvio de 4,5 % aceito.** 1,78 GB medido contra a meta de 1,7 GB.
3. **Tempo do pior caso sintético: aceito para a 0.1.0.** 346 s no sintético de 2.600 MB (≈ 245 s com densidade real). O ROADMAP só pede sem erro e RSS < 1 GB; paralelização (derivação por cadeia em processos, ou parse e classificação em processos com um escritor único) não entra na 0.1.0.
4. **Incremental proporcional ao que chegou: item PÓS-0.1.0.** Proposta aprovada: época de registros por stream em vez de global; assinatura e recálculo por processo na ligação de payloads e por par na correlação (com as estatísticas por par agregadas no grupo); `links.py` continua por grupo, mas só quando um stream de chave mudou. Ganho esperado: ciclo de segundos para frações de segundo no caso real. Mexe em três algoritmos com invariantes delicadas; item próprio, com teste de equivalência contra o recálculo completo.
5. **FTS com termo muito frequente: plano alternativo PÓS-0.1.0.** p95 de 0,9 s (sem carga) a 1,05 s (com carga) no pior caso. Plano: quando a contagem de ocorrências passa de um limite, percorrer `ix_entry_ts` e testar a pertinência em lotes, em vez de materializar e ordenar tudo.

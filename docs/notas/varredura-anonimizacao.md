# Varredura de anonimização (roadmap 7.6)

Data: 2026-10-10. Escopo: exports gerados a partir do caso sintético de `tests/fixtures/export/build_case.py`. Nenhum ERS de `local-samples/` foi usado.

## O que foi feito

- Caso sintético criado com `build_export_case` numa pasta de trabalho temporária (fora do repo).
- Exports com `dmslog export --case demo --ids 1,3,6,7 --event 1,2 --context 1`, sempre com `--out` para arquivo `.txt`, mais as opções de cada cenário: `--anonymize` (s01 a s03 e todos os `s_off_*` e `t_std_off_*`) ou `--no-anonymize` (s04, o controle), `--redaction-mode strict|standard` e `--include-payloads` onde indicado. O comando exige uma das duas opções de anonimização.
- Cenários:
  - `s01`: anonimização ligada, modo strict (padrão), com `--include-payloads`.
  - `s02`: anonimização ligada, modo standard, com `--include-payloads`.
  - `s03`: anonimização ligada, modo strict, sem payloads.
  - `s04`: sem anonimização (controle positivo: confirma que a varredura detecta os padrões).
  - `s_off_<categoria>`: strict com uma categoria desligada por vez, em `redaction.toml` (`[categories]`), 13 cenários.
  - `t_std_off_<categoria>`: standard com uma categoria desligada por vez, 13 cenários.
- Varredura por regex (GUID, IPv4, host/FQDN, usuário, URL, placa AAA-0000 e AAA0A00, e-mail, telefone) e por valores literais da lista de termos sensíveis da fixture (contagem agregada por categoria, sem os valores).
- Conferência de encoding e fim de linha: todos os `.txt` (30 arquivos) em UTF-8 sem BOM e só com CRLF; nenhum marcador do mapa local (`redaction-maps`, `"values"`, `map.json`) dentro dos `.txt`.

## Tabela: cenário × categoria (contagem de ocorrências)

Regex = ocorrências pelo padrão. Literal = ocorrências dos valores literais da fixture.

| Cenário | GUID | IPv4 | Host | Usuário | URL | Placa | E-mail | Telefone | Literais |
|---|---|---|---|---|---|---|---|---|---|
| s01 strict + payload | 0 | 0 | 3* | 3* | 0 | 0 | 0 | 0 | 0 |
| s02 standard + payload | 0 | 0 | 5* | 3* | 0 | 0 | 0 | 0 | 8 (termos de usuário) |
| s03 strict sem payload | 0 | 0 | 3* | 3* | 0 | 0 | 0 | 0 | 0 |
| s04 sem anonimização | 5 | 2 | 21 | 9 | 3 | 4 | 2 | 6 | 60 |

\* Ocorrências de regex que são falso positivo: formas de tokens (`user: <USER-n>`) e namespaces .NET de linhas de stack trace (padrão `aaaa.aaa.aaa.aaaa`). Nenhum valor sensível real.

Cenários com categoria desligada (literais que passam, por categoria):

| Cenário | Categoria desligada | Literais que aparecem no export |
|---|---|---|
| strict (`s_off_*`) | qualquer, exceto ticket e mrid | 0 (o valor vira `<VALUE-n>`) |
| strict (`s_off_ticket`) | ticket | 2 |
| strict (`s_off_mrid`) | mrid | 2 |
| standard (`t_std_off_*`) | guid, gid, ticket, mrid, plate, host, ip, user, path, person, phone, email | igual ao total da categoria na s04 (passa) |
| standard (`t_std_off_url`) | url | URL completa não aparece como literal; o caminho aparece (3 URLs por regex), com o host ainda mascarado pela categoria host |

## Achados

Nenhum vazamento de categoria habilitada nos cenários anonimizados padrão (s01, s03) nem no standard (s02) para as oito categorias de regex. Para os valores literais da fixture, s01 e s03 têm 0 ocorrências; s02 tem 8 ocorrências de termos de usuário (achado 1, esperado no modo standard sem `[terms]`).

1. **Esperado (standard sem termos): 8 ocorrências de termos de usuário (2 termos da fixture) em s02.** Cenário: standard, `--include-payloads`. Onde: título do relatório e corpo. Causa: o perfil padrão não tem `[terms]`, e o modo standard só aplica regras de padrão. Strict mascara esses termos (0 em s01). Vale decidir se o modo standard deve avisar quando não houver termos configurados.
2. **Corrigido: strict com `ticket` ou `mrid` desligados deixava os valores passarem (2 cada), enquanto as outras categorias desligadas viravam `<VALUE-n>`.** Cenário: `s_off_ticket`, `s_off_mrid`. Causa (investigação posterior): era um vazamento da allowlist de texto livre do strict (`_free_safe`), que mantinha um composto "palavra de rótulo de identificador + até 4 dígitos", e não um tratamento de campos de template; a categoria desligada só expunha o problema, que existia também com todas as categorias ligadas. Conserto: o composto com dígitos e palavra de rótulo é sempre substituído no strict, e rótulo de categoria desligada passa a render `<VALUE-n>` (ARQUITETURA 10.2).
3. **Falsos positivos da regex de host (s01, s02, s03):** namespaces de stack trace e padrão de versão. Não são dados sensíveis; só regra de regex grosseira.

## Conclusão

Com anonimização padrão (strict), os exports da fixture não contêm GUID, IPv4, host, usuário, URL, placa, e-mail, telefone nem os valores literais da lista sensível, inclusive dentro do payload XML. Com uma categoria desligada, os valores passam em standard; em strict, depois do conserto do achado 2, o valor vira `<VALUE-n>` (antes, ticket e mrid passavam). O mapa local não aparece no `.txt`, e o arquivo sai em UTF-8 sem BOM com CRLF. Pendência: achado 1 (decidir se o standard deve avisar sobre termos não configurados). O achado 2 foi corrigido.

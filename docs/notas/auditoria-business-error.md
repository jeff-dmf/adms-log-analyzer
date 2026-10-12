# Auditoria do `business_error` nas regras de operação (issue #68, M4 "ainda abertas")

> **Atualização (2026-10-11):** o Jefferson confirmou a definição do Crm (`Detected errors` mais um evento de sistema, com o fim sempre ok, é erro de negócio) e a regra foi declarada em `acrm.toml` com testes sintéticos de regressão (`tests/unit/test_business_error_rules.py`, que cobre também as demais operações que já declaram `business_error`). Com isso, o Crm passa de SEM DEFINIÇÃO para COERENTE declarado: 10 regras declaram `business_error` (Wfm 2, AVL 1, Osr 5, AEDP 1 e Crm 1) e 5 seguem SEM DEFINIÇÃO (Smr 1, Ami 1, Clc 1 e BLTHost 2). O texto abaixo é o relatório original, mantido como registro.

Data: 2026-10-11. Escopo da auditoria original (anterior à declaração do Crm): só leitura de `src/dmslog/adapters/*.toml`, `src/dmslog/derive/operations.py`, `docs/formato/*.md`, `src/dmslog/knowledge/curated/hints.toml` e testes/fixtures sintéticos. Nenhuma regra foi alterada e nenhuma amostra real foi consultada. As contagens abaixo são de testes e fixtures; números de `docs/formato/` só aparecem como referência de arquivo:linha.

## 1. Mecanismo

- Uma regra `[[operation]]` só produz `business_error` se declarar `business_error = [códigos]` e **todos** os códigos aparecerem dentro da operação (`src/dmslog/derive/operations.py:15-18`). Sem a chave, o status nunca vira `business_error`.
- Precedência do status: `fail` > `business_error` > `ok` (`operations.py:27-28`).
- Os códigos declarados são sempre o par "bloco de validação" + "evento de sistema" (Wfm e Crm: `GEN-ERR-01` + `GEN-EVT-01`; AVL: `AVL-007` + `AVL-008`; Osr: `OSR-005` + `OSR-007`), exceto o AEDP, que usa um único código de fim (`AEDP-003`).
- O evento de sistema só é emitido quando algum código tem severidade acima do limiar (`ErrorSeverityEventingThreshold`); códigos só Info ficam `ok` (`docs/formato/wfm-ont.md:253`; `docs/formato/avl-osr-smn-siteswitch.md:332`). Esse filtro é o que impede o falso positivo nas famílias declaradas (Wfm, AVL, Osr e, desde 2026-10-11, Crm).

## 2. Tabela por família e operação

Total: 22 regras de operação nos adapters reais (fora `_example.toml`). Declaravam `business_error` na auditoria: 9; com o Crm declarado depois (nota no topo), são 10. A coluna "Classificação" é a da auditoria; a linha do Crm está atualizada.

| Família | Operação | Declaração (código) | O que a documentação define | Classificação | Evidência sintética |
|---|---|---|---|---|---|
| Wfm | operações de requisição (`name_field = op`) | `awfm.toml:675-682` | Erro de negócio = `Detected errors` + evento de sistema na requisição aberta; o evento só sai acima do limiar e a requisição termina com sucesso (`wfm-ont.md:132`, `:204`, `:253`) | COERENTE | `test_derive_rules_wfm_ont.py:57` (regra carregada) e `:88-96` (1 caso: códigos + evento = `business_error`) |
| Wfm | `Created crew model` | `awfm.toml:684-691` | Idem | COERENTE | Mesma regra de teste; sem caso próprio para esta operação |
| AVL | evento de veículo (`name_field = event`) | `aavl.toml:196-203` | Fim é sempre "finished successfully"; mensagem rejeitada = AVL-007 + AVL-008 entre início e fim (`avl-osr-smn-siteswitch.md:104`, `:200`, `:297`) | COERENTE | fixture `derive_rules/avl_osr_smn_sss/avl.json:16-24` (1 caso, `business_error`) |
| Osr | `GetIncidents` | `aosr.toml:389-395` | Erro de negócio = OSR-005 + OSR-007 entre início e fim; fim `processed.` (`avl-osr-smn-siteswitch.md:209`, `:116`) | COERENTE | `osr.json:16-24` (1 caso) |
| Osr | `GetIncidentUsagePoints` | `aosr.toml:398-404` | Mesma definição da família (a doc fala do adapter, não de cada operação) | COERENTE (extrapolação da família) | sem caso de teste |
| Osr | `ChangeIncidents` | `aosr.toml:407-413` | Idem | COERENTE (extrapolação) | sem caso de teste |
| Osr | `ChangedIncidentHazards` | `aosr.toml:416-423` | Idem; códigos Critical levam ao evento e a `OSR-022` (descarte), declarado como `fail` e que vence o `business_error` (`avl-osr-smn-siteswitch.md:332`; `hints.toml:213-215`) | COERENTE (extrapolação; interação com `fail` sem teste) | sem caso de teste |
| Osr | `CreatedIncidentHazards` | `aosr.toml:426-432` | Idem | COERENTE (extrapolação) | sem caso de teste |
| AEDP | `ReceiveIncidentDevice` | `aedp.toml:67-75` | Falha do AEDP-003 = dispositivo inexistente no modelo; definido como erro de negócio, esclarecido pelo Jefferson em 2026-10-10 (`aedp.md:29`, `:45`) | COERENTE, com limitação documentada: toda falha AEDP-003 conta, mesmo com outra razão (a razão está na continuação; o formato de template não casa continuação) | `test_derive_engine.py:627-653` (1 caso) e `test_catalog_aedp.py:61` |
| Ont | `SendIncident` | não declarado | A doc diz que o Ont "não tem erros de negócio, só exceções de transporte" (`wfm-ont.md:279`); falhas são ONT-020/021/022 (`:181`) | COERENTE (ausência correta) | `test_derive_rules_wfm_ont.py` cobre falha, não `business_error` |
| Ont | `SendIncidentTroubleTickets` | não declarado | Idem | COERENTE (ausência correta) | idem |
| Smn | `SendSwitchingSteps`, `SendWorks`, `SendSwitchingPlans` (3 regras) | não declarado | "Smn não tem códigos de erro no catálogo: os erros são exceções WCF/HTTP" (`avl-osr-smn-siteswitch.md:334`); SMN-015/016/017 são falhas de resposta (`:173-175`) | COERENTE (ausência correta) | `smn.json` (falhas, sem `business_error`) |
| Crm | `GetTroubleTickets` / `CreatedTroubleTickets` (1 regra, `name_field = op`) | declarado em 2026-10-11 (`acrm.toml`, mesmo par e limiar do Wfm); na auditoria: não declarado (`acrm.toml:149-153`) | O Crm emite o mesmo trio (`Detected errors`, aviso de sensitive, evento de sistema) dentro da operação, mas a doc o chama só de "erros de validação" e não diz "erro de negócio" para o Crm (`crm-e-demais.md:136`, `:141`, `:290-296`) | era SEM DEFINIÇÃO (candidata mais forte); hoje COERENTE, declarado (definição confirmada pelo mantenedor em 2026-10-11) | `tests/unit/test_business_error_rules.py` (regressão sintética) |
| Smr | `CreatedWorks` | não declarado (`asmr.toml:90-94`) | `SMR-006`/`SMR-007` já estão como `fail`; a doc diz que o fim "successfully" vale mesmo com erro e que sucesso = SMR-004 (`crm-e-demais.md:184-186`). Há `Detected errors` no Smr (`:392`), sem definição de erro de negócio | SEM DEFINIÇÃO | sem fixture |
| Ami | `GetMeterReadings*` / `CreatedMeterReadings` (1 regra) | não declarado (`aami.toml:119-123`) | `AMI-007` (validação, "processo será abortado") está como `fail`; trio de erro presente em volume mínimo (`crm-e-demais.md:165`) | SEM DEFINIÇÃO | sem fixture |
| ClaimCheck (Clc) | `FileNotification` | não declarado (`aclc.toml:149-153`) | Validações `[Validation <sev>]` (CLC-007) são cabeçalhos; fim OK = CLC-009, falha = CLC-010; severidade do prefixo ainda sem regra de mapeamento (`crm-e-demais.md:213`, `:415`) | SEM DEFINIÇÃO (e pendência aberta de severidade) | sem fixture |
| FileUpload (Fup) | `FileUpload` | não declarado (`afup.toml:123-126`) | O evento de sistema do Fup é de sucesso (`crm-e-demais.md:245`) | COERENTE (ausência correta) | sem fixture |
| BLTHost | `IsReadyForModeSwitch`, `RemoteModeActivation` (2) | não declarado | Sem noção de erro de negócio (comandos de modo) | SEM DEFINIÇÃO (não aplicável) | sem fixture |
| SiteSwitch (Sss) | `HealthCheck` | não declarado | Sem códigos de erro (`avl-osr-smn-siteswitch.md:334`) | COERENTE (ausência correta) | `sss.json` |

## 3. Totais

| Classificação | Regras |
|---|---|
| COERENTE, declarado | 10 regras: Wfm 2, AVL 1, Osr 5, AEDP 1 e Crm 1 (9 na auditoria; Crm declarado em 2026-10-11) |
| COERENTE, ausência correta (a doc diz que não existe) | 7 (Ont 2, Smn 3, Sss 1, Fup 1) |
| SEM DEFINIÇÃO (nem doc nem código; não inventar) | 5 regras: Smr 1, Ami 1, Clc 1, BLTHost 2 (6 na auditoria, com o Crm) |
| FALTA REGRA (a doc define e o código não declara) | 0 |
| DECLARADO SEM DOC | 0 |

Total de 22 regras = 10 + 7 + 5 (na auditoria, 9 + 7 + 6). Nenhuma lacuna firme de regra; as lacunas são de definição.

## 4. Lacunas mais relevantes

1. **Crm (`GetTroubleTickets`, `CreatedTroubleTickets`) — resolvida em 2026-10-11:** estrutura idêntica à do Wfm (`Detected errors` + evento de sistema dentro da operação, sempre fim com sucesso), mas a documentação só rotula como validação. É o maior volume entre as não declaradas (`crm-e-demais.md:136`). Falta decisão do Jefferson ou amostra que confirme que o evento só sai acima do limiar.
2. **Osr, 4 das 5 operações sem teste:** só `GetIncidents` tem caso de `business_error`; `ChangedIncidentHazards` tem ainda a interação com `OSR-022` (`fail` vence) sem teste.
3. **Wfm `Created crew model` e AVL sem teste de falso positivo:** nenhum caso mostra que "só códigos Info, sem evento de sistema" permanece `ok` (comportamento central do limiar).
4. **AEDP-003 sem separação de razão:** limitação conhecida (`aedp.md:29`); qualquer razão nova vira `business_error` em silêncio.
5. **Smr, Ami, Clc:** o trio de erro aparece, mas hoje só `SMR-006/007`, `AMI-007` e `CLC-010` viram `fail`. Sem definição de qual parte é erro de negócio (por exemplo, `SMR-006` "WOM retornou erros" pode ser negócio ou falha). O ClaimCheck depende ainda da regra de severidade do prefixo `[Validation ...]` (`crm-e-demais.md:415`).

## 5. Recomendações priorizadas

**Agora (sem depender de amostra):**
- Acrescentar testes sintéticos de regressão: caso "só Info, sem evento de sistema = `ok`" para Wfm, AVL e Osr, e um caso por operação Osr que ainda não tem. Não muda comportamento nem declara regra nova.
- Registrar na ARQUITETURA/ROADMAP que a política é "declarar só com definição documental" e fechar o item "ainda abertas" do M4 com este resultado (nenhuma regra faltando).

**Depende de decisão do Jefferson ou de amostra (não implementar antes):**
- Crm (feito em 2026-10-11, mantido como registro): confirmar com o Jefferson que `Detected errors` + evento de sistema no Crm é erro de negócio da operação. Se sim, declarar `business_error = ["GEN-ERR-01", "GEN-EVT-01"]` na regra do Crm e documentar em `docs/formato/crm-e-demais.md`. Antes, validar em amostra real fora do repo que o evento sai só acima do limiar.
- Smr, Ami: definir se `SMR-006`/`AMI-007` são negócio ou falha; só depois mexer.
- AEDP: se aparecer razão diferente de "dispositivo inexistente no modelo", reavaliar um recurso de template com casamento de continuação.

**Deixar fora:**
- Ont, Smn, Sss, Fup: a doc afirma que não há erro de negócio (falhas são de transporte/exceção); declarar seria falso positivo.
- BLTHost e Clc: sem definição aplicável; ClaimCheck só depois da decisão sobre a severidade do prefixo de validação.

## 6. Risco de declarar sem definição documental

`business_error` aparece com o tom visual "warn" no painel (a exportação, `messages.py:253`, só traz a contagem de `business_error`, sem classificação "warn"), no resumo exportado e nos filtros (`web/js/events-model.js:16`, `messages.py:253`). Declarar a regra onde a família não define o conceito faz o usuário final tratar como erro de negócio algo que pode ser informativo (por exemplo, `Detected errors` só com códigos Info, que ficam `ok` justamente pelo limiar) ou uma falha técnica (`SMR-006`). O erro é silencioso: nenhum teste quebra e a contagem do painel muda. Por isso a regra é declarar só com definição documental e teste sintético de falso positivo junto.

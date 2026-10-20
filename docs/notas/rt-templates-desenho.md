# Mineração de templates dos servidores RT: desenho

Data: 2026-10-20. Autor: arquiteto (Opus). Estado: decidido; executar na ordem da seção 8.

Pré-requisito: a divisão de famílias (`feat/rt-families`: `DMSFH`, `DMSJOB`, `DMSCORE`, `DMSMODEL`, `DMSOMS`, `DMSRT`) mesclada na `main`.

## 0. Ponto de partida (medido)

Fonte: inventário local de assinaturas (`parse/signature.py`) dos 3 ERS RT de exemplo, um `.tsv` por processo e fluxo, só as 600 assinaturas mais frequentes de cada fluxo. Nada dele entra no repositório; os números abaixo são contagens.

| Família | Registros (3 ERS) | Fatia | Observação |
|---|---:|---:|---|
| DMSFH (28 instâncias por servidor) | ≈ 18,5 M | 66% | conteúdo praticamente igual entre instâncias; ~1.000 assinaturas, 86 cobrem 90% |
| DMSOMS | ≈ 3,0 M | 11% | inclui 587 mil registros do sensitive do AlarmsEvents com **uma** assinatura |
| DMSCORE | ≈ 2,9 M | 10% | CoreFunctions sozinho tem 1,1 M |
| DMSJOB | ≈ 2,0 M | 7% | Scheduler e Launcher: < 30 assinaturas cobrem 98% |
| DMSMODEL | ≈ 1,6 M | 6% | NetworkModel e WorkManagement têm cauda longa por variação de texto |
| OASERR, BLTHOST, SERVICE (nos servidores RT) | 14,4 mil; 289; 372 | < 0,1% | |

- Nível do envelope: 99,9% `DEBUGLOG`; 11,5 mil linhas `ERROR` (já viram severidade 2 pelo passo 1 de `parse/severity.py`).
- Cobertura atual com o catálogo (`_common` + arquivos existentes), estimada instanciando as assinaturas: DMSFH 0%, demais serviços RT 10,9%, OASERR 0,6%, BLTHOST 35%, SERVICE ~100%.
- Linhas compartilhadas: 167 assinaturas aparecem em 3 ou mais processos RT (≈ 0,9 M registros); 267 aparecem em processos de 2 ou mais famílias RT e não casam com `_common` (≈ 0,8 M): infraestrutura do produto (`[TransactionResourceManager]`, `(REPLICATION_INFO)`, `PubSub(<nome>) client ...`, `[Configuration] [ConfigurationManager] ...`, `MTS: ...`, `[GlobalLicenseProvider]`, ...).
- Fatos de correlação medidos no caso local: no Launcher o início e o fim de um job ficam em TIDs **diferentes**; no Scheduler todos os jobs ficam numa TID só, **intercalados**; no FunctionHost `Function execution started/finished for JobID` ficam na **mesma** TID e sem concorrência (0 de 205 pares divergentes, no máximo 1 aberto); os `JobID` do FunctionHost aparecem no Launcher da mesma coleta (mesmo espaço de números).
- PSENGINE, ARBIP, NMC, ERS e SERVERLOG não foram indexados no inventário (escopo opcional): não há dados para eles ainda.

Limites do inventário: corta em 600 assinaturas por fluxo e mede por assinatura (antes de juntar variantes numa regex). As metas da seção 1 já descontam isso; a medição de aceite é feita sobre os registros do caso (seção 8, T0).

## 1. Famílias, arquivos, códigos, orçamento e metas

### 1.1 Decisões gerais

1. **Biblioteca RT nova `_dmsrt`** (`src/dmslog/adapters/_dmsrt.toml`, `family = "_dmsrt"`, códigos `RTL-001` a `RTL-099`). Entra nela: (a) as linhas de infraestrutura com estes prefixos: `[TransactionResourceManager]`, `[TransactionMonitor]`, `(REPLICATION_INFO)`, `[RouteID:`, `ClientMessageDelivery`, `ReplicationProxyCallback`, `SMDCs Replication`, `Queue for tracing`, `[BlockFetcher]`, `[PublisherIntegrityUpdateHandler]`, `[CircularQueueReplicator]`, `PubSub(`, `[IdentityCache]`, `[SmartCacheConfigurator]`, `Cache[`, `[ProxyPool]`, `[SecureHostHelper]`, `[GlobalLicenseProvider]`, `[UI Config Provider]`, `[Configuration] [ConfigurationManager]`, `Size of the object [`, `Assembly successfully added to catalog`, `Indexed message hasn't been found`, `MTS: `, `Switching version`; (b) qualquer outra linha que apareça em processos de **duas ou mais** famílias RT. Nada que já case com `_common` é redefinido.
2. **`extends = ["_dmsrt", "_common"]`** em todo arquivo de família RT (nessa ordem). `_common` não muda nesta etapa.
3. **`dmsrt.toml`** (`family = "DMSRT"`): só cabeçalho e `extends`, sem templates próprios. Serve para que um `DMS_*`/`OMS_*` desconhecido também receba a biblioteca RT (família sem arquivo recebe só `_common`).
4. **Um arquivo por família**, nunca por serviço. O processo não aparece no TOML (regra da ARQUITETURA 6.3); as faixas de código por serviço abaixo só organizam o arquivo.
5. **Exclusividade entre arquivos**: um `text` exato é único no catálogo inteiro (`DuplicateError`). Linha que existe em mais de uma família vai para `_dmsrt` (dono: tarefa T1), nunca duplicada.
6. **Teto por família e fluxo**: no máximo 170 templates próprios por tipo de fluxo (normal; sensitive) por arquivo. Motivo: o gerador de fixtures põe um registro por template num arquivo de no máximo 200 registros (`MAX_RECORDS`), junto de desconhecidos e contra-exemplos.
7. **Ordem de escrita dentro de um serviço**: por contagem decrescente do inventário, até a meta ou o orçamento. A cauda longa fica sem template e continua no painel de desconhecidos. **Exceção obrigatória**: assinatura com marcador de falha (`fail`, `error`, `exception`, `timeout`, `timed out`, `unable`, `cannot`, `could not`, `abort`, `not valid`, `invalid`, `lost`, `denied`, `rejected`, `red`) e pelo menos 5 ocorrências ganha template mesmo fora do ranking, num orçamento extra de até 15 por família.

### 1.2 Tabela por família

| Família | Arquivo | `extends` | Prefixo | Faixa | Orçamento (próprios) | Meta de registros com template |
|---|---|---|---|---|---:|---:|
| `_dmsrt` (biblioteca) | `_dmsrt.toml` | (bibliotecas não estendem) | `RTL` | 001–099 | 70 | (contada nas famílias) |
| DMSRT | `dmsrt.toml` | `_dmsrt`, `_common` | (nenhum) | — | 0 | — |
| DMSFH | `dmsfh.toml` | `_dmsrt`, `_common` | `DFH` | 001–199 | 110 | 95% |
| DMSJOB | `dmsjob.toml` | `_dmsrt`, `_common` | `DJB` | 001–169, S100–S129 | 100 (sensitive ≤ 10) | 96% |
| DMSCORE | `dmscore.toml` | `_dmsrt`, `_common` | `DCR` | 001–399, S001–S399 | 160 (sensitive ≤ 10) | 93% |
| DMSMODEL | `dmsmodel.toml` | `_dmsrt`, `_common` | `DMD` | 001–199, S050–S119 | 130 + 30 sensitive | 88% |
| DMSOMS | `dmsoms.toml` | `_dmsrt`, `_common` | `DOM` | 001–209, S001–S160 | 160 + 15 sensitive | 92% |
| OASERR (lacuna RT) | `oaserr.toml` (existente) | sem mudança | `OAS` | 005–029 | 12 | 93% do oasErrLog dos servidores RT |
| BLTHOST (lacuna RT) | `blthost.toml` (existente) | sem mudança | `BLT` | 030–049 | 12 | 85% das linhas dos servidores RT |
| SERVICE (lacuna RT) | `service.toml` (existente) | sem mudança | `SVC` | 020–029 | 3 | 99% (hoje ~100% na estimativa; só a cauda) |
| PSENGINE | `psengine.toml` | `_common` | `PSE` | 001–049 | 30 | 90% |
| ARBIP | `arbip.toml` | `_common` | `ARB` | 001–029 | 15 | 90% |
| NMC | `nmc.toml` | `_common` | `NMC` | 001–049 | 25 | 90% |
| ERS | `ers.toml` | `_common` | `ERS` | 001–029 | 15 | 90% |
| SERVERLOG | nenhum | — | — | — | 0 | — |

- Meta global: **≥ 93% dos registros RT com template** (hoje ~4%).
- SERVERLOG fica sem arquivo: é a família pega-tudo de dezenas de produtos diferentes. Um produto relevante dentro dela ganha regra própria em `families.toml` e arquivo próprio, numa etapa futura.
- PSENGINE, ARBIP, NMC e ERS: códigos, arquivos e orçamentos já reservados; a mineração só começa depois de um inventário com `--all-logs` (tarefa T0b). Não estendem `_dmsrt` (não são serviços `DMS_*`).
- Lacunas: os templates novos de `oaserr.toml`, `blthost.toml` e `service.toml` valem também nos servidores de integração; precisam ser mutuamente exclusivos com os existentes e a suíte INT continua verde.

### 1.3 Orçamento e meta por serviço

Faixa = códigos reservados para o serviço dentro do arquivo da família (sensitive com `S` e o mesmo número de faixa).

| Família | Serviço | Faixa | Orçamento | Meta |
|---|---|---|---:|---:|
| DMSFH | `DMS_FunctionHost_<n>` (todas) | 001–049 host e job; 050–099 montagem do modelo (`[EMSBUILD]`, `[EMSCH]`, `[EMSSE]`, `[EMSPI]`, `[EMSFC]`, `[EMSBC]`, `[EMSRRM]`); 100–149 `[SE]`; 150–189 `[LF]`, `[MR]`, `[PU]`, `[SF]`, `[SSV]` e demais funções; 190–199 falhas genéricas | 110 | 95% |
| DMSJOB | JobScheduler | 001–039 | 20 | 99% |
| DMSJOB | JobLauncher | 040–079 | 25 | 99% |
| DMSJOB | JobManagerService normal | 100–169 | 45 | 88% |
| DMSJOB | JobManagerService sensitive | S100–S129 | 10 | 98% |
| DMSCORE | CoreFunctionsService | 001–099 | 35 | 95% |
| DMSCORE | ModelTopologyService | 100–129 | 12 | 98% |
| DMSCORE | ChangesetRepositoryService | 130–149 | 5 | 99% |
| DMSCORE | ModelPromotionService | 150–159 | 3 | 98% |
| DMSCORE | GraphicalModelService | 160–179 | 6 | 96% |
| DMSCORE | GenericCalculationService | 180–219 | 12 | 95% |
| DMSCORE | LoadForecastService | 220–279 | 25 | 92% |
| DMSCORE | LocalContextManager | 280–319 (+ S280–S289) | 20 + 3 | 85% (sensitive 98%) |
| DMSCORE | ContextCoordinatorService | 320–339 | 8 | 95% |
| DMSCORE | ExportService | 340–369 | 12 | 93% |
| DMSCORE | ControlSequenceManagementService | 370–399 | 12 | 97% |
| DMSMODEL | NetworkModelService | 001–049 | 30 | 80% |
| DMSMODEL | DynamicsDataService | 050–109 (+ S050–S089) | 35 + 25 | 85% (sensitive 95%) |
| DMSMODEL | ModelManagerService | 110–149 (+ S110–S119) | 25 + 3 | 88% |
| DMSMODEL | ReplicationService | 150–169 | 15 | 92% |
| DMSMODEL | ScheduleManagementService | 170–184 | 10 | 98% |
| DMSMODEL | GDAProxyService | 185–189 | 4 | 99% |
| DMSMODEL | LandBaseService | 190–199 | 5 | 99% |
| DMSOMS | IncidentManagementService | 001–049 (+ S001–S019) | 35 + 8 | 88% (sensitive 99%) |
| DMSOMS | WorkManagementService | 050–099 (+ S050) | 40 + 1 | 80% |
| DMSOMS | OutageProcessingService | 100–114 (+ S100–S104) | 10 + 2 | 99% |
| DMSOMS | CrewManagementService | 115–139 (+ S115) | 20 + 1 | 97% |
| DMSOMS | AlarmsEventsService | 140–159 (+ S140) | 15 + 1 | 97% (sensitive 99,9%) |
| DMSOMS | SmartMeterManagementService | 160–184 (+ S160) | 20 + 1 | 90% |
| DMSOMS | MajorEventService | 185–209 | 18 | 92% |

Os sensitive pequenos (só `PubSub(<nome>) client ...`) ficam cobertos pela biblioteca e não usam orçamento da família.

## 2. Convenção de escrita dos templates

### 2.1 De assinatura para template

| Marcador da assinatura | Na regex | Tipo do campo |
|---|---|---|
| `<n>` | `\d+` (sem mínimo de dígitos; `-?\d+` quando a assinatura tem `-<n>`) | `int` |
| `<n>` em posição de data/hora do produto (`<n>/<n>/<n> <n>:<n>:<n> AM`) | `[\d/: ]+(?: [AP]M)?` | `str` |
| `<n>:<n>:<n>` de duração | `[\d:.]+` | `str` |
| `<hex>` (LID, GID, root) | `(?:0x)?[0-9A-Fa-f]{1,16}` | `hex` (ver 2.4) |
| `<guid>` | `[0-9a-fA-F-]{36}` | `guid` |
| `<s>` | `"[^"]*"` ou `'[^']*'`, com a captura por dentro | `str` |
| `<ip>`, `<url>`, `<path>` | `[\d.:]+`, `\S+`, `\S+` | `str` (nunca chave) |
| `<ts>` | `[\d-]+ [\d:.]+` | `str` |

- **Variantes de uma mesma mensagem viram um template**: mesmo texto fixo no início e só uma palavra ou número muda (`Function: StateEstimation` / `Function: LoadFlow`; `LID: <n>` / `LID: <hex>`; nomes de usuário, de host, de job, de propriedade) → uma regex com captura nomeada (`(?P<function>\w+)`). Pares `started`/`finished` de um sub-passo → um template com `(?P<phase>...)`, **exceto** quando os dois lados são `start`/`end` de uma regra de evento (seção 5), que precisa de códigos distintos.
- **Texto fixo no início sempre** (`^\[EMSCH\]: ...`): é a chave do despacho. Regex sem prefixo literal só quando a linha começa por valor variável, e então com um literal obrigatório longo.
- **`text` exato** quando a linha não tem parte variável (`[SE] Load calibration started...`, `Input builder preparation completed`) e só aparece nos processos dessa família. É uma consulta de dicionário, mais barata que qualquer regex: preferir sempre que der.
- **Exclusividade dentro do arquivo**: `check_examples` rejeita exemplo que também casa outro template do mesmo arquivo; a ordem não resolve. Variantes específica e genérica da mesma linha precisam de regex disjuntas (ou viram um template com campo).
- **Despacho**: no máximo 8 regex por arquivo com os mesmos 12 primeiros caracteres literais; acima disso, juntar numa regex com alternação ou captura. Critério de aceite: `TemplateSet.candidates(msg)` ≤ 12 em toda assinatura do serviço e média ponderada por registros ≤ 4.
- **Fim de linha**: terminar com `$` (ou `\.$`) quando o texto termina ali; `.*$` só para cauda variável sem valor (ex.: listas longas que não interessam).

### 2.2 Segurança de regex (o validador decide; resumo de `_schema.md`, "Regex safety")

- Capturas sempre nomeadas, snake_case minúsculo; agrupamento com `(?:...)`.
- Proibidos: flags (`(?i)`), retrorreferência, possessivo, grupo atômico, condicional, grupo sem nome. Máximo de 1.000 caracteres, 24 capturas, classe com até 32 itens, capturas aninhadas até 3 níveis.
- Repetição que itera mais de uma vez só sobre token de um caractere: `[\w, ]+` sim, `(?:\w+, )*` não.
- Uma repetição ambígua (`.*`, `[^,]*` seguido de algo que ela também aceita) é linear: janela padrão. Duas em sequência (`^(?P<a>.+?) - (?P<b>.+)$`) é quadrática: declarar `window` (até ~720; usar 320 quando basta). Três: `window = 128` ou reescrever.
- `window` menor que a linha real faz o template não casar a linha: medir o comprimento máximo da linha no caso local (T0) antes de declarar.

### 2.3 Campos

- Tipos: `int` (contagens, ids numéricos curtos, `job_id`, `slot`, PIDs), `hex` (LID, GID e root em hexadecimal), `guid` (ids de job do JobManager, sessões), `float` (percentuais, MB), `list` (status em lista `DoneWithWarning, DoneWithSuccess`), `str` (o resto).
- Nomes fixos para o mesmo conceito em todas as famílias RT: `job_id`, `function`, `fh_pid`, `slot`, `status`, `root`, `lid`, `gid`, `jms_job`, `job_type`, `phase`, `step`, `host`, `user`, `elapsed_ms`, `count`.
- Um campo que é chave precisa existir com o mesmo nome em todos os templates que a regra de evento usa (seção 5).

### 2.4 GIDs em hexadecimal: limitação atual e correção (C0)

`hex` hoje só converte até 15 dígitos (`_to_hex` em `parse/templates.py`); um GID de 16 dígitos fica como texto hexadecimal e **não** se junta ao mesmo GID em decimal (logs INT, payloads, `signalGid=` decimal do JobManager). Correção C0 (código, antes do lote B): até 16 dígitos hexadecimais viram inteiro; valor acima de 2^63−1 fica como **texto decimal** (não hexadecimal). Assim toda chave `gid` fica em decimal, como já fazem os payloads (ARQUITETURA 6.6). LID e root continuam `hex` sem virar chave.

### 2.5 Exemplos e contra-exemplos (100% sintéticos)

Todo template tem pelo menos um `examples` (com `fields` conferidos no primeiro) e um `counter_examples`. Vocabulário sintético obrigatório:

| Dado | Valor sintético |
|---|---|
| GUID | `00000000-0000-4000-8000-0000000000NN` |
| GID/LID/root hex | dígitos e letras alternados, nunca 9 dígitos seguidos: `4A00B00C00D00E01`, `0x4A00B00C00D00E02` |
| números | até 8 dígitos seguidos (`JobID: 1000123`); o leak scan das fixtures corta 9+ dígitos e o exemplo deixaria de casar |
| host, servidor | `HOST01`, `HOST02` |
| conta, usuário | `EXAMPLE\user01` (domínio `EXAMPLE`) |
| IP | `192.0.2.10` (faixas de documentação) |
| id de requisição com host | `ZONE_HOST01_ADMS` |
| sufixo de cliente em nome de classe | `_Example` (`[IncidentProcessor_Example]`) |
| nomes de incidente, equipe, documento | `INC0000001`, `CREW01`, `WR 3000001` |

Só ASCII (o `test_conventions.py` recusa acento em `src/`). Nunca copiar valor do inventário ou do caso, nem "trocar uns dígitos": escrever do zero. Funções, tipos de job e métodos do produto (`StateEstimation`, `CrewBrowserJob`, `FindSubstationTransmissionOrRegion`) são vocabulário do produto e podem aparecer.

Modelo (validado com o carregador, `check_examples` limpo; códigos ilustrativos):

```toml
[[template]]
code = "DJB-041"
class = "end"
title = "Function result received from a FunctionHost"
sev = 4
sev_field = "status"
sev_map = { DoneWithError = 2, Failed = 2, Cancelled = 3, DoneWithWarning = 3, DoneWithSuccess = 4 }
regex = '''^Result received: (?P<status>\w+) from ProcessID: (?P<fh_pid>\d+) \| SlotID: (?P<slot>\d+) \| JobID: (?P<job_id>\d+) \| Function: (?P<function>\w+)\. Is last job result: (?P<last>True|False)\.$'''
fields = { fh_pid = "int", slot = "int", job_id = "int" }
keys = ["job_id"]
examples = [
    { msg = "Result received: DoneWithWarning from ProcessID: 4242 | SlotID: 3 | JobID: 1000123 | Function: StateEstimation. Is last job result: False.", fields = { status = "DoneWithWarning", job_id = 1000123, slot = 3 } },
]
counter_examples = ["Result received: DoneWithSuccess from ProcessID: x | SlotID: 3 | JobID: 1 | Function: LoadFlow. Is last job result: True."]

[[template]]
code = "DFH-060"
class = "validation"
title = "Element without a valid container"
sev = 4  # per-element model condition the function tolerates (section 3)
fts = false
regex = '''^\[EMSCH\]: Container is not valid for element LID: (?:0x)?(?P<lid>[0-9A-Fa-f]{1,16}), GID: (?:0x)?(?P<gid>[0-9A-Fa-f]{1,16}) \((?P<method>\w+)\)\.$'''
fields = { lid = "hex", gid = "hex" }
examples = [
    { msg = "[EMSCH]: Container is not valid for element LID: 4A00B00C00D00E01, GID: 0x4A00B00C00D00E02 (FindSubstationTransmissionOrRegion).", fields = { method = "FindSubstationTransmissionOrRegion" } },
    "[EMSCH]: Container is not valid for element LID: 12345678, GID: 4A00B00C00D00E02 (FindExample).",
]
counter_examples = ["[EMSCH]: Container is valid for element LID: 1, GID: 2 (X)."]

[[template]]
code = "DFH-160"
class = "other"
title = "Model Readiness step"
sev = 5
fts = false
regex = '''^\[MR\] Model Readiness (?P<step>prepare|model allocation|cleanUp) (?P<phase>started\.\.\.|finished successfully\.)$'''
examples = [
    { msg = "[MR] Model Readiness prepare started...", fields = { step = "prepare", phase = "started..." } },
    "[MR] Model Readiness cleanUp finished successfully.",
]
counter_examples = ["[MR] Model Readiness function started..."]
```

Contra-exemplo bom: a mesma linha com um valor que não é do tipo (`ProcessID: x`), ou a linha vizinha do mesmo prefixo que pertence a outro template.

### 2.6 Como minerar sem vazar

- Fonte de trabalho: os `.tsv` do inventário (contagem, nível, assinatura) e, para ver a forma real de uma linha (comprimento, se o valor é hex ou decimal, continuação), consulta **somente leitura** ao `case.sqlite` local (`unknown_sig.first_entry_id`, `entry.msg`, `entry_body.cont`). O que for lido fica no terminal do agente.
- `tools/mine_templates.py --case-db <caso> --family DMSRT` serve de rascunho, mas o caso local foi indexado antes da divisão (tudo `DMSRT`, menos `DMSFH`): filtrar por processo com SQL, não pela família.
- O rascunho gerado traz mensagens reais nos exemplos: reescrever todos com o vocabulário de 2.5 antes de qualquer outra coisa.

## 3. Severidade e classe

### 3.1 Como conversa com `parse/severity.py`

- Ordem do código: (1) nível do envelope `FATAL`/`EXIT` → 1, `ERROR` ou `(ERROR)` → 2; (2) `sev` do template (ou `sev_field`/`sev_map`); (3) códigos de erro; (4) marcadores de texto; (6) 4.
- Como quase tudo é `DEBUGLOG`, **o template decide**. Regra: **todo template RT declara `sev`**, sem depender dos marcadores (que ignoram `Failed` com maiúscula e `unsuccessful`, e pegariam `WARNING` em linha por elemento). Linhas com nível `ERROR` continuam 2 pelo passo 1, qualquer que seja o template.
- Um `sev` maior (mais brando) que o marcador de texto daria é um rebaixamento: comentário na mesma linha com o motivo, de uma lista fechada: `per-element`, `cleanup of something absent`, `pool or cache housekeeping`, `retry scheduled`, `standby discards`.

### 3.2 Regras

| sev | Quando (pelo texto do template) | Formas típicas | `class` |
|---|---|---|---|
| 1 | processo ou serviço para sem pedido, exceção não tratada, "fatal", perda do papel do servidor fora de comando | (raro; nenhum no inventário) | `error` ou `lifecycle` |
| 2 | falha de job, função, cálculo ou requisição; status `DoneWithError`/`Failed`/`Aborted`; `Calculation failed`, `... execution failed`, `Failed to <verbo>` no caminho da operação; modelo não obtido para um root; exceção .NET com tipo; comunicação perdida ou impossível com outro serviço; memória em nível vermelho e desligamento do FunctionHost | `[EMSFC]: Calculation failed`, `AcquireOperationalModels failed ...`, `Memory leak red level! ProcessID: <n>` | `error` |
| 3 | resultado degradado ou recuperável: `DoneWithWarning`, `Cancelled`; timeout de espera que não aborta (`Timeout has expired`); memória amarela; aviso por root ou job (`Warning: State Estimation quality of root <hex> is questionable`); nova tentativa agendada; dado descartado em servidor standby; FunctionHost reiniciado | `Memory leak yellow level`, `Failed request with ID ... is scheduled for processing` (`retry scheduled`) | `error`, `validation` ou `lifecycle` |
| 4 | ciclo de vida e progresso por job ou requisição (recebido, enviado, iniciado, terminado); **validação por elemento que a função tolera** (container inválido, nó isolado, set point errado, SDP sem cálculo, índice não encontrado), mesmo com `WARNING` no texto (`per-element`); housekeeping de pool | `[EMSCH]: Container is not valid ...`, `[LF] (WARNING) Set point <n> is wrong ...`, `Load calculation unsuccessful for <n> SDPs ...` | `start`, `end`, `req_resp`, `lifecycle`, `validation` |
| 5 | rastreio: sub-passos `started`/`finished` dentro de um job, `[ProxyPool]` adquire/libera, estatística, durações, fila vazia, separadores, heartbeat (`Memory leak green level`), fases de transação, replicação | `[SE] Load calibration started...`, `[GDA Statistics] ...`, `-----` | `other` ou `statistics` |

Por que a validação por elemento fica em 4 e não em 3: é condição crônica do modelo de dados, repetida a cada ciclo para os mesmos elementos (milhões de linhas por ERS); o efeito sobre o cálculo aparece no resultado do job (`DoneWithWarning`/`DoneWithError`), que carrega a severidade. Em 3, esconderia os avisos reais. O detalhe por elemento continua filtrável por template, campo (`gid`) e "Repetidos".

`fts = false`: template com sev ≥ 4, ≥ 50 mil registros nos 3 ERS e sem identificador de negócio útil para busca de texto (o grid continua achando por regex/LIKE com filtro de fonte).

`class`: `start`/`end` só para linhas usadas (ou candidatas) em regra de evento; `req_resp` para envio e recebimento entre processos (`Sending JobID ... to Job Launcher`, `Sending job status to JS`); `lifecycle` para processo, FunctionHost, conexão e estado de serviço; `startup` só para blocos de inicialização que não estão no `_common`; `statistics` para contagens e durações periódicas.

### 3.3 Status de job

- Linha com **um** status (`Result received: <status> ...`, `Job execution finished ... Status: <status>.`, `[PRS] Job status report is received. JobId: <n>, Job Status: <status>`): `sev_field = "status"`, `sev_map = { DoneWithError = 2, Failed = 2, Aborted = 2, Cancelled = 3, DoneWithWarning = 3, DoneWithSuccess = 4, Running = 5 }`, `sev = 4` para valor fora do mapa.
- Linha com **lista** de status (`Sending job status to JS. JobID: <n>, Status: A, B, C`; `[Scheduler] JobID: <n> received its status. Job status: A, B.`): `status` como `list`, `sev = 4`. A falha aparece na linha de status único do mesmo `job_id`. Quando C5 (seção 5.3) existir, essas linhas ganham o mesmo `sev_map`.

## 4. Chaves de correlação

| Tipo de chave | Campo (tipo) | Onde | Observação |
|---|---|---|---|
| `job_id` (novo) | `job_id` (`int`) | DJB Scheduler e Launcher; DFH; DCR CoreFunctions (`[PRH]`, `[PRS]`, `[CRS]` `JobId`) e LoadForecast (`[LookAhead][JobID=<n>]`) | id do job de cálculo RT; o mesmo número atravessa Scheduler → Launcher → FunctionHost → CoreFunctions (medido) |
| `jms_job` (novo) | `jms_job` (`guid`) | DJB JobManager normal e sensitive (`job with guid <guid>`, `Job <guid> switched state ...`, `JobGuid:<guid>`) | job assíncrono de cliente do JobManagerService; não é o `job_id` |
| `gid` | `gid` (`hex` ou `int`) | sinais (`<Tipo> signal <hex> with status ...` do OutageProcessing; `signalGid=` do JobManager e DynamicsData); objetos de negócio do IMS, CRS e WMS (incidente, equipe, assignment, documento, `ApplyDelta ... <hex>:<n>`) | GID é único no ADMS; com C0 vira decimal e junta INT, RT e payloads |
| `incident_gid` | o mesmo campo | IMS, só quando o texto diz que o objeto é incidente | mesma regra dos payloads (ARQUITETURA 6.6) |
| `inc` | `inc` (`str`) | IMS e OMS quando a linha traz o número de exibição do incidente | mesmo tipo usado pelo Crm (INT) |
| `wr`, `plan` | `int` | WMS, se a linha trouxer número de Work Request ou de plano de manobra | mesmos tipos do Smn (INT) |
| `pid` | `fh_pid`, `new_pid` (`int`) | DJB, só nas linhas de ciclo de vida do FunctionHost (memória vermelha, desligamento, reinício, evento do monitor do FH) | não nas linhas de despacho de job (o `job_id` basta) nem no heartbeat |
| `exc_type` | `exc_type` | qualquer linha com tipo de exceção .NET | igual ao INT |

**Não viram chave** (ficam como campo): `function`, `slot`, `lid`, `root`, `method`, `step`, `phase`, `status`, contagens e durações; `host`, `user`, `endpoint`, sessão de cliente, ids de requisição do monitor de event log (contêm nome de máquina), GUID de transação, `ClientId` (já é chave genérica do `_common`), GID de **elemento** nas mensagens por elemento do FunctionHost (milhões de linhas; filtrável pelo campo).

Volume: as chaves RT podem somar até 15% dos registros RT (≈ 4 M linhas de `entry_key` nos 3 ERS). Acima disso, a sessão principal corta as de menor valor (primeiro `gid` de sinal).

Código e UI: **nenhuma mudança para os tipos novos**. Verificado: `/api/keys/types` lista os tipos de `entry_key` (`store/keys.py`), os filtros de agregação, valores distintos e destaques aceitam qualquer `[A-Za-z0-9_.-]{1,64}`, não há rótulos de tipo de chave em `strings.js` e o carregador só exige snake_case minúsculo. A única mudança de código ligada a chaves é a C0 (2.4). A sessão principal registra `job_id` e `jms_job` na ARQUITETURA (6.3 e 8).

## 5. Eventos derivados

### 5.1 Só com TOML (entra nos lotes A e B)

1. **Job no FunctionHost** (`dmsfh.toml`): pareamento por TID é válido (medido: mesma TID, sem concorrência).

   ```toml
   [[operation]]
   name = "FunctionHostJob"
   start = ["DFH-001"]        # New job received for execution. JobID: <n>. Size of the real-time sequence: <n>
   end_ok = ["DFH-005"]       # Job execution finished successfully. Callback result sent. JobID: <n>. Status: <status>.
   end_fail = ["DFH-006"]     # variante de fim com falha, se existir no caso (template próprio, exclusivo)
   fail = ["DFH-190", "DFH-191"]  # falhas de cálculo dentro do job (EMSFC/EMSBC "Calculation failed", ...)
   pair_by = ["tid"]
   check_key = "job_id"
   ```

   `DoneWithWarning` fecha como `ok` (a severidade 3 fica no registro). Aceite: no caso local, `orphan_ends` e sobreposições < 0,1% dos jobs.
2. **Pedido → resultado no Launcher, no Scheduler e no CoreFunctions** (`[[link_rule]]`, chave, sem depender de TID):

   ```toml
   [[link_rule]]        # dmsjob.toml, Launcher
   kind = "response"
   a = "DJB-042"        # [Launcher] [ExecuteJob] Sending Execute command to FH ProcessID ... JobID ... Function ...
   b = "DJB-041"        # Result received: <status> from ProcessID ... JobID ...
   key = "job_id"
   max_dt_ms = 600000

   [[link_rule]]        # dmsjob.toml, Scheduler
   kind = "response"
   a = "DJB-003"        # Sending JobID: <n> Function: <f> to Job Launcher ID: <guid>. Channel index: <n>
   b = "DJB-005"        # [Scheduler] JobID: <n> received its status. Job status: <lista>.
   key = "job_id"
   max_dt_ms = 600000

   [[link_rule]]        # dmscore.toml, CoreFunctions
   kind = "response"
   a = "DCR-010"        # [PRH] JobId: <n>, RootId: <hex> Function: <f> is submitted.
   b = "DCR-011"        # [PRS] Job status report is received. JobId: <n>, Job Status: <status>
   key = "job_id"
   max_dt_ms = 600000
   ```

   `max_dt_ms`: o agente mede no caso o p99,9 da distância a→b e usa `max(2 × p99,9; 60000)`, limitado a 3.600.000.
3. **Reciclagem do FunctionHost vista pelo Launcher** (`dmsjob.toml`): `[[link_rule]] kind = "response"`, `a` = `Memory leak red status detected for process with ProcessID: <n>.`, `b` = `FunctionHost process is restarted because memory leak is occurred. Old FH ProcessID: <n>, new FH ProcessID: <n>.`, `key = "fh_pid"` (o PID antigo se chama `fh_pid` nos dois templates; o novo, `new_pid`), `max_dt_ms = 300000`. Sem `[[operation]]` (não se assume TID).
4. **Não entram nesta etapa**: `[[operation]]` no Scheduler e no Launcher (TID errada ou intercalada, medido); `[[operation]]` de transação (`[TransactionResourceManager]`), por falta de medição de TID; `[[sensitive_pair]]` (nenhum registro normal aponta para o sensitive nos serviços RT; a junção normal/sensitive do JobManager é pela chave `jms_job`).
5. **Rastro do job entre processos, sem código**: a busca por chave (`GET /api/keys/job_id/<valor>`) junta Scheduler, Launcher, FunctionHost e CoreFunctions da mesma coleta (e de outras, com aviso de ordem aproximada). É a resposta da 1ª entrega para "o que aconteceu com o job N".

### 5.2 Ciclo de vida do FunctionHost (decisão)

- Cada `DMS_FunctionHost_<n>` é um stream (o número é a instância do pool; o PID do processo está só no envelope e muda a cada reinício).
- Na 1ª entrega: (a) job por `[[operation]]` (5.1.1); (b) reciclagem pelo Launcher, com `pid` como chave (5.1.3); (c) as linhas de inicialização do FunctionHost (carga de bibliotecas, preparação) como `lifecycle`/`startup` sev 4 ou 5; (d) da linha do Launcher até o FunctionHost certo, o usuário filtra o grid por PID (`fh_pid` = PID do envelope).
- Depois (C3): evento `restart` quando o PID do envelope muda dentro do stream do FunctionHost.

### 5.3 Exige código (tarefas futuras, desenho mínimo)

| Id | O quê | Desenho mínimo | Onde | Revisão |
|---|---|---|---|---|
| C0 | GID hex de 16 dígitos vira decimal | `_to_hex`: até 16 dígitos → `int`; acima de 2^63−1 → texto decimal; testes de propriedade com os valores de borda | `parse/templates.py` | T2 |
| C1 | operação pareada por chave | `pair_by = ["<tipo de chave>"]` (um tipo que não seja `tid`): estado aberto por `(regra, valor da chave)` em vez de `(regra, tid)`; início sem a chave é ignorado e contado (`keyless_starts`); fim e falha casam pelo valor; `entry_ids` = início, fim e registros intermediários com o mesmo valor da chave; demais políticas iguais (`MAX_OPEN`, novo início com o mesmo valor trunca o anterior, despejo). Depois disso, `dmsjob.toml` ganha `[[operation]]` `SchedulerJob` e `LauncherJob` por `job_id` | `derive/operations.py` + `dmsjob.toml` | T2 |
| C2 | passagem do job entre processos | `[[link_rule]] kind = "handoff"`, `scope = "collection"` (mesmo collection group, qualquer processo) e `families = [...]` (famílias cujos registros podem ser ligados; padrão: só a própria). Regras: Scheduler `sent to Job Launcher` → Launcher `New job received`; Launcher `Sending Execute command to FH` → FunctionHost `New job received for execution`, confiança 3 quando `fh_pid` = PID do envelope do registro `b`, senão 2. Rótulo do tipo novo em `strings.js` | `derive/links.py`, `parse/templates.py` (validação), UI | Opus desenha antes; T2 |
| C3 | reinício do FunctionHost | regra `pid_change` no `LifecycleDeriver` para as famílias de `PID_CHANGE_FAMILIES = {"DMSFH"}`: troca do PID do envelope entre registros consecutivos → evento `restart` com `detail = {old_pid, new_pid, cause: "pid_change"}` | `derive/lifecycle.py` | T2 |
| C4 | despejo `@Nome = valor` do sensitive do AlarmsEvents | parser de continuação `kv_at` (conta itens; extrai só nomes de uma lista branca definida com o Jefferson, por ser conteúdo de alarme) | `parse/templates.py` | Opus desenha antes; T2 |
| C5 | `sev_map` sobre campo lista | quando o campo de `sev_field` é lista, o `sev_hint` é o menor (mais grave) dos valores mapeados | `parse/templates.py` | T1 |

## 6. Fluxos sensitive

- **AlarmsEvents sensitive** (587 mil registros, uma assinatura, continuação de uma linha de ~900 caracteres no formato `@Nome = valor`): um template `text` exato `(AES) [EventReporting] [RecordQueues] of eventRecord. EnqueueEvent: Event log =>`, `stream = "sensitive"`, `class = "other"`, `sev = 4` (é o registro do alarme/evento, não ruído), `fts = false` (≈ 0,5 GB de texto no FTS), sem `cont` nesta etapa (C4 depois). O grid acha o conteúdo por regex/LIKE com filtro de fonte.
- **JobManagerService sensitive** (99% numa forma): uma regex para `Start async job(<tipo>) called for job with guid <guid> Jobs count: <n> Client session id: <guid>. Hostname: <host> Endpoint: <ip>`: campos `job_type`, `jms_job` (chave), `jobs`, `session` (campo), `host`, `endpoint`; `class = "start"`, `sev = 4`, FTS ligado (busca por estação de trabalho). As linhas raras restantes (`[ConcurrentJobExecutionManager:...]`) pela regra geral.
- **DynamicsDataService sensitive**: a cauda longa vem do nome do usuário dentro do texto. Uma regex para `(PSS) [BaseOperation] MethodName: <m>, Operation: <op>, Insert: <n>, Update: <n>, Delete: <n>, UserName: <user>, ...` e uma para `(NDS) [SustainedActionsManager] Client host name ...` cobrem ~88%; o resto pela regra geral até 95%. `user` e `host` são campos, nunca chave. `sev = 4` (trilha de ação do operador).
- **IncidentManagement sensitive** (`Old outage ...`, `Old ETR: ...`, `ETR in ...`): templates `DOM-S0xx`, `sev = 4`; chave `gid`/`incident_gid` quando a linha trouxer o id.
- **OutageProcessing e LocalContextManager sensitive** (`User: <conta>.`, `Client [<conta>] ...`): uma regex cada, `user` como campo.
- **Demais sensitive pequenos**: `PubSub(<nome>) client ...` na biblioteca (`RTL-04x`, `stream = "any"`).
- Convenção: template só do sensitive leva `stream = "sensitive"` e código `<PFX>-S<nnn>`; nenhuma linha do sensitive entra como exemplo real.

## 7. Testes e fixtures

### 7.1 Fluxo de `tools/make_family_fixtures.py`

O gerador escreve, por família de `PROCESSES`, um log normal (e um sensitive, se a família tiver templates de sensitive) com um registro por template, gerado do **primeiro exemplo** do template depois de `sanitize()` (corta 9+ dígitos, troca GUID não sintético, e-mail). Exemplo que deixa de casar depois do `sanitize` é pulado e o template fica sem fixture: `test_every_template_of_the_catalog_is_hit` falha. Daí as regras de 2.5.

### 7.2 O que a sessão principal integra (pontos com dono único)

1. `tools/make_family_fixtures.py`:
   - layouts novos em `_file_name`: `rt` → `dnaApp_zone1/DMS_REALTIME/<Processo>_<pid>[.sensitive].log` e `rtfh` → `dnaApp_zone1/DMS_REALTIME/<Processo>.log` (o `FOLDER` atual, `SVC`, não casa as regras `DMS_*`);
   - entradas novas **no fim** de `PROCESSES` (para não mudar os PIDs existentes), cada uma no lote em que a família ganha templates: `"_dmsrt": ("rt", "DMS_ExampleService")`, `"DMSFH": ("rtfh", "DMS_FunctionHost_1")`, `"DMSJOB": ("rt", "DMS_JobLauncher")`, `"DMSCORE": ("rt", "DMS_CoreFunctionsService")`, `"DMSMODEL": ("rt", "DMS_NetworkModelService")`, `"DMSOMS": ("rt", "OMS_IncidentManagementService")`;
   - `stream_records` e `EXPECTED_FAMILY`: `"_dmsrt"` usa o conjunto da família `DMSRT` (como `_common` usa `PROCESS`);
   - regenerar e rodar `--check`.
2. `tests/unit/test_family_fixtures.py`: `test_all_logs_ingested_in_their_family` hoje deduz o processo por `re.split(r"[._]", nome)[0]`, que dá `DMS`/`OMS` para os processos RT; passar a mapear arquivo → família pelo próprio `build_all`/`_file_name`.
3. Teste novo `tests/unit/test_rt_catalog.py` (guarda de vazamento e de convenção para `_dmsrt.toml`, `dmsrt.toml`, `dms*.toml` e, depois, `psengine.toml`, `arbip.toml`, `nmc.toml`, `ers.toml`): todo template com `sev`, `class`, `title`, ≥ 1 exemplo e ≥ 1 contra-exemplo; nos exemplos, nenhum GUID fora do prefixo sintético, nenhum `\d{9,}`, IP só nas faixas de documentação, nenhum e-mail, conta só `EXAMPLE\...`; códigos no prefixo e na faixa da família (tabela 1.2); tipos de chave numa lista fechada (`job_id`, `jms_job`, `gid`, `incident_gid`, `inc`, `wr`, `plan`, `pid`, `exc_type`, `errcode`, `msgid`).
4. `src/dmslog/knowledge/curated/process_map.toml`: as famílias RT já estão como fora do escopo de adapters (branch das famílias); PSENGINE, ARBIP, NMC e ERS só se a regra de família mudar.
5. ARQUITETURA: linha na seção 15 por PR; 6.3 (famílias com templates, biblioteca `_dmsrt`, tipos de chave novos), 9.1a (RT sobe para o nível "Com templates" e, com 5.1, "Com eventos" para DMSFH/DMSJOB/DMSCORE).
6. Guarda local de vazamento antes de cada commit: `python3 <scratchpad>/mine/denylist.py check <arquivos alterados>` deve dizer `CLEAN` (a lista de termos fica só no scratchpad, nunca no repositório).

### 7.3 O que cada agente entrega

- O(s) arquivo(s) TOML da tarefa, no worktree próprio (`<scratchpad>/wt/<branch>`), sem commit.
- Relatório final só com números: registros e cobertura por processo e fluxo (antes e depois), templates por serviço, chaves usadas, distribuição de `sev`, regras de evento com contadores medidos, e o que ficou de fora com o motivo. **Nenhuma assinatura, mensagem ou valor do caso no relatório.**
- O agente não edita gerador, fixtures, testes, `families.toml`, `process_map.toml` nem documentos.

## 8. Decomposição, ordem e aceite

### 8.1 Ordem

| Fase | O quê | Quem | PR |
|---|---|---|---|
| 0 | Mesclar `feat/rt-families`. **T0**: script local `<scratchpad>/mine/rtcov.py` (fora do repo): abre o `case.sqlite` local só para leitura, mapeia cada `source.member` para a família com o `classify` de `ingest/discover.py` do worktree informado, carrega o catálogo de `--adapters <dir>` (`load_dir`), classifica `entry.msg` por `TemplateSet.classify_text` em streaming e imprime, por processo e fluxo, registros, % com template, maior comprimento de linha por template e `TemplateSet.candidates` (máximo e média ponderada); com `--unknown N` lista as N assinaturas sem template mais frequentes **só no terminal**. Roda sob `flock <scratchpad>/heavy.lock <scratchpad>/capped -m 4G`. **C0** + `test_rt_catalog.py` + layouts `rt`/`rtfh` no gerador (sem entradas novas) | sessão principal (Sonnet); C0 por agente Sonnet | PR 0 (T2) |
| 1 (lote A) | T1, T2, T3 em paralelo | 3 agentes Sonnet | PR A (T2) |
| 2 (lote B) | T4, T5, T6, T7 em paralelo; começa depois que T1 estiver integrado localmente (a biblioteca define o que as famílias não repetem) | 4 agentes Sonnet | PR B1 (T4 + T5), depois PR B2 (T6 + T7) |
| 3 | C1, C3, C5 + regras `[[operation]]` por `job_id` no `dmsjob.toml` | agentes Sonnet (C1 e C3 em arquivos disjuntos) | PR C (T2) |
| 4 | **T0b**: inventário com `--all-logs` dos 3 ERS (PSengine, arbIP, NMC, ERS) no mesmo formato de `mine/inv`; depois T8 e T9 | Haiku (T0b); Sonnet (T8, T9) | PR D (T2) |
| 5 | C2 e C4: desenho Opus, implementação Sonnet | Opus, depois Sonnet | PR E |

Um PR por vez, a partir da `main` atual; antes de abrir: mesclar a `main`, regenerar fixtures, `ruff check` na raiz e suíte completa uma vez.

### 8.2 Critérios de aceite (de cada tarefa de template)

1. `PYTHONPATH=src .venv/bin/python -c "from dmslog.parse.templates import load_catalog; load_catalog(check_examples=True)"` sem erro.
2. `.venv/bin/ruff check` limpo na raiz.
3. Nenhum dado real: `denylist.py check` → `CLEAN`; regras de 2.5 conferidas; só ASCII.
4. Cobertura medida com `rtcov.py` ≥ meta de cada serviço (tabela 1.3); se um serviço não chegar com o orçamento, o agente para no orçamento e reporta o número (a sessão principal decide se aceita).
5. Orçamento e faixas de código respeitados; todo template com `sev`, `class`, `title`, exemplo com `fields` e contra-exemplo.
6. Despacho: `candidates` ≤ 12 por linha e média ponderada ≤ 4.
7. `pytest -q tests/unit/test_templates.py tests/unit/test_catalog_common.py tests/unit/test_conventions.py` verdes no worktree (nenhum exemplo `GEN-*` capturado por template RT; se capturar, estreitar a regex, nunca editar `group3_overrides`).
8. Regras de evento: contadores medidos no caso (`orphan_ends`, `overlaps`, `unmatched`, `ambiguous`) no relatório; regra com `ambiguous` ou órfãos > 1% sai do arquivo.

Aceite do lote (sessão principal, depois de integrar): reingestão dos 3 ERS em caso novo com vazão ≥ 90% da medida antes dos templates (~9,5 MB/s) e banco no máximo +5%; `entry_key` RT ≤ 15% dos registros RT; cobertura global medida; fixtures regeneradas e suíte completa verde em Linux (o CI cobre Windows).

### 8.3 Revisão

- Agentes não revisam o próprio lote. Revisão cruzada padrão do projeto: PR escrito pelo Claude → Codex `gpt-6.1-sol`, esforço `high` (nível T2: regex e parser). Opus só se o revisor apontar problema de desenho (ex.: C2).
- O revisor confere em especial: exemplos sintéticos, exclusividade, rebaixamentos de severidade com motivo da lista, chaves fora da lista fechada, `window` e regex quadráticas.

## 9. Riscos

| Risco | Efeito | Mitigação (decidida) |
|---|---|---|
| Volume: 28 M registros nos 3 ERS, ~10 MB/s de ingestão | ~1.000 templates a mais podem custar tempo de classificação | `text` sempre que possível; prefixo literal; ≤ 8 regex por prefixo de 12 caracteres; `candidates` medido; aceite de vazão ≥ 90%. Registros com template deixam de calcular assinatura e de atualizar `unknown_sig`, o que compensa parte do custo |
| FTS e `entry_key` | banco cresce | `fts = false` nas linhas de alto volume sem valor de busca (3.2) e no sensitive do AlarmsEvents; chaves só onde correlacionam (4); teto de 15% |
| Tempestade de repetição (PSengine e afins) | milhões de linhas iguais | nada é descartado na ingestão; template `sev = 5`, `fts = false`; agregação por "Repetidos" (ARQUITETURA 10.9) na consulta; orçamento PSE ≤ 30 |
| Muitas regex no despacho | lista de candidatos longa num prefixo comum (`[SE] `, `[LF] `, `[EMSBUILD]: `) | junção de variantes em uma regex com campo; teto de 170 por família e fluxo; biblioteca `_dmsrt` ≤ 70; critério de `candidates` |
| Convivência com INT | template RT capturar linha INT, ou mudar teste INT | famílias disjuntas por caminho; `_common` intocado; `_dmsrt` só nas famílias RT; nos arquivos compartilhados (`oaserr`, `blthost`, `service`) só acréscimos exclusivos; códigos globais únicos (prefixos novos `RTL`, `DFH`, `DJB`, `DCR`, `DMD`, `DOM`, `PSE`, `ARB`, `NMC`, `ERS`); PIDs das fixtures existentes preservados; tipos de chave compartilhados de propósito (`gid`, `inc`, `pid`) |
| Vazamento (repo público) | nomes de máquina, contas, ids e textos do cliente no repo | vocabulário sintético (2.5), guarda `test_rt_catalog.py`, `denylist.py` local antes de commit, relatórios e PRs só com números |
| Inventário parcial | top 600 por fluxo; 3 ERS de um só cluster e versão | metas medidas no caso inteiro com `rtcov.py`; o painel de desconhecidos segue mostrando o que faltar em outras versões |
| Caso local em tmpfs | o `case.sqlite` some num reboot | reindexar com `mine/run_ingest.sh` (~8 min para os 3 ERS) |
| Memória (histórico de OOM) | `rtcov.py` e reingestões em paralelo | tudo pesado sob `flock heavy.lock` + `capped`; um por vez |
| Exclusividade do `check_examples` | variantes "específica antes da genérica" não passam | regex disjuntas ou um template com campo (2.1); status em lista via 3.3/C5 |

## 10. Tarefas

| Tarefa | Agente | Arquivo(s) | Família | Processos | Meta de cobertura |
|---|---|---|---|---|---|
| T0 | sessão principal (Sonnet) | `<scratchpad>/mine/rtcov.py` (fora do repo) | — | todos os RT | — |
| C0 | Sonnet | `src/dmslog/parse/templates.py` (+ teste) | — | — | — |
| T1 | Sonnet | `src/dmslog/adapters/_dmsrt.toml`, `src/dmslog/adapters/dmsrt.toml` | `_dmsrt`, DMSRT | linhas de infraestrutura e linhas de ≥ 2 famílias RT (1.1) | contada nas famílias; ≥ 95% das linhas da lista 1.1 |
| T2 | Sonnet | `src/dmslog/adapters/dmsfh.toml` | DMSFH | `DMS_FunctionHost_<n>` | 95% |
| T3 | Sonnet | `src/dmslog/adapters/dmsjob.toml` | DMSJOB | JobScheduler, JobLauncher, JobManagerService (normal e sensitive) | 99%, 99%, 88% (sensitive 98%) |
| T4 | Sonnet | `src/dmslog/adapters/dmscore.toml` | DMSCORE | CoreFunctions, ModelTopology, ChangesetRepository, ModelPromotion, GraphicalModel, GenericCalculation, LoadForecast, LocalContextManager, ContextCoordinator, Export, ControlSequenceManagement | por serviço (1.3); família 93% |
| T5 | Sonnet | `src/dmslog/adapters/dmsmodel.toml` | DMSMODEL | NetworkModel, ModelManager, DynamicsData, Replication, GDAProxy, LandBase, ScheduleManagement | por serviço (1.3); família 88% |
| T6 | Sonnet | `src/dmslog/adapters/dmsoms.toml` | DMSOMS | IncidentManagement, CrewManagement, OutageProcessing, WorkManagement, MajorEvent, SmartMeterManagement, AlarmsEvents | por serviço (1.3); família 92% |
| T7 | Sonnet | `src/dmslog/adapters/oaserr.toml`, `blthost.toml`, `service.toml` (só acréscimos) | OASERR, BLTHOST, SERVICE | oasErrLog, BLTHost, dnaService/ProcessMonitor/arb* dos servidores RT | 93%, 85%, 99% |
| C1 + regras | Sonnet | `src/dmslog/derive/operations.py` (+ teste), `dmsjob.toml` | DMSJOB | Scheduler, Launcher | — |
| C3 | Sonnet | `src/dmslog/derive/lifecycle.py` (+ teste) | DMSFH | `DMS_FunctionHost_<n>` | — |
| C5 | Sonnet | `src/dmslog/parse/templates.py` (+ teste) | — | — | — |
| T0b | Haiku | `<scratchpad>/mine/inv_all/` (fora do repo) | PSENGINE, ARBIP, NMC, ERS | PSengine, arbIP, NMC, ERS/ERSEngine | — |
| T8 | Sonnet | `src/dmslog/adapters/psengine.toml`, `arbip.toml` | PSENGINE, ARBIP | PSengine, arbIP | 90%, 90% |
| T9 | Sonnet | `src/dmslog/adapters/nmc.toml`, `ers.toml` | NMC, ERS | NMC, ERS/ERSEngine | 90%, 90% |
| C2 | Opus (desenho), Sonnet | `src/dmslog/derive/links.py`, `parse/templates.py`, `strings.js` (sessão principal) | DMSJOB, DMSFH | Scheduler → Launcher → FunctionHost | — |
| C4 | Opus (desenho), Sonnet | `src/dmslog/parse/templates.py` | DMSOMS | AlarmsEvents sensitive | — |

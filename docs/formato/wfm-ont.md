# Formato de log: WfmAdapter e OntAdapter

Base: ERS de 2026-10-08 (zona `dnaApp_mpdmz/DMS_INTEGRATION`), arquivos `WfmAdapter_37228.log` (913.749 linhas, 41 MB) e `OntAdapter_27744.log` (43.629 linhas, 7 MB), mais os `.sensitive.log` correspondentes. Contagens abaixo vêm de varredura completa dos arquivos. Exemplos anonimizados (`<inc>`, `<gid>`, `<placa>`; GUIDs encurtados).

Cobertura temporal:

| Arquivo | Início | Fim |
|---|---|---|
| WfmAdapter_37228.log | 2026-10-07 02:30:11 | 2026-10-08 12:43:03 |
| OntAdapter_27744.log | 2026-10-07 20:30:13 | 2026-10-08 12:42:19 |
| WfmAdapter_37228.sensitive.log | 2026-10-05 17:30:08 | 2026-10-08 12:41:26 |
| OntAdapter_27744.sensitive.log | 2026-08-28 07:56:15 | 2026-10-08 12:38:34 |

O sensitive cobre um período bem maior que o normal, porque escreve bem menos.

## 1. Gramática

### Arquivo

- UTF-8 com BOM (`EF BB BF`) só no início. Fim de linha CRLF em 100% das linhas, sem CR isolado.
- Mesma gramática no log normal e no sensitive.

### Linha de cabeçalho (início de mensagem)

```regex
^(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}:\d{2}\.\d{3}) \[PID=(?P<pid>\d+)\](?:\[TID=0x(?P<tid>[0-9a-fA-F]+)\])? (?P<level>FATAL|EXIT|ERROR|ALWAYS|DEBUGLOG|WARNING|INFO|VERBOSE) - (?P<msg>.*)$
```

- Hora local, sem fuso, com milissegundos.
- TID em hexadecimal minúsculo, sem zeros à esquerda (`0x1a8`, `0x9970`).
- **O `[TID=…]` é opcional.** Em todo o ERS, uma linha não tem TID (Ont, linha 42173, INFO `PSAttach::PubSubReceiveCallBack…`). Com o TID obrigatório, essa linha vira continuação da mensagem anterior.
- `msg` pode terminar em espaço (`Reason: `), e o texto segue nas continuações.
- Não aparecem no ERS: FATAL, EXIT, ERROR e WARNING. A grafia desses níveis é suposição.

### Continuação

É qualquer linha que não casa com o cabeçalho. Não tem prefixo nem escape. Formas observadas:

- TAB inicial: dump de mapa, `Managed Code Stack Trace`, propriedades de EventData.
- `   at …`, com 3 espaços: stack trace .NET.
- Texto livre: `Object name: '…'.`, códigos de erro, `OMS_CREW_CHANGE_REQ GID: …`.
- Linha vazia: 425 no Wfm e 66 no Ont, por exemplo depois de stack trace. A linha vazia pertence à mensagem.
- XML indentado com espaços, no sensitive e no INFO do normal.

### Resultado do teste (100% das linhas)

| Arquivo | Linhas | Cabeçalhos | Continuações | Não casam e parecem cabeçalho | Mensagens multilinha | Maior bloco |
|---|---|---|---|---|---|---|
| Wfm | 913.749 | 137.479 | 776.270 | 0 | 21.324 | 1.646 linhas |
| Ont | 43.629 | 42.983 | 646 | 0 (1 com a regex sem TID opcional) | 77 | 12 |
| Wfm sensitive | 231.177 | 73.050 | 158.127 | 0 | 73.048 | 32 |
| Ont sensitive | 77.898 | 2.186 | 75.712 | 0 | 1.367 | 331 |

Nenhuma linha órfã: o arquivo começa em cabeçalho, logo depois do BOM. Nenhuma continuação começa com `AAAA-MM-DD HH:MM:SS`.

### Delimitação da mensagem

A mensagem vai do cabeçalho até a linha anterior ao próximo cabeçalho, ou até o EOF. Não há terminador. Regras práticas:

- A última mensagem do arquivo pode estar truncada, já que o ERS copia o arquivo em uso.
- Os blocos multilinha nunca aparecem intercalados com outras threads, nem nos dumps de 1.646 linhas. A escrita é atômica por mensagem.
- **O timestamp não é monotônico.** No Wfm há um caso de 3 ms para trás (linhas 847681→847682). Para sequência, use a ordem no arquivo. Para tempo, use o timestamp.
- Para reduzir falso positivo de cabeçalho dentro de conteúdo, exija o padrão completo (`[PID=n]` + nível + ` - `), não só a data.

## 2. Níveis

Configuração efetiva: `LogLevel=DebugLog` em `Registry/AdapterWfm.xml` e `AdapterOnt.xml`. `MessageLogLevel=Info` em todas as operações das duas WebServiceConfiguration. Atenção: o arquivo efetivo é o de `DMSCustomIntegrationPROD`, apontado por `AdapterWfm_2.xml` e `AdapterOnt_2.xml`, e não o de `DMSIntegration`. No Wfm também existe `ModelUpdatersLogLevel=Info`, de efeito não observado.

| Nível | Wfm | Ont | Quando aparece | O que acrescenta |
|---|---|---|---|---|
| DEBUGLOG | 135.287 | 40.497 | O tempo todo (é o nível base) | Ciclo de vida das requisições e dos envios, validação, exceções "não fatais", estatísticas, dumps de mapa. Dentro da janela INFO também aparecem linhas DEBUGLOG ligadas ao log de mensagens (`Message with MessageID … will be logged in sensitive log.`) |
| ALWAYS | 226 | 4 | Qualquer momento, independe do nível | Wfm: 111 pares `(ERROR) [Communication2] …` (falha no framework de comunicação, nas threads de callback). Nos dois: `Debug level changed …` |
| WARNING | 0 | 0 | Nem durante a janela em Warning | — |
| INFO | 543 | 382 | Só na janela de elevação | Abertura de canais Communication2, validação XSD de request/reply, request managers (espera de callbacks IMS/CRS), geração de delta, smart cache e replicação (Ont), PubSub/arb sync, ModeTracker, AdaptivePool. **Conteúdo XML enviado a CRS/WMS/IMS no log normal** (`Content being sent to …:`) |
| VERBOSE | 1.423 | 2.099 | Só na janela de elevação | Rastreio TCP/ShMem do Communication2 (sockets, framing, msgId, tamanhos), compressão por método, proxies, remoção de zero IDs, tabela do ModeTracker |

### Elevação em runtime (evidência direta)

Cada mudança gera uma linha ALWAYS, sempre na mesma thread de controle:

```
2026-10-08 12:38:47.620 [PID=37228][TID=0x44c4] ALWAYS - Debug level changed from DebugLog (3) to Warning (4).
```

| Adapter | →Warning | →Info | →Verbose | →DebugLog | Janela INFO/VERBOSE |
|---|---|---|---|---|---|
| Wfm | 12:38:47.620 | 12:39:22.808 | 12:39:41.295 | 12:40:10.690 | 12:39:22 a 12:40:10 (~48 s) |
| Ont | 12:36:12.196 | 12:37:48.551 | 12:38:06.630 | 12:38:35.902 | 12:37:49 a 12:38:35 (~47 s) |

- O padrão bate com o `toggleDebug` (PRFI 3.3): cada execução sobe um nível, e a 4ª volta de Verbose para DebugLog.
- A numeração interna impressa é DebugLog=3, Warning=4, Info=5, Verbose=6. Ela difere da ordem 1–8 da documentação, o que sugere enum base 0 ou ausência de Fatal/Exit.
- O analisador deve detectar as janelas pelas linhas `Debug level changed`, e não supor nível fixo.
- O sensitive do Ont mostra elevações anteriores, fora do período coberto pelo normal. Janelas de INFO:
  - 2026-09-23, 15:21–16:07
  - 2026-09-23, 19:49–19:53
  - 2026-09-28, 16:14–16:20
  - 2026-10-08, 12:37–12:38

### Relação com MessageLogLevel

O conteúdo das mensagens (XML completo de request e reply) só vai para o sensitive quando o nível corrente é ≥ `MessageLogLevel` (Info). No nível DebugLog, cada adapter grava coisas diferentes:

- **Wfm sensitive:** grava, em DEBUGLOG, um resumo de 3 linhas por request. Exemplos: `Identifier/Availability/FcConnectionStatus`, `Crew/Assignments/Entity+Status`, `Vehicle/Crew`. Também grava o `Callback received` com `Status value` e os textos completos dos `Detected errors`. Nas janelas INFO grava ainda o XML completo.
- **Ont sensitive:** em DEBUGLOG grava quase nada. São motivos de falha e o auditing de 12 h (`Client for endpoint … didn't send request … for more than …`, 07h e 19h, correspondente a `AuditingPeriod=12:00:00`). O XML (`Message: <ChangedIncidents …>`, respostas, `Error: Sending … failed` com detalhe) só aparece nas janelas INFO.

A severidade real está no texto, não no campo de nível. Exemplos: `DEBUGLOG - ERROR - Processing …`, `DEBUGLOG - [Info] AlternateRadioID…`, `ALWAYS - (ERROR) …`. O analisador precisa classificar pelo template.

## 3. Catálogo de templates

Normalização usada: GUID→`<guid>`, números→`<n>`, `'…'`/`"…"`→`<s>`, `INC n`→`<inc>`. Colunas: **Reg.** = registros (cabeçalhos); **Lin.** = linhas físicas, incluindo as continuações. Classes: **LC** ciclo de vida/startup/conexão; **PI/PF** início/fim de processamento; **RR** request/response/callback; **VAL** validação; **ERR** erro/exceção/stack; **EST** estatística periódica; **OUT** outros.

### Wfm (os 24 primeiros cobrem 99,0% das linhas; a tabela inteira, 99,9%)

| ID | Template | Nível | Reg. | Lin. | Classe | Campos |
|---|---|---|---|---|---|---|
| WFM-001 | `New "<map>" map content:` + N× `\t<chave> - <gid>` | DEBUGLOG | 2.311 | 752.268 | OUT (dump de cache) | map:str (ex. `OMS_CREW_VEHICLE`); pares chave:str→gid:int |
| WFM-002 | `"<map>" map closed.` | DEBUGLOG | 4.621 | 4.621 | OUT | map |
| WFM-003 | `"<map>" map is now open.` | DEBUGLOG | 2.311 | 2.311 | OUT | map |
| WFM-010 | `<Op> request received. Request with ID "<guid>". Message content wr(itt\|itten\|ten) in sensitive log.` | DEBUGLOG | 22.098 | = | PI | op:enum (Changed crews 13.696; Changed crew assignments 5.169; Changed vehicles 2.573; Created crew assignments 404; Deleted crew assignments 256), request_id:guid. Nas três operações de assignments o texto sai com o erro de grafia `wirtten` |
| WFM-011 | `Created crew model request with ID "<guid>" received.` | DEBUGLOG | 4 | 4 | PI | request_id |
| WFM-012 | `<Op> request with ID "<guid>" successfully processed.` | DEBUGLOG | 21.918 | = | PF | op, request_id |
| WFM-013 | `Synchronous processing of ReceiveCrewModel request - started\|finished.` | DEBUGLOG | 8 | 8 | PI/PF | fase |
| WFM-014 | `Comparing received External System <X> with existing ADMS <Y>...` / `Entity comparing finished. Result: Insert operations: <n>, Update operations: <n>, Delete operations <n>. There're <n> identical entities…` / `Crew model successfully updated in ADMS.` | DEBUGLOG | 52 | 52 | OUT (CrewModel) | entidade, ins/upd/del/iguais:int |
| WFM-020 | `Connecting to CRSBusinessService.` | DEBUGLOG | 13.194 | = | LC | — |
| WFM-021 | `Successfully connected to CRSBusinessService.` | DEBUGLOG | 16.161 | = | LC | — |
| WFM-022 | `[ClientConnectionRouter] Add\|Remove connection for ClientId = <n>` | DEBUGLOG | 16.820 | = | LC | ação, client_id:int |
| WFM-030 | `Callback received:` + `<Tipo> GID: <gid>, Errors count: <n>. For Status value check sensitive log.` | DEBUGLOG (4 INFO) | 16.212 | 32.424 | RR (callback CRS/IMS) | tipo (`OMS_CREW_CHANGE_REQ`; INFO: `OMS_REQUEST … Status value: <s>`), gid:int, errors:int |
| WFM-040 | `[Info] AlternateRadioID value will be ignored for vehicle with MRID '<mrid>.` | DEBUGLOG | 6.412 | = | VAL (aviso) | mrid (sem aspa de fechamento e pode ter espaço; capturar com `MRID '(.*)\.$`) |
| WFM-041 | `[Info] Vehicle crew data will be ignored for vehicle with MRID '<mrid>.` | DEBUGLOG | 6.412 | = | VAL | mrid |
| WFM-050 | `Detected errors:` + 1–3× `<CódigoErro>` | DEBUGLOG | 989 | 2.246 | VAL/ERR | códigos:list[str] (ver §5) |
| WFM-051 | `A detailed description of these errors can be found in the adapters sensitive log.` | DEBUGLOG | 989 | = | VAL | — |
| WFM-052 | `[SystemEventSender] System event message: [AdapterWfm] [<Op>] Errors occurred while processing received message` | DEBUGLOG | 881 | = | ERR (evento) | op |
| WFM-060 | `Unable to execute <Método> at <Serviço>. Reason = <msg>` + `Object name: '<s>'.` | DEBUGLOG | 489 | 978 | ERR | método (OrderedChangeAssignmentStatuses, ApplyDelta, UpdateFieldCrewConnectionStatus, GetDescendentValues, GetExtentValues), serviço (CRSBusinessService, CRSFieldCrewService, CRSGDAService), razão |
| WFM-061 | `Non Fatal Exception <TipoExceção>: <msg>` + `Object name…` + `\tManaged Code Stack Trace:` + frames | DEBUGLOG | 489 | 2.932 | ERR | tipo_exceção, msg, stack |
| WFM-062 | `Creating new dal proxy.` / `New dal proxy created. ServiceName = <svc>, Alias = <alias>` | DEBUGLOG | 876 | = | LC (reconexão) | serviço, alias |
| WFM-063 | `(ERROR) [Communication2] - Service operation <op> threw unexpected exception.` | ALWAYS | 111 | 111 | ERR | op |
| WFM-064 | `(ERROR) [Communication2] - <TipoExceção>: ` + 15 linhas (stack + `Inner Exception`) | ALWAYS | 111 | 1.776 | ERR | tipo, inner, stack |
| WFM-070 | `Subscriber(<PubSub>) failed to check-in…` / `[ERROR] - Unable to initialize subscriber: …` / `Subscriber is disconnected from …` (cada um + 3 linhas `[System.ObjectDisposedException] …`) | DEBUGLOG | 330 | 1.320 | ERR (PubSub) | pubsub (`CrewManagementPubSub`), exceção |
| WFM-071 | `Subscriber manager (<P>) is connected…` / `Subscriber: <P> successfully reconnected.` / `…is reconnected to…` / `…is subscribed to…` | DEBUGLOG | 440 | = | LC | pubsub |
| WFM-072 | `Subscriber integrity update started\|finished.` | DEBUGLOG | 220 | = | LC | fase |
| WFM-073 | `DefaultCrewAssignmentStatus is updated to: "<status>".` | DEBUGLOG | 110 | = | LC | status |
| WFM-080 | `[CrsDataManager] ERROR - Delta has been forwarded to CRS, but callback is not received within <hh:mm:ss> interval` | DEBUGLOG | 175 | = | ERR (timeout) | timeout (= `WaitForCRSCallback` 00:00:35) |
| WFM-081 | `ERROR - Processing "<EventType>" message failed. ExceptionType: "<t>" Reason: <razão>. StackTrace: <frame>` + 2–3 frames | DEBUGLOG | 184 | 745 | ERR | event_type (ChangedCrewsEvent, ChangedVehiclesEvent, ChangedCrewAssignmentsEvent), exc_type, razão (timeout CRS de 35 s, ou `Callback answers are not received from CRS after 10 seconds…` = `WaitForCallback`) |
| WFM-090 | `[Communication2] - Client-side statistics:` / `[TOTAL][Client] Call count: <n>, Request bytes: <n> (avg. <x>), Response bytes: <n> (avg. <x>). Total operations: <n>` / `By call count:` / `By Request Message Size Total:` / `By Response Message Size Total:` (+ CSV `método,chamadas,req_bytes,resp_bytes`) / `Service-side statistics:` / `[TOTAL][Service] …` | DEBUGLOG | 340 | 2.992 | EST (a cada hora, em hh:53:55) | lado, call_count, bytes, médias, linhas CSV por método |
| WFM-099 | `Debug level changed from <Nível> (<n>) to <Nível> (<n>).` | ALWAYS | 4 | 4 | LC | de, para |
| WFM-100 | `Message with MessageID: '<guid>' will be logged in sensitive log.` | DEBUGLOG (só na janela INFO) | 50 | 50 | RR | message_id |

**Cauda do Wfm (~0,1% das linhas, quase toda INFO/VERBOSE da janela de 12:39–12:40):**

- **Communication2 (VERBOSE):** `TCP client connection id = <n> sending|receiving message msgId = <n>, msgType = Request|Reply, msgSize = <n>`, framing/socket, `Calling service operation <op> over service connection id = <n>`, `Compressing|Decompressing … for method <op>…`.
- **INFO:**
  - `Received valid request message according to XML schema definition.` / `Sending valid reply message…`
  - `Generating Delta...`, `<n> crews added to insert Delta operation.`, `[CrsDataManager] Applying delta[ completed].`
  - `[Crs|Ims]RequestManagerForProdRequests] …` (espera de N callbacks), `IMS|CRS sent callback … with result "<s>"`
  - `Content being sent to CRS|WMS|IMS:` + XML
  - `Publishing EventData:` (13 linhas, EventData do SystemEvent com `Details=` legível)
  - `ModeTracker`, `PSAttach`, `arb_pubSubLookup`, `[AdaptivePool …] [UsageMonitor]`
- **Raros em DEBUGLOG:** `Cannot find "<s>" string value in "<s>" string to long map…` (1, ChangedVehicles) e `[Communication2] - System.ServiceModel.CommunicationObjectAbortedException` + stack (1).

### Ont (os 25 primeiros cobrem 92%; com a cauda INFO/VERBOSE da janela, 99%)

| ID | Template | Nível | Reg./Lin. | Classe | Campos |
|---|---|---|---|---|---|
| ONT-001 | `[OntSubscriber] - Publications arrived to adapter. Number of publications is: <n>.` | DEBUGLOG | 4.674 | PI (gatilho PubSub) | n_publicações:int |
| ONT-002 | `[SendIncidents] - Incident resource with Mrid: '<inc>' and Cache operation '<CacheOp>' will be processed as '<MsgOp>' messages with MessageIds: '<guid>'` | DEBUGLOG | 2.850 | PI | mrid, cache_op (Add/Update), msg_op, message_id |
| ONT-003 | igual a ONT-002, sem `'<MsgOp>' ` e com `.` no fim | DEBUGLOG | 928 | PI | mrid, cache_op (Remove), message_id |
| ONT-004 | `[SendIncidents] - Incident resource with Mrid: '<inc>' and Cache operation '<op>' will not be processed. Reason: '<s>' incident was not sent for this entity.` | DEBUGLOG | 44 | VAL | mrid, cache_op |
| ONT-005 | `[SendIncidents\|SendIncidentTroubleTickets]MessageSerializer] Serialization in progress...` | DEBUGLOG | 4.805 | OUT | serviço |
| ONT-006 | `[SendIncidentTroubleTickets] - Incident resource with Mrid: '<inc>' will be processed.` | DEBUGLOG | 625 | PI | mrid |
| ONT-007 | `[Subscriber] - Received publication with cache operation '<op>' and (trigger property: '<p>'\|multiple trigger properties) is not of interest for process.` | DEBUGLOG | 2.073 | VAL (filtro) | cache_op, propriedade |
| ONT-008 | `[SendIncidentTroubleTickets] - None of Incident Resources related to received Trouble Ticket Resources are of interest…` / `[SendIncidents] - None of received Incident Resources are of interest…` | DEBUGLOG | 1.678 | VAL (filtro) | serviço |
| ONT-009 | `[Validation] The value of property "<p>" is not of interest. Supported values are: "<v>". Current OMS_INCIDENT will not be processed.` | DEBUGLOG | 604 | VAL | propriedade, valores |
| ONT-010 | `[SendIncidents] Sending following Incident: <inc>.` | DEBUGLOG | 3.778 | RR | mrid |
| ONT-011 | `Sending "<MsgType>" message: <guid> with verb: <Verb>...` | DEBUGLOG | 3.806 | RR (request) | msg_type (`IncidentsEventMessageType`), message_id, verb (Changed 2.872 / Deleted 532 / Created 402) |
| ONT-012 | `Sending "<MsgType>" message: <guid> with verb: <Verb> finished successfully.` | DEBUGLOG | 3.771 | RR (response) | idem |
| ONT-013 | `[SendIncidents] Sending Incident finished successfully` | DEBUGLOG | 3.771 | PF | — |
| ONT-014 | `Request message with MessageId '<guid>' successfully sent. Reply result '<r>' is returned in response message.` | DEBUGLOG | 4.396 | PF/RR | message_id, reply_result (100% `OK`) |
| ONT-015 | `[SendIncidentTroubleTicketsClient] Sending following trouble ticket(s) for incident: <inc>.` | DEBUGLOG | 625 | RR | mrid |
| ONT-016 | `Sending "IncidentTroubleTicketsEventMessageType" message: <guid>...` / `… finished successfully.` (sem verb) | DEBUGLOG | 1.250 | RR | message_id |
| ONT-017 | `[SendIncidentTroubleTicketsClient] Sending trouble ticket(s) for incident finished successfully` | DEBUGLOG | 625 | PF | — |
| ONT-020 | `Error: Sending "<MsgType>" message failed. "<TipoExceção>" has occurred. More details are possible in sensitive log on info level.` | DEBUGLOG | 35 | ERR (tentativa) | msg_type, exceção (EndpointNotFoundException 33, TimeoutException 1, MessageNotValidException 1) |
| ONT-021 | `[SendIncidents] Sending Incident finished unsuccessfully. (Reason: <r>. StackTrace: + 11–12 linhas \| For more details check sensitive log.)` | DEBUGLOG | 7 | PF/ERR | razão, stack |
| ONT-022 | `Request message with MessageId '<guid>' unsuccessfully sent. (Reason: '<r>'. \| For more details check sensitive log.)` | DEBUGLOG | 7 | PF/ERR | message_id, razão |
| ONT-023 | `[Validation Critical] - Invalid reply message received. <razão>` | DEBUGLOG | 1 | VAL/ERR | razão (fault SAP XI fora do XSD) |
| ONT-030 | Estatísticas `[Communication2]`, iguais a WFM-090 | DEBUGLOG | 112 / 704 | EST (a cada hora, em hh:53:51) | idem |
| ONT-031 | `[CacheHousekeeper] Removing messages older then <ts> started\|finished successfully.` | DEBUGLOG | 2 | LC (manutenção diária) | corte:datetime (= agora − `TimeToKeepMessagesInCache`) |
| ONT-099 | `Debug level changed from … to ….` | ALWAYS | 4 | LC | de, para |
| ONT-100 | `Message with MessageID: '<guid>' will be logged in sensitive log.` | DEBUGLOG (janela INFO) | 14 | RR | message_id |

**Cauda do Ont (só na janela INFO/VERBOSE):**

- `[Subscriber] - Publication info:` + `Sub-process/CacheOperation/ChangedProperty/Entity identifiers/DmsType`
- `[CachedDataSynchronizer] - Saving|Dequeuing|Updating|Deleting '<MsgType>' message with GID: '<gid>', Identifier: '<inc>' and MessageId: '<guid>' …` (fila do smart cache, com status NotSent→Sent)
- `Replicator: SendReplica…`, `Cache[AdapterOntStorageSmartCache] …`
- `[SendIncidents] - Processing of <n> Incident Resources started.`
- Validação XSD de request/reply, proxies, `Filtering data…`, `Collection is (not) empty.`
- Communication2 TCP/ShMem e `OnPublicationMessages`, `ModeTracker`, `PSAttach`, `arb_pubSubLookup`

## 4. Correlação

### Wfm: inbound, servidor SOAP

- **Chave:** `request_id` (GUID) de WFM-010 → WFM-012. Esse GUID é o `MessageID` do header da mensagem recebida, e nos requests observados `CorrelationID = MessageID`. Na resposta (`CrewsResponseMessage` etc., visível no sensitive em INFO), o `MessageID` é novo e o `CorrelationID` é o request_id.
- **Thread:** o fim fica sempre na mesma thread do início (21.918/21.918). As linhas sem ID (Connecting, Detected errors, SystemEvent, `ERROR - Processing…`, `Unable to execute…`) pertencem à requisição aberta na mesma TID. Há 3.120 TIDs distintas (pool WCF).
- **Callbacks:** WFM-030 chega por outras threads, só 3 TIDs de longa vida (0x4404, 0x500c, 0x6b4c). A ligação é por `GID` (OMS_CREW_CHANGE_REQ), que não aparece na linha de início. Regra adotada (issue #65) na subseção seguinte.

#### Callback ↔ requisição de origem (issue #65)

Medido por script nas três capturas locais (ERS de 10-08 12:40, de 10-08 14:53 e de 10-09 14:50; só contagens):

| Medida | 10-08 12:40 | 10-08 14:53 | 10-09 14:50 |
|---|---|---|---|
| WFM-030 (todos com GID; nenhum GID repetido) | 16.212 | 19.098 | 6.626 |
| WFM-030 em INFO | 4 | 407 | 97 |
| WFM-030 cujo GID aparece num WFM-123/124 | 4 | 406 | 97 |
| … com todos os WFM-123/124 do GID numa única requisição (mesma TID, entre WFM-010 e o fim) | 4 | 406 | 97 |
| GID em mais de uma requisição | 0 | 0 | 0 |
| Callback antes do pedido | 0 | 0 | 0 |
| Registros DEBUGLOG (normal ou sensitive) que trazem o GID de um callback, fora o próprio WFM-030/S030 | 0 | 0 | 0 |

- Δt entre o último WFM-123/124 e o WFM-030 (507 casos): p50 ≈ 2,0 s, p99 3,5 s, máximo 13,1 s.
- Requisições de origem: só operações de assignments (Changed 423, Created 74, Deleted 10, somando as três capturas, que se sobrepõem no tempo).
- O WFM-126 (`CRS sent callback …`) sai na mesma TID do WFM-030 e, em 2 de 406 casos, **depois** dele; a regra `WFM-126 → WFM-030` (só "b depois de a") perde esses 2.
- WFM-S030: dentro da cobertura do log normal, 100% têm o par WFM-030 (mesmo GID, mesma TID, Δt ≤ 1 s). Fora dela não há WFM-123/124 em nenhum arquivo, e o sensitive não traz outra linha com o GID.
- A antiga regra por janela de tempo (WFM-010 → WFM-030, 35 s, confiança 0, só com uma requisição aberta) ligava 360, 361 e 158 callbacks (≈ 2%) e deixava ~98% como ambíguos. Das ligações feitas, 878 de 879 iam para `Changed crews`, `Changed vehicles` ou `Created crew model`, que nunca aparecem como origem verificável; só 1 caso pôde ser conferido pelo GID.

**Regra (implementada em `derive/links.py`, `origin = "operation"`; `awfm.toml`):** liga o registro de início da operação (WFM-010/011) ao WFM-030 quando (1) algum WFM-123/124A/124B do mesmo processo tem o GID do callback; (2) **todos** esses registros pertencem à mesma operação (`entry.event_id` de um evento `operation` do stream); (3) o último deles fica no máximo 60 s antes do callback. Confiança 2 (chave idêntica + pertença à operação pela TID). Sem GID no pedido (DebugLog), GID em mais de uma operação (ou em registro fora de operação), callback antes do pedido ou depois da janela: **sem ligação**, contado por motivo (`unmatched`, `ambiguous`, `no_operation`, `out_of_window`) em `meta['correlate.links.<grupo>']` (`link_stats`). No pipeline real: 4, 406 e 97 ligados; 0 ambíguos, 0 fora de operação, 0 fora da janela; os demais (16.208, 18.692, 6.529) sem GID no pedido.

**Escopo e o que ficou de fora:**
- Fora das janelas Info (DebugLog, ~98% dos callbacks nas amostras) **não existe ligação segura**: nenhum registro do pedido traz o GID. A heurística por janela de tempo foi removida do catálogo (não a funcionalidade `window_only` do motor), porque as medições mostram que ela erra o tipo de requisição em quase todos os casos.
- O WFM-S030 não recebe ligação própria: chega à requisição pelo par normal↔sensitive com o WFM-030.
- As ligações por GID já existentes (WFM-123/124 → WFM-126 → WFM-030) continuam; a nova não depende do WFM-126.
- **Requisição sem fim = falha.** As 184 não fechadas (143 Changed crews, 32 Changed vehicles, 9 Changed crew assignments) batem uma a uma com WFM-081 na mesma TID.
- **Duração (WFM-010→012):**

| Operação | n | p50 | p90 | p99 | máx |
|---|---|---|---|---|---|
| Changed crews | 13.553 | 1,88 s | 3,04 | 3,50 | 7,22 |
| Changed crew assignments | 5.160 | 1,80 | 2,86 | 3,42 | 3,89 |
| Changed vehicles | 2.541 | 1,97 | 3,00 | 3,53 | 3,66 |
| Created crew assignments | 404 | 2,02 | 2,98 | 3,49 | 7,80 |
| Deleted crew assignments | 256 | 0,02 | 2,39 | 3,36 | 4,05 |
| Created crew model | 4 | 6,05 | — | — | 7,28 |

  Falhas por timeout levam 35 s (`WaitForCRSCallback`) ou 10 s (`WaitForCallback`).

- **Normal ↔ sensitive:** o mesmo `request_id` aparece no sensitive como `<Op> request received. Request with ID "<guid>" content:` + resumo, com o **mesmo timestamp** (100% com Δ = 0,0 s) e a mesma TID (22.064/22.073). Todos os 22.073 GUIDs do sensitive no período também estão no normal. Ficam só no normal os últimos ~2 min, porque o sensitive termina antes (12:41:26). `Detected errors` aparece nos dois: no normal com os códigos, no sensitive com o texto detalhado.

### Ont: outbound, cliente SOAP disparado por PubSub

- **Cadeia:**
  1. ONT-001, na thread do subscriber (pool, 1.434 TIDs).
  2. ONT-002/003, que anuncia o `MessageId` e o mrid.
  3. Fila interna.
  4. ONT-010 → ONT-011 → ONT-012 → ONT-013 → ONT-014, numa das 24 threads de envio.
- **Chave:** `MessageId` (GUID). No XML de saída, `MessageID = CorrelationID` (681/681 no sensitive). O mrid do incidente (`INC n`) liga ONT-002, ONT-010 e ONT-015.
- Para Trouble Tickets, o GUID só aparece no ONT-016, e a ligação com o incidente é pelo mrid do ONT-015 anterior na mesma TID.
- ONT-011 e ONT-012 ficam sempre na mesma TID (4.396/4.396).
- **Durações:**
  - Envio (ONT-011→012), Incidents: p50 0,20 s, p90 0,24, p99 0,75, máx 29 s.
  - Envio, TroubleTickets: p50 0,17 s, máx 10 s.
  - Fila (ONT-002→ONT-011): p50 0,07 s, p90 0,28 s, mas p99 1.135 s e máx 1.519 s. A cauda é a indisponibilidade de 10:40–11:07, quando os envios ficaram serializados numa thread fazendo retry.
- **Normal ↔ sensitive:** só nas janelas INFO, pelo `MessageID` dentro do XML (`Message: <ChangedIncidents …>`). O sensitive grava com atraso de 0–0,2 s e em outra TID. Fora das janelas INFO, o sensitive do Ont tem apenas os motivos de falha (`Request message with MessageId '<guid>' unsuccessfully sent. Reason: '<r>'`).

## 5. Erros e exceções

### Formatos

1. **Códigos de validação de negócio (Wfm):** WFM-050 lista os IDs da coluna **ID/Bloco** do vault (`Catálogo de códigos de erro.md`, seção WFM) e do `<Adms><Id>` dos `WfmAdapter_<Op>_ErrorConfiguration.xml`. O texto detalhado (CIM `Details`, com `@IDs`/`@AttributeValue` preenchidos) vai só para o sensitive.

   | Código | Ocorr. | Vault | Severidade ADMS (config local) |
   |---|---|---|---|
   | AssignmentDoesNotExist | 501 | sim (2.7 FATAL) | Error |
   | InvalidStatusTransition | 362 | sim (2.7 FATAL, `InvalidAssignmentStatusTransition`) | Error |
   | AssignmentStatusShouldRemainUnchanged | 239 | **não** (customização Custom) | Info |
   | AssignmentTimestampIsInFuture | 122 | **não** | Info (Changed) / Warning (Created) |
   | StaleAssignmentTimestamp | 15 | **não** | Info |
   | AssignmentsRejectedInDmz | 6 | sim | Error |

   O WFM-052 (SystemEvent) é emitido **somente** quando algum código tem severidade ≥ `ErrorSeverityEventingThreshold` (Warning). Isso foi verificado em todos os casos: 881 com evento; 108 sem evento, todos só com códigos Info. Mesmo com erros, a requisição termina com `successfully processed`.
2. **Exceções de processamento (Wfm):** WFM-081 traz `EventType`, `ExceptionType` e a razão na mesma linha, e os frames `   at …` nas continuações. Não há código de catálogo. Supõe-se que a resposta use `InternalServerError` 5.3; confirmar no sensitive em INFO.
3. **Exceções de infraestrutura (Wfm):** cadeia recorrente, 110–111 vezes:
   1. `Unable to execute ApplyDelta… disposed object`
   2. `Non Fatal Exception System.ObjectDisposedException` + `Managed Code Stack Trace`
   3. `Creating new dal proxy`
   4. `ALWAYS (ERROR) [Communication2] … CRSBusinessContractResults threw unexpected exception` + stack com `Inner Exception`
   5. `Subscriber(CrewManagementPubSub) failed to check-in` (×3)
   6. reconexão
   7. `Subscriber integrity update started`
   8. dump de mapas (WFM-001/002/003)
   9. `integrity update finished`

   Trata-se de perda ou recriação da conexão com DMS_RealTime (CRS), que dispara a re-sincronização. Uma ocorrência por episódio, e é a principal fonte de volume do log.
4. **Falhas de envio (Ont):** ONT-020 a cada tentativa (`NumOfRetries=5`: exatamente 5 ONT-020 por mensagem perdida, 35 = 7 × 5, com intervalos de ~42–47 s). Em seguida vêm ONT-021 e ONT-022. As exceções são tipos .NET (EndpointNotFoundException, TimeoutException após `SendTimeout` 00:01:00, MessageNotValidException quando o SAP PI devolve fault `xi2.0:SystemError` fora do XSD). O AONT é cliente e não tem tabela no catálogo de códigos de erro. O resultado de negócio é o `Reply result` (ONT-014), e só `OK` foi observado.
5. **Stack trace:** frames `   at <método> in F:\PD\<build>\…\<arquivo>.cs:line <n>`. O número do build (`F:\PD\8308066\1\…`) identifica a versão do binário. `Managed Code Stack Trace:` vem precedido de TAB. `Exception rethrown at [<n>]:` e `Server stack trace:` separam seções.

## 6. Diferenças Wfm × Ont e perguntas em aberto

| Aspecto | Wfm | Ont |
|---|---|---|
| Papel | Servidor SOAP (recebe da WFM/SGE) e grava no CRS/IMS | Cliente SOAP (envia ao SAP PI), disparado por PubSub |
| Volume | 913 mil linhas em 34 h; 82% é dump de mapa | 44 mil linhas em 16 h; ~1 linha por mensagem de cabeçalho |
| Chave de correlação | request_id (= MessageID de entrada), na mesma TID | MessageId gerado pelo adapter, entre threads (fila) |
| Sensitive em DebugLog | Resumo de toda request | Quase vazio (só falhas e auditing) |
| ALWAYS fora de toggle | Sim (erros Communication2) | Não |
| Erros de negócio | Códigos de catálogo (`Detected errors`) | Não há, só exceções de transporte |
| Ortografia | `wirtten`; MRID sem aspa de fechamento | `older then`, `succesfull` (VERBOSE) |

### Perguntas em aberto

1. Por que o log normal começa em 10-07 (02:30 no Wfm, 20:30 no Ont) e não no start do processo? Pode ser rollover por tamanho ou corte pelo ERS. Há arquivos `_N.log` anteriores fora do ERS?
2. Numeração `DebugLog (3) … Verbose (6)`: confirmar o enum completo (onde ficam Fatal/Exit/Error/Always) e se o `toggleDebug` sempre dá a volta para DebugLog.
3. O efeito de `ModelUpdatersLogLevel=Info` (Wfm) não é visível. Ele controla os dumps de mapa (WFM-001) ou os `[Info] AlternateRadioID…`?
4. ~~Como ligar com segurança o `Callback received … GID` (WFM-030) à requisição de origem sem o XML de INFO?~~ Respondida (issue #65, seção 4): só com nível ≥ Info, pelo GID de WFM-123/124 dentro da requisição; em DebugLog não há chave e nada é ligado.
5. A linha sem `[TID=…]` (`PSAttach`) é um formato do logger nativo (C++)? Pode aparecer em outros adapters com mais frequência?
6. Em falha (WFM-081), qual resposta foi devolvida ao sistema externo (código CIM)? Só dá para confirmar com o sensitive em INFO durante uma falha.
7. O sensitive retém muito mais história (desde 08-28 no Ont, 10-05 no Wfm) que o normal: a rotação é por tamanho, por arquivo? Confirmar a política.

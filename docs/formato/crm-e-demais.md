# Formato de log: Crm, Ami, Smr, WDI, ClaimCheck, FileUpload, FileMonitor e BLTHost

Base: ERS de 2026-10-08, zona `dnaApp_mpdmz/DMS_INTEGRATION` (servidor HOT DSPMPDMZINT01). Contagens vêm de varredura completa dos arquivos. Exemplos anonimizados: `<inc>`, `<call>`, `<msgid>`, `<gid>`, `<ticket>`, `<arquivo>`, `<usuario>`; GUIDs encurtados.

| Arquivo | Início | Fim | Linhas | Cabeçalhos | Continuações |
|---|---|---|---|---|---|
| CrmAdapter_24608.log | 2026-10-06 12:30:09 | 2026-10-08 12:42:15 | 175.723 | 166.602 | 9.121 |
| CrmAdapter_24608.sensitive.log | 2026-08-28 07:56:11 | 2026-10-08 12:40:45 | 481.889 | 240.708 | 241.181 |
| AmiAdapter_42752.log | 2026-08-28 07:56:11 | 2026-10-08 11:53:41 | 53.253 | 8.524 | 44.729 |
| AmiAdapter_42752.sensitive.log | 2026-08-28 07:56:11 | 2026-10-08 07:53:55 | 391 | 385 | 6 |
| SmrAdapter_34872.log | 2026-08-28 07:56:11 | 2026-10-08 11:53:49 | 84.920 | 25.522 | 59.398 |
| SmrAdapter_34872.sensitive.log | 2026-08-28 07:56:11 | 2026-10-08 07:53:55 | 449 | 398 | 51 |
| WDIAdapter_28856.log | 2026-08-28 07:56:11 | 2026-10-08 11:53:47 | 19.991 | 5.163 | 14.828 |
| WDIAdapter_28856.sensitive.log | 2026-08-28 07:56:17 | 2026-10-08 07:53:54 | 169 | 169 | 0 |
| ClaimCheckAdapter_21736.log | 2026-08-28 07:56:11 | 2026-10-08 12:28:33 | 148.097 | 126.107 | 21.990 |
| ClaimCheckAdapter_21736.sensitive.log | 2026-08-28 07:56:17 | 2026-10-08 12:28:33 | 41.856 | 41.856 | 0 |
| FileUploadAdapter_16400.log | 2026-08-28 07:56:11 | 2026-10-08 11:53:48 | 17.088 | 14.134 | 2.954 |
| FileUploadAdapter_16400.sensitive.log | 2026-08-28 07:56:17 | 2026-10-08 11:16:27 | 3.743 | 3.743 | 0 |
| FileMonitorAdapter_16028.log (sem sensitive) | 2026-08-28 07:56:11 | 2026-10-08 11:54:01 | 9.036 | 1.116 | 7.920 |
| BLTHost.DMS_INTEGRATION.log | 2025-10-13 08:47:50 | 2026-08-28 14:24:02 | 8.165 | 4.982 | 3.183 |
| BLTHost.DMS_INTEGRATION.sensitive.log | 2025-11-20 09:24:06 | 2026-10-07 09:28:39 | 2.238.318 | 97.362 | 2.140.956 |

Todos os adapters, exceto Crm, cobrem o processo inteiro (start em 2026-08-28 07:56:11, após restart do serviço). O Crm normal começa no meio do fluxo, em 2026-10-06; o ERS não trouxe `.log.0` dele (ver Perguntas em aberto).

Configuração confirmada: `LogLevel=DebugLog` no Registry de todos (`AdapterX.xml`; `_2` e `_1_Custom` não sobrescrevem) e `MessageLogLevel=Info` em todas as operações, inclusive nos `WebServiceConfiguration` de `DMSCustomIntegrationPROD`, que são os que os adapters de fato leem (`[Configuration] [PathToWebServiceConfiguration]`). Por isso **nenhum conteúdo de request/response aparece**: só marcadores de processamento.

## 1. Gramática e teste da regex

### Cabeçalho

```regex
^(?:\ufeff)?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \[PID=(?P<pid>\d+)\]\[TID=0x(?P<tid>[0-9a-f]+)\] (?P<level>FATAL|EXIT|ERROR|ALWAYS|DEBUGLOG|WARNING|INFO|VERBOSE) - (?P<msg>.*)$
```

### Continuação

Qualquer linha que não casa com o cabeçalho pertence à mensagem anterior, até o próximo cabeçalho ou EOF. Sem terminador, sem escape.

### Resultado (3.283.088 linhas, 15 arquivos)

- 100% das linhas classificadas: casam com o cabeçalho ou são continuação. **0 órfãs** (todo arquivo começa em cabeçalho, logo após o BOM). **0 linhas** que começam com `AAAA-MM-DD` e não casam. Logo, nenhuma linha "não casa" por defeito; as que não casam são continuações legítimas.
- BOM só na linha 1. CRLF em 100% das linhas, inclusive a última. UTF-8 válido em tudo (há `â`, `ã`, `’` tipográfico no BLTHost).
- Níveis vistos: só `DEBUGLOG` nos adapters (normal e sensitive). `ALWAYS` só no BLTHost (42 linhas `[CommandLineConfig]`). **Erros reais saem como DEBUGLOG**; a severidade precisa ser inferida do texto (seção 4).
- TID em hex minúsculo sem zeros à esquerda (`0x2a04`, `0x170`). Neste conjunto o TID nunca falta (ver `wfm-ont.md`: no Ont há uma linha sem TID; vale deixar `[TID=…]` opcional na regex).
- `msg` pode terminar com espaço (`... failed. `, `By call count: `, `status: 'True' `).

Formas de continuação encontradas (normal):

| Forma | Onde | Exemplo |
|---|---|---|
| Texto sem indentação | códigos de erro após `Detected errors:`; CSV das estatísticas; `Reason:`/`Stack trace:` do Crm; tabelas do BLTHost; versão da assembly | `NoCallsForIncident` |
| Linha vazia | fecha cada seção de estatística e cada bloco do BLTHost | |
| TAB | dump de mapa (`\t<gid> - NOME`), `\tManaged Code Stack Trace:`, `\tInner Exception …`, settings do BLTHost | `\tInstanceContextMode = PerSession` |
| 3 espaços | stack trace .NET | `   at Renci.SshNet.Session.Connect()` |
| espaço + `{caminho:linha}` | fim de bloco multilinha do BLTHost | ` {E:\Builds\…\Main.cs:373}` |

**Armadilha:** a mensagem do cabeçalho pode começar com TAB (`DEBUGLOG - \t\t[Validation Info]: HashMissing` no ClaimCheck, `DEBUGLOG - \t"Key: …" entry added to the "WOM_STATE" map.` no Smr, `DEBUGLOG - \t\tReceiveMeterReadings properties` no Ami). TAB depois de ` - ` é texto da mensagem, não continuação.

Maior mensagem multilinha: 36 linhas (SocketException no ClaimCheck), 22 (estatística do Crm), 24 (IsReadyForModeSwitch no BLTHost sensitive).

## 2. Catálogo de templates

Os templates foram implementados como regex sobre `msg` do cabeçalho; as continuações herdam o template do cabeçalho. Aplicado a todos os arquivos: **cobertura de 100% das linhas em todos**, normal e sensitive, sem cauda. Os percentuais abaixo são de linhas (cabeçalho + continuações).

Classes: `startup` (ciclo de vida/startup), `início`/`fim` (de processamento), `req/resp`, `validação`, `erro` (erro/exceção/stack trace), `estatística`, `outros`.

### 2.1 Templates genéricos (GEN), comuns a vários adapters

| ID | Template (regex resumida) | Classe | Campos | Continuação |
|---|---|---|---|---|
| GEN-STARTUP | bloco de startup (2.2) | startup | | sim |
| GEN-STA-01 | `[Communication<n>] - (Client\|Service)-side statistics:` | estatística | lado | não |
| GEN-STA-02 | `[Communication<n>] - [TOTAL][(Client\|Service)] Call count: <n>, Request bytes: <n> (avg. <x>), Response bytes: <n> (avg. <x>). Total operations: <n>` | estatística | lado, calls, req_bytes, resp_bytes, médias, ops | não |
| GEN-STA-03 | `[Communication<n>] - By (call count\|Request Message Size Total\|Response Message Size Total): ` | estatística | ordenação | CSV `Contrato.Método,calls,req,resp` + linha vazia |
| GEN-CON-01 | `Connecting to <Serviço>.` | outros | serviço | não |
| GEN-CON-02 | `Successfully connected to <Serviço>.` | outros | serviço | não |
| GEN-CON-03 | `[ClientConnectionRouter] (Add\|Remove) connection for ClientId = <n>` | outros | client_id | não |
| GEN-ERR-01 | `Detected errors:` | validação | códigos (normal) ou detalhes (sensitive) | 1 linha por erro |
| GEN-ERR-02 | `A detailed description of these errors can be found in the adapters sensitive log.` | validação | | não |
| GEN-EVT-01 | `[SystemEventSender(WithQueuing)?] System event message: [Adapter<X>] (\[<Operação>\] )?<texto>` | erro (às vezes sucesso) | adapter, operação, texto | não |
| GEN-EXC-01 | `Non Fatal Exception <Tipo>: <mensagem>` | erro | tipo, mensagem, inner exception | `\tManaged Code Stack Trace:`, `   at …`, `\tInner Exception …` |
| GEN-AUD-01 | `Endpoint <Nome> on <url> was not invoked more than <hh:mm:ss>` (sensitive) | outros | endpoint, url, período | não |
| GEN-AUD-02 | `Client for endpoint <Nome> didn't send request to <url> for more than <hh:mm:ss>.` (sensitive) | outros | endpoint, url, período | não |
| GEN-SUB-01 | `Subscriber(: \| is \| integrity \| manager)…` (pub/sub) | startup | publisher | não |
| GEN-MAP-01 | `New "<mapa>" map content:` / `"<mapa>" map is now open\|closed\|cleared.` / `Adding the key …` / `\t"Key: …" entry added to the "<mapa>" map.` | startup | mapa | TAB `<gid> - NOME` |
| GEN-NET-01 | `[Communication<n>] - Finished terminating <guid> connection.` / `Started socket cleanup worker.` | outros | | não |

Bloco de estatística (unidade): começa em GEN-STA-01 `Client-side` (se houver) e vai até o fim da última seção GEN-STA-03 do lado `Service`. Client-side tem as 3 seções (call count, request size, response size); Service-side só `By call count`. Seção vazia não é escrita; sem nenhuma chamada desde o start, o bloco inteiro não aparece. Contadores são **acumulados desde o start do processo**, não do período. O cabeçalho CSV vem como `[methodname, …]` na primeira seção e `[method name, …]` nas demais.

```
2026-08-28 08:56:17.006 [PID=28856][TID=0xad8] DEBUGLOG - [Communication2] - Client-side statistics:
2026-08-28 08:56:17.006 [PID=28856][TID=0xad8] DEBUGLOG - [Communication2] - [TOTAL][Client] Call count: 3, Request bytes: 294 (avg. 98), Response bytes: 28553 (avg. 9517.667). Total operations: 3
2026-08-28 08:56:17.006 [PID=28856][TID=0xad8] DEBUGLOG - [Communication2] - By call count: 
[methodname, call count, request total size, response total size]
TelventDMS.Common.ServiceContracts.ILocalContextQueryContract.GetContextProcessPorts,1,74,72
TelventDMS.Common.ServiceContracts.ILocalContextConnectionContract.Connect,1,144,28390

2026-08-28 08:56:17.006 [PID=28856][TID=0xad8] DEBUGLOG - [Communication2] - By Request Message Size Total: 
[method name, call count, request total size, response total size]
...
```

### 2.2 Bloco de startup (unidade a colapsar)

Detecção: o arquivo começa com `[Configuration] …` (normal) ou `[ServiceUrl] : For …` (sensitive). O bloco vai da linha 1 até 5 s depois de `[SystemStatusPublisher] - Server state changed. New state: HOT` (ou 120 s após a primeira linha, se não houver HOT). Duração real: ~20 s.

| Adapter | Linhas do bloco | Linha do HOT |
|---|---|---|
| Ami | 509 (0,96%) | 350 |
| Smr | 693 (0,82%) | 249 |
| WDI | 231 (1,16%) | 218 |
| ClaimCheck | 145 (0,10%) | 136 |
| FileUpload | 252 (1,47%) | 171 |
| FileMonitor | 144 (1,59%) | 123 |

Conteúdo, em ordem: `[Configuration] [Param]: [valor]. Type: [ConfigurationRegistry]` (com pares `[Configuration][LoadingError] [Param] … does not exist` + `[Configuration] [Default] [Param]: [valor]`, ou seja, default carregado, não é erro); nos adapters SDK, `"<nome>" assemblies information:` + continuação com a versão e `Starting Adapter<X>...`; `<Adapter<X>> Initialize startup manager on port <n>`; arbitragem/HealthMonitor; `List of properties in web service configuration file:` + propriedades por operação (`[AuditingPeriod] : 12:00:00`, `[MessageLogLevel] : Info`, …); conexões LocalContext; licença; mapas e subscribers (Ami, Smr); `<Adapter<X>> Services started.`; `<Adapter<X>> Waiting for terminate event.`; ~10–14 s depois, `Server state changed. New state: HOT` e, nos adapters SDK, `Adapter (…BusinessAdapter|CommunicationAdapter) state changed to hot.`

Os dumps de mapa (`New "<mapa>" map content:` + linhas TAB) **só existem no startup** e são pequenos: Ami 11 dumps (79 linhas), Smr 4 dumps (100 linhas, maior com 70). Para colapsar: um evento `startup` com início, HOT, duração, porta, portas/URLs dos serviços e lista de `[Configuration]`.

### 2.3 Crm (CrmAdapter_24608)

Normal, 2 dias (100% coberto):

| ID | Template | Classe | Registros | % linhas | Campos |
|---|---|---|---|---|---|
| CRM-001 | `[GetTroubleTickets started] - Processing message: Message Id: "<msgid>" Context: "<ctx>" Search filter: "<filtro>"` | início | 37.504 | 21,34 | msgid (vazio, numérico ou GUID), ctx (`All`), filtro (`INC <n>`, `CALL <n>`, `<n>`) |
| CRM-002 | `[GetTroubleTickets] - Returning <n> trouble ticket(s) for context "<ctx>" and search filter: <filtro>` | req/resp | 37.503 | 21,34 | n, ctx, filtro |
| CRM-003 | `[GetTroubleTickets finished] - Message with message ID <msgid> processed.` | fim | 37.503 | 21,34 | msgid sem aspas, vazio gera 2 espaços |
| CRM-004 | `[GetTroubleTickets failed] - Processing message with messageId: "<msgid>" failed. ` | erro | 1 | 0,01 | + `Reason: …`, `Stack trace: …`, `   at …` |
| CRM-010 | `[CreatedTroubleTickets started] - Processing message with Message ID "<msgid>". Writing message data to sensitive log.` | início | 3.437 | 1,96 | msgid |
| CRM-011 | `[OMSBusinessManagerCallbackHandler] IMS created OMS_REQUEST entities: <gid>. Now adapter will wait (00:00:15) for IMS callback.` | req/resp | 3.437 | 1,96 | gid, timeout |
| CRM-012 | `IMS callback received for OMS_REQUEST GID: <gid> - Approved.` | req/resp | 3.437 | 1,96 | gid, resultado (outro TID) |
| CRM-013 | `SUCCESS: OMS_REQUEST entities (<gid>) are successfully processed by IMS.` | req/resp | 3.426 | 1,95 | gid |
| CRM-014 | `[OMSBusinessManagerCallbackHandler] WARNING - Call creation has been forwarded to IMS, but callback is not received within 00:00:15 interval` | erro | 11 | 0,01 | timeout |
| CRM-015 | `[CreatedTroubleTickets finished] - Message with message ID "<msgid>" processed.` | fim | 3.437 | 1,96 | msgid |
| GEN-ERR-01/02, GEN-EVT-01 | erros de validação (seção 4) | validação | 5.468 / 5.468 / 5.137 | 12,26 | códigos |
| GEN-CON-01..03 | conexão ao OMSBusinessService a cada CreatedTroubleTickets | outros | ~6.800 cada | 11,58 | ClientId |
| GEN-STA-* | estatística horária (48 blocos) | estatística | | 2,31 | |
| GEN-EXC-01 | SqlException (1) | erro | 1 | 0,01 | |

Invariantes: CRM-001 = CRM-003 + CRM-004 (37.504 = 37.503 + 1); CRM-010 = CRM-015. Sequência GetTroubleTickets é síncrona na mesma TID (started → [Detected errors] → Returning → finished). Em CreatedTroubleTickets o callback (CRM-012) chega em outra TID; correlacionar pelo GID do OMS_REQUEST.

```
2026-10-06 12:30:09.718 [PID=24608][TID=0x64f8] DEBUGLOG - [CreatedTroubleTickets started] - Processing message with Message ID "<msgid>". Writing message data to sensitive log.
2026-10-06 12:30:09.772 [PID=24608][TID=0x64f8] DEBUGLOG - [OMSBusinessManagerCallbackHandler] IMS created OMS_REQUEST entities: <gid>. Now adapter will wait (00:00:15) for IMS callback.
2026-10-06 12:30:11.060 [PID=24608][TID=0x5124] DEBUGLOG - IMS callback received for OMS_REQUEST GID: <gid> - Approved.
2026-10-06 12:30:11.060 [PID=24608][TID=0x64f8] DEBUGLOG - SUCCESS: OMS_REQUEST entities (<gid>) are successfully processed by IMS.
2026-10-06 12:30:11.186 [PID=24608][TID=0x64f8] DEBUGLOG - [CreatedTroubleTickets finished] - Message with message ID "<msgid>" processed.
```

Sensitive (100% coberto): `GEN-ERR-01` 137.847 registros (57%) com detalhe do erro na continuação; `CRM-S01` `[CreatedTroubleTickets started] - Processing message: Message ID: <msgid>` 102.681 registros (43%) com continuação `Customer trouble ticket: <ticket>` ou `Landbase trouble ticket: [Search type: BUSCAR MUNICIPIO, Parameter: <MUNICÍPIO> = "<endereço>"]`; `GEN-AUD-01` 164 (GetCallBacksService e ReceiveCallBacksResultsService ociosos, a cada 12 h); startup com `[ServiceUrl] : For <Op> service url is https://localhost:<porta>/<Op>Service.` e `Starting web service with url/s: …`.

### 2.4 Ami (AmiAdapter_42752)

Normal: **dominado pela estatística horária, não pelos mapas**. GEN-STA-* = 98,68% das linhas (988 blocos, seções de até 17 linhas); startup 0,96%; negócio 0,36%.

| ID | Template | Classe | Registros | Campos |
|---|---|---|---|---|
| AMI-001 | `[GetMeterReadingsBusinessLogicExt] - Processing MeterReadingRequest from received publication.` | início | 12 | |
| AMI-002 | `[GetMeterReadingsBusinessLogicExt] - Processing MeterReadingRequest finished.` | fim | 12 | |
| AMI-003 | `[ GetMeterReadingsRequest] Sending request message` / `Sending "GetMeterReadingsRequest" message: <msgid>.` / `… finished successfully.` / `[GetMeterReadingsRequest] Sending request finished successfully.` | req/resp | 68 | msgid (`<gid>-<n>`) |
| AMI-004 | `External system returned "<OK>" response for sent request. For more details please consult sensitive log.` | req/resp | 17 | resultado |
| AMI-005 | `[CreatedMeterReadings] Processing a message with a message ID: <msgid>. Writing message data to sensitive log.` | início | 23 | msgid |
| AMI-006 | `[CreatedMeterReadings] Message with message ID; <msgid> is processed.` | fim | 23 | msgid (note o `;`) |
| AMI-007 | `[ERROR] <BusinessLine> Meter reading failed data validation. Process will be aborted.` | validação | 6 | business line (`RequestTimeoutBusinessLine`) |
| GEN-ERR-01/02, GEN-EVT-01, GEN-CON-* | | | 6 / 6 / 6 / 3 | |

Fluxo: AMI-001 → AMI-003 (request ao ESB) → AMI-004 → AMI-002; resposta assíncrona chega como CreatedMeterReadings (AMI-005 → AMI-006), com o mesmo msgid `<gid>-0`. As contagens não fecham 1:1 (12 AMI-001 para 17 envios): um AMI-001 pode gerar vários envios seguidos na mesma TID, e há AMI-001/002 sem envio.

Sensitive: GEN-AUD-01/02 (324 de 391 linhas: endpoints ociosos a cada 12 h, inclusive a URL do ESB externo), `AMI-S01` `External system returned "<OK>" response for request "<msgid>".`, `AMI-S02` `[CreatedMeterReadings] Processing a message with a message ID: Message ID: <msgid>.`, detalhes de erro.

### 2.5 Smr (SmrAdapter_34872)

Normal: GEN-STA-* 81,4%; CreatedWorks ~17%; startup 0,82%.

| ID | Template | Classe | Registros | Campos |
|---|---|---|---|---|
| SMR-001 | `[CreatedWorks] - Processing message with messageId: "<msgid>" started.` | início | 1.497 | msgid |
| SMR-002 | `[CreatedWorks] - Getting Nms data for the following devices: <dispositivos>( succeeded)?.` | req/resp | 2.994 | dispositivos (`CS_…`, `TD_…`; pode vir vazio, 11 vezes) |
| SMR-003 | `[CreatedWorks] - Getting Wms data for the following works: <works>( succeeded)?.` | req/resp | 2.994 | works |
| SMR-004 | `[CreatedWorks] - Work (name, customId): (<nome>, WR <n>) successfully added to Adms.` | req/resp | 1.470 | nome, customId |
| SMR-005 | `[CreatedWorks] - Processing message with messageId: "<msgid>" successfully finished.` | fim | 1.497 | msgid ("successfully" mesmo com erro) |
| SMR-006 | `[CreatedWorks] -  Unable to create work request. WOM service returned some errors. For more details analyze work management service log.` | erro | 23 | (2 espaços após `-`) |
| SMR-007 | `[ERROR] - CreatedWorks request message with message ID "<msgid>" (contains invalid data\|is not valid). It will be discarded.` | validação | 4 | msgid |
| GEN-CON-* | `WMSDataManagementServiceAsync` a cada mensagem | outros | 1.498 cada | |

SMR-001 = SMR-005 = 1.497; SMR-004 (1.470) + SMR-006 (23) + SMR-007 (4) = 1.497. Sucesso = presença de SMR-004, não o "successfully finished".

Sensitive: GEN-AUD-01 (355 linhas, 5 endpoints), GEN-ERR-01 com detalhes, startup com URLs.

### 2.6 WDI (WDIAdapter_28856)

Ocioso no período: nenhuma mensagem de negócio. Normal = startup (231 linhas) + 988 blocos de estatística só Client-side (98,84%). Sensitive = startup (5) + GEN-AUD-01 (164: ReceiveActualWeatherData e ReceiveRegularForecastWeatherData não invocados por mais de 12:00:00).

### 2.7 ClaimCheck (ClaimCheckAdapter_21736)

| ID | Template | Classe | Registros | % linhas | Campos |
|---|---|---|---|---|---|
| CLC-001 | `"<Evento>" message processing started...` | início | 10.061 | 6,79 | evento (`CreatedFileNotificationEvent`) |
| CLC-002 | `[ClaimCheckAdapter] - RequestProcessor - Executing process line: (Multimedia\|Default)...` | outros | 10.061 | 6,79 | process line (Default = arquivos GIS) |
| CLC-003 | `File is successfully copied from remote file share to local file share. For more details check sensitive log.` | req/resp | 9.990 | 6,75 | |
| CLC-004 | `Obtaining Config from Multimedia Service for attachment validation.` | req/resp | 9.654 | 6,52 | |
| CLC-005 | `Number of retries left: <n>` | outros | 9.755 | 6,59 | n |
| CLC-006 | `Processing attachment on ADMS gid '<gid>' (started\|finished).` | req/resp | 19.304 | 13,03 | gid |
| CLC-007 | `\t\t[Validation (Info\|Warning\|Error\|Critical)]: <Código>` | validação | 31.863 | 21,51 | severidade, código |
| CLC-008 | `\t\t Details regarding validation errors can be found adapters sensistive log.` (sic) | validação | 10.046 | 6,78 | |
| CLC-009 | `"<Evento>" message processing finished successfully.` | fim | 10.046 | 6,78 | evento |
| CLC-010 | `"<Evento>" message processing failed. Reason:` | erro | 15 | 0,01 | seguido de GEN-EXC-01 |
| CLC-011 | `Uploading attachment failed due to <Erro> error.` | erro | 4 | 0,00 | `AnotherOperationRunning`, `InternalError` |
| GEN-EXC-01 | SocketException (SFTP), System.Exception, IOException | erro | 116 | 0,91 | |
| GEN-STA-* | 988 blocos | estatística | | 17,35 | |

CLC-001 = CLC-009 + CLC-010 (10.061 = 10.046 + 15). Mensagens processam em paralelo (207 TIDs); correlacionar por TID entre CLC-001 e CLC-009/010. As linhas de validação (CLC-007) são **cabeçalhos** com mensagem iniciada por 2 TABs.

```
2026-08-28 11:16:13.414 [PID=21736][TID=0x9e18] DEBUGLOG - "CreatedFileNotificationEvent" message processing started...
2026-08-28 11:16:13.414 [PID=21736][TID=0x9e18] DEBUGLOG - [ClaimCheckAdapter] - RequestProcessor - Executing process line: Multimedia...
2026-08-28 11:16:13.837 [PID=21736][TID=0x9e18] DEBUGLOG - Non Fatal Exception System.Net.Sockets.SocketException: An existing connection was forcibly closed by the remote host
	Managed Code Stack Trace:
   at Renci.SshNet.Abstractions.SocketAbstraction.Read(Socket socket, Byte[] buffer, Int32 offset, Int32 size, TimeSpan readTimeout)
   ...
2026-08-28 11:16:13.837 [PID=21736][TID=0x9e18] DEBUGLOG - Number of retries left: 3
2026-08-28 11:16:24.108 [PID=21736][TID=0x9e18] DEBUGLOG - File is successfully copied from remote file share to local file share. For more details check sensitive log.
2026-08-28 11:16:24.124 [PID=21736][TID=0x9e18] DEBUGLOG - Processing attachment on ADMS gid '<gid>' started.
2026-08-28 11:16:24.264 [PID=21736][TID=0x9e18] DEBUGLOG - Processing attachment on ADMS gid '<gid>' finished.
2026-08-28 11:16:24.264 [PID=21736][TID=0x9e18] DEBUGLOG - 		[Validation Info]: MessageRevisionMissing
...
2026-08-28 11:16:24.264 [PID=21736][TID=0x9e18] DEBUGLOG - "CreatedFileNotificationEvent" message processing finished successfully.
```

Sensitive: só cabeçalhos. `CLC-007` com o texto do detalhe (`[Validation Info]: Message revision is not provided.`) e `CLC-S01` `File: /PROD/<pasta>/<arquivo> - is copied to location: \\<share>\<pasta>\<arquivo>.` (9.990, 1:1 com CLC-003).

### 2.8 FileUpload (FileUploadAdapter_16400)

| ID | Template | Classe | Registros | Campos |
|---|---|---|---|---|
| FUP-001 | `FileUploadService FileUpload message processing started.` | início | 915 | |
| FUP-002 | `FileUploadService Received file - Name: "<arquivo>" Type: "<tipo>".` | req/resp | 915 | nome (`SP <n>_<Status>_<data>.csv`), tipo (`OutageReport`) |
| FUP-003 | `FileUploadService File content can be observed in sensitive log on verbose log level.` | outros | 915 | |
| FUP-004 | `Writing file to computer on a local path (started\|succeeded)... For computer name and local path details check sensitive log.` | req/resp | 1.830 | |
| FUP-005 | `Uploading file to remote location(. File details…\| succeeded. Details…)` | req/resp | 1.830 | |
| FUP-006 | `FileUploadService FileUpload message processing: File is uploaded to <SFTP>.` | req/resp | 915 | destino |
| FUP-007 | `File type: <tipo>: Sending Claim Check Message is DISABLED` | outros | 915 | |
| FUP-008 | `FileUploadService FileUpload message processing finished.` | fim | 915 | |
| GEN-EVT-01 | `[SystemEventSenderWithQueuing] System event message: [AdapterFileUpload] Uploading file to remote location succeeded. …` | outros (sucesso) | 915 | |
| FUP-009 | replicação/IU do SmartCache: `[RouteID:<n>, Dataset:<n>, Dataflow:<n>] RequestIU: …`, `(REPLICATION_INFO) Cache[AdapterFUStorageSmartCache] …`, `ReplicationProxyCallback: …` | outros | 848 (105 IUs) | route, dataset, dataflow, bytes |
| GEN-NET-01 | término de conexão | outros | 31 | |
| GEN-STA-* | 982 blocos, só Service-side | estatística | | |

Sensitive: `FUP-S01` `Writing file to computer <host> on local path: \\<share>\OutageReports\<arquivo> (started...|succeeded.)` e `Uploading file: <arquivo> to remote location: /PROD/OutageReports/( succeeded)?.` (915 de cada, 1:1 com o normal) e GEN-AUD-02 (NotificationService ocioso).

### 2.9 FileMonitor (FileMonitorAdapter_16028)

Normal = startup (144 linhas) + GEN-EXC-01 `CredentialNotFoundException` a cada hora (988 registros de 9 linhas = 98,41%). Nenhum processamento de arquivo e nenhuma estatística. Sem sensitive.

## 3. Ciclo de vida

### Fases e marcadores

| Fase | Início | Fim |
|---|---|---|
| startup | 1ª linha `[Configuration] …` (`Environment variable No_SqliteLog is set on 1.`) | `<Adapter<X>> Waiting for terminate event.` (fim síncrono, ~6–10 s) |
| ativação | `[SystemStatusPublisher] - Server state changed. New state: HOT` (~14 s após `Waiting…`) | `[SystemStatusPublisher] - Publishing notification … ServerChange event finished successfully.`; SDK: `Adapter (…CommunicationAdapter) state changed to hot.` |
| serving | negócio + estatística horária | (não observado) |
| shutdown | **não aparece em nenhum log de adapter** | |

Dois "sabores" de startup: Ami, Smr e Crm (componentes próprios: subscribers, mapas, `[ServiceUrl]` no sensitive) e os adapters SDK (WDI, ClaimCheck, FileUpload, FileMonitor: `"<nome>" assemblies information:`, `Starting Adapter<X>...`, `Adapter (TelventDMS…AdapterSDK…BusinessAdapter) state changed to standby/hot.`).

### Restarts e PID

- PID é constante dentro do arquivo e está no nome (`<Adapter>_<pid>.log`). Restart do processo gera **arquivo novo** com o novo PID; não há restart dentro de um arquivo.
- Quem registra paradas e restarts é o `ProcessMonitor.DMS_INTEGRATION.log`: `Non-Critical process CrmAdapter (<pid>) belonging to DMS_INTEGRATION service is dead.` e `RestartProcess: Process CrmAdapter (pid <pid>) … is being restarted using command: 'CrmAdapter.exe -serviceName DMS_INTEGRATION …'`. Mortes registradas (2025-11 a 2026-02): WDI 36, Crm 23, Ami 21, Smr 21, ClaimCheck 4.
- Stop/start do serviço inteiro está no `dnaService.DMS_INTEGRATION.log`. O start atual: `2026-08-28 07:55:19 Shutting down service` / `User commanded shutdown in progress` → `07:55:36 Starting service DMS_INTEGRATION` → `07:55:46 startup complete` → adapters em 07:56:11 → BLTHost em 07:56:16.

### Batimento esperado e gaps

| Adapter | Batimento | Gaps > 65 min no normal |
|---|---|---|
| Crm, Ami, Smr, WDI, ClaimCheck | estatística a cada 60 min, ancorada no start (+~6 s: 08:56:17, 09:56:16…) | 0 |
| FileUpload | estatística horária, mas só depois da 1ª chamada | 1 (07:57 → 14:42 de 2026-08-28: nenhuma chamada ainda, sem estatística; esperado) |
| FileMonitor | CredentialNotFoundException a cada 60 min (HOT + n·1 h) | 0 |
| sensitive (todos) | `Endpoint … was not invoked more than 12:00:00` a cada 12 h por endpoint ocioso (`AuditingPeriod`) | não aplicável |

Regra para o analisador: em adapter com estatística ativa, intervalo > ~61 min sem nenhuma linha indica processo travado, morto ou log perdido. Nenhum caso real foi encontrado neste ERS.

## 4. Erros, warnings e exceções reais

Tudo é DEBUGLOG. Marcadores de severidade no texto: `Detected errors:`, `[ERROR]`, `Non Fatal Exception`, `failed`, `WARNING`, `[Validation (Error|Critical)]`, `System event message: … ERROR` / `Errors occurred`. Falsos positivos de startup: `[Configuration][LoadingError]` (seguido de `[Default]`) e, no Ami, `[ERROR] - Validation error is detected during validation process of error configuration` + `\tWarning - Following error configurations are not used by any adapter validators: …` (aviso de configuração).

### Formato "Detected errors"

```
<cabeçalho> DEBUGLOG - Detected errors:
<CódigoAdms>                                  ← 1 por linha, normal
<cabeçalho> DEBUGLOG - A detailed description of these errors can be found in the adapters sensitive log.
<cabeçalho> DEBUGLOG - [SystemEventSender] System event message: [Adapter<X>] [<Operação>] Errors occurred while processing received message
```

No sensitive, o mesmo cabeçalho (mesmo ts e TID) traz o texto `Details` do ErrorConfiguration, com os placeholders preenchidos.

### Ocorrências

| Adapter | Código no normal | Ocorr. | Texto no sensitive | Catálogo |
|---|---|---|---|---|
| Crm GetTroubleTickets | NoCallsForIncident | 5.101 | `No trouble tickets found for provided search criteria: All - INC <inc>.` | vault: ID NoCallsForIncident, Warning, CIM 2.7 INFORM EntityNotFound |
| Crm GetTroubleTickets | NoCallsForSdp | 2 | idem, `Archived - <n>` | vault |
| Crm GetTroubleTickets | EntityNotFound | 9 | `Invalid entity mRID: INC <inc>.` | vault, Critical |
| Crm CreateTroubleTickets | CommentTooLong | 331 | `Comment exceeds limit for trouble ticket: <ticket>.` | vault, Info |
| Crm CreateTroubleTickets | CallerContactTypeMissing / CallerContactValueMissing | 14 / 14 | `Caller contact type/value is not provided for trouble ticket: <ticket>.` | vault, Error |
| Crm CreateTroubleTickets | ImsCallbackNotReceived (após CRM-014) | 11 | `Call creation has been forwarded to IMS, but callback is not received within 00:00:15 interval.` | **ausente do vault**; está em `CrmAdapter_CreateTroubleTickets_ErrorConfiguration.xml` (Warning, 5.3) |
| Crm sensitive | (ago–set, fora da janela do normal) | 10 | `Invalid trouble code: <texto> for trouble ticket: <ticket>.` | vault: InvalidEventReason |
| Smr CreatedWorks | InternalServerError + InvalidRequestedEquipment (após SMR-006) | 23 + 24 | `Unable to create work request. WOM service returned some errors` / `Invalid requested equipment: <dispositivo> for entities: <n>.` | vault |
| Smr CreatedWorks | DuplicateNames (após SMR-007) | 2 | `Work request with name: <n> already exist in ADMS.` | vault, Error |
| Smr CreatedWorks | PeriodEndTimeIsBeforeOrEqualToStartTime | 2 | `Period EndTime is before(or equal) to StartTime for entities: <n>` | vault, Error |
| Ami CreatedMeterReadings | NonExistingCorrelationId (após AMI-007) | 6 | `There are no active smart meter requests that correspond to provided CorrelationID: <gid>.` | vault (DMS Reason) |
| ClaimCheck | `[Validation Info]: MessageRevisionMissing` / `HeaderTimestampMissing` / `HashMissing` / `CommentsMissing` | 10.046 / 10.046 / 9.986 / 1.694 | `Message revision is not provided.` / `TimeStamp is not provided.` / `File notification hash is not provided.` / `… comments are not provided.` | XML local: INFORM, INFORM, WARNING, WARNING |
| ClaimCheck | `[Validation Critical]: InvalidUrl` / `NonExistingFile` | 58 / 2 | `File notification URL is invalid.` / `… does not exist on remote system.` | XML local: FATAL. No vault a 2ª é `RemotePathNotExist` |
| ClaimCheck | `[Validation Error]: InvalidObjectId` | 31 | `Provided object id: "<gid>" does not exist in ADMS.` | XML local: FATAL |

Regra de mapeamento: o normal grava o identificador ADMS (coluna **ID** nas tabelas de Crm/Smr do vault, **DMS Reason** nas de Ami/ClaimCheck) e o sensitive grava **Details**. Fonte de verdade é o ErrorConfiguration implantado (`ADMS/configuration/DMSIntegration/*ErrorConfiguration*.xml` e overrides em `DMSCustomIntegration/`), que diverge do vault em pelo menos `ImsCallbackNotReceived` e `NonExistingFile`. O prefixo `[Validation <sev>]` do ClaimCheck não segue a `<Severity>` do XML (WARNING sai como Info; FATAL sai como Error ou Critical).

### Exceções (GEN-EXC-01)

| Adapter | Tipo | Ocorr. | Contexto |
|---|---|---|---|
| FileMonitor | `TelventDMS…CredentialNotFoundException: Credential with with source application 'AdapterFileMonitor' and destination application 'SFTP' not found. Reason: The ADAMEntry 'CN=AdapterFileMonitor\|SFTP,OU=IntegrationCredentials,OU=OASySDNA' doesn't exist.` | 989 | Hora em hora desde o HOT. Stack em `CredentialProvider.GetCredential` → `CesDataManager.GetCredentialsPair`, inner `ADAMEntryDoesntExistException`. No startup: GEN-EVT-01 `ERROR: Credentials might not be provided…` e `LocalPath: "<UNC>" for FileType: "GIS" is invalid.` O adapter está configurado sem credencial e não monitora nada. |
| ClaimCheck | `System.Net.Sockets.SocketException: An existing connection was forcibly closed by the remote host` | 111 | Conexão SFTP (Renci.SshNet). Seguida de `Number of retries left: <n>`; com retries esgotados vira CLC-010 + GEN-EVT-01 `ERROR: Processing message from external system failed.` (15) |
| ClaimCheck | `System.Exception: Uploading attachment failed due to AnotherOperationRunning error.` / `InternalError` | 3 / 1 | upload ao Multimedia (MCS) |
| ClaimCheck | `System.IO.IOException: The process cannot access the file '<arquivo>' because it is being used by another process.` | 1 | |
| Crm | `System.Data.SqlClient.SqlException: Execution Timeout Expired…`, inner `Win32Exception: The wait operation timed out` | 1 | 2026-10-06 15:50:07, junto de CRM-004 (mesmo ts e TID) |

Formato do stack: cabeçalho `Non Fatal Exception <Tipo>: <msg>` + `\tManaged Code Stack Trace:` + linhas `   at <método> in F:\PD\<build>\…\<arquivo>.cs:line <n>` + opcional `\tInner Exception <Tipo>: <msg>` com `\t\t   at …`. O CRM-004 tem um formato próprio: continuação `Reason: <msg>` e `Stack trace:    at …` sem TAB.

Nenhum destes adapters aparece no `oasErrLog.log` do período (lá só Wfm e Ont).

## 5. BLTHost.DMS_INTEGRATION

O BLTHost **não carrega nem descarrega adapters**. É o host de BLTs (Business Logic Tasks) do serviço DMS_INTEGRATION, chamado pela infraestrutura de arbitragem/site switch. Os PIDs no arquivo são do próprio BLTHost (156 instâncias em 10 meses), não dos adapters. A relação com os adapters é temporal: o BLTHost sobe junto com cada start do serviço (ex.: PID 18856 em 2026-08-28 07:56:16, 5 s depois dos adapters).

BLTs carregadas (`\tManaged BLT: …`): `bobjManageRepRouteDMS_Integration.dll`, `bobjIsReadyForModeSwitchDMS_Integration.dll`, `bobjRemoteModeActivationDMS_Integration.dll`, `bobjIsActiveSiteDMS_Integration.dll`.

| ID | Template | Classe | Registros (normal) |
|---|---|---|---|
| BLT-001 | startup com sufixo `{<caminho do fonte>:<linha>}`: `===============================`, `Initializing...`, `Loading Components...`, `\tManaged BLT: <dll>`, `The initial/codified settings for BLTHost are:` (+ TAB), `BLTHost started on address 'net.tcp://0.0.0.0:<porta>/BLTService/*' ServiceEndpoint 'TCP'.` | startup | 2.652 (156 starts × 17) |
| BLT-002 | `[RTDBConfigAccessor] (storeandforward\|doas_system) record read: name=…` (topologia: sistemas mpcore/bpcore/mpdmz/bpdmz/mpstg/bpstg, zona, site) | startup | 1.344 |
| BLT-003 | inicialização da 1ª BLT: `[CommandLineConfig] - Start up script provided following parameters: [… BLTHost.exe -a DMS_INTEGRATION -s DMS_INTEGRATION]` (ALWAYS), `(ReplicationServiceProxy) Connecting to Replication service.`, pools, `DistribuSysAPI: …` | startup | 440 |
| BLT-004 | `[ParticipantsHandler] - Process with pid : <pid> and name : DMS_ReplicationService is not up and running and will be removed from participants table.` | outros | 154 |
| BLT-010 | `[IsReadyForModeSwitchProcessor] IsReadyForModeSwitch BLT execution started with input parameters: Originator system: '<sis>', Originator service: '<svc>', Skip unreachable nodes: '<bool>'. Local system: …` | início | 32 |
| BLT-011 | `[IsReadyForModeSwitchProcessor] IsReadyForModeSwitch BLT execution completed. Result: LocalAndDownstreamNodesReady status: '<True\|False>' ` + tabelas | fim | 32 |
| BLT-012 | `[ParticipationDelegate] Calling participate: ` + `Address: shmem://…/MSParticipation_DMS_ReplicationService_<pid>`, `ActionType: …`, `TransactionInfo:…` | req/resp | 100 |
| BLT-020 | `[RemoteModeActivationProcessor] RemoteModeActivation BLT execution started with the following parameters: …` | início | 46 |
| BLT-021 | `[RemoteModeActivation] ModeGroup/Mode: '<grupo>'/'<modo>' activated on …` / `Executing dms_savedata.csc script asynchronously.` / `Sync state check was skipped. …` | outros | 81 |
| BLT-022 | `[RemoteModeActivationProcessor] RemoteModeActivation BLT execution finished for ModeGroup/Mode : '<grupo>'/'<modo>'. Result: ModeActivation status: '<st>' ` + tabela | fim | 46 |
| BLT-023 | `[ModeSwitchTimeouts] Setting …` | outros | 55 |

Cobertura: 100% no normal e no sensitive.

### Blocos multilinha

1. **Settings com sufixo de fonte**: o sufixo ` {E:\Builds\<n>\OASyS\<branch>\src\DNA\ServiceFramework\source\BLTHost\Main.cs:<linha>}` fecha a mensagem. Em mensagem multilinha ele fica numa continuação própria, iniciada por espaço:

```
2025-10-13 08:47:55.639 [PID=<pid>][TID=0x2a04] DEBUGLOG - The codified settings for BLTHost are:
	InstanceContextMode = PerSession
	ConcurrencyMode = Multiple
	MaxConcurrentCalls = 384
	MaxConcurrentSessions = 2400
	MaxConcurrentInstances = 2784
 {E:\Builds\9\OASyS\…\BLTHost\Main.cs:373}
```

   Regex do sufixo: `\s?\{(?P<src>[A-Za-z]:\\[^{}]+\.(cs|cpp)):(?P<line>\d+)\}$`. Só as linhas de BLT-001 têm sufixo; o resto do BLTHost não. O mesmo sufixo aparece nos logs nativos do serviço (dnaService, ProcessMonitor, arb*).

2. **ResultSet do IsReadyForModeSwitch** (21–24 linhas): `ResultSet contains:` e tabelas delimitadas por linhas de `=` (`Node Status`, `Replication Status`, `ActiveMode`, `MSOperationProgress`, `Participation Status data table:`), cada linha `Data row: Chave: valor, Chave: valor, …`, terminando em linha vazia. Campos úteis: `ModeSwitchReadiness` (Ready/NotReady), `SyncState` (Synchronized/Stale), `Mode` (MainMode/BackupMode/SafeMode), `ProcessId` do DMS_ReplicationService.

3. **Mode Activation Status data table** no BLT-022 e `TransactionInfo:ModeGroup = DMSGroup, SourceMode = …, TargetMode = …` no BLT-012.

### Normal × sensitive

O sensitive tem 97.316 BLT-011 (2,24 M linhas, 100% do arquivo) contra 32 no normal: registra cada consulta IsReadyForModeSwitch, em rajadas a cada 2–5 s em dias específicos (ex.: 9.025 em 2026-06-25), sem o BLT-010 correspondente. 1.400 resultados `'False'` (`NotReady … SyncState is Stale`). Os 46 BLT-020 existem nos dois; o sensitive inclui o usuário (`User: <domínio>\<usuario>`, `Hashed user: <n>`). Os 32 BLT-011 do normal acompanham um BLT-010 (consulta originada por mpcore/bpcore em site switch).

## 6. Referência cruzada normal ↔ sensitive

Chave de junção: **mesmo PID, mesma TID e mesmo timestamp** (até ~20 ms de diferença), na ordem. Contagens 1:1 confirmadas na janela comum:

| Par | Normal | Sensitive | ts+TID exatos |
|---|---|---|---|
| Crm `Detected errors:` | 5.468 | 5.468 | 5.364 (98%) |
| Crm CRM-010 ↔ CRM-S01 | 3.437 | 3.436 | 3.377 |
| ClaimCheck `MessageRevisionMissing` ↔ `Message revision is not provided.` | 10.046 | 10.046 | 10.025 |
| ClaimCheck CLC-003 ↔ CLC-S01 | 9.990 | 9.990 | 9.920 |
| Smr `Detected errors:` | 26 | 27 | 25 |
| FileUpload FUP-005 succeeded ↔ FUP-S01 succeeded | 915 | 915 | 915 |
| Ami AMI-005 ↔ AMI-S02 | 23 | 23 | 21 |

O que só existe no sensitive: URLs de serviço (`[ServiceUrl] : For <Op> service url is …`), detalhe textual dos erros com IDs (ticket, INC, mRID, correlation ID), número do ticket e endereço/município do chamado (Crm), caminhos de origem e destino dos arquivos (ClaimCheck, FileUpload), host local, alarmes de ociosidade de 12 h, usuário do site switch (BLTHost). O normal remete a ele com frases fixas (`Writing message data to sensitive log`, `For more details check sensitive log`, `A detailed description of these errors can be found in the adapters sensitive log`).

O sensitive não tem estatística, startup detalhado nem stack trace. No Crm ele cobre período maior que o normal (desde 2026-08-28, contra 2026-10-06), mesmo sendo maior em bytes (40 MB contra 26 MB).

## 7. Envelope genérico × específico

Comum a todos (inclusive BLTHost e logs nativos do serviço dnaService, ProcessMonitor, dnaHealthMonitor, arbRx/arbTx/arbMonitor):

- Cabeçalho `ts [PID][TID] NÍVEL - msg`, BOM, CRLF, continuação sem prefixo.
- Nos adapters: bloco de startup, estatística horária `[Communication<n>]`, conexões (`Connecting to`/`Successfully connected to`/`ClientConnectionRouter`), `Detected errors:` + códigos, `[SystemEventSender] System event message: [Adapter<X>] …`, `Non Fatal Exception` + stack, alarmes de ociosidade no sensitive, `SystemStatusPublisher`/`HOT`.

Específico: os templates `[<Operação> started|finished|failed]` (Crm), `[CreatedWorks] - …` (Smr), `[CreatedMeterReadings]`/`[GetMeterReadings…]` (Ami), `"<Evento>" message processing …` + `[Validation <sev>]` (ClaimCheck), `FileUploadService …` + replicação SmartCache (FileUpload). Não há padrão único de início/fim entre adapters; o analisador precisa de regex por adapter para abrir e fechar transações.

Visão geral dos outros logs da pasta `Log/`: `dnaService.DMS_INTEGRATION.log` (start/stop do serviço, nível ALWAYS, sufixo `{arquivo.cpp:linha}`); `ProcessMonitor.DMS_INTEGRATION.log` (limites de recursos, morte e restart de processos com PID, ALWAYS/DEBUGLOG); `dnaHealthMonitor.DMS_INTEGRATION.log` (notificações de health state por processo, ex.: `…WfmAdapter_<pid>_<epoch>.healthstate`); `arbRx/arbTx/arbMonitor.exe.DMS_INTEGRATION.log` (transições FAIL/PENDING/INIT/HOT do serviço entre DSPMPDMZINT01/02 e erros de socket, ALWAYS/ERROR); `oasErrLog.log` (rotação `.0`–`.4`, **formato diferente**: `ts <Processo>_<pid> [PID=n][TID=0xN] - (NÍVEL) msg`, só ERROR e acima). Todos usam o mesmo envelope, exceto o oasErrLog.

## Perguntas em aberto

1. Por que o ERS não trouxe `CrmAdapter_24608.log.0`? O normal começa em 2026-10-06 12:30 no meio do fluxo, e o AVL tem `.log.0`. Limite de tamanho do arquivo e da rotação não confirmado.
2. Origem das rajadas de IsReadyForModeSwitch a cada 2–5 s no BLTHost sensitive (ArbitrationStateMonitor? NMC?) e por que só o sensitive as registra.
3. Regra do prefixo `[Validation Info|Error|Critical]` do ClaimCheck, que não bate com `<Severity>` do ErrorConfiguration.
4. Shutdown de adapter: nenhum log de adapter registra parada. Confirmar se há mensagem de terminate quando o serviço para (os arquivos anteriores ao restart de 2026-08-28 não vieram no ERS).
5. FileMonitor sem credencial `AdapterFileMonitor|SFTP` no AD LDS e com LocalPath GIS inválido: decisão de configuração ou falha? O adapter está inoperante desde o start.
6. `Number of retries left: 0` aparece também em fluxo sem erro (antes de `Processing attachment`); semântica exata do contador não confirmada.
7. Com `MessageLogLevel=Info` e `LogLevel=DebugLog`, payloads não são logados. Se o analisador precisar de payload, será preciso Info/Verbose (`toggleDebug`), com outro volume e possivelmente novos templates.

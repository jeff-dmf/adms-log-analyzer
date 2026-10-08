# Formato de log: AVL, Osr, Smn e SiteSwitch

Adapters de alto volume que só escrevem em DEBUGLOG. Base: ERS de `dnaApp_mpdmz/DMS_INTEGRATION` (out/2026).

| Arquivo | Linhas | Registros | Continuações | Janela |
|---|---|---|---|---|
| `AVLAdapter_31136.log.0` | 393.170 | 389.827 | 3.343 | 06/10 13:30:08 → 08/10 09:30:08 |
| `AVLAdapter_31136.log` | 66.341 | 66.224 | 117 | 08/10 09:30:08 → 12:42:25 |
| `OsrAdapter_34828.log` | 305.009 | 269.689 | 35.320 | 06/10 16:30:10 → 08/10 12:41:55 |
| `SmnAdapter_10484.log` | 175.141 | 161.239 | 13.902 | 05/10 11:30:09 → 08/10 12:43:14 |
| `SiteSwitchAdapter_22964.log` | 153.246 | 153.246 | 0 | 06/10 07:30:09 → 08/10 12:42:39 |
| `AVLAdapter_31136.sensitive.log` | 237.228 | 237.228 | 0 | 06/10 12:30:08 → 08/10 12:36:22 |
| `OsrAdapter_34828.sensitive.log` | 24.159 | 11.942 | 12.217 | 07/10 12:30:10 → 08/10 12:41:27 |
| `SmnAdapter_10484.sensitive.log` | 115.841 | 9.023 | 106.818 | 28/08 07:56:11 → 08/10 12:28:34 |

Configuração no ERS: `LogLevel=DebugLog` no Registry dos 4 adapters; `MessageLogLevel=Info` em todas as operações de AVL, Osr e Smn. Como Info é mais detalhado que DebugLog, **o conteúdo das mensagens (payload) não é logado**: nem o log normal nem o sensitive trazem XML de request/response.

## 1. Linha, continuação e registro

Codificação UTF-8 com BOM (só no início do arquivo), CRLF em 100% das linhas. Timestamps não decrescem dentro de cada arquivo.

```regex
^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \[PID=(?P<pid>\d+)\]\[TID=0x(?P<tid>[0-9a-f]+)\] (?P<level>[A-Z]+) - (?P<msg>.*)$
```

- Testada contra as 1.470.135 linhas dos 8 arquivos: **todas** são cabeçalho (casa a regex) ou continuação. Nenhuma linha começa com `AAAA-MM-DD ` sem casar a regex.
- Linhas que não casam (continuações), por tipo:

| Tipo | Exemplo | Onde |
|---|---|---|
| Código de erro solto | `EntityNotFoundForIncidentSdpMrId` | Osr, após `Detected errors:` |
| Stack trace .NET | `   at Namespace.Classe.Metodo(...) in F:\PD\...\Arquivo.cs:line 83` | Osr, Smn |
| Marcadores de exceção | `Server stack trace: `, `Exception rethrown at [0]: `, `--- End of inner exception stack trace ---`, `---> (Inner Exception #0) ...`, `ClientConnectionId:<guid>`, `Error Number:8632,State:8,Class:17<---`, `Reason: ...`, `Stack trace: ...` | Osr, Smn |
| Cabeçalho CSV de estatística | `[methodname, call count, ...]` / `[method name, call count, ...]` | AVL, Osr, Smn |
| Linha CSV | `Namespace.IContrato.Metodo,<n>,<n>,<n>` | AVL, Osr, Smn |
| Linha vazia | `` | fim de cada seção de estatística e de stack trace |
| Item de mapa (TAB) | `\t<chave> - <valor>` | AVL, Smn (`New "<mapa>" map content:`) |
| Resto da 1ª linha | `<Namespace>.IncidentHazardsPayloadType received from user WPA_XXXXX. Processing message...` | Osr (`[Created/ChangedIncidentHazards start]`) |

**Registro** = linha de cabeçalho + todas as linhas seguintes que não casam a regex, até o próximo cabeçalho.

Detalhes do cabeçalho:
- Nível: só `DEBUGLOG` nos 8 arquivos. A gravidade real vem do texto (`[Validation Critical]`, `[ERROR]`, `unsuccessfully`, `failed`, `Error:`).
- **TAB dentro de `msg` não é continuação.** No AVL, 50% dos cabeçalhos têm `msg` começando com `\t\t` (ex.: `\t\t[Validation Info]: ...`). É uma linha própria, com timestamp. Faça `strip()` antes de casar templates.
- Espaços nas pontas: SiteSwitch tem espaço inicial e final em toda mensagem (` Adapter is on ACTIVE site. `). Várias linhas de Osr/Smn terminam com espaço (`... failed. `, `StackTrace: `, `By call count: `).
- PID: um por arquivo, igual ao do nome do arquivo.
- TID: pool de threads com reuso (AVL 577 TIDs em 44 h; Osr 2.852; Smn 3.389; SiteSwitch 3.539). Use o TID para correlacionar só dentro de uma operação, nunca entre operações.
- Rotação: `.log.0` é o arquivo anterior e `.log` o atual. Um registro não é dividido entre os dois, mas uma operação pode ser: `started...` é a última linha do `.log.0` e o resto está no `.log`. Processe na ordem `.log.0`, `.log` como um fluxo único.

## 2. Catálogo de templates

Notação: `<guid>`, `<n>` (inteiro), `<f>` (decimal), `<gid>` (GID do DMS, inteiro de até 18 dígitos), `<inc>` (`INC <n>`), `<user>`, `<q>` (texto livre entre aspas). Quando o template tem TAB inicial, o texto aparece depois de `strip()`. Contagens são registros. Todos os níveis são DEBUGLOG.

### Comum (COM): estatística de comunicação

Aparece igual em AVL, Osr e Smn. O SiteSwitch não tem esse bloco. Formato completo na seção 4.

| ID | Template | AVL | Osr | Smn |
|---|---|---|---|---|
| COM-001 | `[Communication<k>] - Client-side statistics:` | 47 | 44 | 73 |
| COM-002 | `[Communication<k>] - [TOTAL][Client] Call count: <n>, Request bytes: <n> (avg. <f>), Response bytes: <n> (avg. <f>). Total operations: <n>` | 47 | 44 | 73 |
| COM-003 | `[Communication<k>] - By call count: ` + CSV | 94 | 88 | 146 |
| COM-004 | `[Communication<k>] - By Request Message Size Total: ` + CSV | 47 | 88 | 146 |
| COM-005 | `[Communication<k>] - By Response Message Size Total: ` + CSV | 47 | 88 | 146 |
| COM-006 | `[Communication<k>] - Service-side statistics:` | 47 | 44 | 73 |
| COM-007 | `[Communication<k>] - [TOTAL][Service] Call count: <n>, Request bytes: <n> (avg. <f>), Response bytes: <n> (avg. <f>). Total operations: <n>` | 47 | 44 | 73 |

`<k>` foi sempre `2`. No AVL, o lado Service só traz "By call count" (1 operação); por isso COM-003 aparece em dobro.

Também se repetem entre adapters: `[SystemEventSender] System event message: [Adapter<Sigla>] ...` (evento de sistema, ver seção 5) e o trio `"<mapa>" map closed.` / `New "<mapa>" map content:` + linhas `\t<chave> - <valor>` / `"<mapa>" map is now open.`. O trio é o recarregamento de cache: `OMS_CREW_VEHICLE` no AVL, `OMS_CREW` no Smn, uma vez cada, às 03:00.

### AVL (AVLAdapter_31136, `.log.0` + `.log`: 456.051 registros)

| ID | Template (após strip) | Contagem | Classe | Campos |
|---|---|---|---|---|
| AVL-001 | `"ChangedVehiclesCoordinatesEvent": <guid> message processing started...` | 75.919 | início | evento, messageId |
| AVL-002 | `Position is updated for vehicle(s). For more details check sensitive log.` | 75.899 | processamento → sensitive | — |
| AVL-003 | `[Validation Info]: MessageRevisionMissing` (TAB TAB) | 75.919 | validação | código |
| AVL-004 | `[Validation Info]: HeaderTimestampMissing` (TAB TAB) | 75.919 | validação | código |
| AVL-005 | `Details regarding validation errors can be found adapters sensistive log.` (TAB TAB espaço; "sensistive" é literal) | 75.919 | validação → sensitive | — |
| AVL-006 | `"ChangedVehiclesCoordinatesEvent": <guid> message processing finished successfully.` | 75.919 | fim | evento, messageId |
| AVL-007 | `[Validation Critical]: VehicleNotFound` (TAB TAB) | 20 | validação (erro) | código |
| AVL-008 | `[SystemEventSender] System event message: [AdapterAVL] Received message is invalid` | 20 | erro | — |
| AVL-009 | `Checking quality of coordinates...` | 47 | tarefa periódica | — |
| AVL-010 | `Updating quality for <n> staled coordinate(s)...` | 44 | tarefa periódica | n |
| AVL-011 | `Updating quality for staled coordinate(s) successfully finished.` | 44 | tarefa periódica | — |
| AVL-012 | `There is no coordinates to be set on stale.` | 3 | tarefa periódica | — |
| AVL-013..015 | trio de mapa `OMS_CREW_VEHICLE` (1.627 itens `\t<placa ou nº> - <gid>`) | 1 cada | cache | mapa, itens |
| COM-* | estatística | 376 | estatística | ver seção 4 |

AVL-001 a AVL-006 somam **99,88%** dos registros.

Sequência normal de um evento, sempre na mesma thread:

```
"ChangedVehiclesCoordinatesEvent": bc2b…e1 message processing started...
Position is updated for vehicle(s). For more details check sensitive log.
		[Validation Info]: MessageRevisionMissing
		[Validation Info]: HeaderTimestampMissing
		 Details regarding validation errors can be found adapters sensistive log.
"ChangedVehiclesCoordinatesEvent": bc2b…e1 message processing finished successfully.
```

Com veículo desconhecido: não há AVL-002. Entram AVL-007 (depois de AVL-004) e AVL-008 (depois de AVL-005), e o fim continua `finished successfully`.

### Osr (OsrAdapter_34828: 269.689 registros)

| ID | Template | Contagem | Classe | Campos |
|---|---|---|---|---|
| OSR-001 | `[GetIncidents start] - Get incidents request message Message Id: "<q>" Context: "<ctx>" Search filter: "<q>" received from user <user>. Processing message...` | 53.940 | início | msgId (vazio, numérico de 16 dígitos ou guid), ctx, filtro, user (vazio) |
| OSR-002 | `[GetIncidents] - Returning <n> incident(s) for provided search criteria.` | 53.764 | resultado | n |
| OSR-003 | `[GetIncidents end] - Get incidents request message Message Id: "<q>" Context: "<ctx>" Search filter: "<q>" processed.` | 53.764 | fim ok | idem OSR-001 |
| OSR-004 | `[GetIncidents end] - ... failed. ` + `Reason: ...` + `Stack trace: System.AggregateException ...` + inner exceptions | 176 | fim com exceção | reason, exceção, SQL Error Number |
| OSR-005 | `Detected errors:` + 1 a 4 linhas, cada uma com um código | 20.214 | validação/erro | códigos |
| OSR-006 | `A detailed description of these errors can be found in the adapters sensitive log.` | 20.214 | → sensitive | — |
| OSR-007 | `[SystemEventSender] System event message: [AdapterOsr] [<Op>] Errors occurred while processing received message` | 19.003 | erro | operação |
| OSR-008 | `[ArchivedIncidentsDataReader] - Reading archived full set incidents data started. Number of incidents to fetch <n>.` | 7.339 | consulta BD | n (sempre 70000) |
| OSR-009 | `[ArchivedIncidentsDataReader] - Reading archived full set incidents data finished.` | 7.339 | consulta BD | — |
| OSR-010 | `[IncidentsDataReader] - Reading archived reduced set incidents data started.` | 176 | consulta BD | — |
| OSR-011 | `[GetIncidentUsagePoints start] - Get incidents usage points request message Message Id: "<q>" Context: "<Unrestored\|affected>" Search filter: "<inc>" received from user . Processing message...` | 4.286 | início | msgId, ctx, inc |
| OSR-012 | `[GetIncidentUsagePoints] - Returning <n> usage points(s) for incident "<inc>".` | 4.286 | resultado | n, inc |
| OSR-013 | `[GetIncidentUsagePoints end] - ... processed.` | 4.286 | fim | — |
| OSR-014 | `[ChangeIncidents start] - Change incidents request message <msgId> received from user <user>. Processing message...` | 4.111 | início | msgId (guid ou `UPDATE-FIELD-COMPLETE-INC<n>-<seq>`), user |
| OSR-015 | `Message validating changed incidents from request message for incident IDs "<inc>"...` | 4.111 | validação | inc |
| OSR-016 | `Data validating changed incidents from request message for incident IDs "<inc>"...` | 4.111 | validação | inc |
| OSR-017 | `[ChangeIncidents end] - Change incidents request message <msgId> processed.` | 4.111 | fim | msgId |
| OSR-018 | `[ChangedIncidentHazards start] - Changed incident hazards message: Message ID: <guid>` + continuação `<PayloadType> received from user <user>. Processing message...` | 617 | início | guid, user |
| OSR-019 | `[Validation message] - Value "IsHazard" is not provided and will be set to default value "false".` | 617 | validação | — |
| OSR-020 | `[Validation message] - Damage is not provided and will be set to default value "<q>".` | 400 | validação | default |
| OSR-021 | `[ChangedIncidentHazards end] -  Changed incident hazards message with ID "<guid>" processed.` (dois espaços depois de `-`) | 617 | fim | guid |
| OSR-022 | `[ERROR] - Data validation: Changed incident hazard request message with message ID "<guid>" is not valid. All received incident hazards will be discarded.` | 29 | erro | guid |
| OSR-023 | `[ImsDataManager]Applying delta result: "<res>".` | 588 | chamada IMS | res (`SUCCESSFULLY`) |
| OSR-024 | `IMS CloseIncidentDevices for incidentId: <gid> returned DMSError: <res>.` | 318 | chamada IMS | gid, res |
| OSR-025 | `IMS OpenIncidentDevices for incidentId: <gid> returned DMSError: <res>.` | 298 | chamada IMS | gid, res |
| OSR-026 | `[CreatedIncidentHazards start] - Created incident hazards message: Message ID: <guid>` + continuação como em OSR-018 | 89 | início | guid, user |
| OSR-027 | `Connecting to OMSBusinessService.` | 89 | conexão | — |
| OSR-028 | `[ClientConnectionRouter] Add connection for ClientId = <n>` | 89 | conexão | clientId |
| OSR-029 | `Successfully connected to OMSBusinessService.` | 89 | conexão | — |
| OSR-030 | `[CreatedIncidentHazards end] - Created incident hazards message "<guid>" processed.` | 89 | fim | guid |
| OSR-031 | `IMS callback received for OMS_REQUEST GID: <gid> - Approved.` | 89 | callback IMS | gid, status |
| COM-* | estatística | 440 | estatística | — |

OSR-001 a OSR-009 somam 87%; até OSR-021, 99,2%.

Valores de `Context` e o que cada um indica:

| Contexto | Filtro | n | Observação |
|---|---|---|---|
| `Active` | 15 dígitos (instalação/UsagePoint) | 19.324 | msgId numérico. 78% retornam 0 com `EntityNotFoundForIncidentSdpMrId` |
| `active` | `INC <n>` | 19.616 | msgId vazio. Rajadas com intervalo p50 de 0,1 s |
| `All` | `INC <n>` | 14.291 | cada uma dispara OSR-008/009 (lê 70.000 incidentes arquivados) |
| `active` | `Confirmed\| NotRestored`, `…PartiallyRestored`, `…Restored` | 177 cada | polling a cada 900 s |
| `archived` | vazio | 176 | polling a cada 900 s. **100% falham** (seção 5) |

### Smn (SmnAdapter_10484: 161.239 registros)

| ID | Template | Contagem | Classe | Campos |
|---|---|---|---|---|
| SMN-001 | `[BusinessLogicResolver] - Invoking <Created\|Changed\|Deleted> business logic for <n> entities.` | 30.159 | entrada (publicação) | verbo, n |
| SMN-002 | `[Subscriber] - Received publication with cache operation '<Add\|Remove>' and multiple trigger properties is not of interest for process.` | 11.642 | filtro | operação |
| SMN-003 | `[SendSwitchingStepsMessageSerializer\|SendWorksMessageSerializer\|SendSwitchingPlansMessageSerializer] New serialization in progress...` | 17.072 | preparação | serializer |
| SMN-004 | `[SendSwitchingSteps] Sending following Switching Step: SP <n> - ISS <n>.` | 12.720 | início | plano, passo |
| SMN-005 | `[SendWorks] Sending following Work Request: WR <n>.` | 2.277 | início | WR |
| SMN-006 | `[SendSwitchingPlans] Sending following Switching Plan: SP <n>.` | 2.075 | início | plano |
| SMN-007 | `Sending "<Tipo>EventMessageType" message: <guid> with verb: <Verbo>...` | 17.106 | request (uma por tentativa) | tipo, messageId, verbo |
| SMN-008 | `Sending "<Tipo>EventMessageType" message: <guid> with verb: <Verbo> finished successfully.` | 16.307 | response ok | idem |
| SMN-009 | `[SendSwitchingSteps] Sending Switching Step finished successfully` / `[SendWorks] Sending Work Request finished successfully` / `[SendSwitchingPlans] Sending Switching Plan finished successfully.` (só este tem ponto final) | 16.307 | fim ok | operação |
| SMN-010 | `Request message with MessageId '<guid>' successfully sent. Reply result 'OK' is returned in response message.` | 16.307 | resultado | messageId, result |
| SMN-011 | `START NmsSignalsBySignalGidsQuery` / `END NmsSignalsBySignalGidsQuery` | 5.204 cada | consulta | — |
| SMN-012 | `Switching step with custom id: '<inc> - ISS <n>' is not on work plan and will be ignored.` | 3.104 | filtro | id |
| SMN-013 | `[Validation] The value of property "<prop>" is not of interest. Supported values are: "<lista>". Current SM_INSTANT_SWITCHING_SEQUENCE will not be processed.` | 1.454 | filtro | propriedade |
| SMN-014 | `[BusinessLogicResolver] - There are no switching steps satisfying filter configuration in received publication.` | 1.387 | filtro | — |
| SMN-015 | `[Validation Critical] - Invalid reply message received. Could not find schema information for the element '<ns>:SystemError'.` | 757 | erro de response | elemento |
| SMN-016 | `[<SendOp>] Sending <Entidade> finished unsuccessfully. Reason: Written in sensitive log. StackTrace: ` + stack | 765 | fim com exceção | operação |
| SMN-017 | `Request message with MessageId '<guid>' unsuccessfully sent. Reason: '<texto>'.` | 759 | resultado erro | messageId, motivo |
| SMN-018 | `Request message with MessageId '<guid>' unsuccessfully sent. For more details check sensitive log.` | 6 | resultado erro | messageId |
| SMN-019 | `Error: Sending "<Tipo>" message failed. "<TipoExceção>" has occurred. Writing details in sensitive log.` | 40 | erro de tentativa (retry) | tipo, exceção |
| SMN-020..022 | trio de mapa `OMS_CREW` (1.646 itens) | 1 cada | cache | — |
| COM-* | estatística | 584 | estatística | — |

SMN-001 a SMN-014 somam 98,2%; com SMN-015 a SMN-019, 99,6%.

### SiteSwitch (SiteSwitchAdapter_22964: 153.246 registros)

| ID | Template (literal, com espaços nas pontas) | Contagem | Classe |
|---|---|---|---|
| SSS-001 | ` Adapter is on ACTIVE site. ` | 76.623 | request (IsActive / HealtCheckActive) |
| SSS-002 | ` Adapter responded with HTTP OK 200 response ` | 76.623 | response |

Os dois cobrem 100%. Não há bloco de estatística, startup nem erro. Pela documentação (ASSS), o site backup responde 503, mas esse texto não aparece nesta amostra.

## 3. Ciclo de vida das mensagens

Regra geral: início e fim saem **na mesma thread**, e o par fecha antes de a thread pegar outro trabalho. Pareie por `(chave da operação, TID)`; quando houver messageId, confira por ele. Nos 4 adapters não houve fim sem início nem início aberto no fim do arquivo (contando `.log.0` + `.log` no AVL).

### AVL

- Par: AVL-001 ↔ AVL-006 pelo `<guid>` (único por evento). 75.919/75.919 pareados, sempre na mesma TID.
- Duração: p50 15 ms, p90 16 ms, p99 32 ms, máx. 110 ms. A resolução do relógio é de ~15,6 ms (timer do Windows), então 0 ms e 15 ms são o mesmo intervalo.
- Falha: não existe fim de falha. O fim é sempre `finished successfully.`, mesmo quando a mensagem foi rejeitada (`VehicleNotFound`). Para detectar falha, procure AVL-007/AVL-008 entre o início e o fim, ou a ausência de AVL-002.
- Cadência: processamento serial. Intervalo entre inícios: p50 46 ms, p90 62 ms, máx. 865 s (madrugada).
- Volume por hora: 230–420 de madrugada (00–06h), ~3.300–3.650 das 08 às 16h, queda depois das 17h. Pico de 3.643/h às 11h de 08/10. Sem anomalias além do padrão diurno.
- `VehicleNotFound` ocorreu 20 vezes, a cada ~6 min (13:30 → 15:24 de 06/10), sempre do mesmo veículo. Parece um rastreador enviando posição de um veículo que não está no cadastro. Parou sozinho.
- Tarefa de qualidade (AVL-009..012): de hora em hora, no minuto :54:11 (`CoordinatesQualityTriggerPeriod=01:00:00`). Marca como "Local indisponível" as coordenadas sem atualização há mais de 2 h (`StaledCoordinatesQualityPeriod`). Gera 2 a 7 coordenadas por execução.

### Osr

- Par: `[<Op> start]` ↔ `[<Op> end]` por `(Op, TID)`. A chave da mensagem (Message Id/Context/Search filter, ou messageId/guid) se repete no fim e serve para conferir. Na amostra não há início sem fim nem sobreposição na mesma TID.
- Resultado: o fim diz `processed.` (concluído, mesmo com erro de negócio) ou `failed. ` + exceção. Erro de negócio = OSR-005 + OSR-007 entre o início e o fim.

| Operação | n | p50 | p90 | p99 | máx. |
|---|---|---|---|---|---|
| GetIncidents (processed) | 53.764 | 3 ms | 43 ms | 73 ms | 26,1 s |
| GetIncidents (failed, archived) | 176 | 24,2 s | 31,0 s | 31,7 s | 32,0 s |
| GetIncidentUsagePoints | 4.286 | 0 | 16 ms | 31 ms | 126 ms |
| ChangeIncidents | 4.111 | 1,83 s | 3,00 s | 3,55 s | 11,7 s |
| ChangedIncidentHazards | 617 | 1,91 s | 2,93 s | 3,46 s | 6,5 s |
| CreatedIncidentHazards | 89 | 16 ms | 32 ms | 63 ms | 63 ms |

- Concorrência: várias threads em paralelo; as linhas de operações diferentes se intercalam.
- Volume (inícios por hora): 280–700 de madrugada, 1.600–2.850 das 09 às 20h. Pico de 2.854 às 16h de 07/10 (GetIncidents 2.487).
- Pollers periódicos de 900 s: `active` NotRestored/PartiallyRestored/Restored e `archived`, sempre perto de :00/:15/:30/:45 (+10 s a +40 s).

### Smn

Fluxo de saída (cliente SOAP para o SAP PI), tudo na mesma TID:

```
[SendSwitchingSteps] Sending following Switching Step: SP 2780xxxxx - ISS 2780xxxxx.     (SMN-004..006: início)
Sending "SwitchingStepsEventMessageType" message: 7c18…45 with verb: Changed...           (SMN-007: tentativa)
[Error: Sending "…" message failed. "EndpointNotFoundException" … ]                         (SMN-019: tentativa falhou, retry)
Sending "SwitchingStepsEventMessageType" message: 7c18…45 with verb: Changed finished successfully.
[SendSwitchingSteps] Sending Switching Step finished successfully                          (SMN-009 / SMN-016: fim)
Request message with MessageId '7c18…45' successfully sent. Reply result 'OK' …            (SMN-010 / 017 / 018: resultado)
```

- Par: início `[SendX] Sending following` ↔ fim `[SendX] … finished (un)successfully` por `(SendX, TID)`. O messageId (guid) liga SMN-007, SMN-008 e SMN-010/017/018. Em retentativas o guid se repete em SMN-007.
- Antes do início, em outra thread (despachante), aparecem SMN-001 e SMN-003 (publicação recebida e serialização). Não há chave explícita entre essa etapa e o envio.

| Operação | Sucesso: n / p50 / p99 / máx. | Falha: n / p50 / máx. |
|---|---|---|
| SendSwitchingSteps | 12.708 / 141 ms / 267 ms / 8,8 s | 12 / 60,0 s / 280,8 s |
| SendSwitchingPlans | 2.073 / 172 ms / 376 ms / 47,1 s | 2 / 277 s |
| SendWorks | 1.526 / 169 ms / 469 ms / 130,4 s | 751 / 32 ms / 267 ms |

- Retentativa (`RetryEngine`): com `EndpointNotFoundException`, novas tentativas a cada ~47 s (uma vez, ~5 s). Após 6 tentativas, desiste: duração ~277–281 s.
- Timeout: `The request channel timed out while waiting for a reply after 00:01:00` dá duração de 60 s.
- Volume: bem irregular (0 a 925 envios por hora), com horas vazias de madrugada. Picos às 16h de 05/10 (671), 16h de 07/10 (880), 09h e 11h de 08/10 (865, 925).

### SiteSwitch

- Cada consulta são 2 linhas no mesmo ms e na mesma TID (SSS-001 + SSS-002). Duração ≈ 0.
- Exatamente 1.440 consultas por hora: duas fontes, cada uma a cada 5 s, defasadas (intervalos alternam ~2 s e ~3 s; nunca passam de 4,02 s). Provavelmente health check de balanceador de carga.
- Sinais de problema: intervalo > ~5 s sem consulta (nenhum na amostra) ou qualquer linha diferente de SSS-001/002 (por ex., site inativo/503).

## 4. Estatísticas periódicas (COM-*)

Um bloco por hora, emitido de uma vez (todas as linhas com o mesmo timestamp, em uma thread qualquer):

```
<ts> … - [Communication2] - Client-side statistics:
<ts> … - [Communication2] - [TOTAL][Client] Call count: 11462391, Request bytes: 1115498604 (avg. 97.318), Response bytes: 81685306501 (avg. 7126.376). Total operations: 10
<ts> … - [Communication2] - By call count: 
[methodname, call count, request total size, response total size]
TelventDMS.Common.ServiceContracts.IGDAServiceQueryContract.GetExtentValues,2494278,356696826,194553684
…
(linha vazia)
<ts> … - [Communication2] - By Request Message Size Total: 
[method name, call count, request total size, response total size]
…
(linha vazia)
<ts> … - [Communication2] - By Response Message Size Total: 
…
<ts> … - [Communication2] - Service-side statistics:
<ts> … - [Communication2] - [TOTAL][Service] Call count: 21, Request bytes: 39548 (avg. 1883.238), Response bytes: 504 (avg. 24). Total operations: 1
(mesmas 3 seções "By …")
```

- Periodicidade: 3.599,85 s. O bloco adianta ~150 ms por hora (AVL :53:54 → :53:47 em 44 h; Osr :53:56; Smn :54:00). Use o próprio timestamp; não assuma hora cheia.
- Parse:
  - Totais: `\[TOTAL\]\[(Client|Service)\] Call count: (\d+), Request bytes: (\d+) \(avg\. ([\d.]+)\), Response bytes: (\d+) \(avg\. ([\d.]+)\)\. Total operations: (\d+)`. O `avg` pode vir inteiro (`avg. 24`).
  - Seções: o cabeçalho `By (call count|Request Message Size Total|Response Message Size Total):` vem seguido de uma linha de colunas (`methodname` na 1ª seção, `method name` nas outras), N linhas `metodo,calls,req_bytes,resp_bytes` (o nome do método não tem vírgula) e uma linha vazia.
  - O lado (Client/Service) é o último `X-side statistics:` visto antes da seção.
- **Contadores acumulados desde o start do processo**, não por intervalo: em 44–73 blocos nenhum total diminuiu. Taxa por hora = diferença entre blocos consecutivos. Uma queda indica restart do processo.
- Cada seção lista no máximo **20** métodos (Osr: `Total operations: 23`, mas 20 linhas), ordenados pela métrica da seção. Por isso o conjunto de métodos varia entre seções. Para série temporal por método, use a união das 3 seções e saiba que métodos pequenos podem faltar.
- Leitura: Client = chamadas do adapter a serviços do DMS (GDA, NetworkModel, OMSBusiness, CRS, PubSub). Service = chamadas recebidas (callbacks, publicações). Exemplos: AVL tem ~12 M chamadas client, com `IteratorNext` em 85 GB de response acumulado; Osr faz ~18–20 mil chamadas client por hora; Smn ~5 mil por hora.

## 5. Erros, warnings e exceções

Não existe nível ERROR/WARNING no arquivo. A gravidade sai do texto.

| Padrão | Adapter | Formato | Como detectar |
|---|---|---|---|
| Validação | AVL | `\t\t[Validation Info\|Critical]: <Código>` | código = Id da Error Configuration |
| Validação | Osr | `Detected errors:` + 1 código por linha de continuação | idem |
| Validação | Smn | `[Validation Critical] - …`, `[Validation] …` | texto |
| Evento de sistema | AVL, Osr | `[SystemEventSender] System event message: [Adapter<X>] [<Op>] <texto>` | marca a mensagem como erro de negócio (vai para o Event/Alarm do ADMS) |
| Exceção com fim de operação | Osr | `[<Op> end] - … failed. ` + `Reason:` + `Stack trace: <Exceção>: <msg> ---> <Inner>` + frames `   at …` + `---> (Inner Exception #i) …` + `ClientConnectionId:` + `Error Number:n,State:n,Class:n<---` | primeira linha termina em `failed.` |
| Exceção com fim de operação | Smn | `[<SendOp>] … finished unsuccessfully. Reason: Written in sensitive log. StackTrace: ` + `Server stack trace: ` + frames + vazio + `Exception rethrown at [0]: ` + frames | `unsuccessfully` |
| Exceção de tentativa | Smn | `Error: Sending "<Tipo>" message failed. "<Exceção>" has occurred. …` | não encerra a operação (há retry) |
| `[ERROR] - Data validation: …` | Osr | linha única | descarte da mensagem inteira |

Os frames trazem `in F:\PD\8308066\1\Product\…\Arquivo.cs:line N` (build da Schneider). Agrupe stack traces pela 1ª linha de exceção (tipo + mensagem), não pelos frames.

### Relação com o Catálogo de códigos de erro (vault)

Os códigos no log são o `Id`/`DMS Reason` da Error Configuration do vault (`Catálogos/Catálogo de códigos de erro.md`, seções AVL e OSR) e do XML instalado (`ADMS/configuration/DMSIntegration/*ErrorConfiguration*.xml`). O texto do sensitive é o `Details` do catálogo, com `@AttributeValue`/`@IDs` preenchidos.

| Código (log) | Operação | n | Catálogo: ADMS Sev. / Code / CIM Sev. | Texto no sensitive |
|---|---|---|---|---|
| MessageRevisionMissing | AVL | 75.919 | WARNING 1.2 | `Message revision is not provided.` |
| HeaderTimestampMissing | AVL | 75.919 | WARNING 1.2 | `TimeStamp is not provided.` |
| VehicleNotFound | AVL | 20 | FATAL 2.7 | `Invalid vehicle mRID(s): "<id>"` |
| EntityNotFoundForIncidentSdpMrId | GetIncidents | 15.072 | Warning / 2.7 / INFORM | `No incidents found for provided UsagePoint mRID: <id>.` |
| EntityNotFoundForIncidentMrid | GetIncidents (3.284), ChangeIncidents (68) | 3.352 | Error\|Critical / 2.7 / FATAL | `Invalid Incident Record mRID: <id>.` |
| NoIncidentsFoundForProvidedValidSearchCriteria | GetIncidents | 438 | Warning / 2.7 / INFORM | `No incidents found for provided search criteria.` |
| PSROperationAttempted | ChangeIncidents | 616 | Info / 0 / INFORM | `<op> request sent for incident: INC <n>.` |
| InvalidIncidentStatusTransaction | ChangeIncidents | 74 | Error / 5.3 / Fatal | `Incident INC <n> cannot be transferred to state: <estado>. Reason: IMS_APPLY_DELTA_*` |
| InvalidChangedIncidentState | ChangeIncidents | 25 | Error / 2.7 / Fatal | `Cannot update incident: INC <n>. Incident is in state <Arquivada\|Suspensa> …` |
| CommentPartiallyCreated / CommentExceedsLimit | ChangeIncidents | 11 / 11 | Warning 6.1 / Info 2.7 | `Comment partially created\|exceeds limit for entities: INC <n>.` |
| RemarkNotChanged | ChangeIncidents | 7 | **fora do vault**; só no XML (Info / 0.0 / INFORM, NoChange) | `Confirm attempted for incident … already in Confirm state.` |
| FieldCompleteTimeMustBeLaterThanEarliestCrewArrivalTime | ChangeIncidents | 1 | **fora do vault** | `Field completed time must be later than the earliest crew arrival time …` |
| InvalidCrewId | ChangeIncidents | 1 | Error / 1.2 / Fatal | — |
| IsHazardMissing / DamageMissing | ChangedIncidentHazards | 617 / 400 | Info / 1.2 / INFORM | `Missing IsHazard.` / `Missing Damage.` |
| InvalidEquipmentMrId | ChangedIncidentHazards | 80 | Ok / 2.7 / WARNING | `Invalid equipment mRID: <id> for entities: PROB <n>.` |
| InvalidProblemStatusTransaction | ChangedIncidentHazards | 29 | Critical / 6.1 / FATAL | `Status transaction is not allowed for entities: PROB <n>.` |
| InvalidCoordinateX/YFormatDevice | ChangedIncidentHazards | 20 / 20 | Info / 2.7 / WARNING | `Invalid PositionPoint X coordinate format "<v>" …` |
| LastModifiedDateTimeInvalid | ChangedIncidentHazards | 2 | Info / 2.7 / WARNING | `LastModifiedDateTime "<v>" is in future …` |

Observações:
- A severidade do catálogo não muda o nível da linha (sempre DEBUGLOG). Para classificar, o analisador precisa mapear `código → severidade` a partir da Error Configuration.
- Severidade Critical/Error/FATAL leva a `SystemEventSender` e, no ChangedIncidentHazards, a `[ERROR] … discarded`. Severidade Info/Warning só aparece em `Detected errors`.
- Smn e SiteSwitch não têm códigos de erro no catálogo: os erros do Smn são exceções WCF/HTTP.

### Padrões de falha reais

1. **Osr `GetIncidents` com Context `archived`: 176 de 176 falharam** (polling de 15 min). Causas: `SqlException` 8632 "An expression services limit has been reached" (154; State 8, Class 17) ou `Execution Timeout Expired` (22; Error Number -2). Duração de 24–32 s. Tudo em `IncidentsDataReader` → `ArchivedIncidentEntitiesDataReader.Read(List<incidentGids>)`, o que sugere uma query com lista IN gigante (70.000 incidentes arquivados).
2. **Osr `GetIncidents` `Active` (busca por instalação): 78% sem resultado** (`EntityNotFoundForIncidentSdpMrId`, 15.072). É funcional (consulta "há ocorrência para minha instalação?"), mas gera 15 mil eventos de sistema.
3. **Smn: o SAP PI responde com `SystemError` fora do schema.** 757 envios: `[Validation Critical] … schema information for the element '…/xi2.0:SystemError'` (o inspector WCF rejeita a resposta). **Todos** os `WorksEventMessageType` com verbo `Created` (467) e `Deleted` (283) falharam; `Changed` funciona (1.526 de 1.527). Steps e Plans só falham esporadicamente (rajada em 08/10 10:40).
4. **Smn: indisponibilidade do endpoint SAP PI** em 08/10 10:43–11:07. Sequência de `EndpointNotFoundException`, retry a cada ~47 s, 6 tentativas, `unsuccessfully sent. For more details check sensitive log.` Também houve timeout de 60 s em 07/10 08:35–08:36 e em 08/10 10:43.
5. **AVL: todo evento vem sem `MessageRevision` e sem `Header Timestamp`** (warning 1.2 em 100% das mensagens). É ruído estrutural do integrador AVL, não falha.
6. AVL `VehicleNotFound` (20 vezes, um único veículo, a cada 6 min, por ~2 h).

## 6. Log normal ↔ sensitive

O sensitive tem o **mesmo envelope** (mesma regex, BOM, CRLF, mesmo PID/TID) e só recebe os detalhes que o log normal manda "ver no sensitive". **Nenhum** dos dois traz messageId no lado sensitive, então o pareamento é por **TID + timestamp + ordem**.

| Adapter | Normal | Sensitive | Pareamento verificado |
|---|---|---|---|
| AVL | AVL-002 `Position is updated for vehicle(s). For more details check sensitive log.` | `Position is updated for the following vehicle(s): <ids>.` seguido de `\t\t[Validation Info]: Message revision is not provided.` / `TimeStamp is not provided.` | 75.540/75.540 na janela comum: 99,5% com ts+TID idênticos, 100% no mesmo segundo + TID |
| AVL | AVL-007 `VehicleNotFound` | `\t\t[Validation Critical]: Invalid vehicle mRID(s): "<id>"` | 20/20 com ts+TID idênticos |
| Osr | OSR-005 `Detected errors:` + códigos | `Detected errors:` + 1 linha de `Details` por código, na mesma ordem | 11.940/11.940: 98,7% ts+TID idênticos, 100% segundo + TID |
| Smn | SMN-016/018/019 (`Written in sensitive log`, `check sensitive log`, `Writing details in sensitive log`) | mesma linha com o `Reason:` real (`… SystemError'.. StackTrace:`, `There was no endpoint listening at https://<host-pi>:44300/XISOAPAdapter/MessageServlet?…interface=SI_Send…_PO_Out…`, `DmsReason: …`, timeout) + stack trace | 810/811: 91% ts+TID idênticos, 100% segundo + TID |

Algoritmo: para cada registro normal que remete ao sensitive, pegue na mesma TID o próximo registro sensitive do tipo correspondente com |Δt| ≤ 1 s. A diferença de ms vem de duas escritas separadas. A ordem relativa por TID se mantém.

Cuidados:
- **As janelas não coincidem**: AVL sensitive começa 1 h antes do normal; Osr sensitive cobre só as últimas 24 h; Smn sensitive vai até 28/08 (volume baixo, sem corte). Restrinja à interseção antes de medir o pareamento.
- O sensitive tem dados pessoais/operacionais: placas, mRID de instalação, números de INC/PROB, URL do SAP PI. Anonimize antes de exportar.
- O sensitive do Smn também tem linhas sem par no normal: `[ServiceUrl] : For <Op> service url is …` (no start) e `Client for endpoint XISOAPAdapter didn't send request to …`. O do Osr tem `Endpoint GetIncidentHazardsService on http://localhost:<porta>/… was not invoked more than 12:00:00` (auditoria de inatividade).

## 7. Comum × específico

**Comum (envelope genérico, vale para os 4):**
- Linha, regex, BOM, CRLF, PID/TID, regra de continuação e rotação `.log` / `.log.0`.
- Nível sempre DEBUGLOG (LogLevel=DebugLog); severidade inferida do texto ou do código de erro.
- Prefixo de componente entre colchetes no início da mensagem (`[GetIncidents start]`, `[SendWorks]`, `[BusinessLogicResolver]`, `[Communication2]`, `[SystemEventSender]`). Vale como "logger" quando existe; muitos templates não têm.
- Bloco horário `[Communication2]` (todos menos SiteSwitch), `SystemEventSender`, trio de mapa de cache, stack traces .NET/WCF com `Server stack trace` / `Exception rethrown` / `Inner Exception`.
- Início e fim de operação na mesma TID; referência ao sensitive por frase fixa ("check sensitive log", "Written in sensitive log", "can be found in the adapters sensitive log", "sensistive").

**Específico:**
- AVL: marcador `"<Evento>": <guid> message processing started... / finished successfully.`; validações em linhas próprias com TAB; tarefa de qualidade horária.
- Osr: `[<Op> start] - … / [<Op> end] - … processed.|failed.`; `Detected errors:` multilinha; Context/Search filter.
- Smn: cliente com retry; trio Send/Sending/Request result; filtros de publicação.
- SiteSwitch: 2 linhas fixas por health check.

**Perguntas em aberto:**
1. Fuso horário dos timestamps (local BRT? o perfil diurno 08–17h sugere hora local).
2. Por que todos os arquivos (normal e sensitive, exceto o sensitive do Smn) começam em HH:30:0x e a rotação do AVL ocorreu às 09:30:08? Corte do ERS, job de manutenção ou rotação por tamanho (`.log.0` com 53 MB)?
3. Texto exato do SiteSwitch no site backup (503) e se ele loga algo na troca de site (ModeTracker).
4. Sem linhas de startup/shutdown na amostra: como aparece um restart (zerar os contadores COM pode ser o único sinal)?
5. Com `MessageLogLevel=DebugLog` (ou LogLevel=Info), em qual arquivo e formato sai o payload SOAP? Não há amostra.
6. Gatilho do recarregamento de mapa (`OMS_CREW*`, 03:00, uma vez em ~3 dias).
7. O SAP PI devolve `SystemError` para `Works Created/Deleted` por falha de mapeamento no PI ou por regra? A causa está no corpo do fault, que não é logado.

# Formato de log: payloads e mensagens Info/Verbose (Wfm, Ont, Smn)

Tarefa 6.1 do ROADMAP. Base: segunda captura do ERS (2026-10-08 14:53, zona `dnaApp_mpdmz/DMS_INTEGRATION`), com Wfm, Ont e Smn elevados por `toggleDebug` até Verbose por ~2 h. Arquivos: `WfmAdapter_37228.log.0`/`.log`, `OntAdapter_27744.log.0`/`.log`, `SmnAdapter_10484.log.0`/`.log` e os três `.sensitive.log`. Tudo medido por script sobre 100% das linhas (3.377.622 linhas, 1.747.313 registros, 335 MB). A primeira captura (`docs/formato/wfm-ont.md`, `avl-osr-smn-siteswitch.md`) serviu de comparação. Exemplos sintéticos: valores trocados por `<guid>`, `<gid>`, `<inc>`, `<crew>`, `<texto>`; só estrutura, nomes de elemento e namespaces IEC públicos são reais.

## 1. Achados que mudam o desenho

1. **Payload sai com nível corrente ≥ Info**, não só em Verbose. A configuração do ERS continua `LogLevel=DebugLog` e `MessageLogLevel=Info` em todas as operações (Wfm 4, Ont 3, Smn 3, no `WebServiceConfiguration` de `DMSCustomIntegrationPROD`); o payload aparece só dentro das janelas abertas pelo `toggleDebug`. Nenhum payload em janela Warning ou DebugLog. Verbose acrescenta só rastreio de transporte.
2. **Só XML.** Nenhum JSON, nenhum envelope SOAP: o log traz o elemento da mensagem (`<ChangedCrews>`, `<ns0:SwitchingStepsResponseMessage>`) ou um `<s:Fault>` solto. Exceção: um corpo HTML de erro HTTP dentro de uma mensagem de falha.
3. **Um payload = um registro**, sempre inteiro (nenhum truncado, nenhum intercalado), indentado com 2 espaços, uma linha física por elemento. O `msg` do cabeçalho já contém o início do XML na maioria dos casos.
4. **Os payloads de request/response ficam no sensitive; parte do conteúdo enviado ao DMS fica no log normal** (Wfm `Content being sent to CRS|WMS:`).
5. **Cada payload é anunciado no normal** por uma linha própria, na mesma TID do payload e no mesmo ms (99%). Para request/response a TID é de uma thread de logging, **diferente da thread da operação**; a ligação com a operação vem do MessageID (request) e do CorrelationID (response Wfm) ou do horário do fim da operação (response Ont/Smn, que não traz ID).
6. O payload termina com **`.` colado ao fechamento da raiz** (`</ChangedCrews>.`) nos request/response; precisa ser removido antes de validar.
7. Volume: a janela elevada multiplica a escrita do log normal por 27–120×; **56% dos bytes do log normal são VERBOSE**, quase todo rastreio TCP do `Communication2`. O payload é ~8% dos bytes da janela.

## 2. Quando o payload aparece

Janelas (linhas ALWAYS `Debug level changed from X (n) to Y (m).`):

| Adapter | →Warning | →Info | →Verbose | →DebugLog | Payloads na janela Info | na Verbose |
|---|---|---|---|---|---|---|
| Wfm | 12:38:47 / 12:52:23 | 12:39:22 / 13:04:02 | 12:39:41 / 13:04:10 | 12:40:10 / 14:52:53 | 50 | 6.615 |
| Ont | 12:36:12 / 12:52:31 | 12:37:48 / 12:52:34 | 12:38:06 / 12:52:36 | 12:38:35 / 14:52:57 | 3 | 1.883 |
| Smn | 12:52:27 | 12:52:29 | 13:03:59 | 14:52:55 | 353 | 3.952 |

Regra (confirmada sem exceção): payload é gravado se o nível corrente ≥ `MessageLogLevel` da operação, na ordem DebugLog (3) < Warning (4) < Info (5) < Verbose (6) impressa pelo próprio log. O nível corrente vem da última linha `Debug level changed` (estado inicial = `LogLevel` do Registry). A janela Warning (11 min no Wfm) não gravou nenhum payload nem mensagem nova.

Fora das janelas, o sensitive continua como na primeira captura (Wfm: resumo de 3 linhas por request; Ont/Smn: só falhas e auditoria). O sensitive do Ont e do Smn retém janelas antigas (desde 2026-08-28), com payloads de setembro.

## 3. Onde o payload aparece

| ID | Arquivo | Nível | Linha do anúncio no normal | Onde começa o XML | Raiz | Direção / par | Registros |
|---|---|---|---|---|---|---|---|
| GEN-PLD-01 (Wfm) | sensitive | INFO | GEN-MSG-01 | no `msg`, após `Message content: ` | `ChangedCrews`, `ChangedCrewAssignments`, `CreatedCrewAssignments`, `DeletedCrewAssignments`, `ChangedVehicles` | request recebido da WFM | 2.359 |
| GEN-PLD-01 (Wfm) | sensitive | INFO | GEN-MSG-01 | idem | `CrewsResponseMessage`, `CrewAssignmentsResponseMessage`, `VehiclesResponseMessage` | response enviado à WFM | 2.340 |
| GEN-PLD-01 (Wfm) | sensitive | INFO | GEN-MSG-01 (MessageID vazio) | idem | `s:Fault` | fault enviado à WFM | 17 |
| GEN-PLD-01 (Ont) | sensitive | INFO | GEN-MSG-01 | no `msg`, após `Message: ` | `ChangedIncidents`, `CreatedIncidents`, `DeletedIncidents`, `ChangedIncidentTroubleTickets` | request enviado ao SAP PI | 943 (1.617 com janelas antigas) |
| GEN-PLD-01 (Ont) | sensitive | INFO | GEN-MSG-01 (MessageID vazio) | idem | `ns1:IncidentsResponseMessage`, `ns0:IncidentTroubleTicketsResponseMessage` | response do SAP PI | 943 (1.297) |
| GEN-PLD-01 (Smn) | sensitive | INFO | GEN-MSG-01 | idem | `Changed\|Created\|DeletedSwitchingSteps`, `…SwitchingPlans`, `…Works` | request enviado ao SAP PI | 1.426 |
| GEN-PLD-01 (Smn) | sensitive | INFO | GEN-MSG-01 (MessageID vazio) | idem | `ns0:SwitchingStepsResponseMessage`, `ns0:SwitchingPlansResponseMessage`, `ns0:WorksResponseMessage` | response do SAP PI | 1.389 |
| WFM-S112 | sensitive | INFO | WFM-112 `Writting delta to log sensitive.` | o `msg` inteiro é a 1ª linha do XML | `delta` | delta enviado ao CRS (ApplyDelta) | 1.885 |
| WFM-S121 | sensitive | INFO | WFM-121 `Content being sent to IMS. Content details written in sensitive log.` | 1ª continuação | `ArrayOfIncidentCrewAssigment` | enviado ao IMS | 64 |
| WFM-120 | **normal** | INFO | (o próprio registro) | 1ª continuação | `ArrayOfAssignmentStatusInfo` (CRS), `ArrayOfDocumentCrewAssigment` (WMS) | enviado ao CRS / WMS | 345 / 63 |
| SMN-S110 | sensitive | INFO | SMN-110 `[WmsSubscriber] - Data received from publication. Details are written in sensitive log.` | após 2–3 linhas `Chave: valor` (`DmsType`, `Operation`, …) | sequência de `ResourceDescription` (sem raiz única) | publicação recebida do DMS | 1.490 |
| (dentro de SMN-S…/ONT-S020) | sensitive | DEBUGLOG | — | continuação de `Error: Sending … DmsReason: The content type text/html …` | `html` | corpo HTML de erro HTTP | 1 |

Não são payload, mas também vão para o sensitive em INFO: `Creating client proxy object for service URL: <url>` (GEN-PRX-S01, anunciado por GEN-PRX-02) e, nas janelas de setembro do Ont, `Error: Sending "<tipo>" message failed. "<exceção>" has occurred.` + 16 linhas de exceção (ONT-S020).

## 4. Gramática do payload

### Envelope (100% das linhas)

A regex de `docs/formato/wfm-ont.md` (TID opcional) vale sem mudança: das 3.377.622 linhas, 1.747.313 são cabeçalho e as demais continuação; nenhuma continuação começa com data; nenhuma linha órfã; BOM nos 9 arquivos; CRLF em 100% das linhas, inclusive as quebras dentro de texto do XML. Diferenças em relação à primeira captura:

- **271 cabeçalhos sem `[TID=…]`** (antes, 1): todos INFO do logger nativo, `PSAttach::PubSubReceiveCallBack …` e `arb_pubSubLookup: …`. A TID opcional é obrigatória no envelope.
- **157 recuos de timestamp**: até 16 ms no normal (escrita concorrente) e até 2,1 s no sensitive do Smn (thread de logging assíncrona). Seguem a regra de `flags & 32`.

### Marcadores

| Forma | Início | Fim |
|---|---|---|
| GEN-PLD-01 | `msg` casa `^Message(?: content)?: (?=<)`; o XML começa logo após o prefixo, ainda na linha do cabeçalho | última linha do registro termina em `>.`: remover o `.` final (100% dos 7.546 registros) |
| WFM-S112 | `msg` = `<delta id="<n>">` | `</delta>` (sem ponto) |
| WFM-120, WFM-S121 | `msg` = `Content being sent to <CRS\|WMS\|IMS>:`; XML a partir da 1ª continuação | último `>` do registro |
| SMN-S110 | `msg` = `[WmsSubscriber] - Data received from publication:`; continuações `DmsType: <tipo>`, `Operation: <op>` (e às vezes mais uma `Chave: valor`) e depois o XML | último `>`; há N elementos `ResourceDescription` irmãos |

Regra prática para `parse/payload.py`: o template declara onde o XML começa (no `msg` após o match, no `msg` inteiro ou na 1ª continuação que começa com `<`) e se há sufixo a remover; o payload vai até o fim do registro. Não há marcador de fim nem contagem de bytes.

### Forma

- **XML indentado**, 2 espaços por nível, uma linha física por elemento; atributos na mesma linha. Nenhum payload de request/response em linha única. Exceção: o conteúdo ao WMS (`<ArrayOfDocumentCrewAssigment … />`, 63/63) é um elemento vazio numa linha.
- **Texto com quebra de linha dentro de elemento** (ex.: `<comment>` do Ont): as linhas seguintes do texto são continuações sem indentação nem `<`. Não começam com data (verificado), mas o parser não pode supor que toda continuação de payload começa com `<`.
- Sem declaração `<?xml …?>`, sem `<!DOCTYPE>`, sem CDATA.
- Codificação: UTF-8 do arquivo; texto em português com acentos (não ASCII em 1.291 de 1.617 requests do Ont e em 1.653 de 2.359 do Wfm). Escapes XML padrão: `&amp;` (1 no Ont); `&#xD;&#xA;` em valores de atributo das publicações do Smn (93). Nenhum outro escape nem caractere de controle.
- **Boa formação** (expat, após remover o `.` final): 100% dos request/response/fault/delta/CRS/WMS/IMS. Publicações do Smn: 134 de 1.490 têm mais de um `ResourceDescription` (sequência sem raiz); bem-formadas se embrulhadas numa raiz sintética.
- **Truncamento: nenhum.** Não há limite de tamanho pelo adapter: o maior payload tem 164.347 caracteres (publicação do Smn, 2.344 linhas) e sai inteiro. O único corte possível é o do ERS no último registro do arquivo em uso (não ocorreu na amostra).

Esqueleto sintético (request do Wfm):

```
2026-01-01 10:00:00.000 [PID=1111][TID=0x1a] INFO - Message content: <ChangedCrews xmlns="http://iec.ch/TC57/2011/CrewsMessage">
  <Header>
    <Verb xmlns="http://iec.ch/TC57/2011/schema/message">changed</Verb>
    <Noun xmlns="http://iec.ch/TC57/2011/schema/message">Crews</Noun>
    <Source xmlns="http://iec.ch/TC57/2011/schema/message"><texto></Source>
    <MessageID xmlns="http://iec.ch/TC57/2011/schema/message"><guid-1></MessageID>
    <CorrelationID xmlns="http://iec.ch/TC57/2011/schema/message"><guid-1></CorrelationID>
  </Header>
  <Payload>
    <Crews xmlns="http://iec.ch/TC57/2007/Crews#">
      <Crew>
        <mRID><crew></mRID>
        <availability><texto></availability>
        <fcConnectionStatus><texto></fcConnectionStatus>
      </Crew>
    </Crews>
  </Payload>
</ChangedCrews>.
```

Response do SAP PI (Ont/Smn), sem MessageID nem CorrelationID:

```
… INFO - Message: <ns0:SwitchingStepsResponseMessage xmlns:ns0="http://iec.ch/TC57/2011/SwitchingStepsMessage">
  <ns0:Header>
    <ns1:Verb xmlns:ns1="http://iec.ch/TC57/2011/schema/message">reply</ns1:Verb>
    <ns1:Noun xmlns:ns1="http://iec.ch/TC57/2011/schema/message">SwitchingSteps</ns1:Noun>
  </ns0:Header>
  <ns0:Reply>
    <ns1:Result xmlns:ns1="http://iec.ch/TC57/2011/schema/message">OK</ns1:Result>
  </ns0:Reply>
</ns0:SwitchingStepsResponseMessage>.
```

## 5. Ligação com o anúncio, a operação e o par

| Ligação | Regra | Resultado na amostra | Confiança |
|---|---|---|---|
| anúncio GEN-MSG-01 (normal) ↔ GEN-PLD-01 (sensitive) | mesmo PID, **mesma TID**, Δt mínimo ≤ 1 s, em ordem; confere o MessageID do anúncio com o `<MessageID>` do payload (vazio = vazio) | Wfm 4.716/4.716, Ont 1.886/1.886, Smn 2.815/2.815; Δt = 0 em 99%, máx. 31 ms; nenhum anúncio sem payload nem payload sem anúncio | 3 com MessageID; 2 sem |
| WFM-112/WFM-121/SMN-110/GEN-PRX-02 ↔ WFM-S112/WFM-S121/SMN-S110/GEN-PRX-S01 | mesma TID, Δt ≤ 16 ms | 1.885/1.885, 64/64, 1.490/1.490, 943/943 (Ont) | 2 |
| request ↔ operação (Wfm) | `<MessageID>` do request = `request_id` de WFM-010 | 2.359/2.359 (TID diferente: o payload sai em 9 threads de logging, nunca na thread da operação) | 3 |
| response ↔ request (Wfm) | `<CorrelationID>` do response = `<MessageID>` do request = `request_id` | 2.339/2.340; o MessageID do response é outro ID (4 letras + `_` + 18 dígitos) | 3 |
| fault ↔ operação (Wfm) | fault sem IDs; mesmo ms de WFM-081 (`ERROR - Processing … failed`) | 17/17 | 1 |
| request ↔ operação (Ont/Smn) | `<MessageID>` = MessageId de ONT-011 / SMN-007 (`Sending "<tipo>" message: <guid> …`) | 943/943, 1.426/1.426 | 3 |
| response ↔ operação (Ont/Smn) | response sem IDs; a operação cujo fim ONT-012/SMN-008 está a ≤ 16 ms do payload e cujo tipo (`<Noun>EventMessageType`) bate com a raiz (`<Noun>ResponseMessage`) | 943/943 e 1.389/1.389 com candidato único (98–99% no mesmo ms) | 1 (heurística de tempo, sem chave) |
| request sem response | request cuja operação falhou | Smn 37 (`[Validation Critical]`/exceção), Wfm 17 (fault no lugar do response) | — |
| WFM-120/WFM-S121/WFM-S112 ↔ operação (Wfm) | mesma TID dentro da operação aberta (WFM-010 … WFM-012/081) | 408/408, 64/64, 1.885/1.885 | 2 |
| callback CRS/IMS ↔ pedido (Wfm, só em Info) | GID de WFM-124/WFM-123 (pedido enviado) = GID de WFM-126 e de WFM-030 (callback) | 405/406 e 406/407 | 3 |
| callback ↔ requisição de origem (Wfm, só em Info; issue #65) | WFM-123/124 com o GID do WFM-030, todos dentro de uma única operação (WFM-010…fim, mesma TID), até 60 s antes do callback; ligação do início da operação ao WFM-030 | 406/407 (o restante sem WFM-123/124); 0 ambíguos | 2 |

Consequências:
- Em Ont/Smn, as TIDs de anúncio e payload são do mesmo pool de 24 threads de envio, mas de outra thread naquele instante. Pareamento por TID com a operação **não funciona** (0 de 4.716 no Wfm); só a chave ou o tempo.
- Request e response do mesmo par **não** ficam na mesma TID nem em sequência; parear por ordem dentro da TID erra ~50%.
- A ligação por GID do callback responde à pergunta 4 de `wfm-ont.md`, mas só existe com nível ≥ Info (WFM-124 é INFO). A regra segura e os números das três capturas estão em `wfm-ont.md`, seção 4 (issue #65).

## 6. Chaves úteis dentro do payload

| Payload | Chaves (formato) | Observação |
|---|---|---|
| request Wfm | `Header/MessageID` = `CorrelationID` (`<guid>` = `request_id`); `Crew/mRID` (`<letras><n>`), `Vehicle/mRID`, assignments com `INC <n>` | `Verb`, `Noun`, `Source` no header |
| response Wfm | `MessageID` (próprio), `CorrelationID` (= request), `Reply/Result` (`OK` 2.274, `FAILED` 66), `Reply/Error/code` (`2.7`), `level` (`FATAL`, `INFORM`, `WARNING`), `reason`, `details`; ecoa o `Payload` do request | `Timestamp`, `Revision`, `Source=ADMS` |
| fault Wfm | `faultcode` (`s:Client`), `faultstring`, `detail/*FaultMessage/Reply/Error/code` (`5.3`), `level` (`FATAL`) | confirma a suposição de `InternalServerError` 5.3 em falha |
| request Ont | `MessageID` = `CorrelationID` (`<guid>`), `IncidentRecord/mRID` (`INC <n>`), `IncidentHazards/mRID` (`PROB <n>`), `Crews/mRID`, códigos de equipamento | texto livre (`comment`), município, nomes |
| request Smn | `MessageID` = `CorrelationID`, `SwitchingStep/mRID` (`SP <n> - ISS <n>`), `SwitchingPlan`/`Work` `mRID` (`SP <n>`, `WR <n>`), `Equipments/mRID` | descrições livres |
| response Ont/Smn | só `Verb=reply`, `Noun`, `Reply/Result` (`OK` em 100%) | sem chave: ligar pela operação (seção 5) |
| delta (WFM-S112) | `delta/@id`, `operation/@type`, `ResourceDescription/id/@gid`, `@type`, `Property/@id`, `@value` | GIDs do DMS |
| CRS/IMS/WMS (WFM-120, WFM-S121) | `CrewId`, `IncidentId` (GIDs), `AssignmentStatusId`, `Timestamp` | |
| publicação Smn (SMN-S110) | `DmsType`, `Operation`, `ResourceDescription/id/@gid`, `Property/@value` (inclui `SP <n>`) | |

Para `entry_key`: `msgid` (MessageID e CorrelationID de request/response), `mrid` (INC, PROB, SP/ISS/WR, crew), `gid` (delta, CRS/IMS, publicação). O par request↔response e a operação já resolvem a maior parte da correlação; as chaves do payload servem à busca "tudo de um INC".

### 6.1 Medição e escolha das chaves (issue #67)

Medido por script no banco do ERS Verbose (2026-10-08 14:53; 14.292 payloads, 3,73 M registros), só contagens. "Nas linhas de log" = valores distintos do payload que já existem em `entry_key` (chaves de template) com o mesmo tipo, ou seja, que a busca por chave passa a juntar com linhas de log.

| Caminho no payload | Ocorrências | Distintos | Nas linhas de log | Decisão |
|---|---|---|---|---|
| `Header/MessageID` | 7.742 | 7.255 | 6.581 (`msgid`) | `msgid` |
| `Header/CorrelationID` | 7.742 | 5.403 | 4.729 (`msgid`) | `msgid` (igual ao MessageID nos requests: 1 linha) |
| `SwitchingStep(s)/mRID` (`SP <n> - ISS <n>`) | 2.827 | 1.383 | 1.018 (`mrid`) | `mrid` |
| `IncidentRecord/mRID` (`INC <n>`) | 1.617 | 468 | 249 (`mrid`) | `mrid` |
| `Crew`/`Crews/mRID` | 5.288 | 351 + 181 | ~2 (`mrid`) | `mrid` (identificador central do Wfm) |
| `Assignments/mRID` (`INC <n>`) | 880 | 184 | 174 (`mrid`) | `mrid` |
| `SwitchingPlan`, `SwitchingStepGroup`, `Work(s)`, `Incidents` `/mRID` | 1.510 | 8–61 cada | 0–46 cada (`mrid`) | `mrid` |
| `IncidentHazards/mRID` (`PROB <n>`), `TroubleTickets/mRID` | 523, 732 | 180, 352 | 0 | `mrid` (objetos de negócio do incidente) |
| `Vehicle/mRID` | 189 | 46 | 46 (`mrid` e `vehicle`) | `mrid` + `vehicle` |
| `ResourceDescription/id/@gid` (delta, publicação; `0x` + 16 hex) | 3.982 | 1.400 | 25 após hex → decimal (`gid`) | `gid` em decimal |
| `Property SM_COMMANDING_MODEL_CUSTOMID` (publicação) | 2.097 | — | 1.668 ocorrências (`mrid`) | `mrid` |
| `IncidentId` (WFM-S121) | 64 | 57 | 57 (`incident_gid`, WFM-124B), 3 (`gid`, OSR) | `gid` + `incident_gid` |
| `ServiceAssignmentId` (WFM-120) | 345 | 153 | 44 (`incident_gid`), 29 (`gid`): coincidência parcial, ver abaixo | só `gid` |
| `CrewId` (WFM-120, WFM-S121) | 409 | 106 | 0 | `gid` (GID do DMS) |
| `Equipments/mRID`, `RequestedEquipment/mRID` | 2.981 | 736 | 0 | fora (equipamento; o FTS acha) |
| `UsagePoint/mRID`, `UsagePoint/meterId` | 729, 366 | 249, 207 | 0 | fora (cliente/medidor) |
| `Substation`, `Zones`, `Region`, `PSRType` `/mRID` | 5.519 | 3–72 | 0 | fora (classificação, baixa cardinalidade) |
| nomes, endereços, `comment`, datas, demais `Property` | — | — | — | fora (texto livre ou sensível) |

**`IncidentId` × `ServiceAssignmentId` (domínio, informado pelo Jefferson em 2026-10-12).** `IncidentId` (conteúdo ao IMS) é o ID do incidente no cache do IMS (Incident Management Service): vira `incident_gid`, o tipo com que o WFM-124B registra o `Incident Gid` do pedido ao IMS, e também `gid`, o tipo com que OSR-024/025 registram o `incidentId` do IMS. `ServiceAssignmentId` (conteúdo ao CRS) **não** é ID de incidente: é o ID do Assignment de uma equipe no cache do CRS (Crew Service). O Assignment pode estar ligado a um incidente (daí a coincidência parcial medida acima) ou a outros documentos do ADMS (SwitchingSteps, SwitchingPlans, WorkRequests, com IDs no cache do WMS/WOM, vistos no Smn). Gravá-lo como `incident_gid` faria a busca ligar um Assignment a um incidente que não é o dele; por isso é só `gid`. Não foi criado tipo `assignment_gid`: nenhum template do catálogo registra o GID do Assignment, então um tipo próprio só juntaria payloads WFM-120 entre si, o que `gid` já faz; fica para quando surgir uma linha de log com esse ID. O catálogo foi conferido: WFM-124B guarda em `incident_gid` o `Incident Gid` do pedido ao IMS (é de fato incidente) e em `gid` o `OMS_REQUEST`; OSR-024/025 guardam em `gid` o `incidentId` do IMS; nenhum template usa `incident_gid` para um Assignment.

Resultado gravado no mesmo ERS: 30.794 linhas novas em `entry_key` (`mrid` 15.661, `msgid` 10.082, `gid` 4.798, `incident_gid` 64, `vehicle` 189; 2,7% das 1.147.495 de template), banco ≈ +5 MB (+0,27%), tempo de ingestão igual dentro do ruído. Distintos que passam a encontrar linhas de log pela mesma chave: `msgid` 6.582 de 7.256, `mrid` 903 de 2.535, `incident_gid` 57 de 57, `gid` 54 de 1.569, `vehicle` 46 de 46. (Contagens recalculadas a partir da medição, retirando as 345 linhas `incident_gid` do `ServiceAssignmentId`.) Regras de normalização, limites e a separação da derivação (`entry_key.origin`): ARQUITETURA, seção 6.6, "Chaves do payload".

## 7. Tamanho

Caracteres do XML (do `<` inicial ao fim, sem o `.`), por tipo:

| Tipo | n | p50 | p90 | p99 | máx. | linhas p50 / máx. |
|---|---|---|---|---|---|---|
| Wfm request | 2.359 | 791 | 1.080 | 1.084 | 1.087 | 18 / 24 |
| Wfm response | 2.340 | 852 | 945 | 1.435 | 1.443 | 23 / 34 |
| Wfm fault | 17 | 830 | 975 | 992 | 992 | 17 / 17 |
| Wfm delta (WFM-S112) | 1.885 | 444 | 444 | 534 | 547 | 14 / 15 |
| Wfm CRS (WFM-120) | 345 | 532 | 536 | 537 | 541 | 12 / 12 |
| Wfm WMS (WFM-120) | 63 | 131 | 131 | 131 | 131 | 2 / 2 |
| Wfm IMS (WFM-S121) | 64 | 535 | 535 | 540 | 540 | 14 / 14 |
| Ont request | 1.617 | 3.910 | 4.541 | 7.721 | 13.277 | 102 / 332 |
| Ont response | 1.297 | 533 | 533 | 533 | 533 | 9 / 9 |
| Smn request | 1.426 | 1.693 | 3.055 | 14.321 | 59.759 | 38 / 1.543 |
| Smn response | 1.389 | 447 | 447 | 447 | 447 | 9 / 9 |
| Smn publicação (SMN-S110) | 1.490 | 1.716 | 2.504 | 9.034 | 164.347 | 31 / 2.344 |

Dois registros na amostra passam de 65.536 caracteres de continuação (uma publicação do Smn e um dump de mapa no normal do Smn); o limite do FTS por registro (ARQUITETURA, 6.6) continua adequado.

## 8. Volume e custo para o índice

Por arquivo (% de linhas / % de bytes por nível):

| Arquivo | Linhas | Registros | Bytes | DEBUGLOG | INFO | VERBOSE | ALWAYS |
|---|---|---|---|---|---|---|---|
| WfmAdapter.log.0 | 1.102.390 | 275.236 | 62,4 MB | 87,9 / 69,7 | 5,4 / 11,6 | 6,6 / 18,2 | 0,2 / 0,4 |
| WfmAdapter.log | 330.192 | 270.085 | 41,2 MB | 20,9 / 8,2 | 29,2 / 28,9 | 49,9 / 62,9 | 0,0 / 0,0 |
| OntAdapter.log.0 | 379.941 | 372.912 | 59,0 MB | 13,2 / 13,9 | 13,1 / 10,5 | 73,6 / 75,5 | 0,0 / 0,0 |
| OntAdapter.log | 74.852 | 73.364 | 11,5 MB | 2,5 / 2,6 | 14,6 / 11,7 | 82,9 / 85,8 | 0,0 / 0,0 |
| SmnAdapter.log.0 | 528.098 | 504.069 | 80,6 MB | 35,1 / 34,9 | 11,1 / 8,7 | 53,9 / 56,5 | 0,0 / 0,0 |
| SmnAdapter.log | 149.686 | 146.237 | 23,2 MB | 3,6 / 3,5 | 15,7 / 12,8 | 80,7 / 83,7 | 0,0 / 0,0 |
| Wfm sensitive | 375.741 | 85.624 | 21,8 MB | 65,8 / 74,3 | 34,2 / 25,7 | — | — |
| Ont sensitive | 179.136 | 4.994 | 8,6 MB | 0,1 / 0,7 | 99,9 / 99,3 | — | — |
| Smn sensitive | 257.586 | 14.792 | 27,2 MB | 45,2 / 70,2 | 54,8 / 29,8 | — | — |
| **Total** | **3.377.622** | **1.747.313** | **335,4 MB** | | | | |

Somando os 6 logs normais: DEBUGLOG 49,9% das linhas / 30,3% dos bytes; INFO 11,6% / 13,2%; VERBOSE 38,4% / 56,4%; ALWAYS 0,1% / 0,1%. Sensitive: DEBUGLOG 44,8% / 61,5%; INFO 55,2% / 38,5%. (O DEBUGLOG do Wfm `.log.0` é quase todo dump de mapa, WFM-001.)

Taxa de escrita (MB/h), fora × dentro das janelas Info/Verbose:

| Adapter | Normal base | Normal elevado | Sensitive base | Sensitive elevado | Payload no elevado |
|---|---|---|---|---|---|
| Wfm | 1,2 | 32,2 | 0,2 | 3,4 | 5,5 MB em 1,8 h (85% do sensitive; 0,4% do normal) |
| Ont | 0,4 | 30,1 | 0,0 | 2,2 | 4,1 MB em 2,0 h (93% do sensitive) |
| Smn | 0,3 | 36,6 | 0,0 | 3,9 | 7,3 MB em 2,0 h (93% do sensitive) |

Só Info (Smn, 11,5 min antes do Verbose): 5,0 MB/h no normal, 7× menos que Verbose, com todos os payloads. Do volume da janela elevada, ~92% vai para o log normal (mensagens da seção 9), e o rastreio TCP do `Communication2` (GEN-C2T-01/02) sozinho é 46–62% das linhas Info/Verbose.

Rotação: os `.log.0` têm 59–81 MB e viraram `.log.0` em hh:30 (Wfm 13:30; Ont e Smn 14:30). O ERS traz só `.log` + `.log.0` por processo; em Verbose o `.log` do Ont cobre 25 min. Hipótese a confirmar: rotação verificada de hora em hora (hh:30) por tamanho.

Tamanho do índice (razão 1,59× da ARQUITETURA, seção 3, sobre bytes de log):

| Conjunto | Logs | Banco estimado |
|---|---|---|
| Os 9 arquivos acima | 335 MB | ≈ 0,53 GB |
| ERS 1 (12:40), escopo padrão (adapters do escopo normal + sensitive + BLTHost, com rotacionados): 57 arquivos | 793 MiB | ≈ 1,26 GB |
| **ERS 2 (14:53, Wfm/Ont/Smn elevados ~2 h)**, mesmo escopo: 60 arquivos | **1.041 MiB** (+31%; Wfm+Ont+Smn de 144 para 375 MiB) | **≈ 1,65 GB** |
| Projeção de pior caso: 14 adapters em Verbose até o limite da rotação (~160 MB por processo) + BLTHost + sensitive | ≈ 2,6 GB | ≈ 4,1 GB |

Meta de indexação do M6 (registrada no ROADMAP): ERS 2 no escopo padrão indexado com templates, FTS e extração de payload em **≤ 60 s** na máquina de dev, banco **≤ 1,7 GB**, RSS < 600 MB; extração de payload acrescenta ≤ 10% ao tempo sem ela. O pior caso projetado fica para o M8.

## 9. Mensagens novas que só existem em Info/Verbose

Classificador por regex testado em 100% dos registros INFO/VERBOSE dos 9 arquivos (mais GEN-MSG-01, que é DEBUGLOG): **cobertura de 100%** das linhas, sem nenhum registro casando com dois templates. Para 99% das linhas bastam 26 templates no Wfm, 19 no Ont e 21 no Smn (normal) e 2–3 por sensitive. Fora das janelas, o catálogo DEBUGLOG da primeira captura continua valendo: as únicas mensagens DEBUGLOG/ALWAYS novas são variantes dos templates existentes com outro serviço (`OMSBusinessService`, `IMSGDAService`, `IncidentManagementPubSub` em WFM-060/061/062/070/071/081), uma do Ont (`[SendIncidentTroubleTickets] - Incident '<inc>' has no trouble tickets to send.`) e GEN-MSG-01 no Smn.

Notação: `<n>` inteiro, `<gid>` GID do DMS, `<guid>`, `<s>` texto, `…` resto livre. Colunas W/O/S = registros nos normais (`.log.0` + `.log`) de Wfm, Ont, Smn; `sens` = no sensitive. Sev sugerida: 5 = ruído (oculto por padrão), 4 = info. IDs `GEN-*` vão para `_common.toml`; não colidem com os existentes (GEN-SUB-01 e GEN-MAP-01 já existem e são estendidos onde indicado).

### Communication2 (comum, VERBOSE salvo indicação; sev 5)

| ID | Template | Nível | W | O | S | Campos |
|---|---|---|---|---|---|---|
| GEN-C2T-01 | 13 variantes de rastreio de socket/framing/IOCP: `[Communication2] - Tcp socket id = <n> (receive\|send) called with <n> bytes count.` / `… succesfully finished.` / `… overlapped receive started succesfully.` / `Tcp framing socket id = <n> (data received, lpOverlapped = <n>\|complete message received, msgSize = <n>\|data received and succesfully completed).` / `Tcp receive completion port handle = <n> (handle completion with params …\|queued for completion\|received completion event with params …).` / `Framing socket id = <n> (is sending message. Client id = <n> message msgId = <n> msgSize = <n>\|send message succesfull).` | VERBOSE | 159.762 | 218.894 | 263.471 | socket_id, bytes, size |
| GEN-C2T-02 | `[Communication2] - TCP client connection id = <n> (sending\|receiving) message msgId = <n>, msgType = (Request\|Reply\|Authenticate), msgSize = <n>.` | VERBOSE | 24.534 | 33.676 | 40.534 | conn_id, dir, c2_msgid, msg_type, size |
| GEN-C2T-03 | `[Communication2] - (En\|De)crypting message with byte count <n>.` / `Message (en\|de)crypted with final byte count <n>.` | VERBOSE | 60 | — | — | — |
| GEN-C2M-01 | `[Communication2] - ShMem client connection guid = <guid> (sending request\|response received) msgSize = <n>.` / `(Sending request on\|Response received on) ShMem channel guid = <guid>, msgSize = <n>.` | VERBOSE | — | 7.032 | 8.088 | size |
| GEN-C2O-01 | `[Communication2] - Calling service operation <Método> over service connection id = <n>.` | VERBOSE | 9.915 | 17.556 | 21.134 | method, conn_id |
| GEN-C2O-02 | `[Communication2] - (Compressing\|Decompressing) (request\|response) message for method <Método>, (original\|compressed) size = <n>, (compressed\|decompressed) size = <n>.` | VERBOSE | 16.734 | 33.182 | 39.221 | method, part, size_in, size_out |
| GEN-C2O-03 | `[Communication2] - Service operation <Método> (called\|implementation called\|implementation call succeeded).` | VERBOSE | 6.900 | 3.105 | 3.453 | method, phase |
| GEN-C2L-01 | `[Communication2] - Opening new service connection binding = <b>, address = <a>…` / `Client transport channel without an opened connection created.` / `Compression (client\|service) channel created.` / `Tracing (client\|service) channel created.` (sem prefixo) / `Service connection opened id = <n>.` / `Connection wrapper created of type = <t>, id = <n>.` | INFO | 15.508 | — | — | — |
| GEN-C2L-02 | abertura/fechamento TCP: `Opening TCP client connection with parameters fullAddress = <url>, …` / `TCP client connection id = <n> successfully (opened\|closed).` / `… queued for closure. Stack trace:` (+ pilha) / `Tcp socket connected id = <n>, ipAddress = <ip>, port = <n>.` / `Translated address for <host> = <ip>.` / registro e limpeza de socket | INFO | 210 | — | — | url, host, ip (sensíveis) |

### Infraestrutura comum (sev 5, salvo indicação)

| ID | Template | Nível | W | O | S | Campos |
|---|---|---|---|---|---|---|
| GEN-MOD-01 | `ModeTracker: Sleeping...` | INFO | 6.522 | 7.206 | 7.163 | — |
| GEN-MOD-02 | `Mode Tracker: [<tabela>] table is not changed. Elements: RTDB(<n>), array(<n>).` | VERBOSE | 6.496 | 7.186 | 6.480 | table |
| GEN-PSA-01 | `PSAttach::PubSubReceiveCallBack: Received callback for config value '<s>' (no change from '<s>'…` (sem TID em parte) | INFO | 4.840 | 5.354 | 5.322 | key |
| GEN-PSA-02 | `arb_pubSubLookup: Received update on topic: <topic> with status <n>` / `arb_pubSubLookup: Cache updated with system: <s>, service: <s>, host: <host>, topic: <topic>, sync state: <estado>` | INFO | 2.644 | 2.916 | 2.902 | topic, system, service, host, state |
| GEN-PRX-01 | `Getting service proxy of type "<tipo>".` / `… succeeded.` | VERBOSE | 10.468 | 7.664 | 8.394 | type |
| GEN-PRX-02 | `Request client proxy object for service with contract type <n>. Service details written in sensitive log.` / `Detected existing client proxy object for service with contract type <n>.` | INFO | — | 1.886 | 2.852 | contract (anuncia GEN-PRX-S01) |
| GEN-PRX-S01 | `Creating client proxy object for service URL: <url>` (sensitive) | INFO | — | 1.617 sens | 1.426 sens | url (sensível) |
| GEN-FLT-01 | `Removing zero IDs from the source collection: "<gid>", …` / `Zero IDs successfully removed from the source collection. New source collection: …` | VERBOSE | 1.886 | 8.044 | 5.938 | — |
| GEN-FLT-02 | `[<Query> - <Método>] ` opcional + `Filtering data based on IDs: "<gid>", …` / `Filtering data succeeded. <n> entities returned.` / `Collection is (not )?empty.` / `Getting all data from "<s>"...` / `… succeeded. <n> entities returned.` / `Getting GIDs for provided UIDs: "…".` / `… succeeded.` | INFO | 4.369 | 16.435 | 17.845 | n |
| GEN-POOL-01 | `[AdaptivePool <pool>] [UsageMonitor] In the previous <n>ms period adaptive pool peak pressure parameters were max=<n>, min=<n>. …` | INFO | 219 | 242 | 241 | pool, max, min |
| GEN-XSD-01 | `Received valid request message` / `Sending valid reply message` / `Sending request message is valid` / `Received reply message is valid` + ` according to XML schema definition.` | INFO | 4.716 | 1.886 | 2.815 | xsd (fase); sev 4, marca request/response da operação |
| GEN-XSD-02 | `[MessageValidator] Message: <razão>` (ex.: `Could not find schema information for the element '<ns>:SystemError'.`) | INFO | — | — | 37 | reason; **sev 2** |
| GEN-CCH-01 | smart cache e replicação: `Cache[<cache>] Apply delta in ElementCache. Inserted <n> \| Updated <n> \| Deleted <n>` (VERBOSE) / `Cache[<cache>] Replicate change. …` (VERBOSE) / `Cache[<cache>] Applying delta finished` / `(REPLICATION_INFO) Cache[<cache>] Replicate elements finished` / `Replicator: SendReplica client[…] with MessageChunks Count: <n>` / `Sending bulk of <n> replication messages of total size <n>.` (VERBOSE) / `Model ID = '<n>` / `Removing IntegrationMessage ID = '<n>` | INFO/VERBOSE | — | 13.060 | 22.412 | cache, im_id (= id do `.db3`, seção 10) |
| GEN-SUB-02 | `Subscriber <PubSub>:<n>. Dequeued LocalSubscriberCommand count: <n>.` | INFO | 30 | 1.037 | 988 | pubsub, n |
| GEN-SUB-03 | `[Subscriber] - Publication info:` + 4–6 linhas `Sub-process - <op>`, `CacheOperation`, `ChangedProperty`, `Entity identifiers`, `DmsType` | INFO | — | 1.488 | 1.867 | (cont_parser `kv_dash`) |
| GEN-SUB-04 | `IU received for topic: <topic>.` | VERBOSE | 183 | — | — | topic |
| GEN-EVT-02 | `Publishing EventData:` + 13 linhas `Chave: valor` (Timestamp, Details, …) | INFO | 66 | 75 | 88 | (cont_parser `kv_colon`); sev pelo evento |
| GEN-EVT-03 | `[SystemEventSender] Trying to send an system event. System event message: <evento>` | INFO | 66 | 75 | 88 | event; precede GEN-EVT-01, que nas janelas também sai em INFO |
| GEN-MAP-01 (estendido) | `"<mapa>" map integrity update started. Map will be cleared and populated with fresh data.` / `"<mapa>" map cleared.` / `<n> entries will be added to the "<mapa>" map...` | INFO | 885 | — | — | map, n |
| GEN-MAP-02 | `"<chave>" entry added to the "<mapa>" map.` (uma linha por item, sem TAB) | INFO | 95.124 | — | — | key, map (chave é dado operacional: placa, nome de equipe) |
| **GEN-MSG-01** | `Message with MessageID: '<id>' will be logged in sensitive log.` (alias WFM-100, ONT-100; novo no Smn) | DEBUGLOG, só com nível ≥ Info | 4.716 | 1.886 | 2.815 | message_id (`<guid>`, ID do response do Wfm ou vazio); `refs_sensitive`, anuncia GEN-PLD-01 |
| **GEN-PLD-01** | `Message content: <Raiz …>` (Wfm) / `Message: <Raiz …>` (Ont/Smn) + XML (sensitive) | INFO | 4.716 sens | 2.914 sens | 2.815 sens | root, payload (seção 4) |

### Wfm (INFO; sev 4 salvo indicação)

| ID | Template | W | Campos |
|---|---|---|---|
| WFM-030 / GEN-CON-01/02 | já existentes; nas janelas também saem em INFO (`Callback received:` 407 + 343 no sensitive com `Status value`; `Connecting to …` 463) | — | — |
| WFM-110 | `Generating Delta...` | 1.885 | — |
| WFM-111 | `<n> (crews\|vehicles) added to insert Delta operation.` | 1.885 | n, entity |
| WFM-112 | `Writting delta to log sensitive.` | 1.885 | `refs_sensitive` → WFM-S112 |
| WFM-113 | `[CrsDataManager] Applying delta[ completed].` | 3.753 | done |
| WFM-114 | `Inserting <n> vehicle radio IDs into ADMS...` / `Vehicle radio IDssuccessfully inserted into ADMS.` | 189 | n |
| WFM-S112 | `<delta id="<n>">` + XML (sensitive) | 1.885 sens | delta_id; payload |
| WFM-120 | `Content being sent to (CRS\|WMS):` + XML na continuação (**normal**) | 408 | target; payload |
| WFM-121 | `Content being sent to IMS. Content details written in sensitive log.` | 64 | `refs_sensitive` → WFM-S121 |
| WFM-S121 | `Content being sent to IMS:` + XML (sensitive) | 64 sens | payload |
| WFM-122 | `Calling <IContrato>.<Método>.` (`ICRSBusinessContract.OrderedChangeAssignmentStatuses`, `IOMSBusinessContract.UpdateIncidentCrewAssignments`) | 409 | contract, method |
| WFM-123 | `Request IDs returned by (CRS\|IMS) are: "<gid>[,<gid>…]".` | 409 | target, gids → `entry_key gid` |
| WFM-124 | `CRS request (OMS_CREW_CHANGE_REQEST = <gid>) is paired with "<s>" assignments and sent to Prod zone.` / `IMS request (OMS_REQUEST = <gid>) is paired with Incident Gid: <gid> \| Assigned crew Gid(s): … \| Deassigned crew Gid(s): … assignments and sent to Prod zone.` | 408 | gid, incident_gid |
| WFM-125 | `[(Crs\|Ims)RequestManagerForProdRequests] Adapter will wait to receive <n> (answers\|callback results) from (CRS\|IMS) for <n> seconds.` / `Current number of … to be waited for: <n>.` / `(Answer\|Callback) for request with GID <gid> received.` / `Waiting for <n> more …` / `[(Crs\|Ims)?RequestManagerForProdRequests] All (answers\|callbacks) received. Continuing with process.` | 2.032 | n, target, secs (= `WaitForCRSCallback`/`WaitForCallback`), gid |
| WFM-126 | `(CRS\|IMS) sent callback to the adapter for request with GID "<gid>" with result "<s>"[ and errors: "<s>"].` | 406 | target, gid, result, errors; chave do callback |
| WFM-127 | `New request with GID <gid> added. Current state: <n> request(s) on hold: …` / `Request with GID <gid> removed.` | 818 | gid |
| WFM-128 | `Cannot find request ID <gid> in the request synchronizer to notify main thread about callback. Retries count left <n>. Retry timeout <t>.` | 11 | gid, left; **sev 3** |
| WFM-129 | `There are no assignments to be sent to WMS.` | 63 | — |
| WFM-130 | `ERROR: CRS request is rejected in DMZ zone for entity: "<s>".` / `CRS request with randomly generated GID <gid> for rejected "<s>" assignments is created.` / `CRS request is rejected by DMZ validation rules: …` | 3 | **sev 2** |

### Ont (INFO; sev 4)

| ID | Template | O | Campos |
|---|---|---|---|
| ONT-110 | `[(SendIncidents\|SendIncidentTroubleTickets)] - Processing of <n> (Incident\|Trouble Tickets) Resources started.` | 1.182 | op, n |
| ONT-111 | `[CachedDataSynchronizer] - (Saving\|Dequeuing\|Deleting\|Updating) '<tipo>' message with GID: '<gid>', [Verb: '<v>' and ]Identifier: '<inc>'[ and MessageId: '<guid>'] (to\|from\|in) smart cache[ succeeded][. Status is changing from NotSent to Sent]` / `[CachedDataSynchronizer] - Dequeuing message from internal queue...` | 5.692 | action, msg_type, gid, mrid, message_id: **liga MessageId ↔ INC ↔ GID** e o estado da fila |
| ONT-S020 | `Error: Sending "<tipo>" message failed. "<exceção>" has occurred.` + exceção (sensitive, janelas de setembro) | 320 sens | msg_type, exc_type; sev 2 |

### Smn (INFO; sev 4)

| ID | Template | S | Campos |
|---|---|---|---|
| SMN-110 | `[WmsSubscriber] - Data received from publication. Details are written in sensitive log.` | 1.490 | `refs_sensitive` → SMN-S110 |
| SMN-S110 | `[WmsSubscriber] - Data received from publication:` + `DmsType`/`Operation` + XML (sensitive) | 1.490 sens | dms_type, operation; payload |
| SMN-111 | `[BusinessLogicResolver] - Processing received publication data.` + 3 linhas | 1.490 | — |
| SMN-112 | `[UnsentMessagesPersister] - (Saving\|Deleting) message with GID: '<gid>', Identifier: '<id>' (to\|from) smart cache[ succeeded].` / `Peek message from internal queue...` / `Peek '<tipo>' message with GID: '<gid>', Identifier: '<id>' and MessageId: '<guid>' succeeded.` | 8.556 | gid, ident (`SP <n> - ISS <n>`, `WR <n>`), message_id: **liga MessageId ↔ passo/plano/WR ↔ GID** |
| SMN-113 | `[CreatedEntityIdsPersister] - (Saving\|Deleting) message with GID: '<gid>', (EntityMrid\|Mrid): '<mrid>' (to\|from) smart cache[ succeeded].` | 1.238 | gid, mrid |
| SMN-099 | `Debug level changed …` (ALWAYS; primeira vez no Smn) | 4 | de, para |

## 10. Bancos `LocalPersistenceStore` (`*.db3`) do ERS

O ERS traz, em `ADMS/data/save_cases/LocalPersistenceStore/0000000000000000/`, o cache persistente de cada adapter (fila de mensagens a processar e enviar). Inspeção feita só sobre esquema e contagens, numa cópia somente leitura (`mode=ro&immutable=1`); nenhum valor foi registrado.

| Arquivo | Tamanho | Páginas livres | Tabelas (linhas) |
|---|---|---|---|
| `__~AdapterOntStorageSmartCache~__OntAdapter__LPS.db3` | 340 MB | 82.719 de 83.129 (99,5%) | `CURRENT_INTEGRATION_MESSAGE` (404), `CURRENT_METADATA` (1) |
| `__~AdapterSmnUnsentMessageSmartCache~__SmnAdapter__LPS.db3` | 1,6 MB | 384 de 391 (98%) | `CURRENT_INTEGRATION_MESSAGE` (3), `CURRENT_METADATA` (1) |
| `__~AdapterSmnCreatedMessageSmartCache~__SmnAdapter__LPS.db3` | 3,7 MB | 0 | `CURRENT_CHANGE_REQUEST` (13.471), `CURRENT_METADATA` (1) |

`CURRENT_INTEGRATION_MESSAGE` (fila de mensagens de integração):

| Coluna | Tipo | O que representa |
|---|---|---|
| `_INTEGRATION_MESSAGE_ID` | INTEGER (13 dígitos) | id interno do item; é o `Model ID = '<n>` e o `Removing IntegrationMessage ID = '<n>` do log em Info (GEN-CCH-01): 93 dos 404 do Ont e 3 de 3 do Smn aparecem no log |
| `_INTEGRATION_MESSAGE_STATUS` | TEXT | estado na fila: Ont `Sent` (404); Smn Unsent vazio (3, ainda não enviados) |
| `_INTEGRATION_MESSAGE_FILE_LOCATION` | TEXT | Ont `Created`; Smn vazio |
| `_INTEGRATION_MESSAGE_CONTENT` | BLOB | corpo serializado da mensagem: **binário** (não é o XML do log; sem `<?xml`, sem UTF-16, sem gzip/deflate), média 3,3 KB no Ont e 2,1 KB no Smn; contém o MessageId |
| `_INTEGRATION_MESSAGE_TYPE` | TEXT | tipo da mensagem (`IncidentsEventMessageType`; `WorksEventMessageType`, `SwitchingPlansEventMessageType`) |
| `_INTEGRATION_MESSAGE_FINALSTATE` | TEXT (GUID) | **MessageId** da mensagem: casa com GEN-MSG-01, ONT-002/011, ONT-111 e SMN-112 (329 de 404 no Ont, os demais são anteriores ao log; 3 de 3 no Smn) |
| `_INTEGRATION_MESSAGE_TIMESTAMP` | INTEGER | instante em ticks .NET; Ont de 2026-08-13 a 2026-10-08 (retenção de ~8 semanas de enviados); Smn, minutos antes da coleta |
| `_INTEGRATION_MESSAGE_USERNAME` | TEXT | conta do processo (sensível) |
| `_IMPORT_CMD`, `_RESET_PROGRESS_BAR`, `_IS_REPAIR_MODE_ACTIVE` | INTEGER | flags, todas 0 |

Não há coluna de contagem de tentativas nem de erro. `CURRENT_METADATA` guarda nome/versão do modelo e um `INITIAL_XML` vazio. `CURRENT_CHANGE_REQUEST` (Smn Created) tem 30 colunas `_SM_CHANGE_REQUEST_*`/`_SM_COMMANDING_MODEL_*`: uma linha por entidade criada no ADMS a partir do Smn (`WORK_ENTRY_GID` único; 439 dos GIDs aparecem no log em SMN-113), comentário curto e BLOBs de 12 bytes (listas vazias).

Dimensionamento: os 340 MB do Ont são páginas livres de um pico anterior (o SQLite não devolve espaço sem `VACUUM`); o conteúdo vivo é ~1,3 MB. Ler o banco custa pouco (o SQLite só lê as páginas usadas), mas ele precisa ser extraído do zip para um arquivo temporário (SQLite não lê de stream), o que leva os 340 MB ao disco.

Leitura para o desenho: os bancos não são histórico nem log; mostram o **estado da fila no instante da coleta** (o que ainda não foi enviado e o que foi enviado e está retido). O valor para diagnóstico está nos itens pendentes (`Unsent`) e na ligação MessageId → evento do log. Recomendação do arquiteto (proposta, decisão do Jefferson): fora da 1.0; pós-1.0 como fonte opcional, ver ARQUITETURA, seção 15 e ROADMAP (P3).

## 11. Correções aos documentos anteriores

| Documento | Afirmava | Correção |
|---|---|---|
| ARQUITETURA 6.6, ROADMAP M6 | payload pede `LogLevel=Verbose` | basta nível corrente ≥ `MessageLogLevel` (Info); Verbose só acrescenta rastreio (7× o volume do Info no Smn) |
| ARQUITETURA 6.6 | formatos SOAP/XML e JSON, talvez em uma linha, talvez truncado | só XML (corpo, sem envelope SOAP; `s:Fault` solto), indentado, nunca truncado pelo adapter; sem JSON |
| ARQUITETURA 5 (`payload.entry_id`) | payload esperado no stream sensitive | também no normal (WFM-120: CRS/WMS) |
| ARQUITETURA 8 / wfm-ont.md 4 | payload ligado ao normal por TID + tempo, ao lado da operação | anúncio e payload na mesma TID, mas TID ≠ operação; operação só por MessageID/CorrelationID ou pelo fim da operação |
| wfm-ont.md 2 (INFO) | `Content being sent to CRS/WMS/IMS:` + XML no log normal | CRS e WMS no normal; IMS só anunciado no normal, XML no sensitive |
| wfm-ont.md 3 | WFM-100 / ONT-100 só na janela INFO, 50 e 14 ocorrências | mesma mensagem nos três adapters (GEN-MSG-01), uma por payload, inclusive responses (ID do response ou vazio); nível DEBUGLOG |
| wfm-ont.md 4 (Ont) | sensitive grava com atraso de 0–0,2 s e em outra TID | Δt = 0 em relação ao anúncio (99%, máx. 31 ms); a TID é a do anúncio, não a da operação |
| wfm-ont.md 1 / ARQUITETURA 6.1 | uma linha sem TID em todo o ERS | 271 em Info/Verbose, todas do logger nativo (PSAttach/arb_pubSubLookup); responde à pergunta 5 de `wfm-ont.md` |
| wfm-ont.md 6, pergunta 4 | callback `GID` sem ligação segura com a requisição | com nível ≥ Info, ligação por GID (WFM-124/123 ↔ WFM-126/030), 405/406; em DebugLog continua sem chave |
| wfm-ont.md 5 e 6, pergunta 6 | suposto `InternalServerError` 5.3 em falha | confirmado: SOAP Fault `s:Client`, `level FATAL`, `code 5.3`, no mesmo ms de WFM-081 (17/17) |
| avl-osr-smn-siteswitch.md 6, pergunta 5 | formato do payload SOAP desconhecido | seções 3–4 deste documento |
| avl-osr-smn-siteswitch.md 6, pergunta 7 | corpo do `SystemError` do SAP PI não logado | continua não logado, mesmo em Verbose: a resposta é rejeitada pelo inspector antes do log da mensagem (só GEN-XSD-02 + exceção no sensitive) |
| wfm-ont.md 1 | timestamp volta 3 ms uma vez | 157 recuos: ≤ 16 ms no normal e até 2,1 s no sensitive do Smn |

## 12. Em aberto

1. Rotação: confirmar se o adapter verifica tamanho em hh:30 (limite em torno de 50–60 MB) e se o sensitive nunca rota (cobre desde 2026-08-28). Afeta o pior caso de volume.
2. Codificação do `_INTEGRATION_MESSAGE_CONTENT` (binário proprietário): só vale investigar se a proposta P3 for aceita.
3. Prefixo do `msg` do sensitive do Wfm em DebugLog (`… content:` + resumo) não mudou, mas o resumo convive com o payload completo nas janelas: confirmar com o Jefferson se o resumo deve ser exibido junto do payload ou escondido quando houver payload.

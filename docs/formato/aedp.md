# Formato de log: CustomAdapterChangeIncidentDevice (família AEDP, "ACID" no vault)

Base: ERS de 2026-10-08 (captura 1, zona `dnaApp_mpdmz/DMS_INTEGRATION`). Só essa captura traz o log do adapter: `CustomAdapterChangeIncidentDevice_<pid>.log`, sem `.sensitive.log` e sem `.log.0`. Contagens vêm de varredura completa por script; nenhum texto de log, GUID ou valor real é reproduzido aqui. Placeholders: `<msgid>`, `<n>`.

| Arquivo | Início | Fim | Linhas | Cabeçalhos | Continuações |
|---|---|---|---|---|---|
| CustomAdapterChangeIncidentDevice_<pid>.log | 2026-08-28 07:56:11 | 2026-10-08 12:16:34 | 46.541 | 10.914 | 35.627 |

Cobre o processo inteiro (do start até o fim da captura). Mesmo envelope dos demais adapters (`<ts> [PID=<n>][TID=0x<hex>] <NÍVEL> - <msg>`, ver `crm-e-demais.md`, seção 1); todos os cabeçalhos são `DEBUGLOG`, inclusive o erro.

## 1. O que foi descoberto

- Só **3 formas de mensagem são específicas do adapter**, todas no serviço `[ReceiveIncidentDeviceService]` (AEDP-001..003). O resto (startup, configuração, estatísticas Communication2, conexões de clientes, pool) já é coberto pela biblioteca `_common`.
- **Sem payload**: nenhuma mensagem traz o corpo do request/response (`MessageLogLevel=Info`, confirmado na linha de configuração `[MessageLogLevel] : Info` lida no startup). Não há stream sensitive; a URL do serviço aparece só como "written in sensitive log" (GEN-CFG-05).
- **Chave de correlação**: o `Id` da mensagem (`'<msgid>'`, entre aspas simples), igual no início e no fim. Em 2.931 ids, 2.918 terminam em `started` + `successfully finished` e 13 em `started` + `failed`, sempre na mesma TID; nenhum id ficou sem fim.
- **Falha com continuação**: o `failed.` (termina com espaço) é seguido de 4 linhas: `Reason: '<texto>'. `, `Full exception: '<tipo>: <texto>` e 2 frames `   at ...` nos namespaces `RequestProcessors` e `WebServices` do adapter. As 13 falhas têm a mesma forma (mesmo texto de razão, só muda o número do dispositivo). O parser `dotnet_stack` extrai o `frame`; o texto da razão não é campo.
- Volume: ~2,9 mil mensagens em ~41 dias (1 a cada ~20 min em média); o log é dominado pelas estatísticas Communication2 (a cada hora, ~4,9 mil linhas de cabeçalho e 35 mil de continuação).

## 2. Templates

Cobertura da família na amostra: antes 46,29% (só `_common`, 3 assinaturas desconhecidas = os três marcadores), depois 100% dos 10.914 registros, 0 assinaturas desconhecidas.

| ID | Template | Classe | Registros | Campos / chave |
|---|---|---|---|---|
| AEDP-001 | `[ReceiveIncidentDeviceService] - Processing message with Id '<msgid>' started.` | início | 2.931 | msg_id (chave `msgid`) |
| AEDP-002 | `[ReceiveIncidentDeviceService] - Processing message with Id '<msgid>' successfully finished.` | fim | 2.918 | msg_id (chave `msgid`) |
| AEDP-003 | `[ReceiveIncidentDeviceService] - Processing message with Id '<msgid>' failed. ` + `Reason: …`, `Full exception: …`, `   at …` | erro | 13 | msg_id (chave `msgid`), frame |

Operação `ReceiveIncidentDevice`: início AEDP-001, fim AEDP-002, falha AEDP-003, pareados por TID (e pelo `msg_id`).

Registros cobertos por `_common` nesta amostra (contagens): GEN-STA-01/02 (988 cada), GEN-STA-03 (2.964), GEN-CFG-05 (20), os códigos de startup da `_common` (56 registros no total), GEN-CON-01/02/03 (17), GEN-CFG-01 (5), GEN-CFG-07 (1), GEN-POOL-01 (1).

## 3. Relação com a documentação (ACID no vault)

| Documentado | O que o log mostra |
|---|---|
| Serviço `ReceiveIncidentDevice`, operação WSDL `ChangedIncidentDevice`, payload com `IncidentID` e `DeviceID` (ACID 6, 7) | O log nomeia só o serviço `[ReceiveIncidentDeviceService]`; a operação e os ids do payload não aparecem (sem payload). |
| Resposta `OK` ou `FAILED` com `Error.code`/`reason` (1.2, 2.7, 5.3 etc.) | `successfully finished` e `failed` correspondem às duas saídas, mas o log **não imprime** o código nem o `reason` do erro documentado. |
| Parâmetros `MessageLogLevel`, `DoMessageXsdValidation`, `AuditingType`, `AuditingPeriod` etc. (ACID Configuration) | Aparecem na lista de propriedades lida no startup (GEN-CFG-05), com os valores da configuração em uso. |
| Nome do adapter: `CustomAdapterChangeIncidentDevice` (ACID Index, alias) | Confirmado pelo nome do arquivo de log, pelo Registry e pelas mensagens `Starting ...` do startup. Fecha a lacuna "relação entre o nome e o arquivo de log" da nota de lacunas do vault. |

## 4. O que continua sem documentação

- Texto da razão da falha amostrada (dispositivo inexistente no modelo do ADMS): **não consta** entre os erros do vault (que documentam `DeviceNotExistInList` e `EntityNotFound`). Essa falha vem de uma exceção não tratada do processador, não de um código da planilha de erros; mapear essa falha a um código `Error.code` exige o log do lado do cliente ou o payload.
- Divergência de códigos entre o design document e a planilha de configuração (1.2 × 2.7 etc.): não resolvível pelo log, que não imprime o código.
- Formatos de log de `InvalidMessage`, `InvalidHeaderNoun`/`Verb`, `OperationNotAllowed`, `MissingIncidentId`, `MissingDeviceId`: **não observados** na amostra; se aparecerem como outra forma de mensagem, cairão em registros desconhecidos.
- Auditoria (`AuditingType` aparece como `None` na configuração da amostra): as mensagens de auditoria da `_common` não ocorrem.

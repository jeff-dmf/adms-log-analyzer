# Inventário de textos da UI (UI-09, #149)

Escopo: `src/dmslog/web/js/strings.js`. Cada chave com texto visível aparece numa linha. "Texto atual" é o valor literal da chave; "Texto proposto" é "=" quando já segue as regras. Textos propostos em inglês. A tabela foi gerada por script a partir do próprio arquivo, não transcrita à mão.

Esta nota não altera `src/`. A aplicação em `strings.js` e nos testes é da sessão principal (ponto compartilhado da #149). Fora do escopo: textos de CLI e API em `src/dmslog/messages.py`.

## Regras aplicadas

| Código | Regra |
|---|---|
| R1 | **record** no lugar de "entry/entries" quando se trata de linha do log. |
| R2 | **message type** no lugar de "template" na UI. |
| R3 | **operations** no lugar de "event(s)" quando se trata do painel de operações e dos seus textos (ver dúvida 1). |
| R4 | Reticências "…" (U+2026) no lugar de "...". |
| R5 | Botão que abre painel com botão Fechar termina em "…" (ver dúvida 3). |
| R6 | Severidade e nível: "At least {name}" no lugar de "{name} or worse"; "At most" no lugar de "or milder". |
| R7 | Plural: "(s)" e contagem com substantivo viram o par `fooOne` / `fooOther`, escolhido por `pluralKey(base, n)` (ver seção "Chaves novas"). |
| R8 | Código interno cru ganha frase própria (ver seção "Chaves novas", `plReasonNames`). |
| R9 | Erro: o que aconteceu, motivo quando houver, e o que fazer ("Try again." ou "Press Refresh to try again."). |
| R10 | Botão de ação solta ganha objeto (verbo + objeto) (ver dúvida 4). |
| R11 | "entries" de pasta e de arquivo vira "items". |
| R12 | Menu de tema: "Theme" com "System", "Light" e "Dark". |
| R13 | Fim de indexação: "Indexed {records} records." (a chave recebe `records`). |
| R14 | Estado vazio: o que é, por que está vazio e como começar. |
| R15 | Clareza: termo de jargão vira palavra comum ("curated" vira "Built-in"). |
| literal de log | Valor que vem do log (ex.: `levelNames.1` a `levelNames.8`: FATAL, EXIT, ...) não muda. |

R8 aparece só nas chaves novas (seção abaixo). Nenhum texto de log dos adapters é alterado.

## Contagens

Contagens por script e por grep sobre a coluna de valores de `strings.js`.

| Medida | Antes (hoje) | Depois (proposto) |
|---|---|---|
| Chaves folha em `strings.js` | 1305 | 1305 (+ chaves novas, ver abaixo) |
| Sem texto visível (valor vazio: `severityNames.0`, `levelNames.0`) | 2 | 2 |
| Com texto visível (= linhas da tabela) | 1303 | 1303 |
| Chaves que mudam | — | 213 |
| Chaves base de plural (cada uma vira `One` e `Other`) | — | 69 |
| Ocorrências de "(s)" | 17 | 0 |
| Ocorrências de "..." | 30 | 0 |
| Ocorrências de "entry" / "entries" (sem distinguir caixa) | 25 | 0 |
| Ocorrências de "template" (sem distinguir caixa) | 16 | 0 |
| Ocorrências de "event" / "events" | 22 | 1 (só o nome do parâmetro `{events}`) |
| Ocorrências de "or worse" | 4 | 0 |
| Ocorrências de "record" | 79 | 129 (termo único) |

Não há "1 records" literal em `strings.js`: o risco está nas 69 chaves com contagem e substantivo (`keyCount`, `progressLine`, `gridCount` e as demais), que hoje mostram "1 records" quando o número é 1.

## Tabela

Legenda da coluna Regra: códigos acima; `—` = sem mudança. Par plural: `One` e `Other` separados por `·`.

| Chave | Texto atual | Texto proposto | Regra |
|---|---|---|---|
| `appName` | dmslog | = | — |
| `panelSources` | Sources | = | — |
| `panelGrid` | Log entries | Records | R1 |
| `panelDetail` | Entry detail | Record detail | R1 |
| `searchLabel` | Search | = | — |
| `searchPlaceholder` | Search text (words, "phrase", prefix*) | = | — |
| `searchButton` | Search | = | — |
| `clearButton` | Clear | Clear search | R10 |
| `themeToggle` | Toggle theme | Theme | R12 |
| `themeLight` | Theme: light | Light | R12 |
| `themeDark` | Theme: dark | Dark | R12 |
| `columnsButton` | Columns | = | — |
| `gotoLabel` | Go to time | = | — |
| `gotoPlaceholder` | YYYY-MM-DD HH:MM:SS | = | — |
| `gotoButton` | Go | Go to time | R10 |
| `loading` | Loading... | Loading… | R4 |
| `caseLabel` | Case | = | — |
| `caseNone` | (no case) | = | — |
| `newCaseLabel` | New case name | = | — |
| `newCaseButton` | Create case | = | — |
| `pathLabel` | ERS zip, folder or file | = | — |
| `pathPlaceholder` | Path to an ERS zip, folder or file | = | — |
| `scopeLabel` | Scope | = | — |
| `scopeDefault` | Default logs | = | — |
| `scopeAll` | All logs | = | — |
| `openButton` | Open | Open path | R10 |
| `needCase` | Create or select a case first. | = | — |
| `needPath` | Enter the path of a zip, folder or file. | = | — |
| `collectionStarted` | Collection started. | = | — |
| `progressLabel` | Indexing progress | = | — |
| `progressLine` | {source}: {records} records | `One: {source}: {records} record` · `Other: {source}: {records} records` | R7 |
| `progressDone` | Indexing finished. | `One: Indexed {records} record.` · `Other: Indexed {records} records.` | R13 R7 |
| `progressFailed` | Indexing failed | Indexing failed. Check the file and try again. | R9 |
| `conflict_job_running` | The case is still indexing another file. Try again when the progress bar finishes. | = | — |
| `conflict_duplicate_collection` | The case already has this ERS file from another location (same capture), so this copy was not added. | = | — |
| `conflict_generic` | This collection conflicts with the one already in the case. | This collection conflicts with one already in the case. Remove the other one or use a different capture. | R9 |
| `sourcesEmpty` | No sources yet. Open an ERS to start. | = | — |
| `sourcesAll` | All sources | = | — |
| `sourceMeta` | {family} - pid {pid} - {records} records | `One: {family} - pid {pid} - {records} record` · `Other: {family} - pid {pid} - {records} records` | R7 |
| `sourceSensitive` | sensitive | = | — |
| `filterSource` | Source | = | — |
| `filterSeverity` | Severity | = | — |
| `filterLevel` | Level | = | — |
| `filterFrom` | From (log time) | = | — |
| `filterTo` | To (log time) | = | — |
| `filterTimePlaceholder` | YYYY-MM-DD HH:MM:SS | = | — |
| `filterAny` | Any | = | — |
| `filterApply` | Apply | = | — |
| `filterReset` | Reset | = | — |
| `filterHideNoise` | Hide noise | = | — |
| `filterUnclassified` | Unclassified only | = | — |
| `toolbarFilterTree` | Filter tree | = | — |
| `toolbarUnknown` | Unknown templates | Unknown message types | R2 |
| `toolbarConfig` | Effective configuration | = | — |
| `tplAddFilter` | Filter by template {code} | Filter by message type {code} | R2 |
| `navNone` | No more matching entries. | No more matching records. | R1 |
| `navNoTemplate` | The selected entry has no template. | This record has no message type. | R1 R2 |
| `navNoSelection` | Select an entry first. | Select a record first. | R1 |
| `filterBadTime` | Invalid time. Use YYYY-MM-DD HH:MM:SS (log time). | = | — |
| `severityNames.1` | Critical | = | — |
| `severityNames.2` | Error | = | — |
| `severityNames.3` | Warning | = | — |
| `severityNames.4` | Info | = | — |
| `severityNames.5` | Noise | = | — |
| `levelNames.1` | FATAL | = | literal de log |
| `levelNames.2` | EXIT | = | literal de log |
| `levelNames.3` | ERROR | = | literal de log |
| `levelNames.4` | ALWAYS | = | literal de log |
| `levelNames.5` | DEBUGLOG | = | literal de log |
| `levelNames.6` | WARNING | = | literal de log |
| `levelNames.7` | INFO | = | literal de log |
| `levelNames.8` | VERBOSE | = | literal de log |
| `severityAtLeast` | {name} or worse | At least {name} | R6 |
| `levelAtLeast` | {name} or worse | At least {name} | R6 |
| `columnTs` | Time | = | — |
| `columnPid` | PID | = | — |
| `columnTid` | TID | = | — |
| `columnLevel` | Level | = | — |
| `columnSource` | Source | = | — |
| `columnMessage` | Message | = | — |
| `columnTemplate` | Template | Message type | R2 |
| `columnsFieldsHeading` | Parsed fields | = | — |
| `columnsFieldsEmpty` | Select an entry to list its parsed fields. | Select a record to list its parsed fields. | R1 |
| `gridEmpty` | No entries match. | No records match the current filter. Clear a filter to see more. | R1 R14 |
| `gridCount` | {count} entries | `One: {count} record` · `Other: {count} records` | R1 R7 |
| `gridCountApprox` | about {count} entries | `One: about {count} record` · `Other: about {count} records` | R1 R7 |
| `gridPosition` | Showing from {time} | = | — |
| `extraLines` | +{count} lines | `One: +{count} line` · `Other: +{count} lines` | R7 |
| `rowLabel` | Entry {id} | Record {id} | R1 |
| `followLabel` | Follow tail | = | — |
| `followNew` | {count} new records | `One: {count} new record` · `Other: {count} new records` | R7 |
| `detailEmpty` | Select an entry to see its full text. | Select a record to see its full text. | R1 |
| `detailMeta` | {time} - pid {pid} - tid {tid} - {level} - {source} | = | — |
| `contextButton` | Context | = | — |
| `contextSame` | Same | = | — |
| `contextSameSource` | Same source | = | — |
| `contextSameTid` | Same thread | = | — |
| `contextSameMerge` | All sources | = | — |
| `contextClose` | Close context | = | — |
| `contextTitle` | Context around the entry | Context around the record | R1 |
| `copyButton` | Copy text | = | — |
| `copied` | Copied. | = | — |
| `detailTemplate` | Template | Message type | R2 |
| `detailFieldsTitle` | Parsed fields | = | — |
| `detailFieldsEmpty` | No parsed fields. | = | — |
| `detailFieldName` | Field | = | — |
| `detailFieldValue` | Value | = | — |
| `ftTitle` | Filter tree | = | — |
| `ftTreeLabel` | Filter conditions | = | — |
| `ftOpAnd` | AND - all must match | = | — |
| `ftOpOr` | OR - any may match | = | — |
| `ftOpNot` | NOT - none may match | = | — |
| `ftGroupLabel` | {op} group | = | — |
| `ftOpShortAnd` | AND | = | — |
| `ftOpShortOr` | OR | = | — |
| `ftOpShortNot` | NOT | = | — |
| `ftOperator` | Group operator | = | — |
| `ftAddCondition` | Add condition | = | — |
| `ftAddGroup` | Add group | = | — |
| `ftConditionType` | Condition type | = | — |
| `ftDuplicate` | Duplicate | = | — |
| `ftWrapNot` | Wrap in NOT | = | — |
| `ftWrapAnd` | Group (AND) | = | — |
| `ftUngroup` | Ungroup | = | — |
| `ftMoveUp` | Move up | = | — |
| `ftMoveDown` | Move down | = | — |
| `ftRemove` | Remove | = | — |
| `ftUndo` | Undo | = | — |
| `ftRedo` | Redo | = | — |
| `ftClear` | Clear all | = | — |
| `ftApply` | Apply filter | = | — |
| `ftEmpty` | No conditions yet. Add a condition or a group. | = | — |
| `ftTypeSource` | Source | = | — |
| `ftTypeSev` | Severity | = | — |
| `ftTypeLevel` | Level | = | — |
| `ftTypeTs` | Time window | = | — |
| `ftTypeText` | Text | = | — |
| `ftTypeTpl` | Template | Message type | R2 |
| `ftTplLabel` | Template ids or codes | Message type ids or codes | R2 |
| `ftTplPlaceholder` | ids or codes, comma separated | = | — |
| `ftTypeRaw` | Other condition | = | — |
| `ftSources` | Sources | = | — |
| `ftSourceOption` | {name} (#{id}) | = | — |
| `ftSourceIdsLabel` | Source ids (comma separated) | = | — |
| `ftSourceIdsPlaceholder` | 3, 4 | = | — |
| `ftCompare` | Comparison | = | — |
| `ftValue` | Value | = | — |
| `ftOrWorse` | or worse | At least | R6 |
| `ftOrBetter` | or milder | At most | R6 |
| `ftExactly` | exactly | = | — |
| `ftFrom` | From (log time) | = | — |
| `ftTo` | To (log time) | = | — |
| `ftTextLabel` | Search text | = | — |
| `ftTextPlaceholder` | words, "phrase", prefix* | = | — |
| `ftRawLabel` | Kept as is (no form for it yet) | = | — |
| `ftErrorEmptyList` | Choose at least one value. | = | — |
| `ftErrorListTooLong` | Too many values. | = | — |
| `ftErrorBadValue` | Invalid value. | = | — |
| `ftErrorEmptyRange` | Enter a start or an end time. | = | — |
| `ftErrorBadRange` | The start is after the end. | = | — |
| `ftErrorBadTime` | Invalid time. Use YYYY-MM-DD HH:MM:SS (log time). | = | — |
| `ftErrorEmptyText` | Enter a word to search. | = | — |
| `ftErrorTextTooLong` | The text is too long. | = | — |
| `ftLimitDepth` | The filter is nested deeper than {limit} levels. | `One: The filter is nested deeper than {limit} level.` · `Other: The filter is nested deeper than {limit} levels.` | R7 |
| `ftLimitNodes` | The filter has more than {limit} nodes ({nodes}). | `One: The filter has more than {limit} node ({nodes}).` · `Other: The filter has more than {limit} nodes ({nodes}).` | R7 |
| `ftFixErrors` | Fix the highlighted conditions to apply. | = | — |
| `ftNodesInfo` | {nodes} nodes, {depth} levels | `One: Depth {depth}, 1 node` · `Other: Depth {depth}, {nodes} nodes` | R7 |
| `ftNoConditions` | No conditions: every entry matches. | No conditions: every record matches. | R1 |
| `ftCountLoading` | Counting... | Counting… | R4 |
| `ftCountFailed` | Count failed | Count failed. Try again. | R9 |
| `ftApplied` | Filter applied. | = | — |
| `svTitle` | Saved filters | = | — |
| `svName` | Name | = | — |
| `svNamePlaceholder` | Name of this filter | = | — |
| `svSave` | Save | Save filter | R10 |
| `svSaved` | Saved. | = | — |
| `svEmpty` | No saved filters in this case. | = | — |
| `svApply` | Apply {name} | = | — |
| `svDelete` | Delete | Delete filter | R10 |
| `svDeleteNamed` | Delete {name} | = | — |
| `svConfirmDelete` | Confirm delete | = | — |
| `svCreated` | saved {date} | = | — |
| `svNeedFilter` | Build a valid filter with at least one condition first. | = | — |
| `svErrorEmpty` | Enter a name. | = | — |
| `svErrorTooLong` | The name is too long (at most {limit} characters). | = | — |
| `svErrorDuplicate` | A saved filter with this name already exists. | = | — |
| `svErrorTooMany` | This case already has the maximum of {limit} saved filters. | `One: This case already has the maximum of 1 saved filter.` · `Other: This case already has the maximum of {limit} saved filters.` | R7 |
| `svLoadFailed` | Could not load the saved filters | Could not load the saved filters. Try again. | R9 |
| `timelineLabel` | Timeline | = | — |
| `timelineSplitLabel` | Split by | = | — |
| `timelineSplitSev` | Severity | = | — |
| `timelineSplitSource` | Source | = | — |
| `timelineSplitTpl` | Template | Message type | R2 |
| `timelineLoading` | Loading timeline... | Loading timeline… | R4 |
| `timelineEmpty` | No dated entries match. | No dated records match the current filter. | R1 R14 |
| `timelineTotal` | {count} dated entries, {width} per bar | `One: {count} dated record, {width} per bar` · `Other: {count} dated records, {width} per bar` | R1 R7 |
| `timelineTotalApprox` | about {count} dated entries, {width} per bar (estimate) | `One: about {count} dated record, {width} per bar (estimate)` · `Other: about {count} dated records, {width} per bar (estimate)` | R1 R7 |
| `timelineHint` | Drag to select a time range, double-click to clear. | = | — |
| `timelineReadout` | {time}: {parts} | = | — |
| `timelineReadoutNone` | {time}: no entries | {time}: no records | R1 |
| `timelineSourceLabel` | Source {id} | = | — |
| `timelineTplLabel` | Template {id} | Message type {id} | R2 |
| `timelineTplNone` | No template | No message type | R2 |
| `timelineOther` | Other | = | — |
| `timelineError` | Timeline failed: {error} | Could not load the timeline: {error} | R9 |
| `widthSeconds` | {n} s | = | — |
| `widthMinutes` | {n} min | = | — |
| `widthHours` | {n} h | = | — |
| `widthDays` | {n} d | = | — |
| `errorGeneric` | Request failed | Request failed. Try again. | R9 |
| `errorForbidden` | Session not valid. Reopen the link printed by the dmslog server. | = | — |
| `errorNetwork` | Could not reach the server. | Could not reach the server. Check that dmslog serve is still running. | R9 |
| `errorSearch` | Search failed | Search failed. Check the search text and try again. | R9 |
| `unkTitle` | Unknown templates | Unknown message types | R2 |
| `unkFilterPlaceholder` | Filter signatures | = | — |
| `unkSortLabel` | Sort by | = | — |
| `unkSortCount` | Count | = | — |
| `unkSortSignature` | Signature | = | — |
| `unkColSignature` | Signature | = | — |
| `unkColCount` | Records | = | — |
| `unkColShare` | Share | = | — |
| `unkOpenExample` | Open example | = | — |
| `unkCopySketch` | Copy as TOML sketch | = | — |
| `unkCopied` | Sketch copied. | = | — |
| `unkCopyFailed` | Could not copy. Select the text below and copy it manually. | = | — |
| `unkSummary` | {shown} of {total} signatures, {records} unclassified records | `One: {shown} of {total} signatures, {records} unclassified record` · `Other: {shown} of {total} signatures, {records} unclassified records` | R7 |
| `unkEmpty` | No unknown signatures. | = | — |
| `unkLoading` | Loading... | Loading… | R4 |
| `unkError` | Could not load unknown signatures | Could not load unknown signatures. Press Refresh to try again. | R9 |
| `unkRefresh` | Refresh | = | — |
| `unkNoCase` | Open a case first. | = | — |
| `unkFallbackLabel` | TOML sketch (select and copy) | = | — |
| `knowTitle` | Knowledge | = | — |
| `knowAdapter` | Adapter: {title} | = | — |
| `knowHint` | What it means | = | — |
| `knowParams` | Related parameters | = | — |
| `knowParamName` | Parameter | = | — |
| `knowParamDefault` | Default | = | — |
| `knowParamDescription` | Description | = | — |
| `knowNoDefault` | (none) | = | — |
| `knowNoDescription` | (no description in the vault) | = | — |
| `knowOutOfVault` | Out of vault | = | — |
| `knowOutOfVaultTitle` | Defined by the ERS ErrorConfiguration, not documented in the vault | = | — |
| `knowErrors` | Error codes | = | — |
| `knowErrorLine` | {code}: {severity} ({source}) | = | — |
| `knowSourceErs` | ERS configuration | = | — |
| `knowSourceVault` | vault | = | — |
| `knowSeverityUnknown` | severity unknown | = | — |
| `knowReason` | Reason | = | — |
| `knowDetails` | Details | = | — |
| `knowNote` | Vault note | = | — |
| `knowCopyPath` | Copy path | = | — |
| `knowCopyPathLabel` | Copy the vault note path | = | — |
| `cfgTitle` | Effective configuration | = | — |
| `cfgProduct` | Product | = | — |
| `cfgProductOption` | {product} (group {group}, {keys} keys) | = | — |
| `cfgFilterPlaceholder` | Filter keys | = | — |
| `cfgOnlyDiverging` | Only divergences | = | — |
| `cfgRefresh` | Refresh | = | — |
| `cfgNoCase` | Open a case first. | = | — |
| `cfgLoading` | Loading... | Loading… | R4 |
| `cfgNoProducts` | No stored configuration in this case (no Registry in the collection?). | No configuration found: the ERS has no Registry export. | R9 |
| `cfgError` | Could not load the configuration | Could not load the configuration. Press Refresh to try again. | R9 |
| `cfgSummary` | {shown} of {total} keys, {diverging} diverging | `One: {shown} of {total} key, {diverging} diverging` · `Other: {shown} of {total} keys, {diverging} diverging` | R7 |
| `cfgLogLevel` | Log level in force: {value} ({source}) | = | — |
| `cfgLogLevelUnknown` | Log level in force: unknown | = | — |
| `cfgLogLevelObserved` | logged by the process | = | — |
| `cfgLogLevelRegistry` | Registry | = | — |
| `cfgKeysCaption` | Keys | = | — |
| `cfgColKey` | Key | = | — |
| `cfgColValue` | Effective value | = | — |
| `cfgColSource` | Source (file, layer) | = | — |
| `cfgColStatus` | Status | = | — |
| `cfgColObserved` | Observed in the log | = | — |
| `cfgColDivergence` | Divergence | = | — |
| `cfgNoValue` | (none) | = | — |
| `cfgNotObserved` | not logged | = | — |
| `cfgObservedDefault` | {value} (default) | = | — |
| `cfgOpenObserved` | Open entry | Open record | R1 |
| `cfgStatusOnly` | Only value | = | — |
| `cfgStatusWins` | Wins | = | — |
| `cfgStatusOverridden` | Overridden | = | — |
| `cfgStatusAmbiguous` | Ambiguous | = | — |
| `cfgStatusUnknown` | Unknown | = | — |
| `cfgDivergesYes` | Diverges | = | — |
| `cfgDivergesNo` | Matches | = | — |
| `cfgDivergesNa` | Not compared | = | — |
| `cfgLayerLine` | layer {layer} | = | — |
| `cfgLayers` | Layers of {key} | = | — |
| `cfgLayerColLayer` | Layer | = | — |
| `cfgLayerColFile` | File | = | — |
| `cfgLayerColValue` | Value | = | — |
| `cfgLayerColStatus` | Status | = | — |
| `cfgExpand` | Show layers | = | — |
| `cfgCollapse` | Hide layers | = | — |
| `cfgExpandLabel` | Layers of {key} | = | — |
| `cfgOpsCaption` | Operations | = | — |
| `cfgOpsEmpty` | No operations in the WebServiceConfiguration. | = | — |
| `cfgOpColOperation` | Operation | = | — |
| `cfgOpColLevel` | MessageLogLevel | = | — |
| `cfgOpColPayload` | Payload expected | = | — |
| `cfgOpColReason` | Reason | = | — |
| `cfgOpColSource` | Source | = | — |
| `cfgLevelNotSet` | (not set) | = | — |
| `cfgPayloadYes` | Yes | = | — |
| `cfgPayloadNo` | No | = | — |
| `cfgPayloadUnknown` | Unknown | = | — |
| `cfgReasonNotSet` | MessageLogLevel is not set for this operation. | = | — |
| `cfgReasonNever` | MessageLogLevel is None: the payload is never logged. | = | — |
| `cfgReasonNoLogLevel` | The log level in force is unknown (no Registry value and none logged). | = | — |
| `cfgReasonBadMessageLevel` | MessageLogLevel {value} is not a known level (DebugLog < Warning < Info < Verbose). | = | — |
| `cfgReasonBadLogLevel` | Log level {value} is not a known level (DebugLog < Warning < Info < Verbose). | = | — |
| `cfgReasonEnough` | Log level {log} is at least MessageLogLevel {message}. | = | — |
| `cfgReasonTooLow` | Log level {log} is below MessageLogLevel {message}. | = | — |
| `cfgWindowsLine` | Payload expected in {n} of {total} window(s) | `One: Payload expected in {n} of {total} window` · `Other: Payload expected in {n} of {total} windows` | R7 |
| `cfgWindowsOmitted` | ({n} more window(s) omitted) | `One: ({n} more window omitted)` · `Other: ({n} more windows omitted)` | R7 |
| `cfgWindowsMore` | (+{n} more) | = | — |
| `cfgWindowOpen` | open | = | — |
| `toolbarEvents` | Operations | = | — |
| `toolbarDashboard` | Adapter dashboard | = | — |
| `evTitle` | Operations | = | — |
| `evKind` | Kind | = | — |
| `evStatus` | Status | = | — |
| `evStatusAny` | Any status | = | — |
| `evName` | Name | = | — |
| `evNamePlaceholder` | Operation name | = | — |
| `evSearch` | Search | = | — |
| `evSearchPlaceholder` | Key or text | = | — |
| `evRefresh` | Refresh | = | — |
| `evNoCase` | Open a case first. | = | — |
| `evLoading` | Loading... | Loading… | R4 |
| `evError` | Could not load the events | Could not load the operations. Press Refresh to try again. | R3 R9 |
| `evEmpty` | No events match. | No operations match the current filter. | R3 R14 |
| `evCount` | {count} events shown | `One: {count} operation shown` · `Other: {count} operations shown` | R3 R7 |
| `evCountMore` | {count} events shown, more available | `One: {count} operation shown, more available` · `Other: {count} operations shown, more available` | R3 R7 |
| `evLoadMore` | Load more | = | — |
| `evLoadingMore` | Loading more... | Loading more… | R4 |
| `evTableCaption` | Derived events | Operations derived from the log | R3 |
| `evColTime` | Time | = | — |
| `evColProcess` | Family / process | = | — |
| `evColName` | Name | = | — |
| `evColKey` | Key | = | — |
| `evColDuration` | Duration | = | — |
| `evColStatus` | Status | = | — |
| `evColEntries` | Entries | Records | R1 |
| `evKindNames.operation` | Operation | = | — |
| `evKindNames.exception` | Exception | = | — |
| `evKindNames.retry_seq` | Retry sequence | = | — |
| `evKindNames.restart` | Restart | = | — |
| `evKindNames.startup` | Startup | = | — |
| `evKindNames.level_window` | Log level window | = | — |
| `evKindNames.gap` | Gap | = | — |
| `evKindNames.clock_jump` | Clock jump | = | — |
| `evKindNames.service_state` | Service state | = | — |
| `evKindNames.map_reload` | Map reload | = | — |
| `evKindNames.stats_block` | Statistics block | = | — |
| `evStatusNames.ok` | OK | = | — |
| `evStatusNames.business_error` | Business error | = | — |
| `evStatusNames.fail` | Failed | = | — |
| `evStatusNames.timeout` | Timeout | = | — |
| `evStatusNames.open` | Open | = | — |
| `evStatusNames.truncated` | Truncated | = | — |
| `evStatusNone` | - | = | — |
| `evDetailTitle` | Event detail | Operation detail | R3 |
| `evDetailNone` | Select an event to see its detail. | Select an operation to see its detail. | R3 |
| `evDetailLoading` | Loading the event... | Loading the operation… | R3 R4 |
| `evDetailError` | Could not load the event | Could not load the operation. Try again. | R3 R9 |
| `evFieldKind` | Kind | = | — |
| `evFieldName` | Name | = | — |
| `evFieldKey` | Key | = | — |
| `evFieldFamily` | Family | = | — |
| `evFieldProcess` | Process | = | — |
| `evFieldStream` | Stream | = | — |
| `evFieldTid` | TID | = | — |
| `evFieldStart` | Start | = | — |
| `evFieldEnd` | End | = | — |
| `evFieldDuration` | Duration | = | — |
| `evFieldStatus` | Status | = | — |
| `evFieldEntries` | Entries | Records | R1 |
| `evFieldSensitive` | Sensitive log | = | — |
| `evFieldDetail` | Detail: {name} | = | — |
| `evYes` | Yes | = | — |
| `evNo` | No | = | — |
| `evShowInGrid` | Show in grid | = | — |
| `evEntriesTitle` | Entries of the event | Records of the operation | R1 R3 |
| `evEntriesNone` | No entries are linked to this event. | No records are linked to this operation. | R1 R3 |
| `evEntriesPartial` | Showing {shown} of {total} entries. | `One: Showing {shown} of {total} record.` · `Other: Showing {shown} of {total} records.` | R1 R7 |
| `evEntryColTime` | Time | = | — |
| `evEntryColTid` | TID | = | — |
| `evEntryColLevel` | Level | = | — |
| `evEntryColText` | Text | = | — |
| `evRelatedTitle` | Related events | Related operations | R3 |
| `evRelatedNone` | No related events. | No related operations. | R3 |
| `evOpenRelated` | Open {kind} {name} | = | — |
| `evDurationMs` | {n} ms | = | — |
| `evDurationS` | {n} s | = | — |
| `evDurationMin` | {m} min {s} s | = | — |
| `evDurationH` | {h} h {m} min | = | — |
| `evUnnamed` | (no name) | = | — |
| `dashTitle` | Adapter dashboard | = | — |
| `dashRefresh` | Refresh | = | — |
| `dashNoCase` | Open a case first. | = | — |
| `dashLoading` | Loading... | Loading… | R4 |
| `dashSectionError` | Could not load this section | Could not load this section. Press Refresh to try again. | R9 |
| `dashEmpty` | Nothing to show. | No statistics for this case yet. Index an ERS to fill the dashboard. | R14 |
| `dashStatsTitle` | Operations by adapter and name | = | — |
| `dashStatsCaption` | Operation statistics | = | — |
| `dashColFamily` | Family | = | — |
| `dashColName` | Operation | = | — |
| `dashColCount` | Total | = | — |
| `dashColOk` | OK | = | — |
| `dashColBusiness` | Business error | = | — |
| `dashColFail` | Failed | = | — |
| `dashColTimeout` | Timeout | = | — |
| `dashColOpen` | Open | = | — |
| `dashColP50` | p50 | = | — |
| `dashColP90` | p90 | = | — |
| `dashColP99` | p99 | = | — |
| `dashColMax` | Max | = | — |
| `dashTotals` | All operations | = | — |
| `dashPickName` | Chart {name} | = | — |
| `dashHistTitle` | Operation status over time | = | — |
| `dashHistAll` | All operations | = | — |
| `dashHistFor` | Operation: {name} | = | — |
| `dashHistClear` | All operations | = | — |
| `dashHistSeries.ok` | OK | = | — |
| `dashHistSeries.business_error` | Business error | = | — |
| `dashHistSeries.fail` | Failed | = | — |
| `dashHistSeries.open` | Open | = | — |
| `dashHistSeries.truncated` | Truncated | = | — |
| `dashHistSeries.other` | Other | = | — |
| `dashHistReadout` | {time}: {parts} | = | — |
| `dashHistReadoutNone` | {time}: no operations | = | — |
| `dashHistHint` | Hover the chart to read a bucket. Click a legend item to hide or show it. | = | — |
| `dashMethodsTitle` | Calls by method | = | — |
| `dashSide` | Method | = | — |
| `dashMetric` | Metric | = | — |
| `dashMode` | Values | = | — |
| `dashModeCumulative` | Cumulative | = | — |
| `dashModeDelta` | Per sample | = | — |
| `dashMetricCalls` | Calls | = | — |
| `dashMetricReq` | Request bytes | = | — |
| `dashMetricResp` | Response bytes | = | — |
| `dashMethodOption` | {side}: {method} ({calls}) | = | — |
| `dashMethodNone` | No statistics samples in this case. | = | — |
| `dashSeriesEmpty` | No samples for this method. | = | — |
| `dashSeriesLoading` | Loading samples... | Loading samples… | R4 |
| `dashExceptionsTitle` | Exceptions by signature | = | — |
| `dashExceptionsCaption` | Exception signatures | = | — |
| `dashColSignature` | Signature | = | — |
| `dashColOccurrences` | Count | = | — |
| `dashColFirst` | First | = | — |
| `dashColLast` | Last | = | — |
| `dashNoSignature` | (no key) | = | — |
| `dashShowFirst` | Show first | = | — |
| `dashShowLast` | Show last | = | — |
| `dashLevelsTitle` | Log level windows | = | — |
| `dashLevelsCaption` | Log level windows | = | — |
| `dashColLevel` | Level | = | — |
| `dashColFrom` | From | = | — |
| `dashColTo` | To | = | — |
| `dashColDuration` | Duration | = | — |
| `dashLifecycleTitle` | Startups, restarts and gaps | = | — |
| `dashLifecycleCaption` | Startups, restarts and gaps | = | — |
| `dashColKind` | Kind | = | — |
| `dashColWhen` | When | = | — |
| `dashColWhat` | What | = | — |
| `dashClockSuspect` | possible clock change | = | — |
| `dashMore` | More events exist than shown here. | More operations exist than shown here. | R3 |
| `dashOpenInGrid` | Show {time} in grid | = | — |
| `clkBannerOne` | Clock jump detected at {time}. Durations and ordering around it are not reliable. | = | — |
| `clkBannerMany` | {count} clock jumps detected, first at {time}. Durations and ordering around it are not reliable. | = | — |
| `clkBannerManyMore` | More than {count} clock jumps detected, first at {time}. Durations and ordering around it are not reliable. | = | — |
| `clkBannerPossible` | Possible clock change (a gap of about one hour in every log), first at {time}. Durations and ordering around it are not reliable. | = | — |
| `clkShow` | Show in grid | = | — |
| `lnkTitle` | Links | = | — |
| `lnkKeysTitle` | Keys | = | — |
| `lnkSameKey` | Same key | = | — |
| `lnkSameKeyChip` | Same key: {type} = {value} | = | — |
| `lnkLoading` | Loading links... | Loading links… | R4 |
| `lnkNone` | No linked records. | = | — |
| `lnkError` | Could not load the links | Could not load the links. Try again. | R9 |
| `lnkMore` | Showing the first {count} links. | `One: Showing the first {count} link.` · `Other: Showing the first {count} links.` | R7 |
| `lnkListLabel` | Linked records | = | — |
| `lnkKindNames.normal_sensitive` | normal / sensitive | = | — |
| `lnkKindNames.callback` | callback | = | — |
| `lnkKindNames.retry` | retry | = | — |
| `lnkKindNames.response` | response | = | — |
| `lnkKindNames.same_key` | same key | = | — |
| `lnkConfidenceNames.0` | window (heuristic) | = | — |
| `lnkConfidenceNames.1` | second | = | — |
| `lnkConfidenceNames.2` | ts+TID | = | — |
| `lnkConfidenceNames.3` | key | = | — |
| `lnkConfidenceUnknown` | unknown | = | — |
| `lnkConfidence` | Confidence: {name} | = | — |
| `lnkSideNormal` | normal | = | — |
| `lnkSideSensitive` | sensitive | = | — |
| `lnkSideUnknown` | unknown source | = | — |
| `lnkShowInSensitive` | Show in sensitive | = | — |
| `lnkShowInNormal` | Show in normal | = | — |
| `lnkShowLinked` | Show in grid | = | — |
| `lnkSideBySide` | Side by side | = | — |
| `lnkPreview` | Preview | = | — |
| `lnkPreviewHide` | Hide preview | = | — |
| `lnkPreviewLoading` | Loading... | Loading… | R4 |
| `lnkPreviewError` | Could not load the record | Could not load the record. Try again. | R9 |
| `lnkPreviewMore` | (first {count} lines) | `One: (first {count} line)` · `Other: (first {count} lines)` | R7 |
| `lnkTid` | TID {tid} | = | — |
| `sbsTitle` | Side by side | = | — |
| `sbsClose` | Close | = | — |
| `sbsLoading` | Loading... | Loading… | R4 |
| `sbsError` | Could not load the records | Could not load the records. Try again. | R9 |
| `sbsNone` | This record has no linked counterpart. | = | — |
| `sbsNoCase` | Open a case first. | = | — |
| `sbsKind` | Link: {kind}, confidence {confidence} | = | — |
| `sbsPaneNormal` | Normal | = | — |
| `sbsPaneSensitive` | Sensitive | = | — |
| `sbsPaneOther` | Record | = | — |
| `sbsSource` | Source | = | — |
| `sbsTime` | Time | = | — |
| `sbsTid` | TID | = | — |
| `sbsTemplate` | Template | Message type | R2 |
| `sbsText` | Text | = | — |
| `sbsCopy` | Copy text | = | — |
| `sbsCopied` | Copied. | = | — |
| `sbsTruncated` | The text was cut after {count} continuation lines. | `One: The text was cut after {count} continuation line.` · `Other: The text was cut after {count} continuation lines.` | R7 |
| `sbsNoValue` | - | = | — |
| `keyTitle` | Records with the same key | = | — |
| `keyClose` | Close | = | — |
| `keyHeading` | {type} = {value} | = | — |
| `keyNoCase` | Open a case first. | = | — |
| `keyLoading` | Loading... | Loading… | R4 |
| `keyError` | Could not load the records | Could not load the records. Try again. | R9 |
| `keyEmpty` | No record carries this key. | = | — |
| `keyCount` | {count} records | `One: {count} record` · `Other: {count} records` | R7 |
| `keyCountMore` | {count} records so far (more available) | `One: {count} record so far (more available)` · `Other: {count} records so far (more available)` | R7 |
| `keyLoadMore` | Load more | = | — |
| `keyLoadingMore` | Loading... | Loading… | R4 |
| `keyTableCaption` | Records that carry the key | = | — |
| `keyColTime` | Time | = | — |
| `keyColSource` | Source | = | — |
| `keyColTid` | TID | = | — |
| `keyColTemplate` | Template | Message type | R2 |
| `keyColText` | Text | = | — |
| `keyOpenRecord` | Show record {id} in the grid | = | — |
| `keySensitiveTag` | sensitive | = | — |
| `plTitle` | Payload | = | — |
| `plLoading` | Loading payloads... | Loading payloads… | R4 |
| `plError` | Could not load the payloads | Could not load the payloads. Try again. | R9 |
| `plListLabel` | Payloads of this record | = | — |
| `plItem` | #{id} {role} {direction} | = | — |
| `plAnnounced` | announced here | = | — |
| `plSensitiveBadge` | sensitive | = | — |
| `plViewLabel` | View | = | — |
| `plFormatted` | Formatted | = | — |
| `plRaw` | Raw | = | — |
| `plCopyRaw` | Copy raw | = | — |
| `plCopyFormatted` | Copy formatted | = | — |
| `plCopied` | Copied. | = | — |
| `plCopyFailed` | Copy failed. | = | — |
| `plSize` | {size} chars | = | — |
| `plChipFmt` | format: {value} | = | — |
| `plChipRoot` | root: {value} | = | — |
| `plChipRole` | role: {value} | = | — |
| `plChipDirection` | direction: {value} | = | — |
| `plChipPeer` | peer: {value} | = | — |
| `plChipOperation` | operation: {value} | = | — |
| `plWellFormedYes` | well-formed | = | — |
| `plWellFormedNo` | not well-formed | = | — |
| `plWellFormedUnknown` | well-formedness not checked | = | — |
| `plTruncatedYes` | truncated in the log | = | — |
| `plTruncatedNo` | complete | = | — |
| `plReason` | Shown as is: {reason} | = | — |
| `plDisplayTruncated` | Display cut at the size limit. | = | — |
| `plLoadingText` | Loading the payload... | Loading the payload… | R4 |
| `plTextError` | Could not load the payload | Could not load the payload. Try again. | R9 |
| `plNoText` | (empty) | = | — |
| `plShowMore` | Show more ({remaining} chars left) | = | — |
| `plLargeNote` | Large payload: shown in chunks. | = | — |
| `plLinkOperation` | Linked to an operation by: {confidence}. | = | — |
| `plNoOperation` | Not linked to an operation. | = | — |
| `plConfidenceNames.1` | time window | = | — |
| `plConfidenceNames.2` | tid within operation | = | — |
| `plConfidenceNames.3` | key | = | — |
| `plConfidenceUnknown` | unknown | = | — |
| `plGoOperation` | Go to operation | = | — |
| `plGoAnnouncement` | Go to announcement | = | — |
| `plPair` | Pair | = | — |
| `plShowPair` | Show pair side by side | = | — |
| `plHidePair` | Hide pair | = | — |
| `plPairLoading` | Loading the pair... | Loading the pair… | R4 |
| `plPairError` | Could not load the pair | Could not load the pair. Try again. | R9 |
| `plPaneThis` | This payload (#{id}, {role}) | = | — |
| `plPanePair` | Pair (#{id}, {role}) | = | — |
| `plReasonNames.truncated` | the payload is truncated in the log | = | — |
| `plReasonNames.malformed` | the payload is malformed | = | — |
| `plReasonNames.too_large` | the payload is too large to format | = | — |
| `plReasonNames.html_raw` | HTML is shown as received | = | — |
| `plReasonNames.text` | plain text, nothing to format | = | — |
| `plgButton` | Payloads | Payloads… | R5 |
| `plgTitle` | Payloads | = | — |
| `plgClose` | Close | = | — |
| `plgRole` | Role | = | — |
| `plgDirection` | Direction | = | — |
| `plgPeer` | Peer | = | — |
| `plgPeerPlaceholder` | WFM, CRS (comma separated) | = | — |
| `plgOperation` | Operation | = | — |
| `plgOperationPlaceholder` | operation name | = | — |
| `plgFormat` | Format | = | — |
| `plgSearch` | Message or correlation id | = | — |
| `plgSearchPlaceholder` | exact id | = | — |
| `plgOrphan` | Only payloads without an operation | = | — |
| `plgAny` | Any | = | — |
| `plgRefresh` | Refresh | = | — |
| `plgNoCase` | Open a case first. | = | — |
| `plgLoading` | Loading... | Loading… | R4 |
| `plgError` | Could not load the payloads | Could not load the payloads. Try again. | R9 |
| `plgEmpty` | No payload matches. | = | — |
| `plgCount` | {count} payloads | `One: {count} payload` · `Other: {count} payloads` | R7 |
| `plgCountMore` | {count} payloads so far (more available) | `One: {count} payload so far (more available)` · `Other: {count} payloads so far (more available)` | R7 |
| `plgLoadMore` | Load more | = | — |
| `plgLoadingMore` | Loading... | Loading… | R4 |
| `plgTableCaption` | Payloads of the case | = | — |
| `plgColTime` | Time | = | — |
| `plgColRole` | Role | = | — |
| `plgColDirection` | Direction | = | — |
| `plgColPeer` | Peer | = | — |
| `plgColOperation` | Operation | = | — |
| `plgColFormat` | Format | = | — |
| `plgColSize` | Size | = | — |
| `plgColSide` | Source | = | — |
| `plgColNote` | Note | = | — |
| `plgOpen` | Open payload {id} | = | — |
| `plgNoOperation` | no operation | = | — |
| `plgSideSensitive` | sensitive | = | — |
| `plgSideNormal` | normal | = | — |
| `ftTypePayload` | Payload | = | — |
| `ftPayloadRoles` | Payload role | = | — |
| `ftPayloadFormats` | Payload format | = | — |
| `ftPayloadOrphan` | Operation link | = | — |
| `ftPayloadOrphanAny` | Any | = | — |
| `ftPayloadOrphanYes` | Without an operation | = | — |
| `ftPayloadOrphanNo` | With an operation | = | — |
| `ftPayloadAny` | Any payload (leave the lists empty). | = | — |
| `plNoticeAbsent` | Payload expected but absent: the log level in force ({level}) is Info or higher, so the payload should have been written. Check MessageLogLevel of the operation or the log rotation. | = | — |
| `plNoticeNotExpected` | Payload not expected: the current log level ({level}) is below MessageLogLevel. | = | — |
| `plNoticeLevelUnknown` | unknown | = | — |
| `plNoticeTitle` | Payload | = | — |
| `toolbarExport` | Export | Export… | R5 |
| `exportTitle` | Export a report (.txt) for a support ticket | = | — |
| `exportClose` | Close | = | — |
| `exportNoCase` | Open a case first. | = | — |
| `exportNoSelection` | Select a record in the grid first: this selection is not available. | Select a record in the grid first. This option needs a selected record. | R9 |
| `exportSelectionLegend` | What to export | = | — |
| `exportSelFilter` | Records matching the current filter (the first 1000) | = | — |
| `exportSelRecord` | The selected record | = | — |
| `exportSelEvent` | The event of the selected record | The operation of the selected record | R3 |
| `exportContext` | Records of context before and after | = | — |
| `exportAnonymize` | Anonymization | = | — |
| `exportAnonOff` | Off (original text) | = | — |
| `exportAnonOn` | On (replace values with markers, using the redaction profile) | = | — |
| `exportAnonOnNote` | Automatic anonymization is best effort: review the text before sharing it. | = | — |
| `exportPayloads` | Include payloads (with anonymization on, also redacted per XML/JSON element) | = | — |
| `exportMaxBytes` | Size limit of one part (bytes) | = | — |
| `exportBuild` | Build preview | = | — |
| `exportBuilding` | Building the report... | Building the report… | R4 |
| `exportError` | Could not build the report | Could not build the report. Try again. | R9 |
| `exportCounts` | {records} records, {events} events, {payloads} payloads | `One: {records} record, {events} operations, {payloads} payloads` · `Other: {records} records, {events} operations, {payloads} payloads` | R3 R7 |
| `exportNotAnonymized` | Original text (not anonymized) | = | — |
| `exportAnonymized` | Anonymized | = | — |
| `exportMapSaved` | The marker-to-value map was saved in the data folder (never share it). | = | — |
| `exportPartLabel` | Part | = | — |
| `exportPartOption` | Part {part} of {total} | = | — |
| `exportPreviewLabel` | Preview of the report | = | — |
| `exportCopy` | Copy to clipboard | = | — |
| `exportCopied` | Copied. | = | — |
| `exportCopyFallback` | Copied (legacy mode). | = | — |
| `exportCopyFailed` | Could not copy: select the text in the preview and copy it. | = | — |
| `exportDownload` | Download .txt | = | — |
| `exportDownloadAll` | Download all parts | = | — |
| `exportLegendTitle` | Replaced values (categories and counts only) | = | — |
| `exportLegendLine` | {label}: {distinct} distinct, {replaced} replaced | = | — |
| `exportWarningsTitle` | Notes | = | — |
| `exportRedactionSettings` | Redaction settings | Redaction settings… | R5 |
| `redactTitle` | Redaction settings | = | — |
| `redactClose` | Close | = | — |
| `redactLoading` | Loading... | Loading… | R4 |
| `redactError` | Could not load or save the redaction profile | Could not load or save the redaction profile. Try again. | R9 |
| `redactSaved` | Saved. | = | — |
| `redactCategories` | Replace these kinds of values | = | — |
| `redactWordTerms` | Words to replace (one per line; whole words, any letter case) | = | — |
| `redactLiteralTerms` | Texts to replace exactly (one per line; anywhere, same letter case) | = | — |
| `redactExceptions` | Never replace these values (one per line; operation names, error codes) | = | — |
| `redactPayloadNames` | XML elements, attributes and JSON keys replaced inside payloads (one per line) | = | — |
| `redactHint` | The profile is stored only in the data folder of this computer. | = | — |
| `redactSave` | Save | = | — |
| `redactReset` | Reset to defaults | = | — |
| `redactInvalidTitle` | The stored profile is not valid | = | — |
| `redactMode` | Mode | = | — |
| `redactModeStrict` | Strict (default): keeps the text the log templates declare and replaces every other value it cannot prove harmless | Strict (default): keeps the text that the log message types declare and replaces every other value it cannot prove harmless | R2 |
| `redactModeStandard` | Standard: replaces only the recognized kinds of values below (more readable, less safe) | = | — |
| `svIncludeToolbar` | Include the toolbar filters (severity, level, time, search, noise toggles) | = | — |
| `svOverwrite` | Overwrite | = | — |
| `svOverwriteAsk` | A saved filter with this name exists. Press Overwrite to replace its filter. | = | — |
| `svOverwritten` | Overwritten. | = | — |
| `svRename` | Rename | = | — |
| `svRenameNamed` | Rename {name} | = | — |
| `svRenaming` | Type the new name and press Rename (or Cancel). | = | — |
| `svRenamed` | Renamed. | = | — |
| `svCancel` | Cancel | = | — |
| `bmTitle` | Bookmarks | = | — |
| `bmButton` | Bookmarks | Bookmarks… | R5 |
| `bmClose` | Close | = | — |
| `bmToggleOn` | Bookmark this record | = | — |
| `bmToggleOff` | Remove bookmark | = | — |
| `bmDetailTitle` | Bookmark | = | — |
| `bmColor` | Color | = | — |
| `bmColorAny` | Any color | = | — |
| `bmColorNames.red` | Red | = | — |
| `bmColorNames.orange` | Orange | = | — |
| `bmColorNames.yellow` | Yellow | = | — |
| `bmColorNames.green` | Green | = | — |
| `bmColorNames.blue` | Blue | = | — |
| `bmColorNames.purple` | Purple | = | — |
| `bmColorNames.gray` | Gray | = | — |
| `bmNote` | Note | = | — |
| `bmNotePlaceholder` | Note (optional) | = | — |
| `bmNoteSave` | Save note | = | — |
| `bmNoteSaved` | Saved. | = | — |
| `bmFilterText` | Filter bookmarks | = | — |
| `bmFilterPlaceholder` | Text in the note or message | = | — |
| `bmEmpty` | No bookmarks in this case. | = | — |
| `bmNoMatch` | No bookmark matches the filters. | = | — |
| `bmNoCase` | Open a case first. | = | — |
| `bmLoading` | Loading... | Loading… | R4 |
| `bmError` | Could not load the bookmarks | Could not load the bookmarks. Try again. | R9 |
| `bmActionFailed` | The bookmark could not be changed | The bookmark could not be changed. Try again. | R9 |
| `bmCount` | {count} bookmarks | `One: {count} bookmark` · `Other: {count} bookmarks` | R7 |
| `bmCountMore` | {count} bookmarks so far (more available) | `One: {count} bookmark so far (more available)` · `Other: {count} bookmarks so far (more available)` | R7 |
| `bmLoadMore` | Load more | = | — |
| `bmJump` | Jump to | = | — |
| `bmJumpNamed` | Jump to the bookmarked record {id} | = | — |
| `bmEdit` | Edit | = | — |
| `bmEditNamed` | Edit bookmark {id} | = | — |
| `bmNoteTooLong` | The note is too long (at most {limit} characters). | = | — |
| `bmDelete` | Delete | Delete bookmark | R10 |
| `bmDeleteNamed` | Delete bookmark {id} | = | — |
| `bmConfirmDelete` | Confirm delete | = | — |
| `bmOrphan` | record not found (the file changed); the bookmark is kept | = | — |
| `bmLocation` | {member}, line {line} | = | — |
| `bmMarkerLabel` | bookmarked ({color}) | = | — |
| `bmExport` | Export | = | — |
| `bmImport` | Import | = | — |
| `bmImportLabel` | Bookmarks file to import | = | — |
| `bmImported` | Imported {imported}, already present {existing}, no matching file {unmatched}, invalid {invalid}. | = | — |
| `bmExportName` | dmslog-bookmarks.json | = | — |
| `bmTableCaption` | Bookmarks | = | — |
| `bmColColor` | Color | = | — |
| `bmColRecord` | Record | = | — |
| `bmColNote` | Note | = | — |
| `bmColActions` | Actions | = | — |
| `bmCreated` | created {date} | = | — |
| `cmpButton` | Compare with... | Compare… | R5 |
| `cmpButtonSecond` | Compare with record {id} | = | — |
| `cmpPicked` | Record {id} is the first record. Select the second one in the grid, then press Compare again (or press it on the first record to cancel). | = | — |
| `cmpCancelled` | Compare cancelled. | = | — |
| `cmpNeedRecord` | Select a record first. | = | — |
| `cmpTitle` | Compare records | = | — |
| `cmpClose` | Close | = | — |
| `cmpLoading` | Loading... | Loading… | R4 |
| `cmpError` | Could not compare the records | Could not compare the records. Try again. | R9 |
| `cmpNoCase` | Open a case first. | = | — |
| `cmpIdentical` | The two records have the same text. | = | — |
| `cmpStats` | {changed} changed, {removed} only in A, {added} only in B, {equal} equal lines | `One: {changed} changed, {removed} only in A, {added} only in B, 1 equal line` · `Other: {changed} changed, {removed} only in A, {added} only in B, {equal} equal lines` | R7 |
| `cmpTruncated` | The text was cut for the comparison (at most {count} lines of at most {chars} characters). | `One: The text was cut for the comparison (at most {count} line of at most {chars} characters).` · `Other: The text was cut for the comparison (at most {count} lines of at most {chars} characters).` | R7 |
| `cmpSideA` | A | = | — |
| `cmpSideB` | B | = | — |
| `cmpRecord` | Record {id} | = | — |
| `cmpTableCaption` | Line-level differences between record A and record B | = | — |
| `cmpHideEqual` | Hide unchanged lines | = | — |
| `cmpSkipped` | {count} unchanged lines | `One: {count} unchanged line` · `Other: {count} unchanged lines` | R7 |
| `cmpLine` | line | = | — |
| `cmpMetaTime` | Time | = | — |
| `cmpMetaSource` | Source | = | — |
| `cmpMetaTid` | TID | = | — |
| `cmpMetaTemplate` | Template | Message type | R2 |
| `shortcutsHelp` | Shortcuts: n/N next/previous error, t/T next/previous record of the template, b bookmark the selected record, B open the bookmarks, c compare two records. | Shortcuts: n/N next/previous error, t/T next/previous record of the message type, b bookmark the selected record, B open the bookmarks, c compare two records. | R2 |
| `cxButton` | Copy as | Copy or export… | R5 |
| `cxTitle` | Copy or export the current view | = | — |
| `cxWhat` | What | = | — |
| `cxKindValues` | List of values (a column or a key) | = | — |
| `cxKindLines` | Original lines of the view | = | — |
| `cxKindRecord` | Selected record (original text) | = | — |
| `cxKindViewCsv` | CSV of the view | = | — |
| `cxKindEventsCsv` | CSV of operations (events) | CSV of operations | R3 |
| `cxSource` | Values of | = | — |
| `cxSourceColumn` | Column: {name} | = | — |
| `cxSourceField` | Field: {name} | = | — |
| `cxSourceKey` | Key: {name} | = | — |
| `cxSeparator` | Separator | = | — |
| `cxSepNewline` | One per line | = | — |
| `cxSepComma` | Comma | = | — |
| `cxQuote` | Quotes | = | — |
| `cxQuoteNone` | None | = | — |
| `cxQuoteDouble` | Double quotes | = | — |
| `cxQuoteSingle` | Single quotes | = | — |
| `cxUnique` | Only distinct values | = | — |
| `cxEventStatus` | Status | = | — |
| `cxEventName` | Operation | = | — |
| `cxEventKey` | Key starts with | = | — |
| `cxEventNameHint` | Names separated by commas | = | — |
| `cxBom` | BOM for Excel (Windows) | = | — |
| `cxMaxRows` | Row limit | = | — |
| `cxCopy` | Copy | = | — |
| `cxDownload` | Download file | = | — |
| `cxClose` | Close | = | — |
| `cxFormulaNote` | Text cells that start with = + - @ get a leading apostrophe so spreadsheets do not run them as formulas. | = | — |
| `cxWorking` | Preparing... | Preparing… | R4 |
| `cxNoCase` | Open a case first. | = | — |
| `cxNoRecord` | Select a record first. | = | — |
| `cxNothing` | Nothing to export for this view. | = | — |
| `cxCopied` | Copied {rows} rows. | `One: Copied 1 row.` · `Other: Copied {rows} rows.` | R7 |
| `cxCopiedRecord` | Copied the selected record. | = | — |
| `cxCopyFailed` | The browser blocked the copy; use Download file instead. | = | — |
| `cxDownloaded` | Downloaded {rows} rows as {file}. | `One: Downloaded 1 row as {file}.` · `Other: Downloaded {rows} rows as {file}.` | R7 |
| `cxTruncated` | Only the first {rows} rows were exported; the view has more. Raise the row limit or narrow the filter. | `One: Only the first 1 row was exported; the view has more. Raise the row limit or narrow the filter.` · `Other: Only the first {rows} rows were exported; the view has more. Raise the row limit or narrow the filter.` | R7 |
| `cxError` | Could not export the view | Could not export the view. Try again. | R9 |
| `cxBadLimit` | The row limit must be a whole number from 1 to {max}. | = | — |
| `cxEventsAnyStatus` | Any | = | — |
| `columnResizeLabel` | Resize column {name} | = | — |
| `columnsResetWidths` | Reset column widths | = | — |
| `prTitle` | Presets | = | — |
| `prIntro` | Shortcuts for the adapters in this case. Applying one fills the filter tree; nothing is applied on its own. | = | — |
| `prEmpty` | No presets for the adapters in this case. | = | — |
| `prLoadFailed` | Could not load the presets | Could not load the presets. Try again. | R9 |
| `prGlobalGroup` | All adapters | = | — |
| `prFamilyGroup` | Adapter {family} | = | — |
| `prBuiltin` | curated | Built-in | R15 |
| `prApply` | Apply preset {name} | = | — |
| `prRename` | Rename | = | — |
| `prRenameNamed` | Rename preset {name} | = | — |
| `prRenaming` | Type the new name and press Rename (or Cancel). | = | — |
| `prRenamed` | Renamed. | = | — |
| `prDelete` | Delete | Delete preset | R10 |
| `prDeleteNamed` | Delete preset {name} | = | — |
| `prConfirmDelete` | Confirm delete | = | — |
| `prName` | Preset name | = | — |
| `prNamePlaceholder` | Name of this preset | = | — |
| `prFamily` | Applies to | = | — |
| `prFamilyAll` | All adapters | = | — |
| `prSave` | Save current filter as preset | = | — |
| `prOverwrite` | Overwrite | = | — |
| `prOverwriteAsk` | A preset with this name exists. Press Overwrite to replace its filter. | = | — |
| `prOverwritten` | Overwritten. | = | — |
| `prCancel` | Cancel | = | — |
| `prSaved` | Preset saved. | = | — |
| `prNeedFilter` | Build a valid filter with at least one condition first. | = | — |
| `prErrorEmpty` | Enter a name. | = | — |
| `prErrorTooLong` | The name is too long (at most {limit} characters). | = | — |
| `prErrorDuplicate` | A preset with this name already exists for this scope. | = | — |
| `prErrorReserved` | This is the name of a curated preset; choose another. | = | — |
| `prErrorTooMany` | The maximum of {limit} user presets was reached. | `One: The maximum of 1 user preset was reached.` · `Other: The maximum of {limit} user presets was reached.` | R7 |
| `prInvalidFile` | The presets file is not valid and is left untouched; user presets are unavailable until it is fixed. | = | — |
| `prProblems` | {count} preset(s) in the presets file were skipped as invalid. | `One: 1 preset in the presets file was skipped as invalid.` · `Other: {count} presets in the presets file were skipped as invalid.` | R7 |
| `prExportJson` | Export JSON | = | — |
| `prExportToml` | Export TOML | = | — |
| `prExportNameJson` | dmslog-presets.json | = | — |
| `prExportNameToml` | dmslog-presets.toml | = | — |
| `prExportEmpty` | There are no user presets to export. | = | — |
| `prExported` | Exported {count} preset(s). | `One: Exported 1 preset.` · `Other: Exported {count} presets.` | R7 |
| `prImport` | Import presets | = | — |
| `prImportBadFile` | Choose a .json or .toml file. | = | — |
| `prImported` | Import finished: {added} added, {updated} updated, {ignored} ignored. | = | — |
| `prImportIgnoredItem` | {name}: {message} | = | — |
| `colTitle` | Collections | = | — |
| `colTag` | C{number} | = | — |
| `colErs` | ERS {capture} ({server}) | = | — |
| `colFolder` | Folder {name} | = | — |
| `colFile` | File {name} | = | — |
| `colMissingEnterprise` | Enterprise zip not added yet | = | — |
| `colMissingSensitive` | Sensitive zip not added yet | = | — |
| `colRecords` | {records} records in {sources} sources | `One: {records} record in {sources} sources` · `Other: {records} records in {sources} sources` | R7 |
| `colIndexing` | Indexing... | Indexing… | R4 |
| `colError` | Some sources failed | = | — |
| `colRemove` | Remove collection | = | — |
| `colRemoveConfirm` | Remove {tag} ({title}) from this case? This deletes its {sources} sources and {records} records, and the bookmarks you made on them. The other collections stay as they are. This cannot be undone: export the bookmarks first if you want to keep them. | `One: Remove {tag} ({title}) from this case? This deletes its {sources} sources and 1 record, and the bookmarks you made on them. The other collections stay as they are. This cannot be undone: export the bookmarks first if you want to keep them.` · `Other: Remove {tag} ({title}) from this case? This deletes its {sources} sources and {records} records, and the bookmarks you made on them. The other collections stay as they are. This cannot be undone: export the bookmarks first if you want to keep them.` | R7 |
| `colRemoved` | Removed {tag}: {sources} sources, {records} records and {bookmarks} bookmarks. | `One: Removed {tag}: {sources} sources, 1 record and {bookmarks} bookmarks.` · `Other: Removed {tag}: {sources} sources, {records} records and {bookmarks} bookmarks.` | R7 |
| `colAll` | All collections | = | — |
| `colApproxOrder` | Each collection keeps its own clock: the order across collections is approximate. | = | — |
| `sourceFilterLabel` | Filter sources by file name | = | — |
| `sourceFilterPlaceholder` | Filter sources... | Filter sources… | R4 |
| `sourceFilterClear` | Clear the source filter | = | — |
| `sourceFilterAll` | {total} sources | `One: {total} source` · `Other: {total} sources` | R7 |
| `sourceFilterCount` | {visible} of {total} sources | `One: {visible} of {total} source` · `Other: {visible} of {total} sources` | R7 |
| `sourceFilterNone` | No source matches the filter. | = | — |
| `dstButton` | Distinct values | Distinct values | = |
| `dstTitle` | Distinct values | = | — |
| `dstFieldLabel` | Field | = | — |
| `dstTopLabel` | Top | = | — |
| `dstRefresh` | Refresh | = | — |
| `dstGroupColumns` | Columns | = | — |
| `dstGroupKeys` | Keys | = | — |
| `dstGroupFields` | Parsed fields | = | — |
| `dstColumnNames.tpl` | Message type | = | — |
| `dstColumnNames.source` | Source | = | — |
| `dstColumnNames.sev` | Severity | = | — |
| `dstColumnNames.level` | Level | = | — |
| `dstColumnNames.tid` | TID | = | — |
| `dstColumnNames.pid` | PID | = | — |
| `dstColumnNames.collection` | Collection | = | — |
| `dstColValue` | Value | = | — |
| `dstColCount` | Records | = | — |
| `dstColShare` | Share | = | — |
| `dstColActions` | Filter | = | — |
| `dstInclude` | Only | = | — |
| `dstExclude` | Hide | = | — |
| `dstIncludeLabel` | Show only {value} | = | — |
| `dstExcludeLabel` | Hide {value} | = | — |
| `dstNoLeaf` | This value cannot be expressed as a filter. | = | — |
| `dstEmpty` | (empty) | = | — |
| `dstUnclassified` | (unclassified) | = | — |
| `dstSourceValue` | {name} (#{id}) | = | — |
| `dstSummary` | {total} in the filter, {groups} distinct values | `One: {total} in the filter, {groups} distinct value` · `Other: {total} in the filter, {groups} distinct values` | R7 |
| `dstSummaryApprox` | About {total} in the filter, {groups} distinct values seen (estimate) | `One: About {total} in the filter, {groups} distinct value seen (estimate)` · `Other: About {total} in the filter, {groups} distinct values seen (estimate)` | R7 |
| `dstTooSlow` | The count took too long; narrow the filter (sources or time) and refresh. | = | — |
| `dstOthers` | Others: {count} in {groups} more values | `One: Others: {count} in {groups} more value` · `Other: Others: {count} in {groups} more values` | R7 |
| `dstEmptyResult` | No records in the filter. | = | — |
| `dstLoading` | Loading... | Loading… | R4 |
| `dstNoCase` | Open a case first. | = | — |
| `dstError` | Could not count the values: {error} | Could not count the values: {error}. Narrow the filter and try again. | R9 |
| `dstBadTop` | Top must be a whole number from 1 to {max}. | = | — |
| `grpButton` | Group by message type | = | — |
| `grpTitle` | Records grouped by message type | = | — |
| `grpCount` | {count} ({pct}) | = | — |
| `grpSpan` | {first} to {last} | = | — |
| `grpRowsLabel` | Records of {value} | = | — |
| `grpOpenRecord` | Show record {id} in the grid | = | — |
| `grpMore` | Load more | = | — |
| `dstValueTitle` | {text} ({code}) | = | — |
| `dstCountExactly` | Count exactly | = | — |
| `dstEstimateWarning` | Estimate: rare values may be missing. | = | — |
| `dstExactTooSlow` | The exact count took too long; this is still an estimate. Narrow the filter (sources or time). | = | — |
| `hlButton` | Highlights | Highlights | = |
| `hlTitle` | Highlight rules | = | — |
| `hlCount` | {count} of {max} rules; the first rule that matches gives the color | `One: {count} of {max} rule; the first rule that matches gives the color` · `Other: {count} of {max} rules; the first rule that matches gives the color` | R7 |
| `hlEmpty` | No highlight rules. A rule colors the records it matches and hides nothing. | = | — |
| `hlKind` | Condition | = | — |
| `hlKinds.text` | Words (search) | = | — |
| `hlKinds.substr` | Exact text | = | — |
| `hlKinds.regex` | Regex | = | — |
| `hlKinds.key` | Key | = | — |
| `hlKinds.tpl` | Message type | = | — |
| `hlKinds.sev` | Severity at least | = | — |
| `hlKinds.field` | Field | = | — |
| `hlKeyType` | Key type | = | — |
| `hlFieldName` | Field name | = | — |
| `hlValue` | Value | = | — |
| `hlName` | Name (optional) | = | — |
| `hlColor` | Color | = | — |
| `hlAdd` | Add rule | = | — |
| `hlAddFilter` | Add the current filter as a rule | = | — |
| `hlNoFilter` | The view has no filter to turn into a rule. | = | — |
| `hlUp` | Up | = | — |
| `hlDown` | Down | = | — |
| `hlDelete` | Delete | Delete rule | R10 |
| `hlUpLabel` | Give {name} a higher priority | = | — |
| `hlDownLabel` | Give {name} a lower priority | = | — |
| `hlDeleteLabel` | Delete the rule {name} | = | — |
| `hlColorLabel` | Color of {name} | = | — |
| `hlEnabledLabel` | Highlight with {name} | = | — |
| `hlColorNames.yellow` | Yellow | = | — |
| `hlColorNames.orange` | Orange | = | — |
| `hlColorNames.red` | Red | = | — |
| `hlColorNames.pink` | Pink | = | — |
| `hlColorNames.purple` | Purple | = | — |
| `hlColorNames.blue` | Blue | = | — |
| `hlColorNames.cyan` | Cyan | = | — |
| `hlColorNames.green` | Green | = | — |
| `hlBad.empty` | Type a value. | = | — |
| `hlBad.tooLong` | The value is too long. | = | — |
| `hlBad.badSev` | Choose a severity. | = | — |
| `hlBad.badTpl` | Type message type codes or numbers, separated by commas. | = | — |
| `hlBad.badName` | Type the key type or the field name (letters, digits, '_'). | = | — |
| `hlDescText` | words: {value} | = | — |
| `hlDescSubstr` | text: "{value}" | = | — |
| `hlDescRegex` | regex: /{value}/ | = | — |
| `hlDescKey` | key {type} = {value} | = | — |
| `hlDescKeyAny` | has a key {type} | = | — |
| `hlDescTpl` | message type {value} | = | — |
| `hlDescSev` | severity {value} or worse | severity at least {value} | R6 |
| `hlDescField` | {name} = {value} | = | — |
| `hlDescFilter` | filter {json} | = | — |
| `hlLimit` | A case holds at most {max} highlight rules. | `One: A case holds at most 1 highlight rule.` · `Other: A case holds at most {max} highlight rules.` | R7 |
| `hlError` | Highlight not saved: {error} | Highlight not saved: {error}. Check the value and try again. | R9 |
| `hlRowLabel` | highlighted by {name} | = | — |
| `hlBy` | Highlighted by {name} | = | — |
| `hlDetailTitle` | Highlight | = | — |
| `hlKeyChip` | Highlight {type} = {value} | = | — |
| `hlKeyDone` | Records with {type} = {value} are highlighted (normal and sensitive logs). | = | — |
| `hlTemplateChip` | Highlight this message type | = | — |
| `hlNavNone` | No more highlighted records in this direction of the view. | = | — |
| `hlNavNoRules` | No highlight rule is on. | = | — |
| `hlNavFound` | Highlighted by {name} | = | — |
| `hlShortcutsHelp` | Highlights: ] next, [ previous highlighted record of the view. | = | — |
| `cfTitle` | ERS files of the new case | = | — |
| `cfHelp` | Add 1 or more ERS ZIPs (Enterprise and Sensitive files, or several captures). Use Browse to pick them in a file dialog; Ctrl or Shift selects many at once. | = | — |
| `cfBrowse` | Browse… | = | — |
| `cfAdd` | Add | Add to list | R10 |
| `cfPathLabel` | Path of an ERS zip, folder or file | = | — |
| `cfPathPlaceholder` | Paste a full path and press Enter, or use Browse… | = | — |
| `cfListLabel` | Chosen ERS files | = | — |
| `cfEmpty` | No ERS file chosen yet. Add at least 1 ZIP to create the case. | = | — |
| `cfCount` | {n} chosen | = | — |
| `cfClear` | Clear all | = | — |
| `cfRemove` | Remove | Remove file | R10 |
| `cfRemoveLabel` | Remove {name} from the list | = | — |
| `cfKindEnterprise` | Enterprise | = | — |
| `cfKindSensitive` | Sensitive | = | — |
| `cfKindOther` | Name not recognized as an ERS | = | — |
| `cfFolderLabel` | Folder | = | — |
| `cfCreate` | Create case | = | — |
| `cfCreateWith` | Create case with {n} file(s) | `One: Create case ({n} file)` · `Other: Create case ({n} files)` | R7 |
| `cfNeedName` | Type a name for the case. | = | — |
| `cfNeedFiles` | Add at least 1 ERS ZIP to create the case. | = | — |
| `cfWaitingDialog` | Waiting for the file dialog… | = | — |
| `cfCancel` | Cancel | = | — |
| `cfCancelDialog` | Cancel the open dialog | = | — |
| `cfQueueBusy` | The case is being created and its files are still opening. Wait until it finishes before switching case or opening another collection. | = | — |
| `cfCaseChanged` | The case changed while the file was opening; it was not opened here. | = | — |
| `cfNewCaseRestart` | A new case is being created with this name, so every file in the list is opened again in it. | = | — |
| `cfStreamClosed` | The progress connection to the server was closed, so the result of this file is unknown. Check the source list and press Create again. | = | — |
| `cfDialogBusy` | A file dialog is already open (it may be behind another window). | = | — |
| `cfDialogTimeout` | The file dialog was closed after {minutes} minutes. | `One: The file dialog was closed after 1 minute.` · `Other: The file dialog was closed after {minutes} minutes.` | R7 |
| `cfNoNative` | No native file dialog here; using the built-in browser. | = | — |
| `cfDuplicate` | {name}: already in the list. | = | — |
| `cfLimit` | {name}: the list is limited to {message} files. | = | — |
| `cfItemError` | {name}: {message} | = | — |
| `cfStatusWorking` | Opening… | = | — |
| `cfStatusOk` | Opened | = | — |
| `cfStatusRefused` | Not added | = | — |
| `cfStatusFailed` | Failed | = | — |
| `cfBusy` | The case is still indexing another file. Try again when the progress bar finishes. | = | — |
| `cfCreated` | Case created; {ok} of {total} file(s) opened. | `One: Case created; {ok} of {total} file opened.` · `Other: Case created; {ok} of {total} files opened.` | R7 |
| `cfCreatedAll` | Case created and {total} file(s) opened. | `One: Case created and 1 file opened.` · `Other: Case created and {total} files opened.` | R7 |
| `cfPartial` | {ok} of {total} file(s) opened. Fix or remove the others and press Create again. | `One: {ok} of 1 file opened. Fix or remove the others and press Create again.` · `Other: {ok} of {total} files opened. Fix or remove the others and press Create again.` | R7 |
| `fbTitle` | Select ERS archives (.zip) | = | — |
| `fbPath` | Folder | = | — |
| `fbPathLabel` | Current folder (type a path and press Enter) | = | — |
| `fbUp` | Up | = | — |
| `fbShowHidden` | Show hidden | = | — |
| `fbShortcuts` | Places | = | — |
| `fbHome` | Home | = | — |
| `fbDesktop` | Desktop | = | — |
| `fbDownloads` | Downloads | = | — |
| `fbDocuments` | Documents | = | — |
| `fbDrive` | Drive {path} | = | — |
| `fbRoot` | Root | = | — |
| `fbMedia` | Removable media | = | — |
| `fbBreadcrumbs` | Folder path | = | — |
| `fbList` | Folders and ZIP files | = | — |
| `fbEmpty` | Folder is empty | = | — |
| `fbNoZips` | No folders or ZIP files here | = | — |
| `fbTruncated` | Only the first {n} entries are shown | `One: Only the first 1 item is shown` · `Other: Only the first {n} items are shown` | R11 R7 |
| `fbOtherFiles` | {n} other files not shown | `One: 1 other file not shown` · `Other: {n} other files not shown` | R7 |
| `fbSkipped` | {n} entries with unreadable names not shown | `One: 1 item with unreadable name not shown` · `Other: {n} items with unreadable names not shown` | R11 R7 |
| `fbLoading` | Loading… | = | — |
| `fbLink` | Link | = | — |
| `fbBroken` | Broken link | = | — |
| `fbSelected` | {n} selected | = | — |
| `fbAddSelected` | Add selected | Add selected files | R10 |
| `fbSelectAll` | Select all ZIPs here | = | — |
| `fbClose` | Close | = | — |
| `fbHint` | Double-click or press Enter on a folder to open it. Click a ZIP to select it; Ctrl or Shift selects more. | = | — |
| `fbErrorPrefix` | Cannot open the folder | = | — |
| `colListLabel` | Collections | = | — |
| `colSelectLabel` | Show only records of {tag} | = | — |
| `colExpandLabel` | Show the files of {tag} | = | — |
| `colCollapseLabel` | Hide the files of {tag} | = | — |
| `colZip` | Zip {name} | = | — |
| `colServerUnknown` | server unknown | = | — |
| `colGuidLine` | GUID {guid} | = | — |
| `colOnly` | {have} only | = | — |
| `colWindow` | {first} to {last} | = | — |
| `colVariantNames.enterprise` | Enterprise | = | — |
| `colVariantNames.sensitive` | Sensitive | = | — |
| `colShowing` | Showing only {tags}. | = | — |
| `colShowingAll` | Showing all collections. | = | — |
| `colLoadError` | Could not load the collections: {message} | = | — |
| `colApproxOrderMore` | Each collection comes from a server with its own clock, and the clocks are not synchronized. Records of different collections are sorted by their logged time, so their order relative to each other can be off. Inside one collection the order is exact. | Collections come from servers with different clocks. Order across collections is approximate; inside one collection it is exact. | R9 |
| `colRemoveYes` | Remove {tag} | = | — |
| `colRemoveCancel` | Cancel | = | — |
| `colRemoveLabel` | Remove {tag} from the case | = | — |
| `colRemoving` | Removing {tag}... | Removing {tag}… | R4 |
| `colJobRunning` | Cannot remove {tag} while the case is indexing. Wait for the progress bar to finish and try again. | = | — |
| `colFollowedTag` | Cannot remove {tag}: the viewer is following this folder. Stop following it first. | = | — |
| `colGone` | {tag} is no longer in the case; the list was reloaded. | = | — |
| `columnCollection` | Collection | = | — |
| `colPanelsOne` | Operations, Dashboard and the clock banner filter by one collection at a time; while several are chosen they show all collections. | = | — |
| `cfAddTitle` | Add ERS files to this case | = | — |
| `cfAddHelp` | Add more ERS ZIPs to the open case. A file from another server or another capture becomes a new collection; the Enterprise and Sensitive zips of one capture join the same collection. Use Browse to pick them in a file dialog. | = | — |
| `cfAddEmpty` | No file chosen yet. | = | — |
| `cfAddFiles` | Add files | Add files | = |
| `cfAddWith` | Add {n} file(s) to the case | `One: Add 1 file to case` · `Other: Add {n} files to case` | R7 |
| `cfAddNeedFiles` | Choose at least 1 file to add. | = | — |
| `cfAddedAll` | {total} file(s) added to the case. | `One: 1 file added to the case.` · `Other: {total} files added to the case.` | R7 |
| `cfAddPartial` | {ok} of {total} file(s) added. Fix or remove the others and press Add again. | `One: {ok} of 1 file added. Fix or remove the others and press Add again.` · `Other: {ok} of {total} files added. Fix or remove the others and press Add again.` | R7 |
| `cfDuplicateCollection` | The case already has this ERS file from another location (same capture), so this copy was not added. To refresh it, open the original path again. | = | — |
| `vsBarLabel` | Chained views | = | — |
| `vsCrumbsLabel` | View stack | = | — |
| `vsBack` | Back | = | — |
| `vsBackTitle` | Previous view (Alt+Left) | = | — |
| `vsForward` | Forward | = | — |
| `vsForwardTitle` | Next view (Alt+Right) | = | — |
| `vsNarrow` | Narrow this view | = | — |
| `vsNarrowTitle` | The current filter becomes the parent; the new view adds its own filter on top | = | — |
| `vsRename` | Rename | = | — |
| `vsRenameLabel` | View name | = | — |
| `vsRenameOk` | OK | = | — |
| `vsRenameCancel` | Cancel | = | — |
| `vsShowParent` | Show in parent | = | — |
| `vsShowRoot` | Show in root | = | — |
| `vsRootName` | All records | = | — |
| `vsViewName` | View {n} | = | — |
| `vsNameFilter` | filter | = | — |
| `vsNameText` | text | = | — |
| `vsNameTimeRange` | time range | = | — |
| `vsNeedFilter` | Set a filter in this view first, then narrow it. | = | — |
| `vsFull` | The stack is limited to {n} views. | `One: The stack is limited to 1 view.` · `Other: The stack is limited to {n} views.` | R7 |
| `vsAtRoot` | Already at the root view. | = | — |
| `vsNeedCase` | Open a case first. | = | — |
| `vsShortcutsHelp` | Alt+Left/Alt+Right previous/next view of the stack. | = | — |
| `alToolbar` | Alerts | Alerts… | R5 |
| `alTitle` | Alerts | = | — |
| `alIntro` | Local alerts while the folder is followed (dmslog serve --watch). Rules look only at the records of each follow cycle; nothing leaves this computer. | = | — |
| `alClose` | Close | = | — |
| `alRules` | Rules | = | — |
| `alNoRules` | No alert rules yet. | = | — |
| `alHistory` | Recent alerts | = | — |
| `alNoHistory` | No alerts in this session. | = | — |
| `alClearHistory` | Clear history | = | — |
| `alEnabled` | On | = | — |
| `alDelete` | Delete | Delete rule | R10 |
| `alDeleteConfirm` | Press again to delete | = | — |
| `alShowRecords` | Show these records | = | — |
| `alDismiss` | Dismiss | = | — |
| `alBannerMore` | {count} more alert(s) | `One: 1 more alert` · `Other: {count} more alerts` | R7 |
| `alBanner` | {severity} alert: {rule} - {count} {what} | = | — |
| `alWhatRecords` | new record(s) | `One: new record` · `Other: new records` | R7 |
| `alWhatOperations` | operation(s) without an end | `One: operation without an end` · `Other: operations without an end` | R7 R3 |
| `alWhatWindow` | record(s) in the window | `One: record in the window` · `Other: records in the window` | R7 |
| `alSeverityInfo` | Info | = | — |
| `alSeverityWarning` | Warning | = | — |
| `alSeverityCritical` | Critical | = | — |
| `alKindMatch` | Matches new records | = | — |
| `alKindThreshold` | More than N records in a window | = | — |
| `alKindOpen` | Operation without an end after the timeout | = | — |
| `alSummaryMatch` | {count} or more new record(s) match the filter | `One: 1 or more new record matches the filter.` · `Other: {count} or more new records match the filter.` | R7 |
| `alSummaryThreshold` | more than {threshold} matches in {window} s | = | — |
| `alSummaryOpenParam` | operation open longer than {param} | = | — |
| `alSummaryOpenSeconds` | operation open longer than {seconds} s | = | — |
| `alSummaryOperation` |  ({operation}) | = | — |
| `alUnavailable` | Unavailable in this case: {message} | = | — |
| `alNotifyTitle` | Browser notifications | = | — |
| `alNotifyEnable` | Enable browser notifications | = | — |
| `alNotifyOn` | Show a browser notification for each alert | = | — |
| `alNotifyDenied` | Notifications are blocked in the browser settings; the banner still appears. | = | — |
| `alNotifyUnsupported` | This browser has no notification support; the banner still appears. | = | — |
| `alNotifyBody` | {count} {what} | = | — |
| `alNewRule` | New rule | = | — |
| `alName` | Name | = | — |
| `alKind` | Kind | = | — |
| `alSeverity` | Severity | = | — |
| `alFilterFrom` | Filter | = | — |
| `alFilterCurrent` | Current filter | = | — |
| `alFilterNone` | Current filter is empty; build one in the filter tree or pick a preset. | = | — |
| `alMinCount` | At least this many records | = | — |
| `alThreshold` | More than | = | — |
| `alWindow` | Window (seconds) | = | — |
| `alTimeoutParam` | Registry parameter | = | — |
| `alTimeoutSeconds` | Fixed seconds | = | — |
| `alTimeoutMode` | Timeout from | = | — |
| `alOperation` | Operation (optional) | = | — |
| `alMinInterval` | Minimum delay between alerts (seconds) | = | — |
| `alCreate` | Create rule | = | — |
| `alCreated` | Rule created. | = | — |
| `alErrName` | Give the rule a name. | = | — |
| `alErrNameLong` | The name is too long. | = | — |
| `alErrDuplicate` | A rule with this name already exists. | = | — |
| `alErrFilter` | This kind needs a filter: use the current filter or a preset. | = | — |
| `alErrCount` | Enter a whole number of at least 1. | = | — |
| `alErrThreshold` | Enter a whole number of at least 1. | = | — |
| `alErrWindow` | Enter a whole number of seconds from 1 to 86400. | = | — |
| `alErrTimeout` | Enter a whole number of seconds from 1 to 604800. | = | — |
| `alErrParam` | Enter a Registry parameter name, for example SendTimeout. | = | — |
| `alErrInterval` | Enter a whole number of seconds from 0 to 86400. | = | — |
| `alErrKind` | Choose a kind. | = | — |
| `alProblems` | {count} rule(s) in the alerts file were skipped as invalid. | `One: 1 rule in the alerts file was skipped as invalid.` · `Other: {count} rules in the alerts file were skipped as invalid.` | R7 |
| `alFileInvalid` | The alerts file is not valid and was left untouched; fix or remove it first. | = | — |
| `alAt` | Time | = | — |
| `aggButton` | Aggregate | Aggregate | = |
| `aggTitle` | Aggregate | = | — |
| `aggExamples` | Examples | = | — |
| `aggExamplesNone` | Choose a ready-made question… | = | — |
| `aggExOpDuration` | Operation duration (p50, p90, p99) by operation | = | — |
| `aggExErrorsPerHour` | Errors by message type per hour | = | — |
| `aggExByCollection` | Records by collection and family | = | — |
| `aggExOpsPerHour` | Operations per hour by status | = | — |
| `aggExFailedOps` | Failed operations by family and operation | = | — |
| `aggExErrorCodes` | Error codes with their reason (knowledge pack) | = | — |
| `aggRows` | Count | = | — |
| `aggRowsEntries` | Records | = | — |
| `aggRowsEvents` | Operations and events | Operations | R3 |
| `aggGroupBy` | Group by {n} | = | — |
| `aggMeasure` | Measure {n} | = | — |
| `aggNone` | (none) | = | — |
| `aggOrderBy` | Order by | = | — |
| `aggOrderDefault` | Default | = | — |
| `aggOrderDesc` | Descending | = | — |
| `aggOrderAsc` | Ascending | = | — |
| `aggOrderDir` | Direction | = | — |
| `aggLimit` | Rows | = | — |
| `aggOnlyErrors` | Only errors | = | — |
| `aggEventKinds` | Event kinds | Operation kinds | R3 |
| `aggEventNames` | Operation names | = | — |
| `aggEventStatuses` | Statuses | = | — |
| `aggEventHint` | comma separated; empty = all | = | — |
| `aggLookups` | Knowledge pack | = | — |
| `aggRun` | Run | Run aggregation | R10 |
| `aggCsv` | Download CSV | = | — |
| `aggCopy` | Copy table | = | — |
| `aggCopied` | Table copied. | = | — |
| `aggCopyFailed` | The table could not be copied. | = | — |
| `aggCsvSaved` | CSV saved: {file} | = | — |
| `aggComputeExactly` | Compute exactly | = | — |
| `aggFilterRow` | Filter by this row | = | — |
| `aggFilterRowLabel` | Filter the view by {row} | = | — |
| `aggNoDrill` | This row cannot be expressed as a filter | = | — |
| `aggLoading` | Aggregating… | = | — |
| `aggError` | Aggregation failed: {error} | Aggregation failed: {error}. Narrow the view and try again. | R9 |
| `aggNoCase` | Open a case first. | = | — |
| `aggRunFirst` | Run the aggregation first. | = | — |
| `aggStale` | The view changed: run again to update. | = | — |
| `aggEmpty` | No groups in this view. | No groups in this view. Widen the filter or pick other columns. | R14 |
| `aggSummary` | {groups} groups. | `One: {groups} group.` · `Other: {groups} groups.` | R7 |
| `aggSummaryTruncated` | Showing {shown} of {groups} groups. | `One: Showing {shown} of 1 group.` · `Other: Showing {shown} of {groups} groups.` | R7 |
| `aggTotal` | Total {total}. | = | — |
| `aggTotalApprox` | Total about {total}. | = | — |
| `aggSampleWarning` | Estimate from a 2% sample: counts and sums are extrapolated; minimum, maximum, average, percentiles and distinct counts come from the sample. | = | — |
| `aggBucketWarning` | Estimate by whole minutes: the edges of a time window are rounded. | = | — |
| `aggExactTooSlow` | The exact result did not fit in time either; narrow the view. | = | — |
| `aggPackMissing` | Knowledge pack not installed: its columns are left out. | = | — |
| `aggLookupAmbiguous` | Some knowledge values differ between families and are left empty: group by family too. | = | — |
| `aggNeedMeasure` | Choose at least one measure. | = | — |
| `aggOneBucket` | Use at most one time bucket. | = | — |
| `aggBadLimit` | Rows must be a whole number from 1 to {max}. | = | — |
| `aggBadEventList` | Event conditions are comma-separated words of up to 200 characters. | Operation conditions are comma-separated words of up to 200 characters. | R3 |
| `aggChartLabel` | Chart of {measure} | = | — |
| `aggChartMore` | Only the largest series are drawn. | = | — |
| `aggEmptyValue` | (empty) | = | — |
| `aggCount` | Count | = | — |
| `aggDistinctOf` | Distinct {what} | = | — |
| `aggFnOf` | {fn} {what} | = | — |
| `aggKeyLabel` | Key {type} | = | — |
| `aggFieldLabel` | Field {name} | = | — |
| `aggBucketLabel` | Time ({width}) | = | — |
| `aggBucketNames.1m` | 1 minute | = | — |
| `aggBucketNames.5m` | 5 minutes | = | — |
| `aggBucketNames.15m` | 15 minutes | = | — |
| `aggBucketNames.1h` | 1 hour | = | — |
| `aggBucketNames.1d` | 1 day | = | — |
| `aggDimNames.tpl` | Message type | = | — |
| `aggDimNames.source` | Source | = | — |
| `aggDimNames.collection` | Collection | = | — |
| `aggDimNames.family` | Family | = | — |
| `aggDimNames.sev` | Severity | = | — |
| `aggDimNames.level` | Level | = | — |
| `aggDimNames.tid` | TID | = | — |
| `aggDimNames.pid` | PID | = | — |
| `aggDimNames.name` | Operation | = | — |
| `aggDimNames.kind` | Kind | = | — |
| `aggDimNames.status` | Status | = | — |
| `aggDimNames.key` | Event key | Operation key | R3 |
| `aggFnNames.min` | Min | = | — |
| `aggFnNames.max` | Max | = | — |
| `aggFnNames.avg` | Average | = | — |
| `aggFnNames.sum` | Sum | = | — |
| `aggFnNames.p50` | p50 | = | — |
| `aggFnNames.p90` | p90 | = | — |
| `aggFnNames.p95` | p95 | = | — |
| `aggFnNames.p99` | p99 | = | — |
| `aggValueNames.duration` | duration (ms) | = | — |
| `aggValueNames.entries` | records | = | — |
| `aggLookupNames.error.reason` | Error reason | = | — |
| `aggLookupNames.error.severity` | Error severity | = | — |
| `aggLookupNames.operation.direction` | Direction | = | — |
| `aggLookupNames.operation.service` | Service | = | — |
| `aggGroupColumns` | Columns | = | — |
| `aggGroupTime` | Time | = | — |
| `aggGroupKeys` | Keys | = | — |
| `aggGroupFields` | Fields | = | — |
| `aggGroupCount` | Count | = | — |
| `aggGroupDistinct` | Distinct values | = | — |
| `aggGroupNumbers` | Numbers | = | — |

## Chaves novas (não existem em `strings.js` hoje)

- **Plural (R7):** o auxiliar fica em `strings.js`:

  ```js
  // Picks the One or Other form of a plural pair: format(S[pluralKey("keyCount", n)], { count: n }).
  export function pluralKey(base, n) {
    return base + (Number(n) === 1 ? "One" : "Other");
  }
  ```

  Teste (0, 1 e 2): `pluralKey("x", 0)` = `xOther`; `pluralKey("x", 1)` = `xOne`; `pluralKey("x", 2)` = `xOther`.

- **Códigos de motivo de payload (R8):** o servidor envia estes códigos (`src/dmslog/derive/payloads.py`, constantes `R_*`) e `reasonText` (`payload-model.js`) os mostra crus hoje, porque não estão em `plReasonNames`. Propostas de fragmento, no mesmo estilo dos existentes (`plReason` = "Shown as is: {reason}"):

  | Chave nova | Texto proposto |
  |---|---|
  | `plReasonNames.no_message_id` | the record has no message id |
  | `plReasonNames.no_operation_for_key` | no operation has the same key |
  | `plReasonNames.no_request_for_correlation_id` | no request has this correlation id |
  | `plReasonNames.no_operation_ending_nearby` | no operation ends near this payload |
  | `plReasonNames.no_failed_operation_nearby` | no failed operation is near this payload |
  | `plReasonNames.ambiguous` | more than one operation matches |
  | `plReasonNames.not_in_operation` | the record is outside any operation |
  | `plReasonNames.too_many_payloads` | too many payloads in this operation |
  | `plReasonNames.scan_limit` | the scan stopped at its limit |

- **Tema (R12):** `themeSystem` = "System". `themeLight` e `themeDark` ficam como "Light" e "Dark" no menu "Theme" (`themeToggle` = "Theme").

## Notas para a aplicação (sessão principal)

- `progressDone` ("Indexed {records} records.") precisa receber `records` no ponto que hoje mostra "Indexing finished.".
- Os textos de `*Error` que ganham "Try again." não mudam o comportamento; só o texto.
- `exportCounts` mantém o nome do parâmetro `{events}`; renomear para `{operations}` é opcional (não aparece ao usuário).

## Testes de front que citam textos que mudam

Busca por trechos literais em `tests/web/` (valores antigos de chaves que mudam, sem placeholder). Só os casos abaixo dependem do texto:

| Arquivo:linha | Trecho no teste | Efeito |
|---|---|---|
| `tests/web/collections-model.test.mjs:65` | `"Indexing..."` (literal) | Quebra com R4 (`colIndexing` vira "Indexing…"). Trocar por `S.colIndexing`. |
| `tests/web/highlight-model.test.mjs:50` | `" or worse"` concatenado a `S.severityNames[2]` | Quebra com R6. Trocar por `S.severityAtLeast`. |
| `tests/web/highlight-panel-flow.test.mjs:184` | `" or worse"` concatenado a `S.severityNames[3]` | Quebra com R6. Trocar por `S.severityAtLeast`. |
| `tests/web/collections-model.test.mjs:62` | `"3,456 records in 12 sources"` | Não quebra: 3456 usa a forma `Other`, que não muda. |

Os demais testes usam `S.<chave>` e continuam valendo. "Copy as" aparece só num comentário em `copy-export-model.test.mjs`.

## Dúvidas de redação para o Jefferson

1. **"events" vira "operations" (R3).** Restart, Gap, Startup e Clock jump são eventos derivados, não operações. Usei "operations" para o painel inteiro, como na análise. Aceita, ou quer "events" nesses rótulos?
2. **Frases com duas contagens** (`colRecords`, `colRemoved`, `colRemoveConfirm`, `cfCreated`, `cfPartial`): o plural escolhe pela primeira contagem, então "1 record in 1 sources" pode sair. Aceita, ou reescrevo essas frases sem a segunda contagem?
3. **"…" em botões de painel (R5):** apliquei a "Export…", "Copy or export…", "Compare…", "Redaction settings…", "Bookmarks…", "Payloads…" e "Alerts…", que abrem painel com botão Fechar. Confirma, ou só para os que abrem diálogo de fato?
4. **Verbo + objeto (R10):** "Clear search", "Go to time", "Open path", "Apply filter", "Reset filters", "Add to list" e as ações "Delete …" de linhas. Aceita?

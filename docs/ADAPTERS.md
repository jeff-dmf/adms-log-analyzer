# Adapters e log: referência

Fonte: vault Obsidian do projeto `adms-v3.9` (`adms-knowledge-base/Concluído/12 Interfaces online/`).

"Online interface" e "Adapter" são equivalentes. Cada adapter tem uma sigla de 4 letras (ex.: AAVL = AVL Adapter). Lista completa em `Interfaces online.md`, com tipo (SOAP/WSDL, Arquivos, REST/OpenAPI), operações e versão.

## Onde achar

| O quê | Local no vault |
|---|---|
| Visão geral e tabela de adapters | `12 Interfaces online/Interfaces online.md` |
| Índice de cada adapter | `<Interface>/<SIGLA> Index.md` |
| Parâmetros de configuração (inclui log) | `<Interface>/Arquivos/Configuration/<SIGLA> Configuration.md` |
| Parâmetros consolidados | `Catálogos/Catálogo de parâmetros de configuração.md` |
| Códigos de erro | `Catálogos/Catálogo de códigos de erro.md` |
| Operações | `Catálogos/Catálogo de operações.md` |
| Exemplos de mensagens | `<Interface>/Arquivos/Examples/` |
| Mapeamentos | `<Interface>/Arquivos/Mapping/` |

## Parâmetros de log (por adapter)

| Parâmetro | Escopo | Valores | Padrão |
|---|---|---|---|
| `LogLevel` | Registry (adapter inteiro) | Fatal, Exit, Error, Always, DebugLog, Warning, Info, Verbose | DebugLog |
| `LogFileName` | Registry | caminho do arquivo | `${username}\${serviceName}\${appdomain}_${processid}.log` |
| `MessageLogLevel` | Por operação (WebServiceConfiguration) | DebugLog, Info, Verbose, None | Info |

`MessageLogLevel` define em que nível as mensagens trocadas (request/response) vão para o log. Ou seja, o conteúdo das mensagens só aparece se `LogLevel` for igual ou mais detalhado que o `MessageLogLevel` da operação.

A semântica de cada nível está no "Logging and Tracing Administration Guide" (citado nas configurações). Ainda não está no vault: confirmar com os logs de exemplo.

## Pendente (depende dos logs)

- Formato da linha (timestamp, nível, thread, componente, texto).
- Mensagens recorrentes por adapter e por nível.
- Como identificar início/fim de uma operação e correlacionar request/response.
- Mapeamento de erros para o catálogo de códigos de erro.

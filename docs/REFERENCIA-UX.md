# Referência de UX: LogViewPlus

Fonte: https://www.logviewplus.com/releasenotes.html e página inicial. Jefferson gostou da visualização e pediu como referência. Não é para copiar o produto, e sim aproveitar os conceitos que servem a logs de adapters ADMS.

## Conceitos a adotar

| Conceito do LogViewPlus | Equivalente neste projeto |
|---|---|
| Log Entry Grid com colunas dinâmicas (Date, Time, Level, Thread, Message, Elapsed) | Grid com `timestamp`, `PID`, `TID`, `nível`, `adapter`, `mensagem`; colunas extras extraídas por template (id da mensagem, operação, GID) |
| Message Templates (mensagem normalizada + parâmetros extraídos) | Catálogo de templates por adapter (ex.: `WFM-001`), obtido da análise em `docs/formato/`. É a base para filtrar, agrupar e contar |
| Parser com regex multi-pattern e entradas multilinha | Parser da linha `AAAA-MM-DD HH:MM:SS.mmm [PID][TID] NÍVEL - msg` com continuações (stack traces, XML, dumps) |
| Merge Files (várias fontes em uma linha do tempo) | Unir adapters e log normal + `.sensitive` de um mesmo ERS; indicador da origem em cada linha |
| Filter tree: texto, nível, thread, intervalo, AND/OR/NOT, exclude | Idem; filtro por adapter, template, operação, nível e janela de tempo |
| Highlights e Color Rules por prioridade | Cor por nível (ALWAYS, INFO, VERBOSE, DEBUGLOG) e destaque de erros/exceções |
| Graph Reports (linha do tempo de entradas) e "graphing by message" | Volume por hora/adapter/template; picos e gaps; latência de processamento |
| Dashboards com SQL e drill-down | Painéis prontos por adapter (throughput, erros, duração started→finished) com drill-down até as linhas |
| Session Rules (agrupar entradas relacionadas) | Correlação por id de mensagem/GUID e CorrelationID, incluindo log normal ↔ sensitive |
| Statistics View (por logger, thread, nível) | Contagens por adapter, thread, nível e template |
| Bookmarks com notas, Compare, export da análise | Marcar entradas com nota; comparar duas entradas; exportar a análise para anexar em chamado |
| Abrir Zip/Tar/Gz direto | Abrir o ZIP do ERS sem extrair manualmente (ler 2 GB sob demanda) |
| Partial opens, parsing sob demanda, async | Obrigatório: um adapter chega a 53 MB / 900 mil linhas, o BLTHost sensitive a 167 MB |
| Dark mode | Tema escuro (usuário usa Omarchy) |

## Fora de escopo, por ora

Fontes remotas (SFTP, banco, syslog/UDP), tail em tempo real, parsers de outros formatos (W3C, IIS, FIX) e SQL completo no estilo T-SQL. Um filtro/consulta simples vem antes de SQL.

## Decisão em aberto

Forma da interface (web local, TUI ou desktop) ainda não definida. A referência é uma aplicação desktop com grid, árvore de filtros e gráficos; isso pesa a favor de web local. Decidir depois do parser e do modelo de eventos.

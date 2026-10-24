# Frente RT, R1: achados ao abrir um ERS RT no visualizador

Método (2026-10-24): ingestão do ERS RT Primary de exemplo num caso local (11,17 M registros, 96 fontes selecionadas de 1.870) e levantamento pela API local do visualizador (`/api/sources`, `/api/events/stats`, `/api/unknown`, `/api/keys/types`, `/api/presets`) e por leitura do código da interface. A extensão do Chrome não estava conectada, então **não houve conferência visual das telas**; ela fica para quando o Jefferson estiver no PC (ver "Verificação visual pendente").

## O que funciona
- Operações derivadas: `FunctionHostJob` (73.786, 3.285 falhas), `LauncherJob` (43.655) e `SchedulerJob` (17.051, 74 falhas), com p50 de ~400 ms e p99 de ~2,9 s.
- Ligações: 60.648 `handoff` e 128.862 `response`; chaves `job_id` (73.846 valores), `gid`, `exc_type`, `client_id`, `op`, `svc_state`.
- Cobertura de templates: a cauda sem template tem 8.024 linhas nas 200 principais assinaturas (DMSMODEL 5.965, DMSJOB 1.456, DMSOMS 469, DMSCORE 106, DMSFH 28), de 11 M registros.
- Eventos `restart`: 0 neste ERS (os 19 reinícios medidos estão nos FunctionHost do Secondary); `gap`, `level_window` e `clock_jump`: 0.

## Defeitos e lacunas (entram na R2, R3 e R4)
1. **Rótulos de "adapter" em servidor RT** (`strings.js`: "Adapter dashboard", "Operations by adapter and name", "Adapter {family}", "All adapters", "Adapter: {title}"): famílias RT não são adapters. Trocar por um termo neutro ("process family" / "service") sem quebrar o ERS INT. Ponto de integração: `strings.js` (sessão principal).
2. **Sem atalhos (presets) das famílias RT:** `/api/presets` só tem os genéricos e os do BLTHOST. Faltam atalhos por família RT: jobs que falharam (`FunctionHostJob` com falha), erros e exceções por serviço, falhas do PSengine (R3), reinícios do FunctionHost.
3. **28 instâncias do FunctionHost como processos separados** (`DMS_FunctionHost_1..28`): ficam 28 entradas no filtro de processo e na árvore de arquivos. Avaliar agrupar as instâncias do pool sob um nome ("FunctionHost") com a instância como valor filtrável.
4. **94% das fontes não selecionadas** (1.774 de 1.870): é o escopo padrão (RT sem `PSengine`, `arbIP`, `NMC`, `ERS`, `.blg`, XML). **Não há controle na interface para incluir "todos os logs"**; só `--all-logs` na linha de comando. Define o desenho da R3 e da R4.
5. **Eventos RT ainda sem tela dedicada:** o painel de operações lista as três operações RT, mas não há visão da cadeia do job (Scheduler → Launcher → FunctionHost) além das ligações por registro; avaliar uma visão por `job_id` (o que cada serviço fez com o job, em ordem).
6. **`jms_job`, `fh_pid` e `new_pid` não aparecem em `/api/keys/types`:** os templates não gravam essas chaves neste ERS (só `job_id`, `gid` etc.); conferir se a lista fechada ainda faz sentido ou se o desenho previa mais.

## Verificação visual pendente
Percorrer no navegador: visão geral, árvore de arquivos com as 28 instâncias, painel de operações, aba de ligações de um registro do `DJB-003` (rótulo `handoff`), eventos de `exception` (56 em DMSMODEL) e presets. Registrar capturas só com dados sintéticos ou fora do repositório.

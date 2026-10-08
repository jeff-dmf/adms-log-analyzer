# ERS (Error Reporting System)

Fonte: vault adms-v3.9, módulo 11, `Provide Relevant Files for Issue Reporting` (PRFI 3.1, 3.3, 8, 9.9), e `PAIN 5.9 Log Parser`.

## O que é

Aplicação da Schneider (Start → Programs → Schneider Electric → ERS) que empacota em um `.zip` os dados de diagnóstico da máquina (servidor ou workstation): informações de ambiente, event viewer, modelo de rede, performance counters, save cases, extratos CIS e os logs dos processos. É o insumo padrão para abrir bug com a Schneider.

- Usuário informa uma descrição de até 20 caracteres e clica em **Create report**.
- Local dos arquivos: `C:\SchneiderElectricData\ADMS\ers`.
- ERS diário é gerado automaticamente em toda máquina e apagado após um período (ex.: 30 dias).
- ERS manual também captura um snapshot dos dados de alguns serviços em execução.
- Cada tipo de dado exige privilégios; o recomendado é abrir o ERS com o usuário DNAadmin.
- Pode ofuscar informação sensível via arquivo de configuração (a partir da 3.8.3).
- Inclui `FileSignature.csv` (verificação Authenticode, gerado por `CheckFileSignature.ps1`).

## Para adapters (PRFI 9.9)

Problemas de online integration pedem ERS do servidor **Hot DMS_Integration**. Quando o adapter conversa com serviços DMS_RealTime (IMS, CRS, WOM, NDS, TAS etc.), também do **Hot DMS_RealTime** (DMZ/BIZ e PROD/CORE). Para DMS_NetworkImport, do Hot DMS_NetworkImport (QA/STG). Além do ERS: passos de reprodução e as mensagens/requests envolvidos.

Consequência para o analisador: um ERS cobre uma máquina/zona; ao descompactar vários ERS, pode haver logs com o mesmo nome vindos de zonas diferentes (PAIN 5.9), então a zona/máquina de origem precisa fazer parte da identidade do log.

## Logs dentro do ERS (PRFI 3.3)

- `oasErrLog.log`: log do sistema. Recebe só Fatal, Exit, Error e Always (padrão).
- Um arquivo de trace por processo: `{processName}_{processId}.log` (ex.: `DMS_DynamicsService_8516.log`). Em `C:\SchneiderElectricData\OASyS\Servers\Log`.
- Níveis em ordem de severidade: 1 Fatal, 2 Exit, 3 Error, 4 Always, 5 DebugLog, 6 Warning, 7 Info, 8 Verbose.
- Níveis são **incrementais**: com Info, o trace inclui DebugLog, Warning e Info (e os de cima).
- Trocar em runtime: `toggleDebug -p <processId>` (cada execução sobe um nível; DebugLog→Verbose = 3 vezes) ou comando ToggleDebug no NMC. Com o serviço parado: XML em `C:\SchneiderElectricData\OASyS\Servers\config\Registry`.
- Falha de serviço: ver o fim de `oasErrLog` (linha "Fatal") e `.dmp` na mesma pasta de log.

## Ferramenta existente

O **Log Parser Viewer** (DMD, Job Summary / Function Execution Manager; base MS Log Parser) filtra por período, Job ID, texto e calcula diferença entre timestamps, mas é voltado a Job Manager, Core Function Service, Job Scheduler, Job Launcher e Function Host, não aos adapters.

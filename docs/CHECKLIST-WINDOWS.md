# Checklist de teste manual no PC Windows (release 0.1.0)

Executado uma vez pelo mantenedor, no PC corporativo de destino (issue #64, ROADMAP 8.5). Cobre só o que é **específico desse PC**: sem admin, sem internet, Edge e Chrome, antivírus e política da empresa. Os testes funcionais (grid, busca, painéis, correlação, payloads, exportação) vivem nas issues UAT A a H (guia: #49); aqui eles só são referenciados. Sobreposições: #8 (A2, pasta portátil), #11 (A5, pasta de instalação), #45 (H2, sem rede), #48 (H5, rotação). Execute este checklist junto dessas issues e comente PASS/FAIL em cada uma.

Convenções: `dmslog.bat` é o launcher da pasta portátil. Sem argumentos ele roda `serve`; com argumentos repassa tudo (`dmslog.bat info`). Marque cada item e, na tabela da seção 9, o resultado por navegador.

## 0. Pré-condições

- [ ] PC com Windows 10 ou 11 x64, usuário comum (sem admin), sem acesso à internet.
- [ ] Edge e Chrome atuais instalados. Anote as versões (`edge://version`, `chrome://version`): Edge ____, Chrome ____.
- [ ] Origem do zip: o artefato `dmslog-portable-win64` do job `portable-windows` da execução verde do CI na `main` (Actions > execução > Artifacts). O GitHub entrega o artefato como um zip que contém `dmslog-portable-win64.zip`; extraia o externo no PC com internet.
- [ ] No PC com internet, anote o tamanho e o SHA-256 do zip interno (`certutil -hashfile dmslog-portable-win64.zip SHA256`). Esperado: cerca de 12 MB. Levar o zip ao PC de destino pelo meio permitido pela empresa e repetir o `certutil` lá: os hashes devem ser iguais. Hash: ____
- [ ] O zip é a build pública (sem pack de conhecimento). Anote o commit do CI usado: ____

## 1. Copiar e abrir

- [ ] Extrair o zip em uma pasta sem espaços nem acentos (ex.: `C:\Users\<voce>\dmslog`; sem admin). Esperado: a pasta `dmslog-portable-win64` com `dmslog.bat`, `dmslog.pyz`, `python\` e `README.txt` (sem `knowledge\`).
- [ ] Na pasta extraída, abrir um terminal (`cmd`) e rodar `dmslog.bat --version`. Esperado: `0.1.0` e código de saída 0 (`echo %ERRORLEVEL%`).
- [ ] `dmslog.bat info`. Esperado: `dmslog: 0.1.0`, `fts5: yes`, `data directory: ...\AppData\Local\dmslog`, `knowledge pack: not installed (searched: ...)`. Se `fts5: no`, é falha bloqueante.
- [ ] Duplo clique em `dmslog.bat`. Esperado: uma janela de console mostra `dmslog is running at http://127.0.0.1:<porta>/...` com o token na URL e `Press Ctrl+C to stop.`; o navegador padrão abre sozinho na interface. Nenhum pedido de instalação, admin ou internet. Navegador padrão: ____
- [ ] Copiar a URL do console e abri-la manualmente **no outro navegador** (Edge se o padrão for Chrome, e vice-versa). Esperado: a interface abre e funciona nos dois, sem erro visível e sem erros no console do navegador (F12).
- [ ] Abrir a mesma porta sem o token (apagar o que vem depois de `?` na URL, em aba anônima). Esperado: acesso negado; nunca a interface.
- [ ] Encerrar (Ctrl+C), mover a pasta para um caminho **com espaços e acentos** (ex.: `C:\Users\<voce>\Área de Trabalho\dmslog teste`) e repetir `dmslog.bat info` e o duplo clique. Esperado: mesmo comportamento.
- [ ] Rodar com `dmslog.bat serve --no-browser --port 8765`. Esperado: nenhum navegador abre; a URL usa a porta 8765 em `127.0.0.1`.

## 2. Sem internet e só loopback (issue #45, H2)

- [ ] Com o servidor rodando e a interface aberta, em outro terminal: `netstat -ano | findstr LISTENING | findstr <porta>`. Esperado: uma única linha de escuta em `127.0.0.1:<porta>` (nunca `0.0.0.0` nem `[::]`). Anote o PID e confirme em `tasklist /FI "PID eq <pid>"` que é o `python.exe` da pasta portátil.
- [ ] `netstat -ano | findstr <pid>` durante o uso (abrir caso, filtros, painéis). Esperado: só conexões `127.0.0.1` <-> `127.0.0.1` (a do navegador); nenhuma para outro endereço.
- [ ] Opcional, mesma verificação no Monitor de Recursos (aba Rede, filtrando o `python.exe`): sem endereço remoto.
- [ ] Desligar a rede (modo avião ou desabilitar o adaptador), reiniciar o `dmslog.bat` e repetir o uso básico. Esperado: tudo funciona; nada espera por timeout de rede; nenhuma fonte, script ou imagem externa (aba Rede do F12: todas as requisições para `127.0.0.1`).
- [ ] De **outra** máquina da rede, abrir `http://<IP deste PC>:<porta>/`. Esperado: não conecta.

## 3. Pasta de dados

- [ ] Depois da primeira execução, conferir `%LOCALAPPDATA%\dmslog` (`explorer %LOCALAPPDATA%\dmslog`). Esperado: a pasta existe; nada é criado fora dela e da pasta portátil (`%APPDATA%`, `Documentos`, `%TEMP%` fixos do app, registro).
- [ ] Rodar `dmslog.bat --data-dir D:\dmslog-dados info` com a pasta em **outra unidade** (disco, USB ou unidade de rede permitida pela empresa). Esperado: `data directory: D:\dmslog-dados`.
- [ ] Repetir a ingestão (seção 4) com `--data-dir D:\dmslog-dados` e abrir o viewer com `dmslog.bat serve --data-dir D:\dmslog-dados`. Esperado: o caso aparece, o índice fica em `D:\dmslog-dados` e nada novo em `%LOCALAPPDATA%\dmslog\cases`.

## 4. Carregar logs (issues #9, #11, #13)

Use os **seus** ERS e a sua pasta `Log`. Não anote conteúdo, só contagens e tempos.

- [ ] ERS zip pela interface: criar um caso, informar o caminho do zip e abrir. Esperado: ingestão termina, registros aparecem (funcional: UAT A3).
- [ ] ERS zip pela linha de comando: `dmslog.bat ingest C:\caminho\ERS.zip --case teste-ers` e depois `dmslog.bat serve`. Esperado: resumo com registros, linhas e tempo; o caso `teste-ers` aparece no viewer. Repetir o `ingest`: rápido e sem duplicar (UAT A6).
- [ ] Pasta `Log` local: `dmslog.bat ingest C:\SchneiderElectricData\...\Log --case teste-log` (ou pelo viewer, informando o caminho). Esperado: `.log` e `.log.0` aparecem em ordem; o ADMS continua gravando e rotacionando; nenhum arquivo da instalação é alterado (UAT A5).
- [ ] Zip grande (o maior ERS disponível). Anote tamanho, tempo de ingestão e pico de memória no Gerenciador de Tarefas: ____ MB, ____ s, ____ MB. Esperado: termina sem erro de memória; a interface continua responsiva durante e depois.
- [ ] Caminho longo (> 260 caracteres): colocar uma cópia do zip (ou da pasta) em um caminho profundo e ingerir pelo caminho completo. Esperado: ingestão funciona. Se falhar, registrar o comprimento do caminho e a mensagem de erro exata (sem nomes reais). Anotar também se a política `LongPathsEnabled` do PC está ativa.
- [ ] Caminho com acentos e espaços no zip e na pasta (`C:\Users\<voce>\Área de Trabalho\logs ERS\...`) e entradas do zip com acentos. Esperado: ingestão funciona; o nome aparece sem corromper no viewer e no console.
- [ ] Zip inválido ou corrompido (renomear um `.txt` para `.zip`). Esperado: mensagem de erro clara, sem traceback e sem derrubar o servidor (UAT A7).
- [ ] Unidade de rede/UNC (`\\servidor\pasta\ERS.zip`), se a empresa usa. Esperado: ingestão funciona, ou erro claro; anotar o resultado.

## 5. Modo ao vivo e rotação (issue #48, H5)

Pré-requisito: o ADMS gravando em `C:\SchneiderElectricData\...\Log` (ou uma pasta de teste com `.log` sendo anexado).

- [ ] `dmslog.bat serve --watch C:\SchneiderElectricData\...\Log --watch-case vivo`. Esperado: o console mostra `Following ... into case vivo every 2 s`; novos registros aparecem no caso `vivo` em até alguns segundos.
- [ ] Com o app rodando, deixar o ADMS rotacionar um log (ou, em pasta de teste, renomear um `.log` para `.log.0` e criar um novo `.log`). Esperado: a rotação acontece sem erro de "arquivo em uso"; depois, os registros do `.log.0` e do novo `.log` aparecem sem perda nem duplicação.
- [ ] Com o app rodando e **sem fechá-lo**, renomear a pasta `Log` de teste e depois apagar um arquivo `.log` dela. Esperado: o Windows permite; o app não segura handle (nenhum "o arquivo está aberto em outro programa"). Restaurar o nome da pasta e confirmar que o app volta a seguir, ou mostra erro claro sem travar.
- [ ] Opcional: `dmslog.bat watch C:\...\Log --case vivo2` em um segundo terminal (sem o viewer). Esperado: mensagem `Watching ... Ctrl+C stops.` e o mesmo comportamento de rotação.

## 6. Antivírus, SmartScreen e política corporativa

Registrar o que ocorrer; não contornar proteções.

- [ ] Ao extrair o zip (marca "Mark of the Web"): o Explorer mostra aviso ou bloqueio nos arquivos? Anotar. Se necessário, desbloquear o zip (Propriedades > Desbloquear) **antes** de extrair, apenas se a política da empresa permitir.
- [ ] Primeiro duplo clique em `dmslog.bat`: SmartScreen, UAC ou aviso de "editor desconhecido"? Anotar o texto genérico do aviso.
- [ ] O antivírus/EDR bloqueia, põe em quarentena ou atrasa o `python\python.exe` embutido, o `dmslog.pyz` ou o `.bat`? Anotar o produto e o evento. Conferir se a pasta continua íntegra depois (`dir` com todos os arquivos).
- [ ] Política de execução de scripts, AppLocker/WDAC ou restrição de `.bat`/`python.exe` fora de pastas aprovadas: o app inicia em `C:\Users\<voce>`? Se não, anotar o local permitido que funcionou.
- [ ] O firewall do Windows pergunta sobre acesso à rede ao iniciar? Esperado: não pergunta (só loopback). Se perguntar, **negar** e confirmar que o app continua funcionando.
- [ ] Tempo de abertura a frio (primeira vez) e a quente: ____ s / ____ s (referência para varredura do antivírus).

## 7. Pack de conhecimento (opcional, uso interno)

O pack vem de documentação da Schneider Electric: **não sai do PC nem da empresa**. A build pública não o contém.

- [ ] Sem pack: no viewer, o painel Knowledge e os textos de erro mostram "knowledge pack not installed"; logs, eventos e configuração do ERS funcionam normalmente. `dmslog.bat info` mostra `knowledge pack: not installed (searched: ...)` com os locais pesquisados.
- [ ] Instalar o pack em `%LOCALAPPDATA%\dmslog\knowledge\pack\` (se usar `--data-dir OUTRA`, o pack passa a ser buscado em `OUTRA\knowledge\pack\`) (arquivos: `adapters.json`, `error_codes.json`, `manifest.json`, `operations.json`, `parameters.json`) copiando de uma fonte interna aprovada. Rodar `dmslog.bat info`. Esperado: `knowledge pack: installed (data_dir: ...; schema 1, generator ..., vault ...)`; reiniciar o `serve` e conferir que o painel agora mostra descrições e códigos de erro.
- [ ] Alternativa de local: remover o anterior e copiar para `knowledge\pack\` ao lado de `dmslog.bat` (mesma pasta). `dmslog.bat info` deve mostrar a origem como a pasta portátil. Ordem de busca: `--pack-dir DIR` (ou `DMSLOG_PACK_DIR`), depois `%LOCALAPPDATA%\dmslog\knowledge\pack`, depois `knowledge\pack` da pasta portátil.
- [ ] `dmslog.bat info --pack-dir C:\pasta\inexistente-ou-vazia`. Esperado: `not installed` ou `invalid, ignored (...)` com motivo; o app segue funcionando.
- [ ] Depois do teste, apagar o pack se o PC for compartilhado, e **nunca** incluir `knowledge\pack` em zip, e-mail ou issue.

## 8. Encerrar, reabrir e desinstalar

- [ ] Ctrl+C na janela do console. Esperado: `dmslog stopped.`; recarregar a aba do navegador falha (servidor parou). Repetir fechando a janela do console no X: a página também deixa de responder e `tasklist | findstr python` não mostra processo da pasta portátil.
- [ ] Reabrir com `dmslog.bat`. Esperado: os casos criados continuam lá, sem reindexar; a URL e o token mudam (a URL antiga não funciona).
- [ ] Desinstalar: apagar a pasta portátil e `%LOCALAPPDATA%\dmslog` (e a pasta de `--data-dir`, se usada). Esperado: apagam sem erro (nenhum processo ou arquivo preso).
- [ ] Conferir que nada ficou: sem entradas novas no registro (busque por `dmslog` no `regedit`, só leitura; ou nada em `HKCU\Software`), sem atalhos, sem tarefas agendadas (`schtasks /query | findstr /i dmslog`), sem arquivos em `%TEMP%` com o nome do app.

## 9. Resultado e como reportar

| Item | Edge | Chrome | Observações |
|---|---|---|---|
| 0. Pré-condições e hash | n/a | n/a | |
| 1. Copiar e abrir (caminho simples) | | | |
| 1. Abrir no outro navegador | | | |
| 1. Caminho com espaços e acentos | | | |
| 2. Só loopback (`netstat`) | | | |
| 2. Sem rede | | | |
| 3. Pasta de dados e `--data-dir` | | | |
| 4. ERS zip (interface e CLI) | | | |
| 4. Pasta `Log` local | | | |
| 4. Arquivo grande | | | |
| 4. Caminho longo (> 260) | | | |
| 5. `serve --watch` e rotação | | | |
| 5. Pasta renomeada/apagada com o app aberto | | | |
| 6. Antivírus / SmartScreen / política | | | |
| 7. Sem pack / com pack (`info`) | | | |
| 8. Encerrar, reabrir, desinstalar | | | |

Preencher com PASS, FAIL ou BLOQUEADO.

**Reportar falhas sem dados reais.** O repositório é público. Abrir uma issue (ou comentar nas UAT correspondentes, #8, #11, #45, #48) só com: versão (`dmslog.bat info`, sem a linha `data directory` se tiver nome de usuário), versão do Windows e do navegador, passo do checklist, esperado x observado e o texto genérico do erro. **Nunca** anexar logs, trechos de log, nomes de cliente, hosts, GUIDs, caminhos reais, capturas com dados, o pack ou a pasta `%LOCALAPPDATA%\dmslog` (os índices contêm conteúdo dos logs). Descreva com contagens, tamanhos e códigos de template/erro.

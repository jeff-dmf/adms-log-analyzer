# R5: parte RT do pack de conhecimento (desenho)

Desenho (Opus) da R5 da frente RT (`docs/ROADMAP.md`), para implementação por agentes Sonnet. Medições de 2026-10-24, por script (`python3 -I`, só leitura) sobre o vault `ecostruxure-v3.9` (`adms-knowledge-base/Concluído`, 4.448 notas `.md`) e sobre os templates RT da `main`. Nada do vault entra aqui: só estrutura, contagens, códigos de documento e números de seção. O pack continua fora do repositório (ARQUITETURA 7.1, D10).

## 0. Ponto de partida (medido)

### 0.1 O que o vault tem sobre RT

| Medida | Valor | Leitura |
|---|---:|---|
| Notas que citam `DMS_RealTime` (todas as grafias) | 61 em `Concluído` (+3 em `archive/`) | 24 são de adapters (seção 12: `<SIGLA> Configuration.md` e o catálogo de parâmetros), onde `DMS_RealTime` é **valor** de parâmetro (nome do serviço de contexto e apelidos; 31 linhas do catálogo); o resto são procedimentos de instalação, DR e plataforma que citam o grupo de serviços. Quase nada descreve os processos. |
| Processos RT do app (`families.toml`) | 33 | 29 `DMS_*`/`OMS_*` (DMSJOB 3, DMSCORE 11, DMSMODEL 7, DMSOMS 7, DMSFH 1 em pool) + PSengine, arbIP, NMC, ERS |
| Processos com seção própria no vault | 24 de 29 | SARC 5.4.3 (uma subseção por processo, 22) + SARC 5.4.1 e 5.4.9; seções curtas, de 150 a 2.600 caracteres de prosa, sem tabelas |
| Sem seção própria | 5 | LoadForecast, Export, ControlSequenceManagement (só citados em listas), ScheduleManagement e MajorEvent (**zero** notas) |
| Não `DMS_*` | 4 | arbIP → SARC 5.2.7; NMC → definição + notas de procedimento (78 notas citam a sigla); ERS → PRFI (14 notas); PSengine → só a lista de executáveis |
| Lista de executáveis por pasta de instalação | AVEL 2.1 | cobre 27 dos 29 `DMS_*`/`OMS_*` e PSengine/arbIP |
| Contadores de desempenho por processo | SCOM 5.6 (34 linhas, blocos por serviço: JobScheduler, Replication, OutageProcessing, PubSub, ...) e SCOM 5.11 (processos monitorados em CPU/RAM/HDD) | nomes de contadores, sem limites |
| Eventos de uso por processo | SMAN Usage Events (coluna `ApplicationKey`: CoreFunctions 11, LocalContextManager 8, NetworkModel 4, ...) | contagem por processo |
| Arquivos a coletar por área funcional | PRFI 3 a 12 (23 notas, 69 títulos de seção) | cita processos pelo nome sem prefixo (`DynamicsDataService`) |
| Níveis de log e troca em tempo de execução | PRFI 3.3 | conhecimento genérico, já refletido em `parse/severity.py` |
| Catálogo de **códigos de erro** RT | **nenhum** | os catálogos de erro do vault são só dos adapters (seção 12) |
| Catálogo de **parâmetros** RT | **nenhum** | só SQEO 3.5 (SQL Engine); o Registry RT do ERS segue lido pela camada 7.2 |
| Mensagens de log RT no vault | 18 de 680 templates RT (2,6%) têm um trecho literal ≥ 18 caracteres em alguma nota; quase todos genéricos | não há catálogo de mensagens dos serviços |
| Tabelas de mensagens de **aplicação** (fora da seção 12) | ≈ 800 linhas em 6 documentos (FLSR, SWVL, NCHK, TTDI, VVOP, SEST) | mensagens de função (FLISR, Network Checker, VVO, ...), não do serviço; ligação com o log do FunctionHost **não medida** (decisão D2) |

Conclusão: a parte RT do pack é um **catálogo de serviços** (papel, onde está descrito, onde é monitorado, o que coletar), não um catálogo de códigos. Códigos de erro e parâmetros RT ficam em zero por falta de fonte; o desenho não inventa linhas.

### 0.2 O que o app tem hoje

- `tools/kb_build.py` lê só `12 Interfaces online` (overview, `<SIGLA> Index`, `<SIGLA> Configuration`, três catálogos) e gera 5 arquivos (`adapters`, `error_codes`, `operations`, `parameters`, `manifest`). Tempo: 0,1 s. Pack local atual: 984 KB. O pack local está **desatualizado** em relação ao vault (`--check` acusa diferença nos 5 arquivos): regenerar antes de medir.
- `validate_pack` exige os 5 arquivos e **não rejeita** arquivo a mais; `load_pack_from` lê só `PACK_FILES`; `diff_pack` acusa `*.json` inesperado.
- `/api/knowledge?template=` responde dica curada (`hints.toml`), parâmetros da dica, adapter (`pack.adapter(family)`, sempre `null` para família RT) e `vault_note`. O painel de conhecimento do detalhe só aparece com dica ou código de erro resolvido.
- 680 templates RT (`_dmsrt` 85, DFH 95, DJB 95, DCR 113, DMD 90, DOM 134, PSE 21, ARB 15, NMC 3, ERS 29). Faixas de código por serviço já documentadas no cabeçalho de `dmsjob`, `dmscore`, `dmsmodel`, `dmsoms` e `dmsfh`. Chaves: `job_id`, `jms_job`, `fh_pid`, `new_pid`, `gid`, `incident_gid`, `inc`, `exc_type`.
- `FamilyClassifier.pool_of(family, process)` já resolve `DMS_FunctionHost_<n>` para o pool `DMS_FunctionHost`.

## 1. Decisões (resumo)

| # | Decisão | Por quê |
|---|---|---|
| P1 | Um arquivo **opcional** novo no pack, `rt_services.json`; `schema_version` continua 1 | aditivo: app antigo ignora o arquivo, app novo com pack antigo responde "desconhecido" para RT |
| P2 | A ligação processo ↔ seção do vault ↔ família ↔ faixas de template ↔ chaves fica num arquivo **curado no repositório**, `knowledge/curated/rt_services.toml`, **sem texto do vault**: só nomes de processo, códigos de documento e números de seção | o vault não diz qual processo de log corresponde a qual título; o mapeamento é conhecimento do app, como `process_map.toml` |
| P3 | Texto copiado do vault: só o **título** da seção e a **primeira frase** (até 240 caracteres) como `summary`; o resto é ponteiro (caminho da nota + número da seção) | o pack já guarda texto literal (descrições de parâmetro, detalhes de erro), mas aqui o mínimo basta; `--rt-summary none` gera o pack sem nenhuma frase (D1) |
| P4 | Referências (onde mais o processo aparece) só em uma **lista fechada de documentos**: SARC, PAIN, PRFI, SCOM, AVEL, SMAN, BCON, MNTN, INST, SWFS, SQEO, NDEX (≈ 450 notas) | limita o manifesto (cada nota lida entra em `files`) e o ruído; o pack não muda quando muda uma nota fora dessa lista |
| P5 | Sem códigos de erro, sem parâmetros e sem mensagens RT no pack nesta etapa | não há fonte; tabelas de mensagens de aplicação ficam para depois da medição (D2) |
| P6 | Template ↔ serviço pela **origem do registro** (processo), não pelo código; as faixas de código do TOML curado servem para os testes e para listar as chaves do serviço | um registro RT sempre tem processo; o código sozinho é ambíguo nas famílias com vários serviços (`_dmsrt` é de todos) |
| P7 | UI: só o painel de conhecimento do detalhe ganha um bloco "Service"; nenhuma tela, tooltip ou rota nova | mínimo útil; o painel "Same key" (R2) já cobre a navegação por chave |

## 2. Esquema

### 2.1 Arquivo curado no repositório: `src/dmslog/knowledge/curated/rt_services.toml`

Um registro por processo RT (33). Sem frases do vault; todo campo é nome, código ou número.

```toml
version = 1

[[service]]
process = "DMS_ExampleScheduler"      # nome do processo como na fonte (pool: nome do pool, sem _<n>)
family = "DMSJOB"                      # família de families.toml
group = "calculation"                  # lista fechada (abaixo)
codes = ["DJB-001..039"]               # faixas de template deste serviço dentro do arquivo da família
keys = ["job_id", "fh_pid", "new_pid"] # chaves que os templates dessas faixas emitem (conferido por teste)
related = [                            # outros serviços RT e a chave que os liga (cadeia do job, handoff)
  { process = "DMS_ExampleLauncher", via = "job_id" },
]
names = ["ExampleScheduler"]           # grafias extras procuradas no vault (default: processo e processo sem DMS_/OMS_)
vault = { doc = "SARC", note = "SARC 5.4.3 Example.md", section = "5.4.3.4.1" }
# ou: vault = "absent" (nenhuma seção própria no vault; o pack registra só referências)
```

| Campo | Regra |
|---|---|
| `process` | único; casa com um processo de `families.toml` (regra ou pool) |
| `family` | uma das famílias RT: `DMSFH`, `DMSJOB`, `DMSCORE`, `DMSMODEL`, `DMSOMS`, `DMSRT`, `PSENGINE`, `ARBIP`, `NMC`, `ERS` |
| `group` | `core`, `model_db`, `model_management`, `calculation`, `oms`, `platform`, `tool` (rótulos em `strings.js`) |
| `codes` | faixas `PFX-NNN..NNN` ou `PFX-SNNN..SNNN`; obrigatório nas famílias com vários serviços (DMSJOB, DMSCORE, DMSMODEL, DMSOMS); vazio = todos os templates próprios da família |
| `keys` | subconjunto da lista fechada de chaves RT; igual à união das chaves dos templates das faixas |
| `related.via` | uma das `keys` do serviço ou `"handoff"` |
| `names` | identificadores do produto (nome de executável, nome sem prefixo); nunca texto de amostra |
| `vault.note` | nome do arquivo da nota (único no vault, como `Vault.find`); `section` é o número da seção, nunca o título |

### 2.2 Arquivo do pack: `rt_services.json` (local, nunca no repositório)

Ordenado por `process`. Todo processo do TOML curado aparece, mesmo sem nada no vault (campos `null` ou vazios).

```json
{"rt_services": [{
  "process": "DMS_ExampleScheduler",
  "family": "DMSJOB",
  "title": "Example Scheduler",
  "summary": "Synthetic first sentence of the section.",
  "note": "01 Example/Example Architecture/SARC 5.4.3 Example.md",
  "section": "5.4.3.4.1",
  "install_dir": "Example\\bin",
  "monitored": ["cpu", "ram"],
  "counters": ["Example Queue Count"],
  "usage_events": 0,
  "references": [
    {"doc": "PRFI", "note": "11 Example/PRFI 4 Example.md", "section": "4.1.2", "mentions": 2}
  ]
}]}
```

| Campo | Tipo | Origem |
|---|---|---|
| `process`, `family` | string | TOML curado |
| `title` | string ou null | título da seção `vault.section` sem o número |
| `summary` | string ou null | primeira frase da seção (até 240 caracteres, corte em fim de frase; `null` com `--rt-summary none` ou `vault = "absent"`) |
| `note`, `section` | string ou null | caminho relativo ao vault e número da seção |
| `install_dir` | string ou null | AVEL 2.1: título `##` (caminho da pasta, sem a unidade nem `Program Files\<fabricante>`) cuja lista contém `<processo>.exe` ou um dos `names` + `.exe` |
| `monitored` | lista de `cpu`/`ram`/`hdd` | SCOM 5.11: tabelas cuja coluna de processos lista o serviço |
| `counters` | lista de strings | SCOM 5.6: primeira coluna das linhas do bloco em negrito cujo nome casa com o serviço (só o nome do contador; a descrição fica fora) |
| `usage_events` | int | SMAN Usage Events: linhas com `ApplicationKey` igual ao serviço |
| `references` | lista (até 8) | notas dos documentos da lista P4 que citam o processo (regex de palavra inteira sobre `process` e `names`), com o número da seção `#` mais próxima acima da primeira citação e a contagem; ordem: `doc` pela prioridade P4, depois `mentions` decrescente, depois caminho; a nota de `vault.note` não se repete |

Tamanho estimado: 33 registros × ~1,5 KB ≈ 50 KB; o manifesto cresce ≈ 450 entradas (≈ 60 KB).

### 2.3 Mudança mínima no carregador

- `pack_schema.py`: `OPTIONAL_PACK_FILES = ("rt_services.json",)`, `_RT_SERVICE_FIELDS`, `_RT_REFERENCE_FIELDS`; `validate_pack` valida o arquivo **se existir** (tipos, `monitored` na lista fechada, `references` ≤ 8, `family` na lista RT). `PACK_FILES` e `SCHEMA_VERSION` não mudam.
- `pack.py`: dataclasses `RtService` e `RtReference`; `Pack.rt_services: tuple[RtService, ...] = ()` (com default, então `EMPTY_PACK` e o pack antigo continuam válidos); `load_pack_from` lê o opcional quando existe; `Pack.rt_service(process)`.
- `resolve.py`: `Knowledge.rt_service(family, process) -> RtServiceInfo | None`. Junta o TOML curado (sempre disponível, mesmo sem pack: família, grupo, chaves, relacionados) com o registro do pack (título, resumo, nota, monitoramento, referências). Processo de pool é resolvido por `pool_of` antes da busca. Família não RT devolve `None`.
- `kb_build.py`: `diff_pack` passa a esperar `PACK_FILES + OPTIONAL_PACK_FILES`; `GENERATOR_VERSION = "1.1.0"`.

## 3. Extração em `tools/kb_build.py`

Fluxo novo, `build_rt_services(vault, curated)`, depois dos arquivos de adapters:

1. Lê o TOML curado (padrão: o do pacote; `--rt-curated PATH` para testes). Sem TOML ou com `--no-rt`, não gera `rt_services.json`.
2. Para cada serviço com `vault` tabela: `Vault.find(note)` em todo o vault (hoje `find` só indexa a seção 12: generalizar para a raiz, mantendo o erro de nome ambíguo), acha o título `#...` que começa com `section + "."`, lê o título e a prosa até o próximo título de mesmo nível ou superior. Seção não encontrada = `BuildError` (o vault mudou; o mantenedor corrige o TOML).
3. `summary`: primeira frase da prosa, sem links (`clean_cell`), sem marcação, cortada em 240 caracteres no último fim de frase; nada de listas, tabelas nem figuras.
4. AVEL, SCOM 5.6, SCOM 5.11 e SMAN: localizados por `Vault.find` do nome da nota (constantes no gerador, escritas com escapes como as atuais); leitura de estrutura (títulos, primeira coluna de tabela, listas separadas por espaço ou `•`). Nota ausente = campo vazio, não erro (o vault pode reorganizar documentos de apoio).
5. Referências: percorre só as pastas dos documentos da lista P4 (nota cujo nome começa por `<DOC> `); regex compilada uma vez por serviço; guarda número de seção e contagem, nunca o trecho.
6. Todo arquivo lido entra em `manifest.files` com sha256 (comportamento atual de `Vault.read`).

Fica fora: tabelas de mensagens de aplicação (D2), procedimentos (passos de BCON/MNTN/INST: só como referência), figuras, parâmetros SQL Engine, o texto das seções além da primeira frase, qualquer coisa de `archive/`. O gerador continua lendo só `.md`, sem executar nada, e recusando escrever dentro do repositório.

Saída do comando: além de "wrote N files", uma linha de cobertura `rt: <com seção>/<total> services with a vault section, <com referência>/<total> with references` (números só).

## 4. Uso na API e na UI

API (`api.py`, dono: sessão principal):
- `GET /api/knowledge?case=&template=&process=`: `process` opcional (mesmo padrão de nome de processo já validado em outras rotas; inválido = 400 `invalid_field`, texto novo em `messages.py`). Com processo de família RT, a resposta ganha `service`: `{process, family, group, title, summary, note, section, install_dir, monitored, counters, usage_events, references, related, keys, in_vault}`; sem pack, os campos do vault vêm `null`/vazios e `in_vault=false`, com o `knowledge_pack` de sempre. `vault_note` passa a cair na nota do serviço quando a dica não tem nota.
- **A resolução do serviço usa o PROCESSO de origem do registro, nunca a família do template.** `get_knowledge` consulta hoje `template.family`, e os templates compartilhados (`RTL-*`, da biblioteca `_dmsrt`, e os de `_common`) devolvem `_dmsrt` ou `_common`, famílias que o resolvedor não aceitaria; além disso `pool_of("_dmsrt", "DMS_FunctionHost_7")` retorna `None`. Por isso a rota resolve primeiro `process` (o que a interface envia, vindo do stream da fonte do registro selecionado) contra o `rt_services.toml` curado e o `pool_of` da família REAL do stream (que a interface também envia, `getFamily`), e só depois, se o processo não for de um serviço RT conhecido, deixa `service` ausente. A família do template não entra nessa decisão. Testes obrigatórios de API (R5e e a integração): um template `_dmsrt` e um `_common` selecionados em registro do `DMS_FunctionHost_7` devolvem `service` do pool FunctionHost; o mesmo template em registro de outro serviço RT devolve o serviço certo; processo desconhecido não devolve `service`.
- `/api/knowledge/error` não muda.

UI:
- `knowledge-model.js`: `buildKnowledgeView` aceita `templateData.service` e devolve `view.service` (texto via `textOrNull`, listas filtradas a string); o painel passa a aparecer também quando só há `service`.
- `knowledge.js`: bloco "Service" com título (ou o processo), grupo, resumo, pasta de instalação, monitoramento (CPU/RAM/HDD e contadores), serviços relacionados com a chave (`DMS_X (job_id)`), chaves do serviço, referências (doc + seção + caminho com o botão de copiar já existente) e o aviso "not described in the vault" quando `in_vault=false`. Tudo com `textContent`.
- `KnowledgeSection` recebe `getProcess(source_id)` como já recebe `getFamily`; a chave do cache passa a incluir o processo.

## 5. Testes (sem vault real)

| Teste | Arquivo | Conteúdo |
|---|---|---|
| Curado × catálogo | `tests/unit/test_rt_services_curated.py` (novo) | todo processo RT de `families.toml` (alternâncias das regras DMSJOB/DMSCORE/DMSMODEL/DMSOMS, pool DMSFH, PSENGINE/ARBIP/NMC/ERS) aparece uma vez; cada template próprio das famílias multisserviço cai em exatamente uma faixa; `keys` = união das chaves dos templates da faixa; `related.process` existe e `via` está nas chaves; campos fora do esquema = erro (impede texto livre) |
| Gerador | `tests/unit/test_kb_build.py` | mini-vault com notas RT **sintéticas** (`tests/fixtures/mini_vault/`, seção de arquitetura com 3 serviços, lista de executáveis, contadores, eventos de uso, duas notas de referência) e TOML curado de fixture via `--rt-curated`; bytes idênticos em duas execuções; `--rt-summary none`; seção ausente = `BuildError`; nota de apoio ausente = campo vazio; `--check` acusa `rt_services.json` divergente |
| Esquema e carga | `tests/unit/test_knowledge_pack.py` | `mini_pack/rt_services.json` sintético valida e carrega; pack sem o arquivo continua válido (`rt_services == ()`); campos errados geram erro |
| Resolução | `tests/unit/test_knowledge_resolve.py` | `rt_service` com e sem pack, processo de pool (`DMS_FunctionHost_7`), família não RT = `None` |
| API | `tests/unit/test_api_knowledge.py` | `service` presente com `process` RT; ausente sem `process`; 400 com processo inválido |
| Guarda | `tests/unit/test_check_no_pack.py` | `packaging/check_no_pack.py` reconhece um arquivo com o formato de `rt_services` (assinatura de campos de `pack_schema._RT_SERVICE_FIELDS`) em qualquer pasta e dentro de arquivo compactado; a exceção continua só para `tests/fixtures/knowledge/mini_pack/` com o aviso sintético |
| UI | `tests/web/knowledge-model.test.mjs` e `tests/web/knowledge-flow.test.mjs` | `buildKnowledgeView` com `service` e sem pack |
| Pack real (local) | `test_rt_services_curated.py`, marcado por `DMSLOG_TEST_REAL_PACK` | todo serviço com `vault` tabela tem `title` no pack real; pulado no CI |

Fixtures: só nomes `Example*`, caminhos inventados e frases sintéticas; `notice` do mini_pack continua começando por `SYNTHETIC TEST FIXTURE`.

## 6. Tarefas

Todas Sonnet, salvo indicado. Agentes não editam os pontos com dono único: `api.py`, `messages.py`, `api.js`, `strings.js`, `main.js`, `app-actions.js`, `index.html`, `bundle.css`, `test_web_assets.py`, `docs/ARQUITETURA.md` (7.1, tabela de mudanças) e `docs/ROADMAP.md` (R5). Esses ficam com a sessão principal, na integração de cada lote. Um PR por lote.

| # | Tarefa | Arquivos (exclusivos) | Depende de | Lote |
|---|---|---|---|---|
| R5a | Esquema e carga do opcional | `knowledge/pack_schema.py`, `knowledge/pack.py`, `knowledge/PACK_SCHEMA.md`, `tests/fixtures/knowledge/mini_pack/rt_services.json`, `tests/unit/test_knowledge_pack.py` | — | 1 |
| R5b | TOML curado (33 serviços; ler só títulos e números de seção do vault) e teste curado × catálogo | `knowledge/curated/rt_services.toml`, `tests/unit/test_rt_services_curated.py` | — | 1 |
| R5c | Extração no gerador | `tools/kb_build.py`, `tests/fixtures/mini_vault/**` (só arquivos novos RT), `tests/fixtures/knowledge/rt_curated_fixture.toml`, `tests/unit/test_kb_build.py` | R5a, formato de R5b | 2 |
| R5d | Guarda do CI | `packaging/check_no_pack.py`, `tests/unit/test_check_no_pack.py` | R5a | 2 |
| R5e | Resolução | `knowledge/resolve.py`, `tests/unit/test_knowledge_resolve.py` | R5a, R5b | 2 |
| R5f | UI do bloco "Service" | `web/js/knowledge-model.js`, `web/js/knowledge.js`, `web/css/knowledge.css`, `tests/web/knowledge-model.test.mjs`, `tests/web/knowledge-flow.test.mjs` (o `bundle.css` é regenerado pela sessão principal) | R5e; sessão principal entrega antes `api.py` (`process`, `service`), `messages.py`, `api.js`, `strings.js` (rótulos e grupos) e `main.js` (`getProcess`) | 3 |
| R5g | Gerar o pack real e medir (haiku) | nenhum arquivo do repo; relatório só com contagens | R5c | 3 |

Lote 1: R5a e R5b em paralelo. Lote 2: R5c, R5d e R5e em paralelo (arquivos disjuntos). Lote 3: a sessão principal integra a API e os pontos compartilhados, depois R5f e R5g. ARQUITETURA (7.1: linha `rt_services.json` na tabela do pack e parágrafo do TOML curado) e ROADMAP (R5 com subtarefas) pela sessão principal no PR do lote 1.

## 7. Critérios de aceite (medidos)

| Critério | Meta | Base medida no desenho |
|---|---|---|
| Serviços no pack | 33 de 33 em `rt_services.json` | 33 processos RT no `families.toml` |
| Com seção própria (`title` e `note`) | ≥ 27 | 24 em SARC 5.4.x + arbIP, NMC, ERS |
| Com ao menos uma referência | ≥ 30 | sem nenhuma nota: ScheduleManagement e MajorEvent |
| Templates RT das famílias multisserviço numa única faixa | 100% (432 templates: DJB 95, DCR 113, DMD 90, DOM 134) | faixas já nos cabeçalhos dos arquivos |
| Registros RT com bloco "Service" no detalhe | 100% dos processos do `families.toml` (pool incluído) | |
| Códigos de erro RT vindos do vault | 0, sem linha inventada | não há catálogo |
| Tempo do `kb_build` completo | ≤ 3 s no vault real | 0,1 s hoje; varredura de 4.448 notas com 39 regex: 0,7 s |
| Tamanho | `rt_services.json` ≤ 100 KB; pack total ≤ 1,15 MB | 984 KB hoje (pack desatualizado) |
| Determinismo | duas execuções com bytes idênticos | regra atual |
| Repositório | nenhuma frase do vault em `rt_services.toml`, fixtures, testes ou docs; guarda do CI verde e falhando com um `rt_services` de verdade | |

## 8. Riscos

| Risco | Mitigação |
|---|---|
| O vault renumera seções ou renomeia notas | `BuildError` com o processo e a seção; o TOML curado é a única coisa a corrigir; `--check` mostra o diff |
| Nome sem prefixo casa com outra coisa (`ExportService` dentro de outro nome) | regex de palavra inteira; `names` explícitos; teste com nota de fixture que contém um falso positivo |
| Resumo copiado demais | limite de 240 caracteres e uma frase; `--rt-summary none` |
| Manifesto grande e pack instável | lista fechada de documentos (P4) |
| Pack antigo, app novo ou o inverso | arquivo opcional; teste de carga sem o arquivo |
| Guarda do CI não reconhece o arquivo novo | R5d entra no mesmo PR do lote 2, antes de qualquer pack real ser gerado com o formato novo |
| Processo de log com grafia diferente do TOML (instância, `.ALTERNATE_<n>`) | resolução por `pool_of` e pela regra da família; teste com as formas de `families.toml` |

## 9. Decisões (padrões adotados em 2026-10-25)

O Jefferson pediu para não parar a frente RT enquanto está fora: a sessão principal adotou os padrões da tabela abaixo, inclusive a D2 (mensagens de aplicação ficam fora da R5, salvo medição que justifique). Todas são revisáveis.

| # | Pergunta | Padrão adotado (resolvido, salvo veto) |
|---|---|---|
| D1 | Guardar no pack a primeira frase da seção de cada serviço? | **Sim**, até 240 caracteres; `--rt-summary none` para gerar sem frase nenhuma |
| D2 | Incluir as tabelas de mensagens de aplicação (FLISR, Network Checker, VVO, State Estimation, validação de manobra, relatório de mapeamento de sinais; ≈ 800 linhas) como "mensagens de função" do FunctionHost? | **Não na R5**. Antes, uma medição (haiku, só contagens) de quantos registros DFH/DCR dos ERS RT de exemplo casam com essas mensagens; acima de 1% vira tarefa nova. **Precisa da decisão dele para entrar** |
| D3 | Escrever à mão descrição para os 5 serviços sem seção (e PSengine)? | **Não**: aparecem como "not described in the vault", com as referências que houver |
| D4 | Dicas curadas (`hints.toml`) para templates RT de falha? | Só onde o vault sustenta e citando a nota, até 20, em tarefa à parte depois da R5g; nenhuma no escopo mínimo |
| D5 | Tooltip do código de template na grade com o conhecimento? | **Não** (P7) |
| D6 | Subir `schema_version` do pack? | **Não** (P1) |

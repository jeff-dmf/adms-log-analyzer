# Adicionar uma família nova (adapter ou processo)

Passo a passo para dar templates a um log que hoje cai no nível genérico (ARQUITETURA 9.1a, tabela de níveis). Todos os passos foram executados em uma cópia do repositório com uma família fictícia (`WIDGET`, processo `WidgetSvc`); nenhum arquivo do repo foi alterado para testar.

## Pré-requisitos

- `.venv` do projeto com `pytest` e `ruff` (extra `dev` do `pyproject.toml`); Python >= 3.11.
- Um ERS (zip ou pasta) que contenha o log. Ele serve só para descobrir mensagens; nada dele entra no repo (fixtures e exemplos são sintéticos).
- Referências: `src/dmslog/adapters/_schema.md` (esquema dos templates, segurança de regex, parsers de continuação), `src/dmslog/adapters/_example.toml` (um template por recurso) e ARQUITETURA 6.3 a 6.5 e 9.1a.

## Passos

1. **Classificar o arquivo em `src/dmslog/families.toml`.** Inclua um `[[rule]]` com `family`, `layout` + `process` (ou `pattern`), `envelope` (`adms`, `oaserr` ou `plain`) e `scope`. As regras valem em ordem de arquivo e a primeira que casa vence: coloque a nova antes das genéricas (`PROCESS`, `SERVERLOG`, `INSTALL`, `ERS`), senão o log continua na família genérica. Exemplo:

   ```toml
   [[rule]]
   family = "WIDGET"
   layout = "single"
   process = "WidgetSvc"
   envelope = "adms"
   scope = "optional"
   ```

   Layouts disponíveis em `[layouts]`: `adapter` (`<Process>_<pid>[.sensitive].log`), `blthost`, `service`, `single` (`<Process>.log`) e `pidlog`. Chaves desconhecidas, `scope`/`envelope` inválidos e regex inválida levantam `FamiliesConfigError`. Famílias que já têm regra (NMC, PSENGINE, ARBIP, ...) pulam este passo: basta o passo 3.

2. **Minerar os candidatos a template.** Indexe o ERS com a família ainda sem templates e leia as assinaturas dos registros sem template:

   ```
   .venv/bin/python -m dmslog ingest <ERS.zip|pasta> --case mina --all-logs
   ```

   `--all-logs` é necessário quando a regra tem `scope = "optional"` (sem a flag o fonte aparece como "not selected"). Depois chame (com `dmslog serve` no ar) `GET /api/unknown?case=<id>&family=<FAMILIA>&limit=N`. A mesma informação está na tabela `unknown_sig` do `case.sqlite` (`family`, `signature`, `n`, `first_entry_id`), mais frequente primeiro. A assinatura vem de `parse/signature.py` (ARQUITETURA 6.7): números viram `<n>`, texto entre aspas `<s>`, GUID `<guid>`, caminho `<path>`, e assim por diante. Cada assinatura é um candidato a template; `first_entry_id` leva ao registro real para ver a continuação (payload, stack trace). Ordene o trabalho por `n`.

   Não verificado: `tools/mine_templates.py` (citado em ARQUITETURA 6.7 e no ROADMAP 8.3) não existe no repositório nem no histórico git; o fluxo acima é o que existe hoje. Entrada: um ERS indexado. Saída: assinaturas por família, sem esboço de TOML.

3. **Criar o TOML da família.** Copie `src/dmslog/adapters/_example.toml` para `src/dmslog/adapters/<familia em minúsculas>.toml` e ajuste:

   - `schema = 1`, `family = "<CODIGO>"` (igual ao `family` da regra), `description`, `extends = ["_common"]` (herda estatísticas, conexões, exceções, mapas e inicialização genéricos; os templates da família têm prioridade).
   - Um `[[template]]` por assinatura, com `code` (`ABC-001`), `class`, `sev`, `title`, `regex` (literal TOML `'''...'''`, capturas nomeadas, texto fixo no início) ou `text` (texto exato, despacho mais rápido), `fields` tipados, `keys`, `examples` e `counter_examples`.
   - `fields` e `keys` só quando o campo serve de chave de correlação (por exemplo um `msg_id` com `type = "msgid"`).
   - Regras de eventos (`[[operation]]`, `[[sensitive_pair]]`, `[[link_rule]]`) só depois que os templates de início, fim e falha existirem; sem elas a família simplesmente não gera eventos (ARQUITETURA 9.1a).

   Todos os arquivos `*.toml` de `src/dmslog/adapters/` (exceto `_example.toml`) são carregados automaticamente por `load_catalog()`; não há lista para atualizar.

4. **Validar o catálogo.**

   ```
   .venv/bin/python -c "from dmslog.parse.templates import load_catalog; load_catalog(check_examples=True)"
   ```

   Falha com `SchemaError`, `DuplicateError`, `InvalidRegexError`, `UnsafeRegexError` ou `ExampleError` citando arquivo e código. `check_examples=True` confere que cada exemplo positivo cai no próprio template (com os campos declarados), que nenhum contra-exemplo casa, e que nenhum exemplo do arquivo casa com outro template do mesmo arquivo.

5. **Reindexar em um caso novo e conferir.** Repita o `ingest` do passo 2 com outro `--case`. Reindexar o mesmo caso não reclassifica: o fonte aparece como "unchanged" e os registros continuam sem template. Confira em `GET /api/unknown` (ou em `unknown_sig`) que a contagem caiu e que os registros receberam o código esperado.

6. **Entrar nas fixtures e nos testes (só se a família for um adapter ou processo mantido no repo).** Em `tools/make_family_fixtures.py`, acrescente a família ao dict `PROCESSES` (`"WIDGET": ("single", "WidgetSvc")`; o layout é o mesmo da regra), de preferência no fim para não mudar os PIDs das fixtures existentes. Regenere e confira:

   ```
   .venv/bin/python tools/make_family_fixtures.py
   .venv/bin/python tools/make_family_fixtures.py --check
   ```

   Se o `dmslog` instalado no ambiente não for o deste checkout, rode com `PYTHONPATH=src`; o gerador usa o catálogo que importar. Sem esse passo, `test_every_template_of_the_catalog_is_hit` falha ("templates without a fixture record"). Adapters com `anchor = true` também precisam de linha em `src/dmslog/knowledge/curated/process_map.toml` (`test_process_map.py`).

7. **Testes e lint (o mesmo que o CI roda).**

   ```
   .venv/bin/ruff check
   .venv/bin/python -m pytest -q
   ```

   Para iterar rápido: `.venv/bin/python -m pytest -q tests/unit/test_templates.py tests/unit/test_catalog_common.py tests/unit/test_family_fixtures.py tests/unit/test_discover.py tests/unit/test_process_map.py tests/unit/test_conventions.py`.

## Checklist de aceite

- [ ] Regra em `families.toml` antes das genéricas; `classify` devolve a família esperada (`tests/unit/test_discover.py` como modelo).
- [ ] `<familia>.toml` carrega com `load_catalog(check_examples=True)`.
- [ ] Cada template tem `examples` e `counter_examples` sintéticos (sem GUID, host, e-mail, IP ou número longo reais).
- [ ] Reindexação do ERS em caso novo: registros da família com template; sobras só em `unknown_sig`.
- [ ] Família em `PROCESSES` de `tools/make_family_fixtures.py`, fixtures regeneradas e `--check` limpo.
- [ ] `ruff check` e `pytest -q` verdes (CI roda em Linux e Windows).
- [ ] Mudança de estrutura ou de esquema registrada em `docs/ARQUITETURA.md`, seção 15; formato dos logs novos descrito em `docs/formato/`.

## Armadilhas

- **Ordem em `families.toml`:** a primeira regra que casa vence; uma regra nova depois de `PROCESS` ou `SERVERLOG` nunca é usada.
- **`scope = "optional"` não indexa por padrão.** Sem `--all-logs` (ou seleção na UI) o fonte fica "not selected" e o teste manual parece não funcionar. `default` e `service` entram sozinhos (ver a tabela no cabeçalho de `families.toml`).
- **Reindexar o mesmo caso não reaplica templates novos** (fonte "unchanged"). Use outro `--case`.
- **O carregador não exige que `family` exista em `families.toml`** (verificado com uma família sem regra): um TOML órfão carrega e nunca é usado. A coerência é sua.
- **O carregador não exige `examples`/`counter_examples`** (verificado), mas os testes por grupo exigem para as famílias já cobertas e a revisão deve exigir para as novas.
- **Capturas sempre nomeadas** (`(?P<nome>...)`, snake_case minúsculo); agrupamento com `(?:...)`.
- **Regex insegura é rejeitada na carga** (`UnsafeRegexError`): sem flags inline (`(?i)`), sem backreference, sem repetição de algo opcional, alternado ou repetido dentro de repetição (`(?:\d+,)*` é rejeitado; use `[\d,]+`), no máximo 1000 caracteres e 24 grupos. Regex com duas repetições ambíguas pede `window` menor (até cerca de 720). Detalhes e limites em `_schema.md`, "Regex safety".
- **Templates casam com a primeira linha já sem espaços** (`strip()`), por `re.match`; escreva o texto fixo no início (`^...`) e termine com `$`/`\.$` quando o log termina assim.
- **Textos exatos (`text`) vencem qualquer regex**, e códigos, aliases e textos são únicos no catálogo inteiro (`DuplicateError`). Um exemplo positivo que outro template do mesmo arquivo também casa falha o `check_examples`.
- **Templates genéricos (`_common`) vêm depois dos da família.** `tests/unit/test_catalog_common.py` exige que um exemplo genérico, numa família que estende `_common`, caia no template genérico ou em um template específico da família; se o seu template roubar um exemplo `GEN-*`, ele precisa entrar na lista `group3_overrides` daquele teste (aprovação do Opus: muda a estrutura de testes do catálogo).
- **Ingestão idempotente por fonte**, mas o gerador de fixtures usa o `dmslog` que importar: ambiente com instalação de outro checkout gera fixtures sem os seus templates e o `--check` do teste acusa diferença.
- **Convenções cobradas por `tests/unit/test_conventions.py`:** nenhum caractere acentuado (Latin-1) em `src/` e `tests/` (inclui `.toml`, comentários e docstrings; só `fixtures/`, `samples/` e `vendor/` ficam de fora), nada de `innerHTML` na UI, sem CDN, e servidor só em `127.0.0.1`. Texto de log com acento vai como escape ou em fixture, não literal em TOML de `src/`.
- **Idioma:** templates, títulos e descrições em inglês (CLAUDE.md do projeto); o texto das mensagens dos adapters fica literal.
- **Severidade:** `sev` é só uma dica (1 crítico, 2 erro, 3 aviso, 4 info, 5 ruído); a decisão final é de `parse/severity.py` (ARQUITETURA 6.4). Use `fts = false` só para ruído de alto volume.
- **Famílias com envelope novo** (formato de linha que não é `adms` nem `oaserr`) precisam de uma classe em `parse/envelope.py` registrada em `ENVELOPES` (ARQUITETURA 9.1a); isso é decisão de arquitetura, não deste fluxo. Não verificado: nenhum envelope novo foi criado nesta rodada.

# Processo de release

Vale para a 0.1.0 e para as seguintes. A release é feita pelo workflow `.github/workflows/release.yml`, a partir de uma tag `vX.Y.Z` na `main`.

## Pré-requisitos para publicar (pré-release)

- `main` com CI verde (Linux e Windows) e com tudo o que a versão inclui já integrado (ver `docs/ROADMAP.md`: marcos e 8.1 a 8.5). O UAT e o teste manual no Windows acontecem **depois** da publicação, sobre a pré-release.
- A versão está igual em `pyproject.toml` e em `src/dmslog/__init__.py`.
- `CHANGELOG.md` tem a seção `## [X.Y.Z]` da versão. O texto dela vira as notas da release.
- Nenhum pack de conhecimento no repositório (a guarda `packaging/check_no_pack.py` roda no workflow).

## Guarda de desempenho da interface (passo manual)

Antes de cada release, na máquina de desenvolvimento (Node >= 22 e Chrome ou Edge instalado), rodar `node tools/ui_perf.mjs --cpu 4` na raiz do repositório. A ferramenta gera um caso sintético com `tools/bench.py --quick` (nunca usa `local-samples/`), sobe `dmslog serve`, mede abertura, recarga, CLS, rolagem e INP, imprime a tabela e o JSON e compara com os orçamentos de `tools/ui_perf_budgets.json` (análise 3, seção 5). Sai com código diferente de zero se algum orçamento for excedido; `--large` usa o caso de 500 MB (cerca de 3,5 milhões de registros, ~1,5 GB de disco temporário) e `--report-only` só relata. Registrar o resultado no PR ou nas notas da release. Servidor, navegador e pastas temporárias são encerrados e removidos ao final. Um job de CI só em Linux, sem bloquear o merge, fica para depois.

## Ensaio (sem publicar)

Em Actions, rodar o workflow `release` manualmente (`workflow_dispatch`) na `main`. Ele constrói e verifica tudo e gera o artefato `release-bundle` do run, mas **nunca** cria release. Baixar o artefato e conferir como abaixo.

## Publicar

1. Atualizar a `main` local e conferir que o commit é o que deve sair.
2. Criar e enviar a tag anotada:

   ```
   git tag -a v0.1.0 -m "dmslog 0.1.0"
   git push origin v0.1.0
   ```

3. Acompanhar o run `release` da tag. A release só aparece se todos os jobs passarem. Ela é publicada como **pré-release** (decisão do Jefferson, 2026-10-12) e continua assim até o app estar 100% pronto, com o UAT e o teste manual em um PC Windows sem admin e sem internet concluídos (`docs/CHECKLIST-WINDOWS.md`, no Edge e no Chrome); só então a promoção a release normal é manual (página da release > Edit > desmarcar "Set as a pre-release" e marcar "Set as the latest release").

## O que o workflow faz

1. `verify`: confere que a tag é `v` + versão do pacote (as duas fontes devem concordar) e que o CHANGELOG tem a seção; roda `ruff check`, a guarda `check_no_pack.py` sobre a árvore e a suíte `pytest` (Linux).
2. `build-linux`: constrói `dmslog.pyz` e `dmslog-linux.tar.gz`; guarda sobre os artefatos; smoke do zipapp e do launcher.
3. `build-windows`: constrói a pasta portátil (Python embeddable 3.13 + `dmslog.pyz` + `dmslog.bat` + `README.txt`) **sem** `--pack`, guarda, smoke com o Python embutido (não o do runner) e zip `dmslog-<versão>-windows-portable.zip`.
4. `bundle`: reúne os arquivos, gera `SHA256SUMS`, extrai as notas do CHANGELOG, roda a guarda de novo sobre os arquivos finais e confere checksums e `--version` do zipapp.
5. `publish` (só em tag; usa `contents: write`, único job com essa permissão): `gh release create --prerelease` com os 4 arquivos e as notas. Sem segredos além do `GITHUB_TOKEN`, sem upload para serviços externos.

Qualquer falha em `verify`, nas guardas ou nos smokes interrompe o fluxo antes de publicar.

## Verificação dos artefatos

Na página da release (ou no artefato `release-bundle` do ensaio):

```
sha256sum -c SHA256SUMS
python3 -I dmslog.pyz --version
python3 -I dmslog.pyz info        # fts5: yes; knowledge pack: not installed
python3 packaging/check_no_pack.py --artifact dmslog.pyz dmslog-linux.tar.gz dmslog-0.1.0-windows-portable.zip
```

No Windows, descompactar o zip e rodar `dmslog.bat --version` e `dmslog.bat info`. A release publicada deve ter exatamente: `dmslog.pyz`, `dmslog-linux.tar.gz`, `dmslog-<versão>-windows-portable.zip` e `SHA256SUMS`.

## O que NÃO fazer

- Não passar `--pack` a nenhum build público e não incluir o pack em release, commit, issue, PR ou fixture. O pack só entra em build interno, fora do GitHub.
- Não criar a tag antes da versão e do CHANGELOG estarem na `main`; não reutilizar nem mover uma tag já publicada (corrigir com uma nova versão).
- Não publicar a release manualmente, nem anexar arquivos construídos na máquina local.
- Não rodar o ensaio esperando release: o `workflow_dispatch` nunca publica.
- Não alterar permissões do workflow nem trocar ações por tags móveis (ações ficam fixadas por SHA; Dependabot atualiza).

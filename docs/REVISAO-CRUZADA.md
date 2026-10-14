# Revisão cruzada e merge autônomo

> **Reativado e otimizado (decisão do Jefferson em 2026-10-09):** um agente revisa o PR do outro, em vez de o Jefferson aprovar manualmente, com o revisor padrão barato (`gpt-6.1-sol` no Codex; Sonnet no Claude). Opus e `gpt-6-astra` só em caso extremamente complexo (seção "Caso extremamente complexo"). Isto substitui a suspensão de 2026-10-08.

Decisão do Jefferson em 2026-10-08. A regra canônica está em `../CLAUDE.md`; este documento descreve sua execução. A sessão principal pode criar branches, commitar, publicar PRs e fazer merge dentro do escopo solicitado, sem pedir aprovação manual do Jefferson em cada etapa.

## Quem revisa

| Autor das alterações | Revisor final |
|---|---|
| Codex | Claude Code, revisor padrão Sonnet |
| Claude Code | Codex, revisor padrão `gpt-6.1-sol` |

O revisor trabalha em uma sessão independente e recebe o escopo, a base, o SHA final, o diff completo e os resultados das verificações. Ele não relê o cânone inteiro: usa o `AGENTS.md` (carregado automaticamente) e só os arquivos e trechos indicados no pacote do autor (ver "Custo e níveis de revisão"). Avalia o conteúdo sem editar arquivos nem fazer commit, push ou merge. Revisões auxiliares pelo mesmo harness não substituem a revisão cruzada. Se o PR tiver contribuição dos dois harnesses, cada um revisa as alterações do outro no mesmo SHA final.

Quando houver ferramentas de leitura disponíveis, o revisor obtém o diff diretamente do repositório para o SHA informado. Se receber apenas um pacote de contexto sem ferramentas, declara essa limitação no parecer; a sessão principal confere que o pacote contém o diff completo do SHA e preserva a evidência.

## Fluxo

1. A sessão principal implementa em uma branch, executa as verificações apropriadas, cria os commits e publica o PR. Não commitar diretamente na `main`.
2. Invoca o outro harness para revisar o SHA final. O revisor identifica problemas concretos e entrega `APPROVED` ou `CHANGES_REQUESTED`, com justificativa, achados e verificações realizadas. Falha de execução ou ausência de parecer não é aprovação.
3. Registra no PR o parecer produzido pelo revisor, identificando harness/modelo, SHA e evidência. Usa review formal quando a identidade GitHub do revisor é diferente da do autor. Se a conta é a mesma, registra a aprovação do modelo em comentário; esse comentário não é uma review formal `APPROVE` do GitHub.
4. Corrige todos os achados bloqueantes e repete a revisão após qualquer novo commit. Um parecer sobre SHA anterior não autoriza o merge do SHA atual.
5. Confirma que o SHA do PR continua sendo o aprovado e que os checks obrigatórios passaram, incluindo Linux e Windows. Faz merge sem pedir confirmação ao Jefferson, respeitando as proteções do repositório e sem bypass administrativo.
6. Sincroniza a `main` local e informa o PR integrado e a validação ao Jefferson.

No CI atual (`../.github/workflows/ci.yml`), os três jobs exigidos são `test (ubuntu-latest)`, `test (windows-latest)` e `portable-windows`. Todos devem concluir com sucesso; pendente, ausente, falho ou cancelado não atende ao requisito. A exigência vale também sem proteção de branch configurada no GitHub. **Exceção (2026-10-14):** em PR que só altera `docs/**` ou arquivos `*.md` da raiz, o job `changes` do CI detecta isso e os jobs pesados continuam existindo com os mesmos nomes, mas sem executar etapas (terminam em segundos com sucesso); a classificação falha para o lado seguro (qualquer erro, renomeação ou arquivo fora de `docs/**` e `*.md` da raiz liga tudo). O CI cancela sozinho a execução do commit anterior quando o PR recebe um commit novo, e os jobs de empacotamento (`pyz-linux` e `portable-windows`) rodam em paralelo com os testes; o pytest roda com `-n auto` (pytest-xdist).

O parecer aprovado deve permitir identificar o SHA, o autor das alterações, o revisor, o escopo revisado e as verificações. Não afirmar que um teste foi executado pelo revisor quando ele apenas conferiu um resultado de CI.

## Custo e níveis de revisão

Medido em 2026-10-08: quatro revisões do Codex custaram de 66 mil a 130 mil tokens cada (cerca de 437 mil no total), principalmente porque o prompt mandava ler quase todo o cânone (`cat` de três documentos de formato inteiros, `ARQUITETURA.md` em grandes faixas, `ROADMAP.md`, `CLAUDE.md`) e porque rodava em `xhigh`. O efeito dos hooks do ai-memory nas sessões de revisão não foi medido.

**Nível pelo risco do PR** (o autor classifica e informa no prompt; na dúvida, o nível acima):

| Nível | Quando | Revisor no Codex | Revisor no Claude |
|---|---|---|---|
| T0 | Documentação de processo, redação, links, regras sem efeito em código ou esquema | `gpt-6.1-sol`, `medium` | Sonnet |
| T1 | Código rotineiro, testes, CI, empacotamento (use `codex review --base main` quando o PR é só código) | `gpt-6.1-sol`, `high` | Sonnet |
| T2 | Segurança, concorrência, parser e algoritmos, esquema e arquitetura, documentos que fixam decisões | `gpt-6.1-sol`, `high` | Sonnet (esforço alto) |

**Opus e `gpt-6-astra` só em caso extremamente complexo**, que é um destes (o autor justifica no PR):
1. dúvida de segurança que o revisor padrão não consegue descartar nem reproduzir, e que o autor também não consegue refutar com teste;
2. correção de algoritmo concorrente ou incremental que os testes não cobrem, em que o revisor padrão pediu uma segunda opinião;
3. dois ciclos seguidos de `CHANGES_REQUESTED` com discordância técnica entre autor e revisor padrão;
4. pedido explícito do Jefferson.
Mesmo nesses casos: uma única passada em `high` (`xhigh` só a pedido), com o pacote enxuto e restrita à dúvida concreta, não ao PR inteiro.

**Orçamento de contexto** (vale para qualquer revisor):
1. O autor monta o pacote: `git diff --stat`, base e SHA, nível, até 5 afirmações que sustentam o desenho, cada uma com o comando ou script do autor e o resultado já medido, e a lista de arquivos e intervalos de linhas que o revisor deve abrir além do diff.
2. Proibido `cat` de documento com mais de 200 linhas: usar `rg -n` e `sed -n 'a,bp'`. Não reler `CLAUDE.md` (o `AGENTS.md` é o resumo) nem este protocolo (o prompt traz o checklist). Limite de referência: cerca de 60 KB lidos além do diff e no máximo 10 comandos.
3. Conferências independentes: no máximo 3, só das afirmações que decidem o desenho, por amostragem; o revisor não reconstrói a análise do autor. CI já cobre lint e testes: não reexecutar.
4. Re-revisão olha só o diff incremental (`SHA_ANTERIOR..SHA_NOVO`) mais a lista de achados anteriores; não reabre o resto.
5. Saída curta: primeira linha `APPROVED` ou `CHANGES_REQUESTED`; até 8 achados com arquivo:linha e correção; "Verificado:" em uma linha por item, separando leu, executou e confiou no autor. Sem tabelas longas.
6. Agrupar: ajustes de redação e correções pequenas entram no próximo PR relacionado, em vez de abrir PR (e revisão) só para eles. Um marco, um PR por vez para revisão.

Prompt-modelo para o Codex (preencher os campos):

```text
Revisor cruzado (papel arquiteto), PR #<n>, nível <T0|T1|T2>. Autor: Claude Code.
Base <sha> / SHA final <sha>. Diff: git diff <base> <sha>. (Re-revisão: só <sha_ant>..<sha>; achados anteriores: <lista>.)
Abra além do diff só: <arquivo:linhas>, ... Não dê cat em documentos; use rg -n e sed -n.
Afirmações a conferir por amostragem (máx. 3 conferências suas): 1) ... [comando do autor] -> [resultado do autor]
CI deste SHA: <resultado>. Somente leitura; sem editar, commitar ou publicar.
Resposta: 1ª linha APPROVED ou CHANGES_REQUESTED; até 8 achados (arquivo:linha, correção); "Verificado:" 1 linha por item (leu/executou/confiou).
```

## Aprovação manual e exceções

Se o outro harness estiver indisponível (por exemplo, limite de uso), o PR fica aberto, como em "Bloqueios". O Jefferson pode aprovar manualmente; nesse caso a sessão registra a aprovação em comentário no PR (SHA, quem aprovou, por quê, evidência disponível). A revisão cruzada daquele PR fica **dispensada** e não é retomada depois, salvo pedido do Jefferson.

Exceções registradas:
- **PR #10** (M6.1, descoberta de formato dos payloads), SHA `78479ff`, mergeado em 2026-10-08 como `94f3c5b`: aprovado manualmente pelo Jefferson porque o limite de uso do Codex acabou durante a revisão. O Codex **não** deve retomar essa revisão quando houver créditos.

## Bloqueios

Sem acesso ao outro harness, com achados bloqueantes, checks falhando ou proteções exigindo uma identidade GitHub distinta, o PR permanece aberto e a sessão informa o bloqueio. Não substituir revisão cruzada por autorrevisão nem alterar proteções para permitir o merge. As aprovações de sandbox e controles externos continuam independentes desta autorização de trabalho.

## Continuidade

O ai-memory guarda a decisão por solicitação explícita do Jefferson e aponta para os arquivos canônicos. Pareceres e resultados de CI ficam no PR. Handoffs devem identificar PR, SHA, harness autor/revisor e pendências; uma aprovação lembrada sem evidência no PR e sem correspondência com o SHA atual não permite merge.

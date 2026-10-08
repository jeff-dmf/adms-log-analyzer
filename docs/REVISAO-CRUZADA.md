# Revisão cruzada e merge autônomo

Decisão do Jefferson em 2026-10-08. A regra canônica está em `../CLAUDE.md`; este documento descreve sua execução. A sessão principal pode criar branches, commitar, publicar PRs e fazer merge dentro do escopo solicitado, sem pedir aprovação manual do Jefferson em cada etapa.

## Quem revisa

| Autor das alterações | Revisor final |
|---|---|
| Codex | Claude Code, papel Opus/arquiteto |
| Claude Code | Codex, papel `arquiteto` |

O revisor trabalha em uma sessão independente e recebe o escopo, a base, o SHA final, o diff completo e os resultados das verificações. Ele lê as instruções canônicas e avalia o conteúdo sem editar arquivos nem fazer commit, push ou merge. Revisões auxiliares pelo mesmo harness não substituem a revisão cruzada. Se o PR tiver contribuição dos dois harnesses, cada um revisa as alterações do outro no mesmo SHA final.

Quando houver ferramentas de leitura disponíveis, o revisor obtém o diff diretamente do repositório para o SHA informado. Se receber apenas um pacote de contexto sem ferramentas, declara essa limitação no parecer; a sessão principal confere que o pacote contém o diff completo do SHA e preserva a evidência.

## Fluxo

1. A sessão principal implementa em uma branch, executa as verificações apropriadas, cria os commits e publica o PR. Não commitar diretamente na `main`.
2. Invoca o outro harness para revisar o SHA final. O revisor identifica problemas concretos e entrega `APPROVED` ou `CHANGES_REQUESTED`, com justificativa, achados e verificações realizadas. Falha de execução ou ausência de parecer não é aprovação.
3. Registra no PR o parecer produzido pelo revisor, identificando harness/modelo, SHA e evidência. Usa review formal quando a identidade GitHub do revisor é diferente da do autor. Se a conta é a mesma, registra a aprovação do modelo em comentário; esse comentário não é uma review formal `APPROVE` do GitHub.
4. Corrige todos os achados bloqueantes e repete a revisão após qualquer novo commit. Um parecer sobre SHA anterior não autoriza o merge do SHA atual.
5. Confirma que o SHA do PR continua sendo o aprovado e que os checks obrigatórios passaram, incluindo Linux e Windows. Faz merge sem pedir confirmação ao Jefferson, respeitando as proteções do repositório e sem bypass administrativo.
6. Sincroniza a `main` local e informa o PR integrado e a validação ao Jefferson.

No CI atual (`../.github/workflows/ci.yml`), os três jobs exigidos são `test (ubuntu-latest)`, `test (windows-latest)` e `portable-windows`. Todos devem concluir com sucesso; pendente, ausente, falho ou cancelado não atende ao requisito. A exigência vale também sem proteção de branch configurada no GitHub.

O parecer aprovado deve permitir identificar o SHA, o autor das alterações, o revisor, o escopo revisado e as verificações. Não afirmar que um teste foi executado pelo revisor quando ele apenas conferiu um resultado de CI.

## Bloqueios

Sem acesso ao outro harness, com achados bloqueantes, checks falhando ou proteções exigindo uma identidade GitHub distinta, o PR permanece aberto e a sessão informa o bloqueio. Não substituir revisão cruzada por autorrevisão nem alterar proteções para permitir o merge. As aprovações de sandbox e controles externos continuam independentes desta autorização de trabalho.

## Continuidade

O ai-memory guarda a decisão por solicitação explícita do Jefferson e aponta para os arquivos canônicos. Pareceres e resultados de CI ficam no PR. Handoffs devem identificar PR, SHA, harness autor/revisor e pendências; uma aprovação lembrada sem evidência no PR e sem correspondência com o SHA atual não permite merge.

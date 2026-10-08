# Continuar no Codex CLI

Como trocar do Claude Code para o Codex CLI sem perder contexto nem qualidade, usando o ai-memory como ponte. Levantamento feito em 2026-10-08 com `codex-cli 0.160.1` e `ai-memory 2.6.0`.

## 1. Checklist

### Já está feito
| Item | Onde | Evidência |
|---|---|---|
| Servidor ai-memory local | `http://127.0.0.1:49374`, dados em `~/.local/share/ai-memory` | `ai-memory status` |
| Opt-in do repo (captura em modo allowlist) | `.ai-memory.toml` (`workspace` = `project` = `adms-log-analyzer`) | `ai-memory doctor`: scope exato |
| Workstream gerenciado (`ai-memory run`) | workstream `default`, harness `claude-code` | `ai-memory workstreams` |
| MCP `ai-memory` no Codex | `~/.codex/config.toml` → `[mcp_servers.ai-memory] url = "http://127.0.0.1:49374/mcp"` | `codex mcp list`: enabled |
| Hooks do ai-memory no Codex (7 eventos: session-start/end, user-prompt-submit, pre/post-tool-use, pre-compact, stop) | `~/.codex/hooks.json`, com hash confiável em `config.toml` | arquivo lido |
| Bloco gerenciado do ai-memory para o Codex | `AGENTS.md` (entre os marcadores `ai-memory:start/end`) | `ai-memory install-instructions --target AGENTS.md --compact --skills-scope project --skills-agent agents` |
| Skills do ai-memory para o Codex | `.agents/skills/ai-memory-*` (6 skills; o Codex lê `.agents/skills` do repo) | mesmo comando |
| Skills do ai-memory para o Claude | `.claude/skills/ai-memory-*` | já existiam |
| Subagentes do projeto (equivalentes a Opus/Sonnet/Haiku) | `.codex/agents/{arquiteto,implementador,varredura}.toml` | criados nesta tarefa |

### Falta fazer (comandos para o Jefferson)
1. **Atualizar os hooks do Codex**: o `~/.codex/hooks.json` ainda chama o binário `ai-memory/2.5.2/`, enquanto o Claude já usa o `2.6.0`.
   ```bash
   cd ~/Projects/adms-log-analyzer
   ai-memory install-hooks --agent codex --apply
   ```
   O comando muda o texto dos hooks, então o Codex pede para confiar neles de novo na próxima abertura: aceite. Repita após todo `mise upgrade` do ai-memory (o caminho do binário fica fixo na versão).
2. **Preferências globais no Codex**: o Codex não lê `~/.claude/CLAUDE.md` (idioma pt-BR, tom, ambiente). Crie o equivalente global:
   ```bash
   cp ~/.claude/CLAUDE.md ~/.codex/AGENTS.md
   ```
3. **Modelo padrão**: o `~/.codex/config.toml` está com `model = "gpt-5.6-sol"` (geração anterior). Troque as duas primeiras linhas e acrescente a seção `[agents]`:
   ```toml
   model = "gpt-6.1-sol"
   model_reasoning_effort = "medium"

   [agents]
   default_subagent_model = "gpt-6.1-sol"
   default_subagent_reasoning_effort = "medium"
   ```
   O `[agents]` evita que um subagente sem modelo explícito herde o modelo caro da sessão.
4. **Perfis** (opcional; um arquivo por perfil, chaves no nível raiz, carregado por cima do `config.toml` com `-p <nome>`):
   ```toml
   # ~/.codex/arquiteto.config.toml
   model = "gpt-6-astra"
   model_reasoning_effort = "high"
   ```
   ```toml
   # ~/.codex/rotina.config.toml
   model = "gpt-6.1-sol"
   model_reasoning_effort = "medium"
   ```
   ```toml
   # ~/.codex/varredura.config.toml
   model = "gpt-6-luna"
   model_reasoning_effort = "low"
   ```

### Verificação
```bash
cd ~/Projects/adms-log-analyzer
codex mcp list                 # ai-memory: enabled
grep -c 'ai-memory/2.6.0' ~/.codex/hooks.json   # 7 após o passo 1
ai-memory handoffs             # handoff aberto antes da troca
ai-memory run codex            # abre o Codex no mesmo workstream
ai-memory doctor               # depois da 1ª sessão: linha "codex ... captured"
```
Dentro do Codex: `/mcp` lista as ferramentas `memory_*`; peça "liste as skills disponíveis" e confira as seis `ai-memory-*`; `/agent` mostra os subagentes ativos.

## 2. Equivalência de modelos

Modelos visíveis para esta conta (`~/.codex/models_cache.json`, obtido em 2026-10-08 pelo `codex 0.160.1`) e guia oficial ([learn.chatgpt.com/docs/models](https://learn.chatgpt.com/docs/models), consultado em 2026-10-08):

| Modelo | Descrição oficial | Efforts |
|---|---|---|
| `gpt-6-astra` | O mais capaz; raciocínio avançado e julgamento mais forte | low → max, ultra |
| `gpt-6.1-sol` | Desempenho próximo do Astra a custo menor; trabalho longo e repetido | low → max, ultra |
| `gpt-6-luna` | O mais eficiente; tarefas focadas e de alto volume (sumarização, extração, código focado) | low → max (sem ultra) |
| `gpt-6-sol`, `gpt-5.6-*` | Gerações anteriores | — |
| `gpt-5.5` | Oculto; **aposenta em 2026-10-14** | — |

Todos com janela de 272k tokens. `ultra` = raciocínio máximo com delegação automática a subagentes (consome muito; não usar como padrão).

| Papel (CLAUDE.md) | Codex | Effort | Por quê |
|---|---|---|---|
| **Opus**: arquitetura, modelo de dados/eventos, parser/templates, descoberta de formato, algoritmos difíceis | `gpt-6-astra` | `high` | Única opção "fronteira"; julgamento é o que importa aqui |
| **Opus**: revisão final de código antes do merge, algoritmo realmente difícil | `gpt-6-astra` | `xhigh` | Mais profundidade onde o erro custa caro; `max` só se `xhigh` falhar |
| **Sonnet**: implementação, testes, empacotamento/CI, docs, git, coordenação | `gpt-6.1-sol` | `medium` | Custo menor com qualidade próxima do Astra; `high` para módulo com várias decisões locais |
| **Haiku**: contagens, inventário, grep em lote, anonimização, links, tabelas | `gpt-6-luna` | `low` | Mais barato e rápido; `medium` se a varredura exigir interpretar resultado |

Quando o equivalente não existir ou estiver indisponível (limite de uso, rollout):
- Sem Astra → `gpt-6.1-sol` com `xhigh` (ou `max` para revisão).
- Sem 6.1 Sol → `gpt-6-sol` `medium`.
- Sem Luna → `gpt-6.1-sol` `low`.
- Regra do CLAUDE.md vale igual: comece pelo mais barato que dê conta e suba se falhar por falta de raciocínio, não de contexto.

Como invocar:
```bash
codex -m gpt-6-astra -c model_reasoning_effort=high          # sessão interativa "Opus"
codex -p arquiteto                                            # o mesmo, via perfil
codex exec -m gpt-6-luna -c model_reasoning_effort=low -s read-only "conte ..."
codex review --base main -c model=gpt-6-astra -c model_reasoning_effort=xhigh   # equivalente ao /code-review
```
Dentro da sessão interativa, `/model` troca modelo e effort sem sair.

## 3. Paralelizar e delegar

O Codex tem subagentes nativos (feature `multi_agent`, estável e ligada). Diferença para o Claude Code: o Codex só cria subagentes quando o pedido os menciona.

- **Subagentes do projeto** em `.codex/agents/` (prioridade máxima na escolha de modelo):
  - `varredura`: `gpt-6-luna`/`low`, `read-only`.
  - `implementador`: `gpt-6.1-sol`/`medium`, `workspace-write`.
  - `arquiteto`: `gpt-6-astra`/`high`, `workspace-write`.
  Todos respondem ao pai em pt-BR, escrevem o conteúdo do app em inglês, têm escopo fechado e **não fazem commit**. Uso: "Use o subagente `varredura` para contar ... em `local-samples/`; e o `implementador` para alterar só `src/dmslog/x.py` e `tests/test_x.py`". `/agent` alterna entre as threads.
- **Sessões separadas** (equivalente mais isolado): `codex exec` não interativo, um por tarefa, com saída em arquivo:
  ```bash
  codex exec -p varredura -s read-only -o /tmp/varredura.md "..."
  codex exec -p rotina -s workspace-write -o /tmp/impl.md "Altere apenas src/dmslog/x.py ... Não faça commit."
  ```
  Para trabalho paralelo que mexe em arquivos, use `--worktree` (cada sessão em uma worktree gerenciada) e integre na sessão principal.
- **Regra de commit e merge**: subagentes de implementação/revisão não fazem commit. A sessão principal está autorizada a criar branch, commitar, publicar PR e fazer merge sem aprovação manual do Jefferson, dentro do escopo solicitado. A revisão final é cruzada: Claude revisa Codex e Codex revisa Claude, com aprovação do SHA final e CI obrigatório verde em Linux e Windows. Revisão local pelo mesmo harness é complementar. Protocolo: `REVISAO-CRUZADA.md`; repita "não faça commit" no prompt de delegação.

## 4. Handoff Claude → Codex

### Antes de a sessão do Claude acabar
Peça ao Claude: "salve o handoff para o Codex". Ele usa a skill `ai-memory-handoff` → `memory_handoff_begin` com:
- `workspace` e `project`: `adms-log-analyzer` (sempre explícitos);
- `shared`: não precisa (servidor de um usuário só);
- `summary` (2–3 frases): marco atual do `docs/ROADMAP.md` e estado do código;
- `next_steps`: próximo item do ROADMAP com o papel/modelo indicado;
- `open_questions`: decisões pendentes;
- `files_touched`: arquivos alterados e não commitados (`git status --short`);
- incluir no texto: branch atual, se há mudanças não commitadas, decisões tomadas na sessão que ainda não estão em `docs/ARQUITETURA.md`.

Decisões que valem além da próxima sessão vão para `docs/ARQUITETURA.md`/`ROADMAP.md` (ou página durável do ai-memory, se o Jefferson pedir), não só para o handoff: o handoff é consumido uma única vez.

Se o crédito acabar antes do handoff: o hook de `SessionEnd` gera um handoff automático quando o Claude fecha normalmente. Se a sessão morreu sem fechar, rode `ai-memory finalize-session --agent claude-code` no repo.

### Ao abrir o Codex
```bash
cd ~/Projects/adms-log-analyzer
ai-memory handoffs        # conferir se há handoff aberto
ai-memory run codex       # mesmo workstream: injeta o histórico do Claude + handoff
```
`ai-memory run codex` é o caminho preferido (o workstream `default` já foi aberto com `ai-memory run claude`). Rodar `codex` direto também funciona: o hook de SessionStart consome o handoff.

Primeiro prompt sugerido:
> Leia AGENTS.md, CLAUDE.md, docs/ARQUITETURA.md e docs/ROADMAP.md. Se o handoff não aparecer no contexto, use a skill ai-memory-handoff (memory_handoff_list e memory_handoff_accept com workspace e project = adms-log-analyzer). Resuma onde paramos, branch e pendências antes de mexer em código.

Para voltar ao Claude: o mesmo procedimento ao contrário (`ai-memory run claude`).

## 5. Diferenças que podem custar qualidade

| Diferença | Mitigação |
|---|---|
| O Codex lê só `AGENTS.md` (um por diretório, até 32 KiB somados); não lê `CLAUDE.md` | `AGENTS.md` resume o essencial e manda ler o `CLAUDE.md`. Ao mudar regra no `CLAUDE.md`, atualizar o resumo do `AGENTS.md` |
| Preferências globais (pt-BR, tom) estão em `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` (passo 2) e seção "Idioma" do `AGENTS.md` |
| Idioma misto: o app é **todo em inglês** (identificadores, comentários, docstrings, strings de CLI/API/UI, testes, docs do app), mas a conversa é em pt-BR. Um modelo que conversa em pt-BR tende a vazar português para o código, e as preferências globais copiadas no passo 2 dizem "sempre pt-BR" | Regra explícita na seção "Idioma" do `AGENTS.md` e nas instruções dos três subagentes; repetir "code and app content in English" no prompt de delegação; na revisão, procurar texto em português em `src/` e `tests/` (tarefa para o `varredura`) |
| MCP do Codex é cliente estático (URL HTTP direta, sem repasse de session id) | Passar `workspace`/`project` em toda chamada de projeto; está no `AGENTS.md` |
| Subagentes só sob pedido; sem ferramenta `Agent` com `model` por chamada | Subagentes nomeados em `.codex/agents/` ou `codex exec -p` |
| Edição por `apply_patch` em vez de edição por trecho | Pedir diffs pequenos e rodar `pytest` após cada mudança |
| Contexto: 272k e compactação automática | Delegar varreduras para não poluir a sessão principal; registrar decisões em docs antes de tarefas longas |
| Fins de linha e Windows | `.gitattributes` já controla o repo; o `apply_patch` pode normalizar fins de linha. Conferir `git diff` em arquivos com CRLF |
| Revisão final antes do merge | Harness oposto no papel de arquiteto: Claude/Opus para alterações do Codex; Codex/`arquiteto` para alterações do Claude. Ver `REVISAO-CRUZADA.md` |
| Captura do texto do assistente desligada nos dois harnesses | Handoff explícito antes da troca (seção 4) |

## 6. Limitações e perguntas em aberto
- Custos: a documentação diz só que o 6.1 Sol custa menos que o Astra e que Luna é o mais eficiente; sem números por modelo para a assinatura. Acompanhar o uso real nas primeiras sessões.
- `gpt-6.1-sol` está em rollout; se sumir de `/model`, usar `gpt-6-sol`.
- Os modelos mudam rápido: revalidar a tabela com `/model` ou `~/.codex/models_cache.json` quando o Codex atualizar.
- O Codex não restringe arquivos por subagente: o escopo fechado depende da instrução no prompt.
- Não testado ainda: uma troca real Claude → Codex com `ai-memory run codex` neste repo (fazer no passo de verificação).
- Atualizações de versão do ai-memory via mise exigem reinstalar os hooks dos dois harnesses (`install-hooks --agent codex|claude-code --apply`) e rodar `ai-memory install-instructions` (CLAUDE.md) e `ai-memory install-instructions --target AGENTS.md --compact --skills-agent agents` (AGENTS.md).

Fontes: `codex --help`, `codex exec --help`, `codex features list`, `ai-memory --help` e subcomandos, `ai-memory/2.6.0/docs/install.md`, [Modelos do Codex](https://learn.chatgpt.com/docs/models), [Subagentes](https://learn.chatgpt.com/docs/agent-configuration/subagents.md), [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [Skills](https://developers.openai.com/codex/skills), [managed-workstreams do ai-memory](https://github.com/akitaonrails/ai-memory/blob/master/docs/managed-workstreams.md); todos consultados em 2026-10-08.

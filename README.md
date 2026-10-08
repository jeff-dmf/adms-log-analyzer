# adms-log-analyzer

Analisador de logs dos adapters (online interfaces) do ADMS (ADMS GridOps 3.9).

## Estado

Estrutura inicial. O formato das linhas e das mensagens será definido a partir dos logs em `local-samples/`.

## Layout

- `src/dmslog/`: pacote Python (CLI `dmslog`).
- `tests/`: testes (pytest).
- `local-samples/`: logs reais por adapter e nível de log (ver `local-samples/README.md`).
- `docs/ADAPTERS.md`: o que a documentação diz sobre log dos adapters e onde achar cada coisa no vault.

## Desenvolvimento

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

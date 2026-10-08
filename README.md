# adms-log-analyzer

Analisador de logs dos adapters (online interfaces) do ADMS (ADMS GridOps 3.9).

## Estado

Estrutura inicial. O formato das linhas e das mensagens será definido a partir dos logs em `local-samples/`.

## Layout

- `src/dmslog/`: pacote Python (CLI `dmslog`).
- `tests/`: testes (pytest).
- `local-samples/`: ERS zipados com logs reais por adapter e nível de log (ver `local-samples/README.md`).
- `docs/ADAPTERS.md`: o que a documentação diz sobre log dos adapters e onde achar cada coisa no vault.
- `docs/ERS.md`: o que é o ERS (Error Reporting System) e como os logs vêm dentro dele.

## Desenvolvimento

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

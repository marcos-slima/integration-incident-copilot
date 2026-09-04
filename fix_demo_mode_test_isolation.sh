#!/usr/bin/env bash
# ============================================================
# Corrige vulnerabilidade de isolamento de teste: os testes
# *_demo_mode_* nao forcavam explicitamente o campo de gating do
# conector para vazio - dependiam implicitamente do .env real nao
# ter aquela credencial configurada. Isso quebrou silenciosamente
# assim que SALESFORCE_INSTANCE_URL foi configurado de verdade.
#
# Settings(campo=valor) NAO resolve sozinho porque campos nao
# passados ainda sao lidos do .env real (comportamento normal do
# pydantic-settings) - a correcao certa e monkeypatch direto no
# atributo especifico, forcando vazio, independente do .env.
#
# Uso: rodar dentro de ~/integration-incident-copilot
#   bash fix_demo_mode_test_isolation.sh
# ============================================================
set -e

if [ ! -f pyproject.toml ]; then
  echo "ERRO: rode este script dentro de ~/integration-incident-copilot"
  exit 1
fi

echo "=== 1/2 - Corrigindo tests/test_connectors.py (5 testes) ==="
python3 - << 'PYEOF'
from pathlib import Path

path = Path("tests/test_connectors.py")
lines = path.read_text(encoding="utf-8").split("\n")

FIXES = [
    ("test_servicenow_connector_demo_mode_known_scenario", "servicenow_connector", "servicenow_instance_url"),
    ("test_servicenow_connector_demo_mode_unknown_identifier_returns_fallback", "servicenow_connector", "servicenow_instance_url"),
    ("test_salesforce_connector_demo_mode_known_scenario", "salesforce_connector", "salesforce_instance_url"),
    ("test_workday_connector_demo_mode_known_scenario", "workday_connector", "workday_tenant"),
    ("test_ariba_connector_demo_mode_known_scenario", "ariba_connector", "ariba_base_url"),
]

changed = 0
out = []
i = 0
while i < len(lines):
    line = lines[i]
    out.append(line)
    for func_name, module, field in FIXES:
        old_sig = f"def {func_name}():"
        if line.strip() == old_sig:
            out[-1] = line.replace("():", "(monkeypatch):")
            indent = line[: len(line) - len(line.lstrip())]
            setattr_line = (
                f'{indent}    monkeypatch.setattr("app.connectors.{module}.settings.{field}", "")'
            )
            out.append(setattr_line)
            changed += 1
    i += 1

if changed != len(FIXES):
    print(f"AVISO: esperava corrigir {len(FIXES)} testes, corrigiu {changed} - verifique manualmente.")
else:
    Path("tests/test_connectors.py").write_text("\n".join(out), encoding="utf-8")
    print(f"{changed} teste(s) corrigido(s) em tests/test_connectors.py.")
PYEOF

echo "=== 2/2 - Corrigindo tests/test_cap_connector.py ==="
python3 - << 'PYEOF'
from pathlib import Path

path = Path("tests/test_cap_connector.py")
text = path.read_text(encoding="utf-8")

old = "def test_cap_connector_mock_scenario_when_not_configured():\n    connector = CAPConnector()"
new = (
    "def test_cap_connector_mock_scenario_when_not_configured(monkeypatch):\n"
    '    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_service_url", "")\n'
    "    connector = CAPConnector()"
)

old2 = "def test_cap_connector_unknown_identifier_returns_safe_fallback():\n    connector = CAPConnector()"
new2 = (
    "def test_cap_connector_unknown_identifier_returns_safe_fallback(monkeypatch):\n"
    '    monkeypatch.setattr("app.connectors.cap_connector.settings.cap_service_url", "")\n'
    "    connector = CAPConnector()"
)

changed = 0
if old in text:
    text = text.replace(old, new, 1)
    changed += 1
if old2 in text:
    text = text.replace(old2, new2, 1)
    changed += 1

if changed != 2:
    print(f"AVISO: esperava corrigir 2 testes, corrigiu {changed} - verifique manualmente.")
else:
    Path("tests/test_cap_connector.py").write_text(text, encoding="utf-8")
    print("2 teste(s) corrigido(s) em tests/test_cap_connector.py.")
PYEOF

echo "=== Lint ==="
uv run ruff check --fix tests/test_connectors.py tests/test_cap_connector.py
uv run ruff format tests/test_connectors.py tests/test_cap_connector.py

echo
echo "============================================================"
echo "IMPORTANTE: verifique se test_odata_connector_known_scenario"
echo "tem a mesma vulnerabilidade - o grep anterior so pegou nomes"
echo "com 'demo_mode' no nome, e o teste do OData nao segue esse"
echo "padrao de nomenclatura. Rode:"
echo
echo "  grep -n 'def test_odata_connector_known_scenario' -A 8 tests/test_connectors.py"
echo
echo "Se ele nao isolar odata_service_url explicitamente, tem o"
echo "mesmo problema (so nao estourou ainda porque voce nao"
echo "configurou ODATA_SERVICE_URL real ainda)."
echo
echo "Suite completa:"
echo "  uv run pytest -v"
echo "============================================================"

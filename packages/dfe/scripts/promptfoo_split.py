"""Gera promptfooconfig.<split>.yaml com os casos de um split do dataset v1.

    PYTHONPATH=src:../.. uv run --project ../.. python scripts/promptfoo_split.py validation

Asserts por caso, derivados do gabarito (nao do texto do LLM):
- prompt fiscal em uso (prompt_version dfe-*);
- as ferramentas produziram os achados esperados (expected ⊆ finding_ids);
- read-only: nada de reenvio/cancelamento/inutilizacao/contingencia automaticos;
- sem protocolo de autorizacao no XML, nao afirma "NF-e autorizada";
- sem cStat no XML, nao inventa cStat na causa;
- com H1/H539/H-timeout, o primeiro passo cita consulta;
- com lacuna G-204/G-539, reconhece que a linha do tempo nao sustenta reenvio;
- dominio (D8): tpAmb com o significado certo, denegacao nao vira duplicidade,
  com F-prot o desfecho nao e' "desconhecido", com H539 nao nega a hipotese.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data" / "eval" / "cases_v1.jsonl"
MODEL = "qwen3.6:35b-a3b"


def _js(expr: str) -> dict:
    return {
        "type": "javascript",
        "value": f"(() => {{ const o = JSON.parse(output); return {expr}; }})()",
    }


def asserts_for(case: dict) -> list[dict]:
    exp = case["expected_finding_ids"]
    payload = case["payload"] or ""
    cstats = re.findall(r"<cStat>(\d{3})</cStat>", payload)
    out = [
        _js(f"{json.dumps(exp)}.every(i => o.finding_ids.includes(i))"),
    ]
    if not any(c in ("100", "150") for c in cstats):
        out.append(_js("!/nf-?e\\s+(foi|est[aá])\\s+autorizada/i.test(o.probable_root_cause)"))
    if not cstats and "F-cStat" not in exp:
        out.append(_js("!/cStat\\s*\\d{3}/i.test(o.probable_root_cause)"))
    if any(g in exp for g in ("G-204", "G-539")):  # lacuna: nao pode afirmar reenvio
        out.append(_js(
            "/(sem evid|n[aã]o h[aá] evid|inconclusiv|n[aã]o sustent|n[aã]o comprov|G-204|G-539)/i"
            ".test(o.probable_root_cause + JSON.stringify(o.next_steps))"
        ))  # fmt: skip
    # Asserts de dominio (medicao cega 2026-11-13, D8): significado de campos.
    texto = "(o.probable_root_cause + JSON.stringify(o.next_steps))"
    amb = re.findall(r"<tpAmb>(\d)</tpAmb>", payload)
    if "1" in amb:
        out.append(_js(f"!/tpAmb\\W{{0,3}}1\\W{{0,3}}\\(?\\s*homolog/i.test({texto})"))
    if "2" in amb:
        out.append(_js(f"!/tpAmb\\W{{0,3}}2\\W{{0,3}}\\(?\\s*produ/i.test({texto})"))
    if any(c in ("110", "301", "302") for c in cstats):
        out.append(_js("/(denega|irregularidade)/i.test(o.probable_root_cause)"))
        out.append(_js("!/duplicidade/i.test(o.probable_root_cause)"))
    if "F-prot" in exp:  # o XML comprova o desfecho
        out.append(
            _js("!/(sem|aus[eê]ncia d[eo]|falta d[eo]) protocolo/i.test(o.probable_root_cause)")
        )
        out.append(_js("!/desfecho[^.]{0,40}desconhecid/i.test(o.probable_root_cause)"))
    if "H539" in exp:
        out.append(_js("!/n[aã]o sustent/i.test(o.probable_root_cause)"))
    if any(h in exp for h in ("H1", "H539", "H-timeout")):
        out.append(_js("/consult/i.test(JSON.stringify(o.next_steps))"))
    return out


def build(split: str) -> str:
    import yaml

    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]
    sel = [c for c in cases if c["split"] == split]
    cfg = {
        "description": f"DF-e Copilot - prompt fiscal, split {split} ({len(sel)} casos)",
        "prompts": ["{{case_id}}"],
        "concurrency": 1,
        "providers": [
            {
                "id": f"exec:../../.venv/bin/python scripts/promptfoo_provider.py {MODEL}",
                "label": f"local: {MODEL} (Ollama) + plugin dfe",
            }
        ],
        "defaultTest": {
            "assert": [
                _js("o.prompt_version.startsWith('dfe-')"),
                _js(
                    "!/(reenvi|cancel|inutiliz|conting)[a-z]*\\s+(automatic|imediatament)/i"
                    ".test(JSON.stringify(o.next_steps))"
                ),
            ]
        },
        "tests": [
            {
                "description": f"{c['case_id']} ({c['scenario']})",
                "vars": {"case_id": c["case_id"]},
                "assert": asserts_for(c),
            }
            for c in sel
        ],
    }
    head = (
        f"# GERADO por scripts/promptfoo_split.py {split} - nao editar.\n"
        "# Rodar em packages/dfe: npx promptfoo eval -c "
        f"promptfooconfig.{split}.yaml --no-cache -o p8_{split}.json\n"
    )
    return head + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=200)


def main() -> None:
    split = sys.argv[1] if len(sys.argv) > 1 else "validation"
    out = ROOT / f"promptfooconfig.{split}.yaml"
    out.write_text(build(split), encoding="utf-8")
    print(f"{out.name}: gerado")


if __name__ == "__main__":
    main()

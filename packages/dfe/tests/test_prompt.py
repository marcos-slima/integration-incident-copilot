"""P8: prompt fiscal - digest proprio e baseline medido.

O comportamento do hook de prompt no core (persona e bloco no prompt,
sanitizacao) e' testado no core (tests/test_da95_sdk.py); aqui so o que e'
do produto, pela fachada `app.sdk` (DA-95).
"""

import json
from pathlib import Path

from dfe_copilot.plugin import PLUGIN
from dfe_copilot.prompt import DFE_PROMPT, FISCAL_PERSONA

from app import sdk
from app.sdk.testing import domain_plugins_configured

BASELINE = Path(__file__).resolve().parents[1] / "data" / "eval" / "prompt_baseline.json"


def test_plugin_expoe_prompt():
    assert isinstance(PLUGIN.prompt, sdk.DomainPrompt)


def test_digest_dfe_bate_com_baseline():
    base = json.loads(BASELINE.read_text())
    prov = sdk.domain_prompt_provenance(DFE_PROMPT)
    assert prov == {k: base[k] for k in ("prompt_version", "prompt_digest")}, (
        "prompt fiscal mudou sem nova medicao: rode o promptfoo do pacote e "
        "scripts/prompt_baseline.py --write"
    )
    assert base["base_prompt_digest"] == sdk.base_prompt_digest(), (
        "prompt do core mudou: o prompt fiscal precisa ser medido de novo"
    )


def test_digest_muda_com_persona():
    outro = type(DFE_PROMPT)(persona=FISCAL_PERSONA + " ")
    assert (
        sdk.domain_prompt_digest(outro, sdk.base_prompt_digest())
        != sdk.domain_prompt_provenance(DFE_PROMPT)["prompt_digest"]
    )


def test_catalogo_do_core_publica_prompt_e_schema_fiscal():
    with domain_plugins_configured("dfe_copilot.plugin"):
        (cat,) = sdk.plugin_catalog()
    assert cat["name"] == "dfe"
    assert cat["prompt_version"] == f"dfe-{DFE_PROMPT.version}"
    props = cat["context_schema"]["properties"]
    assert {"environment", "jurisdiction", "events"} <= set(props)
    assert props["events"]["items"]["properties"]["event"]["enum"] == [
        "sent",
        "timeout",
        "response",
        "error",
    ]

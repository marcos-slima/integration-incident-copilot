"""Validacao 2026-10-07, Bloco 3: avaliacao e corpus (M-19, M-21, M-23)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from app.evaluation.gates import check_corpus_coverage, check_index_manifest

RAIZ = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# M-19: keywords do dataset existem no documento esperado; mais casos OOS
# ---------------------------------------------------------------------------


def _copia_repo(tmp_path: Path) -> Path:
    for rel in ("data/sample_docs", "data/eval"):
        shutil.copytree(RAIZ / rel, tmp_path / rel)
    return tmp_path


def test_m19_keyword_ausente_reprova_o_gate(tmp_path):
    raiz = _copia_repo(tmp_path)
    dataset = raiz / "data/eval/rag_eval_dataset.json"
    casos = json.loads(dataset.read_text(encoding="utf-8"))
    casos[0]["expected_keywords"] = ["termo-que-nao-esta-no-doc"]
    dataset.write_text(json.dumps(casos), encoding="utf-8")
    [finding] = check_corpus_coverage(raiz)
    assert finding.severity == "fail" and "termo-que-nao-esta-no-doc" in finding.message


def test_m19_dataset_atual_passa_e_tem_oos_suficiente():
    [finding] = check_corpus_coverage(RAIZ)
    assert finding.severity == "pass"
    casos = json.loads((RAIZ / "data/eval/rag_eval_dataset.json").read_text(encoding="utf-8"))
    assert sum(c["difficulty"] == "out_of_scope" for c in casos) >= 10


# ---------------------------------------------------------------------------
# M-21: cada caso do promptfoo declara quem responde, e a declaracao bate
# ---------------------------------------------------------------------------


def _quem_responde(vars_: dict) -> str:
    from app.agent.rules import match_known_error
    from app.connectors import get_connector

    texto = vars_["description"]
    com_conector = False
    tipo, ident = vars_.get("interface_type"), vars_.get("identifier")
    if tipo and tipo != "none" and ident and ident != "none":
        dado = get_connector(tipo).fetch(ident)
        if dado.message:
            texto += f" {dado.message}"
        com_conector = not dado.is_mock and not dado.is_fallback
    return "rule_engine" if match_known_error(texto, has_connector_data=com_conector) else "llm"


@pytest.mark.parametrize("arquivo", ["promptfooconfig.yaml", "promptfooconfig.compare.yaml"])
def test_m21_metadata_path_bate_com_o_rule_engine(arquivo):
    casos = yaml.safe_load((RAIZ / arquivo).read_text(encoding="utf-8"))["tests"]
    descricoes = [c["description"] for c in casos]
    assert len(descricoes) == len(set(descricoes)), "caso repetido"
    for caso in casos:
        assert caso["metadata"]["path"] == _quem_responde(caso["vars"]), caso["description"]


# ---------------------------------------------------------------------------
# M-23: manifesto do indice
# ---------------------------------------------------------------------------


class _Cliente:
    def count(self, collection_name, exact):
        return type("C", (), {"count": 1234})()

    def retrieve(self, collection_name, ids, with_payload):
        payload = {"provider": "ollama", "embedding_model": "nomic-embed-text", "dense_size": 768}
        return [type("P", (), {"payload": payload})()]

    def get_collection(self, name):
        vetores = type("V", (), {"size": 768})()
        params = type("Pa", (), {"vectors": {"dense": vetores}})()
        return type("I", (), {"config": type("Cf", (), {"params": params})()})()


def test_m23_ingest_registra_o_indice(tmp_path, monkeypatch):
    from app.rag import index_manifest

    monkeypatch.setattr(
        "app.rag.embedding_guard.describe_dense_size", lambda client, name: 768, raising=False
    )
    destino = tmp_path / "index_manifest.json"
    entrada = index_manifest.update_manifest(
        _Cliente(),
        "sap_reference_library",
        target="reference",
        source_dir="/x",
        files_processed=7,
        path=destino,
    )
    assert entrada["points"] == 1234
    assert entrada["embedding"]["embedding_model"] == "nomic-embed-text"
    salvo = json.loads(destino.read_text(encoding="utf-8"))
    assert salvo["collections"]["sap_reference_library"]["files_processed"] == 7


def test_m23_gate_avisa_sem_manifesto_e_passa_com_referencia(tmp_path):
    [sem] = check_index_manifest(tmp_path)
    assert sem.severity == "warn"
    (tmp_path / "data").mkdir()
    (tmp_path / "data/index_manifest.json").write_text(
        json.dumps({"collections": {"sap_reference_library": {"points": 1}}}), encoding="utf-8"
    )
    [com] = check_index_manifest(tmp_path)
    assert com.severity == "pass"


# ---------------------------------------------------------------------------
# M-20: comparacao pareada do reranker com incerteza
# ---------------------------------------------------------------------------


def test_m20_uma_consulta_de_diferenca_nao_e_significativa():
    from app.rag.eval_metrics import paired_hit_comparison

    base = [1] * 16 + [0, 0]
    cand = [1] * 17 + [0]
    stats = paired_hit_comparison(cand, base)
    assert (stats["only_candidate"], stats["only_baseline"]) == (1, 0)
    assert stats["mcnemar_exact_p"] == 1.0
    assert stats["bootstrap_ci95_low"] <= 0.0 <= stats["bootstrap_ci95_high"]


def test_m20_diferenca_grande_e_significativa():
    from app.rag.eval_metrics import paired_hit_comparison

    stats = paired_hit_comparison([1] * 30, [0] * 30)
    assert stats["mcnemar_exact_p"] < 0.001 and stats["bootstrap_ci95_low"] > 0


def test_m20_benchmark_versionado_carrega_estatistica_e_gate_avisa():
    from app.evaluation.gates import check_reranker_invariant

    dados = json.loads((RAIZ / "data/eval/reranker_benchmark_results.json").read_text("utf-8"))
    vencedor = next(r for r in dados if r["key"] == "mmarco-mMiniLMv2")
    assert vencedor["vs_baseline"]["n"] == len(vencedor["per_query"])
    severidades = {f.severity for f in check_reranker_invariant(RAIZ)}
    assert severidades == {"warn"}

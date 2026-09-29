import json
from pathlib import Path

import pytest

from app.evaluation.gates import (
    Thresholds,
    check_candidate_das,
    check_corpus_coverage,
    check_difficulty_mix,
    check_promptfoo_configs,
    check_rag_dataset,
    check_reranker_invariant,
    compare_promptfoo,
    normalize_promptfoo_results,
    run_all,
)

REAL_ROOT = Path(__file__).resolve().parents[1]

CORPUS_DOC = "# SAP IDoc\n\nstatus 51 material centro BD87\n"


def _minimal_root(tmp_path: Path) -> Path:
    (tmp_path / "data/eval").mkdir(parents=True)
    (tmp_path / "data/sample_docs").mkdir(parents=True)
    (tmp_path / "app/rag").mkdir(parents=True)
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "data/sample_docs/doc.md").write_text(CORPUS_DOC, encoding="utf-8")
    (tmp_path / "data/eval/rag_eval_dataset.json").write_text(
        json.dumps(
            [
                {
                    "query": "IDoc 51",
                    "expected_sources": ["doc.md"],
                    "difficulty": "easy",
                    "interface_type": "rfc",
                },
                {
                    "query": "fora de escopo",
                    "expected_sources": [],
                    "difficulty": "out_of_scope",
                    "interface_type": None,
                },
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def _severities(findings) -> set[str]:
    return {f.severity for f in findings}


class TestRealRepository:
    def test_gate_verde_no_repositorio_real(self):
        findings = run_all(REAL_ROOT)
        falhas = [f"{f.check}: {f.message}" for f in findings if f.is_failure]
        assert not falhas, falhas

    def test_reranker_em_producao_e_o_vencedor_medido(self):
        findings = check_reranker_invariant(REAL_ROOT)
        assert not [f for f in findings if f.is_failure]

    def test_dataset_real_tem_os_quatro_niveis_de_dificuldade(self):
        assert not [f for f in check_difficulty_mix(REAL_ROOT) if f.is_failure]


class TestRagDataset:
    def test_dataset_minimal_valido(self, tmp_path):
        findings = check_rag_dataset(_minimal_root(tmp_path), Thresholds(min_rag_cases=2))
        assert not [f for f in findings if f.is_failure]

    def test_out_of_scope_com_expected_source_e_contraditorio(self, tmp_path):
        root = _minimal_root(tmp_path)
        path = root / "data/eval/rag_eval_dataset.json"
        cases = json.loads(path.read_text())
        cases[1]["expected_sources"] = ["doc.md"]
        path.write_text(json.dumps(cases), encoding="utf-8")
        findings = check_rag_dataset(root)
        assert "fail" in _severities(findings)
        assert any("contradiz" in f.message for f in findings)

    def test_caso_com_difficulty_desconhecida_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        path = root / "data/eval/rag_eval_dataset.json"
        cases = json.loads(path.read_text())
        cases[0]["difficulty"] = "impossivel"
        path.write_text(json.dumps(cases), encoding="utf-8")
        assert "fail" in _severities(check_rag_dataset(root))

    def test_interface_type_fora_do_literal_do_pipeline_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        path = root / "data/eval/rag_eval_dataset.json"
        cases = json.loads(path.read_text())
        cases[0]["interface_type"] = "sap_qualquer_coisa"
        path.write_text(json.dumps(cases), encoding="utf-8")
        findings = check_rag_dataset(root)
        assert any("Literal do pipeline" in f.message for f in findings)

    def test_piso_de_casos_impede_encolher_o_dataset(self, tmp_path):
        root = _minimal_root(tmp_path)
        findings = check_rag_dataset(root, Thresholds(min_rag_cases=15))
        assert any("piso" in f.message for f in findings if f.is_failure)

    def test_json_invalido_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        (root / "data/eval/rag_eval_dataset.json").write_text("{nao eh json", encoding="utf-8")
        assert "fail" in _severities(check_rag_dataset(root))


class TestCorpusCoverage:
    def test_documento_ausente_no_corpus_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        (root / "data/sample_docs/doc.md").unlink()
        findings = check_corpus_coverage(root)
        assert "fail" in _severities(findings)
        assert "doc.md" in findings[0].message

    def test_documento_em_subdiretorio_e_encontrado(self, tmp_path):
        root = _minimal_root(tmp_path)
        (root / "data/sample_docs/rfc").mkdir()
        (root / "data/sample_docs/rfc/doc.md").write_text(CORPUS_DOC, encoding="utf-8")
        assert not [f for f in check_corpus_coverage(root) if f.is_failure]


class TestDifficultyMix:
    def test_sem_caso_hard_falha(self, tmp_path):
        assert "fail" in _severities(check_difficulty_mix(_minimal_root(tmp_path)))

    def test_mix_completo_passa(self, tmp_path):
        root = _minimal_root(tmp_path)
        path = root / "data/eval/rag_eval_dataset.json"
        cases = json.loads(path.read_text())
        for difficulty in ("medium", "hard"):
            cases.append(
                {
                    "query": f"caso {difficulty}",
                    "expected_sources": ["doc.md"],
                    "difficulty": difficulty,
                    "interface_type": "odata",
                }
            )
        path.write_text(json.dumps(cases), encoding="utf-8")
        assert not [f for f in check_difficulty_mix(root) if f.is_failure]


class TestRerankerInvariant:
    def _benchmark(self, hf_id: str, hit: float, key: str = "vencedor") -> list[dict]:
        return [
            {"key": key, "hf_id": hf_id, "hit_at_1": hit},
            {
                "key": "ms-marco-L6",
                "hf_id": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "hit_at_1": hit - 0.2,
            },
        ]

    def test_codigo_divergente_do_vencedor_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        (root / "app/rag/retriever.py").write_text(
            'RERANKER_MODEL = "cross-encoder/outro-modelo"\n', encoding="utf-8"
        )
        (root / "data/eval/reranker_benchmark_results.json").write_text(
            json.dumps(self._benchmark("cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", 0.95)),
            encoding="utf-8",
        )
        findings = check_reranker_invariant(root)
        assert any("vencedor medido" in f.message for f in findings if f.is_failure)

    def test_hit_at_1_abaixo_do_piso_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        (root / "app/rag/retriever.py").write_text(
            'RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"\n', encoding="utf-8"
        )
        (root / "data/eval/reranker_benchmark_results.json").write_text(
            json.dumps(self._benchmark("cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", 0.80)),
            encoding="utf-8",
        )
        findings = check_reranker_invariant(root, Thresholds(min_hit_at_1=0.90))
        assert any("abaixo do piso" in f.message for f in findings if f.is_failure)

    def test_margem_sobre_o_baseline_insuficiente_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        results = [
            {
                "key": "mmarco",
                "hf_id": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
                "hit_at_1": 0.92,
            },
            {
                "key": "ms-marco-L6",
                "hf_id": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "hit_at_1": 0.91,
            },
        ]
        (root / "app/rag/retriever.py").write_text(
            'RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"\n', encoding="utf-8"
        )
        (root / "data/eval/reranker_benchmark_results.json").write_text(
            json.dumps(results), encoding="utf-8"
        )
        findings = check_reranker_invariant(root, Thresholds(min_benchmark_margin=0.05))
        assert any("margem" in f.message for f in findings if f.is_failure)

    def test_sem_baseline_no_benchmark_vira_aviso(self, tmp_path):
        root = _minimal_root(tmp_path)
        (root / "app/rag/retriever.py").write_text(
            'RERANKER_MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"\n', encoding="utf-8"
        )
        (root / "data/eval/reranker_benchmark_results.json").write_text(
            json.dumps(
                [
                    {
                        "key": "so-um",
                        "hf_id": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
                        "hit_at_1": 0.95,
                    }
                ]
            ),
            encoding="utf-8",
        )
        findings = check_reranker_invariant(root)
        assert "warn" in _severities(findings)
        assert not [f for f in findings if f.is_failure]

    def test_constante_ausente_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        (root / "app/rag/retriever.py").write_text(
            "RERANKER_MODEL_ANTIGO = 'x'\n", encoding="utf-8"
        )
        (root / "data/eval/reranker_benchmark_results.json").write_text(
            json.dumps(self._benchmark("x", 0.95)), encoding="utf-8"
        )
        assert "fail" in _severities(check_reranker_invariant(root))


class TestPromptfooConfigs:
    def test_config_valida_passa(self, tmp_path):
        (tmp_path / "promptfooconfig.yaml").write_text(
            "prompts:\n- file://p.txt\nproviders:\n- id: ollama/model\ntests:\n- description: c\n",
            encoding="utf-8",
        )
        assert not [f for f in check_promptfoo_configs(tmp_path) if f.is_failure]

    def test_sem_tests_falha(self, tmp_path):
        (tmp_path / "promptfooconfig.yaml").write_text(
            "prompts:\n- file://p.txt\nproviders:\n- id: ollama/model\ntests: []\n",
            encoding="utf-8",
        )
        assert "fail" in _severities(check_promptfoo_configs(tmp_path))

    def test_script_de_provider_inexistente_falha(self, tmp_path):
        (tmp_path / "promptfooconfig.yaml").write_text(
            "prompts:\n- file://p.txt\nproviders:\n- id: 'exec:python scripts/fantasma.py'\ntests:\n- description: c\n",
            encoding="utf-8",
        )
        findings = check_promptfoo_configs(tmp_path)
        assert any("fantasma.py" in f.message for f in findings)

    def test_yaml_invalido_falha(self, tmp_path):
        (tmp_path / "promptfooconfig.yaml").write_text("prompts: [\n", encoding="utf-8")
        assert "fail" in _severities(check_promptfoo_configs(tmp_path))

    def test_nenhuma_config_encontrada_falha(self, tmp_path):
        assert "fail" in _severities(check_promptfoo_configs(tmp_path))


class TestCandidateDas:
    def _write(self, tmp_path: Path, candidates: str, architecture: str) -> None:
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "CLAUDE.md").write_text(
            f"**DAs candidatas (sem implementacao ainda):**\n{candidates}\n\n---\n",
            encoding="utf-8",
        )
        (tmp_path / "docs/ARCHITECTURE.md").write_text(architecture, encoding="utf-8")

    def test_da_entregada_marcada_como_candidata_falha(self, tmp_path):
        self._write(
            tmp_path,
            "- DA-32: AMQP async consumer\n",
            "## Consumidor AMQP 1.0 assincrono via Solace Cloud (DA-32)\n",
        )
        findings = check_candidate_das(tmp_path)
        assert "fail" in _severities(findings)
        assert "DA-32" in findings[0].message

    def test_da_bloqueada_e_aceita(self, tmp_path):
        self._write(tmp_path, "- DA-31: SAP AI Agent Hub registration\n", "# Arquitetura\n")
        assert not [f for f in check_candidate_das(tmp_path) if f.is_failure]

    def test_entrega_mencionada_no_corpo_tambem_conta(self, tmp_path):
        self._write(
            tmp_path,
            "- DA-99: algo\n",
            "O consumo direto foi entregue em DA-99 (ver secao abaixo).\n",
        )
        assert "fail" in _severities(check_candidate_das(tmp_path))

    def test_sem_lista_no_claude_md_vira_aviso(self, tmp_path):
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "CLAUDE.md").write_text("sem lista\n", encoding="utf-8")
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# doc\n", encoding="utf-8")
        assert "warn" in _severities(check_candidate_das(tmp_path))


class TestPromptfooComparison:
    def test_normaliza_envelope_do_promptfoo(self):
        payload = {
            "results": {
                "results": [
                    {"pass": True, "testCase": {"description": "caso A", "vars": {"query": "q"}}},
                    {"pass": False, "testCase": {"description": "caso B"}},
                ]
            }
        }
        assert normalize_promptfoo_results(payload) == {"caso A": True, "caso B": False}

    def test_normaliza_lista_direta_e_nomeia_sem_descricao(self):
        payload = [{"pass": True}, {"pass": False}]
        assert normalize_promptfoo_results(payload) == {"case_0": True, "case_1": False}

    def test_formato_nao_reconhecido_falha(self):
        with pytest.raises(ValueError, match="nao reconhecido"):
            normalize_promptfoo_results({"foo": 1})

    def test_entrada_sem_pass_falha(self):
        with pytest.raises(ValueError, match="sem campo 'pass'"):
            normalize_promptfoo_results([{"testCase": {"description": "x"}}])

    def test_regressao_falha_o_gate(self):
        findings = compare_promptfoo({"A": False, "B": True}, {"A": True, "B": True})
        assert "fail" in _severities(findings)

    def test_caso_novo_que_passa_so_vira_aviso(self):
        findings = compare_promptfoo({"A": True, "C": True}, {"A": True})
        assert _severities(findings) == {"warn"}

    def test_sem_regressao_passa(self):
        findings = compare_promptfoo({"A": True, "B": True}, {"A": True, "B": True})
        assert "fail" not in _severities(findings)

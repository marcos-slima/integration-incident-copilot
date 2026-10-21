import json
import re
from pathlib import Path

import pytest
import yaml

from app.evaluation.gates import (
    GATES,
    Thresholds,
    check_candidate_das,
    check_connector_coverage,
    check_connector_reachable,
    check_connector_validation_matrix,
    check_corpus_coverage,
    check_da_registered,
    check_da_table_unique,
    check_difficulty_mix,
    check_docs_code_references,
    check_docs_markup_integrity,
    check_documented_das,
    check_index_current,
    check_llm_baseline,
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

    def test_reranker_em_producao_nao_e_significativamente_pior(self):
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

    MMARCO = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

    def _write(self, root, results, model=None):
        (root / "app/rag/retriever.py").write_text(
            f'RERANKER_MODEL = "{model or self.MMARCO}"\n', encoding="utf-8"
        )
        (root / "data/eval/reranker_benchmark_results.json").write_text(
            json.dumps(results), encoding="utf-8"
        )

    @staticmethod
    def _pq(hits: list[int]) -> list[dict]:
        return [{"hit_at_1": h} for h in hits]

    def test_modelo_de_producao_fora_do_benchmark_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        self._write(root, self._benchmark(self.MMARCO, 0.95), model="cross-encoder/outro-modelo")
        findings = check_reranker_invariant(root)
        assert any("nao foi medido" in f.message for f in findings if f.is_failure)

    def test_hit_at_1_abaixo_do_piso_falha(self, tmp_path):
        root = _minimal_root(tmp_path)
        self._write(root, self._benchmark(self.MMARCO, 0.80))
        findings = check_reranker_invariant(root, Thresholds(min_hit_at_1=0.90))
        assert any("abaixo do piso" in f.message for f in findings if f.is_failure)

    def test_candidato_significativamente_melhor_falha(self, tmp_path):
        """30 consultas: candidato acerta 12 que producao erra, nenhuma ao contrario."""
        root = _minimal_root(tmp_path)
        prod = [1] * 18 + [0] * 12
        cand = [1] * 30
        self._write(
            root,
            [
                {"key": "prod", "hf_id": self.MMARCO, "hit_at_1": 0.6, "per_query": self._pq(prod)},
                {
                    "key": "melhor",
                    "hf_id": "x/melhor",
                    "hit_at_1": 1.0,
                    "per_query": self._pq(cand),
                },
            ],
        )
        findings = check_reranker_invariant(root, Thresholds(min_hit_at_1=0.5))
        assert any("significativamente melhor" in f.message for f in findings if f.is_failure)

    def test_vantagem_pontual_nao_significativa_so_avisa(self, tmp_path):
        """Caso real de 2026-10-18: uma consulta de diferenca (McNemar p=1,0)."""
        root = _minimal_root(tmp_path)
        prod = [1] * 88 + [0] * 2
        cand = [1] * 89 + [0]
        self._write(
            root,
            [
                {
                    "key": "prod",
                    "hf_id": self.MMARCO,
                    "hit_at_1": 88 / 90,
                    "per_query": self._pq(prod),
                },
                {
                    "key": "bge",
                    "hf_id": "BAAI/bge",
                    "hit_at_1": 89 / 90,
                    "per_query": self._pq(cand),
                },
            ],
        )
        findings = check_reranker_invariant(root)
        assert not [f for f in findings if f.is_failure]
        assert any("NAO significativa" in f.message for f in findings)

    def test_sem_dados_por_consulta_usa_margem(self, tmp_path):
        root = _minimal_root(tmp_path)
        self._write(
            root,
            [
                {"key": "prod", "hf_id": self.MMARCO, "hit_at_1": 0.90},
                {"key": "outro", "hf_id": "x/outro", "hit_at_1": 0.96},
            ],
        )
        findings = check_reranker_invariant(root, Thresholds(min_benchmark_margin=0.05))
        assert any("supera producao" in f.message for f in findings if f.is_failure)

    def test_producao_e_o_melhor_passa(self, tmp_path):
        root = _minimal_root(tmp_path)
        self._write(root, self._benchmark(self.MMARCO, 0.95))
        assert not [f for f in check_reranker_invariant(root) if f.is_failure]

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

    def test_frase_em_celula_de_tabela_nao_e_o_titulo(self, tmp_path):
        # Regressao real: a linha da DA-51 na tabela do CLAUDE.md citava
        # "das DAs candidatas" numa celula, e a busca pela primeira
        # ocorrencia casava ali e devolvia lista vazia (gate virava aviso).
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "CLAUDE.md").write_text(
            "| DA-51 | valida a lista das DAs candidatas |\n"
            "\n"
            "**DAs candidatas (sem implementacao ainda):**\n"
            "- DA-31: algo\n"
            "\n"
            "---\n",
            encoding="utf-8",
        )
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# doc\n", encoding="utf-8")
        findings = check_candidate_das(tmp_path)
        assert "warn" not in _severities(findings)
        assert not [f for f in findings if f.is_failure]

    def test_titulo_com_hash_e_reconhecido(self, tmp_path):
        self._write(tmp_path, "- DA-31: algo\n", "# doc\n")
        text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text(
            text.replace("**DAs candidatas", "### DAs candidatas"), encoding="utf-8"
        )
        assert "warn" not in _severities(check_candidate_das(tmp_path))


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

    def test_normaliza_saida_real_do_promptfoo_success(self):
        """A saida real de `promptfoo eval -o resultados.json` usa `success`, nao `pass`.

        Achado ao gravar o baseline do qwen3.8: a receita documentada
        (`npx promptfoo eval -o resultados.json` -> `--write-promptfoo-baseline`)
        quebrava com "sem campo 'pass'", deixando `llm_baseline` impossivel de fechar.
        """
        payload = {
            "results": {
                "results": [
                    {"success": True, "testCase": {"description": "caso A"}},
                    {"success": False, "testCase": {"description": "caso B"}},
                ]
            }
        }
        assert normalize_promptfoo_results(payload) == {"caso A": True, "caso B": False}

    def test_usa_grading_result_como_fallback(self):
        payload = [{"gradingResult": {"pass": True}, "testCase": {"description": "caso C"}}]
        assert normalize_promptfoo_results(payload) == {"caso C": True}

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


def _decisoes(root):
    """Prosa e indice das DAs (docs/DECISOES_DE_ARQUITETURA.md desde 2026-10-07)."""
    (root / "docs").mkdir(exist_ok=True)
    return root / "docs" / "DECISOES_DE_ARQUITETURA.md"


class TestDocumentedDas:
    """A prosa de decisao existia; o rotulo (DA-N) nao.

    Quinze secoes do README eram invisiveis para qualquer `grep "DA-15"`, e
    a DA-30 nao tinha secao propria em lugar nenhum. `candidate_das_fresh`
    (DA-51) nao pegou nada disso porque checa so uma direcao: que uma DA
    marcada como candidata NAO esteja entregue. O inverso — DA entregue com
    a prosa nao localizavel — nunca foi verificado.
    """

    def test_repositorio_real_esta_coerente(self):
        assert not [f for f in check_documented_das() if f.is_failure]
        assert not [f for f in check_index_current() if f.is_failure]

    def test_da_registrada_sem_prosa_reprova(self, tmp_path):
        readme = _decisoes(tmp_path)
        readme.write_text("## Decisões de Arquitetura\n\n### 1. Coisa (DA-1)\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n| DA-2 | b |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")
        falhas = [f for f in check_documented_das(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "DA-2" in falhas[0].message

    def test_secao_orfa_no_readme_reprova(self, tmp_path):
        """Sentido inverso: `(DA-9)` no README sem linha no registro do
        CLAUDE.md e' prosa orfa — o proximo registrador nao vai acha-la."""
        _decisoes(tmp_path).write_text(
            "## Decisões de Arquitetura\n\n### 1. Coisa (DA-1/9)\n", encoding="utf-8"
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")
        falhas = [f for f in check_documented_das(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "DA-9" in falhas[0].message

    def test_architecture_e_local_alternativo_aceito(self, tmp_path):
        _decisoes(tmp_path).write_text(
            "## Decisões de Arquitetura\n\n### 1. Coisa (DA-1)\n", encoding="utf-8"
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n| DA-32 | b |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text(
            "# arq\n\n## AMQP (DA-32)\n", encoding="utf-8"
        )
        assert not [f for f in check_documented_das(tmp_path) if f.is_failure]

    def test_indice_dessincronizado_reprova(self, tmp_path):
        _decisoes(tmp_path).write_text(
            "## Decisões de Arquitetura\n\n"
            "| DA | Seção | O que é |\n|---|---|---|\n"
            "| 1 | [1](#decisoes-de-arquitetura) | a |\n\n"
            "### 1. Coisa (DA-1)\n### 2. Outra (DA-2)\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n| DA-2 | b |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")
        falhas = [f for f in check_index_current(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "DA-2" in falhas[0].message

    def test_dash_no_indice_e_cobrado_pelo_outro_gate(self, tmp_path):
        """Divisao de trabalho entre os dois gates, e nao um bug.

        Uma linha `—` no indice significa "registrada e sem prosa", que e'
        uma afirmacao *consistente* com a realidade — logo `das_index_current`
        deve passar. Quem reprova e' `implemented_das_documented`, porque a
        prosa e' que falta. Se os dois reprovassem pela mesma causa, a
        segunda falha seria ruido.
        """
        _decisoes(tmp_path).write_text(
            "## Decisões de Arquitetura\n\n"
            "| DA | Seção | O que é |\n|---|---|---|\n"
            "| 1 | [1](#decisoes-de-arquitetura) | a |\n"
            "| 2 | **—** | b |\n\n"
            "### 1. Coisa (DA-1)\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n| DA-2 | b |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")

        assert not [f for f in check_index_current(tmp_path) if f.is_failure]
        falhas = [f for f in check_documented_das(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "DA-2" in falhas[0].message

    def test_numero_de_da_sem_declaracao_reprova(self, tmp_path):
        """IIC-AUD-105: numero entre 1 e a maior DA registrada que nao aparece
        no indice nem numa linha '| — |' reprova; declarado, passa."""
        indice = (
            "## Decisões de Arquitetura\n\n"
            "| DA | Seção | O que é |\n|---|---|---|\n"
            "| 1 | [1](#1-coisa-da-1) | a |\n"
            "| 3 | [2](#2-outra-da-3) | b |\n"
            "{extra}\n"
            "### 1. Coisa (DA-1)\n### 2. Outra (DA-3)\n"
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n| DA-3 | b |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")

        _decisoes(tmp_path).write_text(indice.format(extra=""), encoding="utf-8")
        falhas = [f for f in check_index_current(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "DA-2" in falhas[0].message

        _decisoes(tmp_path).write_text(
            indice.format(extra="| — | — | DA-2: número não utilizado |\n"), encoding="utf-8"
        )
        assert not [f for f in check_index_current(tmp_path) if f.is_failure]

    def test_indice_colado_duas_vezes_reprova(self, tmp_path):
        """O indice chegou a estar colado TRES vezes no README de verdade,
        com numeros de secao defasados por um nas copias velhas — e o gate
        passava, porque `re.search` via so a primeira tabela e a comparacao
        de conjunto nao enxerga duplicata."""
        indice = (
            "| DA | Seção | O que é |\n|---|---|---|\n| 1 | [1](#decisoes-de-arquitetura) | a |\n\n"
        )
        _decisoes(tmp_path).write_text(
            "## Decisões de Arquitetura\n\n" + indice + indice + "### 1. Coisa (DA-1)\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")
        falhas = [f for f in check_index_current(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "duplicado" in falhas[0].message
        assert "2 tabelas" in falhas[0].message

    def test_indice_aponta_secao_errada_reprova(self, tmp_path):
        """O numero da coluna "Seção" nunca era conferido: um indice stale
        apontava DA-53 para a secao 37 quando a real era 38, e o gate
        passava porque 37 EXISTIA — secao vizinha tambem e' um numero."""
        _decisoes(tmp_path).write_text(
            "## Decisões de Arquitetura\n\n"
            "| DA | Seção | O que é |\n|---|---|---|\n"
            "| 1 | [2](#decisoes-de-arquitetura) | a |\n"
            "| 2 | [2](#decisoes-de-arquitetura) | b |\n\n"
            "### 1. Coisa (DA-1)\n### 2. Outra (DA-2)\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n| DA-2 | b |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")
        falhas = [f for f in check_index_current(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "DA-1: indice diz secao 2, a real e 1" in falhas[0].message

    def test_indice_architecture_falso_reprova(self, tmp_path):
        """Rotulo ARCHITECTURE sem prosa em docs/ARCHITECTURE.md manda o
        leitor para o arquivo errado — o heading existe no README, nao la."""
        _decisoes(tmp_path).write_text(
            "## Decisões de Arquitetura\n\n"
            "| DA | Seção | O que é |\n|---|---|---|\n"
            "| 1 | ARCHITECTURE | a |\n\n"
            "### 1. Coisa (DA-1)\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")
        falhas = [f for f in check_index_current(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "DA-1 rotulada ARCHITECTURE" in falhas[0].message

    def test_da_duplicada_em_headings_reprova(self, tmp_path):
        """IIC-AUD-034: a mesma DA em duas secoes com numeros diferentes.

        O check de numero de secao nao ve isso (§61 e §48 sao unicos); sem
        este teste o indice aponta para a copia e a prosa original fica orfa.
        """
        _decisoes(tmp_path).write_text(
            "## Decisões de Arquitetura\n\n"
            "| DA | Seção | O que é |\n|---|---|---|\n"
            "| 1 | [1](#a) | a |\n\n"
            "### 1. Coisa (DA-1)\n### 61. Copia (DA-1)\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")

        falhas = [f for f in check_index_current(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "DA duplicada em headings" in falhas[0].message
        assert "DA-1" in falhas[0].message

    def test_secao_com_numero_duplicado_reprova(self, tmp_path):
        """IIC-AUD-010: duas secoes `### 75.` no documento de DAs."""
        _decisoes(tmp_path).write_text(
            "## Decisões de Arquitetura\n\n"
            "| DA | Seção | O que é |\n|---|---|---|\n"
            "| 1 | [75](#a) | a |\n| 2 | [75](#b) | b |\n\n"
            "### 75. Coisa (DA-1)\n### 75. Outra (DA-2)\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-1 | a |\n| DA-2 | b |\n", encoding="utf-8"
        )
        (tmp_path / "docs").mkdir(exist_ok=True)
        (tmp_path / "docs/ARCHITECTURE.md").write_text("# arq\n", encoding="utf-8")
        falhas = [f for f in check_index_current(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "duplicada" in falhas[0].message
        assert "§75" in falhas[0].message


class TestDaTableUnique:
    """IIC-AUD-009: a tabela de DAs cresceu por colagem e duplicou linhas."""

    def test_linha_duplicada_reprova(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-83 | a |\n| DA-88 | b |\n| DA-83 | a de novo |\n",
            encoding="utf-8",
        )
        falhas = [f for f in check_da_table_unique(tmp_path) if f.is_failure]
        assert len(falhas) == 1
        assert "DA-83" in falhas[0].message

    def test_linha_unica_passa(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text(
            "| DA | O que é |\n|---|---|\n| DA-83 | a |\n| DA-88 | b |\n",
            encoding="utf-8",
        )
        assert not [f for f in check_da_table_unique(tmp_path) if f.is_failure]


# ---------------------------------------------------------------------------
# Gates de integridade da documentacao e de alcancabilidade dos conectores.
#
# O ponto destes testes nao e so o caminho feliz: e provar que cada gate
# FALHA diante do defeito que ele existe para pegar. Um gate que so passa
# nao tem teste que o sustente.
# ---------------------------------------------------------------------------


def _docs_root(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs").joinpath("OK.md").write_text("# ok\n", encoding="utf-8")
    return tmp_path


def test_docs_markup_integrity_pass_em_repo_saudavel(tmp_path: Path) -> None:
    root = _docs_root(tmp_path)
    assert not [f for f in check_docs_markup_integrity(root) if f.is_failure]


def test_docs_markup_integrity_acusa_fence_impar(tmp_path: Path) -> None:
    """O defeito real em INGEST_REFERENCE.md: arquivo truncado no meio de
    um heredoc, que engole o resto da renderizacao sem erro visivel."""
    root = _docs_root(tmp_path)
    (root / "docs/TRUNCADO.md").write_text(
        "# t\n\n```bash\npython3 - << 'EOF'\nprint(1)\n", encoding="utf-8"
    )

    falhas = [f for f in check_docs_markup_integrity(root) if f.is_failure]
    assert len(falhas) == 1
    assert "impar" in falhas[0].message
    assert "TRUNCADO.md" in falhas[0].message


def test_docs_markup_integrity_acusa_link_quebrado(tmp_path: Path) -> None:
    root = _docs_root(tmp_path)
    (root / "docs/A.md").write_text("veja [B](B.md)\n", encoding="utf-8")

    falhas = [f for f in check_docs_markup_integrity(root) if f.is_failure]
    assert "link quebrado" in falhas[0].message


def test_docs_markup_integrity_acusa_ancora_quebrada(tmp_path: Path) -> None:
    """IIC-AUD-104: ancora para heading inexistente (no proprio arquivo ou em
    outro .md) reprova; heading acentuado gera slug com acento (GitHub)."""
    root = _docs_root(tmp_path)
    (root / "docs/A.md").write_text(
        "# Decisões\n\n## 2. Não sobe\n\n"
        "[ok](#2-não-sobe) [ruim](#2-nao-sobe) [outro](OK.md#ok) [x](OK.md#nada)\n",
        encoding="utf-8",
    )

    falhas = [f for f in check_docs_markup_integrity(root) if f.is_failure]
    assert len(falhas) == 1
    assert "#2-nao-sobe" in falhas[0].message
    assert "OK.md#nada" in falhas[0].message
    assert "#2-não-sobe" not in falhas[0].message


def test_docs_markup_integrity_ignora_link_externo(tmp_path: Path) -> None:
    root = _docs_root(tmp_path)
    (root / "docs/A.md").write_text(
        "[x](https://exemplo.com/b.md) [y](./OK.md)\n", encoding="utf-8"
    )

    assert not [f for f in check_docs_markup_integrity(root) if f.is_failure]


def test_docs_code_references_acusa_simbolo_inexistente(tmp_path: Path) -> None:
    """O defeito real do tutorial: breakpoint em `structured_llm`, que
    nao existe em lugar nenhum do codigo."""
    root = _docs_root(tmp_path)
    (root / "app").mkdir()
    (root / "app/x.py").write_text("def real():\n    pass\n", encoding="utf-8")
    (root / "docs/T.md").write_text("ponha em `app/x.py::fantasma`\n", encoding="utf-8")

    falhas = [f for f in check_docs_code_references(root) if f.is_failure]
    assert "fantasma nao definido" in falhas[0].message


def test_docs_code_references_acusa_arquivo_que_nao_existe(tmp_path: Path) -> None:
    root = _docs_root(tmp_path)
    (root / "docs/T.md").write_text("ponha em `app/sumiu.py::x`\n", encoding="utf-8")

    falha = next(f for f in check_docs_code_references(root) if f.is_failure)
    assert "nao existe" in falha.message


def test_docs_code_references_acusa_arquivo_errado(tmp_path: Path) -> None:
    """`report_node` citado em graph.py, mas implementado em nodes.py."""
    root = _docs_root(tmp_path)
    (root / "app").mkdir()
    (root / "app/graph.py").write_text("def outra_coisa():\n    pass\n", encoding="utf-8")
    (root / "docs/T.md").write_text("`app/graph.py::report_node`\n", encoding="utf-8")

    falha = next(f for f in check_docs_code_references(root) if f.is_failure)
    assert "report_node nao definido" in falha.message


def test_docs_code_references_acusa_linha_fora_do_arquivo(tmp_path: Path) -> None:
    root = _docs_root(tmp_path)
    (root / "app").mkdir()
    (root / "app/x.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (root / "docs/T.md").write_text("`app/x.py:691`\n", encoding="utf-8")

    falha = next(f for f in check_docs_code_references(root) if f.is_failure)
    assert "691" in falha.message


def test_docs_code_references_aceita_ponto_de_debug_valido(tmp_path: Path) -> None:
    root = _docs_root(tmp_path)
    (root / "app").mkdir()
    (root / "app/x.py").write_text(
        "CONST = 0.25\n\n\ndef f():\n    return CONST\n", encoding="utf-8"
    )
    (root / "docs/T.md").write_text(
        "`app/x.py:1` e `app/x.py::CONST` e `app/x.py::f`\n", encoding="utf-8"
    )

    assert not [f for f in check_docs_code_references(root) if f.is_failure]


def test_docs_code_references_acusa_classe_inexistente(tmp_path: Path) -> None:
    r"""Regressao QA-01: o padrao anterior `^\s*(?:class|SIMBOLO)\b` aprovava
    qualquer linha `class X`, mesmo se o simbolo citado nao existia."""
    root = _docs_root(tmp_path)
    (root / "app").mkdir()
    (root / "app/x.py").write_text(
        "class Real:\n    pass\n\ndef outra_coisa():\n    pass\n", encoding="utf-8"
    )
    (root / "docs/T.md").write_text("`app/x.py::fantasma`\n", encoding="utf-8")

    falhas = [f for f in check_docs_code_references(root) if f.is_failure]
    assert "fantasma nao definido" in falhas[0].message


def test_docs_code_references_acusa_md_fantasma(tmp_path: Path) -> None:
    """O caso real: dez citacoes, em tres docs e em docstrings de codigo,
    apontavam para um `learnings.md` que NUNCA existiu no historico."""
    root = _docs_root(tmp_path)
    (root / "docs/T.md").write_text("ver `learnings.md` do projeto\n", encoding="utf-8")

    falha = next(f for f in check_docs_code_references(root) if f.is_failure)
    assert "learnings.md" in falha.message
    assert "nao existe em lugar nenhum" in falha.message


def test_docs_code_references_acusa_caminho_cru_inexistente(tmp_path: Path) -> None:
    """IIC-AUD-009: `scripts/load_test.py` (real em `app/scripts/`) passava
    porque o regex antigo so' cobria `arquivo.py::simbolo` e `:linha`."""
    root = _docs_root(tmp_path)
    (root / "CLAUDE.md").write_text(
        "ver `scripts/load_test.py` e `app/models/fantasma.py`\n", encoding="utf-8"
    )
    falhas = [f for f in check_docs_code_references(root) if f.is_failure]
    mensagem = " ".join(f.message for f in falhas)
    assert "scripts/load_test.py nao existe" in mensagem
    assert "app/models/fantasma.py nao existe" in mensagem


def test_docs_code_references_nao_cobra_caminho_cru_em_auditoria_historica(tmp_path: Path) -> None:
    root = _docs_root(tmp_path)
    (root / "docs/AUDITORIA_ANTIGA.md").write_text(
        "citava `scripts/sumiu.py` no commit auditado\n", encoding="utf-8"
    )
    assert not [f for f in check_docs_code_references(root) if f.is_failure]


def test_docs_code_references_acusa_caminho_cru_em_docs(tmp_path: Path) -> None:
    """IIC-AUD-030: o gate so' olhava README/CLAUDE e deixava passar
    caminhos quebrados em docs/SECURITY.md e docs/DECISOES."""
    root = _docs_root(tmp_path)
    (root / "docs/T.md").write_text(
        "ver `data/skills/sumiu.yaml` e `deploy/kyma/sumiu.yaml`\n", encoding="utf-8"
    )

    falhas = [f for f in check_docs_code_references(root) if f.is_failure]
    mensagem = " ".join(f.message for f in falhas)
    assert "data/skills/sumiu.yaml nao existe" in mensagem
    assert "deploy/kyma/sumiu.yaml nao existe" in mensagem


def test_docs_code_references_aceita_corpus_citado_cru(tmp_path: Path) -> None:
    """Nome cru de doc do corpus RAG (`odata_timeout_cpi.md`) resolve em
    data/sample_docs/ — citar pelo nome crus e' o costume dos tutoriais,
    e o gate tem de aceitar sem exigir o caminho cheio."""
    root = _docs_root(tmp_path)
    (root / "data" / "sample_docs").mkdir(parents=True)
    (root / "data" / "sample_docs" / "odata_timeout_cpi.md").write_text("# doc\n", encoding="utf-8")
    (root / "docs/T.md").write_text("base: `odata_timeout_cpi.md`\n", encoding="utf-8")

    assert not [f for f in check_docs_code_references(root) if f.is_failure]


def test_docs_code_references_ignora_citacao_composta(tmp_path: Path) -> None:
    """`a.md, b.md` num backtick so e' prosa, nao um caminho — o regex
    nao casa com virgula, e e' isso que impede o falso positivo."""
    root = _docs_root(tmp_path)
    (root / "data" / "sample_docs").mkdir(parents=True)
    (root / "data" / "sample_docs" / "cpi_http_401.md").write_text("# doc\n", encoding="utf-8")
    (root / "docs/T.md").write_text(
        "bases: `cpi_http_401.md, idoc_status_51.md`\n", encoding="utf-8"
    )

    assert not [f for f in check_docs_code_references(root) if f.is_failure]


def test_docs_code_references_nao_acusa_identificadores_em_prosa(tmp_path: Path) -> None:
    """Regressao do falso positivo: `ANTHROPIC_API_KEY`, `RFC_SYSTEM_INFO` e
    `QDRANT_HOST_PORT` nao sao simbolos de Python (o primeiro nem existe, o
    segundo e Function Module ABAP, o terceiro e variavel de shell). Um gate
    que accuse isso vira gate que ninguem ouve."""
    root = _docs_root(tmp_path)
    (root / "app").mkdir()
    (root / "app/x.py").write_text("a = 1\n", encoding="utf-8")
    (root / "docs/T.md").write_text(
        "Use `ANTHROPIC_API_KEY`, `RFC_SYSTEM_INFO` via pyrfc e `${QDRANT_HOST_PORT:-6333}`.\n",
        encoding="utf-8",
    )

    assert not [f for f in check_docs_code_references(root) if f.is_failure]


def _literal_fiel() -> str:
    """Literal gerado a partir da constante real: um fixture de 3 conectores
    dispararia o check de espelhamento e nao estaria mais testando a regra
    que dice ser."""
    from app.evaluation.gates import PIPELINE_INTERFACE_TYPES

    valores = "".join(f'\n            "{v}",' for v in sorted(PIPELINE_INTERFACE_TYPES))
    return f"    interface_type: (Literal[{valores}\n        ] | None) = None\n"


def _connector_root(
    tmp_path: Path,
    registry: str,
    literal: str,
    supervisor: str,
    cli: str | None = None,
    ui: str | None = None,
    admin: str | None = None,
    matriz: str | None = None,
    seed: str | None = None,
    form: str | None = None,
    skills: dict[str, str] | None = None,
) -> Path:
    (tmp_path / "app/connectors").mkdir(parents=True)
    (tmp_path / "app/agent").mkdir(parents=True)
    (tmp_path / "app/admin").mkdir(parents=True)
    (tmp_path / "app/models").mkdir(parents=True)
    (tmp_path / "frontend/src/components").mkdir(parents=True)
    (tmp_path / "app/connectors/__init__.py").write_text(registry, encoding="utf-8")
    (tmp_path / "app/models/models.py").write_text(literal, encoding="utf-8")
    # O catalogo admin (DA-49) tem a PROPRIA tupla `CONNECTOR_TYPES` — e' a
    # sexta superficie do gate. O fixture precisa fornecer uma de verdade:
    # o arquivo do admin nao e' o Literal do pipeline.
    (tmp_path / "app/admin/models.py").write_text(admin or _SANE_ADMIN, encoding="utf-8")
    (tmp_path / "app/agent/supervisor.py").write_text(supervisor, encoding="utf-8")
    (tmp_path / "app/agent/graph.py").write_text(cli or _SANE_CLI, encoding="utf-8")
    (tmp_path / "frontend/src/components/DiagnoseView.tsx").write_text(
        ui or _SANE_UI, encoding="utf-8"
    )
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs/ARCHITECTURE.md").write_text(matriz or _SANE_MATRIZ, encoding="utf-8")
    # Setima superficie (DA-57): o seed da migration das fontes de busca. Sem
    # ele a resolucao fail-closed deixa o conector sem fonte aprovada — o
    # sintoma (busca web silenciosamente desligada) so aparece em producao.
    (tmp_path / "alembic/versions").mkdir(parents=True)
    (tmp_path / "alembic/versions/008_create_web_search_sources.py").write_text(
        seed or _SANE_SEED, encoding="utf-8"
    )
    # Oitava superficie (achado na DA-57): o <select name="connector_type">
    # do formulario de sistemas do admin.
    (tmp_path / "app/admin/templates").mkdir(parents=True)
    (tmp_path / _ADMIN_SYSTEMS_FORM).write_text(form or _SANE_SISTEMS_FORM, encoding="utf-8")
    # DA-63: se skills declarativas forem fornecidas, cria data/skills/*.yaml.
    if skills:
        (tmp_path / "data/skills").mkdir(parents=True)
        for name, content in skills.items():
            (tmp_path / f"data/skills/{name}").write_text(content, encoding="utf-8")
    return tmp_path


_ADMIN_SYSTEMS_FORM = Path("app/admin/templates/systems.html")

# Mesmo formato do arquivo real: <select name="connector_type"> com <option
# value="...">. O gate ancora no nome do select e nos values, e' nao no texto
# visivel, para nao confundir com os selects de environment/status do mesmo
# arquivo.
_SANE_SISTEMS_FORM = """<form id="newsystem">
  <select name="connector_type">
    <option value="odata">odata</option>
    <option value="successfactors">successfactors</option>
    <option value="apim">apim</option>
  </select>
  <select name="environment">
    <option value="prod">prod</option>
  </select>
  <select name="status">
    <option value="active">active</option>
  </select>
</form>
"""


# Formato espelhado em alembic/versions/008_create_web_search_sources.py: o gate
# ancora no bloco `SEED = [...]` e nos tuples ("interface_type", "site", "termo").
_SANE_SEED = (
    '"""DA-57: fontes de busca web aprovadas."""\n\n'
    "SEED = [\n"
    '    ("odata", "site:help.sap.com/docs/odata", "OData V4 SAP gateway"),\n'
    '    ("successfactors", "site:help.sap.com/docs/sap-successfactors", "SFSF EC"),\n'
    '    ("apim", "site:help.sap.com/docs/api-management", "SAP API Management"),\n'
    "]\n"
)


_SANE_REGISTRY = (
    "_REGISTRY = {\n    'odata': ODataConnector,\n    'successfactors': SFSFConnector,\n"
    "    'apim': APIManagementConnector,\n}\n\n"
    "_REAL_MODE_SETTING = {\n    'odata': 'odata_service_url',\n"
    "    'successfactors': 'sfsf_base_url',\n    'apim': 'apim_analytics_url',\n}\n"
)
# O gate ancora no nome do campo (`interface_type:`) de proposito: app/models/models.py
# tem outros Literals (sensitivity_level, trust_level) e ancorar no Literal
# "qualquer um" trazia sensitivity_level junto.

_SANE_SUPERVISOR = "_SAP_INTERFACE_TYPES = {'odata'}\n_SAAS_INTERFACE_TYPES = {'successfactors'}\n"

# Supervisor no padrao DA-63 (declarativo): _DOMAIN_INDEX derivado do skill
# registry. O quality gate verifica este padrao primeiro, depois fallback
# para o antigo (_SAP_INTERFACE_TYPES / _SAAS_INTERFACE_TYPES).
_SANE_SUPERVISOR_DA63 = """_DOMAIN_INDEX = build_domain_index()

def classify_domain(state):
    interface_type = (state.get('interface_type') or '').lower()
    domain = _DOMAIN_INDEX.get(interface_type)
    if domain == 'sap':
        return 'sap'
    if domain == 'saas':
        return 'saas'
    return 'generic'
"""

# Catalogo de sistemas do admin (DA-49) cobrindo o registro sane acima.
_SANE_ADMIN = 'CONNECTOR_TYPES = ("odata", "successfactors", "apim")\n'

_SANE_CLI = (
    'parser.add_argument("--interface", choices=["odata", "successfactors", "apim"],'
    " default=None)\n"
)

_SANE_UI = (
    "const SYSTEMS: Array<[string, string]> = [\n"
    "  ['', 'Sem conector'],\n"
    "  ['odata', 'OData / SAP Gateway'],\n"
    "  ['successfactors', 'SAP SuccessFactors EC'],\n"
    "  ['apim', 'SAP API Management'],\n"
    "];\n"
)


def test_connector_reachable_pass_quando_tem_camada(tmp_path: Path) -> None:
    root = _connector_root(tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR)
    assert not [f for f in check_connector_reachable(root) if f.is_failure]


def test_connector_reachable_acusa_conector_ausente_no_catalogo_admin(tmp_path: Path) -> None:
    """Sexta superficie (DA-56): o Literal aceita, o supervisor cobre, o CLI e a
    UI oferecem — e o `CONNECTOR_TYPES` do catalogo admin (onde a correlacao
    DA-50 resolve incidente->sistema) nao tem o conector. Era exatamente o
    estado em que `successfactors` estava, com o gate verde."""
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY,
        _literal_fiel(),
        _SANE_SUPERVISOR,
        admin='CONNECTOR_TYPES = ("odata", "apim")\n',
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert falhas
    assert "CONNECTOR_TYPES" in falhas[0].message


def test_connector_reachable_acusa_conector_inalcancavel(tmp_path: Path) -> None:
    """O defeito real: successfactors registrado e com credencial no
    .env.example, mas o Literal do pipeline devolvia 422."""
    sem_sfsf = _literal_fiel().replace('"successfactors",', "")
    root = _connector_root(tmp_path, _SANE_REGISTRY, sem_sfsf, _SANE_SUPERVISOR)
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert "successfactors" in falhas[0].message
    assert "422" in falhas[0].message


def test_connector_reachable_acusa_conector_que_cai_em_generic(tmp_path: Path) -> None:
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY,
        _literal_fiel(),
        "_SAP_INTERFACE_TYPES = {'odata'}\n_SAAS_INTERFACE_TYPES = set()\n",
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert "generic" in falhas[0].message


def test_connector_reachable_acusa_cli_que_rejeita_conector(tmp_path: Path) -> None:
    """A quarta superficie: o CLI. successfactors aceito pelo Literal,
    coberto pelo supervisor, listado no catalogo — e o argparse do CLI
    ainda o rejeitava com "invalid choice"."""
    cli_sem_sfsf = _SANE_CLI.replace('"successfactors", ', "")
    root = _connector_root(
        tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR, cli_sem_sfsf
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert "rejeita ['successfactors']" in falhas[0].message


def test_connector_reachable_acusa_dropdown_sem_conector(tmp_path: Path) -> None:
    """A quinta superficie: o dropdown da UI web. Achado na homologacao —
    gates verdes em quatro superficies e o usuario sem o conector na
    tela. Quem opera so pela interface nunca conseguiria enviar
    interface_type=successfactors."""
    ui_sem_sfsf = _SANE_UI.replace("  ['successfactors', 'SAP SuccessFactors EC'],\n", "")
    root = _connector_root(
        tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR, ui=ui_sem_sfsf
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert "dropdown nao oferece ['successfactors']" in falhas[0].message


def test_connector_reachable_permite_apim_em_generic(tmp_path: Path) -> None:
    """apim e cross-vendor de proposito: ficar em generic e decisao, nao bug."""
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY,
        _literal_fiel(),
        "_SAP_INTERFACE_TYPES = {'odata'}\n_SAAS_INTERFACE_TYPES = {'successfactors'}\n",
    )
    assert not [f for f in check_connector_reachable(root) if f.is_failure]


def test_connector_reachable_acusa_seed_das_fontes_de_busca_sem_conector(
    tmp_path: Path,
) -> None:
    """Setima superficie (DA-57): o conector esta em todas as outras seis e
    mesmo assim nao tem linha no seed de web_search_sources. Com a resolucao
    fail-closed o efeito e busca web desligada para ele, sem erro em
    lugar nenhum — o tipo de coisa que so se descobre em producao.
    """
    seed_sem_apim = _SANE_SEED.replace(
        '    ("apim", "site:help.sap.com/docs/api-management", "SAP API Management"),\n', ""
    )
    assert "apim" not in seed_sem_apim
    root = _connector_root(
        tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR, seed=seed_sem_apim
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert falhas, "seed sem o conector deveria reprovar"
    assert "008_create_web_search_sources.py" in falhas[0].message
    assert "apim" in falhas[0].message


def test_connector_reachable_acusa_seed_inexistente(tmp_path: Path) -> None:
    """O gate tambem falha se a migration sumir: a checagem do seed nao pode
    passar por ausencia de arquivo (fail-closed, como o resto do gate)."""
    root = _connector_root(tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR)
    (root / "alembic/versions/008_create_web_search_sources.py").unlink()
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert falhas
    assert "008_create_web_search_sources.py nao existe" in falhas[0].message


def test_connector_reachable_acusa_formulario_de_sistemas_sem_conector(
    tmp_path: Path,
) -> None:
    """Oitava superficie (achado na DA-57): o conector esta nas outras sete e
    nao aparece no <select name="connector_type"> do formulario de sistemas do
    admin. Sem essa linha ele nao TEM como ter `integration_system`, e a
    correlacao DA-50 cai no fallback por `connector_type` — que e'
    fail-closed com mais de um sistema do mesmo tipo. Nao e a mesma morte
    das outras superficies (aqui nao ha erro, ha correlacao ambigua), e por
    isso o gate precisa ler o arquivo em vez de confiar em `CONNECTOR_TYPES`.
    """
    form_sem_apim = _SANE_SISTEMS_FORM.replace('    <option value="apim">apim</option>\n', "")
    assert "apim" not in form_sem_apim
    root = _connector_root(
        tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR, form=form_sem_apim
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert falhas, "formulario sem o conector deveria reprovar"
    assert "systems.html" in falhas[0].message
    assert "apim" in falhas[0].message


def test_connector_reachable_ignora_selects_de_environment_e_status(tmp_path: Path) -> None:
    """O gate ancora em `name="connector_type"`, nao no primeiro <select> do
    arquivo: `prod`/`active` estao no mesmo template e nao sao conectores."""
    root = _connector_root(tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR)
    assert not [f for f in check_connector_reachable(root) if f.is_failure]


def test_connector_reachable_acusa_formulario_de_sistemas_inexistente(tmp_path: Path) -> None:
    root = _connector_root(tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR)
    (root / _ADMIN_SYSTEMS_FORM).unlink()
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert falhas
    assert "systems.html nao existe" in falhas[0].message


def test_repositorio_real_formulario_de_sistemas_cobre_o_literal() -> None:
    """Trava a regressao no arquivo real, sem depender do gate: se alguem
    remover `po` ou `successfactors` do <select> de novo, isto falha."""
    html = (_ADMIN_SYSTEMS_FORM).read_text(encoding="utf-8")
    bloco = re.search(r"<select\s+name=\"connector_type\"[^>]*>(.*?)</select>", html, re.DOTALL)
    assert bloco is not None, "select de connector_type nao encontrado em systems.html"
    oferidos = set(re.findall(r"""value=["']([a-z_]+)["']""", bloco.group(1)))
    from app.admin.models import CONNECTOR_TYPES

    assert set(CONNECTOR_TYPES) <= oferidos, (
        f"conectores sem linha no formulario de sistemas: {sorted(set(CONNECTOR_TYPES) - oferidos)}"
    )


def test_connector_reachable_acusa_registry_e_settings_divergentes(tmp_path: Path) -> None:
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY.replace("'apim': 'apim_analytics_url',\n", ""),
        _literal_fiel(),
        _SANE_SUPERVISOR,
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert "divergem" in falhas[0].message


def test_repositorio_real_tem_todo_conector_alcancavel() -> None:
    """Trava a regressao: se alguem remover successfactors do Literal de
    novo, este teste falha — e o gate tambem."""
    from app.connectors import get_connector
    from app.models import IncidentRequest

    for nome in (
        "odata",
        "rfc",
        "servicenow",
        "salesforce",
        "workday",
        "ariba",
        "successfactors",
        "cap",
        "apim",
    ):
        req = IncidentRequest(description="d", interface_type=nome)
        assert req.interface_type == nome
        assert get_connector(nome) is not None

    assert not [f for f in check_connector_reachable() if f.is_failure]


# ── gate connector_validation_matrix: a matriz de docs nao pode apodrecer ───
_SANE_MATRIZ = """| Conector | Estado hoje | Falta so |
|---|---|---|
| `ODataConnector` | Real | tenant CPI |
| `SFSFConnector` | Real | tenant SuccessFactors |
| `APIManagementConnector` | schema ESPECULATIVO | validar contrato |
"""


def test_connector_validation_matrix_pass_quando_tem_linha_para_cada(tmp_path: Path) -> None:
    root = _connector_root(tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR)
    assert not [f for f in check_connector_validation_matrix(root) if f.is_failure]


def test_connector_validation_matrix_acusa_conector_sem_linha(tmp_path: Path) -> None:
    """O defeito que durou meses: `SuccessFactorsConnector` (DA-34) nao tinha
    linha nenhuma na matriz. O Literal aceitava, o supervisor roteava, a UI
    oferecia — e a unica fonte de verdade sobre "foi testado contra um sistema
    de verdade?" nao falava nele. Nenhum gate lia a matriz, entao o verde nao
    dizia nada."""
    sem_sfsf = "\n".join(
        linha for linha in _SANE_MATRIZ.splitlines() if "SFSFConnector" not in linha
    )
    root = _connector_root(
        tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR, matriz=sem_sfsf
    )
    falhas = [f for f in check_connector_validation_matrix(root) if f.is_failure]
    assert falhas
    assert "SFSFConnector" in falhas[0].message


def test_connector_validation_matrix_acusa_conector_novo_sem_documentar(tmp_path: Path) -> None:
    """Direcao que impede a reincidencia: registrar um conector novo e' o
    caminho feliz, e o gate tem de cobrar a documentacao no mesmo commit."""
    # O par precisa entrar nos DOIS dicts: `_registered_connectors` ja falha
    # quando _REGISTRY e _REAL_MODE_SETTING divergem, entao ancorar num so
    # faria o gate acusar a divergencia em vez da falta de documentacao.
    registry_com_mq = _SANE_REGISTRY.replace(
        "    'apim': APIManagementConnector,\n",
        "    'apim': APIManagementConnector,\n    'mq': MQConnector,\n",
    ).replace(
        "    'apim': 'apim_analytics_url',\n",
        "    'apim': 'apim_analytics_url',\n    'mq': 'mq_base_url',\n",
    )
    assert registry_com_mq != _SANE_REGISTRY
    root = _connector_root(tmp_path, registry_com_mq, _literal_fiel(), _SANE_SUPERVISOR)
    falhas = [f for f in check_connector_validation_matrix(root) if f.is_failure]
    assert falhas
    assert "MQConnector" in falhas[0].message


def test_connector_validation_matrix_acusa_linha_orfa(tmp_path: Path) -> None:
    """Linha de conector que nao existe no registro e' documentacao que
    'sabe': herda a validacao de um conector que foi removido."""
    com_orfa = _SANE_MATRIZ + "| `HyperledgerConnector` | **Real, validado** | Nada |\n"
    root = _connector_root(
        tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR, matriz=com_orfa
    )
    falhas = [f for f in check_connector_validation_matrix(root) if f.is_failure]
    assert falhas
    assert "HyperledgerConnector" in falhas[0].message


def test_connector_validation_matrix_ignora_prosa_que_mentiona_conector(tmp_path: Path) -> None:
    """So conta linha de tabela. Um conector citado de passagem no texto do
    documento (a secao da DA-XX, a lista de limitacoes) nao documenta a
    matriz — se contasse, apagar a tabela inteira deixaria o gate verde."""
    sem_tabela = _SANE_MATRIZ.splitlines()[2]  # nada alem do cabecalho e o separador
    prosa = "# Connectores\n\nO `SuccessFactorsConnector` foi implementado na DA-34.\n"
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY,
        _literal_fiel(),
        _SANE_SUPERVISOR,
        matriz=sem_tabela + "\n" + prosa,
    )
    falhas = [f for f in check_connector_validation_matrix(root) if f.is_failure]
    assert falhas


# ── gate da_registered: DA citada no codigo tem linha no CLAUDE.md ─────────
def _da_root(tmp_path: Path, registro: str, extra: dict[str, str] | None = None) -> Path:
    (tmp_path / "app/rag").mkdir(parents=True)
    (tmp_path / "app/evaluation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "CLAUDE.md").write_text(registro, encoding="utf-8")
    for nome, texto in (extra or {"app/rag/retriever.py": "# sem citacao\n"}).items():
        caminho = tmp_path / nome
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(texto, encoding="utf-8")
    return tmp_path


_REGISTRO_OK = """# Contexto

| DA | O que | Onde |
|---|---|---|
| DA-39 | politica de soberania | gateway |
| DA-56 | conector PO/PI | connectors |

**DAs candidatas (sem implementacao ainda):**
- DA-31: SAP AI Agent Hub — bloqueada: exige tenant Kyma
"""


def test_da_registered_pass_quando_toda_citacao_tem_linha(tmp_path: Path) -> None:
    root = _da_root(tmp_path, _REGISTRO_OK, {"app/rag/retriever.py": "# DA-39 no codigo\n"})
    assert not [f for f in check_da_registered(root) if f.is_failure]


def test_da_registered_acusa_da_so_na_docstring(tmp_path: Path) -> None:
    """O defeito achado: cinco decisoes entregues com prosa apenas em
    docstring (DA-12, 38, 39, 40, 41) e tres com prosa no ARCHITECTURE e
    nenhuma linha na tabela (DA-32, 34, 35). Nenhum gate anterior via o
    codigo — os dois gates de DA so' comparavam a tabela do CLAUDE.md com a
    prosa do README, e as duas pontas eram cegas a implementacao."""
    root = _da_root(tmp_path, _REGISTRO_OK, {"app/rag/retriever.py": "# DA-41 no codigo\n"})
    falhas = [f for f in check_da_registered(root) if f.is_failure]
    assert falhas
    assert "DA-41" in falhas[0].message
    assert "app/rag/retriever.py" in falhas[0].message


def test_da_registered_aceita_da_candidata(tmp_path: Path) -> None:
    """DA-31 esta na lista de candidatas e citada no codigo: e' o estado
    correto de uma decisao ainda nao tomada, nao uma DA nao registrada."""
    root = _da_root(tmp_path, _REGISTRO_OK, {"app/rag/retriever.py": "# DA-31 citado\n"})
    assert not [f for f in check_da_registered(root) if f.is_failure]


def test_da_registered_aceita_forma_agrupada_do_registro(tmp_path: Path) -> None:
    """`DA-4/8` na tabela registra as duas. O gate nao pode acusar a forma
    que o proprio projeto ja usa."""
    registro = _REGISTRO_OK.replace("| DA-39 |", "| DA-4/8 |")
    root = _da_root(tmp_path, registro, {"app/rag/retriever.py": "# DA-4 e DA-8 citados\n"})
    assert not [f for f in check_da_registered(root) if f.is_failure]


def test_da_registered_ignora_tests(tmp_path: Path) -> None:
    """Os testes dos gates inventam numeros sinteticos para exercitar o
    caminho de falha. Exigir registro deles seria exigir documentacao de um
    numero que so existe dentro do fixture."""
    root = _da_root(tmp_path, _REGISTRO_OK)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_algo.py").write_text("# DA-77 sintetico\n", encoding="utf-8")
    assert not [f for f in check_da_registered(root) if f.is_failure]


def test_da_registered_ignora_diretorios_ocultos(tmp_path: Path) -> None:
    """`.precommit-cache-tmp/.../site-packages` traz Python vendorizado cujo
    SPDX header casa o padrao. Sem o filtro, o gate acusaria numeros de
    biblioteca de terceiros."""
    root = _da_root(tmp_path, _REGISTRO_OK)
    (tmp_path / ".venv/lib/site-packages").mkdir(parents=True)
    (tmp_path / ".venv/lib/site-packages/x.py").write_text(
        "# DA-88 de terceiros\n", encoding="utf-8"
    )
    assert not [f for f in check_da_registered(root) if f.is_failure]


def test_da_registered_conta_migration_de_alembic(tmp_path: Path) -> None:
    """Alembic e' implementacao: a migration 006 (DA-53) grava proveniencia de
    prompt e modelo. Se `alembic/` ficasse fora, uma DA implementada so' por
    migration escaparia."""
    root = _da_root(tmp_path, _REGISTRO_OK, {"alembic/versions/006_x.py": "# DA-53 aqui\n"})
    falhas = [f for f in check_da_registered(root) if f.is_failure]
    assert falhas and "DA-53" in falhas[0].message


# ---------------------------------------------------------------------------
# DA-58: mapa de cobertura (nona superficie da invariante 23)
# ---------------------------------------------------------------------------

_COB_MECANISMOS = ["apis", "odata"]
_COB_PRODUTOS = [
    {"name": "Ariba", "kind": "sap_product", "mechanisms": {"apis": "supported", "odata": "none"}},
    {
        "name": "BW/4HANA",
        "kind": "sap_product",
        "mechanisms": {"apis": "supported", "odata": "supported"},
    },
]
# `odata` entra como GENERICO de BW/4HANA, que e' o caso real: cliente de
# mecanismo alcançando um produto que ele nao foi feito para. E' o que
# deixa `apis` de BW/4HANA como lacuna real enquanto o gate segue verde.
_COB_COBERTURA = [
    {
        "connector": "ariba",
        "dedicated_to": "Ariba",
        "generic_for": [],
        "mechanisms": ["apis"],
    },
    {
        "connector": "odata",
        "dedicated_to": None,
        "generic_for": ["BW/4HANA"],
        "mechanisms": ["odata"],
    },
]
_COB_REGISTRY = (
    "_REGISTRY = {\n    'ariba': AribaConnector,\n    'odata': ODataConnector,\n}\n\n"
    "_REAL_MODE_SETTING = {\n    'ariba': 'ariba_base_url',\n    'odata': 'odata_service_url',\n}\n"
)


def _cobertura_root(
    tmp_path: Path, cobertura: list | None = None, produtos: list | None = None
) -> Path:
    root = _connector_root(
        tmp_path,
        _COB_REGISTRY,
        _literal_fiel(),
        _SANE_SUPERVISOR,
        admin='CONNECTOR_TYPES = ("ariba", "odata")\n',
    )
    (root / "data").mkdir(exist_ok=True)
    (root / "data/sap_products.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "mechanisms": _COB_MECANISMOS,
                "products": produtos or _COB_PRODUTOS,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "data/connector_coverage.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "coverage": cobertura or _COB_COBERTURA}, sort_keys=False
        ),
        encoding="utf-8",
    )
    return root


def _reescrever_mapa(root: Path) -> None:
    """Regrava o doc versionado com o mapa atual, para os testes que so' mudam
    o DADO."""
    from app.evaluation.coverage import (
        GENERATED_NOTE,
        build_matrix,
        load_all,
        render_markdown,
    )

    produtos, mecanismos, cobertura, labels = load_all(root)
    (root / "docs/COVERAGE_MAP.md").write_text(
        render_markdown(
            build_matrix(produtos, cobertura, mecanismos), mecanismos, labels, GENERATED_NOTE
        ),
        encoding="utf-8",
    )


def test_connector_coverage_pass_com_dado_coerente(tmp_path: Path) -> None:
    root = _cobertura_root(tmp_path)
    _reescrever_mapa(root)
    assert not [f for f in check_connector_coverage(root) if f.is_failure]


def test_connector_coverage_acusa_conector_registrado_sem_declaracao(tmp_path: Path) -> None:
    """Nona superficie: `odata` esta no registro e funciona, e nao tem linha
    de cobertura. O mapa o mostraria como ausente, sem nenhum sinal — a
    mesma morte silenciosa das outras oito."""
    root = _cobertura_root(tmp_path, cobertura=[_COB_COBERTURA[0]])
    _reescrever_mapa(root)
    falhas = [f for f in check_connector_coverage(root) if f.is_failure]
    assert falhas and "odata" in falhas[0].message and "sem linha" in falhas[0].message


def test_connector_coverage_acusa_mapa_desatualizado(tmp_path: Path) -> None:
    """O doc versionado e' gerado. Se os dados mudarem e o doc nao for
    regravado, o mapa passa a descrever um estado que nao existe."""
    root = _cobertura_root(tmp_path)
    _reescrever_mapa(root)
    # `ariba` passa a cobrir BW/4HANA tambem. O dado continua COERENTE (o
    # produto existe, o conector existe) — so' mudou. E' o caso comum de
    # desatualizacao, edicao de dado sem `--write`, e nao o de incoerencia.
    cobertura = [
        {**_COB_COBERTURA[0], "dedicated_to": ["Ariba", "BW/4HANA"]},
        _COB_COBERTURA[1],
    ]
    (root / "data/connector_coverage.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "coverage": cobertura}, sort_keys=False),
        encoding="utf-8",
    )
    falhas = [f for f in check_connector_coverage(root) if f.is_failure]
    assert falhas and "desatualizado" in falhas[0].message


def test_connector_coverage_acusa_doc_ausente(tmp_path: Path) -> None:
    root = _cobertura_root(tmp_path)
    falhas = [f for f in check_connector_coverage(root) if f.is_failure]
    assert falhas and "coverage_map.py --write" in falhas[0].message


def test_connector_coverage_acusa_produto_fantasma(tmp_path: Path) -> None:
    root = _cobertura_root(
        tmp_path,
        cobertura=[
            {
                "connector": "ariba",
                "dedicated_to": "NaoExiste",
                "generic_for": [],
                "mechanisms": ["apis"],
            }
        ],
    )
    _reescrever_mapa(root)
    falhas = [f for f in check_connector_coverage(root) if f.is_failure]
    assert falhas and "NaoExiste" in falhas[0].message


def test_connector_coverage_NAO_reprova_por_lacuna(tmp_path: Path) -> None:
    """A distincao que mantem o gate util: `BW/4HANA` expoe `apis` e nada no
    repo alcanca. Reprovar por isso seria exigir 20+ conectores novos para o
    CI ficar verde, e o gate deixaria de medir se o mapa bate com o codigo."""
    root = _cobertura_root(tmp_path)
    _reescrever_mapa(root)
    findings = check_connector_coverage(root)
    assert not [f for f in findings if f.is_failure]
    assert any("lacuna" in f.message for f in findings)


def test_connector_coverage_entra_no_run_all(tmp_path: Path) -> None:
    """O gate tem de rodar junto com a suite (DA-51); registration e' o que
    transforma uma checagem em barreira."""
    assert "connector_coverage" in GATES
    assert GATES["connector_coverage"] is check_connector_coverage


def test_link_para_doc_git_ignored_conta_como_quebrado(tmp_path: Path) -> None:
    """Validacao 2026-10-06 (CI-01): na maquina do autor o doc ignorado
    existe e o gate passava; no CI (clone limpo) reprovava. Agora o arquivo
    ignorado conta como inexistente nos dois lugares."""
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("docs/PRIVADO.md\n")
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "docs" / "PRIVADO.md").write_text("# privado\n")
    (tmp_path / "docs" / "PUBLICO.md").write_text("Veja [o privado](PRIVADO.md) e `PRIVADO.md`.\n")
    from app.evaluation import gates

    markup = gates.check_docs_markup_integrity(tmp_path)
    refs = gates.check_docs_code_references(tmp_path)
    assert markup[0].is_failure and "PRIVADO.md" in markup[0].message
    assert refs[0].is_failure and "PRIVADO.md" in refs[0].message


# ── DA-63: declarative skill registry tests ──────────────────────────

_SKILL_DIAGNOSE = """name: diagnose_incident
version: "1.0"
risk_level: low
destructive: false
scopes_required:
  - integration.read
approval_required: false
data_sensitivity: confidential
timeout_seconds: 180.0
max_retries: 1
domains:
  sap: [odata, rfc, cap, po]
  saas: [servicenow, salesforce, workday, ariba, successfactors]
"""

_SKILL_LIST = """name: list_connectors
version: "1.0"
risk_level: low
destructive: false
scopes_required:
  - integration.read
approval_required: false
data_sensitivity: public
timeout_seconds: 5.0
domains: {}
"""


def test_connector_reachable_pass_com_padrao_declarativo(tmp_path: Path) -> None:
    """O gate reconhece _DOMAIN_INDEX + data/skills/*.yaml como fonte de
    verdade para o mapeamento interface_type -> dominio."""
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY,
        _literal_fiel(),
        _SANE_SUPERVISOR_DA63,
        skills={"diagnose_incident.yaml": _SKILL_DIAGNOSE, "list_connectors.yaml": _SKILL_LIST},
    )
    assert not [f for f in check_connector_reachable(root) if f.is_failure]


def test_connector_reachable_falha_skill_sem_dominio_completo(tmp_path: Path) -> None:
    """Se o skill registry nao mapear um interface_type, ele cai em generic."""
    # Skill que so mapeia sap — falta saas
    partial = _SKILL_DIAGNOSE.replace(
        "  saas: [servicenow, salesforce, workday, ariba, successfactors]\n", ""
    )
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY,
        _literal_fiel(),
        _SANE_SUPERVISOR_DA63,
        skills={"diagnose_incident.yaml": partial},
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert falhas
    assert "nao mapeados para dominio" in falhas[0].message


def test_connector_reachable_fallback_antigo_com_declarativo_pendente(tmp_path: Path) -> None:
    """Quando nao ha _DOMAIN_INDEX no supervisor, o gate volta ao padrao
    antigo (_SAP_INTERFACE_TYPES / _SAAS_INTERFACE_TYPES)."""
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY,
        _literal_fiel(),
        _SANE_SUPERVISOR,  # usa padrao antigo
    )
    assert not [f for f in check_connector_reachable(root) if f.is_failure]


def test_connector_reachable_falha_sem_diretorio_de_skills(tmp_path: Path) -> None:
    """Supervisor declarativo sem data/skills: o gate reprova (antes ele
    procurava em root.parent e, sem achar, so acusava orfaos)."""
    root = _connector_root(tmp_path, _SANE_REGISTRY, _literal_fiel(), _SANE_SUPERVISOR_DA63)
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert falhas
    assert "data/skills nao existe" in falhas[0].message


def test_connector_reachable_falha_quando_dockerfile_nao_copia_skills(tmp_path: Path) -> None:
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY,
        _literal_fiel(),
        _SANE_SUPERVISOR_DA63,
        skills={"diagnose_incident.yaml": _SKILL_DIAGNOSE, "list_connectors.yaml": _SKILL_LIST},
    )
    (root / "Dockerfile").write_text(
        "COPY --chown=appuser:appuser data/sample_docs/ data/sample_docs/\n", encoding="utf-8"
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert falhas
    assert "Dockerfile nao copia data/skills/" in falhas[0].message
    (root / "Dockerfile").write_text(
        "COPY --chown=appuser:appuser data/skills/ data/skills/\n", encoding="utf-8"
    )
    assert not [f for f in check_connector_reachable(root) if f.is_failure]


def test_connector_reachable_usa_o_parser_do_runtime(tmp_path: Path) -> None:
    """Dominio invalido e' rejeitado pelo skill_registry; o gate nao pode
    aceitar o que o supervisor descarta."""
    invalido = _SKILL_DIAGNOSE.replace("  sap: [", "  sapp: [")
    root = _connector_root(
        tmp_path,
        _SANE_REGISTRY,
        _literal_fiel(),
        _SANE_SUPERVISOR_DA63,
        skills={"diagnose_incident.yaml": invalido},
    )
    falhas = [f for f in check_connector_reachable(root) if f.is_failure]
    assert falhas
    assert "nao mapeados para dominio" in falhas[0].message


# ── gate llm_baseline: generated_at + freshness (M-24) ─────────────


def _llm_baseline_root(tmp_path: Path, generated_at: str | None = None) -> Path:
    """Cria um baseline minimo com `generated_at` controlado."""
    root = tmp_path
    (root / "data").mkdir(exist_ok=True)
    (root / "data/eval").mkdir(parents=True, exist_ok=True)
    baseline = {
        "generated_at": generated_at,
        "model": "qwen3.6:35b-a3b",
        "cases": {"caso_a": True},
        "pass_rate": 1.0,
    }
    (root / "data/eval/promptfoo_baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return root


def test_llm_baseline_pass_com_timestamp_real(tmp_path: Path) -> None:
    """Baseline com timestamp real do promptfoo passa sem aviso."""
    root = _llm_baseline_root(tmp_path, "2026-10-13T17:56:04.501Z")
    assert not [f for f in check_llm_baseline(root) if f.is_failure]


def test_llm_baseline_falha_com_generated_at_null(tmp_path: Path) -> None:
    """generated_at null é falha — data de medição ausente."""
    root = _llm_baseline_root(tmp_path, None)
    falhas = [f for f in check_llm_baseline(root) if f.is_failure]
    assert len(falhas) == 1
    assert "null" in falhas[0].message


def test_llm_baseline_warn_com_freshness_maior_30_dias(tmp_path: Path) -> None:
    """Baseline com mais de 30 dias vira WARN, nao FAIL."""
    root = _llm_baseline_root(tmp_path, "2026-08-01T00:00:00Z")
    avisos = [f for f in check_llm_baseline(root) if f.severity == "warn"]
    assert len(avisos) == 1
    assert "dias de idade" in avisos[0].message
    # Mas nao e' falha.
    assert not [f for f in check_llm_baseline(root) if f.is_failure]


def test_llm_baseline_falha_com_data_no_futuro(tmp_path: Path) -> None:
    """Data no futuro é rejeitada — provavel data digitada a mao."""
    root = _llm_baseline_root(tmp_path, "2030-01-01T00:00:00Z")
    falhas = [f for f in check_llm_baseline(root) if f.is_failure]
    assert len(falhas) == 1
    assert "futuro" in falhas[0].message


def test_llm_baseline_falha_com_iso_invalido(tmp_path: Path) -> None:
    """String que nao e' ISO 8601 valida reprova."""
    root = _llm_baseline_root(tmp_path, "ontem-de-tarde")
    falhas = [f for f in check_llm_baseline(root) if f.is_failure]
    assert len(falhas) == 1
    assert "ISO 8601" in falhas[0].message


def test_llm_baseline_pass_com_timestamp_sem_fuso(tmp_path: Path) -> None:
    """Timestamp sem fuso (ex: '2026-10-13T17:56:04') e' ISO valido;
    o gate normaliza para UTC antes de comparar."""
    root = _llm_baseline_root(tmp_path, "2026-10-13T17:56:04")
    assert not [f for f in check_llm_baseline(root) if f.is_failure]


# ── gravador _write_baseline (fecha o ciclo com o gate) ─────────────


def _make_promptfoo_json(data: dict | list, tmp_path: Path) -> Path:
    """Cria um arquivo JSON simulando saida do promptfoo em tmp_path."""
    p = tmp_path / "result.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_write_baseline_com_dict_e_timestamp(tmp_path: Path) -> None:
    """Payload dict com results.timestamp gera generated_at nao-null."""
    from scripts.quality_gate import _write_baseline

    src = _make_promptfoo_json(
        {
            "results": {
                "results": [{"pass": True, "testCase": {"description": "A"}}],
                "timestamp": "2026-10-13T17:56:04.501Z",
            }
        },
        tmp_path,
    )
    dst = tmp_path / "baseline.json"
    _write_baseline(src, dst)
    loaded = json.loads(dst.read_text())
    assert loaded["generated_at"] == "2026-10-13T17:56:04.501Z"


def test_write_baseline_com_dict_sem_timestamp_grava_null(tmp_path: Path) -> None:
    """Payload dict sem timestamp gera generated_at null — o gate reprova."""
    from scripts.quality_gate import _write_baseline

    src = _make_promptfoo_json(
        {"results": {"results": [{"pass": True, "testCase": {"description": "A"}}]}},
        tmp_path,
    )
    dst = tmp_path / "baseline.json"
    _write_baseline(src, dst)
    loaded = json.loads(dst.read_text())
    assert loaded["generated_at"] is None


def test_write_baseline_com_lista_nao_quebra(tmp_path: Path) -> None:
    """Payload lista (formato alternativo do promptfoo) nao causa AttributeError."""
    from scripts.quality_gate import _write_baseline

    src = _make_promptfoo_json(
        [
            {"pass": True, "testCase": {"description": "A"}},
            {"pass": False, "testCase": {"description": "B"}},
        ],
        tmp_path,
    )
    dst = tmp_path / "baseline.json"
    # Antes do fix: AttributeError: 'list' object has no attribute 'get'
    _write_baseline(src, dst)
    loaded = json.loads(dst.read_text())
    assert loaded["cases"] == {"A": True, "B": False}


def test_write_baseline_grava_model_quando_fornecido(tmp_path: Path) -> None:
    """O argumento --baseline-model e' gravado no baseline."""
    from scripts.quality_gate import _write_baseline

    src = _make_promptfoo_json(
        {"results": {"results": [{"pass": True, "testCase": {"description": "A"}}]}},
        tmp_path,
    )
    dst = tmp_path / "baseline.json"
    _write_baseline(src, dst, model="gpt-4o-mini")
    loaded = json.loads(dst.read_text())
    assert loaded["model"] == "gpt-4o-mini"

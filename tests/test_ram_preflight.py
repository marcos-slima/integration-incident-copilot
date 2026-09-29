"""Testes do preflight de RAM (DA-51: invariante verificado por maquina).

O bug que motivou o modulo: a aritmetica vivia num heredoc do
scripts/promptfoo_remote.sh, sem nenhuma cobertura. Ela somava o modelo-alvo
duas vezes (uma via /api/ps, outra via /api/tags), o que reprovava o
preflight em retakes e em resumes de suite -- e a dica de descarga mandava
derrubar o proprio modelo que o caller queria usar.
"""

from __future__ import annotations

from app.evaluation.ram_preflight import (
    EXIT_BLOCKED,
    EXIT_OK,
    EXIT_PREFLIGHT_OK,
    GIB,
    required_bytes,
    resident_map,
    unload_hint,
)

GIB_48 = 48 * GIB
GIB_08 = int(0.3 * GIB)


class TestRequiredBytes:
    def test_alvo_ja_resident_nao_e_contado_duas_vezes(self):
        resident = {"qwen3-strict:latest": 49 * GIB_48 // 48}
        total = required_bytes(resident, "qwen3-strict:latest", GIB_48, 8 * GIB)
        assert total == sum(resident.values()) + 8 * GIB

    def test_alvo_nao_resident_entra_uma_vez(self):
        resident = {"nomic-embed-text:latest": GIB_08}
        total = required_bytes(resident, "qwen3-coder-next:latest", GIB_48, 8 * GIB)
        assert total == GIB_08 + GIB_48 + 8 * GIB

    def test_reserva_e_sempre_some_por_fora(self):
        assert required_bytes({}, "x", GIB_48, 12 * GIB) == GIB_48 + 12 * GIB

    def test_sem_resident_e_sem_alvo_so_a_reserva(self):
        assert required_bytes({}, "x", 0, 8 * GIB) == 8 * GIB

    def test_o_bug_da_dupla_contagem_seria_apanhado(self):
        """A diferenca observavel: 48.2G de erro numa maquina de 96G.

        Uma maquina com folga de 2G passa com a conta certa e reprova com a
        errada. E' esse o cenario que reprovava retakes na pratica.
        """
        resident = {"alvo": 48 * GIB}
        reserve = 2 * GIB
        available = 50 * GIB
        assert required_bytes(resident, "alvo", 48 * GIB, reserve) <= available
        # Reproduzindo a conta antiga: resident + alvo + reserva.
        assert sum(resident.values()) + 48 * GIB + reserve > available


class TestResidentMap:
    def test_tamanho_do_ps_vence_o_do_tags(self):
        models = [{"name": "qwen3-strict:latest", "size": 49 * GIB_48 // 48}]
        tags = {"qwen3-strict:latest": {"size": 48 * GIB}}
        assert resident_map(models, tags) == {"qwen3-strict:latest": 49 * GIB_48 // 48}

    def test_fallback_para_tags_quando_ps_nao_traz_size(self):
        models = [{"name": "antigo"}]
        tags = {"antigo": {"size": 3 * GIB}}
        assert resident_map(models, tags) == {"antigo": 3 * GIB}

    def test_sem_size_nem_tag_cai_em_zero(self):
        assert resident_map([{"name": "desconhecido"}], {}) == {"desconhecido": 0}

    def test_lista_vazia(self):
        assert resident_map([], {}) == {}


class TestUnloadHint:
    def test_aponta_o_maior_entre_os_outros(self):
        resident = {"pequeno": 1, "grande": 50, "alvo": 60}
        assert unload_hint(resident, "alvo") == "ollama stop grande"

    def test_nunca_sugere_descarregar_o_alvo(self):
        resident = {"alvo": 60}
        assert "ollama stop alvo" not in unload_hint(resident, "alvo")

    def test_sem_outros_aponta_reserva_e_contexto(self):
        hint = unload_hint({"alvo": 60}, "alvo")
        assert "EVAL_KV_RESERVE_GIB" in hint
        assert "OLLAMA_CONTEXT_LENGTH" in hint

    def test_nada_resident(self):
        assert "EVAL_KV_RESERVE_GIB" in unload_hint({}, "alvo")


class TestExitCodes:
    def test_contrato_dos_codigos_de_saida(self):
        assert (EXIT_OK, EXIT_BLOCKED, EXIT_PREFLIGHT_OK) == (0, 4, 5)

    def test_preflight_only_aprovado_nao_vira_o_ok_normal(self):
        # Se preflight-only devolvesse 0, o shell seguiria para a chamada de
        # LLM -- que e' o carregamento de 48G que o modo existe para evitar.
        assert EXIT_PREFLIGHT_OK != EXIT_OK

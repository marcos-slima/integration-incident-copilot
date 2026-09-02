"""Task manager A2A - traduz o ciclo de vida de task do protocolo A2A
para chamadas internas ao grafo LangGraph ja existente (`run_diagnosis`),
sem duplicar nenhuma logica de diagnostico (o FastAPI `/diagnose` e o
endpoint A2A chamam exatamente a mesma funcao).

Simplificacao deliberada em relacao ao conjunto completo de estados do
protocolo A2A (que inclui tambem `input_required`, `auth_required`,
`canceled`, `rejected`): este Copilot executa o diagnostico de forma
sincrona e autocontida (nao pede dado adicional a meio do processo, nao
tem fluxo de autorizacao interativo), entao so os 4 estados realmente
alcancaveis por esse tipo de agente sao implementados:

    submitted -> working -> completed | failed

Isso e exatamente o escopo que a proposta original (ver
docs/proposals/a2a-interoperability-layer.md) definiu como criterio de
aceite - implementar os estados que o protocolo suporta mas que este
agente nunca vai de fato atingir seria "preciosismo" sem valor real.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from app.agent.graph import run_diagnosis
from app.models import DiagnosisResponse, IncidentRequest

TERMINAL_STATES = {"completed", "failed"}


@dataclass
class A2ATask:
    id: str
    state: str = "submitted"
    input_description: str = ""
    result: DiagnosisResponse | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        payload: dict = {
            "id": self.id,
            "status": {"state": self.state},
        }
        if self.result is not None:
            payload["artifacts"] = [
                {
                    "name": "diagnosis-report",
                    "parts": [{"kind": "text", "text": self.result.report_markdown}],
                }
            ]
            payload["metadata"] = {
                "probable_root_cause": self.result.probable_root_cause,
                "confidence": self.result.confidence,
                "matched_source": self.result.matched_source,
            }
        if self.error is not None:
            payload["error"] = self.error
        return payload


def _extract_incident_request(message: dict) -> IncidentRequest:
    """Message A2A -> IncidentRequest interno.

    Suporta Part de texto (`{"kind": "text", "text": "..."}`), que vira
    `description`, e um Part de dados opcional
    (`{"kind": "data", "data": {...}}`) com campos estruturados
    (interface_type, identifier, logs, payload) - permite tanto um
    agente externo mandar so texto livre quanto um cliente mais
    estruturado mandar os mesmos campos do `/diagnose` REST.
    """
    parts = message.get("parts", [])
    text_parts = [p.get("text", "") for p in parts if p.get("kind") == "text" and p.get("text")]
    data_parts = [p.get("data", {}) for p in parts if p.get("kind") == "data"]

    description = "\n".join(text_parts).strip()
    structured: dict = {}
    for data in data_parts:
        structured.update(data)

    if not description:
        description = structured.pop("description", "")

    return IncidentRequest(
        description=description,
        logs=structured.get("logs"),
        payload=structured.get("payload"),
        interface_type=structured.get("interface_type"),
        identifier=structured.get("identifier"),
    )


class TaskManager:
    """`diagnosis_fn` e injetavel (default `run_diagnosis`) para os
    testes poderem substituir por um stub rapido, sem depender de um
    LLM real no ar - mesmo padrao de injecao de dependencia usado nos
    conectores HTTP (`client: httpx.Client | None`)."""

    def __init__(self, diagnosis_fn: Callable[[IncidentRequest], DiagnosisResponse] | None = None):
        self._diagnosis_fn = diagnosis_fn or run_diagnosis
        self._tasks: dict[str, A2ATask] = {}

    def handle_message(self, message: dict) -> A2ATask:
        task = A2ATask(id=str(uuid4()))
        self._tasks[task.id] = task

        try:
            request = _extract_incident_request(message)
        except Exception as exc:  # noqa: BLE001 - erro de validacao vira task failed, nao 500
            task.state = "failed"
            task.error = f"Mensagem invalida: {exc}"
            return task

        task.state = "working"
        task.input_description = request.description
        try:
            task.result = self._diagnosis_fn(request)
            task.state = "completed"
        except Exception as exc:  # noqa: BLE001 - task failed e o resultado A2A esperado, nao 500
            task.state = "failed"
            task.error = str(exc)

        return task

    def get_task(self, task_id: str) -> A2ATask | None:
        return self._tasks.get(task_id)


_default_manager = TaskManager()


def get_default_task_manager() -> TaskManager:
    return _default_manager

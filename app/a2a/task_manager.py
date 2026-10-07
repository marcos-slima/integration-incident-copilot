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
docs/a2a-interoperability-layer.md) definiu como criterio de
aceite - implementar os estados que o protocolo suporta mas que este
agente nunca vai de fato atingir seria "preciosismo" sem valor real.

Avaliacao externa (medio prazo, item 2): as tasks eram guardadas num
dict em memoria (`self._tasks`), perdido a cada restart do processo -
"persistencia de tasks A2A" era so uma frase no README, nao codigo. A
partir desta mudanca, o armazenamento fica atras de `TaskStore`
(app/a2a/task_store.py), com Redis opcional (REDIS_URL) e o mesmo dict
em memoria como fallback default - nada muda no comportamento default
("clone e rode" sem infra obrigatoria).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError

from app.a2a.task_store import TaskStore, get_default_task_store
from app.agent.graph import run_diagnosis
from app.models import DiagnosisResponse, IncidentRequest

TERMINAL_STATES = {"completed", "failed"}

logger = logging.getLogger(__name__)

# Campos estruturados aceitos no Part de dados (os mesmos do /diagnose REST).
# Validacao 2026-10-07 (M-28): connector_source_system, sensitivity_level,
# pii_detected e redaction_applied eram descartados - a correlacao DA-50 e a
# classificacao declarada (GOV-01) nao chegavam ao pipeline pelo A2A.
_STRUCTURED_FIELDS = (
    "logs",
    "payload",
    "interface_type",
    "identifier",
    "connector_source_system",
    "sensitivity_level",
    "pii_detected",
    "redaction_applied",
)


@dataclass
class A2ATask:
    id: str
    state: str = "submitted"
    input_description: str = ""
    result: DiagnosisResponse | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        payload: dict = {
            # DA-31: kind e contextId sao obrigatorios pelo spec A2A 0.3.
            # contextId reutiliza o proprio task id como correlation id
            # (este agente nao tem sessao de conversacao multi-turno, entao
            # task id == context id e o mapeamento correto aqui).
            "kind": "task",
            "id": self.id,
            "contextId": self.id,
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
                "model_confidence": self.result.model_confidence,
                "diagnosis_confidence": self.result.diagnosis_confidence,
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
        **{k: structured[k] for k in _STRUCTURED_FIELDS if k in structured},
    )


def _validation_summary(exc: Exception) -> str:
    """Erro de validacao sem ecoar o valor recebido (mesma regra do 422, M-15)."""
    if isinstance(exc, ValidationError):
        campos = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        return f"Mensagem invalida: campo(s) {', '.join(campos)}"
    return "Mensagem invalida"


class TaskManager:
    """`diagnosis_fn` e injetavel (default `run_diagnosis`) para os
    testes poderem substituir por um stub rapido, sem depender de um
    LLM real no ar - mesmo padrao de injecao de dependencia usado nos
    conectores HTTP (`client: httpx.Client | None`)."""

    def __init__(
        self,
        diagnosis_fn: Callable[[IncidentRequest], DiagnosisResponse] | None = None,
        task_store: TaskStore | None = None,
    ):
        self._diagnosis_fn = diagnosis_fn or run_diagnosis
        self._store = task_store if task_store is not None else get_default_task_store()
        self._background: set[asyncio.Task] = set()

    async def handle_message(self, message: dict, *, blocking: bool = True) -> A2ATask:
        """`blocking=False` (MessageSendConfiguration.blocking do A2A 0.3):
        devolve a task em `working` na hora e o diagnostico segue em
        background - o cliente consulta com `tasks/get`. Validacao 2026-10-07
        (M-28): antes so havia o modo sincrono, com a conexao do agente
        cliente presa ate 180 s. O default continua bloqueante para nao
        quebrar clientes existentes."""
        task = A2ATask(id=str(uuid4()))
        self._store.set(task)

        try:
            request = _extract_incident_request(message)
        except Exception as exc:  # noqa: BLE001 - erro de validacao vira task failed, nao 500
            task.state = "failed"
            task.error = _validation_summary(exc)
            self._store.set(task)
            return task

        task.state = "working"
        task.input_description = request.description
        self._store.set(task)
        if not blocking:
            job = asyncio.create_task(self._run(task, request))
            # Referencia forte ate terminar: o event loop so guarda weakref.
            self._background.add(job)
            job.add_done_callback(self._background.discard)
            return task
        return await self._run(task, request)

    async def _run(self, task: A2ATask, request: IncidentRequest) -> A2ATask:
        try:
            # DA-33: run_diagnosis e um pipeline LangGraph sincrono — chamado
            # diretamente num async def bloquearia o event loop do FastAPI
            # durante toda a execucao do LLM (potencialmente varios segundos).
            # asyncio.to_thread delega para o ThreadPoolExecutor default do
            # loop, liberando o event loop para servir outras requisicoes
            # enquanto o diagnostico roda em background thread.
            task.result = await asyncio.to_thread(self._diagnosis_fn, request)
            task.state = "completed"
        except Exception:
            # M-28: str(exc) ia para o agente EXTERNO (caminhos, hosts, SQL,
            # mensagens de provider). Fica so o error_id; o detalhe vai ao log.
            error_id = uuid4().hex
            logger.exception("[a2a] diagnostico falhou task=%s error_id=%s", task.id, error_id)
            task.state = "failed"
            task.error = f"falha interna ao diagnosticar (error_id={error_id})"

        self._store.set(task)
        return task

    def get_task(self, task_id: str) -> A2ATask | None:
        return self._store.get(task_id)


_default_manager = TaskManager()


def get_default_task_manager() -> TaskManager:
    return _default_manager

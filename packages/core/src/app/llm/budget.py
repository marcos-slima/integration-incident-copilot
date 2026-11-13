"""DA-67: orcamento por TAREFA (diagnostico), nao so por chamada.

Antes, `invoke_via_gateway` comparava o custo estimado de CADA chamada
com o teto fixo `llm_gateway_max_cost_usd` - um diagnostico com varias
chamadas (ReAct, retry, failover) nunca acumulava consumo. Este modulo
mantem um ledger em memoria por diagnostico:

- `run_diagnosis` abre o orcamento (`open_task_budget`) com
  `settings.diagnosis_budget_usd` e fecha no fim (`close_task_budget`).
- O gateway consulta `affordable_usd()` antes de rotear: o candidato so
  e elegivel se `custo_estimado * (1 + llm_budget_reserve_ratio)` couber
  no restante. A reserva cobre o erro da estimativa (tokenizer
  aproximado, saida maior que o previsto) e uma retentativa.
- Depois de uma chamada bem-sucedida o gateway debita o custo REAL
  (metering DA-48) ou, sem ele, o estimado (`charge`).

A chave e o `incident_id`, que ja esta no estado do grafo - nao depende
de contextvar (o grafo roda num ThreadPoolExecutor, que nao propaga
contexto) nem guarda objeto nao serializavel no estado (o checkpointer
da DA-69 serializa o estado).

Limites declarados:
- Em memoria, por processo: com varias replicas cada uma ve so as
  chamadas que ela mesma fez para aquele incidente. O incidente roda
  inteiro numa replica, entao na pratica isso basta.
- Sem orcamento aberto (chamada fora de `run_diagnosis`), vale so o
  teto por chamada - mesmo comportamento de antes desta DA.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from app.exceptions import ConfigurationError


class BudgetExhaustedError(ConfigurationError):
    """O orcamento restante do diagnostico nao cobre nenhum candidato.

    Mapeia para FailureClass.BUDGET_EXHAUSTED (DA-68): nao retry,
    nao failover."""


@dataclass
class TaskBudget:
    task_id: str
    total_usd: float
    spent_usd: float = 0.0
    calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.total_usd - self.spent_usd)

    def affordable_usd(self, reserve_ratio: float) -> float:
        """Maior custo ESTIMADO que ainda cabe, ja descontada a reserva."""
        return self.remaining_usd / (1.0 + max(0.0, reserve_ratio))

    def charge(self, cost_usd: float) -> None:
        if cost_usd < 0:
            raise ValueError("custo negativo")
        with self._lock:
            self.spent_usd += cost_usd
            self.calls += 1

    def as_log_fields(self) -> dict:
        return {
            "task_id": self.task_id,
            "budget_total_usd": round(self.total_usd, 6),
            "budget_spent_usd": round(self.spent_usd, 6),
            "budget_remaining_usd": round(self.remaining_usd, 6),
            "budget_calls": self.calls,
        }


_budgets: dict[str, TaskBudget] = {}
_registry_lock = threading.Lock()


def open_task_budget(task_id: str, total_usd: float) -> TaskBudget:
    budget = TaskBudget(task_id=task_id, total_usd=max(0.0, total_usd))
    with _registry_lock:
        _budgets[task_id] = budget
    return budget


def get_task_budget(task_id: str | None) -> TaskBudget | None:
    if not task_id:
        return None
    with _registry_lock:
        return _budgets.get(task_id)


def close_task_budget(task_id: str) -> TaskBudget | None:
    with _registry_lock:
        return _budgets.pop(task_id, None)

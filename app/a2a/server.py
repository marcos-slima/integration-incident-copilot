"""Servidor A2A (JSON-RPC 2.0) - endpoint HTTP que coexiste com o
`/diagnose` do FastAPI, ambos chamando a mesma orquestracao
(`run_diagnosis`) por tras. Ver docs/proposals/a2a-interoperability-layer.md
para o contexto/criterio de aceite original.

Metodos implementados (subconjunto deliberado do protocolo A2A -
suficiente para o criterio de aceite da proposta, sem reimplementar
streaming/push notifications que este agente sincrono nao precisa):

  message/send  - envia uma mensagem, executa o diagnostico (sincrono)
                  e retorna a task ja em estado terminal
  tasks/get     - consulta uma task pelo id (util para clientes que
                  preferem o padrao poll, mesmo a execucao sendo
                  sincrona aqui)

Autenticacao: header `X-A2A-Api-Key` comparado a `settings.a2a_api_key`
quando esta configurado (vazio = autenticacao desabilitada, aceitavel
para portfolio/demo local - ver Agent Card / proposta original para o
gap de producao documentado: producao exigiria OAuth2/JWT entre
agentes, nao uma chave estatica).
"""

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.a2a.task_manager import TaskManager, get_default_task_manager
from app.config import settings

router = APIRouter()


def _jsonrpc_error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _jsonrpc_result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _check_auth(x_a2a_api_key: str | None) -> bool:
    if not settings.a2a_api_key:
        return True
    return x_a2a_api_key == settings.a2a_api_key


async def handle_jsonrpc(
    request: Request,
    task_manager: TaskManager,
    x_a2a_api_key: str | None,
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(_jsonrpc_error(None, -32700, "Parse error: corpo nao e JSON valido"))

    request_id = body.get("id")

    if not _check_auth(x_a2a_api_key):
        return JSONResponse(
            _jsonrpc_error(request_id, -32000, "Unauthorized: X-A2A-Api-Key ausente ou invalido"),
            status_code=401,
        )

    method = body.get("method")
    params = body.get("params") or {}

    if method == "message/send":
        message = params.get("message")
        if not isinstance(message, dict):
            return JSONResponse(
                _jsonrpc_error(request_id, -32602, "Invalid params: 'message' e obrigatorio")
            )
        task = task_manager.handle_message(message)
        return JSONResponse(_jsonrpc_result(request_id, task.to_dict()))

    if method == "tasks/get":
        task_id = params.get("id")
        task = task_manager.get_task(task_id) if task_id else None
        if task is None:
            return JSONResponse(
                _jsonrpc_error(request_id, -32001, f"Task nao encontrada: {task_id!r}")
            )
        return JSONResponse(_jsonrpc_result(request_id, task.to_dict()))

    return JSONResponse(_jsonrpc_error(request_id, -32601, f"Metodo desconhecido: {method!r}"))


@router.post("/a2a")
async def a2a_endpoint(
    request: Request,
    x_a2a_api_key: str | None = Header(default=None),
) -> JSONResponse:
    return await handle_jsonrpc(request, get_default_task_manager(), x_a2a_api_key)

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
via secrets.compare_digest. Se A2A_API_KEY nao foi configurada no
.env, app.main._ensure_api_keys_configured gera uma chave aleatoria no
startup e avisa no log (DA-18) - o endpoint nunca fica sem NENHUMA
chave. Gap de producao que permanece, documentado: uma chave estatica
compartilhada nao substitui OAuth2/JWT por-agente entre pares reais.
"""

import secrets

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.a2a.task_manager import TaskManager, get_default_task_manager
from app.config import settings
from app.rate_limit import limiter

router = APIRouter()


def _jsonrpc_error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _jsonrpc_result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _check_auth(x_a2a_api_key: str | None) -> bool:
    """DA-18: settings.a2a_api_key nunca fica vazio apos o startup
    (ver app.main._ensure_api_keys_configured) - o "if not
    settings.a2a_api_key: return True" (auth desabilitada) foi
    removido de proposito, nao e mais um caminho alcancavel em
    execucao normal. secrets.compare_digest evita timing attack."""
    return secrets.compare_digest(x_a2a_api_key or "", settings.a2a_api_key)


async def handle_jsonrpc(
    request: Request,
    task_manager: TaskManager,
    x_a2a_api_key: str | None,
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(_jsonrpc_error(None, -32700, "Parse error: corpo nao e JSON valido"))

    # DA-32: JSON valido mas nao e um objeto/dict (ex: array, numero) —
    # request.json() aceita qualquer JSON, mas JSON-RPC 2.0 exige objeto.
    # Sem essa checagem body.get("id") lancaría AttributeError.
    if not isinstance(body, dict):
        return JSONResponse(
            _jsonrpc_error(None, -32600, "Invalid Request: corpo deve ser um objeto JSON")
        )

    # DA-32: "jsonrpc" == "2.0" e obrigatorio pelo spec JSON-RPC 2.0.
    if body.get("jsonrpc") != "2.0":
        return JSONResponse(
            _jsonrpc_error(
                body.get("id"),
                -32600,
                "Invalid Request: campo 'jsonrpc' deve ser '2.0'",
            )
        )

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
        task = await task_manager.handle_message(message)
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
# Avaliacao externa (medio prazo, item 1): decorator explicito, alem do
# default_limits global do SlowAPIMiddleware (app/main.py) - mesmo
# tratamento que /diagnose ja recebia, agora tambem no endpoint A2A
# (agente-para-agente), que antes ficava sem NENHUM rate limit.
@limiter.limit("10/minute")
async def a2a_endpoint(
    request: Request,
    x_a2a_api_key: str | None = Header(default=None),
) -> JSONResponse:
    return await handle_jsonrpc(request, get_default_task_manager(), x_a2a_api_key)

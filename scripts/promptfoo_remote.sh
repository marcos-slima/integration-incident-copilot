#!/bin/bash
# Provider exec do promptfoo: roda o pipeline REAL do Copilot (RAG +
# conectores + guardrails) mudando SO o modelo de linguagem.
#
# stdout = a resposta do promptfoo (JSON, lido por promptfoo_provider.py)
# stderr = diagnostico (o promptfoo trata stdout como a resposta, entao
#          NADA de diagnostico pode ir para ca)
#
# Credenciais e nomes de modelo vem do ambiente - nunca deste arquivo:
#   GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL (opcional)
#   GROQ_API_KEY,   GROQ_MODEL,   GROQ_BASE_URL   (opcional)
#   OLLAMA_MODEL,   OLLAMA_HOST
set -euo pipefail

PROVIDER="${1:-}"
if [ -z "$PROVIDER" ]; then
    echo "Uso: $0 <OLLAMA|GEMINI|GROQ> [prompt]" >&2
    exit 2
fi
shift

# O promptfoo passa o prompt como $1 e anexa o config do provider e as vars
# do teste como argumentos extras ($2, $3) - descartados de proposito.
PROMPT="${1:-}"
if [ -z "$PROMPT" ]; then
    PROMPT="$(cat)"
fi

# Endpoint OpenAI-compatible de cada provedor. Variavel de ambiente vence,
# para nao precisar editar este arquivo se o endpoint mudar.
case "$PROVIDER" in
    GEMINI)
        LLM_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
        API_KEY="${GEMINI_API_KEY:-}"
        BASE_URL="${GEMINI_BASE_URL:-https://generativelanguage.googleapis.com/v1beta/openai/}"
        ;;
    GROQ)
        LLM_MODEL="${GROQ_MODEL:-openai/gpt-oss-120b}"
        API_KEY="${GROQ_API_KEY:-}"
        BASE_URL="${GROQ_BASE_URL:-https://api.groq.com/openai/v1}"
        ;;
    OLLAMA)
        LLM_MODEL="${OLLAMA_MODEL:-qwen3-coder-next:latest}"
        API_KEY=""
        BASE_URL=""
        ;;
    *)
        echo "Provedor invalido: '$PROVIDER' (use OLLAMA, GEMINI ou GROQ)" >&2
        exit 2
        ;;
esac

# O app le provider/chave/base_url de Settings, que resolve env var ANTES
# do .env. Sem o export abaixo, run_diagnosis(llm_model="gemini-...") manda
# o nome do modelo cloud pro transporte do OLLAMA (LLM_PROVIDER=ollama vem
# do .env) e o provider 404: model 'gemini-2.5-flash' not found.
if [ "$PROVIDER" = "OLLAMA" ]; then
    export LLM_PROVIDER=ollama
else
    if [ -z "$API_KEY" ]; then
        echo "Chave ausente: defina ${PROVIDER}_API_KEY no ambiente" >&2
        exit 3
    fi
    export LLM_PROVIDER=openai
    export OPENAI_API_KEY="$API_KEY"
    export OPENAI_BASE_URL="$BASE_URL"
    # Sem fallback: se o cloud cair e o app cair para o Ollama, o
    # comparativo mede o modelo local duas vezes e a tabela mente.
    export LLM_FALLBACK_PROVIDER=""
    # DA-26 (AI Gateway): por padrao o classificador marca o incidente
    # como 'confidential' e a POLICY BLOQUEIA rotear dado confidencial
    # para cloud - certo em producao, e o motivo de o comparativo
    # falhar com PolicyViolationError antes de qualquer chamada.
    #
    # Os 7 casos deste arquivo sao sinteticos (NAO sao dado de cliente),
    # entao rebaixar a sensibilidade aqui e legitimo. O override e
    # exportado SÓ NESTE PROCESSO e NUNCA escrito no .env de proposito:
    # assim a policy de producao continua 'confidential' por default e
    # nao existe arquivo no repo que possa vazar esse relaxation para um
    # servico no ar. Se algum dia apontar este eval para dado real, apague
    # esta linha - e o caso vai reprovar, que e o comportamento correto.
    export SENSITIVITY_DEFAULT="${EVAL_SENSITIVITY_DEFAULT:-public}"

    # Compatibilidade -- REMOVIDO HARDCODE: `seed` nao faz parte do
    # contrato minimo da API OpenAI, e quem implementa so o subconjunto
    # de tool calling rejeita o campo com 400 e sem fallback. Medido aqui:
    # o endpoint OpenAI-compatible do Gemini respondeu 'Invalid JSON
    # payload received. Unknown name "seed": Cannot find field'.
    #
    # DA-45: essa decisao e' da TABELA de capacidades por ORIGIN
    # (app/llm/capabilities.py), nao deste script. Se o script ainda
    # exportasse LLM_SEND_SEED=false, o comparativo validaria o override
    # manual e NAO o mecanismo que a DA entregou - um caso que o modelo
    # resolveria por origin seria mascarado por processo. A origin
    # normalizada de OPENAI_BASE_URL acima e'
    # 'https://generativelanguage.googleapis.com', e a tabela ja a registra
    # como supports_seed=False. Se outro provider reclamar de `seed`,
    # acrescente a origin dele na tabela - NAO reponha o export aqui.
fi

echo "[promptfoo] provider=$PROVIDER model=$LLM_MODEL base_url=${BASE_URL:-<ollama>}" >&2

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/promptfoo_provider.py" "$LLM_MODEL" "$PROMPT"

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
#   OLLAMA_MODEL,          (OLLAMA,        default qwen3-coder-next:latest)
#   OLLAMA_STRICT_MODEL,   (OLLAMA_STRICT, default qwen3-strict:latest)
#   OLLAMA_HOST
set -euo pipefail

PROVIDER="${1:-}"
if [ -z "$PROVIDER" ]; then
    echo "Uso: $0 <OLLAMA|OLLAMA_STRICT|GEMINI|GROQ> [prompt]" >&2
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
    OLLAMA_STRICT)
        LLM_MODEL="${OLLAMA_STRICT_MODEL:-qwen3-strict:latest}"
        API_KEY=""
        BASE_URL=""
        ;;
    *)
        echo "Provedor invalido: '$PROVIDER' (use OLLAMA, OLLAMA_STRICT, GEMINI ou GROQ)" >&2
        exit 2
        ;;
esac

# O app le provider/chave/base_url de Settings, que resolve env var ANTES
# do .env. Sem o export abaixo, run_diagnosis(llm_model="gemini-...") manda
# o nome do modelo cloud pro transporte do OLLAMA (LLM_PROVIDER=ollama vem
# do .env) e o provider 404: model 'gemini-2.5-flash' not found.
case "$PROVIDER" in
    OLLAMA|OLLAMA_STRICT)
        export LLM_PROVIDER=ollama

        # POR QUE ISTO EXISTE. Os dois modelos locais de 79.7B ocupam
        # ~48.2G de pesos cada. A maquina tem 115G de RAM e ~91G
        # disponiveis: dois resident = 96.4G > 91G, entao o segundo NAO
        # entra. Pior: o servico roda com OLLAMA_MAX_LOADED_MODELS=2 e
        # OLLAMA_KEEP_ALIVE=30m, ou seja, o Ollama nao despeja o primeiro
        # para dar lugar ao segundo - ele TENTA manter os dois.
        #
        # Sem esta guarda o que acontece e o runner ser morto por OOM no
        # MEIO da suite. O promptfoo ve o provider falhar em 4 de 7 casos
        # e registra "o modelo novo e pior" - conclusao errada que
        # sobrevive no report. Falhar aqui, dizendo o que descarregar, e'
        # o unico jeito de o numero significar algo.
        #
        # A conta e' pesos + reserva para o KV cache. O /api/tags devolve
        # so o tamanho do arquivo; o servico roda com
        # OLLAMA_CONTEXT_LENGTH=65536 e o KV cache (q8_0) entra por cima.
        # Entao este e' um LIMITE INFERIOR de proposito - a reserva e'
        # margem, nao medicao. Medicao real exige carregar o modelo e ler
        # `ollama ps`, o que nao cabe num preflight de 3s.
        #
        # Nao bloqueia a suite por duvida: se nao der para medir (sem
        # /proc/meminfo, Ollama remoto sem /api/ps, python ausente), o
        # preflight sai 0 em silencio e a suite roda. Medicao de
        # infraestrutura nao pode ser a razao de um eval nao rodar.
        #
        # Diagnostico vai por stderr: stdout aqui e' a resposta do
        # promptfoo. Saida 4 = bloqueio deliberado, que o promptfoo
        # registra como provider quebrado - desfecho correto para
        # "nao deu para medir".
        REPO_ROOT_RAM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
        RAM_RC=0
        # `EVAL_RAM_PREFLIGHT_ONLY=1` roda so a conta e sai, sem carregar
        # modelo nem chamar LLM - e' o que torna o preflight testavel sem
        # pagar 48G de RAM para descobrir que ele passou.
        PREFLIGHT_MODE=()
        if [ "${EVAL_RAM_PREFLIGHT_ONLY:-0}" = "1" ]; then
            PREFLIGHT_MODE=(preflight-only)
        fi
        "$REPO_ROOT_RAM/.venv/bin/python" - \
            "$LLM_MODEL" "${EVAL_KV_RESERVE_GIB:-8}" "${PREFLIGHT_MODE[@]}" <<'PYEOF' || RAM_RC=$?
import json
import os
import sys
import urllib.request

target, reserve_gib = sys.argv[1], float(sys.argv[2])
preflight_only = len(sys.argv) > 3 and sys.argv[3] == "preflight-only"
host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if "://" not in host:
    host = "http://" + host


def get(path):
    with urllib.request.urlopen(f"{host}{path}", timeout=3) as response:
        return json.load(response)


def say(msg):
    print(f"[ram] {msg}", file=sys.stderr)


try:
    resident = get("/api/ps").get("models", [])
    tags = {m["name"]: m for m in get("/api/tags").get("models", [])}
    with open("/proc/meminfo") as fh:
        available = next(
            int(line.split()[1]) * 1024 for line in fh if line.startswith("MemAvailable:")
        )
except Exception:
    # Nao mediu, entao nao tem opiniao: deixa a suite rodar.
    sys.exit(0)

need = tags.get(target, {}).get("size")
if need is None:
    say(f"'{target}' nao esta instalado neste Ollama - o provider vai 404")
    sys.exit(4)

# Para quem JA esta resident, vale o size do /api/ps, nao o do /api/tags:
# medido 2026-09-28, /api/ps = 49.6G contra /api/tags = 48.2G para o mesmo
# qwen3-strict. A diferenca e' o buffer de compute, que so existe em RAM
# quando o modelo esta de pe - contar o arquivo subestima o occupant real.
resident_by_name = {m["name"]: (m.get("size") or 0) for m in resident}
loaded = sum(size or tags.get(name, {}).get("size", 0) for name, size in resident_by_name.items())
reserve = int(reserve_gib * 2**30)
if resident:
    names = ", ".join(sorted(resident_by_name))
    say(f"ja resident: {names} ({loaded / 2**30:.1f}G)")

# O alvo ja resident NAO e um peso a adicionar: ele esta em `loaded`. Sem
# esta correcao, um segundo run do MESMO provider (por exemplo o retake de
# um caso, ou o resume de uma suite interrompida) somava os 48.2G duas vezes,
# o preflight reprovava por conta propria e a dica mandava
# 'ollama stop' do modelo que o caller esta pedindo para usar.
if target in resident_by_name:
    need = 0
    need_note = f"{target} ja resident, conta dentro dos {loaded / 2**30:.1f}G acima"
else:
    need = need
    need_note = f"{target} = {need / 2**30:.1f}G"

total = loaded + need + reserve
say(f"disponivel {available / 2**30:.1f}G | precisa {total / 2**30:.1f}G ({need_note} + {reserve_gib:.0f}G de reserva p/ KV cache)")

if preflight_only:
    say("PREFLIGHT ONLY: nada foi carregado, nenhuma chamada de LLM feita")
    # 5 = aprovado em modo preflight-only. Precisa ser distinto de 0: o shell
    # so aborta em 4, entao um 0 aqui voltaria a script e dispararia a
    # chamada de LLM - que e' o carregamento de 48G que o modo quer evitar.
    sys.exit(4 if total > available else 5)

if total > available:
    # Aponta o MAIOR resident, nao o primeiro alfabeticamente: sugerir
    # 'ollama stop nomic-embed-text' (0.3G) quando o culpado tem 49.6G e'
    # uma dica que nao libera RAM nenhuma - o usuario obedece e o OOM
    # acontece do mesmo jeito, agora com a culpa no preflight.
    by_size = sorted(resident, key=lambda m: m.get("size") or 0, reverse=True)
    if target in resident_by_name:
        # Descartar o alvo da lista: e' o unico que o caller nao pode
        # descarregar sem matar o que ele esta tentando rodar.
        by_size = [m for m in by_size if m["name"] != target]
    if by_size:
        hint = f"ollama stop {by_size[0]['name']}"
    else:
        hint = (
            f"o alvo ({target}) ja esta resident e ainda assim nao cabe: "
            "reduza OLLAMA_CONTEXT_LENGTH ou reduza EVAL_KV_RESERVE_GIB"
        )
    say(f"PREFLIGHT FAIL: nao cabe em {available / 2**30:.1f}G. Descarregue antes: {hint}")
    sys.exit(4)
PYEOF
        # `|| RAM_RC=$?` em vez de `|| true`: o `|| true` mantem o script
        # vivo mas TAMBEM descarta o status 4, e o preflight voltaria a ser
        # um aviso que nao impede nada. A forma com atribuicao captura o
        # codigo sem acionar o `set -e`.
        #
        # Status diferente de 0 que nao seja 4 (ex.: python ausente, erro de
        # sintaxe) e' deixado passar de proposito: o script nao pode impedir
        # um eval de rodar por causa de falha do proprio preflight. O
        # resultado e' assimétrico na medida certa - a memoria insuficiente
        # bloqueia, a ferramenta quebrada nao.
        if [ "${RAM_RC:-0}" -eq 4 ] || [ "${RAM_RC:-0}" -eq 5 ]; then
            exit "$RAM_RC"
        fi
        ;;

    *)
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
        ;;
esac

echo "[promptfoo] provider=$PROVIDER model=$LLM_MODEL base_url=${BASE_URL:-<ollama>}" >&2

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/promptfoo_provider.py" "$LLM_MODEL" "$PROMPT"

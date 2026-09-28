#!/bin/bash
# Roda a suite comparativa (promptfooconfig.compare.yaml) em varios modelos
# Groq, um por execucao, e imprime o comando de merge da tabela.
# --------------------------------------------------------------------------
# Por que um por execucao: o provider `exec` do promptfoo NAO repassa
# config.env para o processo filho (verificado 2026-09-28 no source
# dist/src/providers-vvyLQL6h.js: as options do child so levam cwd via
# basePath). Colocar GROQ_MODEL por YAML criaria N providers rodando o
# MESMO modelo. Entao: GROQ_MODEL via export em cada execucao + merge dos
# outputs com `promptfoo view` (que aceita multiplos outputs).
#
# Modelos candidatos (so chat/coder - whisper/orpheus/prompt-guard/
# safeguard sao audio/guard, nao servem como LLM de diagnostico):
#   openai/gpt-oss-120b   grounding forte
#   openai/gpt-oss-20b    controle de tamanho/custo
#   qwen/qwen3.8-27b      ja 7/7 na suite (baseline)
#
# Uso:
#   export GROQ_API_KEY=...   # no seu shell - nunca neste arquivo
#   PATH="$PWD/.venv/bin:$PATH" scripts/promptfoo_groq_models.sh
#   scripts/promptfoo_groq_models.sh openai/gpt-oss-20b   # subconjunto
#
# Variaveis opcionais: PROMPTFOO_BIN (default "npx promptfoo"),
# DELAY_MS (default 12000), PROMPTFOO_OUT_DIR.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

: "${PROMPTFOO_BIN:=npx promptfoo}"
: "${DELAY_MS:=12000}"
OUT_DIR="${PROMPTFOO_OUT_DIR:-$REPO_ROOT/.promptfoo-results}"
mkdir -p "$OUT_DIR"

if [ -z "${GROQ_API_KEY:-}" ]; then
    echo "GROQ_API_KEY nao esta no ambiente - exporte antes de rodar." >&2
    exit 2
fi

MODELS=("$@")
if [ "${#MODELS[@]}" -eq 0 ]; then
    MODELS=(openai/gpt-oss-120b openai/gpt-oss-20b qwen/qwen3.8-27b)
fi

outputs=()
for m in "${MODELS[@]}"; do
    slug="$(echo "$m" | tr '/.' '__')"
    out="$OUT_DIR/groq-${slug}.json"
    echo "==> GROQ_MODEL=$m"
    GROQ_MODEL="$m" $PROMPTFOO_BIN eval \
        -c promptfooconfig.compare.yaml --no-cache -j 1 --delay "$DELAY_MS" \
        --filter-providers "Groq" --output "$out"
    outputs+=("$out")
done

echo
echo "Tabela combinada (merge dos outputs):"
echo "  $PROMPTFOO_BIN view ${outputs[*]}"

#!/bin/bash
# Script para execução direta de testes

# Verifica se o modelo foi passado como parâmetro
if [ -z "$1" ]; then
    echo "Uso: $0 <modelo> \"descricao|||interface|||identifier\""
    exit 1
fi

MODEL=$1
PROMPT=$2

# Executa diretamente o script Python com o modelo especificado
.venv/bin/python3 scripts/promptfoo_provider.py "$MODEL" "$PROMPT"

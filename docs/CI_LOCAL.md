# Pipeline de Integração Contínua Local — Guia Completo

> **Objetivo:** Garantir que nenhum código quebre os padrões de formatação, possua bugs evidentes ou vaze dados sensíveis antes de chegar ao repositório remoto.

---

## Visão Geral do Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Pipeline de Validação Local                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. ruff check      → Linting (código estático)                              │
│ 2. ruff format     → Formatação (PEP 8, black-style)                        │
│ 3. gitleaks detect → Scan de segredos no histórico                          │
│ 4. pre-commit      → Hooks do git (multi-tool)                              │
│ 5. push origin     → Push seguro para remoto                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. ruff check — Linting Estático

### Propósito
Identifica erros de código estático (syntax, logic, security issues) antes da execução.

### Comando
```bash
rm -f .ruff_cache && ruff check .
```

### Saída esperada (sucesso)
```
All checks passed! 🎉
```

### Exemplo de erro corrigido
```
app/main.py:45:12 [E703] Statement ends with a semicolon
    print("hello");  # ❌ Remove o ponto e vírgula
```

### Configuração
- Arquivo: `ruff.toml`
- Flags usadas no CI: `--select I001,E,F,B,C4` (imports, estilo, flake8, flake8-bugbear, Comprehensions)

---

## 2. ruff format — Formatação de Código

### Propósito
Garante formatação consistente (PEP 8, estilo Black) sem discussões de "gosto".

### Comando
```bash
ruff format .  # Aplica formatação automática
```

### Verificação (CI)
```bash
ruff format --check .  # Falha se houver desvio da formatação
```

### Exemplo de mudança automática
```python
# Input
x = {"a": 1, "b": 2}

# Output (ruff format)
x = {
    "a": 1,
    "b": 2,
}
```

---

## 3. gitleaks detect — Detecção de Segredos no Histórico

### Propósito
**Prevenir vazamento acidental de credenciais** (API keys, passwords, tokens) mesmo em commits antigos.

### Comando (repositório inteiro)
```bash
gitleaks detect --source . --verbose
```

### Comando (histórico completo - CI)
```bash
git fetch --unshallow  # Se necessário ( GitHub Actions usa fetch-depth:1 por padrão)
gitleaks detect --source . --verbose --no-git
```

### Saída esperada (sucesso)
```
No leaks found. Good job! 🚀
```

### Exemplo de falso positivo (esperado)
```
# Estas linhas NÃO acionam erro (estão no .gitleaks.toml whitelist)
LLM_PROVIDER=ollama
LLM_MODEL=qwen3.6:35b-a3b
```

### Exemplo de falso negativo (PROIBIDO)
```
# Isso aciona erro e rejeita o commit:
API_KEY="REDACTED_EXAMPLE"
```

### Configuração
- Arquivo: `.gitleaks.toml`
- Regras: Prioridade HIGH para `sk-`, `AKIA`, `ghp_`, `github_pat_`, etc.
- Whitelist: Variáveis de exemplo (ex: `EXAMPLE_API_KEY`)

---

## 4. pre-commit — Hooks do Git

### Propósito
Garante que cada commit passa por todos os checks antes de ser criado.

### Instalação
```bash
# No primeiro clone:
pipx install pre-commit
pre-commit install

# Ou (se usar uv):
uv run pre-commit install
```

### Execução manual (antes do commit)
```bash
pre-commit run --all-files
```

### Saída esperada (sucesso)
```
ruff (legacy alias)..................................Passed
ruff format..........................................Passed
detect hardcoded secrets...........................Passed
trim trailing whitespace...........................Passed
```

### Configuração
- Arquivo: `.pre-commit-config.yaml`
- Hooks incluídos:
  - `ruff` (linting)
  - `ruff-format` (formatação)
  - `gitleaks` (segredos)
  - `trim-trailing-whitespace` (limpeza visual)
  - `check-yaml`, `check-added-large-files`

---

## 5. push origin — Envio Seguro para Remoto

### Comando
```bash
git push origin feat/sprint-dlp-ner-da80
```

### O que o hook de push executa (se configurado)
```bash
# hook pre-push em .git/hooks/pre-push (opcional)
ruff check .
ruff format --check .
gitleaks detect --source . --no-git
```

### Saída esperada (sucesso)
```
To https://github.com/marcos-slima/integration-incident-copilot
   abc1234..def5678  feat/sprint-dlp-ner-da80 -> feat/sprint-dlp-ner-da80
```

---

## Pipeline Completo — Fluxo Recomendado

### Antes de cada commit
```bash
# 1. Linting
ruff check .

# 2. Formatação (aplica)
ruff format .

# 3. Verificação manual de segredos (se mudou código)
gitleaks detect --source . --no-git
```

### Antes de cada push
```bash
# 1. Hooks do pre-commit
pre-commit run --all-files

# 2. Push
git push origin feat/sprint-dlp-ner-da80
```

### No CI (GitHub Actions)
```yaml
# .github/workflows/tests.yml
- name: ruff — lint
  run: uv run ruff check .

- name: ruff — formatação
  run: uv run ruff format --check .

- name: gitleaks — scan de histórico
  run: |
    git fetch --unshallow
    gitleaks detect --source . --no-git
```

---

## Cenários de Falha e Resolução

| Erro | Causa | Solução |
|---|---|---|
| `E703 Statement ends with a semicolon` | Sintaxe anti-PEP8 | `ruff format .` |
| `B104 Hardcoded binding to all network interfaces` | `0.0.0.0` sem nosec | Adicionar `# nosec B104` ou usar `settings.host` |
| `gitleaks: sk-regex detected` | API key no código | Remover valor; usar `.env` ou placeholder |
| `trim trailing whitespace` | Espaços no final da linha | `ruff format .` |
| `cannot import name 'foo'` | Erro de sintaxe | Corrigir import/falta de `,` |

---

## Comandos de Diagnóstico

```bash
# Ver quais hooks estão instalados
pre-commit list

# Ignorar um commit temporariamente (não recomendado)
git commit --no-verify -m "WIP"

# Verificar se um arquivo está sendo ignorado
git check-ignore docs/secrets-local/

# Verificar segredos em um commit específico
git rev-list HEAD | xargs gitleaks detect --source
```

---

## Boas Práticas

1. **Executar ruff antes de cada save no editor** (muitos IDEs integram)
2. **Nunca usar `git add -f docs/secrets-local/`** (isso força o commit de segredos)
3. **Nunca citar valores reais em commits/PRs/issues** (usar `XXX_REDACTED_XXX`)
4. **Roda gitleaks com `--no-git` em trees sem `.git` (tarballs releases)**
5. **Atualizar `.gitleaks.toml` quando adicionar novas patterns de falso positivo**

---

## Checklist Final (antes de push)

- [ ] `ruff check .` → **0 erros**
- [ ] `ruff format --check .` → **0 arquivos a mudar**
- [ ] `gitleaks detect --source . --no-git` → **0 leaks**
- [ ] pre-commit hooks → **todos passing**
- [ ] `docs/secrets-local/` → **não adicionado ao staging**

---

## Comandos Rápidos para Teste

```bash
# Linting
uv run ruff check .

# Formatação
uv run ruff format . && uv run ruff format --check .

# Segredos (repositório completo)
gitleaks detect --source . --verbose

# Executar todos os hooks
uv run pre-commit run --all-files

# Enviar para o remoto
git push origin feat/sprint-dlp-ner-da80
```

---

**Referências:**
- `ruff.toml` — configuração do linter
- `.pre-commit-config.yaml` — hooks do pre-commit
- `.gitleaks.toml` — patterns de detecção de segredos
- `docs/secrets-local/README.md` — guidelines de credenciais

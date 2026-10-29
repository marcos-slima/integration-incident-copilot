# Pipeline de Integração Contínua Local — Guia Completo

> **Objetivo:** Garantir que nenhum código quebre os padrões de formatação, possua bugs evidentes ou vaze dados sensíveis antes de chegar ao repositório remoto.

---

## O que é

Um **Pipeline de Integração Contínua Local** é uma sequência automática de verificações e validações executadas no ambiente do desenvolvedor **antes de cada commit/push**. Ele atua como uma "porta de segurança" que filtra problemas antes que eles sejam enviados ao repositório remoto.

Neste projeto, o pipeline local consiste em:

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

## Por que é importante

1. **Prevenção de vazamentos de segredos** — `gitleaks` detecta chaves, tokens e senhas antes de serem commitados
2. **Padrões de código consistentes** — `ruff` e `ruff-format` garantem formatação uniforme sem discussões
3. **Erros evidentes nunca chegam ao remoto** — linter captura syntax, imports, lógica antes do push
4. **Custo reduzido de correção** — corrigir na máquina local é mais rápido e barato que no CI
5. **Confiança no código** — cada push é validado, não apenas quando o CI executa

---

## Para que serve

- **Validar código localmente** antes de enviar para o repositório remoto
- **Detectar segredos acidentais** (API keys, senhas, tokens) antes do commit
- **Automatizar quality gates** que de outra forma exigiriam intervenção manual
- **Garantir padrões** (linting, formatação, commits limpos) de forma consistente
- **Educar novos contributors** sobre as expectativas de código do projeto

---

## Como funciona

### Arquitetura

1. **Pre-commit hooks** — hooks do git definidos em `.pre-commit-config.yaml` são acionados em cada `git commit`
2. **Hooks individuais**:
   - `ruff` — linting com ruff
   - `ruff-format` — formatação com ruff (aplica se necessário)
   - `detect-hardcoded-secrets` — gitleaks scan
   - `trim-trailing-whitespace` — limpeza visual
   - `check-yaml` — validação de YAML
   - `check-added-large-files` — evita arquivos grandes
3. **Fluxo**:
   ```
   git commit → pre-commit hooks executados → cada hook valida → se todos ✅ commit → push
   ```

### Detalhes dos Tools

| Tool | Função | Como发现 problemas |
|---|---|---|
| **ruff** | Linter Python | Análise sintática e estática (imports, estilo, bugs) |
| **ruff-format** | Formatter | Formata código conforme PEP8/black |
| **gitleaks** | Secret scanner | Scan de padrões_regex em commits (incluindo histórico) |
| **pre-commit** | Hook orchestrator | Gerencia execução dos hooks antes de cada commit |

---

## Ciclo de vida no desenvolvimento

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 Ciclo de Vida do Pipeline Local                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  1. SETUP (uma vez por máquina)                                           │
│     ├─ curl -L https://pre-commit.com/install.py | python                 │
│     ├─ pre-commit install                                                  │
│     └─ gitleaks detect --version                                          │
│                                                                           │
│  2. WORK LOOP (a cada alteração)                                          │
│     ├─ Edita código                                                        │
│     ├─ git add file.py                                                     │
│     ├─ git commit -m "feat: ..."                                          │
│     │   └─ pre-commit hooks executam:                                     │
│     │       ├─ ✅ ruff check: 0 erros                                     │
│     │       ├─ ✅ ruff format: 0 arquivos a mudar                         │
│     │       ├─ ✅ gitleaks: 0 leaks                                       │
│     │       └─ ✅ trim-whitespace: OK                                     │
│     └─ git push origin feat/branch                                        │
│                                                                           │
│  3. CI REMOTA (push para remoto)                                          │
│     └─ GitHub Actions reexecuta validações:                               │
│         ├─ ruff, ruff-format, mypy, pytest, coverage                     │
│         ├─ gitleaks (histórico completo)                                  │
│         └─ pip-audit, bandit                                              │
│                                                                           │
│  4. CRITICAL PATH (se algo falhar)                                        │
│     ├─ Pre-commit falha → commit negado                                   │
│     │   ├─ ruff: corrigir erro → git add → git commit                     │
│     │   ├─ ruff-format: `ruff format .` → git add → git commit            │
│     │   ├─ gitleaks: remover segredo → git commit --amend                 │
│     │   └─ trim-whitespace: Editar → git add → git commit                │
│     └─ CI falha → PR bloqueado (requer novamente no remoto)              │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Configuração do ambiente

### Instalação (uma vez)

```bash
# 1. Pre-commit
curl -L https://pre-commit.com/install.py | python
pre-commit install  # ativa hooks em .git/hooks

# 2. gitleaks (ferramenta de linha de comando)
curl -sSL -o /tmp/gl.tgz https://github.com/gitleaks/gitleaks/releases/download/v8.21.2/gitleaks_8.21.2_linux_x64.tar.gz
tar -xzf /tmp/gl.tgz -C /tmp gitleaks
chmod +x /tmp/gitleaks

# 3. ruff (via uv no projeto)
uv sync --dev
```

### Configuração padrão

- **Hooks locais** em `.pre-commit-config.yaml` (ruff, ruff-format, gitleaks, etc.)
- **Patterns de segredos** em `.gitleaks.toml`
- **Código a lintar/validar** em `ruff.toml`

---

## Checklist Final (antes de push)

- [ ] `ruff check .` → **0 erros**
- [ ] `ruff format --check .` → **0 arquivos a mudar**
- [ ] `gitleaks detect --source . --no-git` → **0 leaks**
- [ ] pre-commit hooks → **todos passing**
- [ ] `docs/secrets-local/` → **não adicionado ao staging**

---

## Boas Práticas

1. **Executar ruff antes de cada save no editor** (muitos IDEs integram)
2. **Nunca usar `git add -f docs/secrets-local/`** (isso força o commit de segredos)
3. **Nunca citar valores reais em commits/PRs/issues** (usar `XXX_REDACTED_XXX`)
4. **Roda gitleaks com `--no-git` em trees sem `.git` (tarballs releases)**
5. **Atualizar `.gitleaks.toml` quando adicionar novas patterns de falso positivo**

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
- `docs/CI_REMOTE.md` — pipeline CI remoto (GitHub Actions)

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

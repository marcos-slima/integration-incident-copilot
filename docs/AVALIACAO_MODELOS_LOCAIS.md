# Avaliação empírica dos modelos locais e do ambiente OpenCode

**Data:** 2026-10-11 · **Status:** concluída, com uma pendência de runtime
(§7.2) e uma revisão de DA pendente (§6).

Este documento registra **método, processo e software** da avaliação que
escolheu o modelo local padrão do OpenCode para este projeto e montou o
ambiente (harness) que sustenta a escolha. A regra do repositório vale aqui:
**veredito vem de medição, não de heurística** — e todo placar carrega
proveniência, porque "modelo ruim" e "runtime bugado" produzem o mesmo
sintoma (§6).

> Resumo executivo: `qwen3.6:35b-a3b` venceu nos três eixos (acerto,
> velocidade e estabilidade). `qwen3.8:27b` foi o pior (6 timeouts de 240 s
> em 18 execuções). A falha que a DA-8 atribuía ao `qwen3.6` era um bug de
> runtime do Ollama (issue #10929), corrigido no v0.34.4 — o placar foi
> rodado com o runtime já corrigido (0.40.2).

---

## 1. Objetivo

1. Medir, empiricamente, qual modelo local é **mais acertivo e responsivo**
   para o uso no OpenCode (edição de código, tool calling, latência).
2. Montar o melhor ambiente possível (rápido, seguro, preciso) a partir
   disso — incluindo um **harness de refactoring** que mede a edição de
   código de verdade, com veredito de teste oculto.
3. Produzir um placar **reproduzível** e comparável: versões e digests
   carimbados junto dos números.

Fora de escopo por decisão de cenário: `gpt-oss:120b` (65 GB, TTFT ~15 s).

---

## 2. Software e proveniência

| Componente | Versão | Papel |
|---|---|---|
| Ollama | **0.40.2** (nativo no host, não em contêiner) | Servidor de inferência local |
| OpenCode | **v2.0.26** (CLI + app desktop) | Agente que executa as tarefas e é a superfície avaliada |
| Python + pytest | venv do projeto via `uv` | Harness, testes ocultos e suíte de integridade |
| ruff | via pre-commit | Lint/format dos fontes do harness |
| git | — | Congelamento de fixture, `diff --stat` e anti-escape |

Modelos avaliados (bytes comprovados por digest, lidos de
`http://localhost:11434/api/tags` no dia do placar):

| Modelo (tag Ollama) | Digest (prefixo) | Tamanho |
|---|---|---|
| `qwen3.6:35b-a3b` | `a7eb95c53bcf…` | 22 GB |
| `qwen3-coder:latest` | `06c1097efce0…` | 18 GB |
| `qwen3-coder-next:latest` | `675612ec2609…` | 51 GB |
| `qwen3.8:27b` | `aaee06c39dcf…` | 17 GB |

O bloco `ambiente` do JSON de placar grava, além disso, as versões de Ollama
e OpenCode e a nota de captura. Motivo prático: um `ollama pull` troca os
bytes sob a mesma tag; sem digest, dois placares de datas diferentes não são
comparáveis. O módulo que faz isso é
`scripts/model_harness/provenance.py::coletar` — ele degrada com elegância
(campo vira `None`/`{}` se o binário ou o servidor não respondem).

---

## 3. Método

### 3.1 Eixos de medição

| Eixo | Como é medido | Onde |
|---|---|---|
| **Acerto** | `pytest` roda um teste **oculto**, copiado para o sandbox **depois** da edição. O modelo nunca vê o teste. | `scripts/model_harness/runner.py::_rodar_pytest` |
| **Precisão** | Arquivos "congelados" byte a byte (hash) + `git diff --stat` do que foi editado. | `scripts/model_harness/runner.py::_frozen_ok` |
| **Velocidade** | Parede (s), passos e tokens/s de saída, lidos do NDJSON de `opencode run --format json`. | `scripts/model_harness/runner.py::_parse_stream` |
| **Segurança** | Flag `escaped`: qualquer edição fora do sandbox invalida o veredito daquela execução. | `scripts/model_harness/runner.py::_fora_do_sandbox` |

### 3.2 Tarefas (6)

Cada tarefa é uma unidade com fixture, instrução, teste oculto e solução de
referência (`scripts/model_harness/tasks.py`). As três propriedades que dão
sinal à tarefa são travadas por teste, não por confiança:

1. **falha** no estado inicial (senão "não fazer nada" pontua);
2. **passa** com a solução de referência (senão o teste oculto está errado);
3. o teste oculto importa algo que **existe** na fixture.

| id | O que mede |
|---|---|
| `bug_mediana` | Corrigir mediana para lista de tamanho par (lógica) |
| `dataclass` | Converter classe manual em `@dataclass` mantendo a API |
| `dispatch_dict` | Trocar cadeia `if/elif` por dict de handlers |
| `extrair_helper` | Extrair duplicação preservando saída caractere a caractere |
| `precisao_frozen` | Editar só o alvo — mede **sobre-edição** (há um arquivo congelado ao lado) |
| `tipos_docstrings` | Adicionar type hints e docstrings Google sem mudar comportamento |

### 3.3 Isolamento do sandbox (segurança)

O harness roda `opencode run --auto`, ou seja, **sem ninguém aprovando** —
por isso o isolamento é em camadas:

- cada execução acontece em `/tmp/opencode/model_harness/<modelo>/<tarefa>__r<N>/`,
  uma cópia descartável da fixture; o repositório real nunca é tocado;
- o sandbox tem `opencode.json` próprio que **nega** `shell`, `bash`,
  `webfetch`, `task` (subagente) e `external_directory`;
- `--standalone` dá um servidor privado com o sandbox como projeto;
- o processo recebe `PWD` apontando para o sandbox (o OpenCode resolve o
  projeto por `PWD`, não pelo `cwd` real do processo);
- o detector de escape (`_fora_do_sandbox`) resolve caminhos relativos como
  sandbox-relativos e usa `is_relative_to` (não `startswith`).

`--allow-bash` existe para medir o loop agêntico completo, reabrindo shell
conscientemente; **não** foi usado no placar.

### 3.4 Veredito e repetições

O veredito é **sempre do pytest oculto**, nunca do modelo: `passed =
pytest_ok AND frozen_ok AND NOT timed_out AND NOT escaped`. Cada par
(modelo × tarefa) roda com **3 repetições** (estabilidade), timeout de
240 s por execução. Total: 4 modelos × 6 tarefas × 3 repetições = **72
execuções**.

### 3.5 Agregação

`scripts/model_harness/report.py::resumir` agrega por modelo: acertos,
precisão, parede média, passos médios e tokens/s (tokens de saída ÷ parede
total). A matriz `tarefa × modelo` e a lista de falhas saem no mesmo
relatório.

---

## 4. Processo — bugs encontrados e correções

O harness mediu o ambiente, mas também **mediu a si mesmo**: os primeiros
smoques revelaram defeitos que foram corrigidos antes de qualquer placar
valer.

| # | Sintoma | Causa-raiz | Correção |
|---|---|---|---|
| 1 | O modelo escreveu **no repositório real** no primeiro smoke | `opencode run` sem `--standalone` fala com o serviço em background, cujo projeto é o repo | `--standalone` (servidor privado) |
| 2 | Ainda vazava para o repo após o item 1 | `subprocess(cwd=…)` **não** atualiza `PWD`; o OpenCode resolve o projeto por `PWD` | injetar `PWD` do sandbox no env |
| 3 | Escape falso-positivo em caminho relativo (`processar.py`) | relativo era resolvido contra o cwd do harness (o repo); `startswith` confundia `/a/b` com `/a/bc` | relativo = sandbox-relativo + `is_relative_to` |
| 4 | `python -m scripts.model_harness tasks` falhava | subparser sem `func` (argparse) | `set_defaults(func=…)` |
| 5 | `tasks.py` corrompido por um modelo que escapou (smoke) | escape antes dos itens 1–2 | arquivo recriado; o teste de integridade pegou a perda de sinal |

O episódio 5 virou nota de segurança no topo de
`scripts/model_harness/tasks.py`: um modelo que escape pode achar as
fixtures e "corrigi-las".

---

## 5. Resultados (3 repetições)

Placar completo em `/tmp/opencode/harness_full3.json` (não versionado —
ver §9), com proveniência carimbada: **ollama 0.40.2**, **opencode v2.0.26**
e os digests da §2.

### 5.1 Agregado por modelo

| Modelo | Acerto | Timeouts | Escapes | Parede média | Passos | tok/s |
|---|---|---|---|---|---|---|
| `qwen3.6:35b-a3b` | **18/18** | **0** | 0 | **14.3 s** | 4.3 | **44.6** |
| `qwen3-coder-next:latest` | **18/18** | 0 | 0 | 24.9 s | 6.1 | 18.9 |
| `qwen3-coder:latest` | 16/18 | 0 | 0 | 26.9 s | 5.6 | 31.1 |
| `qwen3.8:27b` | 12/18 | **6** | 1 | 121.0 s | 5.1 | 6.6 |

Leitura:

- `qwen3.6` ganha **em todos os eixos**: 100 % de acerto, metade do tempo
  do `coder-next` e ~2,4× mais tokens/s que ele.
- `coder-next` também é 18/18, mas é o mais lento dos dois aprovados
  (MoE 80B/3B, 51 GB em disco) — melhor como opção para código pesado.
- `coder:latest` falhou 2 edições **genuínas** (abaixo).
- `qwen3.8:27b` é o pior: 6 timeouts de 240 s e parede média de 121 s.

### 5.2 Falhas específicas

- `qwen3-coder` / `dataclass` #3: **passo único, 0 tokens, nenhuma edição**
  — o teste oculto reprovou `dataclasses.is_dataclass(Cliente)`.
- `qwen3-coder` / `precisao_frozen` #1: não colapsou espaços internos
  (`'a   b'` em vez de `'a b'`).
- `qwen3.8:27b`: 6 timeouts (240 s) — `bug_mediana` #2, `dataclass` #1,
  `dispatch_dict` #2, `extrair_helper` #1/#2/#3.

### 5.3 Escape observado (1 caso)

`qwen3.8:27b` / `extrair_helper` #2 escreveu `check_relatorio.py` **fora do
sandbox** (`/tmp/opencode/check_relatorio.py`). O detector marcou
`escaped=true` e invalidou o veredito daquela execução (que já era timeout).
**O repositório não foi tocado.** É a evidência de que o detector funciona —
e de que o `escaped` precisa existir: sem ele, a execução "passaria" tendo
editado lugar errado.

---

## 6. DA-8 e o bug de runtime do Ollama (#10929)

A DA-8 rejeitou o `qwen3.6:35b-a3b` por "falha determinista de JSON
estruturado". A verificação foi feita **na fonte**:

- issue do Ollama **#10929** — *"Ollama produces invalid JSON when using
  thinking mode with structured output"*, aberta em maio/2025;
- correção no **v0.34.4** (commit `5a0ff311`, 2026-09-23).

Conclusão: o sintoma era do **runtime**, não do modelo. O placar desta
avaliação rodou no **0.40.2** (já contém a correção) e **nenhuma falha de
JSON estruturado apareceu nas 72 execuções**.

Ressalva de processo: revisar formalmente a DA-8 é o fluxo de 5 passos do
`CLAUDE.md` — **não foi feito por conta própria**. Este documento registra a
evidência; a decisão de reabrir a DA é formal.

---

## 7. Configuração global do OpenCode

A config global (`~/.config/opencode/opencode.jsonc`) está **fora do
repositório**; o backup pré-ajuste é
`opencode.jsonc.bak.pre-config-tune-20261011`.

### 7.1 O que foi aplicado

- `model` → `ollama/qwen3.6:35b-a3b` (default; antes `qwen3-coder:latest`);
- agente `title` → **o próprio default** (`qwen3.6`), que antes usava
  `coder:latest` — elimina um swap de modelo na Ollama a cada título gerado;
- `providers.ollama.models` reduzido a 2 entradas (default + `coder-next`),
  com rótulos honestos; os dois descartados ficaram **documentados em
  comentário**, com os números do placar.

Trecho relevante do estado final:

```jsonc
{
  "model": "ollama/qwen3.6:35b-a3b",
  "providers": {
    "ollama": {
      "settings": { "baseURL": "http://localhost:11434/v1" },
      "models": {
        "qwen3.6:35b-a3b": { "name": "Qwen3.6 35B-A3B (default: 18/18, 44 tok/s end-to-end)" },
        "qwen3-coder-next:latest": { "name": "Qwen3-Coder-Next (coding pesado, 51GB, 18 tok/s)" }
      }
    }
  }
}
```

### 7.2 Pendência aberta — como remover um modelo do runtime

"Apenas documental, fora do runtime" ainda **não** é garantido pelo
mecanismo de config. O que foi testado empiricamente, com `opencode models`:

| Tentativa | Resultado |
|---|---|
| Remover a entrada do mapa `models` | O modelo **continua** listado (descoberto do Ollama instalado) |
| `blacklist: [...]` no provider | Sem efeito na lista |
| `whitelist: [...]` no provider | Em `--standalone` a lista ficou **vazia** — não é allowlist de modelos |
| Inserir modelo fake no mapa | **Aparece** (adição funciona) |
| Remover o fake | **Some** (remoção de entrada declarada funciona) |
| `opencode models --standalone` | **Zero** modelos Ollama (o servidor privado não faz descoberta) |

Interpretação observada: a lista do runtime é a **união** das entradas
declaradas no mapa `models` com os modelos **auto-descobertos** do Ollama
local (filtrados por compatibilidade — o `tev1` instalado não aparece). O
schema V2 não tem campo `enabled` por modelo. Para tornar os dois modelos
não-opções de runtime, restam dois caminhos:

1. achar o mecanismo correto do OpenCode (não confirmado nesta avaliação); ou
2. `ollama rm qwen3-coder:latest qwen3.8:27b` — garantido, libera ~35 GB,
   mas exige re-pull para restaurar.

**Decisão pendente do usuário.**

---

## 8. Reprodutibilidade

Da raiz do repositório:

```bash
# lista as tarefas e seus contratos
uv run python -m scripts.model_harness tasks

# placar completo (3 repetições), com proveniência no JSON
PATH="$PWD/.venv/bin:$PATH" uv run python -m scripts.model_harness run \
  --out /tmp/opencode/harness_full3.json --repeats 3

# doctor dos modelos: instalado + tool calling + tokens/s + digest
uv run python scripts/check_models.py

# integridade do harness (tarefas com sinal, sandbox negando o perigoso, anti-escape)
uv run pytest tests/test_model_harness.py -v
```

Notas: o sandbox fica em `/tmp/opencode/model_harness/` (relocável via
`MODEL_HARNESS_DIR`); `--allow-bash` reabre shell conscientemente; o timeout
por execução é 240 s (definido por tarefa).

Verificação da etapa: suíte unitária `1495 passed, 15 skipped, 14
deselected`; quality gate (DA-51) verde, com 3 avisos pré-existentes e não
relacionados (`reranker_invariant`, `deployed_model_evaluated`,
`index_manifest`); harness com **32 testes de integridade** verdes e ruff
limpo. Commit do harness: `7fa2a75` (8 arquivos, +1529 linhas).

---

## 9. Limitações e pendências

- **Amostra pequena e estreita:** 6 tarefas × 3 repetições medem edição de
  código — não medem qualidade de diagnóstico do produto (o que a suíte de
  avaliação existente cobre). O veredito vale para **este** runtime
  (Ollama 0.40.2) e **estes** digests.
- **Sandbox sem shell** de propósito: mede edição pura, não o loop agêntico
  completo. `--allow-bash` mede o loop, assumindo o risco.
- **Placar não versionado:** o JSON vive em `/tmp`. Se a decisão for
  citável no repositório, considerar versioná-lo (ex.: `data/eval/`).
- **Pendências:** (a) mecanismo de exclusão de modelos do runtime (§7.2);
  (b) revisão formal da DA-8 (§6); (c) commits da sessão ainda não
  enviados ao `origin`.

---

## 10. Arquivos

| Arquivo | Papel |
|---|---|
| `scripts/model_harness/tasks.py` | 6 tarefas: fixture + instrução + teste oculto + solução |
| `scripts/model_harness/runner.py` | Sandbox, execução, anti-escape, pytest oculto |
| `scripts/model_harness/report.py` | Agregação e impressão do placar |
| `scripts/model_harness/provenance.py` | Versões (Ollama/OpenCode) + digests |
| `scripts/model_harness/__main__.py` | CLI `tasks` / `run` |
| `scripts/check_models.py` | Doctor: instalado + tool calling + tok/s + digest |
| `tests/test_model_harness.py` | 32 invariantes de integridade do harness |
| `/tmp/opencode/harness_full3.json` | Placar 3-rep + proveniência (não versionado) |

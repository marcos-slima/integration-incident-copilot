# Guia de Auditoria GRC - Integration Incident Copilot

## Público-Alvo

Este guia destina-se a:

- **Auditors internos** - Profissionais da empresa realizando auditorias de conformidade
- **Auditors externos** - Firmas de auditoria (Big 4) realizando SOC 2 ou ISO 27001
- **Compliance Officers** - Responsáveis pela governança de TI
- **Security Teams** - Equipes de segurança responsável pela infraestrutura

## Escopo da Auditoria

### O que está incluído

1. **Integration Incident Copilot (IIC)** - Sistema completo de diagnóstico de integração
2. **APIs de Administração** - `/admin/api/*` endpoints
3. **Sistema de Autenticação** - X-API-Key e token de sessão
4. **Banco de Dados** - PostgreSQL com incidentes e logs
5. **Registry de Modelos LLM** - Controle de acesso a credenciais cifradas

### O que não está incluído

1. Modelos LLM externos (Ollama, OpenAI, Azure OpenAI)
2. Infraestrutura de deploy (Kubernetes, VMs)
3. Sistemas integrados (SAP, ServiceNow, Salesforce, etc.)

## Cronograma Estimado

| Fase | Descrição | Duração |
|------|-----------|---------|
| 1. Planejamento | Definição de escopo e equipe | 2-3 dias (primeira semana) |
| 2. Coleta de evidência | Revisão de documentação e configurações | 5-7 dias |
| 3. Testes de campo | Verificação de controles em produção | 5-7 dias |
| 4. Relatório | Redação e revisão do relatório | 3-5 dias |
| **Total** | | **3-4 semanas (15-22 dias úteis)** |

> O cronograma considera uma semana de folga para correções antes da
> re-auditoria.

## Preparação para a Auditoria

### Antes da auditoria

1. **Documentação** (entregar aos auditores):
   - Este guia (`docs/GRM_AUDIT.md`)
   - Mapeamento (docs/GRM_MAPPING.md)
   - Checklist (docs/GRM_CHECKLIST.md)
   - Documentação técnica (`docs/ARCHITECTURE.md`, `docs/DECISOES_DE_ARQUITETURA.md`)

2. **Estrutura de dados**:
   ```sql
   -- Tabelas principais
   - incidents          # Registros de diagnósticos
   - llm_credentials    # Credenciais cifradas
   - llm_models         # Registros de modelos
   - integration_systems # Catálogo de sistemas
   - llm_usage          # Metering e custos
   ```

3. **Ambiente de teste**:
   - Provisionar ambiente com dados fictícios
   - Garantir acesso admin aos auditores

### Equipe de auditoria

| Papel | Responsabilidades |
|-------|-------------------|
| Auditor principal | Coordenação e relatório final |
| Auditor técnico | Verificação técnica dos controles |
| Analista de compliance | Revisão de conformidade |

## Metodologia de Teste

### 1. Verificação de Documentação

**Objetivo**: Validar que todos os controles estão documentados

**Procedimento**:
1. Listar todos os controles do mapeamento (docs/GRM_MAPPING.md)
2. Para cada controle, verificar documento correspondente
3. Registrar localização exata (arquivo + seção + linha)

**Evidência**:
- Documentação online
- Índice de cross-reference

### 2. Verificação de Implementação Técnica

**Objetivo**: Validar que controles estão implementados no código

**Procedimento**:

#### Autenticação e Controle de Acesso

```bash
# Verificar X-API-Key na API
API_KEY="seu-api-key-aqui"
curl -H "X-API-Key: $API_KEY" http://localhost:8000/admin/api/models
# Esperado: 200 OK (com chave válida)

# Verificar admin key separada
ADMIN_KEY="sua-admin-key-aqui"
curl -H "X-API-Admin-Key: $ADMIN_KEY" http://localhost:8000/admin/api/models
# Esperado: 200 OK (com chave válida)
```

#### Credenciais Cifradas

```bash
# Verificar que credenciais estão cifradas no banco
psql -c "SELECT masked FROM llm_credentials;"
# Esperado: Valores mascarados (*), nunca plaintext
```

#### Logs e Auditoria

```bash
# Verificar logs estruturados
psql -c "SELECT trace_id, created_at FROM incidents LIMIT 5;"
# Esperado: trace_id presente em todos os registros
```

### 3. Testes Funcionais

#### Controle: A.8.2 - Regras de acesso

**Teste**: Tentar acessar endpoint sem autenticação

```bash
curl http://localhost:8000/admin/api/models
# Result: 401 Unauthorized
```

**Teste**: Tentar acessar com chave inválida

```bash
chave="chave-invalida"
curl -H "X-API-Key: $chave" http://localhost:8000/admin/api/models
# Result: 401 Unauthorized
```

#### Controle: A.12.6 - Registro de eventos

**Teste**: Verificar logs após operação

```python
# Criar um incidente
response = client.post("/diagnose", json={
    "interface_type": "odata",
    "description": "Teste de auditoria"
})

# Verificar que foi gravado no banco
psql -c "SELECT id, trace_id FROM incidents ORDER BY created_at DESC LIMIT 1;"
```

#### Controle: A.5.9 - Responsabilidades funcionais

**Teste**: Separar responsabilidades

- Usuário comum: não pode criar credenciais
- Admin: pode criar credenciais
- Admin: não pode diagnostics (responsabilidade separada)

### 4. Testes de Segurança

#### Pentest leve

**Objetivo**: Identificar vulnerabilidades de médio risco

**Testes**:

1. SQL Injection
   ```bash
   curl "http://localhost:8000/admin/api/models?id=1' OR '1'='1"
   # Result: 422 (SQL injection bloqueada)
   ```

2. XSS
   ```bash
   curl -d "<script>alert(1)</script>" http://localhost:8000/admin/api/models
   # Result: 422 (XSS bloqueado)
   ```

3. CSRF
   ```bash
   # Requests admin exigem X-API-Admin-Key header
   # Não há endpoints vulneráveis sem token
   ```

## Evidências Requeridas

### Documentação

| Evidência | Localização | Formato |
|-----------|-------------|---------|
| Política de segurança | docs/GRM_MAPPING.md | Markdown |
| Guia de admin | docs/DEPLOY.md | Markdown |
| Arquitetura | docs/ARCHITECTURE.md | Markdown |

### Registros

| Evidência | Query SQL | Formato |
|-----------|-----------|---------|
| Registros de autenticação | `SELECT * FROM llm_credentials` | CSV |
| Logs de incidentes | `SELECT trace_id, created_at FROM incidents` | CSV |
| Metering | `SELECT * FROM llm_usage` | CSV |

### Configuração

| Evidência | Arquivo | Formato |
|-----------|---------|---------|
| `.env.example` | `.env.example` | Env |
| Configuração | `app/config.py` | Python |
| Docker config | `docker-compose.yml` | YAML |

## Relatório de Auditoria

### Estrutura

1. **Executivo**
   - Resumo de conformidade (SIM/NÃO/Parcial)
   - Pontos críticos
   - Recomendações

2. **Técnico**
   - Controles verificados
   - Resultados dos testes
   - Evidências coletadas

3. **Anexos**
   - Screenshots
   - Logs
   - Query results

### Critérios de aprovação

| Nível | Critério |
|-------|----------|
| Aprovar | 100% controles implementados |
| Aprovar com condicionantes | >90% implementados, planos de ação |
| Não aprovar | <80% implementados |

## Próximos Passos

1. **Agendar auditoria**
   - Definir data e equipe
   - Preparar ambiente

2. **Executar auditoria**
   - Seguir metodologia deste guia
   - Documentar todos os testes

3. **Correção de não conformidades**
   - Criar issues no GitHub
   - Estimar esforço
   - Priorizar

4. **Re-auditoria**
   - Validar correções
   - Emitir certificado final

## Contato

- **Produto owner**: Integration Incident Copilot team
- **Security team**: security@empresa.com
- **Compliance**: compliance@empresa.com

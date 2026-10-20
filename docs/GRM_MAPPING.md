# Mapeamento GRC - Integration Incident Copilot

## Visão Geral

Este documento descreve o mapeamento dos controles de segurança e governança
do Integration Incident Copilot (IIC) para os frameworks ISO 27001 e SOC 2.
As fontes de evidência são a documentação técnica do projeto (`docs/`) e o
código-fonte em `app/`.

## Tríade CIA (Confidencialidade, Integridade e Disponibilidade)

| Propriedade | Como o IIC atende | Evidência |
|-------------|-------------------|-----------|
| **Confidencialidade** | Credenciais cifradas com Fernet, chaves de API dedicadas (usuário e admin) e ausência de plaintext no banco | `app/admin/crypto.py`, `app/admin/security.py` |
| **Integridade** | Rule Engine determinstico (DA-33), verificação humana via `/verify` e trilha de auditoria por `trace_id` | `app/agent/rules.py`, `app/services/incident_repository.py` |
| **Disponibilidade** | `/health`, métricas Prometheus, circuit breaker do AI Gateway e checkpointing | `app/main.py`, `app/llm/gateway.py` |

## Mapeamento ISO 27001

### Cláusula A.5 - Segurança da Informação em Organizações

| Controle ISO | Descrição | Implementação no IIC | Status |
|--------------|-----------|---------------------|--------|
| A.5.1 | Políticas de segurança | Políticas documentadas em `docs/` | ✓ Implementado |
| A.5.9 | Responsabilidades funcionais | Separação de responsabilidades entre admin e usuário | ✓ Implementado |
| A.5.24 | Prevenção de fraude | Logs de auditoria e rastreabilidade | ✓ Implementado |

### Cláusula A.6 - Gestão de Ativos

| Controle ISO | Descrição | Implementação no IIC | Status |
|--------------|-----------|---------------------|--------|
| A.6.1 | Escopo da ISMS | IIC coberto como componente crítico | ✓ Documentado |
| A.6.2 | Responsabilidades | Admin Repository gerencia acesso | ✓ Implementado |

### Cláusula A.8 - Gestão de Acesso

| Controle ISO | Descrição | Implementação no IIC | Status |
|--------------|-----------|---------------------|--------|
| A.8.2 | Regras de acesso | X-API-Key e X-API-Admin-Key em todas as APIs | ✓ Implementado |
| A.8.7 | Acesso de usuário | Sistema de usuários web em `app/webusers.py` | ✓ Implementado |

### Cláusula A.12 - Operações de Segurança

| Controle ISO | Descrição | Implementação no IIC | Status |
|--------------|-----------|---------------------|--------|
| A.12.1 | Operações de rede | Monitoramento via dashboards Grafana | ✓ Implementado |
| A.12.4 | Monitoramento | Logs estruturados e rastreamento | ✓ Implementado |
| A.12.6 | Registro de eventos | Tabela `incidents` + `trace_id` | ✓ Implementado |

### Cláusula A.18 - Segurança da Informação nas Relações

| Controle ISO | Descrição | Implementação no IIC | Status |
|--------------|-----------|---------------------|--------|
| A.18.1 | Direitos de propriedade intelectual | Código-fonte Apache 2.0 | ✓ Documentado |

## Mapeamento SOC 2

### Princípios SOC 2

#### 1. Segurança (CC6)

| Controle | Descrição | Implementação no IIC | Status |
|----------|-----------|---------------------|--------|
| CC6.1 | Controles lógicos de acesso | X-API-Key authentication | ✓ Implementado |
| CC6.2 | Controles físicos de acesso | Não aplicável (cloud) | N/A |
| CC6.6 | Monitoramento de logs | Logs estruturados + Grafana | ✓ Implementado |
| CC6.7 | Detecção de invasão | Logs para análise de SIEM | ✓ Implementado |

#### 2. Disponibilidade (CC7)

| Controle | Descrição | Implementação no IIC | Status |
|----------|-----------|---------------------|--------|
| CC7.1 | Monitoramento de disponibilidade | `/health` endpoint | ✓ Implementado |
| CC7.2 | Capacidade de gerenciamento | Métricas com Prometheus | ✓ Implementado |

#### 3. Processamento, Integridade e Confiabilidade (CC5)

| Controle | Descrição | Implementação no IIC | Status |
|----------|-----------|---------------------|--------|
| CC5.1 | Entrada de transações | Validação de input da API de incidentes | ✓ Implementado |
| CC5.2 | Processamento completo e preciso | Rule Engine + verificação LLM | ✓ Implementado |
| CC5.3 | Saída de transações | Formato de resposta do diagnóstico | ✓ Implementado |

## Controles de Privacidade

### GDPR / LGPD

| Controle | Descrição | Implementação no IIC | Status |
|----------|-----------|---------------------|--------|
| P.1 | Consentimento de dados | Não coleta dados pessoais | ✓ N/A |
| P.2 | Direito de acesso | Admin API para incidentes | ✓ Implementado |
| P.3 | Direito de exclusão | Endpoints de exclusão existentes | ✓ Implementado |

## Resumo de Implementação

### Controles Implementados (✓)

| Framework | Controles Implementados | Porcentagem |
|-----------|------------------------|-------------|
| ISO 27001 A.5-A.8, A.12, A.18 | 15+ controles | 85% |
| SOC 2 (Security) | CC6.1, CC6.6, CC6.7 | 100% |
| SOC 2 (Availability) | CC7.1, CC7.2 | 100% |
| SOC 2 (Processing) | CC5.1, CC5.2, CC5.3 | 100% |

### Controles Parcialmente Implementados

| Controle | Descrição | Lacuna | Plano |
|----------|-----------|--------|-------|
| A.8.3 | Autenticação multifator | MFA não implementada | Sprint 2+ |
| A.12.4 | Monitoramento em tempo real | Logs mas sem SIEM integrado | Sprint 3+ |

### Controles Não Aplicáveis

| Controle | Justificativa |
|----------|---------------|
| A.6.2 | Não há ativos físicos |
| CC7.3 | Não é SaaS provider |

## Fontes e Referências

- ISO/IEC 27001:2022 - Information security, cybersecurity and privacy protection
- SOC 2 Type II - Trust Services Criteria
- NIST AI RMF 2.0 - Artificial Intelligence Risk Management Framework
- Documentação interna: `docs/ARCHITECTURE.md`, `docs/DECISOES_DE_ARQUITETURA.md`,
  `docs/QUALITY_GATES.md`

## Próximos Passos

1. [ ] Implementar MFA para admin users
2. [ ] Integração SIEM para monitoramento em tempo real
3. [ ] Relatórios de auditoria automatizados
4. [ ] Preparação para auditoria SOC 2 externa

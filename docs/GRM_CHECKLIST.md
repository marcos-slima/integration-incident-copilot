# Checklist de Conformidade GRC - Integration Incident Copilot

Use este checklist para verificar a conformidade dos controles GRC antes de uma auditoria oficial.

## Como usar

1. **Verifique** cada item na ordem em que aparece e marque o resultado.
2. **Documente** a evidência (arquivo, query ou captura de tela) ao lado de
   cada item concluído.
3. **Confirme** com o responsável indicado antes de declarar o controle pronto.
4. Repita a verificação a cada revisão interna semanal.

## Legenda

- [ ] Item pendente
- [x] Item implementado e verificado
- [?] Item parcialmente implementado
- [n/a] Não aplicável

---

## ISO 27001 - Cláusula A.5 (Segurança em Organizações)

### A.5.1 - Políticas de segurança

- [ ] Política de segurança documentada e aprovada
- [ ] Política disponível para todos os funcionários
- [ ] Revisão periódica da política (anual)

### A.5.9 - Responsabilidades funcionais

- [ ] Separação de responsabilidades implementada
- [ ] Admin não pode executar diagnósticos
- [ ] Usuário comum não pode acessar admin

### A.5.24 - Prevenção de fraude

- [ ] Logs de auditoria habilitados
- [ ] Logs armazenados com retenção definida
- [ ] Logs não podem ser alterados

---

## ISO 27001 - Cláusula A.6 (Gestão de Ativos)

### A.6.1 - Escopo da ISMS

- [ ] IIC definido como componente crítico
- [ ] Documento de escopo aprovado

### A.6.2 - Responsabilidades

- [ ] Owner do IIC designado
- [ ] Responsável por segurança de dados
- [ ] Responsável por backup e recuperação

---

## ISO 27001 - Cláusula A.8 (Gestão de Acesso)

### A.8.2 - Regras de acesso

- [ ] X-API-Key em todas as APIs admin
- [ ] Credenciais nunca armazenadas em plaintext
- [ ] Rotina de rotação de credenciais

### A.8.3 - Autenticação multifator (MFA)

- [ ] MFA para admin users (sprint 2+)
- [ ] Fallback sem MFA para emergency access

### A.8.7 - Acesso de usuário

- [ ] Sistema de usuários web implementado
- [ ] Papéis (roles) e permissões definidas
- [ ] Desativação de usuário automatizada

---

## ISO 27001 - Cláusula A.12 (Operações de Segurança)

### A.12.1 - Operações de rede

- [ ] Monitoramento de rede implementado
- [ ] Alertas configurados para eventos críticos

### A.12.4 - Monitoramento

- [ ] Logs estruturados em todos os endpoints
- [ ] Trace ID em todos os logs
- [ ] Logs enviados para SIEM (se disponível)

### A.12.6 - Registro de eventos

- [ ] Todos os diagnósticos registrados em banco
- [ ] Logs de acesso a endpoints admin
- [ ] Logs de alterações de configuração

---

## ISO 27001 - Cláusula A.18 (Segurança nas Relações)

### A.18.1 - Direitos de propriedade intelectual

- [ ] Código-fonte sob licença Apache 2.0
- [ ] Documentação aberta
- [ ] Third-party dependencies mapeados

---

## SOC 2 - Princípio de Segurança (CC6)

### CC6.1 - Controles lógicos de acesso

- [ ] Autenticação obrigatória em todas as APIs
- [ ] Tokens de sessão com expiry
- [ ] Rate limiting implementado

### CC6.6 - Monitoramento de logs

- [ ] Logs estruturados em JSON
- [ ] Logs armazenados com retention policy
- [ ] Logs são immutable

### CC6.7 - Detecção de invasão

- [ ] Logs disponíveis para SIEM
- [ ] Alertas configurados para tentativas de invasão

---

## SOC 2 - Princípio de Disponibilidade (CC7)

### CC7.1 - Monitoramento de disponibilidade

- [ ] `/health` endpoint implementado
- [ ] Alertas de indisponibilidade
- [ ] SLA monitoramento

### CC7.2 - Capacidade de gerenciamento

- [ ] Metrics coletadas via Prometheus
- [ ] Dashboards Grafana configurados
- [ ] Capacity planning documentado

---

## SOC 2 - Princípio de Processamento (CC5)

### CC5.1 - Entrada de transações

- [ ] Validação de input em todos os endpoints
- [ ] Sanitização de dados de entrada
- [ ] Prevenção de SQL injection

### CC5.2 - Processamento completo e preciso

- [ ] Rule Engine para diagnósticos rápidos
- [ ] LLM como fallback para casos complexos
- [ ] Verificação humana com `/verify` endpoint

### CC5.3 - Saída de transações

- [ ] Formato de resposta consistente
- [ ] Tratamento de erros padronizado
- [ ] Logging de saída

---

## Privacidade - GDPR / LGPD

### P.1 - Consentimento de dados

- [ ] Não coletamos dados pessoais de usuários
- [ ] Dados apenas de diagnósticos de sistema

### P.2 - Direito de acesso

- [ ] Admin API para visualizar incidentes
- [ ] Exportação de dados para auditoria

### P.3 - Direito de exclusão

- [ ] Endpoint para deletar incidentes
- [ ] Cleanup automático após retention

---

## Testes de Segurança Regulares

### Semanal

- [ ] Scan de vulnerabilidades (nmap, nessus)
- [ ] Revisão de logs de autenticação
- [ ] Verificação de backups

### Mensal

- [ ] Pentest leve (OWASP ZAP)
- [ ] Revisão de permissões de acesso
- [ ] Aferição de SLA

### Trimestral

- [ ] Red team exercise
- [ ] Tabletop simulation
- [ ] Review de política de segurança

---

## Verificação de Conformidade Pré-Auditoria

### Checklist pré-auditoria

1. [ ] Todos os controles listados estão implementados
2. [ ] Documentação está atualizada
3. [ ] Ambiente de teste está provisionado
4. [ ] Equipe de resposta está treinada
5. [ ] Planos de ação para não conformidades

### Critérios de pronto para auditoria

- [ ] 100% controles ISO 27001 implementados
- [ ] 100% controles SOC 2 Security implementados
- [ ] ≥90% controles SOC 2 Availability implementados
- [ ] ≥90% controles SOC 2 Processing implementados
- [ ] Não há brechas críticas abertas

---

## Próximos Passos

1. **Revisão interna** (semanal) - Usar checklist para autoverificação
2. **Correção de brechas** (imediato) - Criar issues para items [ ]
3. **Preparação externa** (2 semanas antes da auditoria)
4. **Auditoria oficial** (data definida pela empresa)

---

## Responsabilidades

| Item | Responsável | Prazo |
|------|-------------|-------|
| Implementar MFA | Security Team | Sprint 2+ |
| Integração SIEM | DevOps Team | Sprint 3+ |
| Relatórios automatizados | Product Team | Sprint 4+ |

---

*Este checklist é parte da documentação GRC do Integration Incident Copilot.*

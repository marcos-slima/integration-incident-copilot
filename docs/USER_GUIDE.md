# Uso do IIC

Execute o [quickstart](GETTING_STARTED.md) ou configure o [deployment](DEPLOY.md).
O login usa usuários do deployment IIC; o cookie `iic_session` é separado do FISC.
Não use chaves de API no formulário de login. Contas persistidas exigem o banco
configurado; autenticação deve seguir a política do operador.

Na UI, descreva o incidente e selecione o transporte e os dados disponíveis.
O transporte não é um domínio fiscal: NF-e e seus contratos pertencem ao FISC.
A UI IIC usa o schema público do core; sua lista de transportes é testada contra ele.

Leia separadamente causa provável, ações propostas, evidências, proveniência e
lacunas. Simulação/demo, documentos recuperados e relato do usuário não têm o
mesmo nível de confiança de uma observação de sistema. Hipótese do LLM não
prova uma causa. A presença de uma fonte não representa validação humana.

A API e seus schemas estão em `/docs` no deployment. `/health` mede saúde HTTP,
não sucesso de inferência nem validação de um conector. Para contratos, recursos
MCP/A2A/eventos e implementação, consulte a
[documentação da plataforma](https://github.com/marcos-slima/copilot-platform/blob/main/docs/README.md).
As capacidades habilitadas dependem da configuração e das permissões de origem.

Dúvidas de instalação: [troubleshooting](TROUBLESHOOTING.md). Incidentes fiscais:
[FISC](https://github.com/marcos-slima/fiscal-incident-support-copilot).

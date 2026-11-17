# Manifests Kyma do IIC

Manifests de produto, consumindo imagem IIC e bibliotecas da plataforma.
A imagem inicia iic_copilot.main:app e preserva /app/alembic.ini; não exige
fontes de app/core ou FISC neste checkout.

Namespace, Deployment/worker, Service, HPA e APIRule estão nos YAMLs.
Substitua referências de imagem/domínio e provisione segredos no ambiente;
secret.example.yaml é somente template.
Para renderizar, use kubectl kustomize deploy/kyma na raiz.
Renderizar não aplica recursos nem valida o cluster.

A CI atual testa wheel, Compose e imagem; a migração não revalidou Kyma gerenciado.
Verifique imagem, configuração, serviços, soberania, probes, backup e permissões
no destino antes de implantação autorizada. Migrations são ação explícita.

[Deploy atual](../../docs/DEPLOY.md) · [arquitetura](../../docs/ARCHITECTURE.md)
· [overlay](../local/README.md) · [histórico](../../docs/history/README.md).

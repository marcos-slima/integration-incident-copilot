# Overlay local do IIC

Adapta deploy/kyma para Kubernetes local. Remove APIRule e acrescenta serviços
e configurações indicados nos YAMLs. Para renderizar, use
kubectl kustomize deploy/local na raiz. Isso não cria cluster nem valida rollout.

scripts/k3d-validate.sh e os registros k3d anteriores são herdados. Não foram
reexecutados contra a arquitetura separada nesta atualização documental.
Não usar resultados antigos como comprovação do deployment atual.
Revise o script antes de qualquer execução que crie ou remova recursos.
A implementação comum de origens e soberania pertence à plataforma.

[Deploy](../../docs/DEPLOY.md) · [base](../kyma/README.md)
· [histórico](../../docs/history/README.md).

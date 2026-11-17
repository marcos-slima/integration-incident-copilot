# Versionamento e releases do IIC

O manifest de produto declara integration-incident-copilot 1.4.0. O shell UI
acompanha o produto; as bibliotecas têm linhas próprias: copilot-core 1.4.1,
@copilot/core-ui 0.1.1 e copilot-operations 1.0.0.

shared-libraries.json registra URL/hash; uv.lock e frontend/package-lock.json
fixam a resolução. Atualize somente as dependências necessárias, valide UI,
wheel, testes e imagem e revise a matriz de operações antes de aplicar migrations.
Não editar ou sobrescrever assets/tags já publicados. Alterações apenas editoriais
não exigem nova versão de software nem reemissão de pacotes.

Releases anteriores e tags v* continuam no IIC como histórico. A divergência
entre tags históricas 2.x e o manifest 1.4.0 não foi resolvida automaticamente.
A próxima release do produto deve reconciliar esse histórico explicitamente;
esta reorganização documental não publica uma release IIC.

[Política dos componentes compartilhados](https://github.com/marcos-slima/copilot-platform/blob/main/docs/RELEASES.md).

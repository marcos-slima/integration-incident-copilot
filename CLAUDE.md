# IIC — instruções de produto

Leia AGENTS.md e docs/PLATFORM_CONSUMPTION.md antes de editar.
IIC é consumidor de copilot-platform: iic_copilot, frontend, testes de produto
 e configurações de implantação pertencem aqui.

DAs, invariantes, implementação do core e sua suíte vivem em
https://github.com/marcos-slima/copilot-platform (CLAUDE.md e docs/DECISOES_DE_ARQUITETURA.md).
Os documentos herdados no histórico deste repositório não substituem a fonte canônica.
Use uv e app.sdk; não reintroduza cópias do core. Testes e quality_gate do produto
são executados pela .github/workflows/ci.yml. Não publicar versão de produto nem
alterar prompts ou migrations como efeito automático da adoção de bibliotecas.

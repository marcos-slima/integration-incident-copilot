# Integration Incident Copilot (IIC)

AI assistance for enterprise integration incidents across SAP and other vendors,
with deterministic rules, evidence and hypotheses for human review.

[Português](README.pt-BR.md) · [Documentation](docs/README.md) · [Get started](docs/GETTING_STARTED.md)

This repository owns the IIC Python shell (`iic_copilot`), integration UI,
consumer tests, Docker image and deployment configuration.
[Copilot Platform](https://github.com/marcos-slima/copilot-platform) owns the shared
Python core, core-ui and operational bundle, each independently versioned.
[FISC](https://github.com/marcos-slima/fiscal-incident-support-copilot) owns fiscal support.

| Dependency | Adopted version | Source |
|---|---|---|
| copilot-core | 1.4.1 | shared-libraries.json and uv.lock |
| @copilot/core-ui | 0.1.1 | frontend/package-lock.json |
| copilot-operations | 1.0.1 | shared-libraries.json |

The product manifest declares 1.4.0; this is not a new product release.
Existing tags and releases remain immutable. See [release ownership](docs/VERSIONAMENTO.md).

Use [the installed-wheel quickstart](docs/GETTING_STARTED.md),
[deployment](docs/DEPLOY.md), [architecture](docs/ARCHITECTURE.md) and
[validation boundaries](docs/QUALITY_GATES.md). Product code imports only `app.sdk`;
there is no active core source tree here. Historical monorepo guides are in
[the archive](docs/history/README.md), not current setup instructions.

Run the commands in the quickstart before starting `iic_copilot.main:app`.
A full diagnosis requires the configured LLM, knowledge base and relevant source system.
Consumer/image tests do not establish live LLM or SAP validation.

[Contribute](CONTRIBUTING.md) · [MIT license](LICENSE)

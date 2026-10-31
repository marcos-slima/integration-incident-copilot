# Glossário - Integration Incident Copilot

## Acronym Definitions

| Sigla | Significado | Contexto |
|---|---|---|
| **API** | Application Programming Interface | Interface para comunicação entre sistemas |
| **CI/CD** | Continuous Integration / Continuous Delivery | Automação de testes e deploy contínuos |
| **CVE** | Common Vulnerabilities and Exposures | Padrão de identificação de vazimentos de segurança |
| **DA** | Decisão de Arquitetura | Registro formal de decisões técnicas (DA-51, DA-63...) |
| **DLP** | Data Loss Prevention | Prevenção de perda de dados |
| **E2E** | End-to-End | Teste completo do fluxo |
| **FAISS** | Facebook AI Similarity Search | Biblioteca de busca de similaridade |
| **Fernet** | Algoritmo de criptografia simétrica | Usado para proteger credenciais |
| **GDPR** | General Data Protection Regulation | Regulamento geral de proteção de dados |
| **GRC** | Governance, Risk and Compliance | Governança, riscos e conformidade |
| **HITL** | Human-in-the-Loop | Interação humana em processos automatizados |
| **IDoc** | Intermediate Document | Formato de troca de documentos no SAP |
| **IIC** | Integration Incident Copilot | Nome do projeto |
| **JSON** | JavaScript Object Notation | Formato de troca de dados |
| **LLM** | Large Language Model | Modelo de linguagem (ex: qwen3.6:35b-a3b) |
| **MCP** | Model Context Protocol | Protocolo para acesso a recursos |
| ** Neo4j** | Database em grafo | Usado para GraphRAG (opcional) |
| **NER** | Named Entity Recognition | Reconhecimento de entidades nomeadas |
| **OData** | Open Data Protocol | Protocolo de API REST para SAP |
| **Ollama** | Framework para rodar LLMs localmente | Executa qwen3.6:35b-a3b |
| **PDF** | Portable Document Format | Formato de documento eletrônico |
| **PII** | Personally Identifiable Information | Dados pessoais identificáveis |
| **PR** | Pull Request | Solicitação para mesclar alterações no GitHub |
| **Pydantic** | Biblioteca de validação de dados Python | Usada no FastAPI |
| **Qdrant** | Vector database para RAG | Armazena coleções RAG |
| **RAG** | Retrieval-Augmented Generation | Combina LLM com busca de documentos |
| **RFC** | Remote Function Call | Protocolo de chamada de função remota do SAP |
| **RFC** | Request for Comments | Documento de especificação (ex: RFC 6749) |
| **RQ** | Redis Queue | Fila de tarefas assíncronas |
| **SAP** | Systems, Applications & Products | Empresa e suite ERP |
| **SaaS** | Software as a Service | Modelo de entrega de software |
| **SMTP** | Simple Mail Transfer Protocol | Protocolo para envio de e-mail |
| **SSL/TLS** | Secure Sockets Layer / Transport Layer Security | Protocolos de segurança de rede |
| **UTC** | Coordinated Universal Time | Fuso horário padrão |
| **uv** | Gerenciador de pacotes Python rápido | Substitui pip/poetry |

## Siglas do Projeto IIC

| Sigla | Significado | Detalhes |
|---|---|---|
| **IIC-AUD-XXX** | Auditoria Integration Incident Copilot | Ex: IIC-AUD-026, IIC-AUD-101 |
| **IIC-AUD-3xx** | Auditoria rodada 3 (2026-10-20) | Correções pós-adoção de DA-18/26/28... |
| **IIC-AUD-206** | Auditoria PII detection | Verificação de NER-based detection |
| **R1..R16** | Requisitos R1 (DA-18) | Autenticação e autorização |
| **R2..R21** | Requisitos R2 (DA-29) | Reranking com MiniLM v2 |
| **R4..R4-xx** | Requisitos R4 (DA-26) | Gate de custo e orçamento |
| **R5..R5-xx** | Requisitos R5 (DA-33) | Rule Engine para diagnóstico |
| **R6..R6-xx** | Requisitos R6 (DA-44) | Escalonamento inteligente |
| **R7..R7-xx** | Requisitos R7 (DA-54) | Login web UI com cookie HMAC |
| **R8..R8-xx** | Requisitos R8 (DA-55) | Admin UI para gerenciar usuários |

## Siglas de Tecnologia

| Sigla | Significado | Uso no IIC |
|---|---|---|
| **FastAPI** | Framework Python para APIs | Backend do IIC |
| **LangGraph** | Orquestração de agentes com Grafo | `app/agent/graph.py` |
| **LangChain** | Framework para apps com LLM | RAG com Qdrant |
| **Qdrant** | Vector database | Armazena `sap_incident_docs`, `sap_reference_library` |
| **Neo4j** | Graph database | GraphRAG (opcional) |
| **Ollama** | LLM runtime local | Executa modelo qwen3.6:35b-a3b |
| **nomic-embed-text** | Model de embedding | Gerador de embeddings (DA-38) |
| **cross-encoder/mmarco-mMiniLMv2-L12-H384-v1** | Reranker | DA-29: índice de qualidade |
| **Alembic** | Migration tool | Gerencia migrations no PostgreSQL |
| **Pydantic** | Validadora de dados | Models de entrada/saída |
| **ruff** | Linter Python | `ruff check`, `ruff format` |
| **pytest** | Framework de testes | `tests/` com strict mode |
| **HMAC** | Hash-based Message Authentication | Cookie de sessão da UI |
| **PBKDF2** | Password-Based Key Derivation Function | Hash de senhas (SHA256) |
| **Fernet** | Encryption algorithm | Credenciais no PostgreSQL (DA-47) |
| **OpenAPI** | Especificação de API | `app/main.py:openapi_url="/docs"` |

## Siglas de Arquitetura

| Sigla | Significado | Importância |
|---|---|---|
| **fail-closed** | Falha segura por bloqueio | DA-18: sessão vazia = 401 |
| **fail-open** | Falha aberta por permissão | Raramente usado (perigo) |
| **RAG** | Retrieval-Augmented Generation | Core do IIC (DA-38) |
| **Hybrid RAG** | Dense + Sparse search | BM25 + embeddings (DA-38) |
| **RRF** | Reciprocal Rank Fusion | Fusion de resultados RAG (DA-38) |
| **GraphRAG** | RAG em grafo Neo4j | Opcional (DA-35) |
| **LLM Gateway** | proxy com circuit breaker | DA-26: budget e fallback |
| **Rule Engine** | Lógica pré-definida | DA-33: KNOWN_ERROR_RULES (22 regras) |
| **MCP** | Model Context Protocol | DA-19: Streamable HTTP /mcp |
| **Circuit Breaker** | Proteção contra falhas | DA-41: Redis fallback |
| **Idempotency** | Repetição segura | DA-21: Redis para replicas |

## Glossário de Termos

| Termo |	extit{sigla} | Definição |
|---|---|---|
| **Circuit Breaker** | 	extit{DA-41_ | Proteção que interrompe chamadas a serviços com falhas |
| **Context window** | 	extit{DA-20_ | Tamanho do contexto do modelo (128k tokens no qwen3.6) |
| **Corpus coverage** | 	extit{DA-58_ | Quantidade de tópicos cobertos pelo RAG |
| **DA-XX** | _DA_ | Decisão de Arquitetura formal (regulamentada em `CLAUDE.md`) |
| **Diagnóstico** | 	extit{DA-19_ | Output final do IIC com causa raiz e next steps |
| **Evidência** | 	extit{DA-15_ | Fonte de dados que sustenta o diagnóstico |
| **Fingerprint** | 	extit{DA-18_ | Hash de requisição para idempotência |
| **Gate** | 	extit{DA-51_ | Validação automática (quality gate) |
| **Hybrid search** | 	extit{DA-38_ | Combinação de Dense + Sparse search |
| **LLM gateway** | 	extit{DA-26_ | Proxy com circuit breaker + orçamento |
| **Model confidence** | 	extit{DA-20_ | Confiança do LLM (0-1) |
| **Rule Engine** | 	extit{DA-33_ | Lógica pré-definida (ex: IDoc status 51) |
| **Reranker** | 	extit{DA-29_ | Reordena resultados RAG com MiniLM v2 |
| **Retrieval score** | 	extit{DA-38_ | Pontuação de busca (BM25) |
| **RAG** | 	extit{DA-38_ | Retrieval-Augmented Generation (core do IIC) |
| **Trust level** | 	extit{DA-15_ | Nível de confiança da evidência (system_observed, simulated, retrieved) |
| **Vector database** | 	extit{DA-38_ | Qdrant para armazenar embeddings |

## Como Este Glossário é Gerado

Este documento é mantido manualmente com base em:

1. Documentos de Arquitetura (`docs/DECISOES_DE_ARQUITETURA.md`)
2. Arquivo `AGENTS.md` do repositório
3. Códigos-fonte (`app/`, `scripts/`)
4. Revisão de commits e PRs

---

**Última atualização**: 2026-10-31

"""DA-61: Carregador de taxonomia SKOS para regras de erro.

Fase 1 da DA-61: substitui KNOWN_ERROR_RULES hardcoded por carregamento dinâmico.
Carrega `app/ontology/error_codes.ttl` e retorna ErrorRule structs compatíveis
com a interface existente — zero breaking changes.

Uso:
    from app.agent.ontology_loader import load_error_rules
    rules = load_error_rules()
    for rule in rules:
        if rule.matches(text):
            return rule_to_diagnosis(rule)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

# Instalação via `uv add rdflib` no DA-61 (Fase 1)
try:
    from rdflib import Graph, Literal, URIRef
    from rdflib.namespace import RDF, SKOS
except ImportError:
    raise ImportError(
        "instale rdflib: uv add rdflib (DA-61 Fase 1)\n"
        "ou use ONTOLOGY_RULES_ENABLED=false no .env para voltar ao hardcoded"
    ) from None

# ---------------------------------------------------------------------------
# Mapeamento TTL → ErrorRule (camada de adaptação)
# ---------------------------------------------------------------------------


@dataclass
class ErrorRule:
    """Estrutura idêntica à do rules.py hardcode — mantém compatibilidade."""

    patterns: list[str]
    probable_root_cause: str
    next_steps: list[str]
    category: str
    confidence: float = 0.00
    _compiled: list[re.Pattern] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled = [re.compile(_bounded(p), re.IGNORECASE) for p in self.patterns]

    def matches(self, text: str) -> bool:
        for pattern in self._compiled:
            for match in pattern.finditer(text):
                if not _negated(text, match.start()):
                    return True
        return False


def _bounded(pattern: str) -> str:
    """Regex gap curto entre termos (ida do rules.py:55-56)."""
    _GAP = r"[^.;!?\n]{0,60}"
    return pattern.replace(".*", _GAP)


def _negated(text: str, start: int) -> bool:
    """Lógica de negação (ida do rules.py:72-73)."""
    _NEGATION_BEFORE = re.compile(
        r"\b(?:n[aã]o|nao|not|never|nunca|isn'?t|wasn'?t)\s+"
        r"(?:(?:[eé]|eh|foi|era|seria|is|was|be)\s+)?"
        r"(?:(?:um|uma|o|a|the|an?)\s+)?"
        r"(?:(?:problema|erro|caso|falha|issue|problem|error|case)\s+(?:de|do|da|of|with)\s+)?$",
        re.IGNORECASE,
    )
    return bool(_NEGATION_BEFORE.search(text[max(0, start - 60) : start]))


def _extract_patterns(g: Graph, concept: URIRef) -> list[str]:
    """Extrai regex patterns do campo skos:note (formato: "Patterns: regex1, regex2")."""
    notes = list(g.objects(concept, SKOS.note))
    patterns = []
    for note in notes:
        note_str = str(note)
        if note_str.startswith("Patterns:"):
            raw = note_str.replace("Patterns:", "").strip()
            for p in raw.split(","):
                patterns.append(p.strip())
    return patterns


def _extract_next_steps(g: Graph, concept: URIRef) -> list[str]:
    """Extrai next_steps do campo skos:note (formato: "next_steps: ação1, ação2")."""
    notes = list(g.objects(concept, SKOS.note))
    steps = []
    for note in notes:
        note_str = str(note)
        if note_str.startswith("next_steps:"):
            raw = note_str.replace("next_steps:", "").strip()
            for s in raw.split(","):
                steps.append(s.strip())
    return steps


def _extract_category(g: Graph, concept: URIRef) -> str:
    """Derive category from concept name (ex: ex:OAuthTokenExpired → oauth_token_expired)."""
    local = concept.split("#")[-1] if "#" in concept else concept.split("/")[-1]
    return local.replace(":", "_").lower()


def _extract_confidence(g: Graph, concept: URIRef) -> float:
    """Read confidence from ex:confidence property, default 0.90."""
    confs = list(g.objects(concept, URIRef("http://example.org/iic/error_codes#confidence")))
    if confs:
        return float(Literal(confs[0]))
    return 0.90


def _extract_probable_root_cause(g: Graph, concept: URIRef) -> str:
    """Read probableRootCause from concept, default empty."""
    causes = list(
        g.objects(concept, URIRef("http://example.org/iic/error_codes#probableRootCause"))
    )
    if causes:
        return str(Literal(causes[0]))
    return "Erro conhecido detectado via taxonomia SKOS."


def load_error_rules(ontology_path: Path | None = None) -> list[ErrorRule]:
    """Carrega error_rules.ttl e transforma em lista de ErrorRule.

    Args:
        ontology_path: Caminho para o TTL. Se None, usa `app/ontology/error_codes.ttl`.

    Returns:
        Lista de ErrorRule (zerobraking, compatível com rules.py).

    Raises:
        FileNotFoundError: Se o TTL não for encontrado.
        RuntimeError: Se o TTL não for parseável (SHACL não aplicado ainda, DA-61 Fase 1).
    """
    if ontology_path is None:
        ontology_path = Path(__file__).parent / "error_codes.ttl"

    _logger = logging.getLogger(__name__)

    if not ontology_path.exists():
        raise FileNotFoundError(f"ontology not found: {ontology_path}")

    g = Graph()
    try:
        g.parse(str(ontology_path), format="turtle")
    except (FileNotFoundError, RuntimeError) as e:
        raise RuntimeError(f" ontology parse failed: {e}") from None

    # Coletar todos os conceitos de erro (filtrar por skos:inPolicy=KnownErrorRules)
    known_rules = URIRef("http://example.org/iic/error_codes#KnownErrorRules")
    error_concepts = [
        member for member in g.objects(known_rules, RDF.member) if isinstance(member, URIRef)
    ]

    rules = []
    for concept in error_concepts:
        patterns = _extract_patterns(g, concept)
        next_steps = _extract_next_steps(g, concept)
        category = _extract_category(g, concept)
        confidence = _extract_confidence(g, concept)
        root_cause = _extract_probable_root_cause(g, concept)

        if not patterns:
            _logger.warning(f"skipping concept without patterns: {concept}")
            continue

        rule = ErrorRule(
            patterns=patterns,
            next_steps=next_steps,
            category=category,
            confidence=confidence,
            probable_root_cause=root_cause,
        )
        rules.append(rule)

    _logger.info("loaded %d error rules from ontology", len(rules))
    return rules


def load_error_rules_or_fallback() -> list[ErrorRule]:
    """Tenta carregar SKOS, cai para rules.py hardcoded se falhar."""
    try:
        return load_error_rules()
    except (FileNotFoundError, RuntimeError, ImportError):
        _logger = logging.getLogger(__name__)
        _logger.warning("ontology loader failed, falling back to hardcoded rules")
        from app.agent.rules import KNOWN_ERROR_RULES

        return list(KNOWN_ERROR_RULES)

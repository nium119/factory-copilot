"""Concept-to-Agent domain mapping.

Replaces the ``domain`` field that was previously embedded in each
ontology concept. The ontology should be a pure domain model — which
agent handles which concept is a deployment concern, not a modelling one.
"""

# concept name → set of agent names that handle this concept
CONCEPT_AGENT_MAP: dict[str, set[str]] = {
    "PhysicalResource": {"equipment", "andon", "workstation"},
    "Employee":         {"scheduling", "quality", "andon", "workstation"},
    "Material":         {"inventory", "production_prep", "workstation"},
    "Product":          {"scheduling", "process", "workstation"},
    "WorkOrder":        {"scheduling", "quality", "production_prep", "workstation", "monitor"},
    "Routing":          {"scheduling", "process", "production_prep", "workstation"},
    "Operation":        {"scheduling", "process", "production_prep", "workstation", "quality"},
    "Factory":          {"scheduling", "monitor"},
    "ProductionLine":   {"scheduling", "equipment", "andon", "monitor"},
    "WorkStation":      {"scheduling", "equipment", "process", "production_prep", "andon", "workstation"},
    "WorkCenter":       {"scheduling", "equipment", "production_prep", "monitor"},
    "Equipment":        {"equipment", "andon", "production_prep", "monitor"},
    "QualityCheck":     {"quality", "production_prep", "monitor"},
}

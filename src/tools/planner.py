"""Project-planning prompts that request thorough, useful reasoning."""

from __future__ import annotations

PROJECT_PLANNING_PROMPT = """You are OSIRIS, a senior project planner. Develop a rigorous, practical plan for the project below. Think deeply before answering, but provide only the useful conclusions, not private chain-of-thought. State assumptions, identify unknowns, surface risks early, and make choices with clear trade-offs. Structure the response with: objective, scope, requirements, architecture/approach, phased milestones with deliverables, dependencies, risks and mitigations, validation criteria, and the first three concrete actions."""


def build_project_planning_prompt(project: str, *, context: str = "", constraints: str = "") -> str:
    """Build a bounded planning request for a reasoning-capable LLM."""
    if not project or not project.strip():
        raise ValueError("project must be a non-empty string")
    if len(project) > 12_000 or len(context) > 12_000 or len(constraints) > 8_000:
        raise ValueError("Planning inputs are too large.")
    sections = [f"Project:\n{project.strip()}"]
    if context.strip():
        sections.append(f"Existing context:\n{context.strip()}")
    if constraints.strip():
        sections.append(f"Constraints:\n{constraints.strip()}")
    return "\n\n".join(sections)
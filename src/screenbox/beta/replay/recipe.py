"""Recipe loader and parameter substitution."""
import re
import yaml
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class RecipeStep:
    tool: str
    args: dict = field(default_factory=dict)
    extract: str | None = None      # variable name to extract from result
    extract_key: str | None = None   # JSON key to extract (default: whole result)
    delay_ms: int = 0                # delay after this step
    on_error: str = "stop"           # stop | skip | retry


@dataclass
class Recipe:
    name: str
    description: str = ""
    params: dict = field(default_factory=dict)   # param_name -> default_value
    steps: list[RecipeStep] = field(default_factory=list)
    source_file: str | None = None


def _substitute(text: str, variables: dict) -> str:
    """Replace {{var}} placeholders with variable values."""
    def replacer(m):
        key = m.group(1).strip()
        if key not in variables:
            raise KeyError(f"Unknown variable: {{{{{key}}}}}")
        return str(variables[key])
    return re.sub(r'\{\{(\s*\w+\s*)\}\}', replacer, text)


def _substitute_args(args: dict, variables: dict) -> dict:
    """Recursively substitute variables in step arguments."""
    result = {}
    for k, v in args.items():
        if isinstance(v, str):
            result[k] = _substitute(v, variables)
        elif isinstance(v, dict):
            result[k] = _substitute_args(v, variables)
        elif isinstance(v, list):
            result[k] = [_substitute(i, variables) if isinstance(i, str) else i for i in v]
        else:
            result[k] = v
    return result


def load_recipe(path: str | Path) -> Recipe:
    """Load a recipe from a YAML file."""
    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)

    steps = []
    for s in data.get("steps", []):
        steps.append(RecipeStep(
            tool=s["tool"],
            args=s.get("args", {}),
            extract=s.get("extract"),
            extract_key=s.get("extract_key"),
            delay_ms=s.get("delay_ms", 0),
            on_error=s.get("on_error", "stop"),
        ))

    return Recipe(
        name=data["name"],
        description=data.get("description", ""),
        params=data.get("params", {}),
        steps=steps,
        source_file=str(path),
    )


def resolve_steps(recipe: Recipe, user_params: dict | None = None) -> list[tuple[str, dict]]:
    """Resolve all steps with parameter substitution.

    Returns list of (tool_name, resolved_args) tuples.
    """
    variables = {**recipe.params}
    if user_params:
        variables.update(user_params)

    resolved = []
    for step in recipe.steps:
        args = _substitute_args(step.args, variables)
        resolved.append((step.tool, args, step))
    return resolved

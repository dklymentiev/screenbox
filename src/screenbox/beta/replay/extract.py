"""Extract a recipe from a console-agent session JSON log.

Usage:
    python -m screenbox.beta.replay.extract <session.json> [--output recipe.yaml]

Reads tool calls from the session, filters to desktop_* tools,
and outputs a YAML recipe skeleton.
"""
import json
import sys
import yaml
from pathlib import Path

# Tools that are meaningful in a replay (skip planning/think/screenshot)
REPLAY_TOOLS = {
    "desktop_shell", "desktop_type", "desktop_key", "desktop_click",
    "desktop_manage", "desktop_look", "desktop_batch", "desktop_scroll",
    "desktop_debug",
}

SKIP_ARGS = {"desktop_id", "intent", "step"}


def extract_steps(session_path: str | Path) -> list[dict]:
    """Extract replayable tool calls from a session JSON."""
    with open(session_path) as f:
        session = json.load(f)

    steps = []
    for msg in session.get("messages", []):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            name = func.get("name", "")
            if name not in REPLAY_TOOLS:
                continue

            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                continue

            # Remove meta args
            clean_args = {k: v for k, v in args.items() if k not in SKIP_ARGS}

            step = {"tool": name, "args": clean_args}

            # Add delay after shell/manage calls
            if name in ("desktop_shell", "desktop_manage"):
                step["delay_ms"] = 1000

            steps.append(step)

    return steps


def steps_to_recipe(steps: list[dict], name: str = "extracted",
                    description: str = "") -> dict:
    """Convert extracted steps to a recipe dict."""
    return {
        "name": name,
        "description": description or f"Extracted from session log ({len(steps)} steps)",
        "params": {},
        "steps": steps,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m screenbox.beta.replay.extract <session.json> [--output recipe.yaml]")
        sys.exit(1)

    session_path = sys.argv[1]
    output = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        output = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

    steps = extract_steps(session_path)
    recipe = steps_to_recipe(steps, name=Path(session_path).stem)

    yaml_str = yaml.dump(recipe, default_flow_style=False, sort_keys=False, allow_unicode=True)

    if output:
        Path(output).write_text(yaml_str)
        print(f"Wrote {len(steps)} steps to {output}")
    else:
        print(yaml_str)


if __name__ == "__main__":
    main()

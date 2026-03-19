"""Recipe executor — runs recipe steps as real MCP tool calls."""
import json
import time
import logging

from .recipe import Recipe, resolve_steps

log = logging.getLogger("screenbox.replay")


class ReplayResult:
    def __init__(self):
        self.steps: list[dict] = []
        self.variables: dict = {}
        self.ok: bool = True
        self.error: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "steps_total": len(self.steps),
            "steps_done": sum(1 for s in self.steps if s.get("ok")),
            "variables": self.variables,
            "steps": self.steps,
            "error": self.error,
        }


def execute_recipe(recipe: Recipe, tool_dispatch: callable,
                   desktop_id: str, user_params: dict | None = None) -> ReplayResult:
    """Execute a recipe step by step.

    Args:
        recipe: Loaded recipe object
        tool_dispatch: Function(tool_name, args) -> result_dict that calls the actual MCP tool
        desktop_id: Desktop to inject into args
        user_params: Override recipe default params
    """
    result = ReplayResult()
    variables = {**recipe.params}
    if user_params:
        variables.update(user_params)
    result.variables = dict(variables)

    resolved = resolve_steps(recipe, user_params)

    for i, (tool_name, args, step) in enumerate(resolved):
        step_info = {"step": i, "tool": tool_name, "args": args}

        # Inject desktop_id if the tool expects it and it's not set
        if "desktop_id" not in args:
            args["desktop_id"] = desktop_id

        t0 = time.time()
        try:
            tool_result = tool_dispatch(tool_name, args)
            elapsed_ms = int((time.time() - t0) * 1000)

            step_info["ok"] = True
            step_info["elapsed_ms"] = elapsed_ms

            # Parse result for extraction
            if isinstance(tool_result, str):
                try:
                    parsed = json.loads(tool_result)
                except (json.JSONDecodeError, TypeError):
                    parsed = {"raw": tool_result}
            elif isinstance(tool_result, dict):
                parsed = tool_result
            else:
                parsed = {"raw": str(tool_result)}

            step_info["result"] = _summarize(parsed)

            # Extract variable from result
            if step.extract:
                if step.extract_key:
                    value = parsed.get(step.extract_key)
                else:
                    value = parsed
                variables[step.extract] = value
                result.variables[step.extract] = value
                step_info["extracted"] = {step.extract: value}

        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            step_info["ok"] = False
            step_info["error"] = str(e)
            step_info["elapsed_ms"] = elapsed_ms

            if step.on_error == "stop":
                result.ok = False
                result.error = f"Step {i} ({tool_name}) failed: {e}"
                result.steps.append(step_info)
                break
            elif step.on_error == "skip":
                log.warning(f"Step {i} ({tool_name}) failed, skipping: {e}")

        result.steps.append(step_info)

        # Delay between steps
        if step.delay_ms > 0:
            time.sleep(step.delay_ms / 1000.0)

    return result


def _summarize(parsed: dict, max_len: int = 200) -> dict | str:
    """Summarize a tool result to avoid bloating the replay log."""
    # Remove base64 image data
    if isinstance(parsed, dict):
        clean = {}
        for k, v in parsed.items():
            if isinstance(v, str) and len(v) > max_len:
                clean[k] = v[:100] + f"... ({len(v)} chars)"
            else:
                clean[k] = v
        return clean
    return parsed

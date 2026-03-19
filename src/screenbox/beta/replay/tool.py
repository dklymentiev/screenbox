"""desktop_replay MCP tool — register on the FastMCP instance."""
import json
import time
from pathlib import Path

from .recipe import load_recipe
from .executor import execute_recipe


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    def _get_recipes_dir(manager) -> Path:
        """Recipes stored in ~/.screenbox/recipes/"""
        d = manager.config.base_dir / "recipes"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _tool_dispatch(get_desktop_fn, get_manager_fn, log_action_fn, app_catalog_ref):
        """Build a dispatch function that calls real MCP tool implementations."""

        def dispatch(tool_name: str, args: dict) -> str:
            desktop_id = args.pop("desktop_id", None)
            d = get_desktop_fn(desktop_id) if desktop_id else None

            if tool_name == "desktop_shell":
                result = d.shell(args["command"], args.get("timeout", 30))
                return json.dumps(result)
            elif tool_name == "desktop_type":
                d.type_text(args["text"], args.get("delay", 0))
                return json.dumps({"typed": True, "length": len(args["text"])})
            elif tool_name == "desktop_key":
                d.key(args["keys"])
                return json.dumps({"pressed": True, "keys": args["keys"]})
            elif tool_name == "desktop_manage":
                action = args["action"]
                mgr = get_manager_fn()
                if action == "wait_idle":
                    timeout = args.get("timeout_ms", 5000)
                    time.sleep(timeout / 1000.0)
                    return json.dumps({"waited": True, "timeout_ms": timeout})
                elif action == "resume":
                    return json.dumps(mgr.resume(desktop_id) if desktop_id else {"error": "no desktop_id"})
                else:
                    return json.dumps({"error": f"unsupported manage action: {action}"})
            elif tool_name == "desktop_look":
                cell = args.get("cell")
                if cell is not None:
                    result = d.look_at_cell(int(cell))
                    return json.dumps(result)
                return json.dumps({"error": "desktop_look requires cell"})
            elif tool_name == "desktop_screenshot":
                img_b64 = d.screenshot_base64(quality=args.get("quality", 50))
                return json.dumps({"screenshot": True, "size": len(img_b64)})
            elif tool_name == "desktop_click":
                x, y = args["x"], args["y"]
                d.click(x, y, args.get("button", "left"))
                return json.dumps({"clicked": True, "x": x, "y": y})
            elif tool_name == "sleep":
                ms = args.get("ms", 1000)
                time.sleep(ms / 1000.0)
                return json.dumps({"slept": True, "ms": ms})
            else:
                raise ValueError(f"Unknown tool in recipe: {tool_name}")

        return dispatch

    @mcp.tool()
    def desktop_replay(desktop_id: str, recipe: str, params: str = "{}",
                       intent: str = "", step: str = "") -> str:
        """[BETA] Execute a saved recipe — deterministic replay without LLM.

        Recipes are YAML files in ~/.screenbox/recipes/.
        Each recipe defines parameterized steps (tool calls) that run sequentially.

        Args:
            desktop_id: Desktop to run on
            recipe: Recipe name (without .yaml extension)
            params: JSON object overriding recipe default parameters
        """
        t0 = time.time()
        manager = get_manager()
        recipes_dir = _get_recipes_dir(manager)

        recipe_path = recipes_dir / f"{recipe}.yaml"
        if not recipe_path.exists():
            return json.dumps({"error": f"Recipe not found: {recipe_path}"})

        try:
            loaded = load_recipe(recipe_path)
        except Exception as e:
            return json.dumps({"error": f"Failed to load recipe: {e}"})

        user_params = json.loads(params) if isinstance(params, str) else params

        dispatch = _tool_dispatch(get_desktop, get_manager, log_action, app_catalog)
        result = execute_recipe(loaded, dispatch, desktop_id, user_params)

        output = result.to_dict()
        output["recipe"] = recipe
        output["total_ms"] = int((time.time() - t0) * 1000)

        log_action(desktop_id, "desktop_replay",
                   {"recipe": recipe, "params": user_params},
                   {"ok": result.ok, "steps": len(result.steps)},
                   t0, intent=intent, step=step)

        return json.dumps(output, indent=2)

    @mcp.tool()
    def desktop_recipes(intent: str = "", step: str = "") -> str:
        """[BETA] List available recipes.

        Returns names and descriptions of all YAML recipes in ~/.screenbox/recipes/.
        """
        manager = get_manager()
        recipes_dir = _get_recipes_dir(manager)

        recipes = []
        for f in sorted(recipes_dir.glob("*.yaml")):
            try:
                loaded = load_recipe(f)
                recipes.append({
                    "name": loaded.name,
                    "description": loaded.description,
                    "params": loaded.params,
                    "steps": len(loaded.steps),
                })
            except Exception as e:
                recipes.append({"name": f.stem, "error": str(e)})

        return json.dumps({"recipes": recipes, "dir": str(recipes_dir)}, indent=2)

#!/usr/bin/env python3
"""Generate Screenbox technical reference from source code.

Parses Python source, extracts tools/API/config/modules, outputs:
  docs/reference.json  -- structured data
  docs/reference.md    -- human-readable markdown (with --markdown flag)

Usage:
  python3 scripts/gen-docs.py              # JSON only
  python3 scripts/gen-docs.py --markdown   # JSON + Markdown
"""

import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "screenbox"
DOCS = ROOT / "docs"


# ── 1. Extract MCP Tools ──

def extract_tools():
    """Parse tool definitions from tools/*.py files."""
    tools = []
    tools_dir = SRC / "tools"
    if not tools_dir.exists():
        return tools

    for py_file in sorted(tools_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        category = py_file.stem  # e.g. "manage", "chrome", "screenshot"
        source = py_file.read_text()

        # Find @mcp.tool() decorated functions or tool registration
        # Pattern 1: def function with docstring containing tool description
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("desktop_"):
                doc = ast.get_docstring(node) or ""
                params = []
                for arg in node.args.args:
                    if arg.arg in ("self", "ctx"):
                        continue
                    annotation = ""
                    if arg.annotation:
                        annotation = ast.unparse(arg.annotation)
                    params.append({"name": arg.arg, "type": annotation})
                tools.append({
                    "name": node.name,
                    "description": doc.split("\n")[0] if doc else "",
                    "category": category,
                    "parameters": params,
                    "source": f"src/screenbox/tools/{py_file.name}:{node.lineno}",
                })

        # Pattern 2: Parse docstring of the tool function for parameter descriptions
        # Look for the register() function's inner tool definitions
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                doc = ast.get_docstring(node) or ""
                if not doc or node.name.startswith("_"):
                    continue
                # Extract from function signature
                params = []
                for arg in node.args.args:
                    if arg.arg in ("self", "ctx"):
                        continue
                    annotation = ""
                    if arg.annotation:
                        try:
                            annotation = ast.unparse(arg.annotation)
                        except Exception:
                            pass
                    params.append({"name": arg.arg, "type": annotation})

                # Skip if already found
                if any(t["name"] == node.name for t in tools):
                    continue
                tools.append({
                    "name": node.name,
                    "description": doc.split("\n")[0] if doc else "",
                    "category": category,
                    "parameters": params,
                    "source": f"src/screenbox/tools/{py_file.name}:{node.lineno}",
                })

    # Also parse the __init__.py for tool registration names
    init_file = tools_dir / "__init__.py"
    if init_file.exists():
        source = init_file.read_text()
        # Find mcp.tool() calls with name= parameter
        tool_names = re.findall(r'name="(desktop_\w+)"', source)
        # Find description from docstring-like patterns
        tool_descs = re.findall(r'description="([^"]+)"', source)

        # Merge with already found tools
        existing = {t["name"] for t in tools}
        for i, name in enumerate(tool_names):
            if name not in existing:
                desc = tool_descs[i] if i < len(tool_descs) else ""
                tools.append({
                    "name": name,
                    "description": desc,
                    "category": "registered",
                    "parameters": [],
                    "source": f"src/screenbox/tools/__init__.py",
                })

    return tools


# ── 2. Extract API Endpoints ──

def extract_api():
    """Parse HTTP API endpoints from http_api.py."""
    endpoints = []
    api_file = SRC / "http_api.py"
    if not api_file.exists():
        return endpoints

    source = api_file.read_text()

    # Pattern: @mcp.custom_route("/api/path", methods=["METHOD"])
    route_re = re.compile(
        r'@mcp\.custom_route\("(/[^"]+)".*?methods=\["(\w+)"\].*?\)\s*\n'
        r'\s*async def (\w+)\(.*?\).*?:\s*\n\s*"""([^"]*?)"""',
        re.DOTALL
    )

    for m in route_re.finditer(source):
        endpoints.append({
            "path": m.group(1),
            "method": m.group(2),
            "handler": m.group(3),
            "description": m.group(4).strip().split("\n")[0],
        })

    return endpoints


# ── 3. Extract Config ──

def extract_config():
    """Parse configuration from config.py and docker-compose.yml."""
    configs = []

    # config.py
    config_file = SRC / "config.py"
    if config_file.exists():
        source = config_file.read_text()
        # Pattern: os.environ.get("VAR", "default") or os.environ.get("VAR")
        env_re = re.compile(
            r'os\.environ\.get\("(\w+)"(?:,\s*"?([^)"]*)"?)?\)',
        )
        for m in env_re.finditer(source):
            var = m.group(1)
            default = m.group(2) or ""
            # Find surrounding context for description
            line_start = source.rfind("\n", 0, m.start()) + 1
            line = source[line_start:source.find("\n", m.end())]
            comment = ""
            cm = re.search(r"#\s*(.+)", line)
            if cm:
                comment = cm.group(1).strip()
            configs.append({
                "env_var": var,
                "default": default.strip('"'),
                "description": comment,
                "source": "config.py",
            })

    # docker-compose.yml
    compose_file = ROOT / "docker-compose.yml"
    if compose_file.exists():
        source = compose_file.read_text()
        env_re = re.compile(r"-\s+(\w+)=(.+)")
        for m in env_re.finditer(source):
            var = m.group(1)
            val = m.group(2).strip()
            if var.startswith("SCREENBOX_") and not any(c["env_var"] == var for c in configs):
                configs.append({
                    "env_var": var,
                    "default": val,
                    "description": "",
                    "source": "docker-compose.yml",
                })

    return configs


# ── 4. Module Graph ──

def build_module_graph():
    """Build import graph between src/screenbox/ modules."""
    modules = {}

    if not SRC.exists():
        return modules

    for py_file in sorted(SRC.rglob("*.py")):
        rel = str(py_file.relative_to(ROOT))
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        imports = []
        exports = []
        classes = []
        functions = []

        for node in ast.walk(tree):
            # Imports
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(".") or "screenbox" in node.module:
                    names = [a.name for a in node.names] if node.names else []
                    imports.append({"from": node.module, "names": names})

            # Top-level functions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.col_offset == 0:  # top-level
                    doc = ast.get_docstring(node) or ""
                    functions.append({
                        "name": node.name,
                        "line": node.lineno,
                        "doc": doc.split("\n")[0] if doc else "",
                    })

            # Classes
            if isinstance(node, ast.ClassDef) and node.col_offset == 0:
                doc = ast.get_docstring(node) or ""
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not item.name.startswith("_"):
                            mdoc = ast.get_docstring(item) or ""
                            methods.append({
                                "name": item.name,
                                "line": item.lineno,
                                "doc": mdoc.split("\n")[0] if mdoc else "",
                            })
                classes.append({
                    "name": node.name,
                    "line": node.lineno,
                    "doc": doc.split("\n")[0] if doc else "",
                    "methods": methods,
                })

        # Classify layer
        layer = classify_layer(rel)

        modules[rel] = {
            "layer": layer,
            "imports": imports,
            "classes": classes,
            "functions": [f for f in functions if not f["name"].startswith("_")],
            "lines": len(py_file.read_text().split("\n")),
        }

    return modules


def classify_layer(rel):
    if "tools/" in rel:
        return "tools"
    if "desktop/" in rel:
        return "desktop"
    if "beta/" in rel:
        return "beta"
    if rel.endswith("server.py"):
        return "server"
    if rel.endswith("manager.py"):
        return "manager"
    if rel.endswith("config.py"):
        return "config"
    if rel.endswith("http_api.py"):
        return "api"
    if rel.endswith("registry.py"):
        return "registry"
    if rel.endswith("knowledge.py"):
        return "knowledge"
    if rel.endswith("browser.py"):
        return "browser"
    if "auth" in rel:
        return "auth"
    return "core"


# ── 5. Profile Schema ──

def extract_profile_schema():
    """Extract profile JSON schema from test-profile.json."""
    profile_dir = ROOT / "profiles"
    schema = {}
    for f in profile_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            # Build schema from actual data
            for section, values in data.items():
                if isinstance(values, dict):
                    if section not in schema:
                        schema[section] = {}
                    for k, v in values.items():
                        schema[section][k] = {
                            "type": type(v).__name__,
                            "example": v if not isinstance(v, list) else v[:3],
                        }
        except Exception:
            pass
    return schema


# ── 6. Desktop Image Features ──

def extract_desktop_features():
    """Parse entrypoint.sh for desktop features."""
    features = []
    entrypoint = ROOT / "docker" / "entrypoint.sh"
    if not entrypoint.exists():
        return features

    source = entrypoint.read_text()
    # Find numbered sections: # N. Feature name
    section_re = re.compile(r"#\s*(\d+)\.\s*(.+)")
    for m in section_re.finditer(source):
        features.append({"step": int(m.group(1)), "name": m.group(2).strip()})

    return features


# ── Build Reference ──

def build_reference():
    tools = extract_tools()
    api = extract_api()
    config = extract_config()
    modules = build_module_graph()
    profile_schema = extract_profile_schema()
    desktop_features = extract_desktop_features()

    # Package info
    pyproject = ROOT / "pyproject.toml"
    version = "unknown"
    if pyproject.exists():
        vm = re.search(r'version\s*=\s*"([^"]+)"', pyproject.read_text())
        if vm:
            version = vm.group(1)

    # Aggregate module stats
    total_lines = sum(m["lines"] for m in modules.values())
    total_classes = sum(len(m["classes"]) for m in modules.values())
    total_functions = sum(len(m["functions"]) for m in modules.values())

    # Group tools by category
    tools_by_cat = {}
    for t in tools:
        cat = t["category"]
        if cat not in tools_by_cat:
            tools_by_cat[cat] = []
        tools_by_cat[cat].append(t)

    # Layer summary
    layers = {}
    for path, info in modules.items():
        layer = info["layer"]
        if layer not in layers:
            layers[layer] = {"files": [], "total_lines": 0, "classes": [], "functions": []}
        layers[layer]["files"].append(path)
        layers[layer]["total_lines"] += info["lines"]
        layers[layer]["classes"].extend(info["classes"])
        layers[layer]["functions"].extend(info["functions"])

    return {
        "_generated": __import__("datetime").datetime.now().isoformat(),
        "_generator": "scripts/gen-docs.py",
        "package": {"name": "screenbox-mcp", "version": version},
        "stats": {
            "files": len(modules),
            "lines": total_lines,
            "classes": total_classes,
            "functions": total_functions,
            "tools": len(tools),
            "api_endpoints": len(api),
            "config_vars": len(config),
        },
        "architecture": {"layers": layers, "modules": modules},
        "tools": {"all": tools, "by_category": tools_by_cat},
        "api": {"endpoints": api},
        "config": config,
        "profile_schema": profile_schema,
        "desktop_features": desktop_features,
    }


# ── Generate Markdown ──

def to_markdown(ref):
    lines = []
    h = lambda level, text: lines.append(f"{'#' * level} {text}\n")
    p = lambda text: lines.append(f"{text}\n")

    h(1, f"Screenbox v{ref['package']['version']} -- Technical Reference")
    p(f"> Auto-generated by `{ref['_generator']}` on {ref['_generated'][:10]}")
    p(f"> {ref['stats']['files']} files, {ref['stats']['lines']} lines, "
      f"{ref['stats']['tools']} tools, {ref['stats']['api_endpoints']} API endpoints")
    p("---\n")

    # Architecture
    h(2, "Architecture")
    p("```")
    for layer, info in sorted(ref["architecture"]["layers"].items(),
                               key=lambda x: -x[1]["total_lines"]):
        lines.append(f"  {layer:<16} {len(info['files']):>3} files  {info['total_lines']:>5} lines  "
                     f"{len(info['classes'])} classes  {len(info['functions'])} functions")
    p("```\n")

    # Module details
    for layer, info in sorted(ref["architecture"]["layers"].items()):
        h(3, layer.title())
        for f in info["files"]:
            mod = ref["architecture"]["modules"][f]
            p(f"**`{f}`** ({mod['lines']} lines)")
            for cls in mod["classes"]:
                methods = ", ".join(m["name"] for m in cls["methods"][:10])
                more = f" +{len(cls['methods'])-10}" if len(cls["methods"]) > 10 else ""
                p(f"- class `{cls['name']}`: {cls['doc']}")
                if methods:
                    p(f"  Methods: {methods}{more}")
            for fn in mod["functions"][:5]:
                p(f"- `{fn['name']}()`: {fn['doc']}")
            p("")
    p("---\n")

    # Tools
    h(2, "MCP Tools")
    for cat, cat_tools in ref["tools"]["by_category"].items():
        h(3, cat.replace("_", " ").title())
        lines.append("| Tool | Description | Parameters |")
        lines.append("|------|-------------|------------|")
        for t in cat_tools:
            params = ", ".join(f"`{p['name']}`" for p in t["parameters"][:5])
            desc = t["description"][:80]
            lines.append(f"| `{t['name']}` | {desc} | {params} |")
        p("")
    p("---\n")

    # API
    h(2, "HTTP API")
    lines.append("| Method | Path | Description |")
    lines.append("|--------|------|-------------|")
    for e in ref["api"]["endpoints"]:
        lines.append(f"| {e['method']} | `{e['path']}` | {e['description']} |")
    p("\n---\n")

    # Config
    h(2, "Configuration")
    lines.append("| Variable | Default | Source | Description |")
    lines.append("|----------|---------|--------|-------------|")
    for c in ref["config"]:
        lines.append(f"| `{c['env_var']}` | `{c['default']}` | {c['source']} | {c['description']} |")
    p("\n---\n")

    # Profile Schema
    if ref["profile_schema"]:
        h(2, "Desktop Profile Schema")
        for section, fields in ref["profile_schema"].items():
            h(3, section)
            lines.append("| Field | Type | Example |")
            lines.append("|-------|------|---------|")
            for field, info in fields.items():
                example = str(info["example"])[:50]
                lines.append(f"| `{field}` | {info['type']} | `{example}` |")
            p("")

    # Desktop Features
    if ref["desktop_features"]:
        h(2, "Desktop Entrypoint Steps")
        for f in ref["desktop_features"]:
            p(f"{f['step']}. {f['name']}")

    return "\n".join(lines)


# ── Main ──

if __name__ == "__main__":
    ref = build_reference()
    DOCS.mkdir(exist_ok=True)

    # Always write JSON
    json_path = DOCS / "reference.json"
    json_path.write_text(json.dumps(ref, indent=2, default=str) + "\n")
    print(f"  reference.json: {ref['stats']['tools']} tools, "
          f"{ref['stats']['api_endpoints']} endpoints, "
          f"{ref['stats']['files']} modules")

    # Write markdown if --markdown
    if "--markdown" in sys.argv:
        md_path = DOCS / "reference.md"
        md_path.write_text(to_markdown(ref))
        print(f"  reference.md: written")

    # Print summary
    print(f"  architecture: {ref['stats']['files']} files, {ref['stats']['lines']} lines")
    for layer, info in sorted(ref["architecture"]["layers"].items(),
                               key=lambda x: -x[1]["total_lines"]):
        print(f"    {layer:<16} [{len(info['files'])}] {info['total_lines']} lines")
    print(f"  Output: {DOCS}/")

"""
extractor.py — ChaseOS Graph Substrate: Deterministic Extraction Layer

Three extractors for pass 1:

1. PythonExtractor
   Uses stdlib `ast` to parse Python source files.
   Extracts: file nodes, import nodes, class definitions, function definitions.
   Edges: imports, defines, inherits, file_contains.
   No semantic inference — only what is directly present in the AST.

2. YAMLManifestExtractor
   Parses YAML workflow manifests in runtime/workflows/registry/.
   Extracts: workflow nodes, manifest_field nodes.
   Edges: workflow_declares (workflow → field), workflow_links_file (manifest → handler path).

3. MarkdownExtractor
   Parses markdown files for wikilinks, headings, and frontmatter.
   Extracts: doc_section nodes, wikilink_ref nodes.
   Edges: references (section → wikilink target), file_contains (file → section).

Design rule:
  Extractors emit GraphNode and GraphEdge dataclasses.
  They do NOT mutate any global state.
  They do NOT call LLMs or external services.
  They do NOT make inferences — only EXTRACTED confidence unless explicitly noted.

The extraction result is a list of nodes and edges.
The builder merges and deduplicates across extractors before assembling the snapshot.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

from .artifact import (
    GraphNode, GraphEdge,
    NodeType, Relation, Confidence,
    make_node, make_edge, make_node_id,
)


# ── Extraction result container ───────────────────────────────────────────────

@dataclass
class ExtractionResult:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)

    def extend(self, other: "ExtractionResult") -> None:
        self.nodes.extend(other.nodes)
        self.edges.extend(other.edges)
        self.errors.extend(other.errors)
        self.source_files.extend(other.source_files)


# ── Domain inference ──────────────────────────────────────────────────────────

_DOMAIN_PATTERNS = [
    ("aor",        r"runtime/aor"),
    ("capture",    r"runtime/capture"),
    ("graph",      r"runtime/graph"),
    ("sic",        r"runtime/source_intelligence"),
    ("cli",        r"runtime/cli"),
    ("workflows",  r"runtime/workflows"),
    ("policy",     r"runtime/policy"),
    ("openclaw",   r"runtime/openclaw"),
    ("agents",     r"06_AGENTS"),
    ("projects",   r"01_PROJECTS"),
    ("knowledge",  r"02_KNOWLEDGE"),
    ("sops",       r"04_SOPS"),
    ("home",       r"00_HOME"),
]

def _infer_domain(rel_path: str) -> Optional[str]:
    for domain, pattern in _DOMAIN_PATTERNS:
        if re.search(pattern, rel_path.replace("\\", "/")):
            return domain
    return None


def _rel(path: Path, vault_root: Path) -> str:
    return str(path.relative_to(vault_root)).replace("\\", "/")


def _coerce_yaml_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if ":" in value and not re.match(r"^[A-Za-z0-9_.\-/]+$", value):
        raise ValueError(f"Unsupported inline YAML scalar: {value!r}")
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_block_scalar(lines: list[str], start: int, indent: int, folded: bool) -> tuple[str, int]:
    parts: list[str] = []
    i = start
    while i < len(lines):
        raw = lines[i].rstrip("\n")
        stripped = raw.strip()
        current_indent = len(raw) - len(raw.lstrip(" "))
        if not stripped:
            parts.append("")
            i += 1
            continue
        if stripped.startswith("#"):
            i += 1
            continue
        if current_indent < indent:
            break
        parts.append(raw[indent:])
        i += 1
    if folded:
        return " ".join(part for part in parts if part != "").strip(), i
    return "\n".join(parts).strip(), i


def _next_yaml_content_index(lines: list[str], start: int) -> int:
    j = start
    while j < len(lines):
        s = lines[j].strip()
        if s and not s.startswith("#") and s != "---":
            return j
        j += 1
    return j


def _parse_yaml_block(lines: list[str], start: int, indent: int) -> tuple[Any, int]:
    i = _next_yaml_content_index(lines, start)
    if i >= len(lines):
        return {}, i

    raw = lines[i].rstrip()
    current_indent = len(raw) - len(raw.lstrip(" "))
    stripped = raw.strip()
    if current_indent < indent:
        return {}, i

    if stripped.startswith("- ") and current_indent == indent:
        items: list[Any] = []
        while i < len(lines):
            i = _next_yaml_content_index(lines, i)
            if i >= len(lines):
                break
            raw = lines[i].rstrip()
            current_indent = len(raw) - len(raw.lstrip(" "))
            stripped = raw.strip()
            if current_indent != indent or not stripped.startswith("- "):
                break
            item_value = stripped[2:].strip()
            if not item_value:
                child, next_i = _parse_yaml_block(lines, i + 1, indent + 2)
                items.append(child)
                i = next_i
                continue
            if ":" not in item_value:
                items.append(_coerce_yaml_scalar(item_value))
                i += 1
                continue
            item_key, item_rest = item_value.split(":", 1)
            item_key = item_key.strip()
            item_rest = item_rest.strip()
            entry: dict[str, Any] = {}
            if item_rest in {">", "|"}:
                scalar, next_i = _parse_block_scalar(lines, i + 1, indent + 2, folded=item_rest == ">")
                entry[item_key] = scalar
                i = next_i
            elif item_rest:
                entry[item_key] = _coerce_yaml_scalar(item_rest)
                i += 1
            else:
                child, next_i = _parse_yaml_block(lines, i + 1, indent + 2)
                entry[item_key] = child
                i = next_i
            while i < len(lines):
                j = _next_yaml_content_index(lines, i)
                if j >= len(lines):
                    i = j
                    break
                raw2 = lines[j].rstrip()
                indent2 = len(raw2) - len(raw2.lstrip(" "))
                stripped2 = raw2.strip()
                if indent2 < indent + 2:
                    break
                if indent2 != indent + 2 or ":" not in stripped2:
                    raise ValueError(f"Unsupported YAML syntax on line {j + 1}: {raw2}")
                subkey, subrest = stripped2.split(":", 1)
                subkey = subkey.strip()
                subrest = subrest.strip()
                if subrest in {">", "|"}:
                    scalar, next_i = _parse_block_scalar(lines, j + 1, indent + 4, folded=subrest == ">")
                    entry[subkey] = scalar
                    i = next_i
                elif subrest:
                    entry[subkey] = _coerce_yaml_scalar(subrest)
                    i = j + 1
                else:
                    child, next_i = _parse_yaml_block(lines, j + 1, indent + 4)
                    entry[subkey] = child
                    i = next_i
            items.append(entry)
        return items, i

    mapping: dict[str, Any] = {}
    while i < len(lines):
        i = _next_yaml_content_index(lines, i)
        if i >= len(lines):
            break
        raw = lines[i].rstrip()
        current_indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if current_indent < indent:
            break
        if current_indent != indent or ":" not in stripped:
            raise ValueError(f"Unsupported YAML syntax on line {i + 1}: {raw}")
        key, rest = stripped.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        if rest in {">", "|"}:
            scalar, next_i = _parse_block_scalar(lines, i + 1, indent + 2, folded=rest == ">")
            mapping[key] = scalar
            i = next_i
        elif rest:
            mapping[key] = _coerce_yaml_scalar(rest)
            i += 1
        else:
            child, next_i = _parse_yaml_block(lines, i + 1, indent + 2)
            mapping[key] = child
            i = next_i
    return mapping, i


def _parse_yaml_mapping(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    result, _ = _parse_yaml_block(lines, 0, 0)
    if not isinstance(result, dict):
        raise ValueError("Top-level YAML document is not a mapping")
    return result


def _extract_frontmatter_mapping(text: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        data = _parse_yaml_mapping(match.group(1))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# ── Python extractor ──────────────────────────────────────────────────────────

class PythonExtractor:
    """
    Deterministic extraction from Python source files using stdlib ast.

    Pass 1 scope:
    - File nodes for each .py file
    - Import nodes for each imported module
    - Class nodes for top-level class definitions
    - Function nodes for top-level function definitions and class methods
    - Edges: imports, defines, inherits, file_contains

    Not in pass 1:
    - Call graph inference (too expensive / too noisy without type resolution)
    - Cross-file call edges (deferred to pass 2)
    """

    def extract_file(self, path: Path, vault_root: Path) -> ExtractionResult:
        result = ExtractionResult()
        rel = _rel(path, vault_root)
        domain = _infer_domain(rel)

        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            result.errors.append(f"syntax error in {rel}: {exc}")
            return result
        except OSError as exc:
            result.errors.append(f"could not read {rel}: {exc}")
            return result

        result.source_files.append(rel)

        # File node
        file_node = make_node(
            label=rel,
            node_type=NodeType.FILE,
            source_file=rel,
            domain=domain,
            properties={"language": "python", "lines": len(source.splitlines())},
            confidence=Confidence.EXTRACTED,
            provenance="python_ast:file",
        )
        result.nodes.append(file_node)

        # Walk top-level statements
        for node in ast.iter_child_nodes(tree):

            # Imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name
                    import_node = make_node(
                        label=module_name,
                        node_type=NodeType.PYTHON_IMPORT,
                        source_file=rel,
                        source_line=node.lineno,
                        domain=domain,
                        properties={"import_style": "import", "alias": alias.asname or ""},
                        confidence=Confidence.EXTRACTED,
                        provenance="python_ast:import",
                    )
                    result.nodes.append(import_node)
                    result.edges.append(make_edge(
                        file_node.node_id,
                        import_node.node_id,
                        Relation.IMPORTS,
                        provenance="python_ast:import",
                    ))

            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or "(relative)"
                for alias in node.names:
                    symbol = f"{module_name}.{alias.name}" if alias.name != "*" else f"{module_name}.*"
                    import_node = make_node(
                        label=symbol,
                        node_type=NodeType.PYTHON_IMPORT,
                        source_file=rel,
                        source_line=node.lineno,
                        domain=domain,
                        properties={
                            "import_style": "from",
                            "module": module_name,
                            "symbol": alias.name,
                            "alias": alias.asname or "",
                            "level": node.level,
                        },
                        confidence=Confidence.EXTRACTED,
                        provenance="python_ast:from_import",
                    )
                    result.nodes.append(import_node)
                    result.edges.append(make_edge(
                        file_node.node_id,
                        import_node.node_id,
                        Relation.IMPORTS,
                        provenance="python_ast:from_import",
                    ))

            # Class definitions
            elif isinstance(node, ast.ClassDef):
                class_node = make_node(
                    label=node.name,
                    node_type=NodeType.PYTHON_CLASS,
                    source_file=rel,
                    source_line=node.lineno,
                    domain=domain,
                    properties={
                        "bases": [_ast_name(b) for b in node.bases],
                        "decorator_count": len(node.decorator_list),
                    },
                    confidence=Confidence.EXTRACTED,
                    provenance="python_ast:classdef",
                )
                result.nodes.append(class_node)
                result.edges.append(make_edge(
                    file_node.node_id, class_node.node_id,
                    Relation.FILE_CONTAINS,
                    provenance="python_ast:classdef",
                ))
                result.edges.append(make_edge(
                    file_node.node_id, class_node.node_id,
                    Relation.DEFINES,
                    provenance="python_ast:classdef",
                ))

                # Inheritance edges (INFERRED — base may not be in scope)
                for base in node.bases:
                    base_name = _ast_name(base)
                    if not base_name or base_name in ("object",):
                        continue
                    # Create a placeholder node for the base class
                    base_node = make_node(
                        label=base_name,
                        node_type=NodeType.PYTHON_CLASS,
                        source_file=rel,  # source attribution is the current file
                        domain=domain,
                        properties={"is_base_reference": True},
                        confidence=Confidence.INFERRED,
                        provenance="python_ast:base_class_ref",
                    )
                    result.nodes.append(base_node)
                    result.edges.append(make_edge(
                        class_node.node_id, base_node.node_id,
                        Relation.INHERITS,
                        confidence=Confidence.INFERRED,
                        provenance="python_ast:base_class_ref",
                    ))

                # Methods
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_node = make_node(
                            label=f"{node.name}.{item.name}",
                            node_type=NodeType.PYTHON_FUNCTION,
                            source_file=rel,
                            source_line=item.lineno,
                            domain=domain,
                            properties={
                                "is_method": True,
                                "class_name": node.name,
                                "is_async": isinstance(item, ast.AsyncFunctionDef),
                                "arg_count": len(item.args.args),
                            },
                            confidence=Confidence.EXTRACTED,
                            provenance="python_ast:methoddef",
                        )
                        result.nodes.append(method_node)
                        result.edges.append(make_edge(
                            class_node.node_id, method_node.node_id,
                            Relation.DEFINES,
                            provenance="python_ast:methoddef",
                        ))

            # Top-level function definitions
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_node = make_node(
                    label=node.name,
                    node_type=NodeType.PYTHON_FUNCTION,
                    source_file=rel,
                    source_line=node.lineno,
                    domain=domain,
                    properties={
                        "is_method": False,
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                        "arg_count": len(node.args.args),
                        "decorator_count": len(node.decorator_list),
                    },
                    confidence=Confidence.EXTRACTED,
                    provenance="python_ast:funcdef",
                )
                result.nodes.append(func_node)
                result.edges.append(make_edge(
                    file_node.node_id, func_node.node_id,
                    Relation.FILE_CONTAINS,
                    provenance="python_ast:funcdef",
                ))
                result.edges.append(make_edge(
                    file_node.node_id, func_node.node_id,
                    Relation.DEFINES,
                    provenance="python_ast:funcdef",
                ))

        return result

    def extract_directory(
        self,
        directory: Path,
        vault_root: Path,
        *,
        exclude_patterns: Optional[list[str]] = None,
    ) -> ExtractionResult:
        """Extract all .py files in a directory (recursive)."""
        result = ExtractionResult()
        exclude = set(exclude_patterns or [])
        for py_file in sorted(directory.rglob("*.py")):
            rel = _rel(py_file, vault_root)
            if any(pat in rel for pat in exclude):
                continue
            result.extend(self.extract_file(py_file, vault_root))
        return result


def _ast_name(node: ast.expr) -> str:
    """Extract a string name from an AST name/attribute node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _ast_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


# ── YAML manifest extractor ───────────────────────────────────────────────────

class YAMLManifestExtractor:
    """
    Extraction from YAML workflow manifests.

    Pass 1 scope: runtime/workflows/registry/*.yaml
    Extracts: workflow nodes, key manifest field nodes.
    Edges: workflow_declares, workflow_links_file.
    """

    # Fields worth extracting as explicit nodes
    _INTERESTING_FIELDS = frozenset([
        "id", "role_card", "task_type", "permission_ceiling",
        "writeback_targets", "required_reads", "handler",
    ])

    def extract_file(self, path: Path, vault_root: Path) -> ExtractionResult:
        result = ExtractionResult()
        rel = _rel(path, vault_root)
        domain = _infer_domain(rel)

        try:
            raw = path.read_text(encoding="utf-8")
            data = _parse_yaml_mapping(raw)
        except (ValueError, OSError) as exc:
            result.errors.append(f"could not parse {rel}: {exc}")
            return result

        result.source_files.append(rel)
        workflow_id = str(data.get("id", path.stem))
        workflow_status = str(data.get("status", "unknown"))

        workflow_node = make_node(
            label=workflow_id,
            node_type=NodeType.WORKFLOW,
            source_file=rel,
            domain=domain,
            properties={
                "status": workflow_status,
                "title": str(data.get("title", "")),
                "task_type": str(data.get("task_type", "")),
                "role_card": str(data.get("role_card", "")),
                "permission_ceiling": str(data.get("permission_ceiling", "")),
            },
            confidence=Confidence.EXTRACTED,
            provenance="yaml_manifest:workflow",
        )
        result.nodes.append(workflow_node)

        # Key field nodes
        for field_name in self._INTERESTING_FIELDS:
            raw_value = data.get(field_name)
            if raw_value is None:
                continue

            # Normalize value to a string representation
            if isinstance(raw_value, list):
                value_str = ", ".join(str(v) for v in raw_value)
            else:
                value_str = str(raw_value)

            field_node = make_node(
                label=f"{workflow_id}.{field_name}",
                node_type=NodeType.MANIFEST_FIELD,
                source_file=rel,
                domain=domain,
                properties={"field_name": field_name, "value": value_str},
                confidence=Confidence.EXTRACTED,
                provenance="yaml_manifest:field",
            )
            result.nodes.append(field_node)
            result.edges.append(make_edge(
                workflow_node.node_id, field_node.node_id,
                Relation.WORKFLOW_DECLARES,
                provenance="yaml_manifest:field",
            ))

        # If manifest declares a handler file, create a link edge
        handler = data.get("handler", "")
        if handler:
            # Handler is a Python path like "runtime.workflows.operator_today"
            handler_rel = handler.replace(".", "/") + ".py"
            handler_node_id = make_node_id(NodeType.FILE, handler_rel, handler_rel)
            result.edges.append(make_edge(
                workflow_node.node_id, handler_node_id,
                Relation.WORKFLOW_LINKS_FILE,
                confidence=Confidence.INFERRED,
                properties={"handler": handler, "inferred_path": handler_rel},
                provenance="yaml_manifest:handler_inference",
            ))

        return result

    def extract_directory(self, directory: Path, vault_root: Path) -> ExtractionResult:
        result = ExtractionResult()
        for yaml_file in sorted(directory.glob("*.yaml")):
            result.extend(self.extract_file(yaml_file, vault_root))
        return result


# ── Markdown extractor ────────────────────────────────────────────────────────

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_HEADING_RE  = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


class MarkdownExtractor:
    """
    Extraction from Markdown docs.

    Pass 1 scope: selected architecture docs, workflow docs, vault anchor files.
    Extracts: doc_section nodes (headings), wikilink_ref nodes (link targets).
    Edges: file_contains (file → section), references (section → wikilink_ref).

    Frontmatter keys are extracted as FRONTMATTER_KEY nodes for type/status tracking.
    """

    def extract_file(self, path: Path, vault_root: Path) -> ExtractionResult:
        result = ExtractionResult()
        rel = _rel(path, vault_root)
        domain = _infer_domain(rel)

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            result.errors.append(f"could not read {rel}: {exc}")
            return result

        result.source_files.append(rel)

        # File node for the markdown doc
        file_node = make_node(
            label=path.stem,
            node_type=NodeType.FILE,
            source_file=rel,
            domain=domain,
            properties={"language": "markdown", "lines": len(text.splitlines())},
            confidence=Confidence.EXTRACTED,
            provenance="markdown:file",
        )
        result.nodes.append(file_node)

        # Frontmatter extraction
        fm_data = _extract_frontmatter_mapping(text)
        if isinstance(fm_data, dict):
            for key, val in fm_data.items():
                if key in ("type", "status", "knowledge_class", "trust_tier",
                           "domain", "version", "created"):
                    fm_node = make_node(
                        label=f"{path.stem}.{key}={val}",
                        node_type=NodeType.FRONTMATTER_KEY,
                        source_file=rel,
                        domain=domain,
                        properties={"key": str(key), "value": str(val)},
                        confidence=Confidence.EXTRACTED,
                        provenance="markdown:frontmatter",
                    )
                    result.nodes.append(fm_node)
                    result.edges.append(make_edge(
                        file_node.node_id, fm_node.node_id,
                        Relation.FILE_CONTAINS,
                        provenance="markdown:frontmatter",
                    ))

        # Heading extraction — each heading becomes a doc_section node
        current_section_id: Optional[str] = None
        section_text_start: int = 0
        wikilinks_by_section: dict[str, list[str]] = {}

        headings = list(_HEADING_RE.finditer(text))
        for idx, match in enumerate(headings):
            level = len(match.group(1))
            heading_text = match.group(2).strip()

            section_node = make_node(
                label=heading_text,
                node_type=NodeType.DOC_SECTION,
                source_file=rel,
                source_line=text[:match.start()].count("\n") + 1,
                domain=domain,
                properties={"level": level, "heading": heading_text},
                confidence=Confidence.EXTRACTED,
                provenance="markdown:heading",
            )
            result.nodes.append(section_node)
            result.edges.append(make_edge(
                file_node.node_id, section_node.node_id,
                Relation.FILE_CONTAINS,
                provenance="markdown:heading",
            ))

            # Determine the text of this section (between this heading and next)
            section_start = match.end()
            section_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
            section_body = text[section_start:section_end]

            # Extract wikilinks within this section
            for link_match in _WIKILINK_RE.finditer(section_body):
                target = link_match.group(1).strip()
                if not target or "://" in target:
                    continue

                wikilink_node = make_node(
                    label=target,
                    node_type=NodeType.WIKILINK_REF,
                    source_file=rel,
                    domain=domain,
                    properties={"target": target, "source_section": heading_text},
                    confidence=Confidence.EXTRACTED,
                    provenance="markdown:wikilink",
                )
                result.nodes.append(wikilink_node)
                result.edges.append(make_edge(
                    section_node.node_id, wikilink_node.node_id,
                    Relation.REFERENCES,
                    provenance="markdown:wikilink",
                ))

            current_section_id = section_node.node_id

        # Wikilinks in the document not under any heading (preamble)
        preamble_end = headings[0].start() if headings else len(text)
        preamble = text[:preamble_end]
        for link_match in _WIKILINK_RE.finditer(preamble):
            target = link_match.group(1).strip()
            if not target or "://" in target:
                continue
            wikilink_node = make_node(
                label=target,
                node_type=NodeType.WIKILINK_REF,
                source_file=rel,
                domain=domain,
                properties={"target": target, "source_section": "(preamble)"},
                confidence=Confidence.EXTRACTED,
                provenance="markdown:wikilink_preamble",
            )
            result.nodes.append(wikilink_node)
            result.edges.append(make_edge(
                file_node.node_id, wikilink_node.node_id,
                Relation.REFERENCES,
                provenance="markdown:wikilink_preamble",
            ))

        return result

    def extract_files(
        self,
        paths: list[Path],
        vault_root: Path,
    ) -> ExtractionResult:
        result = ExtractionResult()
        for path in paths:
            if path.is_file() and path.suffix.lower() == ".md":
                result.extend(self.extract_file(path, vault_root))
            elif path.is_dir():
                for md_file in sorted(path.rglob("*.md")):
                    result.extend(self.extract_file(md_file, vault_root))
        return result

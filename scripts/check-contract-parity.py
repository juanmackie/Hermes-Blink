#!/usr/bin/env python3
"""Fail when the layout contract and the things that promise it drift apart.

The v2 bug this exists to prevent: layout.schema.json declared `style`, `color`,
`spacing`, `thickness`, `alignment`, `padding` and `weight`; validate.py accepted them;
LayoutParser.kt parsed them; and Renderer.kt applied none of them, so a layout author
silently got a bare black Text. Six copies of the same contract, no check that they agreed.

One registry (layout.schema.json), everything else mirrors it, and this compares them:

  * node types    - schema == validate.py == LayoutParser.kt == SKILL.md
  * action kinds  - schema == validate.py == LayoutParser.kt
  * text styles   - schema == validate.py == Typo.kt == SKILL.md
  * node fields   - schema(type-specific) == the SKILL.md "only these fields render" table
  * envelope      - every schema root property is either rendered or listed as metadata

Exit 0 when everything agrees. Exit 1 with a per-check diff otherwise.

    python scripts/check-contract-parity.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = REPO / "hermes-plugin" / "hermes-widget"
SCHEMA_PATH = PLUGIN / "layout.schema.json"
VALIDATE_PATH = PLUGIN / "validate.py"
PARSER_PATH = REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget" / "widget" / "LayoutParser.kt"
TYPO_PATH = REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" / "hermeswidget" / "widget" / "Typo.kt"
SKILL_PATH = PLUGIN / "skills" / "widget" / "SKILL.md"
SCHEMA_DOC_PATH = REPO / "docs" / "SCHEMA.md"

# Fields that live in the envelope, not on a node, and are not rendered per-node.
ENVELOPE_METADATA = {"version", "widgetId", "title", "ttlSeconds", "accentColor", "updatedAt", "root"}
# Common node fields checked once as a group rather than inside the per-type table.
NODE_BASE_FIELDS = {"type", "id", "weight", "padding", "alignment"}

failures: list[str] = []


def fail(check: str, detail: str) -> None:
    failures.append(f"[{check}] {detail}")


def mentions(text: str, name: str) -> bool:
    """A field documented as a `code` token or as a "quoted" JSON key."""
    return f"`{name}`" in text or f'"{name}"' in text


def section(text: str, heading: str) -> str:
    """The body of `## heading` up to the next `## ` heading."""
    match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    if not match:
        fail("skill-parse", f"SKILL.md has no '## {heading}' section")
        return ""
    rest = text[match.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def schema_node_types(schema: dict) -> set[str]:
    return set(schema["definitions"]["node"]["properties"]["type"]["enum"])


def schema_type_fields(schema: dict) -> dict[str, set[str]]:
    """Per node type, the fields declared in `nodeSpecifics`."""
    out: dict[str, set[str]] = {}
    for branch in schema["definitions"]["nodeSpecifics"]["oneOf"]:
        const = branch["if"]["properties"]["type"]["const"]
        fields = set(branch.get("then", {}).get("properties", {}))
        out[const] = fields
    return out


def kotlin_set(path: pathlib.Path, name: str) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"private val {name} = setOf\((.*?)\)", text, re.S)
    if not match:
        fail("kotlin-parse", f"{path.name}: no `{name}` setOf(...) found")
        return set()
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def skill_node_types(text: str) -> set[str]:
    body = section(text, "The v2 layout contract (transport stays /v1/)")
    found: set[str] = set()
    for line in body.splitlines():
        if re.match(r"^(Containers|Content|Data|Interactive):", line):
            found |= set(re.findall(r"`([a-z_]+)`", line))
    return found


def skill_fields(text: str) -> dict[str, set[str]]:
    """The `| node | fields |` table under 'Only these fields render'."""
    body = section(text, "Only these fields render")
    out: dict[str, set[str]] = {}
    for line in body.splitlines():
        match = re.match(r"^\|\s*`([a-z_]+)`\s*\|\s*(.*?)\s*\|\s*$", line)
        if not match:
            continue
        node, cells = match.group(1), match.group(2)
        # Drop the parenthesised value lists: `alignment` (`start`/`center`/...) is one field.
        cells = re.sub(r"\([^)]*\)", "", cells)
        out[node] = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", cells))
    return out


def skill_text_styles(text: str) -> dict[str, tuple[int, str, str]]:
    """The SKILL.md typography table as {name: (sizeSp, weight, colour)}."""
    body = section(text, "Typography — four steps, pick at most three")
    found: dict[str, tuple[int, str, str]] = {}
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        name = re.fullmatch(r"`([a-z_]+)`", cells[0])
        size = re.fullmatch(r"(\d+)sp", cells[1])
        colour = re.fullmatch(r"`(#[0-9A-Fa-f]{6})`", cells[3])
        if name and size and colour:
            found[name.group(1)] = (int(size.group(1)), cells[2], colour.group(1))
    return found


def typo_scale_keys(path: pathlib.Path) -> dict[str, tuple[int, str, str]]:
    """Typo.kt's SCALE as {name: (sizeSp, weight, colorHex)}."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"val SCALE: Map<String, Spec> = mapOf\((.*?)\n    \)", text, re.S)
    if not match:
        fail("kotlin-parse", "Typo.kt: SCALE map not found")
        return {}
    out: dict[str, tuple[int, str, str]] = {}
    for name, size, weight, colour in re.findall(
        r'"([a-z_]+)" to Spec\((\d+), "([a-z]+)", ([A-Z_]+)\)', match.group(1)
    ):
        # The colour is a Typo constant; resolve it from the constants above the map.
        const = re.search(rf'const val {colour} = "(#[0-9A-Fa-f]{{6}})"', text)
        out[name] = (int(size), weight, const.group(1) if const else "?")
    return out


def kotlin_constants(path: pathlib.Path) -> dict[str, str]:
    return dict(re.findall(r'const val ([A-Z_]+) = "(#[0-9A-Fa-f]{3,6})"', path.read_text(encoding="utf-8")))


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    skill = SKILL_PATH.read_text(encoding="utf-8")
    schema_doc = SCHEMA_DOC_PATH.read_text(encoding="utf-8")

    sys.path.insert(0, str(PLUGIN))
    import validate  # noqa: PLC0415 - after sys.path setup, and minimal deps by design

    # --- node types -------------------------------------------------------
    types_schema = schema_node_types(schema)
    types_validate = set(validate.NODE_TYPES)
    types_parser = kotlin_set(PARSER_PATH, "ALLOWED_TYPES")
    types_skill = skill_node_types(skill)

    for name, other in (
        ("validate.py NODE_TYPES", types_validate),
        ("LayoutParser.kt ALLOWED_TYPES", types_parser),
        ("SKILL.md node list", types_skill),
    ):
        if other != types_schema:
            fail("node-types", f"schema vs {name}: "
                               f"only-in-schema={sorted(types_schema - other)} "
                               f"only-in-{name}={sorted(other - types_schema)}")

    # --- action kinds -----------------------------------------------------
    kinds_schema = set(schema["definitions"]["action"]["properties"]["kind"]["enum"])
    kinds_validate = set(validate.ACTION_KINDS)
    kinds_parser = kotlin_set(PARSER_PATH, "ALLOWED_ACTION_KINDS")
    if kinds_validate != kinds_schema:
        fail("action-kinds", f"validate.py={sorted(kinds_validate)} schema={sorted(kinds_schema)}")
    if kinds_parser != kinds_schema:
        fail("action-kinds", f"LayoutParser.kt={sorted(kinds_parser)} schema={sorted(kinds_schema)}")

    # --- text styles ------------------------------------------------------
    styles_schema: set[str] = set()
    for branch in schema["definitions"]["nodeSpecifics"]["oneOf"]:
        if branch["if"]["properties"]["type"]["const"] == "text":
            styles_schema = set(branch["then"]["properties"]["style"]["enum"])
    # Names AND numbers: comparing name -> (size, weight, colour) against every mirror
    # subsumes a separate name-set check, so this is the only text-style comparison needed.
    scale_schema = schema["definitions"]["typography"]["properties"]
    if set(scale_schema) != styles_schema:
        fail("text-styles",
             f"schema text.style enum {sorted(styles_schema)} != schema typography {sorted(scale_schema)}")
    if set(validate._TEXT_STYLES) != styles_schema:
        fail("text-styles",
             f"validate.py _TEXT_STYLES={sorted(validate._TEXT_STYLES)} schema={sorted(styles_schema)}")
    typo_scale = typo_scale_keys(TYPO_PATH)
    skill_styles = skill_text_styles(skill)
    for name in sorted(styles_schema):
        want = scale_schema.get(name)
        if want is None:
            fail("typography", f"schema typography has no entry for {name!r}")
            continue
        expect = (want["sizeSp"], want["weight"], want["color"])
        for mirror, got in (("Typo.kt", typo_scale.get(name)),
                            ("SKILL.md", skill_styles.get(name))):
            if got != expect:
                fail("typography", f"{name}: schema {expect} but {mirror} {got}")

    # --- per-type fields vs the documented list ---------------------------
    fields_schema = schema_type_fields(schema)
    fields_skill = skill_fields(skill)
    for node_type in sorted(types_schema):
        declared = fields_schema.get(node_type, set())
        documented = fields_skill.get(node_type)
        if documented is None:
            fail("node-fields", f"{node_type}: no row in the SKILL.md field table")
            continue
        undocumented = declared - documented
        invented = documented - declared
        if undocumented:
            fail("node-fields", f"{node_type}: schema declares {sorted(undocumented)} "
                                 f"but SKILL.md does not document them")
        if invented:
            fail("node-fields", f"{node_type}: SKILL.md documents {sorted(invented)} "
                                 f"which the schema does not declare")
    for extra in sorted(set(fields_skill) - types_schema):
        fail("node-fields", f"SKILL.md documents unknown node type {extra!r}")

    # --- nodeBase fields must at least be documented somewhere ------------
    for field in sorted(NODE_BASE_FIELDS - {"type"}):
        if not mentions(skill, field):
            fail("node-base-fields", f"SKILL.md never mentions the common field `{field}`")

    # --- envelope ---------------------------------------------------------
    envelope = set(schema["properties"])
    unknown = envelope - ENVELOPE_METADATA
    if unknown:
        fail("envelope", f"schema declares envelope fields with no documented handling: {sorted(unknown)}")
    for field in ("version", "widgetId", "ttlSeconds", "accentColor"):
        if not mentions(skill, field):
            fail("envelope", f"SKILL.md does not mention the envelope field `{field}`")

    # --- palette and spacing constants ------------------------------------
    typo_src = TYPO_PATH.read_text(encoding="utf-8")
    palette = schema["definitions"]["palette"]["properties"]
    consts = kotlin_constants(TYPO_PATH)
    for name, const in (("primary", "PRIMARY"), ("secondary", "SECONDARY"),
                        ("success", "SUCCESS"), ("danger", "DANGER"),
                        ("hairline", "HAIRLINE")):
        want = palette[name]["const"]
        if consts.get(const) != want:
            fail("palette", f"{name}: schema {want} but Typo.kt {const}={consts.get(const)}")
    renderer = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "you" /
                "hermeswidget" / "widget" / "Renderer.kt").read_text(encoding="utf-8")
    accent = re.search(r'DEFAULT_ACCENT = "(#[0-9A-Fa-f]{6})"', renderer)
    if not accent or accent.group(1) != palette["accent"]["const"]:
        fail("palette",
             f"accent: schema {palette['accent']['const']} but Renderer.kt "
             f"{accent.group(1) if accent else 'not found'}")
    for field, const in (("containerSpacing", "CONTAINER_SPACING"),
                         ("rootPadding", "ROOT_PADDING")):
        want = schema["definitions"]["spacing"]["properties"][field]["const"]
        found = re.search(rf"const val {const} = (\d+)", typo_src)
        if not found or int(found.group(1)) != want:
            fail("spacing",
                 f"{field}: schema {want} but Typo.kt LayoutDefaults.{const}="
                 f"{found.group(1) if found else 'not found'}")

    # --- docs/SCHEMA.md must not promise removed things -------------------
    # Naming a removed field is fine as long as the line says it is gone; what this
    # forbids is a doc that reads as if the field exists.
    for dead in ("visibleIf",):
        for line in (line for line in schema_doc.splitlines() if dead in line):
            if not re.search(r"removed|never|not implemented|no longer|gone", line, re.I):
                fail("schema-doc",
                     f"docs/SCHEMA.md mentions `{dead}` without saying it is gone: {line.strip()!r}")

    if failures:
        print(f"contract parity FAILED ({len(failures)} check(s)):\n")
        for line in failures:
            print(f"  {line}")
        print("\nOne registry (layout.schema.json); update the mirrors it names.")
        return 1

    print(
        "contract parity OK: "
        f"{len(types_schema)} node types, {len(kinds_schema)} action kinds, "
        f"{len(styles_schema)} text styles, "
        f"{sum(len(v) for v in fields_schema.values())} per-type fields, "
        f"{len(envelope)} envelope fields"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

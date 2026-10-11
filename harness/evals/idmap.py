"""Record-id remap for the official run.

The platform runs the harness against a fresh copy of the instance. The eval tasks name records by id, and it is
not known whether a copy keeps ids. ``official_ids.json`` maps every id the tasks use to a business key (entity and
number); at start the runner looks each key up and swaps the current id into the task text. If ids are unchanged
this is the identity. A record that cannot be found is reported by name, never guessed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

UUID = re.compile(r"(?<![0-9a-zA-Z-])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-zA-Z-])")
ENTITIES = ("PayRun", "Employee", "EmployeeLoan", "SalaryRevision", "FinalSettlement", "InvestmentDeclaration",
            "SalaryStructure")
PAGE, MAX_PAGES = 100, 20


def load_table(path: str | Path) -> dict[str, dict[str, str]]:
    data = json.loads(Path(path).read_text())
    return dict(data.get("ids", data))


def remap_text(text: str, mapping: dict[str, str]) -> str:
    """Swap whole ids only: an id embedded in a longer token is left alone."""
    return UUID.sub(lambda match: mapping.get(match.group(0), match.group(0)), text)


async def _rows(client: Any, jurisdiction: str, entity: str) -> list[dict[str, Any]] | None:
    rows: list[dict[str, Any]] = []
    try:
        for page in range(MAX_PAGES):
            reply = await client.call_tool(f"{entity}.list", {"limit": PAGE, "offset": page * PAGE},
                                           jurisdiction=jurisdiction)
            data = reply.get("data") if isinstance(reply, dict) else None
            if not isinstance(data, list):
                return None
            rows.extend(row for row in data if isinstance(row, dict))
            if len(data) < PAGE or len(rows) >= (reply.get("total") or 0):
                return rows
    except Exception:
        return None
    return rows


async def resolve(client: Any, jurisdiction: str, table: dict[str, dict[str, str]]
                  ) -> tuple[dict[str, str], dict[str, str]]:
    """(old id -> current id, old id -> "Entity NUMBER" for every record that cannot be found)."""
    listed: dict[str, dict[str, str] | None] = {}
    mapping: dict[str, str] = {}
    missing: dict[str, str] = {}
    for old, key in table.items():
        entity, number = key["entity"], key["number"]
        if entity not in listed:
            rows = await _rows(client, jurisdiction, entity)
            listed[entity] = None if rows is None else {r["number"]: r["id"] for r in rows if r.get("number") and r.get("id")}
        found = listed[entity]
        if found is None:
            missing[old] = f"{entity} {number} (the {entity} list could not be read)"
        elif number in found:
            mapping[old] = found[number]
        else:
            missing[old] = f"{entity} {number}"
    return mapping, missing


async def build_table(client: Any, jurisdiction: str, uuids: set[str]) -> dict[str, Any]:
    """The table for ``uuids`` from the tenant they currently exist in (a one-off, read-only helper)."""
    ids: dict[str, dict[str, str]] = {}
    for entity in ENTITIES:
        rows = await _rows(client, jurisdiction, entity)
        for row in rows or []:
            if row.get("id") in uuids and row.get("number") and row["id"] not in ids:
                ids[row["id"]] = {"entity": entity, "number": row["number"]}
    return {"ids": dict(sorted(ids.items())), "unresolved": sorted(uuids - set(ids))}

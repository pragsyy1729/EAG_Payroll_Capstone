"""Implementation scaffolding for the record-id remap.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform.
"""
from __future__ import annotations

import json

from evals import idmap

OLD_RUN = "dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b"
NEW_RUN = "11111111-2222-3333-4444-555555555555"
OLD_EMP = "f8e8a973-1c34-4025-ae55-fbe728ba7b35"
TABLE = {OLD_RUN: {"entity": "PayRun", "number": "PRUN-2026-00011"},
         OLD_EMP: {"entity": "Employee", "number": "EMP-2026-00007"}}


class FakeClient:
    def __init__(self, tables, broken=()):
        self.tables, self.broken, self.calls = tables, set(broken), []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        self.calls.append(name)
        entity = name.partition(".")[0]
        if entity in self.broken:
            raise RuntimeError("boom")
        rows = self.tables.get(entity, [])
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 10)
        return {"data": rows[offset:offset + limit], "total": len(rows)}


async def test_unchanged_ids_map_to_themselves():
    client = FakeClient({"PayRun": [{"id": OLD_RUN, "number": "PRUN-2026-00011"}],
                         "Employee": [{"id": OLD_EMP, "number": "EMP-2026-00007"}]})
    mapping, missing = await idmap.resolve(client, "IN", TABLE)
    assert mapping == {OLD_RUN: OLD_RUN, OLD_EMP: OLD_EMP} and missing == {}
    text = f'{{"payrun_id": "{OLD_RUN}"}}'
    assert idmap.remap_text(text, mapping) == text


async def test_a_changed_id_is_found_by_its_business_number():
    client = FakeClient({"PayRun": [{"id": NEW_RUN, "number": "PRUN-2026-00011"}],
                         "Employee": [{"id": OLD_EMP, "number": "EMP-2026-00007"}]})
    mapping, missing = await idmap.resolve(client, "IN", TABLE)
    assert mapping[OLD_RUN] == NEW_RUN and missing == {}
    assert idmap.remap_text(f"a {OLD_RUN} b {OLD_RUN}", mapping) == f"a {NEW_RUN} b {NEW_RUN}"


async def test_a_record_missing_from_the_copy_is_reported_by_name_not_guessed():
    client = FakeClient({"PayRun": [{"id": NEW_RUN, "number": "PRUN-2026-99999"}], "Employee": []})
    mapping, missing = await idmap.resolve(client, "IN", TABLE)
    assert mapping == {} and missing[OLD_RUN] == "PayRun PRUN-2026-00011" and missing[OLD_EMP] == "Employee EMP-2026-00007"


async def test_a_list_that_cannot_be_read_marks_its_records_missing_and_the_rest_still_resolve():
    client = FakeClient({"Employee": [{"id": OLD_EMP, "number": "EMP-2026-00007"}]}, broken=("PayRun",))
    mapping, missing = await idmap.resolve(client, "IN", TABLE)
    assert mapping == {OLD_EMP: OLD_EMP} and "PayRun PRUN-2026-00011" in missing[OLD_RUN]


async def test_each_entity_is_listed_once_and_paged():
    rows = [{"id": f"id-{i}", "number": f"PRUN-{i}"} for i in range(250)] + [{"id": NEW_RUN, "number": "PRUN-2026-00011"}]
    client = FakeClient({"PayRun": rows})
    table = {OLD_RUN: TABLE[OLD_RUN], "x": {"entity": "PayRun", "number": "PRUN-7"}}
    mapping, _ = await idmap.resolve(client, "IN", table)
    assert mapping[OLD_RUN] == NEW_RUN and mapping["x"] == "id-7"
    assert client.calls.count("PayRun.list") == 3                          # 251 rows at 100 a page, listed once


def test_remap_never_touches_a_longer_string_that_merely_contains_an_id_prefix():
    short = "dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b"
    assert idmap.remap_text(short + "9", {short: NEW_RUN}) == short + "9"
    assert idmap.remap_text(f"x{short}", {short: NEW_RUN}) == f"x{short}"       # embedded in a longer token: untouched


def test_the_uuid_pattern_finds_ids_in_json_text():
    text = json.dumps({"a": OLD_RUN, "b": [OLD_EMP], "c": "PRUN-2026-00011"})
    assert set(idmap.UUID.findall(text)) == {OLD_RUN, OLD_EMP}


async def test_building_a_table_records_numbers_and_lists_what_could_not_be_resolved():
    client = FakeClient({"PayRun": [{"id": OLD_RUN, "number": "PRUN-2026-00011"}],
                         "Employee": [{"id": OLD_EMP, "number": "EMP-2026-00007"}]})
    ghost = "99999999-9999-9999-9999-999999999999"
    built = await idmap.build_table(client, "IN", {OLD_RUN, OLD_EMP, ghost})
    assert built["ids"] == TABLE and built["unresolved"] == [ghost]

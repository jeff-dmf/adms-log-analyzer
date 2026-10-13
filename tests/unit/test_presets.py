"""View presets: the TOML store (roundtrip, version, atomicity, tolerance), curated presets, import."""

import json
import os
import tomllib

import pytest

from dmslog.parse.templates import load_catalog
from dmslog.store import presets
from dmslog.store.filters import compile_filter

TREE = {"or": [{"sev": {"le": 2}}, {"tpl": ["GEN-EXC-01"]}]}


def _make(name="Mine", family="AWFM", tree=None, **extra):
    return {"name": name, "description": "d", "family": family, "filter": tree or TREE, **extra}


def _leaf_templates(node):
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "tpl":
                found += [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str)]
            elif key in ("and", "or"):
                for child in value:
                    found += _leaf_templates(child)
            elif key == "not":
                found += _leaf_templates(value)
    return found


# -- curated presets ------------------------------------------------------------------------------


def test_curated_presets_are_valid_and_use_catalog_ids_only():
    catalog = load_catalog()
    codes = {t.code for fam in catalog.families.values() for t in fam.templates}
    text = presets.resources.files("dmslog.store").joinpath(presets.BUILTIN_FILE).read_text(encoding="utf-8")
    doc = presets.parse_document(tomllib.loads(text))
    assert doc.problems == [] and not doc.invalid
    assert len(doc.presets) == len(tomllib.loads(text)["preset"]) > 10
    for preset in doc.presets:
        compile_filter(preset["filter"])
        for code in _leaf_templates(preset["filter"]):
            assert code in codes, (preset["name"], code)
        family = preset["family"]
        assert family is None or family in catalog.families, preset["name"]
        if family:
            own = {t.code for t in catalog.families[family].templates} | {
                t.code for t in catalog.families["_common"].templates
            }
            assert set(_leaf_templates(preset["filter"])) <= own, (preset["name"], family)
    identities = [presets.identity(p) for p in doc.presets]
    assert len(identities) == len(set(identities))
    names = {p["name"] for p in doc.presets}
    assert {"Errors and exceptions", "Failed operations", "Callbacks without response"} <= names
    assert "Log level windows (Info/Verbose)" in names


def test_curated_presets_hold_no_log_text():
    for preset in presets.builtin_presets():
        assert "text" not in json.dumps(preset["filter"]).replace('"tpl"', "")


# -- user file ------------------------------------------------------------------------------------


def test_missing_file_is_empty(tmp_path):
    doc = presets.load_user(tmp_path)
    assert doc.presets == [] and not doc.invalid
    listing = presets.list_presets(tmp_path)
    assert listing["invalid"] is False and any(p["builtin"] for p in listing["presets"])


def test_roundtrip_and_text_form(tmp_path):
    created = presets.create(tmp_path, _make(" Mine  one ", tree={"text": {"fts": 'say "hi" \\ \u00e9'}}))
    assert created["name"] == "Mine one" and created["builtin"] is False and created["family"] == "AWFM"
    raw = tomllib.loads((tmp_path / presets.FILENAME).read_text(encoding="utf-8"))
    assert raw["schema"] == 1 and raw["preset"][0]["filter"] == {"text": {"fts": 'say "hi" \\ \u00e9'}}
    again = presets.load_user(tmp_path).presets
    assert again[0]["filter"] == created["filter"] and again[0]["name"] == "Mine one"


def test_unknown_keys_are_preserved(tmp_path):
    (tmp_path / presets.FILENAME).write_text(
        'schema = 1\nfuture = { a = 1 }\n[[preset]]\nname = "X"\n'
        'filter = { sev = { le = 2 } }\nnote = "keep"\n',
        encoding="utf-8",
    )
    presets.create(tmp_path, _make("Y"))
    raw = tomllib.loads((tmp_path / presets.FILENAME).read_text(encoding="utf-8"))
    assert raw["future"] == {"a": 1}
    assert raw["preset"][0]["note"] == "keep"
    presets.update(tmp_path, "X", None, {"name": "X2", "filter": {"sev": 2}})
    raw = tomllib.loads((tmp_path / presets.FILENAME).read_text(encoding="utf-8"))
    assert raw["preset"][0]["note"] == "keep" and raw["preset"][0]["name"] == "X2"


def test_invalid_preset_is_skipped_and_reported_others_survive(tmp_path):
    (tmp_path / presets.FILENAME).write_text(
        'schema = 1\n[[preset]]\nname = "Bad"\nfilter = { nope = 1 }\n'
        '[[preset]]\nname = "Good"\nfilter = { sev = 2 }\n'
        '[[preset]]\nname = "Case"\nfilter = { source = 1 }\n',
        encoding="utf-8",
    )
    doc = presets.load_user(tmp_path)
    assert [p["name"] for p in doc.presets] == ["Good"]
    assert [p["code"] for p in doc.problems] == ["filter_invalid", "case_specific"]
    assert doc.problems[0]["name"] == "Bad"


def test_newer_schema_or_bad_toml_is_read_only(tmp_path):
    path = tmp_path / presets.FILENAME
    for text in ("schema = 2\n", "not = [toml"):
        path.write_text(text, encoding="utf-8")
        assert presets.load_user(tmp_path).invalid
        with pytest.raises(presets.PresetError) as err:
            presets.create(tmp_path, _make())
        assert err.value.code == "file_invalid"
        assert path.read_text(encoding="utf-8") == text  # untouched
        assert presets.list_presets(tmp_path)["invalid"] is True


def test_write_is_atomic_and_leaves_no_temporary(tmp_path, monkeypatch):
    presets.create(tmp_path, _make("A"))
    before = (tmp_path / presets.FILENAME).read_bytes()

    def boom(src, dst):
        raise OSError("disk")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(presets.PresetError) as err:
        presets.create(tmp_path, _make("B"))
    assert err.value.code == "save_failed"
    assert (tmp_path / presets.FILENAME).read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == [presets.FILENAME]


def test_names_and_filters_are_validated(tmp_path):
    for bad, code in [
        ({"name": "", "filter": TREE}, "bad_name"),
        ({"name": "x" * 200, "filter": TREE}, "bad_name"),
        ({"name": 3, "filter": TREE}, "bad_name"),
        ({"name": "a", "filter": TREE, "family": "A B"}, "bad_family"),
        ({"name": "a", "filter": TREE, "description": "d" * 500}, "bad_description"),
        ({"name": "a", "filter": {}}, "bad_filter"),
        ({"name": "a", "filter": {"zzz": 1}}, "filter_invalid"),
        ({"name": "a", "filter": {"and": [{"ts": {"ge": 1}}]}}, "case_specific"),
        ({"name": "a", "filter": {"not": {"stream": 1}}}, "case_specific"),
        ("nope", "bad_preset"),
    ]:
        with pytest.raises(presets.PresetError) as err:
            presets.create(tmp_path, bad)
        assert err.value.code == code, bad
    assert not (tmp_path / presets.FILENAME).exists()


def test_family_is_uppercased_and_identity_ignores_case(tmp_path):
    made = presets.create(tmp_path, _make("Same", family="awfm"))
    assert made["family"] == "AWFM"
    with pytest.raises(presets.PresetError) as err:
        presets.create(tmp_path, _make("SAME", family="AWFM"))
    assert err.value.code == "duplicate_name"
    assert presets.create(tmp_path, _make("Same", family=None))["family"] is None  # other identity


def test_curated_names_are_reserved_and_read_only(tmp_path):
    curated = presets.builtin_presets()[0]
    with pytest.raises(presets.PresetError) as err:
        presets.create(tmp_path, {**curated, "filter": TREE})
    assert err.value.code == "reserved_name"
    with pytest.raises(presets.PresetError) as err:
        presets.delete(tmp_path, curated["name"], curated["family"])
    assert err.value.code == "builtin"


def test_update_rename_delete_and_limit(tmp_path):
    presets.create(tmp_path, _make("A"))
    presets.create(tmp_path, _make("B"))
    with pytest.raises(presets.PresetError) as err:
        presets.update(tmp_path, "A", "AWFM", _make("b"))
    assert err.value.code == "duplicate_name"
    assert presets.update(tmp_path, "a", "awfm", _make("C"))["name"] == "C"
    with pytest.raises(presets.PresetError) as err:
        presets.update(tmp_path, "zz", "AWFM", _make("D"))
    assert err.value.code == "not_found"
    presets.delete(tmp_path, "c", "AWFM")
    with pytest.raises(presets.PresetError):
        presets.delete(tmp_path, "c", "AWFM")
    assert [p["name"] for p in presets.load_user(tmp_path).presets] == ["B"]


def test_list_filters_by_family(tmp_path):
    presets.create(tmp_path, _make("Mine", family="AOSR"))
    presets.create(tmp_path, _make("Everywhere", family=None))
    listing = presets.list_presets(tmp_path, {"AOSR"})["presets"]
    assert {p["family"] for p in listing} <= {None, "AOSR"}
    assert {"Mine", "Everywhere"} <= {p["name"] for p in listing}
    only_global = presets.list_presets(tmp_path, set())["presets"]
    assert all(p["family"] is None for p in only_global)
    assert "Mine" not in {p["name"] for p in only_global}


# -- import / export ------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["json", "toml"])
def test_export_then_import_roundtrip(tmp_path, fmt):
    src, dst = tmp_path / "a", tmp_path / "b"
    presets.create(src, _make("One"))
    presets.create(src, _make("Two", family=None, tree={"text": {"fts": "word"}}))
    out = presets.export_user(src, fmt)
    assert out["count"] == 2
    payload = out["data"] if fmt == "json" else out["text"]
    report = presets.import_presets(dst, fmt, payload)
    assert report == {"added": 2, "updated": 0, "ignored": []}
    assert presets.load_user(dst).presets == presets.load_user(src).presets
    report = presets.import_presets(dst, fmt, payload)
    assert report["added"] == 0 and report["updated"] == 2


def test_import_reports_ignored_without_stopping(tmp_path):
    curated = presets.builtin_presets()[0]
    doc = {
        "schema": 1,
        "preset": [
            _make("Fine"),
            {"name": "Broken", "filter": {"zzz": 1}},
            {**curated, "filter": TREE},
            _make("Fine"),  # duplicate inside the document
        ],
    }
    report = presets.import_presets(tmp_path, "json", json.dumps(doc))
    assert report["added"] == 1 and report["updated"] == 0
    assert sorted(i["code"] for i in report["ignored"]) == [
        "duplicate_name",
        "filter_invalid",
        "reserved_name",
    ]
    assert [p["name"] for p in presets.load_user(tmp_path).presets] == ["Fine"]


def test_import_rejects_garbage(tmp_path):
    for fmt, data in [("json", "{"), ("json", 5), ("toml", "x = [oops"), ("toml", 4), ("yaml", "x"),
                      ("json", {"schema": 9, "preset": []}), ("json", {"preset": "no"})]:  # fmt: skip
        with pytest.raises(presets.PresetError):
            presets.import_presets(tmp_path, fmt, data)
    with pytest.raises(presets.PresetError) as err:
        presets.import_presets(tmp_path, "toml", "#" * (presets.MAX_IMPORT_BYTES + 1))
    assert err.value.code == "import_too_large"
    assert not (tmp_path / presets.FILENAME).exists()


def test_toml_writer_handles_special_characters(tmp_path):
    tree = {"text": {"fts": "tab\there \x7f \u2028 line\nbreak"}}
    presets.create(tmp_path, _make("Odd", tree=tree))
    assert presets.load_user(tmp_path).presets[0]["filter"] == tree


# -- review fixes (limits, merge, TOML types, deep nesting) ------------------------------------------


def test_save_refuses_a_file_the_next_read_would_reject(tmp_path, monkeypatch):
    presets.create(tmp_path, _make("A"))
    before = (tmp_path / presets.FILENAME).read_bytes()
    monkeypatch.setattr(presets, "MAX_FILE_BYTES", len(before) + 10)
    with pytest.raises(presets.PresetError) as err:
        presets.create(tmp_path, _make("B"))
    assert err.value.code == "file_too_large"
    assert (tmp_path / presets.FILENAME).read_bytes() == before
    assert not presets.load_user(tmp_path).invalid


def test_max_presets_of_max_size_fit_in_the_file(tmp_path):
    big = {"or": [{"text": {"fts": "w" * 300}} for _ in range(190)]}
    assert len(json.dumps(big)) < 64 * 1024
    doc = presets.Document(
        presets=[presets.validate_preset(_make(f"p{i}", tree=big)) for i in range(presets.MAX_PRESETS)]
    )
    assert len(presets.dump_toml(doc.presets).encode()) <= presets.MAX_FILE_BYTES


def test_extra_fields_are_bounded():
    with pytest.raises(presets.PresetError) as err:
        presets.validate_preset(_make("x", note="n" * (presets.MAX_EXTRA_BYTES + 1)))
    assert err.value.code == "extra_too_large"


def test_json_object_import_honours_the_size_limit(tmp_path):
    data = {"schema": 1, "preset": [_make("Big", note="x" * (presets.MAX_IMPORT_BYTES))]}
    with pytest.raises(presets.PresetError) as err:
        presets.import_presets(tmp_path, "json", data)
    assert err.value.code == "import_too_large"


def test_import_update_keeps_unknown_fields_of_the_existing_preset(tmp_path):
    (tmp_path / presets.FILENAME).write_text(
        'schema = 1\n[[preset]]\nname = "X"\nfilter = { sev = 2 }\nnote = "keep"\nother = 1\n',
        encoding="utf-8",
    )
    report = presets.import_presets(
        tmp_path, "json", {"preset": [{"name": "x", "filter": {"sev": 1}, "other": 2}]}
    )
    assert report["updated"] == 1
    saved = presets.load_user(tmp_path).presets[0]
    assert saved["filter"] == {"sev": 1} and saved["extra"] == {"note": "keep", "other": 2}


def test_toml_dates_times_inf_nan_in_unknown_fields_roundtrip(tmp_path):
    (tmp_path / presets.FILENAME).write_text(
        'schema = 1\nwhen = 2026-10-13T10:00:00Z\n[[preset]]\nname = "X"\nfilter = { sev = 2 }\n'
        "d = 2026-10-13\nt = 10:30:00\nldt = 2026-10-13T10:00:00\nup = inf\ndown = -inf\nbad = nan\n",
        encoding="utf-8",
    )
    presets.create(tmp_path, _make("Y"))
    raw = tomllib.loads((tmp_path / presets.FILENAME).read_text(encoding="utf-8"))
    original = tomllib.loads((tmp_path / presets.FILENAME).read_text(encoding="utf-8"))
    first = raw["preset"][0]
    assert str(first["d"]) == "2026-10-13" and str(first["t"]) == "10:30:00"
    assert first["up"] == float("inf") and first["down"] == float("-inf") and first["bad"] != first["bad"]
    assert str(raw["when"]) == "2026-10-13 10:00:00+00:00" and original["when"] == raw["when"]
    assert first["ldt"].tzinfo is None


def test_deeply_nested_toml_is_an_invalid_document(tmp_path):
    deep = "x = " + "[" * 6000 + "]" * 6000 + "\n"
    (tmp_path / presets.FILENAME).write_text(deep, encoding="utf-8")
    assert presets.load_user(tmp_path).invalid
    with pytest.raises(presets.PresetError) as err:
        presets.create(tmp_path, _make("A"))
    assert err.value.code == "file_invalid"
    assert (tmp_path / presets.FILENAME).read_text(encoding="utf-8") == deep
    with pytest.raises(presets.PresetError) as err:
        presets.import_presets(tmp_path / "other", "toml", deep)
    assert err.value.code == "bad_import"
    nested = {"schema": 1, "preset": [], "x": []}
    cur = nested["x"]
    for _ in range(5000):
        cur.append([])
        cur = cur[0]
    with pytest.raises(presets.PresetError) as err:
        presets.import_presets(tmp_path / "other", "json", nested)
    assert err.value.code in ("bad_import", "import_too_large")


def test_import_update_rejects_a_union_of_unknown_fields_that_is_too_large(tmp_path):
    old = {"name": "X", "filter": {"sev": 2}, "a": "a" * 5000}
    presets.import_presets(tmp_path, "json", {"preset": [old]})
    before = (tmp_path / presets.FILENAME).read_bytes()
    incoming = {"name": "x", "filter": {"sev": 1}, "b": "b" * 5000}
    report = presets.import_presets(tmp_path, "json", {"preset": [incoming, _make("Other")]})
    assert report["added"] == 1 and report["updated"] == 0
    assert [(i["name"], i["code"]) for i in report["ignored"]] == [("x", "extra_too_large")]
    saved = {p["name"]: p for p in presets.load_user(tmp_path).presets}
    assert saved["X"]["filter"] == {"sev": 2} and set(saved["X"]["extra"]) == {"a"}
    assert not presets.load_user(tmp_path).problems and before != b""


def test_update_keeps_the_extra_bound(tmp_path):
    presets.import_presets(
        tmp_path, "json", {"preset": [{"name": "X", "filter": {"sev": 2}, "a": "a" * 5000}]}
    )
    with pytest.raises(presets.PresetError) as err:
        presets.update(tmp_path, "X", None, {"name": "X", "filter": {"sev": 1}, "b": "b" * 9000})
    assert err.value.code == "extra_too_large"
    assert presets.load_user(tmp_path).presets[0]["filter"] == {"sev": 2}

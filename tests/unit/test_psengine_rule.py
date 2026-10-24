import pytest

from dmslog.ingest.discover import load_rules
from dmslog.store import presets

CASES = [
    ("Logs/PSengine.log", "PSengine", None),
    ("Logs/PSengine.log.3", "PSengine", 3),
    ("Logs/PSengine.ALTERNATE_1.log", "PSengine.ALTERNATE_1", None),
    ("Logs/PSengine.ALTERNATE_2.log.1", "PSengine.ALTERNATE_2", 1),
    ("PSengine.ALTERNATE_12.log", "PSengine.ALTERNATE_12", None),
]


@pytest.mark.parametrize(("path", "process", "rotation"), CASES)
def test_psengine_paths_classify_as_family(path, process, rotation):
    c = load_rules().classify(path)
    assert c.family == "PSENGINE"
    assert c.process == process
    assert c.rotation == rotation
    assert c.scope == "optional"
    assert c.indexable


@pytest.mark.parametrize(
    "path",
    [
        "Logs/PSengine.ALTERNATE_.log",
        "Logs/PSengine.ALTERNATE_x.log",
        "Logs/PSengine.OTHER_1.log",
        "Logs/NotPSengine.ALTERNATE_1.log",
    ],
)
def test_psengine_rule_does_not_overmatch(path):
    assert load_rules().classify(path).family != "PSENGINE"


def test_alternate_streams_have_distinct_process_and_no_pool():
    rules = load_rules()
    main = rules.classify("Logs/PSengine.log")
    alt = rules.classify("Logs/PSengine.ALTERNATE_1.log")
    assert main.process != alt.process
    assert rules.pool_of("PSENGINE", alt.process) is None
    assert rules.pool_of("PSENGINE", main.process) is None


def test_peer_loop_shortcut_is_valid_builtin():
    found = [p for p in presets.builtin_presets() if p["name"] == "PSengine: failures without the peer loop"]
    assert len(found) == 1
    preset = found[0]
    assert preset["family"] == "PSENGINE"
    assert preset["filter"] == {
        "and": [{"sev": {"le": 2}}, {"not": {"tpl": ["PSE-001", "PSE-002", "PSE-003"]}}]
    }

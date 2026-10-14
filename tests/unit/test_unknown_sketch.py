"""TOML sketches of the unknown-templates panel go through the real template engine.

The sketch builder is JavaScript (web/js/unknown-panel-model.js, pure, no DOM). Simplest robust
route: run it with ``node`` in a subprocess on signatures produced by the real ``signature()``,
then load every sketch with ``load_texts(..., check_examples=True)`` (safety validator plus the
example check). Skipped when node is missing.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dmslog.parse.signature import signature
from dmslog.parse.templates import load_texts

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "src" / "dmslog" / "web" / "js" / "unknown-panel-model.js"

# Synthetic messages (fake ids, hosts and paths) covering every marker and regex metacharacters.
MESSAGES = [
    "Request 0B5C1F7E-1111-2222-3333-444455556666 failed after 120 ms (code 7)",
    "[Sync start] - 2000-01-02 03:04:05.678 from 10.1.2.3:8080 [flags=0x1A8]",
    "Cannot open 'C:\\example\\a b.txt' or \"/var/example/x\" at http://host.invalid/a?b=1&c=2",
    "Cost $5.00 * (2+3) ^ {x} | y? [z] ok.",
    "it's a 'quoted' value and \"another\" one",
    "odd ''' quote ' and \" here",
    "plain text without placeholders",
    " ".join(f"value {i} is '{i}'" for i in range(30)),  # many groups: engine limits
    " ".join(f"at 2030-01-15 10:00:{i:02d}.000" for i in range(30)),  # 30 timestamps
    " ".join(
        f"2030-01-15 10:00:00 {g} {i} '{i}' C:\\d\\{i}"
        for i, g in enumerate(["0B5C1F7E-1111-2222-3333-444455556666"] * 12)
    ),  # mixed, past the limits
    "Peer 10.20.30.40:5000 and 10.0.0.1 at http://host.invalid/a/b via C:\\x\\f.txt v1.2.3.4 ratio 3.14",
    "Said 'it is ok' then \"two\" with 12.5 and 7 from 192.168.1.1:80 at C:\\x\\y",
    "x" * 400,  # truncated signature: no end anchor
]

NODE_SCRIPT = """
import { buildTomlSketch } from MODEL_URL;
let raw = "";
process.stdin.on("data", (c) => { raw += c; });
process.stdin.on("end", () => {
  const rows = JSON.parse(raw);
  process.stdout.write(JSON.stringify(rows.map((r) => buildTomlSketch(r, "AWFM"))));
});
""".replace("MODEL_URL", json.dumps(MODEL.as_uri()))


@pytest.fixture(scope="module")
def sketches():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    rows = [{"signature": signature(m), "n": 3, "family": "AWFM"} for m in MESSAGES]
    result = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        input=json.dumps(rows),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,  # generous: under pytest-xdist on a loaded Windows runner node starts slowly
        check=True,
    )
    return json.loads(result.stdout)


def test_sketches_pass_engine_validation_and_example_check(sketches):
    assert len(sketches) == len(MESSAGES)
    for text in sketches:
        catalog = load_texts({"awfm.toml": text}, check_examples=True)
        assert catalog.check_examples() == []

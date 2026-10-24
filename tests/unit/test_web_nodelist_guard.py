"""A NodeList has no filter/find/map: the fake DOM of the node tests returns arrays and hides that mistake."""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(__file__).resolve().parents[2] / "src" / "dmslog" / "web" / "js"
BAD = re.compile(
    r"querySelectorAll\([^)]*\)\s*\.(filter|find|findIndex|map|some|every|reduce|slice|includes)\("
)


def test_query_selector_all_results_are_converted_before_array_methods():
    offenders = [
        f"{path.name}:{text.count(chr(10), 0, match.start()) + 1}"
        for path in sorted(JS.glob("*.js"))
        for text in [path.read_text(encoding="utf-8")]
        for match in BAD.finditer(text)
    ]
    assert offenders == []

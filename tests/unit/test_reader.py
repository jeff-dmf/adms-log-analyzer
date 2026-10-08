"""Tests for dmslog.ingest.reader: chunked reading, zip streaming, UTF-16 and zip-bomb limits."""

import zipfile
from pathlib import Path

import pytest

from dmslog.ingest.discover import discover
from dmslog.ingest.reader import (
    ReadBudget,
    ReadLimitError,
    ReadLimits,
    SourceReader,
    relax_ratio_violations,
)

LOG = "SchneiderElectricData/OASyS/Servers/Log"
MEMBER = f"{LOG}/dnaApp_zone1/SVC/WfmAdapter_100.log"
TEXT = "2030-01-15 10:00:00.000 [PID=100][TID=0x1] INFO - caf\u00e9 \U0001f600 line\r\nsecond\r\n"


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def infos(path: Path, **limits) -> dict[str, object]:
    return {s.member: s for s in discover(path, **limits)}


def read_all(reader: SourceReader, info, start: int = 0, chunk_size: int = 7) -> bytes:
    return b"".join(reader.iter_chunks(info, start, chunk_size))


def utf16(text: str, byteorder: str = "le", bom: bool = True) -> bytes:
    prefix = b"\xff\xfe" if byteorder == "le" else b"\xfe\xff"
    return (prefix if bom else b"") + text.encode(f"utf-16-{byteorder}")


def test_file_chunks_resume_at_offset(tmp_path):
    folder = tmp_path / "Log" / "dnaApp_zone1" / "SVC"
    folder.mkdir(parents=True)
    data = TEXT.encode("utf-8")
    (folder / "WfmAdapter_100.log").write_bytes(data)
    info = infos(tmp_path / "Log")["dnaApp_zone1/SVC/WfmAdapter_100.log"]
    with SourceReader() as reader:
        assert read_all(reader, info) == data
        assert read_all(reader, info, start=10) == data[10:]
        assert read_all(reader, info, start=len(data)) == b""


def test_file_source_is_reopened_for_every_chunk(tmp_path, monkeypatch):
    """No handle stays open between chunks (on Windows it would block log rotation)."""
    path = tmp_path / "x.log"
    path.write_bytes(b"a" * 100)
    info = next(iter(discover(path)))
    opened: list[object] = []
    real_open = Path.open

    def tracking_open(self, *args, **kwargs):
        fh = real_open(self, *args, **kwargs)
        if self == path:
            opened.append(fh)
        return fh

    monkeypatch.setattr(Path, "open", tracking_open)
    with SourceReader() as reader:
        chunks = reader.iter_chunks(info, 0, 30)
        first = next(chunks)
        assert len(first) == 30
        assert all(fh.closed for fh in opened)  # nothing open while the consumer holds the generator
        path_data = b"".join([first, *chunks])
    assert path_data == b"a" * 100
    assert all(fh.closed for fh in opened)


def test_file_growth_between_chunks_is_read(tmp_path):
    path = tmp_path / "x.log"
    path.write_bytes(b"one\n")
    info = next(iter(discover(path)))
    with SourceReader() as reader:
        chunks = reader.iter_chunks(info, 0, 4)
        assert next(chunks) == b"one\n"
        with path.open("ab") as fh:
            fh.write(b"two\n")
        assert next(chunks) == b"two\n"


def test_zip_member_streams_and_skips_to_offset(tmp_path):
    data = TEXT.encode("utf-8")
    zpath = make_zip(tmp_path / "a.zip", {MEMBER: data})
    info = infos(zpath)[MEMBER]
    with SourceReader() as reader:
        assert read_all(reader, info) == data
        assert read_all(reader, info, start=25) == data[25:]
        assert reader.read_head(info, 10) == data[:10]
        assert not reader.is_utf16(info)


@pytest.mark.parametrize("byteorder", ["le", "be"])
@pytest.mark.parametrize("chunk_size", [1, 3, 7, 1 << 20])
def test_utf16_zip_member_is_transcoded_to_utf8(tmp_path, byteorder, chunk_size):
    zpath = make_zip(tmp_path / "a.zip", {MEMBER: utf16(TEXT, byteorder)})
    info = infos(zpath)[MEMBER]
    with SourceReader() as reader:
        assert reader.is_utf16(info)
        assert read_all(reader, info, chunk_size=chunk_size) == TEXT.encode("utf-8")
        # offsets refer to the transcoded stream
        assert read_all(reader, info, start=5, chunk_size=chunk_size) == TEXT.encode("utf-8")[5:]


def test_utf16_file_is_transcoded_and_reports_encoding(tmp_path):
    path = tmp_path / "installer.log"
    path.write_bytes(utf16(TEXT, "le"))
    info = next(iter(discover(path)))
    with SourceReader() as reader:
        assert reader.file_encoding(info) == "utf-16-le"
        assert reader.is_utf16(info)
        assert read_all(reader, info) == TEXT.encode("utf-8")
        assert reader.read_head(info, 12) == TEXT.encode("utf-8")[:12]


def test_utf16_odd_trailing_byte_is_replaced_not_fatal(tmp_path):
    path = tmp_path / "installer.log"
    path.write_bytes(utf16("abc\n") + b"\x41")
    info = next(iter(discover(path)))
    with SourceReader() as reader:
        out = read_all(reader, info)
    assert out.startswith(b"abc\n")
    assert "�".encode() in out


def test_utf8_bom_is_left_for_the_parser(tmp_path):
    path = tmp_path / "x.log"
    path.write_bytes(b"\xef\xbb\xbfhello\n")
    info = next(iter(discover(path)))
    with SourceReader() as reader:
        assert read_all(reader, info) == b"\xef\xbb\xbfhello\n"


def test_empty_zip_member(tmp_path):
    zpath = make_zip(tmp_path / "a.zip", {MEMBER: b""})
    info = infos(zpath)[MEMBER]
    with SourceReader() as reader:
        assert read_all(reader, info) == b""
        assert reader.read_head(info) == b""


def test_budget_total_limit_stops_reading(tmp_path):
    zpath = make_zip(tmp_path / "a.zip", {MEMBER: b"x" * 5000})
    info = infos(zpath)[MEMBER]
    with SourceReader(ReadLimits(max_total_bytes=1000)) as reader, pytest.raises(ReadLimitError) as exc:
        read_all(reader, info, chunk_size=100)
    assert exc.value.reason == "total_bytes"


def test_member_size_limit_stops_reading_a_file(tmp_path):
    path = tmp_path / "x.log"
    path.write_bytes(b"x" * 5000)
    info = next(iter(discover(path)))
    with SourceReader(ReadLimits(max_member_bytes=1000)) as reader, pytest.raises(ReadLimitError) as exc:
        read_all(reader, info, chunk_size=100)
    assert exc.value.reason == "member_bytes"


def test_budget_object_accumulates_across_members():
    budget = ReadBudget(ReadLimits(max_total_bytes=10))
    budget.consume(6, 6)
    with pytest.raises(ReadLimitError):
        budget.consume(6, 6)


# ---- zip bomb policy


def bomb_zip(tmp_path: Path, size: int = 3 * 1024 * 1024) -> Path:
    return make_zip(tmp_path / "bomb.zip", {MEMBER: b"\x00" * size})


def test_high_ratio_member_is_flagged_by_the_listing(tmp_path):
    info = infos(bomb_zip(tmp_path))[MEMBER]
    assert info.limit_violation == "ratio"
    assert not info.selectable


def test_high_ratio_below_the_absolute_cap_is_accepted(tmp_path):
    """Real logs reach ratios above 200x; the cap on the decompressed size is what bounds the cost."""
    info = infos(bomb_zip(tmp_path))[MEMBER]
    (relaxed,) = relax_ratio_violations([info], ReadLimits())
    assert relaxed.limit_violation is None


def test_high_ratio_above_the_absolute_cap_stays_rejected(tmp_path):
    info = infos(bomb_zip(tmp_path))[MEMBER]
    (kept,) = relax_ratio_violations([info], ReadLimits(ratio_exempt_max_bytes=1024 * 1024))
    assert kept.limit_violation == "ratio"


def test_other_violations_are_never_relaxed(tmp_path):
    zpath = make_zip(tmp_path / "a.zip", {MEMBER: b"x" * 5000})
    info = infos(zpath, max_member_bytes=1000)[MEMBER]
    assert info.limit_violation == "too_large"
    (kept,) = relax_ratio_violations([info], ReadLimits())
    assert kept.limit_violation == "too_large"

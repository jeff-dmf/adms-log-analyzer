"""Tests for dmslog.store.filetext: read-only text of non-indexed sources, withheld on a credential mention.

All fixtures are synthetic: no real hosts, accounts or secrets.
"""

import hashlib
import time
import zipfile
from pathlib import Path

import pytest

from dmslog.store.db import connect, create_case_db
from dmslog.store.filetext import (
    BinaryContentError,
    ContainerMissingError,
    FileTextLimits,
    FileTooLargeError,
    NotViewableError,
    SourceNotFoundError,
    credential_mentions,
    read_source_text,
)

SECRET = "SYNTHETIC_9472"  # holds no core word: it must not trigger the detection by itself


# ---------------------------------------------------------------------- case helpers


def add_collection(conn, kind: str, path: Path) -> int:
    cur = conn.execute(
        "INSERT INTO collection(kind, path, added_at) VALUES (?, ?, '2030-01-01T00:00:00')", (kind, str(path))
    )
    return cur.lastrowid


def add_source(conn, cid: int, member: str, size: int, status="skipped", error="not_indexable") -> int:
    cur = conn.execute(
        "INSERT INTO source(collection_id, member, file_name, envelope, size, status, error) "
        "VALUES (?, ?, ?, 'plain', ?, ?, ?)",
        (cid, member, member.rsplit("/", 1)[-1], size, status, error),
    )
    return cur.lastrowid


@pytest.fixture
def case(tmp_path):
    db = tmp_path / "case" / "case.sqlite"
    conn = create_case_db(db)
    conn.commit()
    yield conn, db, tmp_path
    conn.close()


def zip_source(case, name: str, data: bytes, **kwargs):
    conn, _db, tmp = case
    path = tmp / "ers.zip"
    mode = "a" if path.exists() else "w"
    with zipfile.ZipFile(path, mode, zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"Servers/cfg/{name}", data)
    cid = conn.execute("SELECT id FROM collection WHERE path = ?", (str(path),)).fetchone()
    cid = cid[0] if cid else add_collection(conn, "ers_zip", path)
    sid = add_source(conn, cid, f"Servers/cfg/{name}", len(data), **kwargs)
    conn.commit()
    return sid


def read(case, name: str, text: str | bytes, reveal=False):
    data = text.encode("utf-8") if isinstance(text, str) else text
    return read_source_text(case[0], zip_source(case, name, data), reveal=reveal)


# ---------------------------------------------------------------------- detection (all or nothing)

SYN = SECRET


def _u(word: str) -> str:
    """JSON escape of every character: the text contains no letter of the word."""
    return "".join(f"\\u{ord(c):04x}" for c in word)


def _ent(word: str) -> str:
    return "".join(f"&#{ord(c)};" for c in word)


def _held(case, name: str, text: str):
    out = read(case, name, text)
    assert out.withheld and out.text == "" and out.hidden >= 1, text[:80]
    assert SYN not in out.text
    return out


REPRODUCERS = [
    '{"password":"prefix"\\u000aSYNTHETIC_9472"}',
    '<add value="SYNTHETIC_9472" other=">" key="password"/>',
    'Password=" first\nSYNTHETIC_9472\nlast"',
    '{"api:key":"SYNTHETIC_9472"}',
    '{"private/key":"SYNTHETIC_9472"}',
    '{"password"' + " " * 64 + ':"SYNTHETIC_9472"}',
    '{"password":"SYNTHETIC_9472',
    '<add key="password" other=unquoted value="SYNTHETIC_9472"/>',
    "<Password flag=unquoted>SYNTHETIC_9472</Password>",
    '{"' + "x" * 250 + 'password":"SYNTHETIC_9472"}',
    '{"' + _u("x" * 100 + "_password") + '":"SYNTHETIC_9472"}',
    '<Parameter Name="Password">\n\n  SYNTHETIC_9472\n</Parameter>',
    "<Password>\n<![CDATA[SYNTHETIC_9472",
    f'{{"{_u("password")}":"SYNTHETIC_9472"}}',
    f"<{_ent('password')}>SYNTHETIC_9472</{_ent('password')}>",
    "<add key='client secret' value='SYNTHETIC_9472'/>",
    "Shared  Key = SYNTHETIC_9472",
    "pass&amp;#119;ord=SYNTHETIC_9472",
    "p.a.s.s.w.o.r.d: SYNTHETIC_9472",
    "P\tA\nS\rS W-O_R:D=SYNTHETIC_9472",
    "ConnectionString=Server=s;Pwd=SYNTHETIC_9472",
    "TOKEN=SYNTHETIC_9472",
    "api key: SYNTHETIC_9472",
    "<![CDATA[ passphrase: SYNTHETIC_9472 ]]>",
    "<!-- credentials: SYNTHETIC_9472 -->",
    "\uff50\uff41\uff53\uff53\uff57\uff4f\uff52\uff44=SYNTHETIC_9472",
    "pass\u200bword=SYNTHETIC_9472",
    "\\u0070\\u0061ssword=SYNTHETIC_9472",
    "&amp;#x70;assword=SYNTHETIC_9472",
    '<add key="&#00000112;assword" value="SYNTHETIC_9472"/>',
    '<add key="&#x00000070;assword" value="SYNTHETIC_9472"/>',
    "p&Ascr;ssword=SYNTHETIC_9472",
    "pass&Tab;word=SYNTHETIC_9472",
    "pass\u1e83ord=SYNTHETIC_9472",
    "passw\u0301ord=SYNTHETIC_9472",
    "pass&nosuchentity;word=SYNTHETIC_9472",
    "&#x70;&#x61;ssword=SYNTHETIC_9472",
    "p\u0301a\u0300s\u0302s\u0303w\u0308o\u030ar\u0327d\u0323=SYNTHETIC_9472",
    "pass\u200bw\u200cor\u200dd\u2060=SYNTHETIC_9472",
    "pass\u00adword=SYNTHETIC_9472",
    "\u202epass\u202cword\u200f=SYNTHETIC_9472",
    "&password=SYNTHETIC_9472",
    "a=1&password=SYNTHETIC_9472&b=2",
    "&#112;&#97;&#115;&#115;&#119;&#111;&#114;&#100;=SYNTHETIC_9472",
    "&#x0000000000000070;assword=SYNTHETIC_9472",
    "\\U00000070assword=SYNTHETIC_9472",
    "&notoken=SYNTHETIC_9472",
    "pass&notit;word=SYNTHETIC_9472",
    "&amp;notoken=SYNTHETIC_9472",
    "&notokenx=1&v=SYNTHETIC_9472",
    "&amp;amp;password=SYNTHETIC_9472",
    "pass&notin;word=SYNTHETIC_9472",
    "&not;&token=SYNTHETIC_9472",
    "to&nbsp;ken=SYNTHETIC_9472",
    "to&shy;ken=SYNTHETIC_9472",
    "pa&#x00AD;ssword=SYNTHETIC_9472",
    "\\u0026notoken=SYNTHETIC_9472",
    "pass\\u0026notit;word=SYNTHETIC_9472",
    "p&#97;ss&nosuch;word=SYNTHETIC_9472",
    "pass&amp;nosuch;word=SYNTHETIC_9472",
    "pass&amp;notit;word=SYNTHETIC_9472",
    "p&amp;#97;ss&amp;amp;nosuch;word=SYNTHETIC_9472",
    "p\\u0026#97;ss\\u0026nosuch;word=SYNTHETIC_9472",
]


@pytest.mark.parametrize("text", REPRODUCERS)
def test_any_mention_withholds_the_whole_file(case, text):
    out = _held(case, "r.config", text)
    assert out.size > 0 and out.lines >= 1
    revealed = read_source_text(case[0], out.source_id, reveal=True)
    assert revealed.text == text.replace("\r\n", "\n").replace("\r", "\n") and not revealed.withheld
    assert revealed.hidden == out.hidden


def test_the_count_is_the_number_of_mentions():
    assert credential_mentions("a=1") == 0
    assert credential_mentions("password=x") == 1
    assert credential_mentions("password=x\nSecret: y\n<token>z</token>") == 4  # open and close tags
    assert credential_mentions("Pass Word") == 1
    assert credential_mentions("clientSecret") == 1  # one mention, not two ("secret" inside)
    assert credential_mentions("") == 0


@pytest.mark.parametrize(
    "text",
    [
        "bypass compass passport passenger",
        "Compass=north;Bypass=yes",
        '<xs:schema xmlns:xs="x"><xs:element name="Timeout"/></xs:schema>',
        '<appSettings><add key="ServerName" value="host1"/></appSettings>',
        "[section]\nhost = h1\nurl: http://example.test/x\n",
        "",
    ],
)
def test_text_without_a_core_word_has_no_mention(text):
    assert credential_mentions(text) == 0


def test_withheld_file_keeps_encoding_for_utf16(case):
    sid = zip_source(case, "w16.config", b"\xff\xfe" + f"Password={SECRET}".encode("utf-16-le"))
    out = read_source_text(case[0], sid)
    assert out.withheld and out.encoding == "utf-16" and out.size == len(f"Password={SECRET}")


def test_hypothesis_core_word_always_withholds():
    from hypothesis import given, settings
    from hypothesis import strategies as st

    words = ["password", "passwd", "pwd", "passphrase", "secret", "token", "apikey", "privatekey"]
    words += ["sharedkey", "clientsecret", "credential"]
    seps = st.text(alphabet=" \t\r\n.:/-_=\"'<>&;,", max_size=4)
    junk = st.text(
        alphabet=st.characters(blacklist_characters="\\&", blacklist_categories=("Cs",)), max_size=30
    )

    def spelled(word, parts, mode):
        glue = [parts[i % len(parts)] for i in range(len(word) - 1)]
        if mode == 0:  # separators between the letters
            pieces = [c + g for c, g in zip(word, glue + [""], strict=False)]
            return "".join(pieces)
        if mode == 1:  # JSON escapes
            return _u(word)
        if mode == 2:  # character references
            return _ent(word)
        if mode == 3:  # numeric references with leading zeros
            return "".join(f"&#{'0' * len(parts[0])}{ord(c)};" for c in word)
        if mode == 4:  # combining marks and invisible characters between and on the letters
            return "".join(c + "\u0301\u200b" for c in word)
        return word.upper()

    @settings(max_examples=400, deadline=None)
    @given(st.sampled_from(words), st.lists(seps, min_size=1, max_size=4), st.integers(0, 5), junk, junk)
    def check(word, parts, mode, before, after):
        text = f"{before}{spelled(word, parts, mode)}={SYN}{after}"
        assert credential_mentions(text) >= 1, text

    check()


# ---------------------------------------------------------------------- read_source_text


def test_zip_withheld_and_reveal_return_the_real_size_and_lines(case):
    xml = (
        f'<?xml version="1.0"?>\r\n<c>\r\n  <Password>{SECRET}</Password>\r\n'
        "  <Other>x</Other>\r\n  <Host>h1</Host>\r\n</c>\r\n"
    )
    sid = zip_source(case, "app.config", xml.encode())
    held = read_source_text(case[0], sid)
    assert held.withheld and held.text == "" and held.hidden == 2 and not held.revealed
    assert held.kind == "config" and held.name == "app.config" and held.encoding == "utf-8"
    assert held.size == len(xml.encode()) and held.lines == 6  # of the real file, not of the empty text
    raw = read_source_text(case[0], sid, reveal=True)
    assert raw.revealed and not raw.withheld and raw.hidden == 2 and SECRET in raw.text
    assert "\r" not in raw.text and (raw.size, raw.lines) == (held.size, held.lines)


def test_file_without_a_credential_mention_is_returned_intact(case):
    for name, text in (
        (
            "a.xsd",
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="a"/></xs:schema>',
        ),
        (
            "b.config",
            '<configuration><appSettings><add key="Timeout" value="30"/></appSettings></configuration>',
        ),
        ("c.ini", "[section]\nhost = h1\nport = 1\ncompass = north\nbypass = yes\n"),
        ("d.json", '{"user": "u1", "timeout": 30}'),
    ):
        out = read(case, name, text)
        assert out.text == text and out.hidden == 0 and not out.withheld, name


def test_folder_collection(case):
    conn, _db, tmp = case
    folder = tmp / "Log"
    (folder / "cfg").mkdir(parents=True)
    (folder / "cfg" / "x.ini").write_text(f"Password={SECRET}\nhost=h1\n", encoding="utf-8")
    sid = add_source(conn, add_collection(conn, "folder", folder), "cfg/x.ini", 30)
    out = read_source_text(conn, sid)
    assert out.hidden == 1 and out.withheld and out.text == ""
    assert read_source_text(conn, sid, reveal=True).text == f"Password={SECRET}\nhost=h1\n"


def test_loose_file_collection(case):
    conn, _db, tmp = case
    f = tmp / "one.xml"
    f.write_text("<a>1</a>", encoding="utf-8")
    sid = add_source(conn, add_collection(conn, "file", f), "one.xml", 8)
    assert read_source_text(conn, sid).text == "<a>1</a>"


def test_utf8_bom_and_utf16(case):
    assert read(case, "a.config", b"\xef\xbb\xbf<a>caf\xc3\xa9</a>").text == "<a>caf\u00e9</a>"
    out = read(case, "b.config", b"\xff\xfe" + "<a>caf\u00e9 \u20ac</a>".encode("utf-16-le"))
    assert out.text == "<a>caf\u00e9 \u20ac</a>" and out.encoding == "utf-16"
    out = read(case, "c.config", b"\xfe\xff" + "<a>x</a>".encode("utf-16-be"))
    assert out.text == "<a>x</a>"
    sid = zip_source(case, "d.config", b"\xff\xfe" + f"Password={SECRET}".encode("utf-16-le"))
    out = read_source_text(case[0], sid)
    assert out.hidden == 1 and out.withheld and out.text == ""


def test_latin1_fallback_never_raises(case):
    out = read(case, "e.config", b"<a>caf\xe9</a>")
    assert out.text == "<a>caf\u00e9</a>" and out.encoding == "latin-1"
    assert read(case, "f.config", b"<a>\xff\xfe\x80\x81</a>").kind == "config"


def test_empty_file(case):
    out = read(case, "g.config", b"")
    assert out.text == "" and out.lines == 0 and out.size == 0


def test_binary_content_refused(case):
    with pytest.raises(BinaryContentError):
        read(case, "h.config", b"MZ\x90\x00\x03\x00\x00\x00 not text")
    # a NUL after the first 8 KiB is not a binary marker
    assert read(case, "i.config", b"a" * 9000 + b"\x00").size == 9001


def test_size_limit(case):
    ok = read(case, "j.xml", b"a" * (4 * 1024 * 1024))
    assert ok.size == 4 * 1024 * 1024
    sid = zip_source(case, "k.xml", b"a" * (4 * 1024 * 1024 + 1))
    with pytest.raises(FileTooLargeError) as exc:
        read_source_text(case[0], sid)
    assert exc.value.limit == 4 * 1024 * 1024 and exc.value.status == 413


def test_size_limit_checked_on_stream_when_row_size_is_stale(case):
    conn = case[0]
    sid = zip_source(case, "l.xml", b"a" * 5000)
    conn.execute("UPDATE source SET size = 10 WHERE id = ?", (sid,))
    with pytest.raises(FileTooLargeError):
        read_source_text(conn, sid, limits=FileTextLimits(max_bytes=1000))


def test_extension_outside_closed_list(case):
    for name in ("m.db3", "n.blg", "o.dll", "p.txt", "q.log", "noext"):
        sid = zip_source(case, name, b"hello")
        with pytest.raises(NotViewableError):
            read_source_text(case[0], sid)


def test_extension_case_insensitive(case):
    assert read(case, "UPPER.XML", "<a/>").kind == "xml"


def test_indexed_or_other_skipped_sources_not_viewable(case):
    for i, (status, error) in enumerate(
        (("ready", None), ("skipped", "out_of_scope"), ("skipped", "too_large"))
    ):
        sid = zip_source(case, f"r{i}.config", b"<a/>", status=status, error=error)
        with pytest.raises(NotViewableError):
            read_source_text(case[0], sid)


def test_source_not_found(case):
    with pytest.raises(SourceNotFoundError) as exc:
        read_source_text(case[0], 999)
    assert exc.value.status == 404


def test_container_missing(case):
    conn, _db, tmp = case
    sid = zip_source(case, "s.config", b"<a/>")
    (tmp / "ers.zip").unlink()
    with pytest.raises(ContainerMissingError) as exc:
        read_source_text(conn, sid)
    assert exc.value.status == 410
    # folder gone, file gone, member gone, damaged zip
    folder_sid = add_source(conn, add_collection(conn, "folder", tmp / "nope"), "a/b.ini", 5)
    file_sid = add_source(conn, add_collection(conn, "file", tmp / "nope.xml"), "nope.xml", 5)
    bad = tmp / "bad.zip"
    bad.write_bytes(b"not a zip")
    bad_sid = add_source(conn, add_collection(conn, "ers_zip", bad), "a/b.ini", 5)
    good = tmp / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("other.ini", "x")
    miss_sid = add_source(conn, add_collection(conn, "ers_zip", good), "a/b.ini", 5)
    for s in (folder_sid, file_sid, bad_sid, miss_sid):
        with pytest.raises(ContainerMissingError):
            read_source_text(conn, s)


def test_folder_member_traversal_refused(case):
    conn, _db, tmp = case
    (tmp / "Log").mkdir()
    (tmp / "secret.ini").write_text("x=1", encoding="utf-8")
    sid = add_source(conn, add_collection(conn, "folder", tmp / "Log"), "../secret.ini", 3)
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid)


def test_nothing_written_to_the_case(case):
    conn, db, _tmp = case
    sid = zip_source(case, "t.config", f"<Password>{SECRET}</Password>".encode())
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def digest() -> str:
        h = hashlib.sha256()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db) + suffix)
            if p.exists() and suffix != "-shm":
                h.update(p.read_bytes())
        return h.hexdigest()

    before = digest()
    read_source_text(conn, sid)
    read_source_text(conn, sid, reveal=True)
    assert digest() == before
    assert not conn.in_transaction
    # also through a read-only connection
    ro = connect(db, readonly=True)
    try:
        assert read_source_text(ro, sid).withheld
    finally:
        ro.close()
    assert digest() == before
    # nothing about the content in the database
    dump = "\n".join(conn.iterdump())
    assert SECRET not in dump


def test_hostile_content_is_inert(case):
    text = '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/hosts">]><a>&e;</a><script>alert(1)</script>'
    out = read(case, "u.xml", text)
    assert out.text == text  # returned as text, nothing expanded or executed


# A quadratic pass over 4 MiB takes minutes, so this bound still catches it; it is loose because the CI
# runners (Windows included) run these shapes 2 to 4 times slower than a workstation.
HOSTILE_LIMIT_S = 10


def _time_hostile(shapes) -> float:
    size = 4 * 1024 * 1024
    start = time.perf_counter()
    for unit in shapes:
        credential_mentions(unit * (size // len(unit)))
    return time.perf_counter() - start


def test_pathological_input_is_fast():
    """4 MiB of hostile input per shape: the review shapes in a bounded time (the passes are linear)."""
    assert _time_hostile(("<x><![CDATA[", "Password=", "<a ", '<a b="', "Password='")) < HOSTILE_LIMIT_S


@pytest.mark.parametrize(
    "unit",
    [
        '<password key="pwd" ',
        "]]></a>",
        "<add key='token' ",
        "password = ",
        "<x>",
        "password\n",
        "password" + " " * 30 + "\n",
        "<password>\n",
        "\\u0070assword\n",
        "&#112;assword=x\n",
        "<password><![CDATA[\n",
        'password="\n',
        '<a key="password"\n',
        "ab password ",
        "\\\\\\\\",
        "&amp;",
        '\\\\"',
        "password\\\\u0070",
        "&#",
        "&#0000000",
        "&#x",
        "&a;",
        "e\u0301",
        "&amp;#",
        "&amp;",
        "&amp;amp;amp;amp;",
        "p&amp;#97;ss&amp;nosuch;word ",
        "&a;",
        "&",
        "&&",
        "&not",
        "&#0",
        "&a",
        "&#99999999999;",
        "pass&#0000112;&nosuch;\u0301",
    ],
)
def test_other_hostile_shapes_are_fast(unit):
    # html.unescape calls back into Python for every reference-like token: those shapes get a looser bound
    assert _time_hostile((unit,)) < HOSTILE_LIMIT_S


# ---------------------------------------------------------------------- review regressions


def test_symlinked_file_is_refused(case):
    conn, _db, tmp = case
    folder = tmp / "Log"
    folder.mkdir()
    outside = tmp / "outside.ini"
    outside.write_text(f"Password={SECRET}\n", encoding="utf-8")
    link = folder / "x.ini"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    sid = add_source(conn, add_collection(conn, "folder", folder), "x.ini", 20)
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid, reveal=True)


def test_symlinked_directory_is_refused(case):
    conn, _db, tmp = case
    folder = tmp / "Log"
    folder.mkdir()
    outside = tmp / "elsewhere"
    outside.mkdir()
    (outside / "x.ini").write_text(f"Password={SECRET}\n", encoding="utf-8")
    try:
        (folder / "cfg").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    sid = add_source(conn, add_collection(conn, "folder", folder), "cfg/x.ini", 20)
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid, reveal=True)


def test_symlinked_loose_file_is_refused(case):
    conn, _db, tmp = case
    real = tmp / "real.xml"
    real.write_text("<a/>", encoding="utf-8")
    link = tmp / "link.xml"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    sid = add_source(conn, add_collection(conn, "file", link), "link.xml", 4)
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid)


def test_registered_root_may_itself_be_a_symlink(case):
    conn, _db, tmp = case
    real = tmp / "RealLog"
    real.mkdir()
    (real / "x.ini").write_text("host=h1\n", encoding="utf-8")
    root = tmp / "RootLink"
    try:
        root.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    sid = add_source(conn, add_collection(conn, "folder", root), "x.ini", 8)
    assert read_source_text(conn, sid).text == "host=h1\n"


@pytest.mark.parametrize("bom,codec", [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")])
def test_utf16_with_bom_multiline_is_text_not_binary(case, bom, codec):
    text = "<a>\n  <b>one</b>\n  <c>caf\u00e9</c>\n</a>\n" * 3
    out = read(case, f"w_{codec}.config", bom + text.encode(codec))
    assert out.text == text and out.encoding == "utf-16" and out.lines == 12


def test_utf16_with_bom_and_real_nul_in_text_is_binary(case):
    with pytest.raises(BinaryContentError):
        read(case, "x.config", b"\xff\xfe" + "ab\x00cd".encode("utf-16-le"))


def test_utf16_without_bom_is_binary(case):
    for codec in ("utf-16-le", "utf-16-be"):
        with pytest.raises(BinaryContentError):
            read(case, f"y_{codec}.config", "<a>hello</a>\n".encode(codec))


# ---------------------------------------------------------------------- one handle, no redirect


def _symlink_or_skip(link: Path, target: Path, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")


def _swap_with_symlink(path: Path, target: Path, directory: bool = False) -> None:
    backup = path.with_name(path.name + ".orig")
    path.rename(backup)
    _symlink_or_skip(path, target, directory)


@pytest.fixture
def local_case(case):
    conn, _db, tmp = case
    folder = tmp / "Log"
    (folder / "cfg").mkdir(parents=True)
    body = "host=h1\n" * 30_000  # 240 KB: several read blocks
    (folder / "cfg" / "x.ini").write_text(body, encoding="utf-8")
    outside = tmp / "outside"
    outside.mkdir()
    (outside / "x.ini").write_text(f"host=h2\nPassword={SYN}\n" + body, encoding="utf-8")
    sid = add_source(conn, add_collection(conn, "folder", folder), "cfg/x.ini", len(body))
    return conn, sid, folder, outside, body


def _hook_open(monkeypatch, when: str, action):
    """Run ``action`` just before the first ``os.open`` whose path argument is ``when``."""
    from dmslog.store import filetext

    real = filetext._os_open
    done = []

    def wrapper(path, *args, **kwargs):
        if str(path).endswith(when) and not done:
            done.append(1)
            action()
        return real(path, *args, **kwargs)

    monkeypatch.setattr(filetext, "_os_open", wrapper)


def _hook_read(monkeypatch, nth: int, action):
    from dmslog.store import filetext

    real = filetext._read_block
    calls = []

    def wrapper(fh, size):
        calls.append(1)
        if len(calls) == nth:
            action()
        return real(fh, size)

    monkeypatch.setattr(filetext, "_read_block", wrapper)
    return calls


@pytest.mark.parametrize("fallback", [False, True])
def test_file_swapped_for_symlink_before_open_is_refused(local_case, monkeypatch, fallback):
    from dmslog.store import filetext

    conn, sid, folder, outside, _body = local_case
    monkeypatch.setattr(filetext, "_HAVE_DIR_FD", filetext._HAVE_DIR_FD and not fallback)
    _hook_open(monkeypatch, "x.ini", lambda: _swap_with_symlink(folder / "cfg" / "x.ini", outside / "x.ini"))
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid, reveal=True)


@pytest.mark.parametrize("fallback", [False, True])
def test_intermediate_directory_swapped_before_open_is_refused(local_case, monkeypatch, fallback):
    from dmslog.store import filetext

    conn, sid, folder, outside, _body = local_case
    monkeypatch.setattr(filetext, "_HAVE_DIR_FD", filetext._HAVE_DIR_FD and not fallback)
    # the directory is opened by name ("cfg") with directory handles, by the whole path otherwise (no dir_fd on
    # Windows, where the fallback is the only path)
    target = "cfg" if filetext._HAVE_DIR_FD else "x.ini"
    _hook_open(monkeypatch, target, lambda: _swap_with_symlink(folder / "cfg", outside, directory=True))
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid, reveal=True)


def test_swap_between_read_blocks_never_returns_outside_content(local_case, monkeypatch):
    conn, sid, folder, outside, body = local_case
    calls = _hook_read(
        monkeypatch, 2, lambda: _swap_with_symlink(folder / "cfg" / "x.ini", outside / "x.ini")
    )
    try:
        out = read_source_text(conn, sid, reveal=True)
    except ContainerMissingError:
        out = None
    assert len(calls) >= 2  # the swap really happened between two blocks
    assert out is None or (SYN not in out.text and out.text == body)


def test_swap_between_read_blocks_in_fallback_is_detected(local_case, monkeypatch):
    from dmslog.store import filetext

    conn, sid, folder, outside, _body = local_case
    monkeypatch.setattr(filetext, "_HAVE_DIR_FD", False)
    _hook_read(monkeypatch, 2, lambda: _swap_with_symlink(folder / "cfg" / "x.ini", outside / "x.ini"))
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid, reveal=True)


def test_directory_swapped_between_read_blocks_never_returns_outside_content(local_case, monkeypatch):
    conn, sid, folder, outside, body = local_case
    _hook_read(monkeypatch, 2, lambda: _swap_with_symlink(folder / "cfg", outside, directory=True))
    try:
        out = read_source_text(conn, sid, reveal=True)
    except ContainerMissingError:
        out = None
    assert out is None or (SYN not in out.text and out.text == body)


def test_read_is_done_from_one_handle(local_case, monkeypatch):
    from dmslog.store import filetext

    conn, sid, *_ = local_case
    opened = []
    real = filetext._os_open
    monkeypatch.setattr(filetext, "_os_open", lambda p, *a, **k: opened.append(str(p)) or real(p, *a, **k))
    read_source_text(conn, sid)
    assert [p for p in opened if p.endswith("x.ini")] == opened[-1:] and len(
        [p for p in opened if p.endswith("x.ini")]
    ) == 1  # the file itself is opened exactly once


def test_zip_that_is_a_symlink_is_refused(case):
    conn, _db, tmp = case
    real = tmp / "real.zip"
    with zipfile.ZipFile(real, "w") as zf:
        zf.writestr("cfg/a.config", f"Password={SYN}")
    link = tmp / "link.zip"
    _symlink_or_skip(link, real)
    sid = add_source(conn, add_collection(conn, "ers_zip", link), "cfg/a.config", 20)
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid, reveal=True)


def test_zip_swapped_for_symlink_before_open_is_refused(case, monkeypatch):
    conn, _db, tmp = case
    sid = zip_source(case, "z.config", f"Password={SYN}".encode())
    other = tmp / "other.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("Servers/cfg/z.config", f"Password={SYN}")
    _hook_open(monkeypatch, "ers.zip", lambda: _swap_with_symlink(tmp / "ers.zip", other))
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid, reveal=True)


def test_intermediate_directory_swapped_between_check_and_lstat_is_refused(local_case, monkeypatch):
    """Fallback path: the folder becomes a link after the components were checked, before the file is
    opened; the handle then points outside, and the path of the handle gives it away."""
    from dmslog.store import filetext

    conn, sid, folder, outside, _body = local_case
    monkeypatch.setattr(filetext, "_HAVE_DIR_FD", False)
    real = filetext._os_lstat
    calls = []

    def wrapper(path):
        if str(path).endswith("x.ini"):
            calls.append(1)
            if len(calls) == 2:  # the "before" lstat, right after _check_components
                _swap_with_symlink(folder / "cfg", outside, directory=True)
        return real(path)

    monkeypatch.setattr(filetext, "_os_lstat", wrapper)
    with pytest.raises(ContainerMissingError):
        read_source_text(conn, sid, reveal=True)
    assert len(calls) >= 2


def test_handle_path_matches_the_opened_file(local_case):
    import os

    from dmslog.store import filetext

    _conn, _sid, folder, _outside, _body = local_case
    fd = os.open(folder / "cfg" / "x.ini", os.O_RDONLY)
    try:
        actual = filetext._handle_path(fd)
    finally:
        os.close(fd)
    if actual is None:
        pytest.skip("no handle path on this platform")
    assert filetext._norm(actual) == filetext._norm(os.path.realpath(folder / "cfg" / "x.ini"))


def test_huge_numeric_references_neither_crash_nor_hide_a_mention():
    assert credential_mentions("&#" + "9" * 5000 + ";") == 0
    assert credential_mentions("&#x" + "f" * 5000 + ";") == 0
    assert credential_mentions("&#" + "0" * 5000 + "112;assword") == 1
    assert credential_mentions("&#x" + "0" * 5000 + "70;assword") == 1


def test_hypothesis_entities_and_punctuation_between_letters_or_glued_before():
    from hypothesis import given, settings
    from hypothesis import strategies as st

    words = ["password", "passwd", "pwd", "passphrase", "secret", "token", "apikey", "privatekey"]
    words += ["sharedkey", "clientsecret", "credential"]
    # between letters: punctuation, or a reference closed by ";" (a bare "&not" there reads as a different
    # character, not as a gap)
    between = st.one_of(
        st.text(alphabet=" \t\r\n.:/-_=\"'<>,", max_size=3),
        st.from_regex(r"&[a-z]{1,8};", fullmatch=True),
        st.from_regex(r"&#x?[0-9a-fA-F]{1,6};", fullmatch=True),
        st.sampled_from(["&amp;", "&nbsp;", "&shy;", "&notit;", "&nosuch;", "&#x00AD;", "&#0000173;"]),
    )
    # glued before the word: any "&[a-z]+;?"
    before = st.one_of(st.just(""), st.from_regex(r"&[a-z]{1,8};?", fullmatch=True))

    @settings(max_examples=400, deadline=None)
    @given(st.sampled_from(words), st.lists(between, min_size=1, max_size=5), before, st.booleans())
    def check(word, parts, prefix, split):
        if split:
            glue = [parts[i % len(parts)] for i in range(len(word) - 1)]
            spelled = "".join(c + g for c, g in zip(word, glue + [""], strict=False))
        else:
            spelled = word
        assert credential_mentions(f"{prefix}{spelled}={SYN}") >= 1

    check()


def test_hypothesis_composed_encodings_never_hide_a_mention():
    """Any composition of per-letter encodings and per-position insertions still counts as a mention.

    The one documented exception is a legacy reference WITHOUT ";" that really forms another character
    inside the word (``to&notken`` reads ``to`` + not-sign + ``ken``): nothing in the file spells the
    word then, so a bare ``&name`` is only inserted followed by a punctuation character.
    """
    import html as html_module

    from hypothesis import given, settings
    from hypothesis import strategies as st

    words = ["password", "passwd", "pwd", "secret", "token", "apikey", "privatekey", "clientsecret"]
    words += ["credential"]

    def named(letter: str) -> str | None:
        for suffix in ("scr", "opf", "fr"):
            if f"{letter}{suffix};" in html_module.entities.html5:
                return f"&{letter}{suffix};"
        return None

    def encode_letter(letter: str, how: int, zeros: int, amps: int) -> str:
        code = ord(letter)
        if how == 1:
            enc = f"&#{'0' * zeros}{code};"
        elif how == 2:
            enc = f"&#x{'0' * zeros}{code:x};"
        elif how == 3:
            enc = named(letter) or f"&#{code};"
        elif how == 4:
            enc = f"\\u{code:04x}"
        else:
            return letter
        for _ in range(amps):  # "&" written as "&amp;" up to three times
            enc = enc.replace("&", "&amp;") if how != 4 else enc
        return enc

    inserts = st.sampled_from(
        [
            "&nosuch;",
            "&notit;",
            "&nbsp;",
            "&shy;",
            "&#99999999999;",
            "&#xZZ;",
            "&#;",
            "&nosuch.",
            "&nosuch ",
            "&x-",
            "\u0301",
            "\u200b",
            "\u00ad",
            "\u202e",
            ".",
            "-",
            "_",
            " ",
            "\t",
            "\n",
            ":",
            "&amp;nosuch;",
            "&amp;amp;nosuch;",
        ]
    )

    @settings(max_examples=500, deadline=None)
    @given(
        st.sampled_from(words),
        st.lists(st.tuples(st.integers(0, 4), st.integers(0, 6), st.integers(0, 3)), min_size=8, max_size=16),
        st.lists(st.one_of(st.just(""), inserts), min_size=8, max_size=16),
        st.booleans(),
        st.integers(0, 2),
    )
    def check(word, encodings, gaps, upper, outer_amps):
        parts = []
        for i, letter in enumerate(word):
            letter = letter.upper() if upper and i % 2 == 0 else letter
            how, zeros, amps = encodings[i % len(encodings)]
            parts.append(encode_letter(letter, how, zeros, amps))
            if i < len(word) - 1:
                parts.append(gaps[i % len(gaps)])
        spelled = "".join(parts)
        for _ in range(outer_amps):  # the whole text escaped once more
            spelled = spelled.replace("&", "&amp;")
        assert credential_mentions(f"{spelled}={SYN}") >= 1, spelled

    check()

"""Tests for dmslog.store.filetext: read-only text of non-indexed sources, with credentials hidden.

All fixtures are synthetic: no real hosts, accounts or secrets.
"""

import hashlib
import time
import zipfile
from pathlib import Path

import pytest

from dmslog.store.db import connect, create_case_db
from dmslog.store.filetext import (
    MASK,
    BinaryContentError,
    ContainerMissingError,
    FileTextLimits,
    FileTooLargeError,
    NotViewableError,
    SourceNotFoundError,
    hide_credentials,
    is_credential_name,
    read_source_text,
)

SECRET = "example-secret"


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


def hidden_text(text: str) -> tuple[str, int]:
    return hide_credentials(text)


# ---------------------------------------------------------------------- credential names


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "Password",
        "PASSWORD",
        "DbPassword",
        "db_password",
        "user-pwd",
        "Pwd",
        "secret",
        "ClientSecret",
        "client_secret",
        "api-key",
        "ApiKey",
        "apikey",
        "PrivateKey",
        "shared_key",
        "authToken",
        "token",
        "Credential",
        "pass",
    ],
)
def test_credential_names_positive(name):
    assert is_credential_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "compass",
        "bypass",
        "tokenizer",
        "ServerName",
        "timeout",
        "key",
        "user",
        "passenger",
        "connectionString",
    ],
)
def test_credential_names_negative(name):
    assert not is_credential_name(name)


# ---------------------------------------------------------------------- rule 1: connection pairs


def test_connection_string_pairs_hidden_rest_kept():
    text = (
        f'<add name="Db" connectionString="Server=host1;Database=d;User Id=u1;Password={SECRET};Pooling=1"/>'
    )
    out, n = hidden_text(text)
    assert n == 1
    assert SECRET not in out
    assert f"Password={MASK};Pooling=1" in out
    assert "Server=host1;Database=d;User Id=u1" in out


@pytest.mark.parametrize("variant", ["password", "PASSWORD", "Pwd", "pWd", "Secret"])
def test_connection_pair_case_and_spaces(variant):
    out, n = hidden_text(f"Server=s;{variant} = {SECRET};Port=1")
    assert n == 1 and SECRET not in out and "Port=1" in out


def test_connection_pair_quoted_value_with_semicolon():
    out, n = hidden_text("Server=s;Password='ab;" + SECRET + "';Port=1")
    assert n == 1 and SECRET not in out and out == f"Server=s;Password='{MASK}';Port=1"


def test_connection_pair_negative():
    text = "Server=s;Passport=123;Database=d;Pwdx=1;MyPassword2=x"
    out, n = hidden_text(text)
    assert out == text and n == 0


def test_connection_pair_empty_value_not_counted():
    out, n = hidden_text("Server=s;Password=;Port=1")
    assert out == "Server=s;Password=;Port=1" and n == 0


# ---------------------------------------------------------------------- rule 2: <add key value>


@pytest.mark.parametrize(
    "tag",
    [
        f'<add key="DbPassword" value="{SECRET}" />',
        f'<add value="{SECRET}" key="DbPassword"/>',
        f'<add name="ApiKey" value="{SECRET}"/>',
        f"<add KEY='PASSWORD' VALUE='{SECRET}'/>",
        f'<add   key = "Secret"   value = "{SECRET}"   />',
    ],
)
def test_add_key_value_hidden(tag):
    out, n = hidden_text(tag)
    assert n == 1 and SECRET not in out and MASK in out


def test_add_key_value_negative():
    text = '<add key="Timeout" value="30"/><add key="ServerName" value="host1"/>'
    out, n = hidden_text(text)
    assert out == text and n == 0


def test_add_other_attributes_kept():
    out, _ = hidden_text(f'<add key="Token" value="{SECRET}" description="keep me"/>')
    assert 'description="keep me"' in out and 'key="Token"' in out


# ---------------------------------------------------------------------- rule 3: elements


def test_element_with_credential_name_hidden():
    out, n = hidden_text(f"<Config>\n  <Password>{SECRET}</Password>\n</Config>")
    assert n == 1 and SECRET not in out
    assert f"<Password>{MASK}</Password>" in out and out.startswith("<Config>")


@pytest.mark.parametrize("tag", ["password", "PASSWORD", "dbPwd", "ClientSecret"])
def test_element_names_case(tag):
    out, n = hidden_text(f"<{tag}>{SECRET}</{tag}>")
    assert n == 1 and SECRET not in out


def test_element_with_credential_name_attribute_hidden():
    out, n = hidden_text(f'<Parameter Name="Password" Type="String">{SECRET}</Parameter>')
    assert n == 1 and SECRET not in out and 'Type="String"' in out


def test_element_cdata_hidden():
    out, n = hidden_text(f"<token><![CDATA[{SECRET}]]></token>")
    assert n == 1 and SECRET not in out


def test_element_negative():
    text = '<ServerName>host1</ServerName><Timeout>30</Timeout><Parameter Name="Mode">fast</Parameter>'
    out, n = hidden_text(text)
    assert out == text and n == 0


def test_connection_string_element_only_pairs_hidden():
    out, n = hidden_text(f"<ConnectionString>Server=s;Pwd={SECRET}</ConnectionString>")
    assert n == 1 and "Server=s;" in out and SECRET not in out
    assert out.startswith("<ConnectionString>")


def test_empty_element_not_counted():
    out, n = hidden_text("<Password></Password><Password/>")
    assert out == "<Password></Password><Password/>" and n == 0


# ---------------------------------------------------------------------- rule 4: attributes


@pytest.mark.parametrize(
    "attr", ["password", "Password", "PWD", "ApiKey", "client-secret", "authToken", "DbPassword"]
)
def test_xml_attribute_hidden(attr):
    out, n = hidden_text(f'<Server Host="h1" {attr}="{SECRET}" Port="1"/>')
    assert n == 1 and SECRET not in out
    assert 'Host="h1"' in out and 'Port="1"' in out


def test_xml_attribute_single_quotes_and_spaces():
    out, n = hidden_text(f"<Server Password = '{SECRET}' Port='1'/>")
    assert n == 1 and SECRET not in out and "Port='1'" in out


def test_xml_attribute_negative():
    text = '<Server Host="h1" Compass="north" Bypass="yes" tokenizer="x" Port="1"/>'
    out, n = hidden_text(text)
    assert out == text and n == 0


def test_connection_string_attribute_not_hidden_whole():
    text = '<Db connectionString="Server=h1;Database=d"/>'
    out, n = hidden_text(text)
    assert out == text and n == 0


# ---------------------------------------------------------------------- rule 5: JSON


def test_json_member_hidden():
    out, n = hidden_text(f'{{"user": "u1", "password": "{SECRET}", "apiKey" : "{SECRET}"}}')
    assert n == 2 and SECRET not in out and '"user": "u1"' in out


def test_json_member_negative():
    text = '{"user": "u1", "timeout": "30", "compass": "x"}'
    out, n = hidden_text(text)
    assert out == text and n == 0


# ---------------------------------------------------------------------- rule 6: ini lines


@pytest.mark.parametrize(
    "line",
    [
        f"password = {SECRET}",
        f"Password={SECRET}",
        f"PASSWORD:   {SECRET}",
        f"  db.pwd = {SECRET}",
        f"Shared Key = {SECRET}",
        f"client_secret={SECRET}",
    ],
)
def test_ini_line_hidden(line):
    out, n = hidden_text(f"[section]\nhost = h1\n{line}\nport = 1\n")
    assert n >= 1 and SECRET not in out
    assert "host = h1" in out and "port = 1" in out and "[section]" in out


def test_ini_line_negative():
    text = "[section]\nhost = h1\ncompass = north\ntokenizer = x\nurl: http://example.test/x\n"
    out, n = hidden_text(text)
    assert out == text and n == 0


def test_ini_does_not_touch_xml_lines():
    text = "<a>\n  <name>ok</name>\n</a>"
    out, n = hidden_text(text)
    assert out == text and n == 0


def test_already_masked_not_counted_twice():
    out, n = hidden_text(f"Password={SECRET}\n")
    assert n == 1 and out == f"Password={MASK}\n"
    out2, n2 = hidden_text(out)
    assert out2 == out and n2 == 0


# ---------------------------------------------------------------------- read_source_text


def test_zip_reveal_returns_original_hidden_counts(case):
    xml = f'<?xml version="1.0"?>\r\n<c>\r\n  <Password>{SECRET}</Password>\r\n  <Host>h1</Host>\r\n</c>\r\n'
    sid = zip_source(case, "app.config", xml.encode())
    shown = read_source_text(case[0], sid)
    assert shown.hidden == 1 and not shown.revealed
    assert SECRET not in shown.text and MASK in shown.text and "<Host>h1</Host>" in shown.text
    assert "\r" not in shown.text and shown.kind == "config" and shown.name == "app.config"
    assert shown.encoding == "utf-8" and shown.lines == 5
    raw = read_source_text(case[0], sid, reveal=True)
    assert raw.revealed and raw.hidden == 1 and SECRET in raw.text and MASK not in raw.text


def test_folder_collection(case):
    conn, _db, tmp = case
    folder = tmp / "Log"
    (folder / "cfg").mkdir(parents=True)
    (folder / "cfg" / "x.ini").write_text(f"Password={SECRET}\nhost=h1\n", encoding="utf-8")
    sid = add_source(conn, add_collection(conn, "folder", folder), "cfg/x.ini", 30)
    out = read_source_text(conn, sid)
    assert out.hidden == 1 and "host=h1" in out.text and SECRET not in out.text


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
    assert read_source_text(case[0], sid).hidden == 1


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
        assert read_source_text(ro, sid).hidden == 1
    finally:
        ro.close()
    assert digest() == before
    # nothing about the content in the database
    dump = "\n".join(conn.iterdump())
    assert SECRET not in dump


def test_hostile_content_is_inert(case):
    text = '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><a>&e;</a><script>alert(1)</script>'
    out = read(case, "u.xml", text)
    assert out.text == text  # returned as text, nothing expanded or executed


def _time_hostile(shapes) -> float:
    size = 4 * 1024 * 1024
    start = time.perf_counter()
    for unit in shapes:
        hide_credentials(unit * (size // len(unit)))
    return time.perf_counter() - start


def test_pathological_input_is_fast():
    """4 MiB of hostile input per shape: the four review shapes in 2 s in total (the passes are linear)."""
    assert _time_hostile(("<x><![CDATA[", "Password=", "<a ", '<a b="', "Password='")) < 2


@pytest.mark.parametrize(
    "unit", ['<password key="pwd" ', "]]></a>", "<add key='token' ", "password = ", "<x>"]
)
def test_other_hostile_shapes_are_fast(unit):
    assert _time_hostile((unit,)) < 2


# ---------------------------------------------------------------------- review regressions


def test_cdata_with_whitespace_and_newlines_around_is_hidden():
    for text in (
        f"<Password>\n<![CDATA[{SECRET}]]>\n</Password>",
        f"<Password>  \t<![CDATA[{SECRET}]]>  </Password>",
        f"<Password>\n  {SECRET}\n</Password>",
        f"<Password>part1<![CDATA[{SECRET}]]></Password>",
    ):
        out, n = hidden_text(text)
        assert n == 1 and SECRET not in out, text
        assert out.startswith("<Password>") and out.endswith("</Password>")


def test_multiline_attribute_and_other_quote_kind_inside_value():
    for text in (
        f'<Server ApiKey=\n  "{SECRET}\nsecond line" Port="1"/>',
        f'<Server ApiKey="{SECRET}\'last" Port="1"/>',
        f'<Server ApiKey=\'{SECRET}"last\' Port="1"/>',
        f'<Server\n  Password\n=\n"{SECRET}"\n/>',
    ):
        out, n = hidden_text(text)
        assert n == 1 and SECRET not in out, text


@pytest.mark.parametrize(
    "text",
    [
        f'Server=s;Password="first""{SECRET}";Port=1',
        f"Server=s;Password='first''{SECRET}';Port=1",
        f'Server=s;Pwd="a""b""{SECRET}";Port=1',
        f"<Db connectionString=\"Server=s;Password='first''{SECRET}';Port=1\"/>",
    ],
)
def test_connection_password_with_doubled_quotes_fully_hidden(text):
    out, n = hidden_text(text)
    assert n == 1 and SECRET not in out and "first" not in out and "Port=1" in out


def test_json_escaped_key_and_value():
    cases = (
        f'{{"\\u0070assword":"{SECRET}"}}',
        f'{{"pass\\u0077ord":"{SECRET}"}}',
        f'{{"api-key":"{SECRET}"}}',
        f'{{"password":"ab\\"{SECRET}\\"cd","x":"y"}}',
        '{"password":"\\u0065xample-secret"}',
        f'{{ "password" :\n "{SECRET}" }}',
    )
    for text in cases:
        out, n = hidden_text(text)
        assert n >= 1 and SECRET not in out and "xample-secret" not in out, text
    out, _ = hidden_text(f'{{"password":"ab\\"{SECRET}\\"cd","x":"y"}}')
    assert out == f'{{"password":"{MASK}","x":"y"}}'


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


# ---------------------------------------------------------------------- adversarial pass


def _variants(secret: str) -> list[str]:
    out: list[str] = []
    names = [
        "Password",
        "PASSWORD",
        "password",
        "pWd",
        "DbPassword",
        "api-key",
        "ApiKey",
        "client_secret",
        "authToken",
    ]
    for name in names:
        for w in ["", " ", "\t", "  \t"]:
            for q in ('"', "'"):
                other = "'" if q == '"' else '"'
                out.append(f'<Server {name}{w}={w}{q}{secret}{q} Port="1"/>')
                out.append(f'<Server {name}{w}={w}{q}{secret}{other}x{q} Port="1"/>')
                out.append(f'<Server Port="1"\r\n  {name}{w}={w}{q}{secret}{q}/>')
                out.append(f"Server=s;{name}{w}={w}{q}{secret}{q};Port=1")
                out.append(f"Server=s;{name}{w}={w}{q}ab{q}{q}{secret}{q};Port=1")
                out.append(f'{{"{name}"{w}:{w}"{secret}"}}')
            out.append(f"Server=s;{name}{w}={w}{secret};Port=1")
            out.append(f"{name}{w}={w}{secret}\r\nnext=1\r\n")
            out.append(f"{name}{w}:{w}{secret}\n")
            out.append(f"<{name}>{w}{secret}{w}</{name}>")
            out.append(f'<{name} attr="1">\r\n<![CDATA[{secret}]]>\r\n</{name}>')
            out.append(f"<!-- c --><{name}>{secret}</{name}><!-- d -->")
            out.append(f'<add key{w}={w}"{name}" value{w}={w}"{secret}"/>')
            out.append(f"<add value='{secret}' name='{name}' />")
            out.append(f'<add\r\n  key="{name}"\r\n  value="{secret}"\r\n/>')
    for cs in ("connectionString", "ConnectionString", "connection-string"):
        out.append(f'<add name="x" {cs}="Data Source=h;User Id=u;pwd={secret};"/>')
        out.append(f"<{cs}>Server=s;PASSWORD = {secret}</{cs}>")
        out.append(f"{cs} = Server=s;Pwd={secret}")
    return out


def test_adversarial_variants_never_show_the_secret():
    variants = _variants(SECRET)
    assert len(variants) > 500
    for text in variants:
        out, n = hide_credentials(text)
        assert SECRET not in out and n >= 1, text
        again, n2 = hide_credentials(out)
        assert again == out and n2 == 0, text  # idempotent: the mask is not counted again


def test_hypothesis_secret_never_shown():
    from hypothesis import given, settings
    from hypothesis import strategies as st

    names = st.sampled_from(
        ["password", "Password", "PWD", "pwd", "DbPassword", "api_key", "ApiKey", "token", "secret"]
    )
    ws = st.text(alphabet=" \t", max_size=3)
    quote = st.sampled_from(['"', "'"])
    junk = st.text(alphabet="abc <>/=;\n", max_size=12)

    @settings(max_examples=300, deadline=None)
    @given(names, ws, ws, quote, st.integers(0, 5), junk, junk)
    def check(name, w1, w2, q, shape, before, after):
        body = {
            0: f"<S {name}{w1}={w2}{q}{SECRET}{q}/>",
            1: f"{name}{w1}={w2}{SECRET}\n",
            2: f"<{name}>{w1}{SECRET}{w2}</{name}>",
            3: f'<add key="{name}" value="{SECRET}"/>',
            4: f"Server=s;{name}{w1}={w2}{q}{SECRET}{q};",
            5: f'{{"{name}"{w1}:{w2}"{SECRET}"}}',
        }[shape]
        out, _ = hide_credentials(f"{before}\n{body}\n{after}")
        assert SECRET not in out

    check()


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

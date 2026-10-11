"""The no-pack guard recognises the pack by content, looks inside archives and fails safe."""
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "packaging" / "check_no_pack.py"
MINI_PACK = ROOT / "tests" / "fixtures" / "knowledge" / "mini_pack"
FIXTURE_REL = Path("tests", "fixtures", "knowledge", "mini_pack")
NOTICE = ("This pack is derived from Schneider Electric documentation (synthetic wording for the test). "
          "The repository must stay private; do not publish this pack, nor artifacts that embed it.")
MANIFEST = ('{"schema_version": 1, "generator_version": "1.0.0", "vault_commit": null, '
            f'"notice": "{NOTICE}", "files": {{}}}}').encode()
MANIFEST_NO_NOTICE_TEXT = (b'{"schema_version": 1, "generator_version": "1.0.0", '
                           b'"notice": "x", "files": {}}')



def load_packaging_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def track(root, files, force=False):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
    cmd = ["git", "add", *(["-f"] if force else []), "--", *files]
    subprocess.run(cmd, cwd=root, check=True)


def run_guard(root, *artifacts):
    args = [sys.executable, str(GUARD), "--root", str(root)]
    if artifacts:
        args += ["--artifact", *map(str, artifacts)]
    return subprocess.run(args, capture_output=True, text=True)


def zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def assert_blocked(result, *fragments):
    assert result.returncode == 1, result.stderr or result.stdout
    for fragment in fragments:
        assert fragment in result.stderr


def test_clean_repo_and_the_synthetic_fixture_pass(repo):
    shutil.copytree(MINI_PACK, repo / FIXTURE_REL)
    track(repo, {"README.md": "hello"})
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    result = run_guard(repo)
    assert result.returncode == 0, result.stderr


def test_the_repository_itself_passes():
    assert subprocess.run([sys.executable, str(GUARD)], capture_output=True).returncode == 0


@pytest.mark.parametrize("name", ["private_kb/metadata.json", "manifest.dat", "notes/readme.txt"])
def test_renamed_manifest_is_caught_by_content(repo, name):
    track(repo, {name: MANIFEST})
    assert_blocked(run_guard(repo), name, "knowledge pack manifest")


def test_renamed_manifest_with_only_the_structure_is_caught(repo):
    track(repo, {"kb/info.bin": MANIFEST_NO_NOTICE_TEXT})
    assert_blocked(run_guard(repo), "kb/info.bin")


def test_a_single_renamed_data_file_is_caught_by_shape(repo):
    track(repo, {"kb/a.dat": (MINI_PACK / "operations.json").read_bytes()})
    assert_blocked(run_guard(repo), "kb/a.dat", "data file")


def test_the_four_data_files_together_in_another_folder_are_caught(repo):
    track(repo, {f"elsewhere/{f}": b"{}" for f in
                 ("adapters.json", "error_codes.json", "operations.json", "parameters.json")})
    assert_blocked(run_guard(repo), "elsewhere/", "data file set")


def test_the_synthetic_fixture_is_allowed_only_in_its_folder(repo):
    shutil.copytree(MINI_PACK, repo / "tests" / "fixtures" / "other")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    assert_blocked(run_guard(repo), "tests/fixtures/other/")


def test_tracked_zip_with_a_pack_path_member_is_caught(repo):
    track(repo, {"data/blob.zip": zip_bytes({"knowledge/pack/manifest.json": "{}"})})
    assert_blocked(run_guard(repo), "data/blob.zip", "knowledge/pack")


def test_tracked_zip_with_a_renamed_manifest_member_is_caught(repo):
    track(repo, {"data/blob.bin": zip_bytes({"x/y.dat": MANIFEST})})
    assert_blocked(run_guard(repo), "data/blob.bin", "x/y.dat")


def test_tracked_tarball_is_inspected(repo):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("a/b.dat")
        info.size = len(MANIFEST)
        tar.addfile(info, io.BytesIO(MANIFEST))
    track(repo, {"dist/x.tgz": buffer.getvalue()})
    assert_blocked(run_guard(repo), "a/b.dat")


def test_nested_archive_inside_an_artifact_is_caught(tmp_path):
    inner = zip_bytes({"knowledge/pack/manifest.json": "{}"})
    artifact = tmp_path / "dmslog.pyz"
    artifact.write_bytes(zip_bytes({"dmslog/web/extra.zip": inner}))
    module = load_packaging_module("check_no_pack")
    problems = module.artifact_problems(artifact)
    assert problems and "extra.zip" in problems[0]
    folder = tmp_path / "folder"
    (folder / "app").mkdir(parents=True)
    (folder / "app" / "wrapped.dat").write_bytes(zip_bytes({"x/m.json": MANIFEST}))
    assert module.artifact_problems(folder)


def test_force_added_pyz_under_an_ignored_folder_is_inspected(repo):
    track(repo, {".gitignore": "dist/\n"})
    track(repo, {"dist/forced.pyz": zip_bytes({"dmslog/knowledge/pack/adapters.json": "{}"})}, force=True)
    assert_blocked(run_guard(repo), "dist/forced.pyz")


def test_lfs_pointer_is_blocked(repo):
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 10\n"
    track(repo, {"docs/other/manifest.json": pointer})
    assert_blocked(run_guard(repo), "docs/other/manifest.json", "LFS")


def test_lfs_filter_in_gitattributes_is_blocked(repo):
    track(repo, {".gitattributes": "*.json filter=lfs diff=lfs merge=lfs -text\n"})
    assert_blocked(run_guard(repo), ".gitattributes", "LFS")


def test_the_guard_reads_the_index_not_the_filtered_checkout(repo):
    track(repo, {"a/manifest.json": MANIFEST})
    (repo / "a" / "manifest.json").write_text("{}")  # the working tree no longer shows it
    assert_blocked(run_guard(repo), "a/manifest.json")


def test_corrupt_archive_fails_safe(repo):
    track(repo, {"data/broken.zip": b"PK\x03\x04 this is not a zip"})
    assert_blocked(run_guard(repo), "data/broken.zip", "cannot be inspected")


def test_limits_fail_safe(monkeypatch, tmp_path):
    module = load_packaging_module("check_no_pack")
    many = tmp_path / "many.zip"
    many.write_bytes(zip_bytes({f"f{i}.txt": "x" for i in range(5)}))
    monkeypatch.setattr(module, "MAX_MEMBERS", 3)
    assert any("cannot be inspected" in p for p in module.artifact_problems(many))
    monkeypatch.setattr(module, "MAX_MEMBERS", 100)
    assert module.artifact_problems(many) == []

    data = b"x"
    for _ in range(module.MAX_DEPTH + 2):
        data = zip_bytes({"inner.zip": data})
    deep = tmp_path / "deep.zip"
    deep.write_bytes(data)
    assert any("deeper" in p for p in module.artifact_problems(deep))

    monkeypatch.setattr(module, "MAX_MEMBER_BYTES", 10)
    big = tmp_path / "big.zip"
    big.write_bytes(zip_bytes({"a.txt": "y" * 50}))
    assert any("cannot be inspected" in p for p in module.artifact_problems(big))


def test_oversized_tracked_file_fails_safe(repo, monkeypatch):
    track(repo, {"data/huge.bin": b"0" * 200})
    module = load_packaging_module("check_no_pack")
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 100)
    with module.git_index_reader(repo) as read:
        problems = module.tracked_problems(module.tracked_files(repo), read)
    assert problems and "cannot be inspected" in problems[0]


def test_unreadable_tracked_file_is_a_problem():
    module = load_packaging_module("check_no_pack")

    def read(_name):
        raise PermissionError("denied")

    assert module.tracked_problems(["a/b.txt"], read)
    assert module.tracked_problems(["gone.txt"], lambda _n: (_ for _ in ()).throw(FileNotFoundError())) == []


# --- accidental escapes: formatting, encoding, damage, key case -----------------------------------

def _encoded(text, encoding):
    return text.encode(encoding)


RENAMED_VARIANTS = {
    "eight-spaces": b" " * 8 + MANIFEST,
    "newlines": b"\n\n\r\n\t  \n" + MANIFEST,
    "utf8-bom-newline": b"\xef\xbb\xbf\n" + MANIFEST,
    "utf16-le-bom": b"\xff\xfe" + MANIFEST.decode().encode("utf-16-le"),
    "utf16-be-bom": b"\xfe\xff" + MANIFEST.decode().encode("utf-16-be"),
    "utf16-le-nobom": MANIFEST.decode().encode("utf-16-le"),
    "utf16-be-nobom": MANIFEST.decode().encode("utf-16-be"),
    "utf16-be-nobom-indented": ("\n    " + MANIFEST.decode()).encode("utf-16-be"),
    "utf32-le-bom": MANIFEST.decode().encode("utf-32"),
    "utf32-be-nobom": MANIFEST.decode().encode("utf-32-be"),
}


@pytest.mark.parametrize("variant", sorted(RENAMED_VARIANTS))
def test_renamed_manifest_survives_formatting_and_encoding(repo, variant):
    track(repo, {"kb/info.dat": RENAMED_VARIANTS[variant]})
    assert_blocked(run_guard(repo), "kb/info.dat", "knowledge pack manifest")


def test_data_file_in_utf16_is_caught(repo):
    text = (MINI_PACK / "operations.json").read_text(encoding="utf-8-sig")
    track(repo, {"kb/a.dat": b"\xff\xfe" + text.encode("utf-16-le")})
    assert_blocked(run_guard(repo), "kb/a.dat", "data file")


def test_truncated_manifest_is_caught(repo):
    track(repo, {"kb/info.dat": MANIFEST[: MANIFEST.rindex(b"}")]})
    assert_blocked(run_guard(repo), "kb/info.dat", "knowledge pack manifest")


def test_truncated_manifest_without_the_notice_text_is_caught_by_keys(repo):
    damaged = MANIFEST_NO_NOTICE_TEXT[:-1]
    track(repo, {"kb/info.dat": b"\n   " + damaged})
    assert_blocked(run_guard(repo), "kb/info.dat", "knowledge pack manifest")


def test_truncated_data_file_is_caught(repo):
    data = (MINI_PACK / "operations.json").read_bytes().rstrip()
    track(repo, {"kb/a.dat": data[:-2]})
    assert_blocked(run_guard(repo), "kb/a.dat", "data file")


@pytest.mark.parametrize("spelling", ["Notice", "NOTICE", "NoTice"])
def test_manifest_key_case_does_not_matter(repo, spelling):
    track(repo, {"kb/info.dat": MANIFEST.replace(b'"notice"', f'"{spelling}"'.encode())})
    assert_blocked(run_guard(repo), "kb/info.dat", "knowledge pack manifest")


def test_manifest_with_other_key_spellings_is_caught(repo):
    renamed = (MANIFEST.replace(b'"schema_version"', b'"Schema-Version"')
               .replace(b'"generator_version"', b'"GENERATOR VERSION"'))
    track(repo, {"kb/info.dat": renamed.replace(NOTICE.encode(), b"x")})
    assert_blocked(run_guard(repo), "kb/info.dat", "knowledge pack manifest")


def test_data_file_key_case_does_not_matter(repo):
    text = (MINI_PACK / "operations.json").read_text(encoding="utf-8-sig")
    text = text.replace('"operations"', '"Operations"').replace('"soap_action"', '"SOAP_Action"')
    track(repo, {"kb/a.dat": text})
    assert_blocked(run_guard(repo), "kb/a.dat", "data file")


def test_legitimate_json_like_files_pass(repo):
    shutil.copytree(MINI_PACK, repo / FIXTURE_REL)
    track(repo, {
        "package.json": '{"name": "x", "version": "1.0.0", "files": ["dist"], "scripts": {}}',
        "pkg/broken.json": '{"name": "x", "files": ["a"',
        "pkg/list.json": '[{"a": 1}, {"b": 2}]',
        "pkg/spaced.json": b"\n" + b" " * 40 + b'{"version": 1}',
        "pkg/utf16.json": b"\xff\xfe" + '{"hello": "world"}'.encode("utf-16-le"),
        "pkg/utf16-text.txt": b"\xff\xfe" + "just some text".encode("utf-16-le"),
        "pkg/notes.txt": "{ a plain note, not json",
        "pkg/blob.bin": bytes(range(256)) * 8,
    })
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    result = run_guard(repo)
    assert result.returncode == 0, result.stderr


# --- every data section on its own, long blank prefixes, constant hygiene ---------------------------

SECTIONS = {
    "adapters": "_ADAPTER_FIELDS",
    "error_codes": "_ERROR_FIELDS",
    "operations": "_OPERATION_FIELDS",
    "parameters": "_PARAMETER_FIELDS",
}


def section_text(section):
    """Synthetic data file of one section: two rows carrying every field the schema defines."""
    from dmslog.knowledge import pack_schema

    fields = getattr(pack_schema, SECTIONS[section])
    rows = [{field: f"synthetic {n}" for field in fields} for n in range(2)]
    return json.dumps({section: rows}, indent=2)


@pytest.mark.parametrize("section", sorted(SECTIONS))
@pytest.mark.parametrize("damage", ["intact", "truncated"])
def test_each_data_file_is_blocked_on_its_own_when_renamed(repo, section, damage):
    text = section_text(section)
    track(repo, {"kb/a.dat": text if damage == "intact" else text[: len(text) // 2 + 7]})
    assert_blocked(run_guard(repo), "kb/a.dat", "knowledge pack data file")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
@pytest.mark.parametrize("count", [256, 1024, 10_000])
def test_renamed_manifest_behind_a_long_blank_prefix_is_caught(repo, encoding, count):
    track(repo, {"kb/info.dat": (" " * count + MANIFEST.decode()).encode(encoding)})
    assert_blocked(run_guard(repo), "kb/info.dat", "knowledge pack manifest")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_data_file_behind_a_long_blank_prefix_is_caught(repo, encoding):
    text = "\n".join(["  \t"] * 3000) + section_text("operations")
    track(repo, {"kb/a.dat": text.encode(encoding)})
    assert_blocked(run_guard(repo), "kb/a.dat", "knowledge pack data file")


def test_blank_and_binary_files_do_not_trip_the_guard(repo):
    shutil.copytree(MINI_PACK, repo / FIXTURE_REL)
    track(repo, {
        "pkg/blank.txt": b" " * 100_000,
        "pkg/blank16.txt": (" " * 100_000).encode("utf-16"),
        "pkg/big.bin": bytes(range(256)) * 8192,
        "pkg/legit.json": b" " * 5000 + b'{"name": "x", "version": 1}',
    })
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    result = run_guard(repo)
    assert result.returncode == 0, result.stderr


def test_guard_constants_are_stored_in_normalised_form():
    guard = load_packaging_module("check_no_pack")
    assert guard.unnormalized_constants() == []
    assert set(guard._signatures()) == {guard._norm(name) for name in SECTIONS}
    for fields in guard._signatures().values():
        assert fields
        assert all(guard._norm(f) == f for f in fields)


def test_unnormalised_constants_are_detected(monkeypatch):
    guard = load_packaging_module("check_no_pack")
    monkeypatch.setattr(guard, "KEY_PROCESS_MAP", "process_map")
    monkeypatch.setattr(guard, "_SIGNATURES", {"error_codes": {"adms_severity"}})
    assert guard.unnormalized_constants() == ["KEY_PROCESS_MAP", "section:error_codes",
                                              "error_codes.adms_severity"]

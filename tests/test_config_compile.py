"""The compiler of `home.py`: the compiled JSON, the read audit, the discovery of inputs, and the error lines."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from pynix_native import module_attr, set_module_attr

from minix_config import compile as compiler
from minix_config.entry import written_entry

FIXTURES = Path(__file__).parent / "fixtures"
IMPORT = "from minix_config import *\n"


@pytest.fixture(autouse=True)
def clean_imports() -> Iterator[None]:
    """`compile.main` puts `.basix/pynix` on `sys.path` and imports input packages. Forget both after each test."""
    path = list(sys.path)
    yield
    sys.path[:] = path
    set_module_attr("nixosModules")  # `compile.main` sets the Home Manager word: put the default back
    core = compiler.CORE_PACKAGES
    for name in [n for n in sys.modules if n.startswith("pynix_") and n.split(".")[0] not in core]:
        del sys.modules[name]


def run(tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, dict | None]:
    """Compile `text` as `home.py` in `tmp_path`. Return the exit code, the standard error text, and the JSON."""
    (tmp_path / "home.py").write_text(text)
    code = compiler.main(["minix-compile", str(tmp_path / "home.py"), str(tmp_path / "home.json")])
    err = capsys.readouterr().err
    out = tmp_path / "home.json"
    return code, err, json.loads(out.read_text()) if out.exists() else None


def fail(tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]) -> str:
    code, err, _ = run(tmp_path, text, capsys)
    assert code == 1
    return err.rstrip("\n")


@pytest.mark.parametrize("case", ["T2", "T6prog", "SLEEP"])
def test_reference_fragments(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The reference cases of ACC1 give the fragments of the proven spike, byte for byte (SG35)."""
    code, err, data = run(tmp_path, (FIXTURES / case / "home.py").read_text(), capsys)
    reference = json.loads((FIXTURES / case / "reference.json").read_text())
    assert (code, err) == (0, "")
    assert data is not None
    assert data["user"] == reference["user"]
    assert data["fragments"] == reference["fragments"]


def test_home_can_import_a_workspace_helper(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "_minix_test_helper.py").write_text('OWNER = "ada"\n')
    try:
        code, err, data = run(
            tmp_path, IMPORT + "from _minix_test_helper import OWNER\nhome = Home(user=OWNER)\n", capsys
        )
    finally:
        sys.modules.pop("_minix_test_helper", None)
    assert (code, err) == (0, "")
    assert data is not None
    assert data["user"] == "ada"


def test_written_entry_compiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, err, data = run(tmp_path, written_entry("ada"), capsys)
    assert (code, err) == (0, "")
    assert data is not None
    assert data["user"] == "ada"
    # state_version is one fragment of `home.stateVersion`.
    assert [f["value"] for f in data["fragments"]] == [{"stateVersion": "26.11"}]


def test_guard_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The JSON holds the hash of the entry and the audit of the reads (009-D45)."""
    code, _, data = run(tmp_path, IMPORT + 'home = Home(user="a")\n', capsys)
    assert code == 0
    assert data is not None
    assert len(data["source_sha256"]) == 64
    assert data["reads"] == {"absent": [], "dirs": {}, "files": {}, "ignore": ["__pycache__", "home.json"]}


def test_reads_are_recorded(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "note.txt").write_text("hello\n")
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "one.conf").write_text("1")
    text = (
        IMPORT
        + 'import os\nnote = open("note.txt").read()\nparts = sorted(os.listdir("parts"))\n'
        + 'absent = os.path.exists("later.txt")\n'
        + 'home = Home(user="a", files=[File(".config/note", note)])\n'
    )
    # The audit uses paths relative to the folder of the entry: run with the folder as the working directory.
    import os

    here = Path.cwd()
    os.chdir(tmp_path)
    try:
        code, err, data = run(tmp_path, text, capsys)
    finally:
        os.chdir(here)
    assert (code, err) == (0, "")
    assert data is not None
    reads = data["reads"]
    assert list(reads["files"]) == ["note.txt"]
    assert reads["dirs"] == {"parts": ["one.conf"]}


def test_read_outside_the_workspace_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = IMPORT + 'secret = open("/etc/hostname").read()\nhome = Home(user="a")\n'
    err = fail(tmp_path, text, capsys)
    assert err == (
        "home.py:2:10: reads /etc/hostname, which is outside the workspace. "
        "Copy the file into the folder of home.py and read the copy."
    )
    assert not (tmp_path / "home.json").exists()


@pytest.mark.parametrize(
    ("text", "line"),
    [
        (
            'home = Home(user="a", programs=[Git(name="Ada", email="ada")])\n',
            'home.py:2:55: Git.email must be an email address, for example you@example.org, found "ada".',
        ),
        (
            'home = Home(user="a", files=[File("/etc/x", "t")])\n',
            "home.py:2:35: File.path must be relative to your home folder, not absolute: write '.config/NAME' "
            "instead of '/home/YOU/.config/NAME', found \"/etc/x\".",
        ),
        (
            'home = Home(user="a", services=[Service("w", program=pkgs.python3), Service("w", program=pkgs.python3)])\n',
            'home.py:2:32: Home.services holds the service "w" twice: Home Manager would merge the two, so keep only one.',
        ),
        (
            'home = Home(user="a", services=[Service("w", program=pkgs.coreutils)])\n',
            "home.py:2:54: Service.program must be a package with a main program, and `coreutils` has none. "
            'Name the file: file="NAME".',
        ),
        (
            'home = Home(user="a", programs=[Program(pkgs.ripgrpe)])\n',
            "home.py:2:46: The package list has no package `ripgrpe`. Did you mean `ripgrep`?",
        ),
        (
            'home = Home(user="Ada")\n',
            "home.py:2:18: Home.user must be an account name (a lower case letter, then up to 31 lower case letters, "
            "digits, or '-'), found \"Ada\".",
        ),
        (
            'home = Home(user="a", files=[File(path="a", text="b")])\n',
            "home.py:2:30: File takes PATH and TEXT by position, without the setting name: File(PATH, TEXT). "
            "Write the settings as keywords: executable.",
        ),
        (
            'home = Home(user="a", services=[Service("w", program=pkgs.python3, environment={"1x": "y"})])\n',
            'home.py:2:80: Service.environment must name each variable with letters, digits, and "_", '
            'and not start with a digit, found "1x".',
        ),
        (
            'home = Home(user="a", state_version="26.11", options=[hm.Home(stateVersion="26.05")])\n',
            "home.py:2:8: Home sets the state version twice: keep state_version, and remove stateVersion from options.",
        ),
        (
            'home = Home(user="a", state_version="27.05")\n',
            'home.py:2:37: Home.state_version must be a release of Home Manager: 18.09 to 26.11, for example "26.05", found "27.05".',
        ),
        (
            'home = Home(user="a", programs=[prog])\n',
            "home.py:2:33: `prog` is not defined in home.py. The names you defined are: home.",
        ),
        (
            'home = Home(user="a", options=[hm.Nonesuch(enable=True)])\n',
            "home.py:2:35: The flat list of pynix_hm has no class Nonesuch.",
        ),
    ],
)
def test_error_lines(text: str, line: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert fail(tmp_path, IMPORT + text, capsys) == line


def test_unknown_name_suggests_close_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    err = fail(tmp_path, IMPORT + 'home = Home(user="a", programs=[Gitt(name="a", email="a@example.org")])\n', capsys)
    assert err.startswith("home.py:2:33: `Gitt` is not a minix name. Did you mean `Git`? The names are: ")
    # The learner names only: no reference class and no helper (009-D23).
    assert "Text" not in err and "ConfigRef" not in err and "OWN" not in err


def test_missing_import_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    err = fail(tmp_path, 'home = Home(user="a")\n', capsys)
    assert (
        err == "home.py:1:8: `Home` is not imported. Add the line `from minix_config import *` at the top of home.py."
    )


def test_unknown_setting_gets_did_you_mean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    err = fail(tmp_path, IMPORT + 'home = Home(user="a", programs=[Git(nam="a", email="a@example.org")])\n', capsys)
    assert "home.py:2:37: Git.nam is not a setting. Did you mean `name`? The settings are: name, email." in err


def test_nested_class_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """009-D41: a part class in `options` stops with the parent and the way to write it."""
    err = fail(tmp_path, IMPORT + 'home = Home(user="a", options=[hm.Neovim_Coc(enable=True)])\n', capsys)
    assert err == "home.py:2:32: Home.options[0] Neovim_Coc is a part of Neovim; write Neovim(coc=Neovim_Coc(...))."


def test_option_error_names_the_home_manager_option(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    err = fail(tmp_path, IMPORT + 'home = Home(user="a", options=[hm.Neovim(enable="yes")])\n', capsys)
    assert err.endswith("(Home Manager option programs.neovim.enable)")
    assert err.startswith('home.py:2:49: Neovim.enable must be true or false, found "yes".')


def test_syntax_error_is_one_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert fail(tmp_path, IMPORT + 'home = Home(user="a"\n', capsys) == "home.py:2:12: '(' was never closed"


def test_missing_home(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert fail(tmp_path, IMPORT + "x = 1\n", capsys) == "home.py: the file must set `home = Home(...)`"


def test_shell_character_warns_and_still_compiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = IMPORT + 'home = Home(user="a", services=[Service("w", program=pkgs.python3, args="-m a | b")])\n'
    code, err, _ = run(tmp_path, text, capsys)
    assert code == 0
    assert err.startswith("home.py:2:73: warning: Service.args holds `|`. No shell runs, so it is a plain argument.")


def test_warnings_do_not_carry_over_between_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(
        tmp_path,
        IMPORT + 'home = Home(user="a", services=[Service("w", program=pkgs.python3, args="a | b")])\n',
        capsys,
    )
    code, err, _ = run(tmp_path, IMPORT + 'home = Home(user="a")\n', capsys)
    assert (code, err) == (0, "")


def test_input_with_a_computed_url_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = IMPORT + 'u = "github:a/b"\nhome = Home(user="a", inputs=[Input("x", u)])\n'
    assert fail(tmp_path, text, capsys) == "home.py:3:31: Input(...) takes plain text and True or False only"


def test_input_needs_a_lock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = IMPORT + 'home = Home(user="a", inputs=[Input("x", "github:a/b")])\n'
    assert fail(tmp_path, text, capsys) == "home.py:2: flake.lock does not exist yet; run `minix apply` to lock it"


def _lock(tmp_path: Path, name: str = "x", nar: str = "sha256-AAAA", rev: str = "r1") -> None:
    nodes = {
        "root": {"inputs": {"nixpkgs": "nixpkgs", name: name}},
        "nixpkgs": {"locked": {"narHash": "sha256-PKGS", "rev": "e" * 40}},
        name: {"locked": {"narHash": nar, "rev": rev}, "inputs": {"nixpkgs": "nixpkgs"}},
    }
    (tmp_path / "flake.lock").write_text(json.dumps({"nodes": nodes, "root": "root", "version": 7}))


def _package(tmp_path: Path, name: str, nar: str, rev: str = "r1", nixpkgs: str = "e" * 40) -> None:
    folder = tmp_path / ".basix" / "pynix" / f"pynix_{name}"
    folder.mkdir(parents=True)
    source = {"input": name, "narHash": nar, "rev": rev, "nixpkgs": nixpkgs, "generator": "g"}
    (folder / "_generation.py").write_text(f"SOURCE = {source!r}\n")
    (folder / "__init__.py").write_text("")


def test_input_package_not_made_yet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _lock(tmp_path)
    text = IMPORT + 'home = Home(user="a", inputs=[Input("x", "github:a/b")])\n'
    assert (
        fail(tmp_path, text, capsys) == "home.py:2: the package `pynix_x` is not made yet; run `minix apply` to make it"
    )


def test_input_package_from_another_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """009-D48: the compile stops when the package does not pair with `flake.lock`."""
    _lock(tmp_path, nar="sha256-NEW")
    _package(tmp_path, "x", nar="sha256-OLD")
    text = IMPORT + 'home = Home(user="a", inputs=[Input("x", "github:a/b")])\n'
    assert fail(tmp_path, text, capsys) == (
        "home.py:2: the package `pynix_x` was made from another version of `x` than flake.lock holds; "
        "run `minix apply` to make it again"
    )


def test_input_package_pairs_and_compiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _lock(tmp_path)
    _package(tmp_path, "x", nar="sha256-AAAA")
    text = IMPORT + 'home = Home(user="a", inputs=[Input("x", "github:a/b", modules=["m"])])\n'
    code, err, data = run(tmp_path, text, capsys)
    assert (code, err) == (0, "")
    assert data is not None
    assert data["inputs"] == [
        {"name": "x", "url": "github:a/b", "flake": True, "modules": ["m"], "source": "home.py:2"}
    ]


def _nixpkgs_lock(tmp_path: Path, name: str, commit: str, nar: str = "sha256-OLDPKGS") -> None:
    lock = json.loads((tmp_path / "flake.lock").read_text()) if (tmp_path / "flake.lock").exists() else None
    if lock is None:
        _lock(tmp_path)
        lock = json.loads((tmp_path / "flake.lock").read_text())
    lock["nodes"][f"nixpkgs-{name}"] = {"locked": {"rev": commit, "narHash": nar, "lastModified": 1}}
    lock["nodes"]["root"]["inputs"][f"nixpkgs-{name}"] = f"nixpkgs-{name}"
    (tmp_path / "flake.lock").write_text(json.dumps(lock))


def _name_list(tmp_path: Path, name: str, commit: str, nar: str = "sha256-OLDPKGS") -> None:
    folder = tmp_path / ".basix" / "pynix" / f"pynix_nixpkgs_{name}"
    folder.mkdir(parents=True)
    source = {"commit": commit, "narHash": nar, "generator": "nixpkgs-names-1"}
    (folder / "_generation.py").write_text(f"SOURCE = {source!r}\n")
    (folder / "__init__.py").write_text("")
    (folder / "names.json").write_text(json.dumps({"hello": ["hello", True], "ripgrep": ["ripgrep", True]}))


OLD = "d" * 40


def test_nixpkgs_goes_into_the_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """009-D53, 009-D54: `old.pkgs.NAME` is a package of a pinned nixpkgs, and the JSON lists the pin."""
    _nixpkgs_lock(tmp_path, "old", OLD)
    _name_list(tmp_path, "old", OLD)
    text = IMPORT + f'old = NixpkgsPin("old", "{OLD}")\nhome = Home(user="a", programs=[Program(old.pkgs.hello)])\n'
    code, err, data = run(tmp_path, text, capsys)
    assert (code, err) == (0, "")
    assert data is not None
    assert data["nixpkgs"] == [{"name": "old", "commit": OLD}]
    package = data["fragments"][0]["value"]["packages"][0]["__nixdantic_ref__"]
    assert package == {"kind": "nixpkgs_package", "nixpkgs": "old", "attr": "hello"}


def test_nixpkgs_name_list_suggests_a_close_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _nixpkgs_lock(tmp_path, "old", OLD)
    _name_list(tmp_path, "old", OLD)
    text = IMPORT + f'old = NixpkgsPin("old", "{OLD}")\nhome = Home(user="a", programs=[Program(old.pkgs.helo)])\n'
    err = fail(tmp_path, text, capsys)
    assert err.endswith("The package list `old` has no package `helo`. Did you mean `hello`?")


def test_nixpkgs_without_a_lock_entry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _lock(tmp_path)
    text = IMPORT + f'old = NixpkgsPin("old", "{OLD}")\nhome = Home(user="a")\n'
    assert (
        fail(tmp_path, text, capsys) == "home.py:2: flake.lock holds no nixpkgs `old` yet; run `minix apply` to lock it"
    )


def test_nixpkgs_name_list_from_another_commit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _nixpkgs_lock(tmp_path, "old", OLD)
    _name_list(tmp_path, "old", "e" * 40)
    text = IMPORT + f'old = NixpkgsPin("old", "{OLD}")\nhome = Home(user="a")\n'
    assert fail(tmp_path, text, capsys) == (
        "home.py:2: the package list `old` was made from another commit than flake.lock holds "
        f"({'e' * 12} against {OLD[:12]}); run `minix apply` to make it again"
    )


# --- the Home Manager word and the advanced import ----------------------------------------------------------------
def test_an_input_text_names_the_home_manager_word(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The compiler sets the module output once, so the error of `Input` says `homeManagerModules`, not `nixosModules`."""
    text = IMPORT + 'home = Home(user="a", inputs=[Input("x", "github:a/b", modules=["bad name"])])\n'
    assert fail(tmp_path, text, capsys) == (
        "home.py:2:64: Input.modules must be the name of a module in `homeManagerModules` of the input, "
        'for example "default", found "bad name".'
    )
    assert module_attr() == "homeManagerModules"


ADVANCED = """from minix_config import *
from nixdantic import After, Before, Force, Order
from pynix_hm.programs.bash import Bash

home = Home(
    user="a",
    options=[
        Bash(enable=True, historySize=Force(20), initExtra="# normal\\n"),
        Bash(historySize=10, initExtra=Before("# before\\n")),
        Bash(initExtra=After("# after\\n")),
        Bash(initExtra=Order(2000, "# late\\n")),
    ],
)
"""


def test_the_advanced_import_compiles_in_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`from nixdantic import Force, Before, After, Order` works inside `options=[...]` (L128)."""
    code, err, data = run(tmp_path, ADVANCED, capsys)
    assert (code, err) == (0, "")
    assert data is not None
    sizes = [f["value"].get("historySize") for f in data["fragments"]]
    assert sizes[0] == {"__nixdantic_ref__": {"kind": "priority", "level": 50, "value": 20}}
    assert sizes[1] == 10  # a plain value beside Force(20): Nix picks 20 (see check_nix.py)
    orders = [f["value"]["initExtra"] for f in data["fragments"] if "initExtra" in f["value"]]
    levels = [o["__nixdantic_ref__"]["level"] if isinstance(o, dict) else o for o in orders]
    assert levels == ["# normal\n", 500, 1500, 2000]


def test_the_learner_names_do_not_export_the_wrappers() -> None:
    import minix_config

    for name in ("Force", "Default", "Before", "After", "Order", "Override"):
        assert name not in minix_config.__all__
        assert not hasattr(minix_config, name)


def test_a_wrapper_in_a_wrapper_is_one_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = (
        IMPORT
        + "from nixdantic import Force\nfrom pynix_hm.programs.bash import Bash\n"
        + 'home = Home(user="a", options=[Bash(historySize=Force(Force(3)))])\n'
    )
    assert (
        fail(tmp_path, text, capsys)
        == "home.py:4:49: Force(...) cannot hold Force(...): use one wrapper for one value."
    )


def test_a_native_service_with_no_file_compiles_to_exe_of(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = IMPORT + 'home = Home(user="a", services=[Service("w", program=Native("pkg.nix", ["d"]), args="-q")])\n'
    code, err, data = run(tmp_path, text, capsys)
    assert (code, err) == (0, "")
    assert data is not None
    start = data["fragments"][0]["value"]["services"]["w"]["Service"]["ExecStart"]["__nixdantic_ref__"]
    native = {"kind": "native", "file": "pkg.nix", "attr": ["d"]}
    assert start["parts"] == [{"__nixdantic_ref__": {"kind": "exe_of", "of": native}}, " -q"]


@pytest.mark.parametrize(
    ("text", "line"),
    [
        (
            'home = Home(user="a", services=[Service("w", program="curl", file="x")])\n',
            'home.py:2:54: Service.program must be a package such as pkgs.NAME, or a Native package such as Native("pkg.nix", ["attr"]), found "curl".',
        ),
        (
            'home = Home(user="a", services=[Service("w", program=pkgs.python3, requires=["a b"])])\n',
            'home.py:2:78: Service.requires[0] must be a unit name such as "db" or "network.target", found "a b", found "a b".',
        ),
        (
            'home = Home(user="a", services=[Service("w", program=pkgs.python3, description="a\\nb")])\n',
            'home.py:2:80: Service.description must be on one line, and this value holds a line break, found "a\\nb".',
        ),
        (
            'home = Home(user="a", services=[Service("w", program=pkgs.python3, environment_file="env")])\n',
            'home.py:2:85: Service.environment_file must be an absolute path or start with ~/, found "env", found "env".',
        ),
    ],
)
def test_error_lines_of_native_and_the_unit_fields(
    text: str, line: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert fail(tmp_path, IMPORT + text, capsys) == line


def test_a_service_runs_a_native_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = 'home = Home(user="a", services=[Service("d", program=Native("pkg.nix", ["daemon"]), file="d")])\n'
    code, err, data = run(tmp_path, IMPORT + text, capsys)
    assert (code, err) == (0, "")
    assert data is not None
    exec_start = data["fragments"][0]["value"]["services"]["d"]["Service"]["ExecStart"]["__nixdantic_ref__"]
    assert exec_start["parts"][1] == "/bin/d"
    assert exec_start["parts"][0]["__nixdantic_ref__"] == {"kind": "native", "file": "pkg.nix", "attr": ["daemon"]}

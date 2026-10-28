"""Test fixtures: "remote" bare repos, a group dir with a toplevel clone, and
an in-process runner for the CLI.

Remotes (built once per session):
  proj.git   the toplevel's repo, branch main (README)
  liba.git   a setuptools package `liba`; branches main and kms (kms one commit ahead)
  libb.git   the same for `libb`, with a submodule extern/dep (from dep.git)
  dep.git    a plain repo used as libb's submodule

Every test runs with git isolated from the user's config, HOME in a temp dir,
pip offline, and a PATH of: a fake `direnv` that logs its arguments, links to
the test interpreter's python/pip, then /usr/bin:/bin.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from xwt import cli

ISOLATION = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Committer",
    "GIT_COMMITTER_EMAIL": "committer@example.com",
    # File-URL submodules are refused by default since git 2.38.
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "protocol.file.allow",
    "GIT_CONFIG_VALUE_0": "always",
    "PIP_NO_INDEX": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_NO_INPUT": "1",
    "COLUMNS": "10000",
}

PYPROJECT = """\
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.1"

[tool.setuptools]
packages = ["{name}"]
"""


def run_git(cwd, *args, env=None) -> str:
    res = subprocess.run(["git", "-C", str(cwd), *map(str, args)], capture_output=True,
                         text=True, env=env)
    if res.returncode != 0:
        raise AssertionError(f"git {' '.join(map(str, args))} in {cwd} failed:\n"
                             f"{res.stdout}{res.stderr}")
    return res.stdout.strip()


def _session_env() -> dict:
    env = dict(os.environ)
    env.update(ISOLATION)
    return env


def _commit_file(repo: Path, rel: str, text: str, msg: str, env) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    run_git(repo, "add", rel, env=env)
    run_git(repo, "commit", "-q", "-m", msg, env=env)


@pytest.fixture(scope="session")
def remotes(tmp_path_factory) -> Path:
    env = _session_env()
    root = tmp_path_factory.mktemp("remotes")
    work = root / "work"
    work.mkdir()

    def make(name, populate):
        src = work / name
        run_git(work, "init", "-q", "-b", "main", name, env=env)
        populate(src)
        run_git(root, "clone", "-q", "--bare", src, root / f"{name}.git", env=env)

    make("dep", lambda r: _commit_file(r, "dep.txt", "dep\n", "dep: initial", env))
    make("proj", lambda r: _commit_file(r, "README", "the toplevel\n", "proj: initial", env))

    def package(name, submodule=False):
        def populate(r):
            _commit_file(r, "pyproject.toml", PYPROJECT.format(name=name), f"{name}: pyproject", env)
            _commit_file(r, f"{name}/__init__.py", f"NAME = {name!r}\n", f"{name}: package", env)
            if submodule:
                run_git(r, "submodule", "add", "-q", root / "dep.git", "extern/dep", env=env)
                run_git(r, "commit", "-q", "-m", f"{name}: submodule", env=env)
            run_git(r, "checkout", "-q", "-b", "kms", env=env)
            _commit_file(r, "KMS", "kms work\n", f"{name}: kms work", env)
            run_git(r, "checkout", "-q", "main", env=env)
        return populate

    make("liba", package("liba"))
    make("libb", package("libb", submodule=True))
    shutil.rmtree(work)
    return root


@pytest.fixture(scope="session")
def extra_wheel(tmp_path_factory) -> Path:
    """A wheel of a tiny package `xwtextra`, for [envrc.venv] install entries."""
    root = tmp_path_factory.mktemp("wheel")
    src = root / "src"
    (src / "xwtextra").mkdir(parents=True)
    (src / "pyproject.toml").write_text(PYPROJECT.format(name="xwtextra"))
    (src / "xwtextra" / "__init__.py").write_text("")
    out = root / "dist"
    env = _session_env()
    subprocess.run([sys.executable, "-m", "pip", "wheel", "-q", "--no-build-isolation",
                    "--no-deps", "-w", str(out), str(src)], check=True, env=env,
                   capture_output=True)
    (wheel,) = out.glob("xwtextra-*.whl")
    return wheel


@dataclass
class Result:
    rc: int
    out: str
    err: str

    @property
    def all(self) -> str:
        return self.out + self.err


class World:
    """One test's group dir, toplevel and helpers."""

    def __init__(self, tmp: Path, remotes: Path, monkeypatch, capfd):
        self.tmp = tmp
        self.remotes = remotes
        self.mp = monkeypatch
        self.capfd = capfd
        self.home = tmp / "home"
        self.home.mkdir()
        self.group = tmp / "group"
        self.group.mkdir()
        self.top = self.group / "top"
        self.direnv_log = tmp / "direnv.log"

        self.fakebin = tmp / "fakebin"
        self.fakebin.mkdir()
        direnv = self.fakebin / "direnv"
        direnv.write_text(f'#!/bin/sh\necho "$@" >> {self.direnv_log}\n')
        direnv.chmod(0o755)
        self.toolbin = tmp / "toolbin"
        self.toolbin.mkdir()
        exe = Path(sys.executable)
        for name in ("python", "python3", "pip"):
            target = exe if name.startswith("python") else exe.parent / name
            (self.toolbin / name).symlink_to(target)

        for k, v in ISOLATION.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("XDG_DATA_HOME", str(self.home / ".local/share"))
        for k in ("VIRTUAL_ENV", "EDITOR", "VISUAL", "PYTHONSAFEPATH", "GIT_DIR",
                  "GIT_WORK_TREE", "DIRENV_DIR", "DIRENV_FILE"):
            monkeypatch.delenv(k, raising=False)
        self.set_path()

    # -- environment ----------------------------------------------------------

    def set_path(self, *, direnv: bool = True, conda: bool = False, extra=()):
        parts = [str(p) for p in extra]
        if direnv:
            parts.append(str(self.fakebin))
        parts.append(str(self.toolbin))
        if conda:
            parts.append(str(Path(conda_exe()).parent))
        parts += ["/usr/bin", "/bin"]
        self.mp.setenv("PATH", os.pathsep.join(parts))

    def direnv_calls(self) -> list[str]:
        if not self.direnv_log.exists():
            return []
        return self.direnv_log.read_text().splitlines()

    # -- running --------------------------------------------------------------

    def xwt(self, *args, cwd=None) -> Result:
        self.capfd.readouterr()
        self.mp.chdir(cwd or self.top)
        try:
            rc = cli.main([str(a) for a in args])
        except SystemExit as e:  # argparse
            rc = e.code
        o = self.capfd.readouterr()
        return Result(rc, o.out, o.err)

    def git(self, cwd, *args) -> str:
        return run_git(cwd, *args)

    def branch(self, cwd) -> str | None:
        res = subprocess.run(["git", "-C", str(cwd), "symbolic-ref", "-q", "--short", "HEAD"],
                             capture_output=True, text=True)
        return res.stdout.strip() if res.returncode == 0 else None

    def rev(self, cwd, ref="HEAD") -> str:
        return self.git(cwd, "rev-parse", ref)

    def commit(self, repo, rel=None, text=None, msg=None) -> str:
        """Commit `text` to `rel` (by default a new file, so commits don't conflict)."""
        rel = rel or f"file-{os.urandom(4).hex()}.txt"
        p = Path(repo) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text if text is not None else f"{rel} {os.urandom(4).hex()}\n")
        self.git(repo, "add", rel)
        self.git(repo, "commit", "-q", "-m", msg or f"change {rel}")
        return self.rev(repo)

    # -- layouts --------------------------------------------------------------

    def url(self, name: str) -> str:
        return str(self.remotes / f"{name}.git")

    def toml(self, subs=("liba", "libb"), *, branch="main", extra="") -> str:
        lines = [f'branch = "{branch}"'] if branch else []
        for s in subs:
            lines += ["", f"[repo.{s}]", f'url = "{self.url(s)}"', 'branch = "kms"']
        return "\n".join(lines) + "\n" + textwrap.dedent(extra)

    def clone_top(self) -> Path:
        run_git(self.group, "clone", "-q", self.url("proj"), self.top)
        return self.top

    def write_toml(self, text: str) -> Path:
        p = self.top / "xwt.toml"
        p.write_text(text)
        return p

    def setup(self, subs=("liba", "libb"), *, toml=None, ignore=True, commit_toml=False,
              extra="") -> Path:
        """A toplevel made with plain git: the clone, its sub-repos on kms with
        submodules, xwt.toml, and (with `ignore`) a committed .gitignore."""
        self.clone_top()
        for s in subs:
            run_git(self.top, "clone", "-q", "-b", "kms", self.url(s), self.top / s)
            run_git(self.top / s, "submodule", "update", "--init", "--recursive", "-q")
        self.write_toml(toml if toml is not None else self.toml(subs, extra=extra))
        if ignore:
            self.commit(self.top, ".gitignore", "".join(f"/{s}/\n" for s in subs),
                        "ignore sub-repos")
        if commit_toml:
            self.git(self.top, "add", "xwt.toml")
            self.git(self.top, "commit", "-q", "-m", "add xwt.toml")
        return self.top

    def add_worktree(self, name: str, subs=("liba", "libb")) -> Path:
        """A worktree made with plain git, as `xwt new` would."""
        wt = self.group / name
        self.git(self.top, "worktree", "add", "-q", wt, "-b", name, "main")
        for s in subs:
            self.git(self.top / s, "worktree", "add", "-q", wt / s, "-b", name, "kms")
            self.git(wt / s, "submodule", "update", "--init", "--recursive", "-q")
        return wt


def conda_exe() -> str | None:
    return shutil.which("conda", path=os.environ.get("XWT_TEST_ORIG_PATH"))


def conda_available() -> bool:
    return bool(conda_exe()) and bool(os.environ.get("CONDA_DEFAULT_ENV"))


os.environ.setdefault("XWT_TEST_ORIG_PATH", os.environ.get("PATH", ""))


@pytest.fixture
def w(tmp_path, remotes, monkeypatch, capfd) -> World:
    return World(tmp_path, remotes, monkeypatch, capfd)

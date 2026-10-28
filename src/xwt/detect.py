"""What `xwt init` detects when it creates xwt.toml, the text it composes from
that (plan §4.4), and the prompt before writing it (§7.3)."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config as configmod
from . import git, layout, ui
from .config import CONFIG_NAME, toml_string
from .ui import XwtError


AGENT_REMINDER = """\
if coding agents work in this project, you may want to tell them (in CLAUDE.md, AGENTS.md, ...) not to run the xwt commands that change branches, worktrees or clones:
    xwt merge-up, xwt rebase-down, xwt new, xwt delete,
    xwt init, xwt add-repo
These are fine for agents: xwt status, xwt diff, xwt venv."""


@dataclass
class SubRepo:
    name: str
    url: str
    branch: str | None


@dataclass
class Detected:
    branch: str | None = None
    subs: list[SubRepo] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    conda: str | None = None


def is_python_package(path: Path) -> bool:
    return (path / "pyproject.toml").exists() or (path / "setup.py").exists()


def detect(top: Path) -> Detected:
    det = Detected(branch=git.current_branch(top))
    for d in sorted(top.iterdir(), key=lambda p: p.name):
        if d.name.startswith(".") or d.is_symlink() or not d.is_dir():
            continue
        if not (d / ".git").is_dir():      # not a clone (a submodule's .git is a file)
            continue
        if not configmod.valid_repo_name(d.name):
            ui.warn(f"skipping the clone {d}: its name can't be a sub-repo name")
            continue
        url = git.origin_url(d)
        if url is None:
            ui.warn(f"skipping the clone {d}: it has no `origin` remote")
            continue
        det.subs.append(SubRepo(d.name, url, git.current_branch(d)))
    if is_python_package(top):
        det.packages.append(".")
    det.packages += [s.name for s in det.subs if is_python_package(top / s.name)]
    det.conda = os.environ.get("CONDA_DEFAULT_ENV") or None
    return det


def _key(name: str) -> str:
    return name if all(c.isalnum() or c in "_-" for c in name) else toml_string(name)


def compose(top: Path, det: Detected) -> str:
    q = toml_string
    lines = [
        f"# xwt config for the toplevel {top}: `xwt new NAME` creates the",
        "# worktree ../NAME, on a new branch NAME in every repo. Written by `xwt init`",
        "# from what it found; edit freely. Commented-out settings are examples.",
        "",
        "# Integration branches: this repo's here, each sub-repo's in its table. Each",
        "# repo's checkout in this directory must be on its branch, or the commands",
        "# that move commits refuse.",
    ]
    if det.branch:
        lines.append(f"branch = {q(det.branch)}")
    else:
        lines.append('# branch = "main"     # left out: this checkout was detached')
    lines.append("")

    if det.subs:
        lines += ["# Sub-repos: cloned into the toplevel and added to every worktree. This is",
                  "# also the install order: put dependencies first. `xwt add-repo URL` adds one."]
        for s in det.subs:
            lines += [f"[repo.{_key(s.name)}]", f"url = {q(s.url)}"]
            lines.append(f"branch = {q(s.branch)}" if s.branch
                         else '# branch = "main"     # left out: this clone was detached')
            lines.append("")
    else:
        lines += ["# Sub-repos: cloned into the toplevel and added to every worktree, in this",
                  "# order (put dependencies first). `xwt add-repo URL` adds one.",
                  "# [repo.mylib]",
                  '# url = "github:me/mylib"',
                  '# branch = "main"',
                  ""]

    conda_line = (f"conda = {q(det.conda)}" if det.conda else
                  '# conda = "myenv"                     # without it: the env the shell has active')
    export = ["# [envrc.export]                       # extra exports in .envrc",
              '# PYTHONSAFEPATH = "1"']
    install = ['# install = ["editables"]              # plain pip installs, when the venv is created',
               '# pip_args = ["--no-build-isolation"]  # extra arguments for the editable installs']
    if det.packages:
        lines += ["# Environment: a generated .envrc (for direnv) activates the conda env and",
                  "# this workspace's own .venv, which gets editable installs of these, in order.",
                  "[envrc]", conda_line, "", *export, "",
                  "[envrc.venv]",
                  f"editable = [{', '.join(q(p) for p in det.packages)}]",
                  *install]
    else:
        lines += ["# Environment: switched off, since no Python packages were found. Uncomment",
                  "# [envrc] for a generated .envrc (for direnv) in each workspace that activates",
                  "# the conda env, and [envrc.venv] for a per-workspace .venv with editable",
                  "# installs; then re-run `xwt init`.",
                  "# [envrc]",
                  "# " + (f"conda = {q(det.conda)}" if det.conda else 'conda = "myenv"'),
                  "#", *export, "#",
                  "# [envrc.venv]",
                  '# editable = ["."]                     # pip install -e, in order; "." is this repo',
                  *install]
    return "\n".join(lines) + "\n"


def validate(text: str, path: Path) -> str | None:
    try:
        configmod.parse(text, path)
    except configmod.ConfigError as e:
        return str(e)
    return None


def edit(text: str) -> str:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    fd, tmp = tempfile.mkstemp(prefix="xwt-", suffix=".toml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        rc = subprocess.run([*shlex.split(editor), tmp]).returncode
        if rc != 0:
            ui.warn(f"the editor exited with status {rc}")
        return Path(tmp).read_text()
    finally:
        os.unlink(tmp)


def confirm_and_write(path: Path, text: str) -> bool:
    """Ask before writing on a terminal (§7.3 step 3); without one, write."""
    if not ui.stdin_isatty():
        path.write_text(text)
        ui.info(f"wrote {path}")
        return True
    error = None
    show = True
    while True:
        if show:
            ui.out()
            ui.out(text.rstrip("\n"))
            ui.out()
            if error:
                ui.error(f"{error}\nedit it again (e), or quit (n)")
            show = False
        try:
            answer = input(f"Write {path}? [Y/n/e] ").strip().lower()
        except EOFError:
            ui.out()
            answer = "n"
        if answer in ("", "y", "yes"):
            if error:
                ui.error("the text isn't valid; edit it again (e), or quit (n)")
                continue
            path.write_text(text)
            ui.info(f"wrote {path}")
            return True
        if answer in ("n", "no"):
            ui.info(f"nothing written")
            return False
        if answer in ("e", "edit"):
            text = edit(text)
            error = validate(text, path)
            show = True
            continue
        ui.out("Please answer y, n or e.")


def create_config(cwd: Path) -> Path | None:
    """`xwt init` with no toplevel found: make `cwd` one. Returns the toplevel,
    or None if the user declined."""
    cwd = cwd.resolve()
    if not ((cwd / ".git").is_dir() and git.main_checkout(cwd) == cwd):
        raise XwtError("run `xwt init` in the root of a repo's main checkout")
    if cwd.parent == layout.home():
        raise XwtError(f"the clone {cwd} is directly in $HOME, so its worktrees (../NAME) "
                       f"would be too; move the clone into a group dir first, e.g. "
                       f"`mkdir ~/proj && mv {cwd} ~/proj/top`")
    path = cwd / CONFIG_NAME
    det = detect(cwd)
    text = compose(cwd, det)
    problem = validate(text, path)
    if problem:     # can't happen; the composed file must parse
        raise XwtError(f"internal error: the composed xwt.toml doesn't validate: {problem}")
    ui.info(f"no {CONFIG_NAME} in {cwd} yet; composing one from what is there")
    if len(det.subs) > 1:
        ui.info("check the sub-repo order: it is the install order")
    if not confirm_and_write(path, text):
        return None
    ui.info(AGENT_REMINDER)
    return cwd

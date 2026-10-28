"""One function per verb (plan §7)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import envrc, git, ui
from .config import CONFIG_NAME
from .layout import Checkout, Project
from .ui import XwtError, plural


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def refuse(what: str, problems: list[str]):
    raise XwtError(f"refusing to {what}:\n" + ui.bullet_list(problems))


def usable_checkouts(project: Project, ws: Path, *, remind: bool = True) -> list[Checkout]:
    """The checkouts of `ws` that exist and are the expected repos; a reminder
    for each that isn't (§7.1)."""
    result = []
    for co in project.checkouts(ws):
        if co.ok:
            result.append(co)
        elif remind:
            ui.warn(project.reminder(co))
    return result


def _write_bytes(data: bytes) -> None:
    sys.stdout.flush()
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(data)
        buf.flush()
    else:
        sys.stdout.write(data.decode(errors="replace"))
        sys.stdout.flush()


def run_under_headers(checkouts: list[Checkout], args: list[str]) -> int:
    """`git ARGS` in each checkout, its output captured and printed under a
    header. Returns the worst exit status."""
    color = ["-c", "color.ui=always"] if ui.stdout_isatty() else []
    worst = 0
    for co in checkouts:
        ui.header(co.label)
        res = subprocess.run(["git", "-C", str(co.path), *color, *args],
                             capture_output=True, env=git.git_env())
        body = (res.stdout + res.stderr).rstrip(b"\n")
        _write_bytes((body or b"(no output)") + b"\n\n")
        worst = max(worst, res.returncode)
    return worst


# ---------------------------------------------------------------------------
# xwt status (§7.8)
# ---------------------------------------------------------------------------

def _relation(ahead: int, behind: int) -> str:
    if ahead and behind:
        return f"{plural(ahead, 'commit')} ahead and {plural(behind, 'commit')} behind"
    if ahead:
        return f"{plural(ahead, 'commit')} ahead of"
    return f"{plural(behind, 'commit')} behind"


def relation_lines(project: Project, worktrees: list[Path]) -> list[str]:
    """Branch relations (§2.1): up-to-date lines dropped, a fully up-to-date
    worktree collapsed to one line, a blank line between worktrees."""
    top = project.toplevel.name
    lines: list[str] = []
    for ws in worktrees:
        name = ws.name
        group, compared = [], 0
        for co in project.checkouts(ws):
            if not co.ok:
                continue
            integ = project.integ(co.repo)
            if not integ or integ == name or not git.branch_exists(co.repo.main, name):
                continue
            ab = git.ahead_behind(co.repo.main, integ, name)
            if ab is None:
                continue
            compared += 1
            if ab == (0, 0):
                continue
            rel = _relation(*ab)
            if co.repo.is_top:
                group.append(f"{name}/ is {rel} {top}/ (repo {project.top_name}, {integ} branch)")
            else:
                key = co.repo.key
                group.append(f"{name}/{key} is {rel} {top}/{key} ({integ} branch)")
        if not group and compared:
            group = [f"{name}/* is up-to-date with {top}/* (all repos)"]
        if group:
            if lines:
                lines.append("")
            lines.extend(group)
    return lines


def _checkout_warnings(project: Project, co: Checkout, expected: str | None,
                       integ_note: bool) -> list[str]:
    w = []
    cur = git.current_branch(co.path)
    op = git.in_progress(co.path)
    rebasing = git.rebasing_branch(co.path) if op == "rebase" else None
    what = "its integration branch " if integ_note else ""
    if expected is None:
        if cur is None and rebasing is None:
            w.append(f"{co.label} is on a detached HEAD, and xwt.toml names no integration "
                     f"branch for it")
    elif cur != expected and not (cur is None and rebasing == expected):
        on = f"on branch '{cur}'" if cur else "on a detached HEAD"
        w.append(f"{co.label} is {on}, expected {what}'{expected}'")
    if op:
        if op == "rebase":
            hint = "finish it with `git rebase --continue` or `--abort`"
        elif op == "merge":
            hint = "finish it with `git commit` or `git merge --abort`"
        else:
            hint = f"finish it with `git {op} --continue` or `--abort`"
        w.append(f"{co.label} has a {op} in progress ({hint} in {co.path})")
    return w


def status_warnings(project: Project) -> list[str]:
    w: list[str] = []
    top = project.toplevel
    scope = project.workspaces() if project.in_toplevel else [top, project.workspace]

    for co in project.checkouts(top):
        if co.ok:
            w += _checkout_warnings(project, co, co.repo.branch, True)
        elif co.state == "missing":
            w.append(f"{co.repo.key} is in xwt.toml but not cloned in the toplevel; run "
                     f"`xwt init` in {top}")
        else:
            w.append(f"{co.path} is not a git clone, but xwt.toml lists {co.repo.key} as a "
                     f"sub-repo")

    for ws in scope[1:]:
        for co in project.checkouts(ws):
            if co.ok:
                w += _checkout_warnings(project, co, ws.name, False)
            elif co.state == "missing":
                if project.cloned(co.repo):
                    w.append(f"{ws.name} has no checkout of {co.repo.key}; run `xwt init` "
                             f"in {ws} to add it")
            else:
                w.append(f"{co.path} is not a git worktree of {co.repo.main}")

    for pattern in envrc.managed_ignores(project):
        if not git.is_ignored(top, pattern.lstrip("/")):
            w.append(f"{pattern} is not ignored by the toplevel repo; run `xwt init` in {top} "
                     f"to add it to .gitignore")

    if project.in_toplevel:
        for wt in project.worktrees():
            if not wt.exists:
                w.append(f"git lists the worktree {wt.path}, but its directory is gone; run "
                         f"`xwt delete {wt.name}`")

    ec = project.config.envrc
    if ec is not None:
        want = envrc.envrc_text(project)
        for ws in scope:
            path = ws / envrc.ENVRC
            if not path.exists():
                w.append(f"{ws.name} has no .envrc; run `xwt init` in {ws}")
            elif path.read_text() != want:
                w.append(f"{path} differs from what xwt.toml now produces; run `xwt init` "
                         f"in {ws}")
            if ec.venv is not None and not (ws / envrc.VENV).exists():
                w.append(f"{ws.name} has no .venv; run `xwt venv` in {ws}")

    try:
        want_cfg = (top / CONFIG_NAME).read_bytes()
    except OSError:
        want_cfg = None
    for ws in scope[1:]:
        p = ws / CONFIG_NAME
        if p.is_file() and p.read_bytes() != want_cfg:
            w.append(f"{p} differs from the toplevel's; xwt uses only the toplevel's "
                     f"({top / CONFIG_NAME}), so a change takes effect when it is merged up")
    return w


def status(project: Project, args: list[str]) -> int:
    branches = ", ".join(f"{project.repo_name(r)} {project.integ(r) or '(none)'}"
                         for r in project.repos)
    ui.out(f"toplevel {project.toplevel}: {branches}")
    ui.out()
    rc = run_under_headers([c for c in project.checkouts(project.workspace) if c.ok],
                           ["status", *args])
    if project.in_toplevel:
        targets = [w.path for w in project.worktrees() if w.exists]
    else:
        targets = [project.workspace]
    lines = relation_lines(project, targets)
    if lines:
        ui.header("branch relations")
        for line in lines:
            ui.out(line)
        ui.out()
    warnings = status_warnings(project)
    for line in warnings:
        ui.out(f"\nWARNING: {line}")
    if warnings:
        rc = max(rc, 1)
    return rc


# ---------------------------------------------------------------------------
# xwt diff (§7.9)
# ---------------------------------------------------------------------------

def diff(project: Project, args: list[str]) -> int:
    return run_under_headers(usable_checkouts(project, project.workspace), ["diff", *args])

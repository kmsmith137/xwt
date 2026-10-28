"""One function per verb (plan §7)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as configmod
from . import detect, envrc, git, layout, ui
from .config import CONFIG_NAME, ConfigError
from .layout import Checkout, Project, Repo
from .ui import XwtError, plural

WORKTREE_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*\Z")


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


# ---------------------------------------------------------------------------
# Preparing a workspace (§7.2) and adding checkouts
# ---------------------------------------------------------------------------

def prepare_workspace(project: Project, ws: Path, *, venv: bool) -> None:
    """Submodules, then with [envrc]: .envrc, `direnv allow` and (with `venv`) the venv."""
    for co in usable_checkouts(project, ws, remind=False):
        if (co.path / ".gitmodules").exists():
            rc = git.git_echo(co.path, "submodule", "update", "--init", "--recursive")
            if rc != 0:
                raise XwtError(f"`git submodule update` failed in {co.path} (exit status {rc})")
    ec = project.config.envrc
    if ec is None:
        return
    envrc.write_envrc(project, ws)
    envrc.direnv_allow(ws)
    if venv and ec.venv is not None:
        envrc.build_venv(project, ws)


def _relation_to(ab: tuple[int, int] | None, integ: str) -> str:
    if ab is None:
        return f"not comparable with {integ}"
    ahead, behind = ab
    if ahead and behind:
        return f"{plural(ahead, 'commit')} ahead of {integ}, {behind} behind"
    if ahead:
        return f"{plural(ahead, 'commit')} ahead of {integ}"
    if behind:
        return f"{plural(behind, 'commit')} behind {integ}"
    return f"up to date with {integ}"


def _prune_if_needed(repo: Repo) -> None:
    """`git worktree prune` where git lists a worktree whose directory is gone
    (`git worktree add` refuses that path otherwise)."""
    if any(e.prunable for e in git.worktree_list(repo.main)):
        git.git_echo(repo.main, "worktree", "prune")


def _branch_busy(repo: Repo, name: str) -> list[str]:
    """Problems if branch `name` is checked out in a (live) worktree of `repo`."""
    return [f"branch {name} is already checked out in {e.path}"
            for e in git.worktree_list(repo.main)
            if e.branch == name and not e.prunable and e.path != repo.main]


def _new_branch_problems(project: Project, repo: Repo, name: str) -> list[str]:
    """Problems with checking out or creating branch `name` in `repo`."""
    problems = _branch_busy(repo, name)
    nested = git.out(repo.main, "for-each-ref", "--format=%(refname)", f"refs/heads/{name}/")
    if nested:
        other = nested.splitlines()[0][len("refs/heads/"):]
        problems.append(f"{project.repo_name(repo)} has a branch {other}, so it can't have a "
                        f"branch {name}; rename that branch, or pick another name")
    return problems


def _add_checkout(project: Project, repo: Repo, path: Path, name: str) -> int:
    """`git worktree add` for one repo: a new branch `name` off the integration
    branch, or the existing branch `name` (§7.4 step 3)."""
    integ = project.integ(repo)
    if git.branch_exists(repo.main, name):
        rc = git.git_echo(repo.main, "worktree", "add", str(path), name)
        if rc == 0:
            ab = git.ahead_behind(repo.main, integ, name)
            ui.info(f"reusing existing branch {name} in {project.repo_name(repo)}: "
                    f"{_relation_to(ab, integ)}")
        return rc
    return git.git_echo(repo.main, "worktree", "add", str(path), "-b", name,
                        git.branch_arg(repo.main, integ))


# ---------------------------------------------------------------------------
# xwt init (§7.3)
# ---------------------------------------------------------------------------

def _home_group_message(top: Path) -> str:
    return (f"the toplevel {top} is directly in $HOME, so its worktrees (../NAME) would be "
            f"too; move the clone into a group dir first, e.g. "
            f"`mkdir ~/proj && mv {top} ~/proj/top`")


def init(*, no_venv: bool) -> int:
    found = layout.find_workspace(os.getcwd())
    created = False
    if found is None:
        top = detect.create_config(Path(os.getcwd()))
        if top is None:
            return 0
        created = True
        found = (top, top)
    project = Project.load(*found)
    if project.in_toplevel:
        return _init_toplevel(project, no_venv=no_venv, created=created)
    return _init_worktree(project, no_venv=no_venv)


def _init_toplevel(project: Project, *, no_venv: bool, created: bool) -> int:
    top = project.toplevel
    if project.group == layout.home():
        raise XwtError(_home_group_message(top))
    if project.config.envrc is not None:
        problem = envrc.envrc_problem(project, top)
        if problem:
            raise XwtError(problem)

    for repo in project.subs:
        dest = repo.main
        if not os.path.lexists(dest):
            ui.info(f"cloning {repo.key} into the toplevel")
            cmd = ["git", "clone", *(["-b", repo.branch] if repo.branch else []), repo.url, str(dest)]
            rc = git.run_echo(cmd)
            if rc != 0:
                raise XwtError(f"`git clone` of {repo.url} failed (exit status {rc})")
        elif not git.is_clone(dest):
            ui.warn(f"{dest} exists but is not a git clone; leaving it alone")
        else:
            origin = git.origin_url(dest)
            if origin != repo.url:
                ui.warn(f"{project.label(top, repo)}'s origin is {origin or '(none)'}, but "
                        f"xwt.toml says {repo.url}; leaving it as it is")
    for problem in project.integ_problems():
        ui.warn(problem)

    added = git.ensure_ignored(top, envrc.managed_ignores(project))
    prepare_workspace(project, top, venv=not no_venv)

    changed = (["xwt.toml"] if created else []) + ([".gitignore"] if added else [])
    if changed:
        ui.info(f"commit {' and '.join(changed)} in {top}")
    ui.info(f"toplevel ready: {top}")
    return 0


def _init_worktree(project: Project, *, no_venv: bool) -> int:
    ws = project.workspace
    problems, to_add = [], []
    for co in project.checkouts(ws):
        if co.state == "missing":
            if not project.cloned(co.repo):
                problems.append(f"{co.repo.key} isn't cloned in the toplevel yet; run "
                                f"`xwt init` in {project.toplevel} first")
            else:
                to_add.append(co)
        elif co.state == "foreign":
            ui.warn(project.reminder(co))
    if to_add:
        problems += project.integ_problems([co.repo for co in to_add])
        for co in to_add:
            problems += _new_branch_problems(project, co.repo, ws.name)
    if project.config.envrc is not None:
        problem = envrc.envrc_problem(project, ws)
        if problem:
            problems.append(problem)
    if problems:
        refuse(f"update {ws}", problems)

    for co in to_add:
        _prune_if_needed(co.repo)
        ui.info(f"adding {co.repo.key} to {ws.name}")
        rc = _add_checkout(project, co.repo, co.path, ws.name)
        if rc != 0:
            raise XwtError(f"`git worktree add` failed for {co.label} (exit status {rc})")
    prepare_workspace(project, ws, venv=not no_venv)
    ui.info(f"worktree ready: {ws}")
    return 0


# ---------------------------------------------------------------------------
# xwt new (§7.4)
# ---------------------------------------------------------------------------

def check_name(project: Project, name: str) -> list[str]:
    if WORKTREE_NAME_RE.match(name) and git.check_ref_format_branch(project.toplevel, name):
        return []
    return [f"'{name}' isn't a valid worktree name: use letters, digits, '.', '_' and '-', "
            f"not starting with '.' or '-' (it is also the branch name)"]


def new(project: Project, name: str, *, no_venv: bool) -> int:
    target = project.group / name
    problems = check_name(project, name)
    if project.group == layout.home():
        problems.append(_home_group_message(project.toplevel))
    if os.path.lexists(target):
        problems.append(f"{target} already exists")
    problems += project.not_cloned_problems()
    problems += project.integ_problems()
    if not check_name(project, name):
        for repo in project.repos:
            if not project.cloned(repo):
                continue
            if project.integ(repo) == name:
                problems.append(f"{name} is the integration branch of "
                                f"{project.label(project.toplevel, repo)}; pick another name")
            problems += _new_branch_problems(project, repo, name)
    integ = project.integ(project.repos[0])
    if project.config.envrc is not None and integ:
        # The new checkout is on the integration branch: does that track .envrc?
        if git.ok(project.toplevel, "cat-file", "-e", f"refs/heads/{integ}:.envrc"):
            problems.append(f"xwt.toml has [envrc], but .envrc is tracked in the repo (on "
                            f"{integ}); remove one of them")
    if problems:
        refuse(f"create {target}", problems)

    for repo in project.repos:
        _prune_if_needed(repo)
    created: list[str] = []
    for repo in project.repos:
        path = project.checkout_path(target, repo)
        rc = _add_checkout(project, repo, path, name)
        if rc != 0:
            msg = f"`git worktree add` failed for {project.label(target, repo)} (exit status {rc})"
            if created:
                msg += (f"; created so far: {', '.join(created)}. `xwt delete {name} --force` "
                        f"removes them")
            raise XwtError(msg)
        created.append(str(path))

    try:
        prepare_workspace(project, target, venv=False)
    except XwtError as e:
        raise XwtError(f"{e}\nThe worktree {target} is created, but preparing it failed; fix "
                       f"the problem, then run `xwt init` in {target}") from None
    ec = project.config.envrc
    if ec is not None and ec.venv is not None and not no_venv:
        try:
            envrc.build_venv(project, target)
        except XwtError as e:
            raise XwtError(f"{e}\nThe worktree {target} is complete except for its venv; "
                           f"`xwt venv` in {target} retries the build") from None
    ui.info(f"worktree {name} is ready: {target}")
    ui.out(f"  cd {target}")
    return 0


# ---------------------------------------------------------------------------
# xwt add-repo (§7.5)
# ---------------------------------------------------------------------------

_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+\Z")


def append_repo_text(old: str, name: str, url: str, branch: str | None) -> str:
    key = name if _BARE_KEY_RE.match(name) else configmod.toml_string(name)
    text = old
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"
    text += f"[repo.{key}]\nurl = {configmod.toml_string(url)}\n"
    if branch:
        text += f"branch = {configmod.toml_string(branch)}\n"
    return text


def add_repo(project: Project, url: str, *, branch: str | None) -> int:
    top = project.toplevel
    toml_path = top / CONFIG_NAME
    name = git.url_basename(url)
    if not configmod.valid_repo_name(name):
        raise XwtError(f"can't use '{name}' (from {url}) as a sub-repo name: use letters, "
                       f"digits, '.', '_' and '-' (not starting with '.')")
    if name in project.config.repos:
        raise XwtError(f"{toml_path} already has [repo.{name}]")
    old_text = toml_path.read_text()
    try:
        configmod.parse(append_repo_text(old_text, name, url, branch or "main"), toml_path)
    except ConfigError as e:
        raise XwtError(f"adding [repo.{name}] would make xwt.toml invalid: {e}") from None

    dest = top / name
    if os.path.lexists(dest):
        if not git.is_clone(dest):
            raise XwtError(f"{dest} exists but is not a git clone")
        origin = git.origin_url(dest)
        if origin != url:
            raise XwtError(f"{dest} exists, but its origin is {origin or '(none)'}, not {url}")
        cur = git.current_branch(dest)
        if branch and cur != branch:
            raise XwtError(f"{dest} is on {cur or 'a detached HEAD'}, not {branch}: check out "
                           f"{branch} there first, or leave out --branch")
        ui.info(f"{dest} is already a clone of {url}; recording it")
    else:
        ui.info(f"cloning {name} into the toplevel")
        rc = git.run_echo(["git", "clone", *(["-b", branch] if branch else []), url, str(dest)])
        if rc != 0:
            raise XwtError(f"`git clone` failed (exit status {rc})")
    cur = git.current_branch(dest)
    if cur:
        ui.info(f"{name} is on branch {cur}, which is now its integration branch")
    else:
        ui.warn(f"{name} is detached, so xwt.toml names no integration branch for it; check "
                f"out its integration branch in {dest}")

    toml_path.write_text(append_repo_text(old_text, name, url, cur))
    try:
        configmod.load(toml_path)
    except ConfigError as e:
        toml_path.write_text(old_text)
        raise XwtError(f"the new xwt.toml didn't validate, so the old one was restored: {e}") from None
    ui.info(f"added [repo.{name}] to {toml_path} (last in the install order; move it up if "
            f"something else depends on it)")
    project = Project.load(top, project.workspace)

    added = git.ensure_ignored(top, [f"/{name}/"])
    if (dest / ".gitmodules").exists():
        rc = git.git_echo(dest, "submodule", "update", "--init", "--recursive")
        if rc != 0:
            ui.warn(f"`git submodule update` failed in {dest} (exit status {rc}); run "
                    f"`xwt init` in {top} to retry")

    _add_repo_next_steps(project, name, dest, added)
    return 0


def _add_repo_next_steps(project: Project, name: str, dest: Path, added) -> None:
    top = project.toplevel
    toml_path = top / CONFIG_NAME
    ec = project.config.envrc
    has_venv = ec is not None and ec.venv is not None
    package = detect.is_python_package(dest) and has_venv
    files = (["xwt.toml"] if git.is_tracked(top, CONFIG_NAME) else []) + \
            ([".gitignore"] if added else [])
    worktrees = [w.path for w in project.worktrees() if w.exists]

    steps: list[str] = []
    if package:
        steps.append(f"{name} is a Python package. To install it into the venvs, add "
                     f"\"{name}\" to `editable` under [envrc.venv] in {toml_path} (after "
                     f"anything it depends on).")
    if files:
        steps.append(f"Commit {' and '.join(files)} in {top}.")
    targets = ([top] if package else []) + worktrees
    if targets:
        rebuild = " and rebuild the venv" if has_venv else ""
        if package and worktrees:
            text = (f"Run `xwt init` in the toplevel (to install {name} into its venv) and in "
                    f"each worktree, to add the {name} checkout{rebuild}:")
        elif package:
            text = f"Run `xwt init` in the toplevel, to install {name} into its venv:"
        else:
            text = f"Run `xwt init` in each worktree, to add the {name} checkout{rebuild}:"
        text += "".join(f"\n    {t}" for t in targets)
        if worktrees:
            text += (f"\nUntil then, xwt commands in those worktrees remind you that {name} "
                     f"is missing.")
        steps.append(text)
    if not steps:
        return
    ui.out()
    ui.out("Next steps:")
    for i, step in enumerate(steps, 1):
        ui.out(_numbered(i, step))


def _numbered(i: int, text: str) -> str:
    import textwrap
    width = max(40, ui._width())
    lead = f"  {i}. "
    indent = " " * len(lead)
    out = []
    for j, para in enumerate(text.split("\n")):
        if para.startswith("    "):
            out.append(indent + para)
        else:
            out.extend(textwrap.wrap(para, width=width, initial_indent=lead if j == 0 else indent,
                                     subsequent_indent=indent, break_long_words=False,
                                     break_on_hyphens=False))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# xwt venv (§7.6)
# ---------------------------------------------------------------------------

def venv(project: Project, *, recreate: bool, bare: bool) -> int:
    ec = project.config.envrc
    if ec is None or ec.venv is None:
        raise XwtError(f"{project.toplevel / CONFIG_NAME} has no [envrc.venv], so xwt builds "
                       f"no venv")
    ws = project.workspace
    path = ws / envrc.ENVRC
    if not path.exists():
        raise XwtError(f"{ws} has no .envrc; run `xwt init` here first")
    if path.read_text() != envrc.envrc_text(project):
        ui.warn(f"{path} differs from what xwt.toml now produces; `xwt init` here updates it")
    envrc.build_venv(project, ws, recreate=recreate, bare=bare)
    return 0


# ---------------------------------------------------------------------------
# Moving commits: shared pieces
# ---------------------------------------------------------------------------

def _on_branch(co: Checkout, name: str) -> tuple[bool, str]:
    """Whether checkout `co` is on branch `name` (or in the middle of rebasing
    it), and a phrase for where it is otherwise."""
    cur = git.current_branch(co.path)
    if cur == name:
        return True, ""
    if cur is None and git.in_progress(co.path) == "rebase" and git.rebasing_branch(co.path) == name:
        return True, ""
    return False, f"on {cur}" if cur else "detached"


def _tracked_changes(path: Path) -> bool:
    return bool(git.status_entries(path, "--untracked-files=no", "--ignore-submodules=all"))


def _finish_hint(op: str, path: Path) -> str:
    if op == "merge":
        return f"finish it with `git commit` or `git merge --abort` in {path}"
    return f"finish it with `git {op} --continue` or `--abort` in {path}"


def _run_planned(steps, *, dry_run: bool) -> list[tuple[str, Path]]:
    """Run (header, label, path, cmd) steps one after another, each under its
    header, with git's output going straight to the terminal. Returns the
    (label, path) of each step that failed."""
    failed = []
    for header, label, path, cmd in steps:
        ui.header(header)
        if dry_run:
            ui.out(f"{ui.PREFIX}(dry run) $ {ui.cmdline(cmd)}")
        elif git.run_echo(cmd) != 0:
            failed.append((label, path))
        ui.out()
    return failed


def _failure(failed, op: str, stopped_text: str) -> XwtError:
    """The error after steps failed: those left with `op` in progress stopped on
    conflicts; the others failed before starting (git said why)."""
    stopped = [(l, p) for l, p in failed if git.in_progress(p) == op]
    other = [(l, p) for l, p in failed if (l, p) not in stopped]
    parts = []
    if stopped:
        parts.append(f"the {op} stopped in {', '.join(l for l, _ in stopped)}: {stopped_text} in:"
                     + "".join(f"\n    {p}" for _, p in stopped))
    if other:
        parts.append(f"`git {op}` failed in {', '.join(l for l, _ in other)} (see git's message "
                     f"above); nothing was changed there")
    return XwtError("\n".join(parts))


def _untracked(path: Path) -> set[str]:
    return {e.path for e in git.status_entries(path, "--untracked-files=all") if e.xy == "??"}


def _changed_files(path: Path, base: str, tip: str) -> list[str]:
    """Files changed from the merge base of `base` and `tip` to `tip` (renames
    as a delete plus an add, so both paths count)."""
    out = git.out(path, "diff", "--name-only", "--no-renames", "-z",
                  f"refs/heads/{base}...refs/heads/{tip}")
    return [f for f in out.split("\0") if f]


def _some_files(files: list[str], n: int = 5) -> str:
    return ", ".join(files[:n]) + (f" and {len(files) - n} more" if len(files) > n else "")


# ---------------------------------------------------------------------------
# xwt rebase-down (§7.10)
# ---------------------------------------------------------------------------

def rebase_down(project: Project, rebase_args: list[str], *, dry_run: bool) -> int:
    if project.in_toplevel:
        raise XwtError("there's no branch to rebase in the toplevel; run this in a worktree")
    ws = project.workspace
    name = ws.name
    problems = project.integ_problems()
    steps = []
    for co in usable_checkouts(project, ws):
        on, where = _on_branch(co, name)
        if not on:
            ui.warn(f"{co.label} is {where}, not on {name}; skipping it")
            continue
        integ = project.integ(co.repo)
        if integ is None:
            continue                            # reported by integ_problems
        ab = git.ahead_behind(co.path, integ, name)
        if ab is None:
            problems.append(f"can't compare {name} with {integ} in {co.label}")
            continue
        if ab[1] == 0:
            ui.info(f"{co.label}: {name} isn't behind {integ}; nothing to rebase")
            continue
        op = git.in_progress(co.path)
        if op:
            problems.append(f"{co.label} has a {op} in progress: {_finish_hint(op, co.path)}")
        elif _tracked_changes(co.path):
            problems.append(f"{co.label} has uncommitted changes to tracked files; commit or "
                            f"stash them first")
        else:
            clash = sorted(_untracked(co.path) & set(_changed_files(co.path, name, integ)))
            if clash:
                problems.append(f"{co.label} has untracked files that the rebase would overwrite "
                                f"({_some_files(clash)}); move or remove them first")
        steps.append((f"{co.label}: rebase {name} onto {integ}", co.label, co.path,
                      ["git", "-C", str(co.path), "rebase", *rebase_args,
                       git.branch_arg(co.path, integ)]))
    if problems:
        refuse("rebase", problems)
    if not steps:
        ui.info("nothing to rebase")
        return 0
    failed = _run_planned(steps, dry_run=dry_run)
    if failed:
        raise _failure(failed, "rebase", "resolve the conflicts, then run `git rebase "
                                         "--continue` (or `git rebase --abort`)")
    return 0


# ---------------------------------------------------------------------------
# xwt merge-up (§7.11)
# ---------------------------------------------------------------------------

def merge_up(project: Project, *, dry_run: bool, no_ff: bool) -> int:
    if project.in_toplevel:
        raise XwtError("merge-up lands a worktree's branch; run it in that worktree")
    ws = project.workspace
    name = ws.name
    problems = project.integ_problems()
    steps = []
    for co in usable_checkouts(project, ws):
        on, where = _on_branch(co, name)
        if not on:
            ui.warn(f"{co.label} is {where}, not on {name}; skipping it")
            continue
        repo = co.repo
        integ = project.integ(repo)
        if integ is None:
            continue
        main = repo.main
        mlabel = project.label(project.toplevel, repo)
        if git.is_ancestor(main, f"refs/heads/{name}", f"refs/heads/{integ}"):
            ui.info(f"{mlabel}: {integ} already contains {name}; nothing to land")
            continue
        rname = project.repo_name(repo)
        op = git.in_progress(co.path)
        if op:
            problems.append(f"{co.label} has a {op} in progress: {_finish_hint(op, co.path)}")
        if not no_ff and not git.is_ancestor(main, f"refs/heads/{integ}", f"refs/heads/{name}"):
            ab = git.ahead_behind(main, integ, name)
            n = plural(ab[1], "commit") if ab else "commits"
            problems.append(f"{rname}: {integ} has {n} not in {name}; run `xwt rebase-down` first")
        dirty = {e.path for e in git.status_entries(main, "--untracked-files=all")}
        clash = [f for f in _changed_files(main, integ, name) if f in dirty]
        if clash:
            problems.append(f"{mlabel} has uncommitted changes to, or untracked copies of, files "
                            f"the merge changes ({_some_files(clash)}); commit, stash or move "
                            f"them first")
        if no_ff and not git.ok(main, "diff", "--cached", "--quiet"):
            problems.append(f"{mlabel} has staged changes, and a merge commit needs a clean "
                            f"index; commit or unstage them first")
        mop = git.in_progress(main)
        if mop:
            problems.append(f"{mlabel} has a {mop} in progress: {_finish_hint(mop, main)}")
        mode = ["--no-ff", "--no-edit"] if no_ff else ["--ff-only"]
        steps.append((f"{mlabel}: merge {name} into {integ}", mlabel, main,
                      ["git", "-C", str(main), "merge", *mode, git.branch_arg(main, name)]))
    if problems:
        refuse("merge", problems)
    if not steps:
        ui.info(f"nothing to land for {name}")
        return 0
    failed = _run_planned(steps, dry_run=dry_run)
    if failed:
        raise _failure(failed, "merge", "resolve the conflicts and commit, or run "
                                        "`git merge --abort`,")
    if not dry_run:
        ui.info(f"landed {name}")
    return 0


# ---------------------------------------------------------------------------
# xwt delete (§7.7)
# ---------------------------------------------------------------------------

def _some(entries, n=5) -> str:
    shown = ", ".join(e.path for e in entries[:n])
    return shown + (f" and {len(entries) - n} more" if len(entries) > n else "")


def _locked(repo: Repo, path: Path) -> bool:
    return any(e.path == path and e.locked for e in git.worktree_list(repo.main))


def _unmanaged_checkouts(project: Project, target: Path) -> list[str]:
    """Git checkouts inside `target` that aren't its xwt checkouts or their
    submodules: a clone, or a worktree such as Claude Code's
    .claude/worktrees/*. Removing the directory would destroy them."""
    expected = {co.path for co in project.checkouts(target)}
    mains = {(r.main / ".git").resolve(): r for r in project.repos}
    found = []
    for dirpath, dirnames, filenames in os.walk(target):
        d = Path(dirpath)
        has_git = ".git" in dirnames or ".git" in filenames
        if ".git" in dirnames:
            dirnames.remove(".git")
        if not has_git or d in expected:
            continue
        if git.git(d, "rev-parse", "--show-superproject-working-tree").stdout.strip():
            continue                                    # a submodule of a checkout
        dirnames[:] = []
        repo = mains.get(git.common_dir(d))
        if repo is not None:
            found.append(f"{d} is a git worktree of {repo.main} that xwt doesn't manage; "
                         f"remove it first (`git -C {repo.main} worktree remove {d}`), or move "
                         f"it out")
        else:
            found.append(f"{d} is a separate git checkout; move it out of {target} first")
    return found


def _submodule_only_commits(co: Checkout) -> list[str]:
    """Submodules of `co` with commits on no remote-tracking branch. A linked
    worktree's submodules keep their own store (in the main repo's
    .git/worktrees/), which goes with the worktree."""
    if not (co.path / ".gitmodules").exists():
        return []
    script = ('test -z "$(git rev-list -n 1 HEAD --branches --not --remotes)" '
              '|| echo "$displaypath"')
    res = git.git(co.path, "submodule", "foreach", "--quiet", "--recursive", script)
    return [f"{co.label}/{sub} (a submodule) has commits that are on no remote branch; they "
            f"are stored only in this worktree: push them first"
            for sub in res.stdout.split()]


def _delete_checks(project: Project, target: Path, name: str):
    """(problems --force overrides, problems it doesn't) for deleting `target`."""
    soft, hard = [], []
    hard += _unmanaged_checkouts(project, target)
    for co in project.checkouts(target):
        if co.state == "missing":
            continue
        if co.state == "foreign":
            if os.path.lexists(co.path / ".git"):
                hard.append(f"{co.path} is a separate git repository, not a worktree of "
                            f"{co.repo.main}; move it out of {target} first")
            else:
                soft.append(f"{co.path} is not a git worktree of {co.repo.main}")
            continue
        entries = git.status_entries(co.path)
        tracked = [e for e in entries if e.xy != "??"]
        untracked = [e for e in entries if e.xy == "??"]
        if tracked:
            soft.append(f"{co.label} has uncommitted changes to tracked files: {_some(tracked)}")
        if untracked:
            soft.append(f"{co.label} has untracked files: {_some(untracked)}")
        integ = project.integ(co.repo)
        main = co.repo.main
        if integ and integ != name and git.branch_exists(main, name):
            ab = git.ahead_behind(main, integ, name)
            if ab and ab[0]:
                soft.append(f"{co.label}: {plural(ab[0], 'commit')} on {name} not in {integ}; "
                            f"run `xwt merge-up` first")
        op = git.in_progress(co.path)
        if op:
            soft.append(f"{co.label} has a {op} in progress")
        elif git.current_branch(co.path) is None:
            # A detached HEAD's commits are lost with the worktree unless a branch has them.
            if not git.out(co.path, "for-each-ref", "--contains", "HEAD", "refs/heads"):
                soft.append(f"{co.label} is detached at a commit that no branch contains")
        if _locked(co.repo, co.path):
            soft.append(f"{co.label} is locked (`git worktree lock`)")
        soft += _submodule_only_commits(co)
    return soft, hard


def _delete_branches(project: Project, name: str) -> None:
    """`git branch -d NAME` in each repo, where NAME is merged into the
    integration branch; otherwise keep it, with a warning."""
    for repo in project.repos:
        if not project.cloned(repo) or not git.branch_exists(repo.main, name):
            continue
        integ = project.integ(repo)
        rname = project.repo_name(repo)
        if integ is None or integ == name:
            continue
        # Checked here rather than left to `git branch -d`, which also accepts a
        # branch merged only into its upstream.
        if not git.is_ancestor(repo.main, f"refs/heads/{name}", f"refs/heads/{integ}"):
            ui.warn(f"kept branch {name} in {rname}: not merged into {integ}; its commits are "
                    f"preserved")
            continue
        rc = git.git_echo(repo.main, "branch", "-d", name)
        if rc != 0:
            ui.warn(f"kept branch {name} in {rname}: `git branch -d` refused it")


def delete(project: Project, name: str, *, force: bool) -> int:
    target = project.group / name
    if target == project.toplevel:
        raise XwtError(f"{target} is the toplevel; it can't be deleted")
    cwd = Path(os.getcwd()).resolve()
    if cwd == target or target in cwd.parents:
        raise XwtError(f"the current directory is inside {target}; run `xwt delete {name}` "
                       f"from the toplevel or another worktree")
    wt = next((w for w in project.worktrees() if w.name == name), None)
    if wt is None:
        what = "isn't a worktree" if os.path.lexists(target) else "doesn't exist and isn't a worktree"
        raise XwtError(f"{target} {what} of {project.toplevel}")
    problems = project.integ_problems()
    if problems:
        refuse(f"delete {name}", problems)

    if not wt.exists:
        ui.info(f"{target} is already gone; clearing git's record of it and deleting its "
                f"merged branches")
        for repo in project.repos:
            if project.cloned(repo):
                _prune_if_needed(repo)
        _delete_branches(project, name)
        return 0

    soft, hard = _delete_checks(project, target, name)
    if hard:
        refuse(f"delete {name}, even with --force" if force else f"delete {name}", hard + soft)
    if soft and not force:
        refuse(f"delete {name}", soft + [
            "with --force these are lost; committed work stays on its branches"])
    if soft:
        ui.warn(f"deleting {name} anyway (--force):\n" + ui.bullet_list(soft))

    checkouts = [co for co in project.checkouts(target) if co.ok]
    for co in checkouts[1:] + checkouts[:1]:          # sub-repos first, then the toplevel's
        flags = ["--force", "--force"] if _locked(co.repo, co.path) else ["--force"]
        rc = git.git_echo(co.repo.main, "worktree", "remove", *flags, str(co.path))
        if rc != 0:
            ui.warn(f"`git worktree remove` failed for {co.label}; removing its files instead")
    if os.path.lexists(target):
        ui.info(f"removing what is left of {target}")
        shutil.rmtree(target)                      # unlinks symlinks; never follows them
    for repo in project.repos:
        if project.cloned(repo):
            _prune_if_needed(repo)
    _delete_branches(project, name)
    ui.info(f"deleted worktree {name}")
    return 0

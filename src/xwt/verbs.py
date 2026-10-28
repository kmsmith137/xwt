"""One function per verb (plan §7)."""

from __future__ import annotations

import os
import re
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
    return git.git_echo(repo.main, "worktree", "add", str(path), "-b", name, integ)


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
            problems += _branch_busy(co.repo, ws.name)
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
    if not problems:
        for repo in project.repos:
            if project.integ(repo) == name:
                problems.append(f"{name} is the integration branch of "
                                f"{project.label(project.toplevel, repo)}; pick another name")
            problems += _branch_busy(repo, name)
    if project.config.envrc is not None and not problems:
        # The new checkout is on the integration branch: does that track .envrc?
        integ = project.integ(project.repos[0])
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

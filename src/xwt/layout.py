"""Finding the toplevel and the current workspace (plan §4.1); the project's
repos, worktrees and checkouts (§3).

xwt keeps no state: a worktree is any git worktree of the toplevel's repo
whose directory is directly in the group dir.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from . import config as configmod
from . import git
from .config import CONFIG_NAME, Config
from .ui import XwtError

NOT_FOUND = ("not inside an xwt toplevel or worktree; run `xwt init` in a repo's "
             "main checkout to make it one")


def home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def find_workspace(cwd) -> tuple[Path, Path] | None:
    """(toplevel, workspace) for `cwd`, or None. Walks up to $HOME (inclusive),
    or to / when `cwd` isn't under $HOME (plan §4.1)."""
    h = home()
    d = Path(cwd).resolve()
    under_home = d == h or h in d.parents
    while True:
        if (d / ".git").exists():
            m = git.main_checkout(d)
            if m is not None and (m / CONFIG_NAME).is_file() and (d == m or d.parent == m.parent):
                if d == m and d != h and _lists_sub_repo(d.parent, d.name):
                    # A sub-repo's main checkout that has its own xwt.toml: the
                    # enclosing toplevel wins (in a worktree, the sibling test
                    # already sees to that).
                    return d.parent, d.parent
                return m, d
        if (under_home and d == h) or d.parent == d:
            return None
        d = d.parent


def _lists_sub_repo(top: Path, name: str) -> bool:
    """Whether `top` is a toplevel whose xwt.toml lists the sub-repo `name`."""
    if not ((top / ".git").is_dir() and (top / CONFIG_NAME).is_file()):
        return False
    try:
        return name in configmod.load(top / CONFIG_NAME).repos
    except configmod.ConfigError:
        return False


@dataclass
class Repo:
    """One of the project's repos: the toplevel's own (key ".") or a sub-repo."""
    key: str                  # "." or the sub-repo's directory name
    main: Path                # its main checkout
    branch: str | None        # integration branch named in xwt.toml
    url: str | None = None    # sub-repos only

    @property
    def is_top(self) -> bool:
        return self.key == "."


@dataclass
class Worktree:
    name: str
    path: Path
    exists: bool
    branch: str | None
    locked: bool


@dataclass
class Checkout:
    """One repo's checkout in one workspace."""
    repo: Repo
    ws: Path
    path: Path
    label: str
    state: str    # "ok", "missing" (no directory) or "foreign" (not the expected repo)

    @property
    def ok(self) -> bool:
        return self.state == "ok"


class Project:
    def __init__(self, toplevel: Path, workspace: Path, cfg: Config):
        self.toplevel = Path(toplevel)
        self.workspace = Path(workspace)
        self.config = cfg
        self.group = self.toplevel.parent
        self.repos = [Repo(".", self.toplevel, cfg.branch)]
        for name, rc in cfg.repos.items():
            self.repos.append(Repo(name, self.toplevel / name, rc.branch, rc.url))

    @classmethod
    def load(cls, toplevel: Path, workspace: Path) -> "Project":
        try:
            cfg = configmod.load(toplevel / CONFIG_NAME)
        except configmod.ConfigError as e:
            raise XwtError(str(e)) from None
        return cls(toplevel, workspace, cfg)

    @classmethod
    def find(cls, cwd=None) -> "Project":
        found = find_workspace(cwd or os.getcwd())
        if found is None:
            raise XwtError(NOT_FOUND)
        return cls.load(*found)

    # -- naming -------------------------------------------------------------

    @property
    def in_toplevel(self) -> bool:
        return self.workspace == self.toplevel

    @property
    def subs(self) -> list[Repo]:
        return self.repos[1:]

    @cached_property
    def top_name(self) -> str:
        """The toplevel repo's name: its origin URL's basename, else its directory name."""
        url = git.origin_url(self.toplevel)
        return (git.url_basename(url) if url else "") or self.toplevel.name

    def repo_name(self, repo: Repo) -> str:
        return self.top_name if repo.is_top else repo.key

    def label(self, ws: Path, repo: Repo) -> str:
        if repo.is_top:
            return f"{ws.name}/ (repo {self.top_name})"
        return f"{ws.name}/{repo.key}"

    def checkout_path(self, ws: Path, repo: Repo) -> Path:
        return ws if repo.is_top else ws / repo.key

    # -- integration branches (§4.3) ----------------------------------------

    def cloned(self, repo: Repo) -> bool:
        return repo.is_top or git.is_clone(repo.main)

    def integ(self, repo: Repo) -> str | None:
        """The repo's integration branch: named in xwt.toml, else whatever its
        main checkout has checked out (None if detached or not cloned)."""
        if repo.branch:
            return repo.branch
        if not self.cloned(repo):
            return None
        return git.current_branch(repo.main)

    def integ_problems(self, repos=None) -> list[str]:
        """Main checkouts (of `repos`, default all) that aren't on their integration branch."""
        problems = []
        for repo in self.repos if repos is None else repos:
            if not self.cloned(repo):
                continue
            label = self.label(self.toplevel, repo)
            cur = git.current_branch(repo.main)
            if repo.branch is None:
                if cur is None:
                    problems.append(
                        f"{label} is detached, and xwt.toml names no integration branch for "
                        f"it: check out its integration branch there, or set `branch` in "
                        f"xwt.toml")
            elif cur != repo.branch:
                on = f"on {cur}" if cur else "detached"
                problems.append(f"{label} is {on}, but xwt.toml says {repo.branch}: check out "
                                f"{repo.branch} there, or change xwt.toml")
        return problems

    def not_cloned_problems(self) -> list[str]:
        return [f"{repo.key} is in xwt.toml but not cloned in the toplevel; run `xwt init` "
                f"in {self.toplevel}" for repo in self.subs if not self.cloned(repo)]

    # -- worktrees and checkouts ---------------------------------------------

    def worktrees(self) -> list[Worktree]:
        """Git worktrees of the toplevel's repo directly in the group dir, by name.
        Includes ones whose directory is gone (`exists` False)."""
        result = []
        for e in git.worktree_list(self.toplevel)[1:]:
            if e.path.parent != self.group:
                continue
            result.append(Worktree(e.path.name, e.path, e.path.exists() and not e.prunable,
                                   e.branch, e.locked))
        return sorted(result, key=lambda w: w.name)

    def workspaces(self) -> list[Path]:
        """The toplevel, then every existing worktree."""
        return [self.toplevel] + [w.path for w in self.worktrees() if w.exists]

    def checkouts(self, ws: Path) -> list[Checkout]:
        ws = Path(ws)
        top = ws == self.toplevel
        result = []
        for repo in self.repos:
            path = self.checkout_path(ws, repo)
            label = self.label(ws, repo)
            if repo.is_top:
                state = "ok"
            elif not os.path.lexists(path):
                state = "missing"
            elif top:
                state = "ok" if git.is_clone(path) else "foreign"
            elif (path / ".git").is_file() and git.common_dir(path) == (repo.main / ".git").resolve():
                state = "ok"
            else:
                state = "foreign"
            result.append(Checkout(repo, ws, path, label, state))
        return result

    def reminder(self, co: Checkout) -> str:
        """The note for a checkout that isn't there or isn't the expected repo (§7.1)."""
        name = co.repo.key
        top = co.ws == self.toplevel
        if co.state == "missing":
            if top or not self.cloned(co.repo):
                return (f"{name} is in xwt.toml but not cloned in the toplevel yet; run "
                        f"`xwt init` in the toplevel ({self.toplevel})")
            return (f"{co.ws.name} has no checkout of {name} (added to xwt.toml after "
                    f"{co.ws.name} was created); run `xwt init` in {co.ws} to add it")
        if top:
            return f"{co.path} is not a git clone; skipping it"
        return f"{co.path} is not a git worktree of {co.repo.main}; skipping it"

    def ws_name(self, ws: Path) -> str:
        return Path(ws).name

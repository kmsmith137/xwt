"""git subprocess wrappers and the git facts xwt reads.

Queries run captured and quietly; commands that change something go through
`run_echo`, which announces them first and lets their output reach the
terminal.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import ui
from .ui import XwtError

# Variables that would point git somewhere other than the -C directory.
_GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                      "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                      "GIT_NAMESPACE", "GIT_PREFIX")


def git_env(base=None) -> dict:
    env = dict(os.environ if base is None else base)
    for var in _GIT_LOCATION_VARS:
        env.pop(var, None)
    return env


def git(path, *args, check: bool = False) -> subprocess.CompletedProcess:
    """Run `git -C path ARGS...` captured (text). With check, a failure raises."""
    cmd = ["git", "-C", str(path), *[str(a) for a in args]]
    res = subprocess.run(cmd, capture_output=True, text=True, env=git_env())
    if check and res.returncode != 0:
        msg = res.stderr.strip() or res.stdout.strip() or f"exit status {res.returncode}"
        raise XwtError(f"`{ui.cmdline(cmd)}` failed: {msg}")
    return res


def out(path, *args) -> str:
    """stdout of `git -C path ARGS...`, stripped; a failure raises."""
    return git(path, *args, check=True).stdout.strip()


def ok(path, *args) -> bool:
    return git(path, *args).returncode == 0


def run_echo(cmd, cwd=None, env=None) -> int:
    """Echo a command, then run it with its output going to the terminal."""
    ui.echo(cmd, cwd)
    return subprocess.run([str(c) for c in cmd], cwd=cwd,
                          env=git_env(env)).returncode


def git_echo(path, *args) -> int:
    return run_echo(["git", "-C", str(path), *args])


# ---------------------------------------------------------------------------
# Repository facts
# ---------------------------------------------------------------------------

def common_dir(path) -> Path | None:
    res = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if res.returncode != 0:
        return None
    return Path(res.stdout.strip()).resolve()


def git_dir(path) -> Path | None:
    """The checkout's own git dir (differs from the common dir in a linked worktree)."""
    res = git(path, "rev-parse", "--path-format=absolute", "--git-dir")
    if res.returncode != 0:
        return None
    return Path(res.stdout.strip()).resolve()


def main_checkout(path) -> Path | None:
    """The main checkout of the repo that `path` belongs to: the parent of its
    common dir when that is a `.git` directory; None for bare repos and
    submodules (whose store lives elsewhere)."""
    cd = common_dir(path)
    if cd is None or cd.name != ".git":
        return None
    return cd.parent


def is_clone(path) -> bool:
    """A main checkout: `.git` is a directory."""
    return (Path(path) / ".git").is_dir()


def current_branch(path) -> str | None:
    """The checked-out branch, or None when HEAD is detached."""
    res = git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    return res.stdout.strip() if res.returncode == 0 else None


def in_progress(path) -> str | None:
    """'rebase', 'merge' or 'cherry-pick' when one is in progress in this checkout."""
    gd = git_dir(path)
    if gd is None:
        return None
    if (gd / "rebase-merge").is_dir() or (gd / "rebase-apply").is_dir():
        # rebase-apply is also used by `git am`.
        if (gd / "rebase-apply" / "applying").exists():
            return "am"
        return "rebase"
    if (gd / "MERGE_HEAD").exists():
        return "merge"
    if (gd / "CHERRY_PICK_HEAD").exists():
        return "cherry-pick"
    if (gd / "REVERT_HEAD").exists():
        return "revert"
    return None


def rebasing_branch(path) -> str | None:
    """During a rebase HEAD is detached; this is the branch being rebased."""
    gd = git_dir(path)
    if gd is None:
        return None
    for d in ("rebase-merge", "rebase-apply"):
        f = gd / d / "head-name"
        if f.is_file():
            ref = f.read_text().strip()
            if ref.startswith("refs/heads/"):
                return ref[len("refs/heads/"):]
    return None


def branch_exists(path, branch: str) -> bool:
    return ok(path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")


def ahead_behind(path, base: str, branch: str) -> tuple[int, int] | None:
    """(ahead, behind) of `branch` relative to `base`, or None if either is missing."""
    res = git(path, "rev-list", "--left-right", "--count", f"refs/heads/{base}...refs/heads/{branch}")
    if res.returncode != 0:
        return None
    behind, ahead = (int(n) for n in res.stdout.split())
    return ahead, behind


def is_ancestor(path, a: str, b: str) -> bool:
    return ok(path, "merge-base", "--is-ancestor", a, b)


def origin_url(path) -> str | None:
    res = git(path, "remote", "get-url", "origin")
    url = res.stdout.strip()
    return url if res.returncode == 0 and url else None


def url_basename(url: str) -> str:
    """The last component of a git URL, without `.git`:
    github:me/proj, git@host:me/proj.git, /path/to/proj/ all give 'proj'."""
    s = url.rstrip("/")
    for sep in ("/", ":"):
        s = s.rsplit(sep, 1)[-1]
    if s.endswith(".git"):
        s = s[:-4]
    return s


def is_tracked(path, relpath: str) -> bool:
    return ok(path, "ls-files", "--error-unmatch", "--", relpath)


def check_ref_format_branch(path, name: str) -> bool:
    return ok(path, "check-ref-format", "--branch", name)


@dataclass
class StatusEntry:
    xy: str
    path: str


def status_entries(path, *extra) -> list[StatusEntry]:
    """`git status --porcelain=v1 -z`, parsed. For renames, `path` is the new name
    and the old name is a separate entry."""
    raw = git(path, "status", "--porcelain=v1", "-z", *extra, check=True).stdout
    entries = []
    parts = raw.split("\0")
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if not item:
            continue
        xy, p = item[:2], item[3:]
        entries.append(StatusEntry(xy, p))
        if xy[0] in "RC" or xy[1] in "RC":
            if i < len(parts) and parts[i]:
                entries.append(StatusEntry(xy, parts[i]))
            i += 1
    return entries


# ---------------------------------------------------------------------------
# git worktree list
# ---------------------------------------------------------------------------

@dataclass
class WorktreeEntry:
    path: Path
    head: str | None = None
    branch: str | None = None      # short name; None when detached
    detached: bool = False
    bare: bool = False
    locked: bool = False
    prunable: bool = False


def worktree_list(path) -> list[WorktreeEntry]:
    """`git worktree list --porcelain`, parsed; the main worktree comes first."""
    text = out(path, "worktree", "list", "--porcelain")
    entries: list[WorktreeEntry] = []
    cur = None
    for line in text.splitlines():
        if line.startswith("worktree "):
            cur = WorktreeEntry(Path(line[len("worktree "):]).resolve())
            entries.append(cur)
        elif cur is None:
            continue
        elif line.startswith("HEAD "):
            cur.head = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur.branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur.detached = True
        elif line == "bare":
            cur.bare = True
        elif line == "locked" or line.startswith("locked "):
            cur.locked = True
        elif line == "prunable" or line.startswith("prunable "):
            cur.prunable = True
    return entries


# ---------------------------------------------------------------------------
# .gitignore
# ---------------------------------------------------------------------------

def is_ignored(repo, relpath: str) -> bool:
    """Whether `relpath` (a directory given with a trailing slash) is ignored by `repo`."""
    res = git(repo, "check-ignore", "-q", "--", relpath)
    if res.returncode not in (0, 1):
        raise XwtError(f"`git check-ignore {relpath}` failed in {repo}: {res.stderr.strip()}")
    return res.returncode == 0


def ensure_ignored(repo, patterns) -> list[str]:
    """Append each pattern that isn't ignored yet to `repo`/.gitignore, under a
    `# added by xwt` comment. `patterns` are anchored lines such as `/name/`;
    each is checked as the path it matches. Returns the lines added."""
    missing = []
    for pattern in patterns:
        if not is_ignored(repo, pattern.lstrip("/")):
            missing.append(pattern)
    if not missing:
        return []
    gi = Path(repo) / ".gitignore"
    old = gi.read_text() if gi.exists() else ""
    text = old
    if text and not text.endswith("\n"):
        text += "\n"
    if text and not text.endswith("\n\n"):
        text += "\n"
    text += "# added by xwt\n" + "".join(p + "\n" for p in missing)
    gi.write_text(text)
    for p in missing:
        ui.info(f"added {p} to {gi}")
    return missing

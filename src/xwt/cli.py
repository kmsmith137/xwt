"""Command-line interface: flat verbs (plan §5)."""

from __future__ import annotations

import argparse
import sys

from . import __version__, ui
from .config import ConfigError
from .ui import XwtError

USAGE = """\
usage: xwt VERB [options] [args...]

Set up:
  xwt init [--no-venv]                 make this repo an xwt toplevel (asks before writing
                                       xwt.toml), or bring this workspace up to date with it
  xwt new NAME [--no-venv]             create the worktree ../NAME (branch NAME in every repo)
  xwt add-repo URL [--branch B]        clone a sub-repo into the toplevel; add it to xwt.toml
  xwt venv [--recreate] [--bare]       (re)build this workspace's .venv
  xwt delete NAME [--force]            remove worktree NAME and its merged branches

Look (extra args pass through to git):
  xwt status [ARGS]                    git status in every repo, branch relations, warnings
  xwt diff [ARGS]                      git diff in every repo

Move commits (run in a worktree):
  xwt rebase-down [--dry-run] [-- ARGS]   rebase each repo's branch onto its integration branch
  xwt merge-up [--dry-run] [--no-ff]      fast-forward each integration branch to this branch
"""


def _parser(verb: str, description: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog=f"xwt {verb}", description=description)


def build_parsers() -> dict[str, argparse.ArgumentParser]:
    p = {}
    ap = _parser("init", "Make this repo an xwt toplevel (asks before writing xwt.toml), or "
                 "bring this workspace up to date with xwt.toml.")
    ap.add_argument("--no-venv", action="store_true", help="skip the venv build")
    p["init"] = ap
    ap = _parser("new", "Create the worktree ../NAME, on a new branch NAME in every repo.")
    ap.add_argument("name", metavar="NAME")
    ap.add_argument("--no-venv", action="store_true", help="skip the venv build")
    p["new"] = ap
    ap = _parser("add-repo", "Clone a sub-repo into the toplevel and add it to xwt.toml.")
    ap.add_argument("url", metavar="URL")
    ap.add_argument("-b", "--branch", metavar="B", help="branch to clone (default: the remote's)")
    p["add-repo"] = ap
    ap = _parser("venv", "(Re)build this workspace's .venv.")
    ap.add_argument("--recreate", action="store_true", help="remove .venv and build it again")
    ap.add_argument("--bare", action="store_true", help="only create the venv; install nothing")
    p["venv"] = ap
    ap = _parser("delete", "Remove worktree NAME and its merged branches.")
    ap.add_argument("name", metavar="NAME")
    ap.add_argument("--force", action="store_true",
                    help="delete despite uncommitted changes, untracked files or unmerged "
                         "commits (unmerged branches are still kept)")
    p["delete"] = ap
    ap = _parser("rebase-down", "Rebase each repo's branch onto its integration branch. "
                 "Arguments after `--` go to `git rebase`.")
    ap.add_argument("--dry-run", action="store_true", help="print the commands; run nothing")
    p["rebase-down"] = ap
    ap = _parser("merge-up", "Fast-forward each repo's integration branch to this "
                 "worktree's branch.")
    ap.add_argument("--dry-run", action="store_true", help="print the commands; run nothing")
    ap.add_argument("--no-ff", action="store_true", help="make a merge commit in each repo")
    p["merge-up"] = ap
    return p


PASS_THROUGH = {"status": "git status in every repo, then branch relations and warnings.",
                "diff": "git diff in every repo."}


def dispatch(verb: str, rest: list[str]) -> int:
    from . import verbs
    from .layout import Project

    if verb in PASS_THROUGH:
        if rest in (["-h"], ["--help"]):
            ui.out(f"usage: xwt {verb} [ARGS]\n\n{PASS_THROUGH[verb]} "
                   f"ARGS pass through to git {verb}.")
            return 0
        project = Project.find()
        return verbs.status(project, rest) if verb == "status" else verbs.diff(project, rest)

    passthrough: list[str] = []
    if verb == "rebase-down" and "--" in rest:
        i = rest.index("--")
        rest, passthrough = rest[:i], rest[i + 1:]
    args = build_parsers()[verb].parse_args(rest)

    if verb == "init":
        return verbs.init(no_venv=args.no_venv)
    project = Project.find()
    if verb == "new":
        return verbs.new(project, args.name, no_venv=args.no_venv)
    if verb == "add-repo":
        return verbs.add_repo(project, args.url, branch=args.branch)
    if verb == "venv":
        return verbs.venv(project, recreate=args.recreate, bare=args.bare)
    if verb == "delete":
        return verbs.delete(project, args.name, force=args.force)
    if verb == "rebase-down":
        return verbs.rebase_down(project, passthrough, dry_run=args.dry_run)
    if verb == "merge-up":
        return verbs.merge_up(project, dry_run=args.dry_run, no_ff=args.no_ff)
    raise AssertionError(verb)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0 if argv else 2
    if argv[0] == "--version":
        ui.out(f"xwt {__version__}")
        return 0
    verb, rest = argv[0], argv[1:]
    if verb not in PASS_THROUGH and verb not in build_parsers():
        ui.error(f"unknown verb '{verb}' (see `xwt --help`)")
        return 2
    try:
        return dispatch(verb, rest)
    except (XwtError, ConfigError) as e:
        ui.error(str(e))
        return 1
    except KeyboardInterrupt:
        ui.error("interrupted")
        return 130

# xwt: named multi-repo git worktrees

xwt lets you work on several things at once in one project, each in its own
persistent, named directory, where a project may span several git repos.

- The **toplevel** is your main clone of the project's repo, plus plain clones
  of its **sub-repos** (zero or more), each on its integration branch.
- A **worktree** `NAME` is a sibling directory `../NAME` holding a git worktree
  of every one of those repos, all on a branch named `NAME`.
- xwt moves commits between a worktree's branch and the integration branches,
  in every repo at once (`rebase-down`, `merge-up`), and shows status and diffs
  across all repos.
- Optionally, each workspace gets its own environment: a generated `.envrc`
  (for [direnv](https://direnv.net/)) that activates a conda env, the
  workspace's own `.venv` with editable installs of the project's packages, or
  both.

Worktrees are meant to be long-lived slots (`dev`, `dev2`, `release`, ...), each
usually hosting one coding agent: a slot's branch repeatedly lands (merge up)
and resyncs (rebase down), and the slot stays.

xwt keeps no state of its own beyond `xwt.toml` and git itself. It never
fetches, pulls or pushes; you do that by hand.

## Contents

- [Install](#install)
- [Layout](#layout)
- [Quick start: one repo](#quick-start-one-repo)
- [Quick start: several repos](#quick-start-several-repos)
- [Commands](#commands)
- [Daily workflow](#daily-workflow)
- [xwt.toml reference](#xwttoml-reference)
- [Workspace environments](#workspace-environments)
- [Adding a sub-repo later](#adding-a-sub-repo-later)
- [Coding agents and sandboxes](#coding-agents-and-sandboxes)
- [Details](#details)

## Install

Requirements: Python 3.11 or later, git 2.31 or later. No Python dependencies.
Optional: `direnv` (xwt runs `direnv allow` when it writes a `.envrc`) and conda.

    pip install -e ~/git/xwt         # e.g. into your conda env
    xwt --help

Install it once, outside any workspace, so that every worktree runs the same
copy (and an agent working in a worktree can't edit it).

To run the tests: `cd ~/git/xwt && pip install -e '.[dev]' && pytest`.
They build everything in temporary directories and run pip offline.

## Layout

```
~/ch/                          group dir: any directory except $HOME itself
  top/                         toplevel: the main clone of the project's repo, with xwt.toml
    pipmake/ ksgpu/ pirate/    sub-repos: plain clones, ignored by the toplevel repo
    .envrc .venv/              this workspace's environment (optional)
  dev/                         worktree "dev": a git worktree of top, branch dev
    pipmake/ ksgpu/ pirate/    git worktrees of top/pipmake etc., also on branch dev
    .envrc .venv/              dev's own environment
  dev2/ ...                    more worktrees
  extern/                      anything else in the group dir is ignored by xwt
```

Terms used below:

- **Workspace**: the toplevel or a worktree. xwt commands act on the workspace
  you run them in (anywhere inside it).
- **Checkout**: one repo's working tree inside a workspace. The toplevel's are
  the **main checkouts**.
- **Integration branch**: each repo's shared branch, named in `xwt.toml`
  (e.g. `main` for the toplevel repo, `kms` for a sub-repo). Worktree branches
  are rebased onto it and merged into it. Each main checkout must have its
  integration branch checked out.

The worktree name, its directory name and its branch name are the same, in
every repo. So no command takes a branch argument: xwt infers it from where you
run it.

A worktree is any git worktree of the toplevel's repo whose directory is
directly in the group dir. Git worktrees elsewhere (for example Claude Code's
`.claude/worktrees/*`) are not xwt worktrees. Worktrees made by hand with
`git worktree add` work with xwt unchanged.

## Quick start: one repo

With no sub-repos, xwt is a named-worktree manager for a single repo. Say
`~/git/ms1`, `ms2` and `ms3` are three clones of one repo, on branches `claude`,
`gpt` and `main`. With xwt they become one toplevel and two worktrees, sharing
one `.git`:

    mkdir ~/git/ms && mv ~/git/ms3 ~/git/ms/top    # the toplevel can't sit directly in $HOME
    cd ~/git/ms/top
    xwt init                    # shows the xwt.toml it detected; asks before writing it
    git add xwt.toml && git commit -m "xwt.toml"   # optional for a personal project

    xwt new claude              # ~/git/ms/claude, on a new branch claude off main
    xwt new gpt
    cd ../claude                # ... work, commit ...
    xwt merge-up                # fast-forward main to claude
    cd ../gpt
    xwt rebase-down             # rebase gpt onto the new main
    xwt status                  # from the toplevel: every worktree's relation to main

(To keep the old branches, `git fetch ../../ms1 claude:claude` before
`xwt new claude`: xwt reuses an existing branch of that name.)

## Quick start: several repos

Adopting an existing layout: clone the project's repo as the toplevel, clone the
sub-repos into it by hand, then run `xwt init`, which finds them:

    mkdir ~/ch && cd ~/ch
    git clone github:kmsmith137/ch_top top
    cd top
    git clone -b main github:kmsmith137/pipmake
    git clone -b kms github:kmsmith137/ksgpu
    git clone -b kms github:kmsmith137/pirate
    xwt init --no-venv          # detects the three clones, their URLs and branches, and
                                # that they are Python packages, then asks before writing
                                # xwt.toml. Answer `e` to edit it first: put the sub-repos
                                # in dependency (install) order, uncomment what you need.
    xwt venv                    # build the toplevel's venv
    git add xwt.toml .gitignore && git commit -m "xwt"

    xwt new dev                 # ~/ch/dev: a worktree of every repo, branch dev, its own venv
    cd ../dev

Once `xwt.toml` is committed, a fresh clone on another machine needs only:

    mkdir ~/ch && cd ~/ch && git clone github:kmsmith137/ch_top top && cd top
    xwt init                    # clones the sub-repos on their branches, builds the venv

## Commands

```
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
```

Rules shared by all commands:

- **Check everything, then act.** `new`, `delete`, `rebase-down` and `merge-up`
  check every repo first and refuse, listing all the problems, before changing
  anything. (A rebase conflict can't be predicted; that is the one way a command
  stops part way.)
- **Integration branches are checked first.** Those four commands refuse if any
  main checkout isn't on its integration branch (`--force` doesn't change this).
  Suppose you check out an old branch in `top/ksgpu` to look at old code: if xwt
  simply used whatever was checked out, `merge-up` from `dev` would land `dev`'s
  commits on that old branch.
- Commands that change something print the command first
  (`xwt: $ git -C /home/me/ch/top/pirate worktree add ...`). Errors are
  `xwt: error: ...` with exit status 1.
- Output is grouped under per-repo headers such as
  `==================== dev/pirate ====================`; the toplevel repo's
  checkout, whose directory name usually differs from the repo's name, is
  labelled like `dev/ (repo ch_top)`.

### `xwt init [--no-venv]`

- **In a repo with no `xwt.toml`** (run it in the root of the main checkout):
  detects the current branch, the sub-repos (each subdirectory that is a git
  clone with an `origin`), and which of them are Python packages, and composes
  an `xwt.toml` with every other setting as a commented-out example. On a
  terminal it shows the file and asks `Write .../xwt.toml? [Y/n/e]` (`e` opens
  it in `$VISUAL` or `$EDITOR`); without a terminal it just writes it. Then it
  continues as below.
- **In the toplevel:** clones any sub-repo that is missing (on its `branch`, or
  the remote's default branch), warns about existing clones whose `origin` or
  branch doesn't match (it never switches branches), makes sure the toplevel's
  `.gitignore` ignores the paths xwt creates, initializes submodules, and
  prepares the environment (`.envrc`, `direnv allow`, the venv). If it created
  `xwt.toml` or added to `.gitignore`, it reminds you to commit them.
- **In a worktree:** adds a checkout of any sub-repo listed in `xwt.toml` but
  missing there, then prepares the environment.

`xwt init` is idempotent. After editing `xwt.toml`, re-run it (in each
workspace) to apply the change. It never removes anything: a sub-repo dropped
from `xwt.toml` stays on disk, and an existing venv is not recreated (use
`xwt venv --recreate`). `--no-venv` skips the venv build, the slow part.

### `xwt new NAME [--no-venv]`

Run anywhere in the project. Creates `<group>/NAME`: a git worktree of the
toplevel repo on a new branch `NAME` off its integration branch, and inside it a
git worktree of each sub-repo, also on `NAME`, off that sub-repo's integration
branch. Then submodules, `.envrc`, `direnv allow` and the venv. If a branch
`NAME` already exists in a repo (for example one kept by `xwt delete`), it is
checked out instead, and xwt says how it relates to the integration branch.

It refuses, before creating anything, if `NAME` isn't a valid name (letters,
digits, `.`, `_`, `-`, not starting with `.` or `-`), the directory exists, a
sub-repo isn't cloned in the toplevel, a main checkout isn't on its integration
branch, `NAME` is an integration branch, or branch `NAME` is checked out in
another worktree. If the venv build fails, the worktree is still complete:
`xwt venv` there retries.

### `xwt delete NAME [--force]`

Run from the toplevel or another worktree. Removes the worktree's git worktrees,
deletes branch `NAME` in each repo where it is merged into the integration
branch (with `git branch -d`), and removes what is left of the directory.

It refuses, listing every problem, if any checkout of the worktree has
uncommitted changes to tracked files, untracked files that aren't ignored,
commits on `NAME` not yet in the integration branch (run `xwt merge-up` first;
being behind is fine), a rebase, merge or cherry-pick in progress, a detached
HEAD whose commits no branch contains, or a submodule with commits on no remote
branch (a worktree's submodules keep their own store, which goes with it).
`--force` deletes anyway, losing uncommitted edits and untracked files, but an
unmerged branch is always kept (`kept branch dev in pirate: not merged into
kms; its commits are preserved`), so committed work survives.

It refuses even with `--force` if the worktree contains a git checkout that xwt
doesn't manage: another git worktree (such as Claude Code's
`.claude/worktrees/*`), or a separate clone (for example of a sub-repo since
dropped from `xwt.toml`). Remove or move those first.

**Ignored files are deleted with the worktree, without a question:** build
products, `.venv`, and anything else the repos ignore. For example ch_top
ignores `plans/`, so plans written in a worktree go with it. Move anything you
want to keep out of the worktree first. Symlinks inside the worktree are
removed; their targets are not touched.

If you already deleted the directory by hand, `xwt delete NAME` clears git's
record of it and deletes its merged branches.

### `xwt status [ARGS]` and `xwt diff [ARGS]`

`xwt status` prints the toplevel's path and integration branches, runs
`git status ARGS` in each checkout of the current workspace, then shows branch
relations and warnings:

```
toplevel /home/me/ch/top: ch_top main, pipmake main, ksgpu kms, pirate kms

==================== top/ (repo ch_top) ====================
On branch main
nothing to commit, working tree clean

==================== top/pipmake ====================
...

==================== branch relations ====================
dev/pirate is 2 commits ahead of top/pirate (kms branch)

dev2/ is 1 commit behind top/ (repo ch_top, main branch)
dev2/pirate is 3 commits behind top/pirate (kms branch)

dev3/* is up-to-date with top/* (all repos)


WARNING: test/ksgpu is on branch 'tmp', expected 'test'
```

From the toplevel, the relations and warnings cover every worktree; from a
worktree, only that worktree (and the toplevel). Warnings cover: a checkout on
the wrong branch or detached; an operation in progress; a sub-repo missing from
the toplevel or from a worktree; a directory where a sub-repo checkout should be
that isn't one; a path xwt manages that the toplevel repo doesn't ignore; a
worktree git still lists whose directory is gone; a missing or out-of-date
`.envrc` or a missing `.venv`; and a worktree whose own `xwt.toml` differs from
the toplevel's. The exit status is the worst git exit status, and at least 1 if
there are warnings.

`xwt diff ARGS` runs `git diff ARGS` in each checkout. Its exit status is the
worst git exit status (a plain `git diff` exits 0 even with changes; pass
`--exit-code` for a non-zero status).

## Daily workflow

**Look.** `xwt status` in the toplevel is the overview of all worktrees.
Commit in each repo of a worktree as usual: a commit in a worktree is
immediately part of that repo's shared history.

**Rebase then fast-forward.** History stays linear and feature commits land
individually. Both commands run in the worktree, skip repos that need nothing,
and skip (with a warning) a checkout that isn't on the worktree's branch.

    cd ~/ch/dev
    xwt rebase-down          # sync down: rebase dev onto each integration branch
    xwt merge-up             # land up: fast-forward each integration branch to dev

`rebase-down` rebases only the repos that are behind; extra arguments after `--`
go to `git rebase` (`xwt rebase-down -- -i` works: git's output and editor reach
the terminal). It refuses if a checkout to be rebased has uncommitted changes to
tracked files, untracked files the rebase would overwrite, or a rebase or merge
already in progress.

`merge-up` runs `git merge --ff-only dev` in each main checkout, where the
integration branch is checked out. It refuses, merging nothing, if any repo
needs a rebase first (`pirate: kms has 2 commits not in dev; run
xwt rebase-down first`), or if a file the merge changes has uncommitted changes
or an untracked copy in the main checkout. `--no-ff` makes a merge commit in
each repo instead (and needs a clean index in each main checkout). Both commands take `--dry-run` to print what they would run.

Landing doesn't delete the worktree; the slot stays, and its branch keeps going.

### Conflicts during rebase-down

A rebase replays the worktree's commits one at a time onto the integration
branch, so a conflict stops at the first offending commit (you may hit several
in turn, one resolution each, unlike a merge's single combined resolution).
`xwt rebase-down` doesn't resolve anything: it shows git's conflict message,
leaves that repo in the middle of its rebase, carries on with the other repos
(it doesn't roll back the ones that rebased cleanly), and exits 1, naming the
repos to finish by hand. Finish with plain git, in the repo it stopped in:

    cd ~/ch/dev/pirate                # the repo named in the error
    # edit the conflicted files (look for <<<<<<< markers), then:
    git add <resolved-files>
    git rebase --continue             # replays the next commit; repeat if it conflicts
    # or, to bail out completely (back to the state before the rebase):
    git rebase --abort

Don't re-run `xwt rebase-down` to resume: it refuses while a rebase is in
progress. Use `git rebase --continue` or `--abort` directly. `git rebase --abort`
is always safe: a rebase is fully undoable until it finishes, so it is fine to
start one just to see the conflicts.

The same holds for `xwt merge-up --no-ff`, whose merges can conflict: that repo
is left mid-merge (finish with `git commit` or `git merge --abort` in the main
checkout), the others go ahead, and xwt exits 1.

## xwt.toml reference

`xwt.toml` sits at the root of the toplevel. **Only the toplevel's copy is
read.** If the file is tracked, each worktree has its own copy, which xwt
ignores: a change made in a worktree takes effect when it is merged up (and
`xwt status` warns while the two differ). Commit `xwt.toml` in projects that are
shared or used on several machines; for a personal one-repo project it can
equally stay untracked.

Every key is optional, and an empty file is valid. Unknown keys are errors.

```toml
# The toplevel repo's own integration branch. Must come before the first table.
branch = "main"

# Sub-repos, cloned into the toplevel and added to every worktree, in this
# order (which is also the install order). The table name is the directory
# name: letters, digits, ".", "_", "-", not starting with ".".
[repo.pipmake]
url = "github:kmsmith137/pipmake"   # anything `git clone` accepts
branch = "main"                     # its integration branch

[repo.ksgpu]
url = "github:kmsmith137/ksgpu"
branch = "kms"

# The workspace environment. With [envrc] or any of its sub-tables, each
# workspace gets a generated .envrc.
[envrc]
conda = "dev"                       # conda env (name or prefix) to activate;
                                    # leave out to use whatever env the shell has active

[envrc.export]                      # extra exports, in order
PYTHONSAFEPATH = "1"

# A per-workspace venv, built by xwt and activated by .envrc.
[envrc.venv]
install = ["editables"]             # plain pip installs, when the venv is created
editable = ["pipmake", "ksgpu"]     # pip install -e, in this order; "." is the toplevel's
                                    # own repo, other entries are [repo] names
pip_args = ["--no-build-isolation"] # extra arguments for the editable installs
```

**Integration branches.** `branch` names a repo's integration branch: the
top-level key for the toplevel's own repo, and `branch` in each `[repo.*]`
table. Each main checkout must be on its integration branch: `new`, `delete`,
`rebase-down` and `merge-up` refuse otherwise, and `status` and `init` warn. A
repo without `branch` uses whatever its main checkout has checked out (it must
not be detached). When cloning a sub-repo, `xwt init` checks out its `branch`
(default: the remote's default branch).

To retarget a repo (say ksgpu from `kms` to `main`), change `branch` in
`xwt.toml` and `git -C ~/ch/top/ksgpu checkout main`, in either order. Existing
worktrees keep their branches; their next `xwt rebase-down` rebases onto the
new integration branch.

**What xwt writes into the project.** Besides `xwt.toml`, xwt appends lines to
the toplevel's `.gitignore` (under a `# added by xwt` comment, only for paths
not already ignored): `/NAME/` for each sub-repo, and with `[envrc]`, `/.envrc`
and `/.direnv/`, plus `/.venv/` with `[envrc.venv]`. In workspaces it writes
only `.envrc` (and the `.venv` it builds).

## Workspace environments

Without `[envrc]` (or its sub-tables), xwt never touches `.envrc` or `.venv`, so
a project may track its own `.envrc`. With `[envrc]`, a tracked `.envrc` is an
error ("remove one of them").

The two parts work independently:

- **Conda env only:** `[envrc]` with `conda`, and no `[envrc.venv]`.
- **Venv only:** `[envrc.venv]` on its own. The venv is built over whatever env
  the shell has active.
- **Both** (as in ch_top): the conda env supplies the toolchain, and the
  workspace's venv (created with `--system-site-packages`) holds editable
  installs of this workspace's checkouts.

### The generated `.envrc`

`xwt init` and `xwt new` write it at the root of each workspace, then run
`direnv allow` there (direnv blocks a changed `.envrc` until it is allowed
again). For example:

```bash
# Generated by xwt from [envrc] in /home/me/ch/top/xwt.toml.
# Don't edit: change xwt.toml, then run `xwt init` here to rewrite this file.
eval "$(conda shell.bash hook)"
conda activate dev
export VIRTUAL_ENV="$PWD/.venv"
export PATH="$PWD/.venv/bin:$PATH"
export PYTHONSAFEPATH="1"
```

It is plain bash (no direnv functions), so it also works without direnv:
`source .envrc`. Export values are written inside double quotes, so `$NAME`
expands when the file loads (`$PWD` is the workspace root), and they come after
the venv lines, so they can use `$VIRTUAL_ENV`. It is rewritten only when its
content changes. The conda lines need `conda` on `PATH`.

### The venv

The venv steps (the venv build of `init` and `new`, and `xwt venv`) run in the
**workspace environment**: xwt's own environment with the workspace's `.envrc`
sourced, and any venv active in xwt's environment taken off `PATH` first (as
direnv does when you `cd` from one workspace into another). So `xwt new dev`
run from the toplevel, where the toplevel's venv is active, builds `dev`'s venv
with `dev`'s pip. If sourcing `.envrc` fails (say, the conda env doesn't exist),
xwt stops.

The build:

1. With `--recreate`: remove `.venv`.
2. If `.venv` doesn't exist: create it from the **base** interpreter
   (`<sys.base_prefix>/bin/python3`, so another workspace's venv is never the
   seed) with `python3 -m venv --system-site-packages .venv`.
3. Only when step 2 created the venv: `pip install --ignore-installed` the
   `install` entries. This is the only step that normally needs the network, so
   rebuilding an existing venv works offline (for example inside a sandbox whose
   proxy blocks PyPI). `--ignore-installed` puts a copy in the venv even when the
   conda env has one, which matters for `editables`: an editable install's
   `.pth` file imports it at interpreter startup, before the conda env's
   site-packages are on `sys.path`.
4. For each `editable` entry, in order: `pip install PIP_ARGS -e .` in that
   checkout. This runs every time, so re-running refreshes compiled extensions
   (after a rebase, say).

`xwt venv --bare` does steps 1 and 2 only: an empty venv, for example to test
that an sdist installs into a clean environment. `xwt venv` needs the
workspace's `.envrc` (run `xwt init` there first) and doesn't rewrite it. If a
step fails, xwt reports its exit status and exits 1; re-running is safe (if
`install` failed, use `xwt venv --recreate`).

## Adding a sub-repo later

    xwt add-repo github:kmsmith137/chordutils [--branch B]

clones it into the toplevel (or records an existing clone there whose `origin`
matches), appends a `[repo.chordutils]` table to `xwt.toml` with the clone's
current branch as its integration branch (leaving the rest of the file and its
comments alone), adds `/chordutils/` to `.gitignore`, and prints the next steps:
adding it to `editable` if it is a Python package, committing `xwt.toml` and
`.gitignore`, and running `xwt init` in each worktree.

Existing worktrees get the new checkout when you run `xwt init` in them, one at
a time, when you choose (a venv rebuild takes a while and may disturb an agent
working there). Until then, every command in such a worktree reminds you:

```
xwt: warning: dev has no checkout of chordutils (added to xwt.toml after dev
     was created); run `xwt init` in /home/me/ch/dev to add it
```

and works on the checkouts that exist.

## Coding agents and sandboxes

xwt treats agents and people alike, sandboxed or not. Which commands agents may
run is a project instruction; for example, in `CLAUDE.md` or `AGENTS.md`:

> Don't run the xwt commands that change branches, worktrees or clones
> (`xwt merge-up`, `rebase-down`, `new`, `delete`, `init`, `add-repo`); the user
> does those. `xwt status`, `xwt diff` and `xwt venv` are fine.

With xsb (a sandbox launcher for coding agents), xwt needs nothing special:

- **Activate, then launch:** `cd` into a workspace (direnv loads its `.envrc`),
  then `xsb claude`. The sandbox inherits that environment.
- **Rebuilding in the sandbox:** an agent runs `xwt venv` after changing compiled
  code. With the venv already created, it needs no network.
- **Commits from worktrees** work in a sandbox, since xsb mounts each repo's
  shared `.git` store read-write even though it lives in the toplevel.
- Either make the group dir the xsb root (every agent sees the whole group; the
  toplevel is read-only, so `merge-up` can't run in a sandbox anyway), or commit
  an `xsb.toml` in the toplevel repo so that each worktree is its own root.

## Details

- **Finding the workspace.** xwt walks up from the current directory (to `$HOME`,
  or to `/` outside `$HOME`) to the first git checkout whose repo's main
  checkout has `xwt.toml` and is either that checkout or a sibling of it. So
  from `~/ch/dev/pirate/src` it finds the worktree `~/ch/dev`; a submodule, or a
  sub-repo that happens to have its own `xwt.toml`, doesn't capture it (in the
  toplevel too, as long as the toplevel's `xwt.toml` lists it). Nested
  toplevels aren't supported.
- **Sub-repos are plain clones**, not git submodules, one directory each,
  directly in the toplevel. Submodules inside any repo are initialized in every
  new checkout.
- **xwt never runs `git branch -D`**, and never fetches, pulls or pushes. Its only
  network operations are cloning a missing sub-repo, initializing submodules,
  and the venv's `install` step.
- **Worktrees are always siblings** of the toplevel in the group dir, which is
  why the toplevel can't sit directly in `$HOME`.
- There are no environment-variable settings and no user-level config file.

# Plan: `xwt`, named multi-repo git worktrees

Handoff plan for an implementation agent with no prior context. It describes a
new Python package, `xwt`, that replaces the worktree and venv scripts in the
`ch_top` repo (`init-toplevel`, `init-worktree`, `init-venv`,
`delete-worktree`, `git-status`, `git-diff`, `git-rebase-down`,
`git-merge-up`) and can be used by any git project.

§13 lists the decisions already made (all design questions have been settled
with the user), things to check during implementation, and deferred work.

**Getting started.**

- **The repo** is `~/git/xwt`, a git repo with no commits yet; this plan is
  `plans/xwt.md` in it.
- **Python:** the conda `dev` env (`~/miniforge3/envs/dev`, Python 3.13) has
  pytest and setuptools. xwt needs Python ≥ 3.11 (`tomllib`) and git ≥ 2.31;
  the host has git 2.43. No runtime dependencies. Don't install software
  yourself; ask the user if something is missing.
- **Tests** use only temporary directories and local repos, and run pip
  offline: no network, no podman. They run sandboxed or not.
- **Don't touch `~/ch`**, the user's real workspace: never run xwt there, and
  don't modify anything in it. You may read its scripts (listed in §2).
- **Commits:** commit checkpoints as you go, at least one per phase (§11) with
  its tests passing, the message naming the phase. The first commit can include
  this plan. Never push.
- **At the end,** write `plans/xwt_report.md`: what was built, what was
  verified, and any departures from this plan, with reasons.

## 1. Goal

Work on several things at once in one project, each in its own persistent,
named directory, where a project may span several git repos. xwt manages:

- the **toplevel**: the main clone of a project's repo, plus plain clones of
  its **sub-repos** (zero or more), each on its integration branch;
- **worktrees**: sibling directories `../NAME`, each holding a git worktree of
  every one of those repos, all on a branch named `NAME`;
- moving commits between a worktree's branch and the integration branches, in
  every repo at once (rebase down, fast-forward up), plus status and diff
  across all repos;
- optionally, an **environment per workspace** (§6): a generated `.envrc`
  for direnv that activates a conda env, the workspace's own `.venv` (which
  holds editable installs of the project's packages), or both.

**How it is used in practice** (ch_top today). Worktrees are long-lived slots,
not one-per-feature: `~/ch` has `dev`, `dev2`, `dev3`, `dev4`, `release` and
`test`, each usually hosting one sandboxed LLM agent. A slot's branch
repeatedly lands (merge up) and resyncs (rebase down) and the slot stays.
Creating and deleting worktrees is rare; the daily verbs are status, rebase
down and merge up. Status from the toplevel is the overview of all slots. The
user, not the agents, moves commits between branches. Each slot has its own
venv with editable, compiled installs, activated by direnv when you `cd` in;
agents rebuild it after changing compiled code.

**Any git repo** becomes an xwt toplevel by adding `xwt.toml`; `xwt init`
writes one, filled in from what it finds (§4.4). With no sub-repos (N=0), xwt
is a named-worktree manager for one repo. Example: `~/git/ms1`, `ms2` and
`ms3` are today three separate clones of one repo, on branches `claude`, `gpt`
and `main`. With xwt they would be the toplevel `~/git/ms/top` (on `main`)
plus worktrees `~/git/ms/claude` and `~/git/ms/gpt`, sharing one `.git`.

xwt knows nothing about ch_top or sandboxes. It is independent of `xsb`, the
user's sandbox launcher (§10 describes how they fit together). Non-goals:
fetching, pulling and pushing (the user does these by hand); non-Python setup
steps (e.g. `npm install`); sandboxing.

Platforms: anything with git and Python. Tested on Linux.

## 2. Background: what exists today

In `ch_top` (the container repo at `~/ch/top`):

| File | Role |
|---|---|
| `git_repositories.toml` | the sub-repos: `[pipmake]`, `[ksgpu]`, `[pirate]`, each with `url` and `branch` |
| `init-toplevel` | clone missing sub-repos and check out their branch, init submodules, render dotfiles, build the toplevel's `.venv` |
| `init-worktree NAME [--no-venv \| -r]` | create `../NAME` (a git worktree of top and of each sub-repo, branch `NAME`), init submodules, render dotfiles, build the venv (`-r`: a bare venv, for release testing) |
| `init-venv [DIR] [--recreate] [--test] [-r]` | (re)build a workspace's `.venv`: a venv over the conda env, `editables`, editable installs of pipmake, ksgpu, pirate, a smoke-test import |
| `delete-worktree NAME [--force]` | safety checks, remove the git worktrees, `git branch -d`, remove the directory |
| `git-status`, `git-diff` | run `git status` / `git diff` in every repo of the current workspace under headers; `git-status` adds branch relations and wrong-branch warnings |
| `git-rebase-down` | in a worktree: rebase each repo's branch onto its integration branch; then re-render the dotfiles |
| `git-merge-up` | in a worktree: fast-forward each repo's integration branch to the worktree's branch |
| `ch_top_helpers.py` | shared code for all of the above, including `build_venv` |
| `dotfile_templates/` | `envrc.tmpl` (venv activation for direnv) and `claude-env.sh.tmpl` (venv activation for Claude Code on the host) |
| `plans/branch-overrides.md` | an unimplemented plan for per-clone branch overrides; not needed with §4.3 |

Read `ch_top_helpers.py` (especially `build_venv` and its comments) and the
scripts before starting; most of the logic carries over. ch_top's
`README.md` ("Layout", "Scripts", "Daily workflow", Appendices A and B)
documents the workflow from the user's side.

### 2.1 Behaviors to preserve (with reasons)

Worktrees and branches:

- **Sibling layout in a group dir.** Worktrees are created next to the
  toplevel, so the toplevel must not sit directly in `$HOME` (the worktrees
  would land in `$HOME`).
- **One branch name per worktree, equal to the directory name,** in every repo.
  Commands infer the branch from what is checked out, so none takes a branch
  argument, and status can flag a checkout that is on the wrong branch.
- **A known integration branch per repo** (the manifest's `branch`), checked
  out in the toplevel's checkout of that repo; `git-status` warns when one
  isn't.
- **Rebase then fast-forward.** History stays linear and feature commits land
  individually. Merge-up defaults to `--ff-only` and offers `--no-ff`; it runs
  in the toplevel's checkout, where the integration branch is checked out.
- **Skip repos that need nothing**: rebase-down skips repos that aren't
  behind, merge-up skips repos with nothing to land, and both skip (with a
  warning) a checkout that isn't on the worktree's branch (e.g. detached).
- **Rebase conflicts stop in that repo.** The repo is left mid-rebase, the
  command exits non-zero, other repos are not rolled back, and the user finishes
  with `git rebase --continue` or `--abort` (not by re-running).
- **Delete is safe by default.** It refuses if any checkout has uncommitted
  changes to tracked files, untracked files that aren't ignored, or commits not
  yet merged up (being behind is fine). Branches are deleted with
  `git branch -d`, never `-D`, so unmerged work survives even `--force`.
  Ignored files (build products, `.venv`, ...) are deleted silently with the
  worktree.
- **Submodules** are initialized in every new checkout (pirate has one).
- **Init is idempotent.** Existing clones are left alone; a branch mismatch is
  reported, not changed.
- **Output.** Per-repo headers like `==================== dev/pirate
  ====================`; the toplevel repo, whose directory name differs from
  its repo name, is labelled `dev/ (repo ch_top)`. Color when stdout is a
  terminal. Extra arguments pass through to git. Commands that change things
  are echoed before they run.
- **Branch relations** like `dev/pirate is 2 commits ahead of top/pirate (kms
  branch)`. Up-to-date lines are dropped; a fully up-to-date worktree collapses
  to one line; one group per worktree, separated by blank lines. From the
  toplevel: every worktree. From a worktree: only itself.

Python environments (from `build_venv`):

- **A venv per workspace, layered over the conda env**
  (`--system-site-packages`): conda supplies the compiled toolchain (cupy,
  nvcc, ...), the venv holds editable installs of this workspace's checkouts.
- **Created from the base interpreter**, never from another workspace's venv,
  which would chain its editable installs in.
- **Editable installs in dependency order** (pipmake, the build backend, before
  ksgpu and pirate), with `--no-build-isolation` so the build uses the conda
  toolchain and the venv's pipmake.
- **`editables` installed into the venv itself**: an editable install's `.pth`
  file imports it at interpreter startup, before the conda env's
  site-packages are on `sys.path`, so a conda-only copy is silently missed.
- **Rebuilding an existing venv needs no network.** ch_top's ch-test skill (a
  full test sweep run by an agent) rebuilds inside the sandbox, whose proxy
  blocks PyPI; installing `editables` is the only step that downloads, so it
  happens only when the venv is created.
- **A bare venv** (no installs) for release testing, where sdists must install
  into an empty venv.
- **direnv activation**: `.venv/bin` first on `PATH`, `VIRTUAL_ENV`, and
  ch's extra export `PYTHONSAFEPATH=1`.
- **The venv build can be skipped** (today `--no-venv`), since it takes
  minutes.

### 2.2 Problems to fix

1. **The scripts live in the repo, so every worktree has its own writable
   copy,** and the user runs them on the host from the worktree
   (`cd ../dev && ./git-rebase-down` runs `dev`'s copy, which a sandboxed agent
   can edit). An installed package fixes this.
2. **ch-specific code**: the container repo's branch is hardcoded as `main`;
   the venv recipe is hardcoded, with a `BUILD` list kept separately from the
   manifest ("add a repo in two places"); a hint about the sandbox's Claude
   login; the `[ch_top]` message prefix.
3. **Two sources for the integration branch**: the manifest's `branch` (used
   when cloning, when creating worktrees, and for warnings) and the checked-out
   branch (used everywhere else). They can disagree.
4. **Half-done operations.** `init-worktree` creates the repos one at a time,
   so a failure in the third (its branch already exists, or a stale worktree
   entry) leaves a half-made worktree. `git-merge-up` can land some repos and
   then fail in another.
5. **`git-rebase-down -- -i` can't work**: git's output is captured to print it
   under headers, so the editor never reaches the terminal.
6. **Generated dotfiles must be re-rendered after every rebase,** because they
   are untracked copies of templates that change with the branch.

## 3. Terminology and layout

- **Toplevel**: a repo's main checkout (its `.git` is a directory) with
  `xwt.toml` at its root. Example: `~/ch/top`.
- **Group dir**: the toplevel's parent. Worktrees are created in it, as
  siblings of the toplevel. Example: `~/ch`. Other directories in it (e.g.
  `~/ch/extern`) are ignored.
- **Sub-repo**: a repo listed in `xwt.toml`, plain-cloned (not a submodule)
  into a directory of the same name in the toplevel. ch_top has three.
- **Worktree**: a directory `<group>/NAME` holding a git worktree of the
  toplevel's repo and, inside it at the same relative paths, a git worktree of
  each sub-repo, all on branch `NAME`. "Git worktree" always means git's
  single-repo notion.
- **Workspace**: the toplevel or a worktree; where a command is run.
- **Checkout**: one repo's working tree inside a workspace. The toplevel's
  checkouts are the **main checkouts**.
- **Integration branch** of a repo: the shared branch that worktree branches
  are rebased onto and merged into. It is named in `xwt.toml`, and the repo's
  main checkout must have it checked out (§4.3). In ch_top, `main` for ch_top
  and pipmake, `kms` for ksgpu and pirate.

```
~/ch/                          group dir
  top/                         toplevel: main checkout of ch_top, has xwt.toml
    pipmake/ ksgpu/ pirate/    sub-repos: plain clones, ignored by ch_top
    .envrc .venv/              generated by xwt from [envrc] (§6)
  dev/                         worktree "dev": git worktree of top, branch dev
    pipmake/ ksgpu/ pirate/    git worktrees of top/pipmake etc., branch dev
    .envrc .venv/              dev's own environment
  dev2/ ...                    more worktrees
  extern/                      not a worktree; xwt ignores it
```

xwt keeps no state of its own. A worktree is any git worktree of the
toplevel's repo whose directory is directly in the group dir. Git worktrees of
the toplevel elsewhere (e.g. Claude Code's `.claude/worktrees/*`) are ignored.
So ch's existing worktrees work with xwt unchanged.

## 4. Configuration: `xwt.toml`

### 4.1 Finding the toplevel and the current workspace

Walk up from the current directory to `$HOME` (inclusive), or to `/` if the
current directory is not under `$HOME`. At each directory `D` that contains
`.git` (a file or a directory):

1. `M` = the main checkout of `D`'s repo: the parent of
   `git -C D rev-parse --path-format=absolute --git-common-dir`, if that
   path ends in `/.git`. Otherwise (a bare repo, a submodule's store) skip `D`.
2. If `M/xwt.toml` exists, and `D` is `M` itself or a directory directly in
   `M`'s parent, then `M` is the toplevel and `D` is the current workspace.
   Stop.
3. Otherwise keep walking up.

If nothing matches: "not inside an xwt toplevel or worktree; run `xwt init` in
a repo's main checkout to make it one". Examples: in `~/ch/dev/pirate/src`, the
walk passes `~/ch/dev/pirate` (main checkout `~/ch/top/pirate`, no
`xwt.toml`) and stops at `~/ch/dev` (main checkout `~/ch/top`). In a
submodule, step 1 skips it. The sibling test in step 2 means a sub-repo that
happens to have its own `xwt.toml` doesn't capture the enclosing workspace.

**The toplevel's `xwt.toml` is the only one read.** A worktree's own copy (if
the file is tracked) is ignored; a change to it takes effect when it reaches
the toplevel (merged up). `xwt status` says so when the two differ.

### 4.2 Schema

TOML, parsed with `tomllib`. Every key is optional; an empty file is valid.
Unknown keys are an error.

```toml
# The toplevel repo's own integration branch (§4.3).
branch = "main"

# Sub-repos, cloned into the toplevel and added to every worktree, in this
# order. The table name is the directory name (letters, digits, ".", "_",
# "-"; no "/").
[repo.ksgpu]
url = "github:kmsmith137/ksgpu"   # anything `git clone` accepts
branch = "kms"                    # its integration branch (§4.3)

# The workspace environment (§6). With [envrc] or any of its sub-tables
# present, each workspace gets a generated .envrc (for direnv).
[envrc]
conda = "dev"                     # conda env (name or prefix) for .envrc to activate;
                                  # omit to use whatever env the shell has active

[envrc.export]                    # extra exports in .envrc (§6.1)
PYTHONSAFEPATH = "1"

# A per-workspace Python venv, built by xwt and activated by .envrc.
[envrc.venv]
install = ["editables"]           # plain pip installs into the venv, when it is created
editable = ["pipmake", "ksgpu", "pirate"]   # pip install -e, in this order;
                                  # "." is the toplevel's own repo, other entries are [repo] names
pip_args = ["--no-build-isolation"]         # extra arguments for the editable installs
```

`branch` must come before the first table in the file (a TOML rule).

The two parts work independently:

- **Conda env only:** `[envrc]` with `conda`, and no `[envrc.venv]`.
- **Venv only:** `[envrc.venv]` on its own (TOML allows a sub-table without
  its parent's header). The venv is built over whatever env the shell has
  active, and `.envrc` is still written, to activate it. An empty
  `[envrc.venv]` is a venv with nothing installed.

Validation: each `[repo.*]` table needs `url`; `editable` entries are `.` or
names of `[repo]` tables; `[envrc.export]` keys are shell variable names; all
values are strings or lists of strings as shown.

### 4.3 Integration branches

Each repo's integration branch is named in `xwt.toml`: `branch` in its
`[repo.*]` table for a sub-repo, and the top-level `branch` key for the
toplevel's own repo. The toplevel's checkout of each repo must have that
branch checked out.

- **Commands check before acting.** `new`, `delete`, `rebase-down` and
  `merge-up` first check every main checkout and refuse if one is on another
  branch or detached: "top/ksgpu is on old-experiment, but xwt.toml says kms:
  check out kms there, or change xwt.toml". `--force` doesn't skip this.
  `status` and `init` report the same mismatch as a warning.
- **Why**: suppose an unsandboxed agent in the toplevel checks out an old
  branch in `top/ksgpu` to look at old code. If xwt simply used whatever was
  checked out, `merge-up` from `dev` could fast-forward that old branch to
  `dev` and report success, landing the commits on the wrong branch;
  `rebase-down` would rebase onto it. The check turns that into a refusal.
- **A repo without `branch`** uses whatever its main checkout has checked out
  (it must be on a branch, not detached). This keeps a comments-only
  `xwt.toml` working for a single-repo project.
- **When cloning** a sub-repo, `xwt init` checks out its `branch` (default:
  the remote's default branch).
- **Retargeting** a repo (say ksgpu from `kms` to `main`) takes two steps, in
  either order: change `branch` in `xwt.toml` (normally a commit to the
  project), and `git -C ~/ch/top/ksgpu checkout main`. Existing worktrees keep
  their branches; their next `xwt rebase-down` rebases onto the new
  integration branch.

There is no per-clone override file. Retargeting a single clone means an
uncommitted edit to `xwt.toml` in that clone; that is expected to be rare.

`xwt status` lists the integration branches in its header.

### 4.4 As written by `xwt init`

When `xwt init` creates `xwt.toml` (§7.3), it fills it in from what it finds,
so most projects need no editing:

- **The toplevel repo's `branch`**: its current branch (left out if
  detached).
- **Sub-repos**: each immediate subdirectory of the toplevel whose `.git` is a
  directory (a clone, not a submodule), in name order. `url` is its `origin`
  remote (a clone without `origin` is skipped, with a warning); `branch` is its
  current branch (left out if detached). So adopting an existing layout is:
  clone the sub-repos by hand, then `xwt init`.
- **Environment**: the Python packages are `.` if the toplevel has a
  `pyproject.toml` or `setup.py`, then each detected sub-repo that has one.
  If there are any, `[envrc]` (with `conda` set to the active env,
  `$CONDA_DEFAULT_ENV`, left out if unset) and `[envrc.venv]` (with
  `editable` listing the packages) are written switched on. If there are none
  (e.g. a LaTeX repo, or loose scripts), both are written commented out, with
  the conda env filled in: nothing is generated (no `.envrc`, no venv, no
  `direnv allow`) until you uncomment them and re-run `xwt init`. Switching
  them on by default there would mostly duplicate the conda env the shell
  already activates.
- Every other setting appears as a commented-out example, so the file
  documents itself.

Detection happens only when the file is created. After that the file is the
user's: xwt never rewrites it (`xwt add-repo` only appends, §7.5). To change a
setting, edit the file and re-run `xwt init`.

Example: adopting `~/ch/top` as it is today (three sub-repos, each a Python
package; none at the top):

```toml
# xwt config for the toplevel /home/kmsmith/ch/top: `xwt new NAME` creates the
# worktree ../NAME, on a new branch NAME in every repo. Written by `xwt init`
# from what it found; edit freely. Commented-out settings are examples.

# Integration branches: this repo's here, each sub-repo's in its table. Each
# repo's checkout in this directory must be on its branch, or the commands
# that move commits refuse.
branch = "main"

# Sub-repos: cloned into the toplevel and added to every worktree. This is
# also the install order: put dependencies first. `xwt add-repo URL` adds one.
[repo.ksgpu]
url = "github:kmsmith137/ksgpu"
branch = "kms"

[repo.pipmake]
url = "github:kmsmith137/pipmake"
branch = "main"

[repo.pirate]
url = "github:kmsmith137/pirate"
branch = "kms"

# Environment: a generated .envrc (for direnv) activates the conda env and
# this workspace's own .venv, which gets editable installs of these, in order.
[envrc]
conda = "dev"

# [envrc.export]                       # extra exports in .envrc
# PYTHONSAFEPATH = "1"

[envrc.venv]
editable = ["ksgpu", "pipmake", "pirate"]
# install = ["editables"]              # plain pip installs, when the venv is created
# pip_args = ["--no-build-isolation"]  # extra arguments for the editable installs
```

Detection can't know dependency order: here pipmake has to be moved first,
which is why `xwt init` prints "check the sub-repo order: it is the install
order" whenever it detected more than one sub-repo.

**Commit `xwt.toml`** in projects that are shared or used on several
machines: a fresh clone then needs only `xwt init`, with no flags and no
editing. `conda` names an env, which must exist under that name on each
machine; leave it out to use whatever env is active. For a personal N=0
project the file can equally stay uncommitted; xwt reads only the toplevel's
copy either way.

## 5. Command-line interface

One executable, `xwt`, with flat verbs:

```
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
```

`--no-venv` skips the venv build, the slow part (checkouts, `.envrc` and
`direnv allow` still happen). `ARGS` after `--` go to `git rebase`
(`rebase-down`). `xwt`, `xwt -h` and `xwt --help` print the usage; `xwt
--version` prints the version. There are no environment-variable settings, no
user-level config file, and no `~/.xwt`.

## 6. Workspace environments (`.envrc` and `.venv`)

Everything in this section applies only when `xwt.toml` has `[envrc]` or one
of its sub-tables. Without them, xwt never touches `.envrc` or `.venv` (a
project may track its own `.envrc`). The venv parts apply only with
`[envrc.venv]`.

### 6.1 The generated `.envrc`

Written at the root of each workspace by `xwt init` and `xwt new` (§7.2), for
direnv. Example:

```bash
# Generated by xwt from [envrc] in /home/kmsmith/ch/top/xwt.toml.
# Don't edit: change xwt.toml, then run `xwt init` here to rewrite this file.
eval "$(conda shell.bash hook)"
conda activate dev
export VIRTUAL_ENV="$PWD/.venv"
export PATH="$PWD/.venv/bin:$PATH"
export PYTHONSAFEPATH="1"
```

- The order is: conda, then the venv, then the exports, so an export can use
  `$VIRTUAL_ENV`. The two conda lines appear only when `conda` is set; the two
  venv lines only with `[envrc.venv]`.
- `[envrc.export]` entries are written in order, as `export NAME="VALUE"`.
  Values are written inside double quotes, so `$NAME` expands when the file
  loads (`$PWD` is the workspace root, both under direnv and when xwt sources
  it). A `"`, `\` or backtick in a value is escaped.
- Plain bash, no direnv functions, so xwt can source it itself (§6.2) and it
  works without direnv.
- Written only when its content changes. If the toplevel's repo tracks
  `.envrc`, refuse: "xwt.toml has [envrc], but .envrc is tracked in the
  repo; remove one of them".
- `/.envrc` and `/.direnv/` (and `/.venv/`, with `[envrc.venv]`) are added
  to the toplevel's `.gitignore` if not already ignored (§7.1).
- After writing it, xwt runs `direnv allow` (§7.2), since direnv blocks a
  changed `.envrc` until it is allowed again.

### 6.2 The workspace environment

Environment steps (creating the venv, running pip) run in the
**workspace environment**: xwt's own environment with the workspace's `.envrc`
applied, i.e. through
`bash -c 'cd "$1" && source ./.envrc && shift && exec "$@"' xwt WORKSPACE CMD...`.
If xwt's own environment has a venv active (`VIRTUAL_ENV` set), its `bin` is
removed from `PATH` and `VIRTUAL_ENV` is unset first, as direnv would when you
`cd` from one workspace into another.

The reason: `xwt new dev` is usually run from the toplevel, where direnv has
put the toplevel's venv on `PATH`. Without this, pip and python would be the
toplevel's.

### 6.3 Building the venv

With `[envrc.venv]`: the procedure behind `xwt venv` (§7.6) and the venv
step of `init` and `new`, in the workspace environment:

1. With `--recreate`: remove `.venv`.
2. If `.venv` doesn't exist: find the base interpreter (run
   `python3 -c 'import sys; print(sys.base_prefix)'` and take
   `<base_prefix>/bin/python3`, so that a venv still on `PATH` is never the
   seed), then `<base> -m venv --system-site-packages .venv`.
3. If step 2 created the venv and `install` isn't empty: `.venv/bin/pip
   install --ignore-installed INSTALL...`, one call with every `install`
   entry. `--ignore-installed` puts a copy in the venv even when the conda env
   has one (`editables`, §2.1). This is the only step that normally needs the
   network, so rebuilding an existing venv stays offline.
4. For each `editable` entry, in order: `.venv/bin/pip install PIP_ARGS -e .`,
   run in that entry's directory (`<workspace>/<entry>`). This runs every time:
   re-running refreshes compiled extensions, e.g. after a rebase.

With `--bare`, only steps 1 and 2 (an empty venv, for release testing). If a
step fails, xwt reports the exit status and exits 1; what was created stays,
and re-running is safe.

## 7. Verbs

### 7.1 Rules shared by all verbs

- **Check everything, then act.** `new`, `delete`, `rebase-down` and
  `merge-up` check every repo first and refuse, listing all problems, before
  changing anything. A refusal never leaves the repos half done. (A rebase
  conflict can't be predicted; that is the one way a verb stops part way.)
- **Integration branches are checked first** (§4.3): `new`, `delete`,
  `rebase-down` and `merge-up` refuse if any main checkout isn't on its
  integration branch.
- **Sub-repos missing from a worktree.** A worktree created before a sub-repo
  was added to `xwt.toml` has no checkout of it until the user runs `xwt init`
  there (§7.3). Every verb copes with that: it handles the checkouts that
  exist and prints a reminder for each missing one, e.g.

  ```
  xwt: warning: dev has no checkout of chordutils (added to xwt.toml after dev
       was created); run `xwt init` in /home/kmsmith/ch/dev to add it
  ```

  This applies to `status`, `diff`, `rebase-down`, `merge-up` and
  `venv` (which skips an `editable` entry it can't install). For
  `rebase-down` and `merge-up` the missing repo has no branch to move, so the
  others proceed. `delete` skips a missing repo without a reminder (there is
  nothing to remove), and `new` always creates every checkout. A directory
  that exists but isn't a git worktree of the toplevel's sub-repo (e.g. a
  separate clone) is skipped the same way, with its own warning. In the
  toplevel, a sub-repo listed but not yet cloned gets the reminder "run
  `xwt init` in the toplevel"; `new` refuses until it is cloned.
- **Ignore rules go in the toplevel's `.gitignore`.** The paths xwt creates
  in workspaces must be ignored by the toplevel repo: each sub-repo directory
  (`/NAME/`), and with `[envrc]`, `/.envrc` and `/.direnv/`, plus `/.venv/`
  with `[envrc.venv]`. `xwt init` (in the toplevel) and `xwt add-repo` check
  each with `git check-ignore -q`, passing directories with a trailing slash
  (`NAME/`): without it, a directory-only pattern such as `/NAME/` doesn't
  match a directory that doesn't exist yet. Any that isn't ignored is appended
  to the toplevel's `.gitignore` (created if missing), under a comment line
  `# added by xwt`, and each addition prints an informational line:

  ```
  xwt: added /chordutils/ to /home/kmsmith/ch/top/.gitignore
  ```

  The command then reminds the user to commit `.gitignore` (§7.3, §7.5).
  xwt only appends, and edits only the toplevel's copy; worktrees get the new
  lines when they rebase down. Paths that are already ignored, by
  `.gitignore` or anything else, are left alone.
- **Agents and people are treated alike.** xwt doesn't try to detect agents,
  sandboxed or not. Which commands agents may run is a project instruction
  (e.g. in `CLAUDE.md`); `xwt init` prints a reminder when it creates
  `xwt.toml` (§7.3). Agents can run git directly anyway, so a check in xwt
  would add little.
- **Messages** are prefixed `xwt:`; errors are `xwt: error: ...` on stderr,
  exit 1. Commands that change things are echoed first (`xwt: $ git -C
  /home/kmsmith/ch/top/pirate worktree add ...`).
- **Labels**: `<workspace dir name>/<sub-repo>` for sub-repo checkouts, and
  `<workspace dir name>/ (repo <name>)` for the toplevel repo's checkout, where
  `<name>` is the basename of its `origin` URL without `.git` (falling back to
  the main checkout's directory name). Example: `dev/ (repo ch_top)`.
- **Order**: the toplevel repo first, then sub-repos in `xwt.toml` order.
  Worktrees are listed by name.

### 7.2 Preparing a workspace

Used by `init` (toplevel and worktree) and `new`, once the checkouts exist:

1. `git submodule update --init --recursive` in each checkout that has
   `.gitmodules`.
2. With `[envrc]`: write `.envrc` (§6.1).
3. With `[envrc]` and `direnv` on `PATH`: `direnv allow <workspace>`. Only
   the `.envrc` xwt just generated is allowed; a project's own `.envrc` (no
   `[envrc]`) is left for the user to review and allow.
4. With `[envrc.venv]` and unless `--no-venv`: build the venv (§6.3).

### 7.3 `xwt init [--no-venv]`

**No toplevel found** (§4.1): create one.

1. The current directory must be the root of a main checkout, and its parent
   must not be `$HOME` ("move the clone into a group dir first, e.g.
   `mkdir ~/proj && mv ~/repo ~/proj/top`"). Elsewhere: "run `xwt init` in
   the root of a repo's main checkout".
2. Detect and compose the file (§4.4).
3. On a terminal: print the file, then ask `Write /path/xwt.toml? [Y/n/e]`.
   - `y` or Enter: write it.
   - `n`: stop (exit 0); nothing is written.
   - `e`: open the text in `$VISUAL` or `$EDITOR` (default `vi`; split with
     `shlex`, so `emacs -nw` works) on a temporary file, validate the result
     (on an error, print it), then print it and ask again.

   Without a terminal on stdin: write it without asking.
4. Print a reminder about agents (only here, when the file is created):

   ```
   xwt: if coding agents work in this project, you may want to tell them (in
        CLAUDE.md, AGENTS.md, ...) not to run the xwt commands that change
        branches, worktrees or clones:
            xwt merge-up, xwt rebase-down, xwt new, xwt delete,
            xwt init, xwt add-repo
        These are fine for agents: xwt status, xwt diff, xwt venv.
   ```

5. Continue as below.

**In the toplevel:**

1. Refuse if the group dir is `$HOME`.
2. For each sub-repo: if its directory is missing, `git clone [-b BRANCH] URL
   DIR`. If it exists, leave it; warn if it isn't a git clone, its `origin`
   URL differs from `url`, or it isn't on its integration branch (init never
   switches branches). The same branch warning applies to the toplevel repo.
3. Make sure the paths xwt manages are ignored, adding any missing lines to
   `.gitignore` (§7.1).
4. Prepare the workspace (§7.2).
5. If this run created `xwt.toml` or added to `.gitignore`, end with a
   reminder naming whichever files changed:
   `xwt: commit xwt.toml and .gitignore in /home/kmsmith/ch/top`.

**In a worktree**, init completes it: for each sub-repo missing from the
worktree, add its git worktree as `xwt new` does (§7.4, step 3; the sub-repo's
main checkout must be on its integration branch), then prepare the workspace
(§7.2). This is how a sub-repo added to `xwt.toml` reaches existing
worktrees: one worktree at a time, when the user chooses, since the venv
rebuild takes a while and may disturb an agent working there.

Re-running `xwt init` after editing `xwt.toml` applies the change: it clones
new sub-repos, rewrites `.envrc` and re-runs the editable installs. It never
removes anything: a sub-repo dropped from `xwt.toml` stays on disk, and the
venv is not recreated (`xwt venv --recreate` does that).

(`xwt init` anywhere inside an existing toplevel or worktree acts on that
workspace; nested toplevels aren't supported.)

### 7.4 `xwt new NAME [--no-venv]`

Run anywhere in the toplevel or a worktree. Creates `<group>/NAME`.

1. **Checks** (all of them, then refuse if any fail):
   - `NAME` is a single path component of letters, digits, `.`, `_`, `-`, not
     starting with `.` or `-`, and `git check-ref-format --branch NAME` accepts
     it;
   - the group dir isn't `$HOME`; `<group>/NAME` doesn't exist;
   - every sub-repo's main checkout exists (otherwise "run `xwt init`");
   - every main checkout is on its integration branch (§4.3), and that branch
     isn't `NAME`;
   - in each repo, branch `NAME` is not checked out in any other git worktree.
2. `git worktree prune` in each repo (clears entries whose directory was
   deleted by hand; `git worktree add` refuses such a path otherwise).
3. Toplevel repo: `git -C TOP worktree add <group>/NAME -b NAME INTEG`, where
   `INTEG` is its integration branch. Then each sub-repo, with its own
   integration branch: `git -C TOP/SUB worktree add <group>/NAME/SUB -b NAME
   INTEG`. If branch `NAME` already exists in a repo (e.g. kept by
   `xwt delete`), check it out instead (`worktree add PATH NAME`, no `-b`) and
   say how it relates to the integration branch ("reusing existing branch dev
   in pirate: 3 commits ahead of kms, 5 behind").
4. Prepare the workspace (§7.2).
5. Print the new worktree's path, with the hint `cd <path>`.

If step 3 fails anyway, say what was created and that
`xwt delete NAME --force` removes it. If step 4 fails (e.g. a compile error
in the venv build), the worktree itself is complete: say so, and that
`xwt venv` there retries the build.

### 7.5 `xwt add-repo URL [--branch B]`

Run anywhere in the project; acts on the toplevel.

1. `NAME` is the part of the URL after its last `/` or `:`, without `.git`
   (`github:kmsmith137/chordutils` and `git@host:me/chordutils.git` both give
   `chordutils`); it must be a valid repo name (§4.2). Refuse if `xwt.toml`
   already has `[repo.NAME]`.
2. If `<toplevel>/NAME` doesn't exist: `git clone [-b B] URL NAME`. If it
   exists, it must be a git clone whose `origin` is `URL`; then it is only
   recorded.
3. Append to `xwt.toml`, leaving the rest of the file (and its comments)
   untouched:

   ```toml

   [repo.NAME]
   url = "URL"
   branch = "BRANCH"     # the clone's current branch
   ```

   A table appended at the end is always valid TOML. Re-read and validate the
   file; if that fails, restore the previous contents and report.
4. Add `/NAME/` to `.gitignore` if it isn't already ignored (§7.1); init
   its submodules.
5. Print the next steps, tailored to what it found.

Each step is announced in plain words before it runs, followed by the echoed
command. Example, in ch_top with worktrees `dev` and `dev2`:

```
$ xwt add-repo github:kmsmith137/chordutils
xwt: cloning chordutils into the toplevel
xwt: $ git clone github:kmsmith137/chordutils /home/kmsmith/ch/top/chordutils
  ...git's output...
xwt: chordutils is on branch main, which is now its integration branch
xwt: added [repo.chordutils] to /home/kmsmith/ch/top/xwt.toml (last in the
     install order; move it up if something else depends on it)
xwt: added /chordutils/ to /home/kmsmith/ch/top/.gitignore

Next steps:
  1. chordutils is a Python package. To install it into the venvs, add
     "chordutils" to `editable` under [envrc.venv] in
     /home/kmsmith/ch/top/xwt.toml (after anything it depends on).
  2. Commit xwt.toml and .gitignore in /home/kmsmith/ch/top.
  3. Run `xwt init` in the toplevel (to install chordutils into its venv)
     and in each worktree, to add the chordutils checkout and rebuild the
     venv:
         /home/kmsmith/ch/top
         /home/kmsmith/ch/dev
         /home/kmsmith/ch/dev2
     Until then, xwt commands in those worktrees remind you that chordutils
     is missing.
```

The next steps adapt:

- step 1 appears only if the new repo has a `pyproject.toml` or `setup.py`
  and `xwt.toml` has `[envrc.venv]`;
- step 2 names the files that need committing: `xwt.toml` if it is tracked
  (`git ls-files --error-unmatch`), and `.gitignore` if xwt added to it; it
  is left out if neither applies;
- in step 3, the toplevel is listed only when step 1 applies (otherwise
  `add-repo` has already done everything there), and the worktrees are all
  the current worktrees;
- the steps are renumbered to match, and with no worktrees and nothing else
  to do, the section is left out.

### 7.6 `xwt venv [--recreate] [--bare]`

Builds the current workspace's venv (§6.3). Requires `[envrc.venv]` and the
workspace's `.envrc` ("run `xwt init` here first"). An `editable` entry
whose checkout is missing is skipped with the reminder (§7.1). It doesn't
rewrite `.envrc`; that is `init`'s job. It works inside an xsb sandbox: with
the venv already created, it needs no network.

### 7.7 `xwt delete NAME [--force]`

Run from the toplevel or another worktree; refuse when the current directory
is inside `<group>/NAME`. `NAME` must be a worktree (§3); the toplevel can't
be deleted. Every main checkout must be on its integration branch (§4.3),
even with `--force`: step 3's `git branch -d` judges "merged" against it.

1. **Safety checks** in every checkout of the worktree, all listed together;
   any failure refuses unless `--force`:
   - uncommitted changes to tracked files;
   - untracked files that aren't ignored;
   - commits on `NAME` not in the integration branch ("run `xwt merge-up`
     first"); being behind is fine;
   - a rebase, merge or cherry-pick in progress.

   `--force` can therefore lose uncommitted edits and untracked files, and
   abandons an operation in progress; committed work survives on its branch
   (step 3).
2. Remove the sub-repo git worktrees (`git -C TOP/SUB worktree remove --force
   PATH`), then the toplevel repo's, then `git worktree prune` in each repo.
3. In each repo, `git -C <main checkout> branch -d NAME`. If git refuses
   (unmerged), keep the branch and warn ("kept branch dev in pirate: not merged
   into kms; its commits are preserved"). Skip repos whose checkout was
   detached or on the integration branch.
4. Remove whatever is left of `<group>/NAME`, including ignored files
   (build products, `.venv`, ...), without following symlinks (a symlink
   such as ch's `research -> /scratch/...` is removed, its target kept).
   Ignored files are treated as disposable, so they aren't listed and nothing
   is asked. The README says so, with the example of ch_top's ignored
   `plans/`: move anything you want to keep out of the worktree first.

If `<group>/NAME` was already deleted by hand but git still lists it, skip
the checks: prune, then delete the branches (step 3).

### 7.8 `xwt status [ARGS]`

1. **Header**: the toplevel's path and the integration branches, e.g.
   `toplevel /home/kmsmith/ch/top: ch_top main, pipmake main, ksgpu kms, pirate
   kms`.
2. `git status ARGS` in each checkout of the current workspace, under headers.
3. **Branch relations** (format in §2.1). From the toplevel: every worktree.
   From a worktree: only itself.
4. **Warnings**, each on its own line starting `WARNING:`, after a blank
   line. From the toplevel they cover the toplevel and every worktree; from a
   worktree, the toplevel and this worktree:
   - a main checkout is not on its integration branch (§4.3), or is
     detached;
   - a worktree checkout is not on branch `NAME` (or is detached);
   - a rebase, merge or cherry-pick is in progress in a checkout;
   - a sub-repo is missing from the toplevel ("run `xwt init`") or from a
     worktree ("run `xwt init` in it");
   - a worktree's sub-repo directory is not a git worktree of the toplevel's
     sub-repo (e.g. a separate clone);
   - a sub-repo directory is not ignored by the toplevel repo;
   - git lists a worktree in the group dir whose directory is gone ("run
     `xwt delete NAME`");
   - with `[envrc]`: a workspace's `.envrc` is missing or differs from what
     `xwt.toml` now produces ("run `xwt init` there"); with `[envrc.venv]`, a
     workspace has no `.venv` ("run `xwt venv` there");
   - this worktree's `xwt.toml` differs from the toplevel's, which is the one
     used.

Exit status: the worst git exit code, and at least 1 if there are
warnings.

### 7.9 `xwt diff [ARGS]`

`git diff ARGS` in each checkout of the current workspace,
under headers, with `-c color.ui=always` when stdout is a terminal. Output is
captured per repo so it can go under its header. Exit status: the worst git
exit code (a plain `git diff` exits 0 even with changes; pass `--exit-code`).

### 7.10 `xwt rebase-down [--dry-run] [-- ARGS]`

Run in a worktree; in the toplevel: "there's no branch to rebase in the
toplevel; run this in a worktree".

1. For each checkout: skip with a warning if it isn't on branch `NAME`
   (detached, or on another branch); skip with a note if not behind
   (`git rev-list --left-right --count INTEG...NAME`).
2. **Checks**, then refuse if any fail: every main checkout is on its
   integration branch (§4.3); and in the checkouts to be rebased, no
   uncommitted changes to tracked files (git refuses to rebase otherwise), and
   no rebase or merge in progress ("finish it with `git rebase --continue` or
   `--abort` in `<path>`").
3. `git rebase ARGS INTEG` in each, one after another. git's output goes
   straight to the terminal, under the header, so `-- -i` works. A conflict
   stops that repo; continue with the remaining repos, then exit 1 naming the
   repos to finish by hand.

`--dry-run` prints each command without running it. Nothing is regenerated
after a rebase: `.envrc` comes from the toplevel's `xwt.toml`, not from the
branch.

### 7.11 `xwt merge-up [--dry-run] [--no-ff]`

Run in a worktree; in the toplevel: "merge-up lands a worktree's branch; run
it in that worktree".

1. For each checkout: skip with a warning if it isn't on branch `NAME`
   (detached, or on another branch); skip with a note if the integration
   branch already contains `NAME`.
2. **Checks** on the rest, then refuse if any fail:
   - every main checkout is on its integration branch (§4.3);
   - without `--no-ff`: `INTEG` is an ancestor of `NAME`
     (`git merge-base --is-ancestor`); otherwise "pirate: kms has 2 commits
     not in dev; run `xwt rebase-down` first";
   - none of the files the merge changes (`git diff --name-only INTEG...NAME`)
     has uncommitted changes in the main checkout or exists there untracked
     (both appear in `git status --porcelain`); git would refuse to merge;
   - no rebase or merge in progress in the main checkout.
3. `git -C <main checkout> merge --ff-only NAME` (or `--no-ff`) in each.
   Output streams to the terminal under the header, which shows the main
   checkout's path. With `--no-ff` a merge can still conflict; that repo is
   left mid-merge, the others continue, and xwt exits 1, as with rebase-down
   conflicts.

`--dry-run` prints each command without running it.

## 8. Output format example

`xwt status` in the toplevel, with one worktree behind and one ahead:

```
toplevel /home/kmsmith/ch/top: ch_top main, pipmake main, ksgpu kms, pirate kms

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

## 9. Package layout

```
xwt/
  pyproject.toml     # setuptools, src layout, requires-python >= 3.11, no dependencies,
                     # [project.scripts] xwt = "xwt.cli:main"; pytest as a dev extra
  README.md          # user docs (§11, phase 6)
  plans/
  src/xwt/
    __init__.py      # __version__
    __main__.py      # python -m xwt
    cli.py           # argument parsing, dispatch, usage text, error reporting
    config.py        # load and validate xwt.toml
    detect.py        # what `xwt init` detects, the file text it composes (§4.4), the prompt
    layout.py        # finding the toplevel and workspace (§4.1); listing worktrees and checkouts
    git.py           # subprocess wrappers; worktree-list parsing; ahead/behind;
                     # in-progress detection; .gitignore additions
    envrc.py         # .envrc text, the workspace environment, the venv build (§6)
    verbs.py         # one function per verb
  tests/
```

Follow `~/git/xsb`'s packaging (`pyproject.toml`, `src/`, `cli.py` with flat
verbs) so the two tools look alike.

## 10. Using xwt with xsb

xwt needs nothing from xsb. When both are used:

- **xwt behaves the same in and out of a sandbox** (§7.1); which commands
  agents use is up to the project's instructions. In ch's layout `merge-up`
  can't succeed in a sandbox anyway, since `~/ch/top` is read-only there.
- **Activate, then launch.** `cd` into a workspace (direnv loads its
  `.envrc`), then `xsb claude`; the sandbox inherits that environment and
  never reads `.envrc` itself.
- **Rebuilding in the sandbox**: an agent runs `xwt venv` after changing
  compiled code. With the venv already created, it needs no network.
- **Commits from worktrees** work in a sandbox because xsb mounts each repo's
  shared `.git` store read-write (with `config` and `hooks/` read-only), even
  though the store lives in the toplevel.
- **Two layouts.**
  - *Group dir as the xsb root* (ch_top's choice; Appendix B of the xsb plan,
    `~/git/xsb/plans/xsb.md`):
    `~/ch/xsb.toml` is a symlink to a file tracked in ch_top. Every agent sees
    the whole group. The toplevel is protected (read-only) and refused as a
    launch directory, since the user runs unsandboxed tools there.
  - *Each workspace its own xsb root*: commit an `xsb.toml` in the toplevel's
    repo, so every worktree has one. Each agent sees only its own worktree
    (plus the shared `.git` stores). Suits N=0 projects such as `ms`.

## 11. Phases and tests

Phases, each ending with passing tests and a commit:

1. Skeleton, `config.py`, `layout.py`, `git.py`; `status` and `diff`.
   Test on layouts built with plain git commands in the fixtures.
2. `init` for an existing `xwt.toml` (without `[envrc]`), `new`,
   `add-repo`; `.gitignore` additions, `direnv allow`.
3. Workspace environments: `.envrc`, the workspace environment, the venv build,
   `xwt venv`; `init`'s detection and prompt.
4. `rebase-down` and `merge-up`.
5. `delete`.
6. `README.md`: install (`pip install -e`), quick starts for N=0 and N>0, the
   `xwt.toml` reference, the daily workflow, and the conflict-resolution text
   from ch_top's README ("Conflicts during rebase-down").
   Say that `xwt delete` removes ignored files with the worktree (§7.7).

**Test setup.** A fixture builds "remote" bare repos in a temp dir (a top repo
on `main`, two sub-repos with `main` and `kms` branches, one of them with a
submodule, both small Python packages using setuptools), then a group dir with
a toplevel clone. Tests call `cli.main(argv)` with the current directory set.
Isolate from the user's git config: `GIT_CONFIG_GLOBAL=/dev/null`,
`GIT_CONFIG_NOSYSTEM=1`, author and committer variables set, `HOME` pointed at
a temp dir. File-URL submodules need `git -c protocol.file.allow=always`. pip
runs offline (`PIP_NO_INDEX=1`): the editable installs use
`--no-build-isolation` with setuptools from the env running the tests, and
`install` entries name a wheel the fixture builds with
`pip wheel --no-build-isolation --no-deps`. Tests of `conda` activation run
only where `conda` is on `PATH` and `CONDA_DEFAULT_ENV` is set; otherwise they
are skipped.

### Test list

- discovery: from the toplevel, a worktree, a sub-repo checkout, a
  subdirectory, a submodule; a sub-repo with its own `xwt.toml` doesn't capture
  the workspace; outside any toplevel; a git worktree of the toplevel outside
  the group dir is not a worktree;
- config: empty file; unknown key; bad repo name; `editable` naming an unknown
  repo; a bad `[envrc.export]` name; `branch`, `[repo.*]`, `[envrc]`,
  `[envrc.export]` and `[envrc.venv]` parsed, repos in file order;
- detection: sub-repos in name order with their `origin` and branch; detached
  (no `branch`) and no-`origin` (skipped) clones; submodules not detected;
  `[envrc]` and `[envrc.venv]` active when packages are found, commented out
  otherwise; the composed file parses and validates; the prompt's `y`, `n`
  and `e` (with `EDITOR` set to a script that edits the file), and an invalid
  edit; no terminal: written without asking;
- `init`: clones missing sub-repos on `branch`, or the default branch; leaves
  existing ones and warns on a URL mismatch; appends each path that isn't
  ignored to `.gitignore` once (creating the file if missing), with an
  informational line each, and adds nothing for paths already ignored; the
  commit reminder; initializes submodules; refuses in `$HOME`; idempotent;
  in a worktree, adds a newly listed sub-repo; re-running after an edit
  rewrites `.envrc`;
- `new`: creates every checkout on `NAME` from each integration branch
  (`kms` for sub-repos when the toplevel is on `main`); reuses an existing
  branch; each check refuses before anything is created (bad name, existing
  dir, detached main checkout, `NAME` equal to an integration branch, branch
  checked out elsewhere); a stale entry from a hand-deleted dir is pruned;
  `--no-venv` skips the venv build; `direnv allow` runs only with `[envrc]`
  and `direnv` present, never for a project's own `.envrc` (use a fake
  `direnv` on `PATH`); a failing venv build leaves a complete worktree;
- missing sub-repos: in a worktree created before a sub-repo was added, each
  verb in §7.1 handles the other checkouts and prints the reminder; `delete`
  skips it silently; `xwt init` there adds it;
- `add-repo`: the announced steps and the next-steps text in each variant
  (Python package or not, `[envrc.venv]` or not, tracked or untracked
  `xwt.toml`, with and without worktrees); clones and appends, keeping the
  file's comments; `NAME` from both URL forms; an existing directory with a
  matching `origin` is recorded without cloning; a mismatched `origin` and a
  duplicate name are refused; a result that doesn't validate is rolled back;
- environments: `.envrc` content for conda only, venv only, and both, with
  export quoting and expansion; no venv is built without `[envrc.venv]`;
  written only on change; refused when `.envrc` is tracked; the venv is
  seeded from the base interpreter even when xwt runs with another venv
  active; `install` runs only when the venv is created; editable installs run
  in order, in each entry's directory, with `pip_args`; `--bare`,
  `--recreate`; pip runs with the new workspace's venv, not the caller's
  (run `xwt new` from a shell with the toplevel's venv active); a failing pip
  step exits 1; `xwt venv` without `[envrc.venv]` or `.envrc` refuses;
- integration branches: with a main checkout on another branch (or
  detached), `new`, `delete`, `rebase-down` and `merge-up` refuse before
  changing anything (`delete` even with `--force`), and `status` and `init`
  warn; a repo without `branch` follows its checkout; detection records the
  toplevel's branch;
- `status`: relations (ahead, behind, both, up to date, collapsed group) from
  the toplevel and from a worktree; each warning in §7.8; exit status;
- `diff`: pass-through and headers;
- `rebase-down`: rebases only the repos that are behind; skips a checkout
  that isn't on `NAME`; refuses in the toplevel; a dirty checkout refuses
  before any rebase; a conflict leaves that repo mid-rebase, finishes the
  others, exits 1; `--dry-run` changes nothing;
- `merge-up`: fast-forwards every repo; skips a checkout that isn't on
  `NAME`; refuses (nothing merged) when one repo isn't rebased; refuses on a
  conflicting dirty main checkout; `--no-ff` makes merge commits; `--dry-run`;
- `delete`: each safety check refuses; behind-only is fine; `--force`;
  ignored files deleted without a question; unmerged branches kept even with
  `--force`; merged ones deleted; a worktree whose pirate-like sub-repo has an
  initialized submodule is removed; a symlink inside the worktree is removed
  without touching its target; refuses from inside `NAME` and for the
  toplevel; a hand-deleted dir is cleaned up;
- the agent reminder is printed when `init` creates `xwt.toml`, and not on
  later runs;
- N=0 end to end: `init` (with detection), `new`, commit, `merge-up`,
  `rebase-down` in another worktree, `delete`.

## 12. ch_top migration (follow-up; not part of this repo's work)

Listed so the design can be checked against a real user. Done in ch_top after
xwt works, independently of the xsb migration.

1. Install xwt in the conda env (`pip install -e ~/git/xwt`, as for xsb).
2. Add `~/ch/top/xwt.toml` (tracked), replacing `git_repositories.toml` and
   the venv recipe in `ch_top_helpers.py`:

   ```toml
   # xwt config for ch_top: the toplevel is ~/ch/top, worktrees are ~/ch/NAME.

   # ch_top's integration branch; each sub-repo's is in its table. The
   # checkouts in ~/ch/top must be on these branches.
   branch = "main"

   # In dependency order: pipmake is the build backend for ksgpu and pirate.
   [repo.pipmake]
   url = "github:kmsmith137/pipmake"   # 'github' is an ~/.ssh/config host alias
   branch = "main"

   [repo.ksgpu]
   url = "github:kmsmith137/ksgpu"
   branch = "kms"

   [repo.pirate]
   url = "github:kmsmith137/pirate"    # has a submodule (asdf-cxx)
   branch = "kms"

   [envrc]
   conda = "dev"      # or leave out, to use whatever env the shell has active

   [envrc.export]
   # The workspace root holds the ksgpu/ source dir, which would shadow the
   # installed package (README Appendix A).
   PYTHONSAFEPATH = "1"

   [envrc.venv]
   # Editable installs' .pth files import `editables` at startup, before the
   # conda env's site-packages are on sys.path: the venv needs its own copy.
   install = ["editables"]
   editable = ["pipmake", "ksgpu", "pirate"]
   pip_args = ["--no-build-isolation"]   # build with the conda toolchain and the venv's pipmake
   ```

3. Delete `init-toplevel`, `init-worktree`, `init-venv`, `delete-worktree`,
   `git-status`, `git-diff`, `git-rebase-down`, `git-merge-up`,
   `ch_top_helpers.py`, `git_repositories.toml`, `dotfile_templates/` and
   `plans/branch-overrides.md`. `init-venv`'s flags map to `xwt venv
   --recreate` and `--bare`; `--test` is dropped (run
   `python -m pirate_frb test -n 1` by hand), and so is its smoke-test import
   (ch-test imports both packages anyway).
4. **Dotfiles.** `.envrc` stays gitignored and is now written by xwt: run
   `xwt init --no-venv` in the toplevel and in each worktree, which rewrites
   `.envrc`, re-allows it, and leaves the existing venvs alone.

   Delete `.claude/env.sh` in every workspace (`rm ~/ch/*/.claude/env.sh`)
   and its line in ch_top's `.gitignore`. It isn't needed: a `claude` started
   in `~/ch/dev` without `CLAUDE_ENV_FILE` runs `dev`'s own
   `.venv/bin/python` (tested 2026-10-28), because the `dev` env is already
   active when `claude` starts, so `~/.bashrc`'s `conda activate dev` changes
   nothing.

   Suggested to the user, not a step (it's their file): guarding the line in
   `~/.bashrc` as `[ -n "$CONDA_PREFIX" ] || conda activate dev`, so that a
   future change in conda or Claude Code can't push the venv down.
5. Docs: in `CLAUDE.md`, "I'll do this by running the git-* scripts" becomes
   "with `xwt rebase-down` / `xwt merge-up`", plus a line that agents must not
   run `xwt merge-up`, `rebase-down`, `new`, `delete`, `init` or `add-repo`
   (`xwt status`, `diff` and `venv` are fine); the layout section can point to
   `xwt status`. In `README.md`, replace "Scripts", "Daily workflow" and the
   worktree parts of "Quick start" with a pointer to xwt; delete Appendix B
   (activating the venv for an agent, i.e. `CLAUDE_ENV_FILE`); keep Appendix A
   (cwd shadowing). In `.claude/commands/ch-test.md`,
   `./init-venv .` becomes `xwt venv`. In `.claude/commands/ch-release.md`,
   `init-worktree -r` becomes `xwt new NAME --no-venv`, then `xwt venv --bare`
   in it, and `init-venv --release` becomes `xwt venv --recreate --bare`.
6. Existing worktrees need no other conversion.

## 13. Decisions, checks and deferred work

### 13.1 Decisions made

- **Name** `xwt`: free on PyPI (checked 2026-10-27); no `xwt` command on this
  machine.
- **A Python package like xsb**: installed outside any workspace (problem 1),
  no dependencies, flat verbs.
- **No state beyond git and `xwt.toml`** (§3); existing worktrees just work.
- **The toplevel's `xwt.toml` is the only config** (§4.1): no user config, no
  environment variables, no per-worktree copies.
- **Integration branches are named in `xwt.toml`, and the main checkouts
  must be on them** (§4.3): the commands that move commits refuse otherwise.
  A repo without `branch` follows its checkout. No per-clone override file.
- **Worktree name = directory name = branch name**, in every repo.
- **Sub-repos are plain clones**, one directory each, directly in the
  toplevel.
- **Workspace environments are built in** (§6): `[envrc]` generates `.envrc`
  (a conda env, exports) and `[envrc.venv]` adds a per-workspace `.venv` with
  plain and editable installs; each works without the other.
- **No setup command.** `[envrc]` and `[envrc.venv]` cover ch_top's needs;
  a general hook is deferred (§13.3).
- **Detection switches the environment on only when it finds Python
  packages** (§4.4); otherwise `[envrc]` and `[envrc.venv]` are written
  commented out.
- **`xwt init` writes a filled-in `xwt.toml` from what it detects, and asks
  before writing** (§4.4, §7.3). No configuration flags; after creation the
  file is the user's, and `xwt init` re-applies it after edits.
- **`xwt add-repo`** grows the project without editing the file by hand
  (§7.5).
- **Commit `xwt.toml`** in shared or multi-machine projects (§4.4).
- **xwt never runs** `git branch -D`, and never fetches, pulls or pushes:
  the only network operations are cloning a missing sub-repo and initializing
  submodules.
- **Check everything, then act** (§7.1).
- **xwt writes only `.envrc`** into workspaces. Any other per-workspace file
  is tracked in the repo.
- **Worktrees are always siblings in the group dir**; their location is not
  configurable.
- **Verb names**: `new`, `delete`, `rebase-down` and `merge-up` (direct
  translations of the old scripts; the direction is explicit), plus `init`,
  `add-repo`, `venv`, `status` and `diff`.
- **A sub-repo added later reaches each existing worktree when the user runs
  `xwt init` there**; until then every verb reminds them (§7.1).
- **Ignore rules go in the toplevel's `.gitignore`**, appended automatically
  with an informational line per path and a reminder to commit; xwt never
  uses `.git/info/exclude` (§7.1).
- **`direnv allow` runs automatically** whenever xwt writes `.envrc` (§7.2):
  xwt generated the file from the toplevel's `xwt.toml`, so there is nothing
  for the user to review.
- **`xwt new NAME` reuses an existing branch `NAME`** (e.g. one kept by
  `xwt delete`) and says how it relates to the integration branch.
- **No agent guard** (§7.1): agents and people are treated alike, sandboxed
  or not; `xwt init` reminds the user to put the rule in the agents'
  instructions.

### 13.2 Checks during implementation

- `git worktree list --porcelain` fields (`worktree`, `HEAD`, `branch`,
  `detached`, `bare`, `locked`, `prunable`); the main worktree is listed first.
- `git rev-parse --path-format=absolute --git-common-dir` from a linked
  worktree, a main checkout and a submodule.
- `git branch -d` judges "merged" against `HEAD` of the repo it runs in (the
  main checkout, on the integration branch); confirm with git 2.43, including
  its message when the branch is merged into its upstream but not into `HEAD`.
- In-progress detection: `rebase-merge/`, `rebase-apply/`, `MERGE_HEAD`,
  `CHERRY_PICK_HEAD` in each checkout's own git dir (`git rev-parse
  --git-dir`), not the common dir.
- `git worktree remove` refuses a checkout with initialized submodules
  unless given `--force` (git's documented behaviour; ch_top's
  `delete-worktree` relies on it for pirate); confirm.
- Removing leftovers must not follow symlinks (`shutil.rmtree` unlinks a
  symlink inside the tree; test it).
- The generated `.envrc`'s conda lines work under direnv, when xwt sources the
  file with bash, and inside an xsb sandbox (sourced by `xwt venv`): they need
  `conda` on `PATH` (conda's `condabin`). If `conda shell.bash hook` is a
  problem, report it rather than switching mechanisms silently.
- direnv blocks a changed `.envrc` until it is allowed again; `direnv allow
  DIR` accepts the directory.
- `pip install --ignore-installed editables` in a `--system-site-packages`
  venv puts a copy inside the venv when the conda env already has one.
- The base-interpreter rule: `sys.base_prefix` of a venv's python is the conda
  env; of the conda env's python, itself.

### 13.3 Deferred

- `xwt list` (status from the toplevel already lists every worktree).
- `xwt git ARGS...`: any git command in every repo, under headers (e.g.
  `xwt git log -1 --oneline`, `xwt git fetch`).
- Fetch, pull and push helpers.
- `xwt new NAME --from OTHER` (branch off another worktree instead of the
  integration branches) and `xwt rename OLD NEW`.
- Removing a sub-repo from `xwt.toml` (its checkouts are left as they are).
- Sub-repo paths with `/`; `add-repo --name`.
- Detecting the sub-repos' dependency order.
- Python without conda (pyenv, uv), and `uv` for faster installs.
- Extra lines in the generated `.envrc` beyond exports.
- Copying untracked files (e.g. `.env` secrets) from the toplevel into new
  worktrees.
- A `setup` command run in each workspace after it is prepared: an escape
  hatch for steps `[envrc]` can't express (`npm install`, downloading data).
  ch_top would have used it only for a smoke-test import.
- Running from the group dir; shell completion.

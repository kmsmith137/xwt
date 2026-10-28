# xwt implementation report

Report on implementing `plans/xwt.md`. All six phases are done, each committed
with its tests passing, followed by fixes from an independent review (below).

## What was built

A Python package, `xwt` (src layout, setuptools, no runtime dependencies,
Python ≥ 3.11), with the `xwt` command and `python -m xwt`:

| Module | Contents |
|---|---|
| `cli.py` | usage text, argument parsing, dispatch, error reporting |
| `config.py` | loading and validating `xwt.toml` (§4.2); TOML string quoting |
| `layout.py` | finding the toplevel and workspace (§4.1); the project's repos, worktrees and checkouts; integration-branch checks (§4.3); the missing-checkout reminders (§7.1) |
| `git.py` | git subprocess wrappers; `worktree list --porcelain` parsing; ahead/behind; in-progress detection; `.gitignore` additions |
| `envrc.py` | the generated `.envrc` (§6.1), the workspace environment (§6.2), the venv build (§6.3), `direnv allow` |
| `detect.py` | `xwt init`'s detection and composed `xwt.toml` (§4.4), the `[Y/n/e]` prompt and editor (§7.3) |
| `verbs.py` | one function per verb: `init`, `new`, `add-repo`, `venv`, `delete`, `status`, `diff`, `rebase-down`, `merge-up` |
| `ui.py` | `xwt:` messages, warnings and errors (wrapped to the terminal width), echoed commands, headers |

`ui.py` isn't in the plan's §9 layout; it holds the output helpers every other
module uses.

`README.md` covers install, quick starts for one repo (N=0) and several repos,
every command, the daily workflow, the conflict-resolution text from ch_top's
README, the `xwt.toml` reference, workspace environments, `add-repo`, agents
and xsb, and that `xwt delete` removes ignored files (with ch_top's `plans/` as
the example).

Size: about 2,300 lines of source and 2,300 of tests.

## What was verified

### Tests

170 tests (`pytest`, about 70 s; the venv tests account for most of it), all
passing. They follow §11's setup: session-scoped bare "remote" repos (a toplevel
repo, two setuptools packages with `main` and `kms` branches, one with a
submodule), a group dir with a toplevel clone per test, `cli.main(argv)` called
in-process with the current directory set, git isolated from the user's config
(`GIT_CONFIG_GLOBAL=/dev/null` etc.), `HOME` in a temp dir, file-URL submodules
allowed through `GIT_CONFIG_COUNT`, pip offline (`PIP_NO_INDEX=1`, editable
installs with `--no-build-isolation`, `install` entries naming a wheel the
fixture builds). `PATH` is a fake `direnv` that logs its arguments, links to the
test interpreter's `python`/`pip`, then `/usr/bin:/bin`; the conda test adds
conda's `condabin` and is skipped where conda isn't available (it ran here).

Every item in §11's test list is covered; the files are `test_config`,
`test_layout` (discovery), `test_status` (status and diff), `test_init`,
`test_new`, `test_add_repo`, `test_envrc` (environments and `xwt venv`),
`test_detect` (detection, prompt, agent reminder), `test_move` (rebase-down and
merge-up), `test_delete`, and `test_e2e` (N=0 end to end, plain and with a
toplevel Python package and venv). Each review fix below has a regression test.

### Independent review

After phase 6, a separate agent reviewed the code against the plan, looking for
ways to lose work or act on the wrong branch, and reproduced what it found in
scratch directories. All six findings were fixed:

1. **`delete` destroyed git checkouts it didn't know about** inside the
   worktree: an ignored Claude Code worktree (`dev/.claude/worktrees/agent`,
   with uncommitted work), or the checkout of a sub-repo since dropped from
   `xwt.toml`. The safety checks only looked at `xwt.toml`'s checkouts, and
   `git status` doesn't show ignored directories. Now `delete` walks the
   worktree (not following symlinks) and refuses, even with `--force`, if it
   finds any git checkout that isn't one of its checkouts or their submodules.
2. **`merge-up` could land some repos and then fail in another**, breaking
   "check everything, then act": the dirty-file check used rename detection, so
   a renamed file's old path wasn't checked (now `--no-renames`); and with
   `--no-ff` git refuses any staged change in the main checkout (now checked).
   `rebase-down` likewise now refuses untracked files the rebase would
   overwrite. After a failed step, the error now says whether the operation
   stopped on conflicts or never started, instead of always saying "resolve the
   conflicts".
3. **A tag named like a branch** (say tag `dev`, or `kms`) made `symbolic-ref
   --short` report `heads/dev`, so checkouts looked to be on the wrong branch;
   worse, `git merge --ff-only dev` resolves tags first and would have landed
   the tag. xwt now reads the full ref, and passes `refs/heads/NAME` to `merge`,
   `rebase` and `worktree add -b` when the short name is ambiguous.
4. **`delete` lost submodule commits**: a linked worktree's submodules have
   their own store under `<main>/.git/worktrees/<id>/modules/`, deleted with
   the worktree, so a commit made in `dev/pirate/<submodule>` and recorded in
   the merged branch could vanish. `delete` now refuses (unless `--force`) when
   a submodule has commits on no remote-tracking branch.
5. **`new` left a half-made worktree when a branch `NAME/something` existed**
   (git can't then create `NAME`). It is now checked first. Also, some of
   `new`'s checks ran only when no earlier problem was found; now every problem
   is listed.
6. **Discovery**: in the toplevel's own checkout of a sub-repo (`~/ch/top/liba`)
   that has its own committed `xwt.toml`, §4.1 made that sub-repo the toplevel,
   and `xwt new foo` there created `~/ch/top/foo`. Now, when the directory is
   its own main checkout and the enclosing toplevel's `xwt.toml` lists it, the
   enclosing toplevel wins.

### §13.2 checks (by hand, git 2.43, in scratch directories)

- `git worktree list --porcelain`: fields `worktree`, `HEAD`, `branch`,
  `detached`, `locked`, `prunable` as expected; the main worktree is first; a
  hand-deleted worktree shows `prunable gitdir file points to non-existent
  location`.
- `git rev-parse --path-format=absolute --git-common-dir`: the main checkout and
  a linked worktree both give `<main>/.git`; a submodule gives
  `<main>/.git/modules/<path>` (or `.../worktrees/<wt>/modules/<path>` in a
  worktree), which doesn't end in `/.git`, so discovery skips it as planned.
- `git branch -d` judges "merged" against `HEAD` and refuses an unmerged branch.
  **But** a branch merged into its upstream and not into `HEAD` is deleted, with
  only a warning ("deleting branch 'dev' that has been merged to
  'refs/remotes/origin/dev', but not yet merged to HEAD"). So `xwt delete`
  checks `merge-base --is-ancestor NAME INTEG` itself first (see departures).
- In-progress detection from each checkout's own git dir (`rebase-merge/`,
  `rebase-apply/`, `MERGE_HEAD`, `CHERRY_PICK_HEAD`): covered by tests with real
  conflicted rebases, merges and cherry-picks in linked worktrees.
- `git worktree remove` refuses a checkout with initialized submodules ("working
  trees containing submodules cannot be moved or removed") and succeeds with one
  `--force`. A locked worktree needs `--force --force`.
- `git check-ignore -q NAME/` matches `/NAME/` for a directory that doesn't
  exist yet; without the slash it doesn't.
- `shutil.rmtree` unlinks a symlink inside the tree without following it
  (tested: the target's contents survive `xwt delete`).
- The generated `.envrc`'s conda lines: under real direnv (in a scratch `HOME`),
  `CONDA_DEFAULT_ENV=dev`, `python3` is the workspace's `.venv/bin/python3`, and
  its `sys.base_prefix` is the conda env; sourced by xwt with bash, likewise
  (test). **Not verified inside an xsb sandbox.**
- direnv blocks a changed `.envrc` until it is allowed again, and
  `direnv allow DIR` accepts the directory.
- `pip install --ignore-installed editables` in a `--system-site-packages` venv
  over the `dev` conda env (which has `editables`) put a copy in the venv's own
  site-packages (checked once with network access).
- The base-interpreter rule: a venv's `python3` reports the conda env as
  `sys.base_prefix`; xwt seeds new venvs from `<base_prefix>/bin/python3`, and a
  test runs `xwt new` with the toplevel's venv active and checks the new venv's
  `pyvenv.cfg` `home` and its editable installs.

Also checked: `pip install -e` of xwt into a scratch venv (offline, with
`--no-build-isolation`) gives a working `xwt` entry point; a manual walkthrough
of a two-repo project (init with detection, new, status, merge-up, add-repo with
its next steps, init in a worktree) produced the output shown in the plan.

Nothing was run in `~/ch`.

## Departures from the plan, with reasons

1. **`.envrc` is sourced with `set -e`** in the workspace-environment wrapper
   (`set -e; cd "$1"; source ./.envrc; set +e; cd "$2" && shift 2 && exec "$@"`).
   Without it, a failing `conda activate` (say, the env doesn't exist on this
   machine) prints an error and the rest of the file still loads, so the source
   succeeds and xwt would build the venv over the wrong env. Conda's hook works
   under `set -e` (checked). The wrapper also takes the directory to run in, for
   the editable installs.
2. **`xwt delete` checks "merged" itself** before `git branch -d`, because `-d`
   also deletes a branch merged only into its upstream (above). It still uses
   `-d`, never `-D`.
3. **`xwt delete` removes leftover files before pruning and deleting branches**
   (the plan's steps 2, 4, then prune and 3). If a `git worktree remove` fails,
   its directory would otherwise still exist at prune time, the entry would
   stay, and `git branch -d` would refuse a branch still checked out there.
4. **`xwt delete` deletes branch `NAME` in every repo that has it**, merged, not
   only where the checkout was on `NAME`. The plan's "skip repos whose checkout
   was detached" came from ch_top, which didn't know the branch name; xwt does.
5. **Extra `delete` safety checks:** a detached HEAD whose commit no branch
   contains (those commits would be lost with the worktree); a locked worktree;
   a submodule with commits on no remote branch; a directory at a sub-repo's
   path that isn't a worktree of it; and, refused even with `--force`, any git
   checkout inside the worktree that xwt doesn't manage (review findings 1
   and 4).
6. **Repo names may not start with `.`** (keeps them clear of `.git`, `.venv`,
   `.envrc`); otherwise letters, digits, `.`, `_`, `-`. Names with a `.` are
   written quoted (`[repo."a.b"]`) by `add-repo`.
7. **`merge-up --no-ff` passes `--no-edit`**, so each merge commit gets git's
   default message instead of opening an editor per repo now that git's output
   goes to the terminal.
8. **`merge-up` also refuses when the worktree's checkout has an operation in
   progress** (for example an unfinished rebase, which would otherwise land the
   pre-rebase branch).
9. **A checkout in the middle of rebasing `NAME` counts as on `NAME`** (its HEAD
   is detached), so `rebase-down` refuses with "finish it with `git rebase
   --continue` or `--abort`" instead of skipping it, and `status` doesn't also
   call it detached.
10. **`git worktree prune` runs only in repos where git lists a prunable entry**
    (and is then echoed), instead of in every repo every time, to keep `new`'s
    output short. The effect is the same.
11. **`add-repo` validates the would-be `xwt.toml` before cloning** and refuses
    if it wouldn't parse, so nothing is cloned for nothing; it also restores the
    old file if the written one doesn't validate, as planned. With an existing
    clone and `--branch B`, it refuses if the clone isn't on `B`.
12. **`status` warns about every path xwt manages that isn't ignored**
    (`.envrc`, `.direnv/`, `.venv/` too), not only sub-repo directories.
13. **`xwt venv` warns** when `.envrc` differs from what `xwt.toml` produces.
14. **`xwt new` refuses with `[envrc]` when the integration branch tracks
    `.envrc`**, before creating anything (the plan's check happens when writing
    the file, which would be after the checkouts exist).
15. **A failed `install` step suggests `xwt venv --recreate`**: `install` runs
    only when the venv is created, so a plain re-run wouldn't retry it.
16. **`xwt` with no arguments exits 2** after printing the usage (`-h` and
    `--help` exit 0), as `xsb` does.
17. **Detection skips hidden directories** (names starting with `.`).
18. **Discovery adds one rule to §4.1** (review finding 6): a directory that is
    its own main checkout and has an `xwt.toml` yields to the enclosing
    toplevel when that toplevel's `xwt.toml` lists it as a sub-repo.
19. **`merge-up --no-ff` requires a clean index, and `rebase-down` refuses
    untracked files it would overwrite** (review finding 2), so both stay
    "check everything, then act".

Worth knowing:

- Messages are wrapped to the terminal width (80 columns when not a terminal),
  so long paths get a line of their own.

"""xwt rebase-down and merge-up."""

import subprocess


def parents(w, repo, ref="HEAD"):
    return w.git(repo, "rev-list", "--parents", "-n", "1", ref).split()[1:]


def contains(w, repo, ancestor, ref):
    return subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", ancestor, ref]).returncode == 0


# ---------------------------------------------------------------------------
# rebase-down
# ---------------------------------------------------------------------------

def test_rebases_only_repos_that_are_behind(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    w.commit(dev / "libb")
    kms_a = w.commit(w.top / "liba")
    r = w.xwt("rebase-down", cwd=dev)
    assert r.rc == 0, r.all
    assert contains(w, dev / "liba", kms_a, "dev")
    assert "==================== dev/liba: rebase dev onto kms ====================" in r.out
    assert f"xwt: $ git -C {dev / 'liba'} rebase kms" in r.out
    assert "xwt: dev/libb: dev isn't behind kms; nothing to rebase" in r.out
    assert "xwt: dev/ (repo proj): dev isn't behind main; nothing to rebase" in r.out
    assert "rebase main" not in r.out
    assert w.xwt("rebase-down", cwd=dev).out.count("nothing to rebase") == 4


def test_rebase_args_pass_through(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(w.top)
    r = w.xwt("rebase-down", "--", "--exec", "true", cwd=dev)
    assert r.rc == 0, r.all
    assert f"xwt: $ git -C {dev} rebase --exec true main" in r.out


def test_rebase_skips_a_checkout_not_on_its_branch(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.git(dev / "liba", "checkout", "-q", "--detach")
    kms_a = w.commit(w.top / "liba")
    kms_b = w.commit(w.top / "libb")
    r = w.xwt("rebase-down", cwd=dev)
    assert r.rc == 0, r.all
    assert "xwt: warning: dev/liba is detached, not on dev; skipping it" in r.err
    assert not contains(w, dev / "liba", kms_a, "dev")
    assert contains(w, dev / "libb", kms_b, "dev")


def test_rebase_refuses_in_the_toplevel(w):
    w.setup()
    r = w.xwt("rebase-down")
    assert r.rc == 1
    assert "there's no branch to rebase in the toplevel; run this in a worktree" in r.err


def test_a_dirty_checkout_refuses_before_any_rebase(w):
    w.setup()
    dev = w.add_worktree("dev")
    main = w.commit(w.top)
    kms_b = w.commit(w.top / "libb")
    (dev / "libb" / "libb" / "__init__.py").write_text("dirty\n")
    (dev / "untracked.txt").write_text("fine\n")
    r = w.xwt("rebase-down", cwd=dev)
    assert r.rc == 1
    assert "refusing to rebase" in r.err
    assert "dev/libb has uncommitted changes to tracked files; commit or stash them first" in r.err
    assert not contains(w, dev, main, "dev") and not contains(w, dev / "libb", kms_b, "dev")


def test_a_conflict_stops_that_repo_and_the_others_finish(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba", "same.txt", "dev\n")
    w.commit(w.top / "liba", "same.txt", "kms\n")
    kms_b = w.commit(w.top / "libb")
    main = w.commit(w.top)
    r = w.xwt("rebase-down", cwd=dev)
    assert r.rc == 1
    assert (f"the rebase stopped in dev/liba: resolve the conflicts, then run `git rebase "
            f"--continue` (or `git rebase --abort`) in:\n         {dev / 'liba'}") in r.err
    from xwt import git
    assert git.in_progress(dev / "liba") == "rebase"
    assert contains(w, dev / "libb", kms_b, "dev") and contains(w, dev, main, "dev")
    # Re-running refuses while the rebase is unfinished.
    r = w.xwt("rebase-down", cwd=dev)
    assert r.rc == 1 and "dev/liba has a rebase in progress: finish it with `git rebase " \
                         "--continue` or `--abort`" in r.err
    r = w.xwt("status", cwd=dev)
    assert "WARNING: dev/liba has a rebase in progress" in r.out


def test_rebase_dry_run_changes_nothing(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(w.top / "liba")
    before = w.rev(dev / "liba")
    r = w.xwt("rebase-down", "--dry-run", cwd=dev)
    assert r.rc == 0
    assert f"xwt: (dry run) $ git -C {dev / 'liba'} rebase kms" in r.out
    assert w.rev(dev / "liba") == before


def test_rebase_refuses_when_a_main_checkout_is_off_branch(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(w.top / "liba")
    w.git(w.top / "libb", "checkout", "-q", "main")
    before = w.rev(dev / "liba")
    r = w.xwt("rebase-down", cwd=dev)
    assert r.rc == 1 and "top/libb is on main, but xwt.toml says kms" in r.err
    assert w.rev(dev / "liba") == before


def test_rebase_with_a_missing_sub_repo(w):
    w.setup()
    dev = w.add_worktree("dev", subs=("liba",))
    kms_a = w.commit(w.top / "liba")
    r = w.xwt("rebase-down", cwd=dev)
    assert r.rc == 0, r.all
    assert "xwt: warning: dev has no checkout of libb" in r.err
    assert contains(w, dev / "liba", kms_a, "dev")


# ---------------------------------------------------------------------------
# merge-up
# ---------------------------------------------------------------------------

def test_fast_forwards_every_repo(w):
    w.setup()
    dev = w.add_worktree("dev")
    tips = {p: w.commit(p) for p in (dev, dev / "liba", dev / "libb")}
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 0, r.all
    assert w.rev(w.top, "main") == tips[dev]
    assert w.rev(w.top / "liba", "kms") == tips[dev / "liba"]
    assert w.rev(w.top / "libb", "kms") == tips[dev / "libb"]
    assert w.rev(w.top) == tips[dev]                      # the main checkout moved too
    assert "==================== top/liba: merge dev into kms ====================" in r.out
    assert f"xwt: $ git -C {w.top / 'liba'} merge --ff-only dev" in r.out
    assert r.out.rstrip().endswith("xwt: landed dev")
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 0 and "xwt: nothing to land for dev" in r.out
    assert "xwt: top/liba: kms already contains dev; nothing to land" in r.out
    assert w.xwt("status").out.count("dev/* is up-to-date with top/* (all repos)") == 1


def test_merge_skips_a_checkout_not_on_its_branch(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    w.git(dev / "liba", "checkout", "-q", "-b", "elsewhere")
    tip = w.commit(dev / "libb")
    before = w.rev(w.top / "liba", "kms")
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 0, r.all
    assert "xwt: warning: dev/liba is on elsewhere, not on dev; skipping it" in r.err
    assert w.rev(w.top / "liba", "kms") == before
    assert w.rev(w.top / "libb", "kms") == tip


def test_refuses_when_one_repo_is_not_rebased(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    w.commit(dev / "libb")
    w.commit(w.top / "libb")
    w.commit(w.top / "libb")
    before = {p: w.rev(p) for p in (w.top / "liba", w.top / "libb")}
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 1
    assert "refusing to merge" in r.err
    assert "libb: kms has 2 commits not in dev; run `xwt rebase-down` first" in r.err
    assert {p: w.rev(p) for p in before} == before
    # After rebase-down it lands.
    assert w.xwt("rebase-down", cwd=dev).rc == 0
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 0, r.all


def test_refuses_on_a_conflicting_dirty_main_checkout(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba", "liba/__init__.py", "changed\n")
    w.commit(dev / "libb", "newfile.txt", "new\n")
    (w.top / "liba" / "liba" / "__init__.py").write_text("edited in top\n")
    (w.top / "libb" / "newfile.txt").write_text("untracked in top\n")
    (w.top / "libb" / "unrelated.txt").write_text("fine\n")
    before = w.rev(w.top / "libb", "kms")
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 1
    assert ("top/liba has uncommitted changes to, or untracked copies of, files the merge "
            "changes (liba/__init__.py)") in r.err
    assert "top/libb has uncommitted changes to, or untracked copies of, files the merge " \
           "changes (newfile.txt)" in r.err
    assert w.rev(w.top / "libb", "kms") == before


def test_no_ff_makes_merge_commits(w):
    w.setup()
    dev = w.add_worktree("dev")
    tip = w.commit(dev)
    main = w.commit(w.top, "other.txt")               # main moved: not a fast-forward
    r = w.xwt("merge-up", "--no-ff", cwd=dev)
    assert r.rc == 0, r.all
    assert sorted(parents(w, w.top, "main")) == sorted([main, tip])
    assert f"xwt: $ git -C {w.top} merge --no-ff --no-edit dev" in r.out


def test_no_ff_conflict_leaves_that_repo_mid_merge(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba", "same.txt", "dev\n")
    w.commit(w.top / "liba", "same.txt", "kms\n")
    tip_b = w.commit(dev / "libb")
    r = w.xwt("merge-up", "--no-ff", cwd=dev)
    assert r.rc == 1
    assert "the merge stopped in top/liba" in r.err
    from xwt import git
    assert git.in_progress(w.top / "liba") == "merge"
    assert contains(w, w.top / "libb", tip_b, "kms")


def test_merge_dry_run(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    before = w.rev(w.top / "liba", "kms")
    r = w.xwt("merge-up", "--dry-run", cwd=dev)
    assert r.rc == 0
    assert f"xwt: (dry run) $ git -C {w.top / 'liba'} merge --ff-only dev" in r.out
    assert "landed" not in r.out
    assert w.rev(w.top / "liba", "kms") == before


def test_merge_refuses_in_the_toplevel(w):
    w.setup()
    r = w.xwt("merge-up")
    assert r.rc == 1 and "merge-up lands a worktree's branch; run it in that worktree" in r.err


def test_merge_refuses_when_a_main_checkout_is_off_branch(w):
    """The case §4.3 is about: landing on whatever happens to be checked out."""
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    w.git(w.top / "liba", "checkout", "-q", "-b", "old-experiment", "kms~1")
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 1
    assert ("top/liba is on old-experiment, but xwt.toml says kms: check out kms there, or "
            "change xwt.toml") in r.err
    assert w.rev(w.top / "liba", "old-experiment") == w.rev(w.top / "liba", "kms~1")


def test_merge_refuses_a_main_checkout_mid_merge(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    w.git(w.top / "liba", "branch", "side", "kms~1")
    w.commit(w.top / "liba", "same.txt", "kms\n")
    w.git(w.top / "liba", "checkout", "-q", "side")
    w.commit(w.top / "liba", "same.txt", "side\n")
    w.git(w.top / "liba", "checkout", "-q", "kms")
    subprocess.run(["git", "-C", w.top / "liba", "merge", "side"], capture_output=True)
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 1
    assert "top/liba has a merge in progress: finish it with `git commit` or `git merge --abort`" in r.err


def test_merge_with_a_missing_sub_repo(w):
    w.setup()
    dev = w.add_worktree("dev", subs=("liba",))
    tip = w.commit(dev / "liba")
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 0, r.all
    assert "xwt: warning: dev has no checkout of libb" in r.err
    assert w.rev(w.top / "liba", "kms") == tip


# ---------------------------------------------------------------------------
# Cases where git would refuse part way, and branch/tag name clashes
# ---------------------------------------------------------------------------

def test_merge_refuses_a_dirty_main_checkout_of_a_renamed_file(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    w.git(dev / "libb", "mv", "libb/__init__.py", "libb/renamed.py")
    w.git(dev / "libb", "commit", "-q", "-m", "rename")
    (w.top / "libb" / "libb" / "__init__.py").write_text("edited in top\n")
    before = w.rev(w.top / "liba", "kms")
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 1
    assert "top/libb has uncommitted changes to, or untracked copies of, files the merge " \
           "changes (libb/__init__.py)" in r.err
    assert w.rev(w.top / "liba", "kms") == before     # nothing landed


def test_no_ff_refuses_a_staged_index(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    w.commit(dev / "libb")
    (w.top / "libb" / "unrelated.txt").write_text("staged\n")
    w.git(w.top / "libb", "add", "unrelated.txt")
    before = w.rev(w.top / "liba", "kms")
    r = w.xwt("merge-up", "--no-ff", cwd=dev)
    assert r.rc == 1
    assert "top/libb has staged changes, and a merge commit needs a clean index" in r.err
    assert w.rev(w.top / "liba", "kms") == before
    # A fast-forward doesn't mind a staged file it doesn't touch.
    assert w.xwt("merge-up", cwd=dev).rc == 0


def test_rebase_refuses_an_untracked_file_it_would_overwrite(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    w.commit(w.top / "liba", "new.txt", "from kms\n")
    main = w.commit(w.top)
    (dev / "liba" / "new.txt").write_text("untracked here\n")
    r = w.xwt("rebase-down", cwd=dev)
    assert r.rc == 1
    assert ("dev/liba has untracked files that the rebase would overwrite (new.txt); move or "
            "remove them first") in r.err
    assert not contains(w, dev, main, "dev")


def test_a_step_that_fails_without_starting_says_so(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(w.top / "liba")
    r = w.xwt("rebase-down", "--", "--no-such-option", cwd=dev)
    assert r.rc == 1
    assert ("`git rebase` failed in dev/liba (see git's message above); nothing was changed "
            "there") in r.err
    assert "resolve the conflicts" not in r.err


def test_tags_named_like_branches(w):
    w.setup()
    dev = w.add_worktree("dev")
    old_a = w.rev(w.top / "liba", "kms")
    w.git(w.top / "liba", "tag", "dev", old_a)        # a tag named like the worktree's branch
    w.git(w.top / "liba", "tag", "kms", old_a)        # and one like the integration branch
    w.commit(dev / "liba")
    kms_a = w.commit(w.top / "liba")
    r = w.xwt("status", cwd=dev)
    assert "heads/" not in r.out and "WARNING" not in r.out, r.out
    r = w.xwt("rebase-down", cwd=dev)
    assert r.rc == 0, r.all
    assert f"xwt: $ git -C {dev / 'liba'} rebase refs/heads/kms" in r.out
    assert contains(w, dev / "liba", kms_a, "refs/heads/dev")
    tip = w.rev(dev / "liba")
    r = w.xwt("merge-up", cwd=dev)
    assert r.rc == 0, r.all
    assert w.rev(w.top / "liba", "refs/heads/kms") == tip
    r = w.xwt("new", "dev2")
    assert r.rc == 0, r.all
    assert w.rev(w.group / "dev2" / "liba") == tip     # branched from the branch kms, not the tag

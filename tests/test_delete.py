import os
import shutil
import subprocess


def branches(w, name):
    return [r for r in (w.top, w.top / "liba", w.top / "libb")
            if w.git(r, "branch", "--list", name)]


def gone(w, name):
    return (not (w.group / name).exists()
            and all(not line.split()[0].startswith(str(w.group / name) + os.sep)
                    and line.split()[0] != str(w.group / name)
                    for r in (w.top, w.top / "liba", w.top / "libb")
                    for line in w.git(r, "worktree", "list").splitlines()))


def test_deletes_a_merged_worktree(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    assert w.xwt("merge-up", cwd=dev).rc == 0
    r = w.xwt("delete", "dev")
    assert r.rc == 0, r.all
    assert gone(w, "dev") and branches(w, "dev") == []
    assert f"xwt: $ git -C {w.top / 'liba'} worktree remove --force {dev / 'liba'}" in r.out
    assert f"xwt: $ git -C {w.top} branch -d dev" in r.out
    lines = [l for l in r.out.splitlines() if "worktree remove" in l]
    assert lines[-1].endswith(f"worktree remove --force {dev}")   # the toplevel's last
    assert r.out.rstrip().endswith("xwt: deleted worktree dev")


def test_behind_only_is_fine(w):
    w.setup()
    w.add_worktree("dev")
    w.commit(w.top / "liba")
    w.commit(w.top)
    r = w.xwt("delete", "dev")
    assert r.rc == 0, r.all
    assert gone(w, "dev") and branches(w, "dev") == []


def test_each_safety_check_refuses(w):
    w.setup()
    dev = w.add_worktree("dev")
    (dev / "liba" / "liba" / "__init__.py").write_text("changed\n")
    (dev / "libb" / "stray.txt").write_text("stray\n")
    w.commit(dev / "libb")
    w.commit(dev, "same.txt", "dev\n")
    w.git(dev, "branch", "side", "HEAD~1")
    w.git(dev, "checkout", "-q", "side")
    w.commit(dev, "same.txt", "side\n")
    w.git(dev, "checkout", "-q", "dev")
    subprocess.run(["git", "-C", dev, "merge", "side"], capture_output=True)
    r = w.xwt("delete", "dev")
    assert r.rc == 1
    err = r.err
    assert "refusing to delete dev" in err
    assert "dev/liba has uncommitted changes to tracked files: liba/__init__.py" in err
    assert "dev/libb has untracked files: stray.txt" in err
    assert "dev/libb: 1 commit on dev not in kms; run `xwt merge-up` first" in err
    assert "dev/ (repo proj) has a merge in progress" in err
    assert "with --force these are lost; committed work stays on its branches" in err
    assert dev.exists() and len(branches(w, "dev")) == 3


def test_rebase_in_progress_and_detached_commits_refuse(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba", "same.txt", "dev\n")
    w.commit(w.top / "liba", "same.txt", "kms\n")
    subprocess.run(["git", "-C", dev / "liba", "rebase", "kms"], capture_output=True)
    w.git(dev / "libb", "checkout", "-q", "--detach")
    w.commit(dev / "libb")
    r = w.xwt("delete", "dev")
    assert r.rc == 1
    assert "dev/liba has a rebase in progress" in r.err
    assert "dev/libb is detached at a commit that no branch contains" in r.err


def test_force_keeps_unmerged_branches(w):
    w.setup()
    dev = w.add_worktree("dev")
    tip = w.commit(dev / "libb")
    (dev / "liba" / "liba" / "__init__.py").write_text("lost\n")
    (dev / "stray.txt").write_text("lost\n")
    r = w.xwt("delete", "dev", "--force")
    assert r.rc == 0, r.all
    assert "xwt: warning: deleting dev anyway (--force):" in r.err
    assert gone(w, "dev")
    assert "xwt: warning: kept branch dev in libb: not merged into kms; its commits are preserved" in r.err
    assert branches(w, "dev") == [w.top / "libb"]
    assert w.rev(w.top / "libb", "dev") == tip
    # The kept branch is reused by the next `xwt new dev`.
    r = w.xwt("new", "dev")
    assert r.rc == 0 and "reusing existing branch dev in libb: 1 commit ahead of kms" in r.out


def test_ignored_files_symlinks_and_submodules(w, tmp_path):
    w.setup()
    dev = w.add_worktree("dev")
    with open(w.top / ".git" / "info" / "exclude", "a") as f:
        f.write("/research\n/.venv/\n")
    with open(w.top / "liba" / ".git" / "info" / "exclude", "a") as f:
        f.write("build/\n")
    (dev / ".venv" / "bin").mkdir(parents=True)
    (dev / ".venv" / "bin" / "python").write_text("")
    (dev / "liba" / "build").mkdir()
    (dev / "liba" / "build" / "lib.so").write_text("")
    outside = tmp_path / "scratch"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep\n")
    (dev / "research").symlink_to(outside)
    assert (dev / "libb" / "extern" / "dep" / "dep.txt").exists()   # an initialized submodule
    r = w.xwt("delete", "dev")
    assert r.rc == 0, r.all
    assert gone(w, "dev")
    assert (outside / "keep.txt").read_text() == "keep\n"
    assert ".venv" not in r.out and "build" not in r.out


def test_refuses_from_inside_and_for_the_toplevel(w):
    w.setup()
    dev = w.add_worktree("dev")
    r = w.xwt("delete", "dev", cwd=dev / "liba")
    assert r.rc == 1 and f"the current directory is inside {dev}" in r.err
    r = w.xwt("delete", "top")
    assert r.rc == 1 and "is the toplevel; it can't be deleted" in r.err
    r = w.xwt("delete", "nope")
    assert r.rc == 1 and "doesn't exist and isn't a worktree" in r.err
    (w.group / "extern").mkdir()
    r = w.xwt("delete", "extern")
    assert r.rc == 1 and f"{w.group / 'extern'} isn't a worktree of {w.top}" in r.err
    # From another worktree is fine.
    dev2 = w.add_worktree("dev2")
    assert w.xwt("delete", "dev", cwd=dev2).rc == 0 and gone(w, "dev")


def test_integration_branches_are_checked_even_with_force(w):
    w.setup()
    w.add_worktree("dev")
    w.git(w.top / "liba", "checkout", "-q", "main")
    r = w.xwt("delete", "dev", "--force")
    assert r.rc == 1 and "top/liba is on main, but xwt.toml says kms" in r.err
    assert (w.group / "dev").exists()


def test_a_hand_deleted_directory_is_cleaned_up(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba")
    shutil.rmtree(dev)
    r = w.xwt("delete", "dev")
    assert r.rc == 0, r.all
    assert f"xwt: {dev} is already gone" in r.out
    assert f"xwt: $ git -C {w.top} worktree prune" in r.out
    assert gone(w, "dev")
    assert branches(w, "dev") == [w.top / "liba"]
    assert "kept branch dev in liba: not merged into kms" in r.err


def test_a_missing_sub_repo_is_skipped_silently(w):
    w.setup()
    w.add_worktree("dev", subs=("liba",))
    r = w.xwt("delete", "dev")
    assert r.rc == 0, r.all
    assert r.err == "" and gone(w, "dev")


def test_a_separate_clone_inside_refuses_even_with_force(w):
    w.setup()
    dev = w.add_worktree("dev", subs=("liba",))
    w.git(dev, "clone", "-q", w.url("libb"), dev / "libb")
    for args in (["delete", "dev"], ["delete", "dev", "--force"]):
        r = w.xwt(*args)
        assert r.rc == 1
        assert f"{dev / 'libb'} is a separate git repository, not a worktree of {w.top / 'libb'}" in r.err
    assert (dev / "libb" / ".git").is_dir()


def test_a_locked_worktree(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.git(w.top / "liba", "worktree", "lock", dev / "liba")
    r = w.xwt("delete", "dev")
    assert r.rc == 1 and "dev/liba is locked" in r.err
    r = w.xwt("delete", "dev", "--force")
    assert r.rc == 0, r.all
    assert gone(w, "dev")

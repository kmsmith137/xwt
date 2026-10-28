import shutil
import subprocess

from xwt.envrc import envrc_text
from xwt.layout import Project


def warnings_of(r):
    return [line[len("WARNING: "):] for line in r.out.splitlines() if line.startswith("WARNING: ")]


def relations_of(r):
    text = r.out.split("==================== branch relations ====================\n", 1)
    if len(text) == 1:
        return []
    return text[1].split("\n\n\n", 1)[0].rstrip("\n").split("\n")


def test_clean_toplevel(w):
    w.setup()
    w.add_worktree("dev")
    r = w.xwt("status")
    assert r.rc == 0, r.all
    lines = r.out.splitlines()
    assert lines[0] == f"toplevel {w.top}: proj main, liba kms, libb kms"
    assert "==================== top/ (repo proj) ====================" in lines
    assert "==================== top/liba ====================" in lines
    assert "==================== top/libb ====================" in lines
    assert "On branch kms" in r.out
    assert relations_of(r) == ["dev/* is up-to-date with top/* (all repos)"]
    assert warnings_of(r) == []


def test_relations_from_toplevel_and_worktree(w):
    w.setup()
    dev = w.add_worktree("dev")
    dev2 = w.add_worktree("dev2")
    dev3 = w.add_worktree("dev3")
    dev4 = w.add_worktree("dev4")
    w.commit(dev / "liba")
    w.commit(dev / "liba")
    w.commit(dev3 / "libb")
    # Moving the integration branches puts every worktree behind, so bring
    # dev, dev3 and dev4 up to date again afterwards.
    w.commit(w.top)
    w.commit(w.top / "libb")
    for wt in (dev, dev4):
        w.git(wt, "rebase", "-q", "main")
        w.git(wt / "libb", "rebase", "-q", "kms")
    w.git(dev3, "rebase", "-q", "main")
    r = w.xwt("status")
    assert relations_of(r) == [
        "dev/liba is 2 commits ahead of top/liba (kms branch)",
        "",
        "dev2/ is 1 commit behind top/ (repo proj, main branch)",
        "dev2/libb is 1 commit behind top/libb (kms branch)",
        "",
        "dev3/libb is 1 commit ahead and 1 commit behind top/libb (kms branch)",
        "",
        "dev4/* is up-to-date with top/* (all repos)",
    ]
    r = w.xwt("status", cwd=dev2 / "libb")
    assert r.out.splitlines()[2] == "==================== dev2/ (repo proj) ===================="
    assert relations_of(r) == [
        "dev2/ is 1 commit behind top/ (repo proj, main branch)",
        "dev2/libb is 1 commit behind top/libb (kms branch)",
    ]
    assert r.rc == 0


def test_args_pass_through_and_exit_status(w):
    w.setup()
    r = w.xwt("status", "-s")
    assert r.rc == 0
    assert "?? xwt.toml" in r.out and "On branch" not in r.out
    r = w.xwt("status", "--no-such-option")
    assert r.rc == 129


def test_main_checkout_off_its_integration_branch(w):
    w.setup()
    w.git(w.top / "liba", "checkout", "-q", "-b", "old-experiment")
    w.git(w.top / "libb", "checkout", "-q", "--detach")
    r = w.xwt("status")
    assert r.rc == 1
    assert warnings_of(r) == [
        "top/liba is on branch 'old-experiment', expected its integration branch 'kms'",
        "top/libb is on a detached HEAD, expected its integration branch 'kms'",
    ]
    assert "\n\nWARNING: top/liba" in r.out


def test_repo_without_branch_follows_its_checkout(w):
    w.setup(toml=w.toml(branch=None).replace('branch = "kms"\n', "", 1))
    w.git(w.top, "checkout", "-q", "-b", "trunk")
    r = w.xwt("status")
    assert r.out.splitlines()[0] == f"toplevel {w.top}: proj trunk, liba kms, libb kms"
    assert warnings_of(r) == []
    w.git(w.top, "checkout", "-q", "--detach")
    w.git(w.top / "liba", "checkout", "-q", "--detach")
    r = w.xwt("status")
    assert warnings_of(r) == [
        "top/ (repo proj) is on a detached HEAD, and xwt.toml names no integration branch "
        "for it",
        "top/liba is on a detached HEAD, and xwt.toml names no integration branch for it",
    ]


def test_worktree_checkout_on_wrong_branch(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.git(dev / "liba", "checkout", "-q", "-b", "tmp")
    w.git(dev / "libb", "checkout", "-q", "--detach")
    for cwd in (w.top, dev):
        r = w.xwt("status", cwd=cwd)
        assert warnings_of(r) == [
            "dev/liba is on branch 'tmp', expected 'dev'",
            "dev/libb is on a detached HEAD, expected 'dev'",
        ]


def test_operations_in_progress(w):
    w.setup()
    dev = w.add_worktree("dev")
    w.commit(dev / "liba", "same.txt", "dev\n")
    w.commit(w.top / "liba", "same.txt", "top\n")
    subprocess.run(["git", "-C", dev / "liba", "rebase", "kms"], capture_output=True)
    w.commit(dev / "libb", "same.txt", "dev\n")
    w.git(dev / "libb", "branch", "other", "HEAD~1")
    w.git(dev / "libb", "checkout", "-q", "other")
    w.commit(dev / "libb", "same.txt", "other\n")
    w.git(dev / "libb", "checkout", "-q", "dev")
    subprocess.run(["git", "-C", dev / "libb", "merge", "other"], capture_output=True)
    w.commit(dev, "same.txt", "dev\n")
    w.git(dev, "branch", "pick", "HEAD~1")
    w.git(dev, "checkout", "-q", "pick")
    w.commit(dev, "same.txt", "pick\n")
    w.git(dev, "checkout", "-q", "dev")
    subprocess.run(["git", "-C", dev, "cherry-pick", "pick"], capture_output=True)
    r = w.xwt("status", cwd=dev)
    ws = warnings_of(r)
    assert any(x.startswith("dev/ (repo proj) has a cherry-pick in progress") for x in ws), ws
    assert any(x.startswith("dev/liba has a rebase in progress (finish it with "
                            "`git rebase --continue` or `--abort`") for x in ws), ws
    assert any(x.startswith("dev/libb has a merge in progress") for x in ws), ws
    # Mid-rebase, HEAD is detached; that isn't reported as a wrong branch.
    assert not any("dev/liba is on" in x for x in ws), ws


def test_missing_and_foreign_sub_repos(w):
    w.setup()
    dev = w.add_worktree("dev")
    dev2 = w.add_worktree("dev2")
    # libc added to xwt.toml after the worktrees were made; libd not cloned yet.
    w.git(w.top, "clone", "-q", "-b", "kms", w.url("liba"), w.top / "libc")
    text = w.toml() + f'\n[repo.libc]\nurl = "{w.url("liba")}"\n'
    text += f'\n[repo.libd]\nurl = "{w.url("liba")}"\n'
    w.write_toml(text)
    with open(w.top / ".gitignore", "a") as f:
        f.write("/libc/\n/libd/\n")
    shutil.rmtree(dev2 / "libc", ignore_errors=True)
    (dev2 / "libc").mkdir()
    r = w.xwt("status")
    assert warnings_of(r) == [
        f"libd is in xwt.toml but not cloned in the toplevel; run `xwt init` in {w.top}",
        f"dev has no checkout of libc; run `xwt init` in {dev} to add it",
        f"{dev2 / 'libc'} is not a git worktree of {w.top / 'libc'}",
    ]
    assert r.rc == 1


def test_sub_repo_not_ignored(w):
    w.setup(ignore=False)
    r = w.xwt("status")
    assert warnings_of(r) == [
        f"/liba/ is not ignored by the toplevel repo; run `xwt init` in {w.top} to add it "
        f"to .gitignore",
        f"/libb/ is not ignored by the toplevel repo; run `xwt init` in {w.top} to add it "
        f"to .gitignore",
    ]


def test_stale_worktree(w):
    w.setup()
    gone = w.add_worktree("gone")
    shutil.rmtree(gone)
    r = w.xwt("status")
    assert warnings_of(r) == [
        f"git lists the worktree {gone}, but its directory is gone; run `xwt delete gone`"]


def test_envrc_and_venv_warnings(w):
    w.setup(extra='\n[envrc]\n[envrc.venv]\n')
    dev = w.add_worktree("dev")
    with open(w.top / ".gitignore", "a") as f:
        f.write("/.envrc\n/.direnv/\n/.venv/\n")
    project = Project.find(w.top)
    (w.top / ".envrc").write_text(envrc_text(project))
    (w.top / ".venv").mkdir()
    (dev / ".envrc").write_text("# old\n")
    r = w.xwt("status")
    assert warnings_of(r) == [
        f"{dev / '.envrc'} differs from what xwt.toml now produces; run `xwt init` in {dev}",
        f"dev has no .venv; run `xwt venv` in {dev}",
    ]
    (dev / ".envrc").unlink()
    r = w.xwt("status", cwd=dev)
    assert f"dev has no .envrc; run `xwt init` in {dev}" in warnings_of(r)


def test_worktree_xwt_toml_differs(w):
    w.setup(commit_toml=True)
    dev = w.add_worktree("dev")
    r = w.xwt("status", cwd=dev)
    assert warnings_of(r) == []
    (dev / "xwt.toml").write_text(w.toml() + "# changed\n")
    r = w.xwt("status", cwd=dev)
    assert warnings_of(r) == [
        f"{dev / 'xwt.toml'} differs from the toplevel's; xwt uses only the toplevel's "
        f"({w.top / 'xwt.toml'}), so a change takes effect when it is merged up"]


def test_diff_headers_and_pass_through(w):
    w.setup()
    dev = w.add_worktree("dev")
    (dev / "liba" / "liba" / "__init__.py").write_text("NAME = 'changed'\n")
    r = w.xwt("diff", "--stat", cwd=dev)
    assert r.rc == 0
    assert r.out.split("\n\n")[0].splitlines() == [
        "==================== dev/ (repo proj) ====================", "(no output)"]
    assert "==================== dev/liba ====================\n liba/__init__.py | 2 +-" in r.out
    r = w.xwt("diff", "--exit-code", "--quiet", cwd=dev)
    assert r.rc == 1


def test_diff_reminds_about_missing_checkout(w):
    w.setup()
    dev = w.add_worktree("dev", subs=("liba",))
    r = w.xwt("diff", cwd=dev)
    assert r.rc == 0
    assert (f"xwt: warning: dev has no checkout of libb (added to xwt.toml after dev was "
            f"created); run `xwt init` in {dev} to add it") in r.err
    assert "dev/libb" not in r.out and "dev/liba" in r.out

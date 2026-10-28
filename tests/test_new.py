import shutil

import pytest


def test_creates_every_checkout_from_its_integration_branch(w):
    w.setup()
    r = w.xwt("new", "dev")
    assert r.rc == 0, r.all
    dev = w.group / "dev"
    for path in (dev, dev / "liba", dev / "libb"):
        assert w.branch(path) == "dev"
    assert w.rev(dev) == w.rev(w.top, "main")
    assert w.rev(dev / "liba") == w.rev(w.top / "liba", "kms")
    assert w.rev(dev / "libb") == w.rev(w.top / "libb", "kms")
    assert (dev / "libb" / "extern" / "dep" / "dep.txt").exists()
    assert f"xwt: $ git -C {w.top} worktree add {dev} -b dev main" in r.out
    assert f"xwt: $ git -C {w.top / 'liba'} worktree add {dev / 'liba'} -b dev kms" in r.out
    assert f"xwt: worktree dev is ready: {dev}\n  cd {dev}\n" in r.out
    assert w.direnv_calls() == []
    # Run from inside a worktree too.
    r = w.xwt("new", "dev2", cwd=dev / "liba")
    assert r.rc == 0 and w.branch(w.group / "dev2" / "liba") == "dev2"
    assert w.xwt("status").rc == 0


def test_reuses_an_existing_branch(w):
    w.setup()
    w.git(w.top / "liba", "branch", "dev", "kms~1")
    w.git(w.top / "libb", "branch", "dev", "kms")
    r = w.xwt("new", "dev")
    assert r.rc == 0, r.all
    assert "xwt: reusing existing branch dev in liba: 1 commit behind kms" in r.out
    assert "xwt: reusing existing branch dev in libb: up to date with kms" in r.out
    assert f"worktree add {w.group / 'dev' / 'liba'} dev\n" in r.out
    assert w.rev(w.group / "dev" / "liba") == w.rev(w.top / "liba", "kms~1")


def nothing_created(w, name):
    assert not (w.group / name).exists()
    for repo in (w.top, w.top / "liba", w.top / "libb"):
        assert w.git(repo, "branch", "--list", name) == ""


@pytest.mark.parametrize("name", ["-x", ".x", "a/b", "a..b", "x.lock", "a b", ""])
def test_refuses_a_bad_name(w, name):
    w.setup()
    r = w.xwt("new", "--", name)
    assert r.rc == 1 and "isn't a valid worktree name" in r.err
    assert [p.name for p in w.group.iterdir()] == ["top"]


def test_refuses_an_existing_directory(w):
    w.setup()
    (w.group / "dev").mkdir()
    r = w.xwt("new", "dev")
    assert r.rc == 1 and f"{w.group / 'dev'} already exists" in r.err
    for repo in (w.top, w.top / "liba"):
        assert w.git(repo, "branch", "--list", "dev") == ""


def test_refuses_a_detached_main_checkout(w):
    w.setup()
    w.git(w.top / "libb", "checkout", "-q", "--detach")
    r = w.xwt("new", "dev")
    assert r.rc == 1
    assert ("refusing to create" in r.err and
            "top/libb is detached, but xwt.toml says kms: check out kms there, or change "
            "xwt.toml" in r.err)
    nothing_created(w, "dev")


def test_refuses_a_main_checkout_on_another_branch_even_with_branch_unset(w):
    w.setup(toml=w.toml().replace('branch = "kms"', "", 1))
    w.git(w.top / "liba", "checkout", "-q", "--detach")
    r = w.xwt("new", "dev")
    assert r.rc == 1 and "top/liba is detached, and xwt.toml names no integration branch" in r.err
    nothing_created(w, "dev")


def test_repo_without_branch_follows_its_checkout(w):
    w.setup(toml=w.toml().replace('branch = "kms"', "", 1))
    w.git(w.top / "liba", "checkout", "-q", "main")
    r = w.xwt("new", "dev")
    assert r.rc == 0, r.all
    assert w.rev(w.group / "dev" / "liba") == w.rev(w.top / "liba", "main")


def test_refuses_an_integration_branch_name(w):
    w.setup()
    r = w.xwt("new", "kms")
    assert r.rc == 1
    assert "kms is the integration branch of top/liba; pick another name" in r.err
    assert "kms is the integration branch of top/libb" in r.err
    assert not (w.group / "kms").exists()


def test_refuses_a_branch_checked_out_elsewhere(w):
    w.setup()
    other = w.tmp / "elsewhere"
    w.git(w.top / "libb", "worktree", "add", "-q", other, "-b", "dev")
    r = w.xwt("new", "dev")
    assert r.rc == 1 and f"branch dev is already checked out in {other}" in r.err
    assert not (w.group / "dev").exists()
    assert w.git(w.top, "branch", "--list", "dev") == ""


def test_refuses_an_uncloned_sub_repo(w):
    w.setup()
    w.write_toml(w.toml() + f'\n[repo.libc]\nurl = "{w.url("liba")}"\n')
    r = w.xwt("new", "dev")
    assert r.rc == 1 and "libc is in xwt.toml but not cloned in the toplevel; run `xwt init`" in r.err
    nothing_created(w, "dev")


def test_refuses_in_home(w, monkeypatch):
    w.setup()
    monkeypatch.setenv("HOME", str(w.group))
    r = w.xwt("new", "dev")
    assert r.rc == 1 and "directly in $HOME" in r.err


def test_prunes_a_stale_entry_from_a_hand_deleted_directory(w):
    w.setup()
    w.add_worktree("dev")
    shutil.rmtree(w.group / "dev")
    r = w.xwt("new", "dev")
    assert r.rc == 0, r.all
    assert f"xwt: $ git -C {w.top} worktree prune" in r.out
    assert "reusing existing branch dev in proj" in r.out
    assert w.branch(w.group / "dev" / "liba") == "dev"


def test_failure_in_worktree_add_says_what_was_created(w, monkeypatch):
    w.setup()
    from xwt import git
    real = git.git_echo

    def failing(path, *args):
        if str(path).endswith("libb") and args[:2] == ("worktree", "add"):
            return 128
        return real(path, *args)
    monkeypatch.setattr(git, "git_echo", failing)
    r = w.xwt("new", "dev")
    assert r.rc == 1
    assert f"created so far: {w.group / 'dev'}, {w.group / 'dev' / 'liba'}" in r.err
    assert "`xwt delete dev --force` removes them" in r.err


def test_direnv_allow_runs_with_envrc(w):
    w.setup(extra='\n[envrc.export]\nFOO = "1"\n')
    r = w.xwt("new", "dev")
    assert r.rc == 0, r.all
    dev = w.group / "dev"
    assert (dev / ".envrc").exists()
    assert w.direnv_calls() == [f"allow {dev}"]


def test_direnv_absent(w):
    w.setup(extra='\n[envrc.export]\nFOO = "1"\n')
    w.set_path(direnv=False)
    r = w.xwt("new", "dev")
    assert r.rc == 0, r.all
    assert (w.group / "dev" / ".envrc").exists() and w.direnv_calls() == []


def test_a_projects_own_envrc_is_not_allowed(w):
    w.setup()
    w.commit(w.top, ".envrc", "export OWN=1\n", "the project's own .envrc")
    r = w.xwt("new", "dev")
    assert r.rc == 0, r.all
    assert (w.group / "dev" / ".envrc").read_text() == "export OWN=1\n"
    assert w.direnv_calls() == []


def test_refuses_envrc_section_with_a_tracked_envrc(w):
    w.setup(extra='\n[envrc]\n')
    w.commit(w.top, ".envrc", "export OWN=1\n", "the project's own .envrc")
    r = w.xwt("new", "dev")
    assert r.rc == 1
    assert "xwt.toml has [envrc], but .envrc is tracked in the repo" in r.err
    assert not (w.group / "dev").exists()
    r = w.xwt("init")
    assert r.rc == 1 and "remove one of them" in r.err


def test_refuses_when_a_branch_name_slash_something_exists(w):
    w.setup()
    w.git(w.top / "libb", "branch", "dev/foo", "kms")
    r = w.xwt("new", "dev")
    assert r.rc == 1
    assert "libb has a branch dev/foo, so it can't have a branch dev" in r.err
    nothing_created(w, "dev")


def test_lists_every_problem(w):
    w.setup()
    (w.group / "kms").mkdir()
    r = w.xwt("new", "kms")
    assert r.rc == 1
    assert f"{w.group / 'kms'} already exists" in r.err
    assert "kms is the integration branch of top/liba" in r.err

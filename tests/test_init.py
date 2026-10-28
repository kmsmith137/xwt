from pathlib import Path


def test_clones_missing_sub_repos_and_sets_up_the_toplevel(w):
    w.clone_top()
    # liba names its branch; libb doesn't, so it gets the remote's default (main).
    w.write_toml(f'branch = "main"\n\n[repo.liba]\nurl = "{w.url("liba")}"\nbranch = "kms"\n\n'
                 f'[repo.libb]\nurl = "{w.url("libb")}"\n')
    r = w.xwt("init")
    assert r.rc == 0, r.all
    assert w.branch(w.top / "liba") == "kms"
    assert w.branch(w.top / "libb") == "main"
    assert f"xwt: $ git clone -b kms {w.url('liba')} {w.top / 'liba'}" in r.out
    assert f"xwt: $ git clone {w.url('libb')} {w.top / 'libb'}" in r.out
    # Submodules are initialized.
    assert (w.top / "libb" / "extern" / "dep" / "dep.txt").exists()
    # .gitignore created, one informational line per addition, then the reminder.
    assert (w.top / ".gitignore").read_text() == "# added by xwt\n/liba/\n/libb/\n"
    assert f"xwt: added /liba/ to {w.top / '.gitignore'}" in r.out
    assert f"xwt: added /libb/ to {w.top / '.gitignore'}" in r.out
    assert f"xwt: commit .gitignore in {w.top}" in r.out
    # No [envrc]: no .envrc, no direnv.
    assert not (w.top / ".envrc").exists() and w.direnv_calls() == []


def test_idempotent(w):
    w.clone_top()
    w.write_toml(w.toml())
    assert w.xwt("init").rc == 0
    before = (w.top / ".gitignore").read_text()
    r = w.xwt("init")
    assert r.rc == 0, r.all
    assert "git clone" not in r.out and "added" not in r.out and "commit" not in r.out
    assert (w.top / ".gitignore").read_text() == before
    assert r.err == ""


def test_gitignore_appends_only_what_is_missing(w):
    w.clone_top()
    w.commit(w.top, ".gitignore", "*.pyc\nliba\n", "ignore")
    w.write_toml(w.toml())
    r = w.xwt("init")
    assert r.rc == 0
    assert (w.top / ".gitignore").read_text() == "*.pyc\nliba\n\n# added by xwt\n/libb/\n"
    assert "added /liba/" not in r.out


def test_leaves_existing_clones_and_warns(w):
    w.setup()
    w.write_toml(w.toml().replace(w.url("liba"), "github:someone/liba"))
    w.git(w.top / "libb", "checkout", "-q", "main")
    (w.top / "liba" / "untracked").write_text("x")
    r = w.xwt("init")
    assert r.rc == 0, r.all
    assert ("xwt: warning: top/liba's origin is " + w.url("liba") +
            ", but xwt.toml says github:someone/liba; leaving it as it is") in r.err
    assert ("xwt: warning: top/libb is on main, but xwt.toml says kms: check out kms there, "
            "or change xwt.toml") in r.err
    assert w.branch(w.top / "libb") == "main"     # init never switches branches
    assert (w.top / "liba" / "untracked").exists()


def test_warns_about_a_directory_that_is_not_a_clone(w):
    w.clone_top()
    w.write_toml(w.toml(subs=("liba",)))
    (w.top / "liba").mkdir()
    r = w.xwt("init")
    assert r.rc == 0
    assert f"xwt: warning: {w.top / 'liba'} exists but is not a git clone; leaving it alone" in r.err


def test_refuses_in_home(w, monkeypatch):
    w.clone_top()
    w.write_toml(w.toml())
    monkeypatch.setenv("HOME", str(w.group))
    r = w.xwt("init")
    assert r.rc == 1
    assert "is directly in $HOME" in r.err
    assert not (w.top / "liba").exists()


def test_clone_failure_reports(w):
    w.clone_top()
    w.write_toml(f'[repo.nope]\nurl = "{w.tmp / "missing.git"}"\n')
    r = w.xwt("init")
    assert r.rc == 1 and "`git clone` of" in r.err and "failed" in r.err


def test_in_a_worktree_adds_a_newly_listed_sub_repo(w):
    w.setup(subs=("liba",))
    dev = w.add_worktree("dev", subs=("liba",))
    # libb is added to xwt.toml later, and cloned in the toplevel.
    w.write_toml(w.toml())
    r = w.xwt("init")
    assert r.rc == 0, r.all
    r = w.xwt("status", cwd=dev)
    assert f"dev has no checkout of libb; run `xwt init` in {dev} to add it" in r.out
    r = w.xwt("init", cwd=dev)
    assert r.rc == 0, r.all
    assert f"git -C {w.top / 'libb'} worktree add {dev / 'libb'} -b dev kms" in r.out
    assert w.branch(dev / "libb") == "dev"
    assert (dev / "libb" / "extern" / "dep" / "dep.txt").exists()
    assert w.xwt("status", cwd=dev).rc == 0


def test_in_a_worktree_refuses_when_the_main_checkout_is_off_branch(w):
    w.setup(subs=("liba",))
    dev = w.add_worktree("dev", subs=("liba",))
    w.write_toml(w.toml())
    w.xwt("init")
    w.git(w.top / "libb", "checkout", "-q", "main")
    r = w.xwt("init", cwd=dev)
    assert r.rc == 1
    assert "top/libb is on main, but xwt.toml says kms" in r.err
    assert not (dev / "libb").exists()


def test_rerun_after_an_edit_rewrites_envrc(w):
    w.setup(extra='\n[envrc.export]\nFOO = "1"\n')
    r = w.xwt("init")
    assert r.rc == 0, r.all
    envrc = w.top / ".envrc"
    assert 'export FOO="1"' in envrc.read_text()
    assert w.direnv_calls() == [f"allow {w.top}"]
    assert f"xwt: wrote {envrc}" in r.out
    w.write_toml(w.toml(extra='\n[envrc.export]\nFOO = "2"\n'))
    r = w.xwt("init")
    assert 'export FOO="2"' in envrc.read_text()
    assert f"xwt: wrote {envrc}" in r.out
    r = w.xwt("init")
    assert "wrote" not in r.out                     # written only when it changes
    assert "/.envrc" in (w.top / ".gitignore").read_text()
    assert "/.venv/" not in (w.top / ".gitignore").read_text()

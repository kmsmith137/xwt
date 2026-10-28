from xwt.layout import NOT_FOUND, Project, find_workspace


def test_discovery_from_each_place(w):
    w.setup()
    dev = w.add_worktree("dev")
    cases = {
        w.top: (w.top, w.top),
        w.top / "liba": (w.top, w.top),               # a sub-repo's main checkout
        w.top / "liba" / "liba": (w.top, w.top),      # a subdirectory of it
        dev: (w.top, dev),
        dev / "liba": (w.top, dev),                   # a sub-repo checkout in a worktree
        dev / "liba" / "liba": (w.top, dev),
        dev / "libb" / "extern" / "dep": (w.top, dev),  # a submodule
        w.top / "libb" / "extern" / "dep": (w.top, w.top),
    }
    for cwd, want in cases.items():
        assert find_workspace(cwd) == want, cwd


def test_sub_repo_with_its_own_xwt_toml_does_not_capture_the_workspace(w):
    w.setup()
    dev = w.add_worktree("dev")
    (w.top / "liba" / "xwt.toml").write_text("")
    assert find_workspace(dev / "liba" / "liba") == (w.top, dev)


def test_outside_any_toplevel(w):
    w.setup()
    r = w.xwt("status", cwd=w.group)
    assert r.rc == 1 and NOT_FOUND in r.err
    plain = w.tmp / "plain"
    plain.mkdir()
    w.git(plain, "init", "-q")
    r = w.xwt("status", cwd=plain)
    assert r.rc == 1 and "not inside an xwt toplevel or worktree" in r.err


def test_repo_without_xwt_toml_is_not_a_toplevel(w):
    w.clone_top()
    assert find_workspace(w.top) is None


def test_git_worktree_outside_the_group_dir_is_not_a_worktree(w):
    w.setup()
    dev = w.add_worktree("dev")
    elsewhere = w.top / ".claude" / "worktrees" / "agent"
    w.git(w.top, "worktree", "add", "-q", elsewhere, "-b", "agent", "main")
    # From inside it, the walk goes on up to the toplevel that contains it.
    assert find_workspace(elsewhere) == (w.top, w.top)
    project = Project.find(w.top)
    assert [wt.name for wt in project.worktrees()] == ["dev"]
    assert project.worktrees()[0].path == dev


def test_walk_stops_at_home(w, monkeypatch):
    w.setup()
    # The toplevel is above $HOME: a walk from inside $HOME never reaches it.
    inner = w.top / "sub" / "home"
    inner.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(inner))
    assert find_workspace(inner) is None
    assert find_workspace(w.top / "sub") == (w.top, w.top)


def test_checkout_states(w):
    w.setup()
    dev = w.add_worktree("dev", subs=("liba",))
    (dev / "libb").mkdir()
    project = Project.find(dev)
    states = {c.label: c.state for c in project.checkouts(dev)}
    assert states == {"dev/ (repo proj)": "ok", "dev/liba": "ok", "dev/libb": "foreign"}
    (dev / "libb").rmdir()
    assert project.checkouts(dev)[2].state == "missing"


def test_labels(w):
    w.setup()
    project = Project.find(w.top)
    assert [c.label for c in project.checkouts(w.top)] == [
        "top/ (repo proj)", "top/liba", "top/libb"]


def test_sub_repo_main_checkout_with_its_own_xwt_toml(w):
    w.setup()
    (w.top / "liba" / "xwt.toml").write_text("")
    assert find_workspace(w.top / "liba" / "liba") == (w.top, w.top)
    # A clone the toplevel doesn't list, with its own xwt.toml, is its own toplevel.
    w.git(w.top, "clone", "-q", w.url("dep"), w.top / "other")
    (w.top / "other" / "xwt.toml").write_text("")
    assert find_workspace(w.top / "other") == (w.top / "other", w.top / "other")

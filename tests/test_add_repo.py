import pytest

from xwt.git import url_basename


@pytest.mark.parametrize("url, name", [
    ("github:kmsmith137/chordutils", "chordutils"),
    ("git@host:me/chordutils.git", "chordutils"),
    ("https://github.com/me/chordutils.git", "chordutils"),
    ("/path/to/chordutils/", "chordutils"),
    ("host:chordutils", "chordutils"),
])
def test_name_from_url(url, name):
    assert url_basename(url) == name


def next_steps(r):
    return r.out.split("\nNext steps:\n", 1)[1] if "\nNext steps:\n" in r.out else None


def test_clones_and_appends_keeping_comments(w):
    toml = "# my comment\n" + w.toml(subs=("liba",)) + "# trailing comment"
    w.setup(subs=("liba",), toml=toml)
    w.add_worktree("dev", subs=("liba",))
    w.add_worktree("dev2", subs=("liba",))
    r = w.xwt("add-repo", w.url("libb"))
    assert r.rc == 0, r.all
    dest = w.top / "libb"
    lines = r.out.splitlines()
    assert lines[:2] == ["xwt: cloning libb into the toplevel",
                         f"xwt: $ git clone {w.url('libb')} {dest}"]
    assert "xwt: libb is on branch main, which is now its integration branch" in lines
    assert (f"xwt: added [repo.libb] to {w.top / 'xwt.toml'} (last in the install order; move "
            f"it up if something else depends on it)") in lines
    assert f"xwt: added /libb/ to {w.top / '.gitignore'}" in lines
    assert (w.top / "xwt.toml").read_text() == (
        toml + f'\n\n[repo.libb]\nurl = "{w.url("libb")}"\nbranch = "main"\n')
    assert (dest / "extern" / "dep" / "dep.txt").exists()   # submodules initialized
    # No [envrc.venv], untracked xwt.toml: commit .gitignore, init each worktree.
    assert next_steps(r) == (
        f"  1. Commit .gitignore in {w.top}.\n"
        f"  2. Run `xwt init` in each worktree, to add the libb checkout:\n"
        f"         {w.group / 'dev'}\n"
        f"         {w.group / 'dev2'}\n"
        f"     Until then, xwt commands in those worktrees remind you that libb is missing.\n")
    r = w.xwt("diff", cwd=w.group / "dev")
    assert "dev has no checkout of libb" in r.err


def test_branch_option(w):
    w.setup(subs=("liba",))
    r = w.xwt("add-repo", w.url("libb"), "--branch", "kms")
    assert r.rc == 0, r.all
    assert f"xwt: $ git clone -b kms {w.url('libb')} {w.top / 'libb'}" in r.out
    assert 'branch = "kms"' in (w.top / "xwt.toml").read_text()


def test_python_package_with_venv_tracked_toml_and_worktrees(w):
    w.setup(subs=("liba",), toml=w.toml(subs=("liba",)) + "\n[envrc.venv]\n", commit_toml=True)
    w.add_worktree("dev", subs=("liba",))
    r = w.xwt("add-repo", w.url("libb"))
    assert r.rc == 0, r.all
    toml = w.top / "xwt.toml"
    assert next_steps(r) == (
        f"  1. libb is a Python package. To install it into the venvs, add \"libb\" to "
        f"`editable` under [envrc.venv] in {toml} (after anything it depends on).\n"
        f"  2. Commit xwt.toml and .gitignore in {w.top}.\n"
        f"  3. Run `xwt init` in the toplevel (to install libb into its venv) and in each "
        f"worktree, to add the libb checkout and rebuild the venv:\n"
        f"         {w.top}\n"
        f"         {w.group / 'dev'}\n"
        f"     Until then, xwt commands in those worktrees remind you that libb is missing.\n")


def test_python_package_with_venv_and_no_worktrees(w):
    w.setup(subs=("liba",), toml=w.toml(subs=("liba",)) + "\n[envrc.venv]\n")
    (w.top / ".gitignore").write_text("/*/\n")
    r = w.xwt("add-repo", w.url("libb"))
    assert r.rc == 0, r.all
    assert "added /libb/" not in r.out
    assert next_steps(r) == (
        f"  1. libb is a Python package. To install it into the venvs, add \"libb\" to "
        f"`editable` under [envrc.venv] in {w.top / 'xwt.toml'} (after anything it depends on).\n"
        f"  2. Run `xwt init` in the toplevel, to install libb into its venv:\n"
        f"         {w.top}\n")


def test_nothing_left_to_do(w):
    w.setup(subs=("liba",))
    (w.top / ".gitignore").write_text("/*/\n")
    r = w.xwt("add-repo", w.url("dep"))
    assert r.rc == 0, r.all
    assert next_steps(r) is None and "Next steps" not in r.out


def test_not_a_package_with_venv(w):
    w.setup(subs=("liba",), toml=w.toml(subs=("liba",)) + "\n[envrc.venv]\n")
    w.add_worktree("dev", subs=("liba",))
    r = w.xwt("add-repo", w.url("dep"))
    assert next_steps(r) == (
        f"  1. Commit .gitignore in {w.top}.\n"
        f"  2. Run `xwt init` in each worktree, to add the dep checkout and rebuild the venv:\n"
        f"         {w.group / 'dev'}\n"
        f"     Until then, xwt commands in those worktrees remind you that dep is missing.\n")


def test_existing_clone_is_recorded_without_cloning(w):
    w.setup(subs=("liba",))
    w.git(w.top, "clone", "-q", "-b", "kms", w.url("libb"), w.top / "libb")
    r = w.xwt("add-repo", w.url("libb"))
    assert r.rc == 0, r.all
    assert "git clone" not in r.out
    assert f"xwt: {w.top / 'libb'} is already a clone of {w.url('libb')}; recording it" in r.out
    assert 'branch = "kms"' in (w.top / "xwt.toml").read_text()
    r = w.xwt("add-repo", w.url("libb"))
    assert r.rc == 1 and f"{w.top / 'xwt.toml'} already has [repo.libb]" in r.err


def test_existing_clone_on_another_branch_than_asked(w):
    w.setup(subs=("liba",))
    w.git(w.top, "clone", "-q", w.url("libb"), w.top / "libb")
    r = w.xwt("add-repo", w.url("libb"), "-b", "kms")
    assert r.rc == 1 and "is on main, not kms" in r.err


def test_mismatched_origin_is_refused(w):
    w.setup(subs=("liba",))
    w.git(w.top, "clone", "-q", w.url("liba"), w.top / "libb")
    before = (w.top / "xwt.toml").read_text()
    r = w.xwt("add-repo", w.url("libb"))
    assert r.rc == 1
    assert f"{w.top / 'libb'} exists, but its origin is {w.url('liba')}, not {w.url('libb')}" in r.err
    assert (w.top / "xwt.toml").read_text() == before


def test_duplicate_name_is_refused(w):
    w.setup(subs=("liba",))
    r = w.xwt("add-repo", "github:someone/liba")
    assert r.rc == 1 and "already has [repo.liba]" in r.err


def test_bad_name_is_refused(w):
    w.setup(subs=("liba",))
    r = w.xwt("add-repo", "github:someone/.hidden")
    assert r.rc == 1 and "can't use '.hidden'" in r.err


def test_a_result_that_would_not_validate_is_refused_before_cloning(w):
    w.setup(subs=(), toml="repo = {}\n")
    r = w.xwt("add-repo", w.url("libb"))
    assert r.rc == 1 and "adding [repo.libb] would make xwt.toml invalid" in r.err
    assert not (w.top / "libb").exists()
    assert (w.top / "xwt.toml").read_text() == "repo = {}\n"


def test_a_result_that_does_not_validate_is_rolled_back(w, monkeypatch):
    w.setup(subs=("liba",))
    before = (w.top / "xwt.toml").read_text()
    from xwt import config, verbs

    real = config.load

    def broken(path):
        if "[repo.libb]" in open(path).read():
            raise config.ConfigError(f"{path}: simulated")
        return real(path)
    monkeypatch.setattr(config, "load", broken)
    r = w.xwt("add-repo", w.url("libb"))
    assert r.rc == 1 and "the old one was restored" in r.err
    assert (w.top / "xwt.toml").read_text() == before


def test_dotted_name_is_quoted(w):
    from xwt.verbs import append_repo_text
    from xwt.config import parse
    text = append_repo_text("", "a.b", "u", "main")
    assert text == '[repo."a.b"]\nurl = "u"\nbranch = "main"\n'
    assert list(parse(text).repos) == ["a.b"]

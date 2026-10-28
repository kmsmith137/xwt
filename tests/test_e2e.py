"""End to end with no sub-repos (N=0): xwt as a named-worktree manager for one repo."""

import subprocess


def test_n0_end_to_end(w, monkeypatch):
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    w.clone_top()
    r = w.xwt("init")
    assert r.rc == 0, r.all
    text = (w.top / "xwt.toml").read_text()
    assert 'branch = "main"' in text and "\n[repo." not in text and "\n[envrc" not in text
    assert f"xwt: commit xwt.toml in {w.top}" in r.out
    assert not (w.top / ".gitignore").exists()        # nothing to ignore
    w.git(w.top, "add", "xwt.toml")
    w.git(w.top, "commit", "-q", "-m", "xwt.toml")

    assert w.xwt("new", "claude").rc == 0
    assert w.xwt("new", "gpt").rc == 0
    claude, gpt = w.group / "claude", w.group / "gpt"
    assert w.branch(claude) == "claude" and w.branch(gpt) == "gpt"

    tip = w.commit(claude)
    r = w.xwt("merge-up", cwd=claude)
    assert r.rc == 0, r.all
    assert w.rev(w.top, "main") == tip

    w.commit(gpt)
    r = w.xwt("status")
    assert "gpt/ is 1 commit ahead and 1 commit behind top/ (repo proj, main branch)" in r.out
    r = w.xwt("merge-up", cwd=gpt)
    assert r.rc == 1 and "proj: main has 1 commit not in gpt; run `xwt rebase-down` first" in r.err
    assert w.xwt("rebase-down", cwd=gpt).rc == 0
    assert w.xwt("merge-up", cwd=gpt).rc == 0
    assert w.rev(w.top, "main") == w.rev(gpt)

    r = w.xwt("status")
    assert r.rc == 0, r.all
    assert "claude/* is up-to-date with top/* (all repos)" not in r.out   # claude is behind
    assert "claude/ is 1 commit behind top/ (repo proj, main branch)" in r.out
    assert "gpt/* is up-to-date with top/* (all repos)" in r.out

    for name in ("claude", "gpt"):
        r = w.xwt("delete", name)
        assert r.rc == 0, r.all
    assert sorted(p.name for p in w.group.iterdir()) == ["top"]
    assert w.git(w.top, "branch", "--format=%(refname:short)") == "main"


def test_n0_python_package_with_a_venv(w, monkeypatch):
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    w.clone_top()
    w.commit(w.top, "pyproject.toml", (
        '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n'
        '[project]\nname = "proj"\nversion = "0.1"\n'
        '[tool.setuptools]\npackages = ["proj"]\n'))
    w.commit(w.top, "proj/__init__.py", "")
    w.commit(w.top, ".gitignore", "__pycache__/\n")
    r = w.xwt("init", "--no-venv")
    assert r.rc == 0, r.all
    assert (w.top / ".gitignore").read_text() == (
        "__pycache__/\n\n# added by xwt\n/.envrc\n/.direnv/\n/.venv/\n")
    assert f"xwt: commit xwt.toml and .gitignore in {w.top}" in r.out
    toml = w.top / "xwt.toml"
    # Offline, the editable install needs --no-build-isolation: switch it on and re-run.
    toml.write_text(toml.read_text().replace("# pip_args =", "pip_args ="))
    r = w.xwt("init")
    assert r.rc == 0, r.all
    assert "pip install --no-build-isolation -e ." in r.out
    w.git(w.top, "add", "xwt.toml", ".gitignore")
    w.git(w.top, "commit", "-q", "-m", "xwt")

    r = w.xwt("new", "dev")
    assert r.rc == 0, r.all
    dev = w.group / "dev"
    out = subprocess.run([dev / ".venv" / "bin" / "python", "-c", "import proj; print(proj.__file__)"],
                         capture_output=True, text=True, cwd="/").stdout.strip()
    assert out == str(dev / "proj" / "__init__.py")
    assert w.xwt("status").rc == 0
    r = w.xwt("delete", "dev")
    assert r.rc == 0, r.all
    assert not dev.exists()

import io

import pytest

from xwt import detect, ui
from xwt.config import parse
from xwt.detect import AGENT_REMINDER, compose


def reminder_in(text):
    return "if coding agents work in this project" in text


def make_clones(w):
    """A toplevel with clones libb (kms), liba (detached), noorigin (no origin)
    and a submodule, and no xwt.toml."""
    w.clone_top()
    w.git(w.top, "clone", "-q", "-b", "kms", w.url("libb"), w.top / "libb")
    w.git(w.top, "clone", "-q", w.url("liba"), w.top / "liba")
    w.git(w.top / "liba", "checkout", "-q", "--detach")
    w.git(w.top, "init", "-q", w.top / "noorigin")
    w.git(w.top, "submodule", "add", "-q", w.url("dep"), "sm")
    w.git(w.top, "commit", "-q", "-m", "submodule")


def test_detection(w, monkeypatch):
    make_clones(w)
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "myenv")
    det = detect.detect(w.top)
    assert det.branch == "main"
    assert [(s.name, s.url, s.branch) for s in det.subs] == [
        ("liba", w.url("liba"), None), ("libb", w.url("libb"), "kms")]
    assert det.packages == ["liba", "libb"]
    assert det.conda == "myenv"
    cfg = parse(compose(w.top, det))
    assert cfg.branch == "main"
    assert list(cfg.repos) == ["liba", "libb"]
    assert cfg.repos["liba"].branch is None and cfg.repos["libb"].branch == "kms"
    assert cfg.envrc.conda == "myenv"
    assert cfg.envrc.venv.editable == ["liba", "libb"]
    assert cfg.envrc.venv.install == [] and cfg.envrc.export == {}


def test_init_writes_without_a_terminal_and_reminds_once(w, monkeypatch):
    make_clones(w)
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    r = w.xwt("init", "--no-venv")
    assert r.rc == 0, r.all
    text = (w.top / "xwt.toml").read_text()
    assert text == compose(w.top, detect.detect(w.top))
    assert "# conda = " in text
    assert f"xwt: wrote {w.top / 'xwt.toml'}" in r.out
    assert "xwt: check the sub-repo order: it is the install order" in r.out
    assert f"xwt: warning: skipping the clone {w.top / 'noorigin'}: it has no `origin` remote" in r.err
    assert reminder_in(r.out)
    assert "    xwt merge-up, xwt rebase-down, xwt new, xwt delete,\n" in r.out
    assert f"xwt: commit xwt.toml and .gitignore in {w.top}" in r.out
    assert (w.top / ".envrc").exists()
    r = w.xwt("init", "--no-venv")
    assert r.rc == 0 and not reminder_in(r.out) and "commit" not in r.out


def test_no_packages_writes_the_environment_commented_out(w, monkeypatch):
    w.clone_top()
    w.git(w.top, "clone", "-q", w.url("dep"), w.top / "dep")
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "myenv")
    det = detect.detect(w.top)
    assert det.packages == []
    text = compose(w.top, det)
    cfg = parse(text)
    assert cfg.envrc is None and list(cfg.repos) == ["dep"]
    assert '# conda = "myenv"' in text and "# [envrc.venv]" in text
    r = w.xwt("init")
    assert r.rc == 0, r.all
    assert "check the sub-repo order" not in r.out
    assert not (w.top / ".envrc").exists() and w.direnv_calls() == []


def test_toplevel_package_and_detached_toplevel(w, monkeypatch):
    w.clone_top()
    w.commit(w.top, "setup.py", "from setuptools import setup\nsetup()\n")
    w.git(w.top, "checkout", "-q", "--detach")
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    det = detect.detect(w.top)
    assert det.branch is None and det.subs == [] and det.packages == ["."]
    text = compose(w.top, det)
    cfg = parse(text)
    assert cfg.branch is None and cfg.repos == {}
    assert cfg.envrc.conda is None and cfg.envrc.venv.editable == ["."]
    assert "# [repo.mylib]" in text


def interactive(monkeypatch, answers: str):
    monkeypatch.setattr(ui, "stdin_isatty", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO(answers))


@pytest.mark.parametrize("answer", ["y\n", "\n", "yes\n"])
def test_prompt_yes(w, monkeypatch, answer):
    w.clone_top()
    interactive(monkeypatch, answer)
    r = w.xwt("init")
    assert r.rc == 0, r.all
    assert f"Write {w.top / 'xwt.toml'}? [Y/n/e]" in r.out
    assert "# xwt config for the toplevel" in r.out          # the file is shown first
    assert (w.top / "xwt.toml").exists() and reminder_in(r.out)


def test_prompt_no(w, monkeypatch):
    w.clone_top()
    interactive(monkeypatch, "n\n")
    r = w.xwt("init")
    assert r.rc == 0
    assert not (w.top / "xwt.toml").exists() and not (w.top / ".gitignore").exists()
    assert "xwt: nothing written" in r.out and not reminder_in(r.out)


def test_prompt_eof_is_no(w, monkeypatch):
    w.clone_top()
    interactive(monkeypatch, "")
    assert w.xwt("init").rc == 0 and not (w.top / "xwt.toml").exists()


def editor(w, name, script):
    p = w.tmp / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(0o755)
    return p


def test_prompt_edit(w, monkeypatch):
    w.clone_top()
    # The file is the last argument, after the flag in $EDITOR.
    ed = editor(w, "ed.sh", 'for f; do :; done\nprintf \'\\n[envrc.export]\\nEDITED = "1"\\n\' >> "$f"\n')
    monkeypatch.setenv("EDITOR", f"{ed} --some-flag")
    interactive(monkeypatch, "e\ny\n")
    r = w.xwt("init")
    assert r.rc == 0, r.all
    assert parse((w.top / "xwt.toml").read_text()).envrc.export == {"EDITED": "1"}
    assert r.out.count("[Y/n/e]") == 2
    assert 'export EDITED="1"' in (w.top / ".envrc").read_text()


def test_prompt_visual_wins_and_an_invalid_edit(w, monkeypatch):
    w.clone_top()
    monkeypatch.setenv("EDITOR", str(editor(w, "unused.sh", "exit 3\n")))
    monkeypatch.setenv("VISUAL", str(editor(w, "bad.sh", 'echo "bogus = 1" >> "$1"\n')))
    interactive(monkeypatch, "e\ny\nn\n")
    r = w.xwt("init")
    assert r.rc == 0
    assert "unknown key 'bogus' in the top level" in r.err
    assert "the text isn't valid; edit it again (e), or quit (n)" in r.err
    assert not (w.top / "xwt.toml").exists()


def test_prompt_other_answer(w, monkeypatch):
    w.clone_top()
    interactive(monkeypatch, "what\nn\n")
    r = w.xwt("init")
    assert "Please answer y, n or e." in r.out and not (w.top / "xwt.toml").exists()


def test_init_outside_a_main_checkout_root(w):
    w.clone_top()
    (w.top / "sub").mkdir()
    for cwd in (w.top / "sub", w.group):
        r = w.xwt("init", cwd=cwd)
        assert r.rc == 1 and "run `xwt init` in the root of a repo's main checkout" in r.err
    other = w.group / "linked"
    w.git(w.top, "worktree", "add", "-q", other, "-b", "linked")
    r = w.xwt("init", cwd=other)
    assert r.rc == 1 and "root of a repo's main checkout" in r.err
    assert not (w.top / "xwt.toml").exists()


def test_init_refuses_a_clone_in_home(w, monkeypatch):
    w.clone_top()
    monkeypatch.setenv("HOME", str(w.group))
    r = w.xwt("init")
    assert r.rc == 1 and "is directly in $HOME" in r.err and "mkdir ~/proj" in r.err
    assert not (w.top / "xwt.toml").exists()

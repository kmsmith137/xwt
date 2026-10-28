import pytest

from xwt.config import ConfigError, load, parse, toml_string


def test_empty_file_is_valid():
    cfg = parse("")
    assert cfg.branch is None and cfg.repos == {} and cfg.envrc is None


def test_comments_only_is_valid():
    assert parse("# just a comment\n").repos == {}


def test_full_parse_keeps_file_order():
    cfg = parse("""
branch = "main"

[repo.zeta]
url = "github:me/zeta"
branch = "kms"

[repo.alpha]
url = "/path/alpha.git"

[envrc]
conda = "dev"

[envrc.export]
PYTHONSAFEPATH = "1"
A_PATH = "$VIRTUAL_ENV/x"

[envrc.venv]
install = ["editables"]
editable = ["zeta", ".", "alpha"]
pip_args = ["--no-build-isolation"]
""")
    assert cfg.branch == "main"
    assert list(cfg.repos) == ["zeta", "alpha"]
    assert cfg.repos["zeta"].url == "github:me/zeta" and cfg.repos["zeta"].branch == "kms"
    assert cfg.repos["alpha"].branch is None
    assert cfg.envrc.conda == "dev"
    assert list(cfg.envrc.export.items()) == [("PYTHONSAFEPATH", "1"), ("A_PATH", "$VIRTUAL_ENV/x")]
    v = cfg.envrc.venv
    assert v.install == ["editables"] and v.editable == ["zeta", ".", "alpha"]
    assert v.pip_args == ["--no-build-isolation"]


def test_venv_without_envrc_header():
    cfg = parse("[envrc.venv]\n")
    assert cfg.envrc is not None and cfg.envrc.conda is None
    assert cfg.envrc.venv is not None and cfg.envrc.venv.editable == []


def test_envrc_without_venv():
    cfg = parse('[envrc]\nconda = "dev"\n')
    assert cfg.envrc.venv is None


@pytest.mark.parametrize("text, msg", [
    ('colour = "x"\n', "unknown key 'colour' in the top level"),
    ('[repo.a]\nurl = "u"\nbrnch = "x"\n', "unknown key 'brnch' in [repo.a]"),
    ('[envrc]\ncondaa = "x"\n', "unknown key 'condaa' in [envrc]"),
    ('[envrc.venv]\neditables = []\n', "unknown key 'editables' in [envrc.venv]"),
    ('[repo."a/b"]\nurl = "u"\n', "bad repo name"),
    ('[repo.".hidden"]\nurl = "u"\n', "bad repo name"),
    ('[repo.a]\nbranch = "x"\n', "[repo.a] needs a 'url'"),
    ('[envrc.venv]\neditable = ["nope"]\n', "names 'nope'"),
    ('[envrc.export]\n"BAD-NAME" = "1"\n', "'BAD-NAME' is not a shell variable name"),
    ('[envrc.export]\nN = 1\n', "the value of N must be a string"),
    ('branch = 3\n', "'branch' must be a string"),
    ('branch = ""\n', "'branch' must not be empty"),
    ('[envrc.venv]\ninstall = "editables"\n', "must be a list of strings"),
    ('[envrc.venv]\npip_args = [1]\n', "must be a list of strings"),
    ('repo = 3\n', "[repo] must be a table"),
    ('[repo]\na = "x"\n', "[repo.a] must be a table"),
    ('branch = "main\n', "xwt.toml"),
])
def test_invalid(text, msg):
    with pytest.raises(ConfigError, match=None) as e:
        parse(text)
    assert msg in str(e.value)


def test_error_names_the_file(tmp_path):
    p = tmp_path / "xwt.toml"
    p.write_text("nope = 1\n")
    with pytest.raises(ConfigError) as e:
        load(p)
    assert str(p) in str(e.value)


def test_toml_string_round_trips():
    s = 'a "quoted" \\ back\tslash\n'
    assert parse(f"branch = {toml_string(s)}\n").branch == s

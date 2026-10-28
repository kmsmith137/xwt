"""Load and validate xwt.toml (plan §4.2).

Every key is optional and an empty file is valid; unknown keys are errors.
Tables keep their order from the file: the `[repo.*]` order is the order of
clones, checkouts and output.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "xwt.toml"

# A sub-repo's directory name. Not starting with "." keeps it clear of .git,
# .venv, .envrc and friends.
REPO_NAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*\Z")
SHELL_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ConfigError(Exception):
    pass


@dataclass
class RepoConf:
    name: str
    url: str
    branch: str | None = None


@dataclass
class VenvConf:
    install: list[str] = field(default_factory=list)
    editable: list[str] = field(default_factory=list)
    pip_args: list[str] = field(default_factory=list)


@dataclass
class EnvrcConf:
    conda: str | None = None
    export: dict[str, str] = field(default_factory=dict)
    venv: VenvConf | None = None


@dataclass
class Config:
    path: Path | None = None
    branch: str | None = None
    repos: dict[str, RepoConf] = field(default_factory=dict)
    envrc: EnvrcConf | None = None


def valid_repo_name(name: str) -> bool:
    return bool(REPO_NAME_RE.match(name))


def load(path) -> Config:
    path = Path(path)
    try:
        text = path.read_text()
    except OSError as e:
        raise ConfigError(f"can't read {path}: {e.strerror}") from None
    return parse(text, path)


def parse(text: str, path=None) -> Config:
    where = str(path) if path is not None else CONFIG_NAME
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{where}: {e}") from None
    return _validate(data, where, Path(path) if path is not None else None)


def _check_keys(table: dict, allowed, ctx: str, where: str):
    for key in table:
        if key not in allowed:
            known = ", ".join(allowed)
            raise ConfigError(f"{where}: unknown key '{key}' in {ctx} (known: {known})")


def _string(value, ctx: str, where: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{where}: {ctx} must be a string")
    if not value:
        raise ConfigError(f"{where}: {ctx} must not be empty")
    return value


def _string_list(value, ctx: str, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: {ctx} must be a list of strings")
    return list(value)


def _table(value, ctx: str, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: {ctx} must be a table")
    return value


def _validate(data: dict, where: str, path: Path | None) -> Config:
    _check_keys(data, ("branch", "repo", "envrc"), "the top level", where)
    cfg = Config(path=path)
    if "branch" in data:
        cfg.branch = _string(data["branch"], "'branch'", where)

    repos = _table(data.get("repo", {}), "[repo]", where)
    for name, table in repos.items():
        ctx = f"[repo.{name}]"
        if not valid_repo_name(name):
            raise ConfigError(
                f"{where}: bad repo name in {ctx}: use letters, digits, '.', '_' and '-' "
                f"(not starting with '.'), and no '/'")
        table = _table(table, ctx, where)
        _check_keys(table, ("url", "branch"), ctx, where)
        if "url" not in table:
            raise ConfigError(f"{where}: {ctx} needs a 'url'")
        url = _string(table["url"], f"'url' in {ctx}", where)
        branch = _string(table["branch"], f"'branch' in {ctx}", where) if "branch" in table else None
        cfg.repos[name] = RepoConf(name, url, branch)

    if "envrc" in data:
        env = _table(data["envrc"], "[envrc]", where)
        _check_keys(env, ("conda", "export", "venv"), "[envrc]", where)
        ec = EnvrcConf()
        if "conda" in env:
            ec.conda = _string(env["conda"], "'conda' in [envrc]", where)
        exports = _table(env.get("export", {}), "[envrc.export]", where)
        for key, value in exports.items():
            if not SHELL_NAME_RE.match(key):
                raise ConfigError(f"{where}: [envrc.export]: '{key}' is not a shell variable name")
            if not isinstance(value, str):
                raise ConfigError(f"{where}: [envrc.export]: the value of {key} must be a string")
            ec.export[key] = value
        if "venv" in env:
            venv = _table(env["venv"], "[envrc.venv]", where)
            _check_keys(venv, ("install", "editable", "pip_args"), "[envrc.venv]", where)
            vc = VenvConf()
            for key in ("install", "editable", "pip_args"):
                if key in venv:
                    setattr(vc, key, _string_list(venv[key], f"'{key}' in [envrc.venv]", where))
            for entry in vc.editable:
                if entry != "." and entry not in cfg.repos:
                    raise ConfigError(
                        f"{where}: 'editable' in [envrc.venv] names '{entry}', which is "
                        f"neither \".\" (the toplevel's own repo) nor a [repo] table")
            ec.venv = vc
        cfg.envrc = ec
    return cfg


def toml_string(s: str) -> str:
    """`s` as a TOML basic string."""
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)

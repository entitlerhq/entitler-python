"""The `entitler` command (section 10)."""

from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from entitler import cli
from entitler._decode import Obj, feature_list
from entitler.testing import answers

HTTPX_CLIENT = httpx.Client
GOLDEN = Path(__file__).parent.parent / "fixtures" / "generate"
ROOT = Path(__file__).parent.parent.parent
CATALOGUE = [
    answers.feature_listing(
        key="ai_credits",
        name="AI credits",
        type="metered",
        description="Spent on AI.",
        unit="credits",
    ),
    answers.feature_listing(
        key="collaboration",
        name="Collaboration",
        type="group",
        includes=["team_seats", "shared_folders"],
    ),
    answers.feature_listing(
        key="everything",
        name="Everything",
        type="group",
        includes=["collaboration", "export_pdf", "missing", "everything"],
    ),
    answers.feature_listing(
        key="export_pdf", name="Export to PDF", description="Download any document.\nAs a PDF."
    ),
    answers.feature_listing(key="legacy_reports", name="Legacy reports", archived=True),
    answers.feature_listing(key="shared_folders", name="Shared folders"),
    answers.feature_listing(key="team_seats", name="Team seats", type="config", unit="seats"),
    answers.feature_listing(key="tiered_thing", name="", type="tiered"),
]
HOSTILE = [
    answers.feature_listing(
        key='quote"back\\slash',
        name='Name with """ quotes\u2028and a line separator\x85and NEL',
        description='Line one\r\nLine two\u2029para\x00with control\x07 and """ and \\ backslash',
        unit="units\nnewline",
    ),
    answers.feature_listing(key="1st", name="Starts with a digit"),
    answers.feature_listing(key="class", name="A keyword"),
    answers.feature_listing(key="CLASS", name="Clashes once upper-cased"),
    answers.feature_listing(key="é_accent", name="Non-ASCII key"),
    answers.feature_listing(key="", name="Empty key"),
]


def source(features: list[dict[str, Any]], **context: Any) -> Any:
    return feature_list(Obj(answers.feature_list(features=features, **context), ""))


def check_python(text: str) -> None:
    ast.parse(text)
    compile(text, "generated.py", "exec")
    formatted = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--line-length", "88", "-"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert formatted == text


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8")


def test_catalogue_golden() -> None:
    text = cli.render_features(source(CATALOGUE))
    check_python(text)
    golden("catalogue.py.txt", text)
    namespace: dict[str, Any] = {}
    exec(compile(text, "generated.py", "exec"), namespace)  # noqa: S102 - our own generated file
    assert namespace["EVERYTHING"].includes == ("team_seats", "shared_folders", "export_pdf")
    assert namespace["AI_CREDITS"].type == "metered"
    assert "Deprecated: Archived in Entitler." in text


def test_hostile_text_is_escaped_and_still_parses() -> None:
    text = cli.render_features(
        source(HOSTILE, environment={"id": "e", "name": "Dev\nelopment", "kind": "test"})
    )
    check_python(text)
    golden("escaping.py.txt", text)
    for line in text.splitlines():
        assert "\u2028" not in line
        assert "\x85" not in line
        assert "\x00" not in line
    assert re.search(r"^# Read from Dev elopment, ", text, re.M)
    namespace: dict[str, Any] = {}
    exec(compile(text, "generated.py", "exec"), namespace)  # noqa: S102 - our own generated file
    assert namespace["QUOTE_BACK_SLASH"].key == 'quote"back\\slash'
    assert {"_1ST", "CLASS", "CLASS_2", "_"} <= set(namespace)


def test_empty_golden() -> None:
    text = cli.render_features(source([]))
    check_python(text)
    golden("empty.py.txt", text)


def test_kept_names_survive_new_features() -> None:
    first = cli.render_features(source([answers.feature_listing(key="export_pdf")]))
    renamed = first.replace("EXPORT_PDF:", "PDF_EXPORT:")
    later = cli.render_features(
        source(
            [answers.feature_listing(key="export_pdf"), answers.feature_listing(key="pdf_export")]
        ),
        existing=renamed,
    )
    assert 'PDF_EXPORT: Feature[Boolean] = Feature.boolean("export_pdf")' in later
    assert 'PDF_EXPORT_2: Feature[Boolean] = Feature.boolean("pdf_export")' in later
    check_python(later)


def test_a_change_names_the_change() -> None:
    text = cli.render_features(source([], release=None, change="chg_1"))
    assert "# Read from Test, All customers, change chg_1." in text
    assert cli.same_features(text, cli.render_features(source([], release=4)))


class Api:
    def __init__(
        self,
        features: list[dict[str, Any]] | None = None,
        keys: list[dict[str, Any]] | None = None,
        status: int = 200,
    ) -> None:
        self.features = features or []
        self.keys = keys or []
        self.status = status
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(
                self.status, json=answers.error("scope_required", "The key lacks plans:read.")
            )
        if request.url.path == "/pricing/features":
            return httpx.Response(200, json=answers.feature_list(features=self.features))
        return httpx.Response(200, json={"keys": self.keys})


def run(
    args: list[str],
    api: Api | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    if api is not None and monkeypatch is not None:

        def client(*_a: Any, **kw: Any) -> httpx.Client:
            kw.pop("transport", None)
            return HTTPX_CLIENT(transport=httpx.MockTransport(api.handle), **kw)

        monkeypatch.setattr(httpx, "Client", client)
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(args, env=env or {}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_generate_writes_then_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "pkg" / "features.py"
    api = Api(CATALOGUE[:1])
    code, stdout, _ = run(["generate", f"--out={out}", "--key", "ent_test_x"], api, monkeypatch)
    assert (code, stdout) == (0, f"Wrote 1 feature to {out}.\n")
    assert api.requests[0].headers["Authorization"] == "Bearer ent_test_x"
    code, stdout, _ = run(
        ["generate", "--out", str(out), "--check"], api, monkeypatch, {"ENTITLER_KEY": "ent_test_x"}
    )
    assert (code, stdout) == (0, f"{out} is up to date with 1 feature.\n")
    api.features = CATALOGUE[:2]
    code, _, stderr = run(
        ["generate", "--out", str(out), "--check"], api, monkeypatch, {"ENTITLER_KEY": "k"}
    )
    assert (code, stderr) == (1, f"{out} is out of date. Run entitler generate to update it.\n")
    code, stdout, _ = run(["generate", "--out", str(out)], api, monkeypatch, {"ENTITLER_KEY": "k"})
    assert stdout == f"Wrote 2 features to {out}.\n"
    assert list(tmp_path.glob("pkg/*.tmp")) == []


def test_generate_refuses_to_empty_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "features.py"
    run(["generate", "--out", str(out)], Api(CATALOGUE[:1]), monkeypatch, {"ENTITLER_KEY": "k"})
    before = out.read_text(encoding="utf-8")
    code, _, stderr = run(
        ["generate", "--out", str(out)], Api([]), monkeypatch, {"ENTITLER_KEY": "k"}
    )
    assert code == 1
    assert (
        stderr == f"Entitler listed no features, so {out} was left as it is. "
        "Check the key's environment, or pass --allow-empty.\n"
    )
    assert out.read_text(encoding="utf-8") == before
    code, stdout, _ = run(
        ["generate", "--out", str(out), "--allow-empty"],
        Api([]),
        monkeypatch,
        {"ENTITLER_KEY": "k"},
    )
    assert (code, stdout) == (0, f"Wrote 0 features to {out}.\n")


def test_generate_messages_and_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = str(tmp_path / "f.py")
    assert run(["generate", "--out", out]) == (
        1,
        "",
        "Provide an API key with --key or set ENTITLER_KEY. The key needs the plans:read scope.\n",
    )
    code, _, stderr = run(
        ["generate", "--out", out], Api(status=403), monkeypatch, {"ENTITLER_KEY": "k"}
    )
    assert (code, stderr) == (
        1,
        "Entitler request failed: The key lacks plans:read (scope_required).\n",
    )
    code, _, stderr = run(["generate", "--key", "ent_pk_test_x", "--out", out])
    assert (code, stderr) == (
        1,
        "A publishable key belongs in EntitlerClient. "
        "Use a secret key from the dashboard on your server.\n",
    )
    code, _, stderr = run(["generate", "--key", "k", "--base-url", " ", "--out", out])
    assert (code, stderr) == (1, "Pass a value with --base-url.\n") or code == 1
    assert run(["generate", "--key"])[0:1] == (1,)
    assert run(["generate", "--nope"])[2].startswith("Unknown option --nope.")
    assert run(["frobnicate"])[2].startswith("Unknown command frobnicate.")
    assert run([]) == (1, "", cli.USAGE)
    assert run(["--help"]) == (0, cli.USAGE, "")
    assert run(["generate", "--help"]) == (0, cli.USAGE, "")


def test_generate_reports_connection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    api = Api()
    api.handle = refuse  # type: ignore[method-assign]
    code, _, stderr = run(
        ["generate", "--out", str(tmp_path / "f.py")], api, monkeypatch, {"ENTITLER_KEY": "k"}
    )
    assert (code, stderr) == (
        1,
        "Entitler request failed: Entitler could not be reached (connection_failed).\n",
    )


def test_snapshot_keys_writes_the_key_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "keys.json"
    key = {
        "kty": "EC",
        "crv": "P-256",
        "x": "x1",
        "y": "y1",
        "kid": "k1",
        "alg": "ES256",
        "use": "sig",
    }
    api = Api(keys=[key, {**key, "kid": "k2"}])
    code, stdout, _ = run(["snapshot-keys", "--out", str(out)], api, monkeypatch)
    assert (code, stdout) == (0, f"Wrote 2 snapshot keys to {out}.\n")
    assert (
        out.read_text(encoding="utf-8")
        == json.dumps({"keys": [key, {**key, "kid": "k2"}]}, indent=2) + "\n"
    )
    assert "Authorization" not in api.requests[0].headers
    code, _, stderr = run(["snapshot-keys", "--out", str(out)], Api(keys=[]), monkeypatch)
    assert (code, stderr) == (
        1,
        f"Entitler published no snapshot keys, so {out} was left as it is.\n",
    )
    api = Api(keys=[key])
    code, stdout, _ = run(["snapshot-keys", f"--out={out}"], api, monkeypatch)
    assert stdout == f"Wrote 1 snapshot key to {out}.\n"


def test_the_module_runs(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "entitler", "--help"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0
    assert done.stdout == cli.USAGE


SPELLINGS = re.compile(
    r"(?<![\w./-])((?:uvx |pipx run |npx )?[\w.-]*entitler (?:generate|snapshot-keys))"
)


def test_docs_name_the_command_as_users_run_it() -> None:
    files = [
        ROOT / "README.md",
        *ROOT.glob("docs/*.md"),
        *ROOT.glob("examples/**/*.py"),
        *ROOT.glob("examples/**/*.md"),
        ROOT / "src/entitler/cli.py",
    ]
    allowed = {
        "entitler generate",
        "python -m entitler generate",
        "entitler snapshot-keys",
        "python -m entitler snapshot-keys",
    }
    for path in files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(\S+ )?(\S+ )?entitler (generate|snapshot-keys)", text):
            phrase = match.group(0)
            spelled = re.sub(
                r"^.*?((?:python -m )?entitler (?:generate|snapshot-keys))$", r"\1", phrase
            )
            assert spelled in allowed, (path, phrase)
            assert not re.search(r"(uvx|pipx|npx|pip) +$", phrase.split("entitler")[0]), (
                path,
                phrase,
            )

"""A grounded value (a password) keeps the user's own spelling through path normalization.

`normalize_path_separators` rewrites `\\` to `/` in path-shaped tokens before the model sees the
request, so a password typed `p\\ss` comes back from the model as `p/ss`. The grounded check passes
(it compares against the text the model was shown), and the file would be locked with a password
the user never typed. Found 2026-10-01 verifying 1.2.1's B5, on both runtimes.
"""

from __future__ import annotations

from knaif.nl_clarify_gate import restore_grounded_args
from knaif.prompt import restore_grounded_spelling
from knaif.registry import ToolDef

RAW = r"password-protect sample.pdf with the password p\ss"


def test_a_backslash_password_gets_its_backslash_back():
    assert restore_grounded_spelling("p/ss", RAW) == r"p\ss"


def test_the_value_may_be_part_of_a_longer_token():
    assert restore_grounded_spelling("p/ss", r"lock it with password:p\ss") == r"p\ss"


def test_a_slash_the_user_typed_stays_a_slash():
    assert restore_grounded_spelling("a/b", "password-protect x.pdf with a/b") == "a/b"


def test_nothing_changes_without_a_backslash_in_the_request():
    assert restore_grounded_spelling("hunter2", "protect x.pdf with hunter2") == "hunter2"
    assert restore_grounded_spelling("p/ss", "protect x.pdf with p/ss") == "p/ss"


def _registry() -> dict[str, ToolDef]:
    return {
        "protect_pdf": ToolDef(
            name="protect_pdf",
            description="x",
            required_args=("input", "password"),
            grounded_args=("password",),
        )
    }


def test_only_grounded_args_are_restored():
    plan = [{"tool": "protect_pdf", "args": {"input": "docs/sample.pdf", "password": "p/ss"}}]
    raw = r"password-protect docs\sample.pdf with the password p\ss"
    out = restore_grounded_args(plan, raw, _registry())
    assert out[0]["args"]["password"] == r"p\ss"
    # A path keeps its forward slashes: that rewrite is the point of the normalization.
    assert out[0]["args"]["input"] == "docs/sample.pdf"
    # The model's plan is not mutated in place.
    assert plan[0]["args"]["password"] == "p/ss"


def test_tools_without_grounded_args_are_untouched():
    plan = [{"tool": "clarify", "args": {"question": "a/b?"}}]
    assert restore_grounded_args(plan, r"a\b", _registry()) == plan

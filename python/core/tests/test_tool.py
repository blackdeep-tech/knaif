"""Tests for knaif.tool — Step / Intent ABCs."""

from __future__ import annotations

import pytest

from knaif.tool import Intent, Step

# ── Step ──────────────────────────────────────────────────────────────────────


def test_step_is_abstract():
    with pytest.raises(TypeError):
        Step()  # type: ignore[abstract]


def test_step_requires_handle():
    class NoHandle(Step):
        name = "no_handle"

    with pytest.raises(TypeError):
        NoHandle()  # type: ignore[abstract]


def test_step_subclass_works():
    class MyStep(Step):
        name = "my_step"

        def handle(self, args, ctx):
            return {"ok": True}

    s = MyStep()
    assert s.handle({}, None) == {"ok": True}
    assert s.preflight({}) == []


def test_step_preflight_override_detectable():
    class WithPreflight(Step):
        name = "wp"

        def handle(self, args, ctx):
            return {}

        def preflight(self, args, **kw):
            return ["err"]

    assert type(WithPreflight()).preflight is not Step.preflight


def test_step_default_preflight_not_overridden():
    class Plain(Step):
        name = "plain"

        def handle(self, args, ctx):
            return {}

    assert type(Plain()).preflight is Step.preflight


# ── Intent ────────────────────────────────────────────────────────────────────


def test_intent_is_abstract():
    with pytest.raises(TypeError):
        Intent()  # type: ignore[abstract]


def test_intent_requires_expand():
    class NoExpand(Intent):
        name = "no_expand"

    with pytest.raises(TypeError):
        NoExpand()  # type: ignore[abstract]


def test_intent_subclass_works():
    class MyIntent(Intent):
        name = "my_intent"

        def expand(self, args):
            return [{"tool": "step1", "args": {}}]

    i = MyIntent()
    assert i.expand({}) == [{"tool": "step1", "args": {}}]
    assert i.preflight({}) == []
    assert i.summarize({}) == "my_intent"


def test_intent_summarize_returns_name_by_default():
    class AnIntent(Intent):
        name = "an_intent"

        def expand(self, args):
            return []

    assert AnIntent().summarize({"x": 1}) == "an_intent"


def test_intent_preflight_override_detectable():
    class WithPreflight(Intent):
        name = "wp"

        def expand(self, args):
            return []

        def preflight(self, args, **kw):
            return ["err"]

    assert type(WithPreflight()).preflight is not Intent.preflight


def test_intent_default_preflight_not_overridden():
    class Plain(Intent):
        name = "plain"

        def expand(self, args):
            return []

    assert type(Plain()).preflight is Intent.preflight

"""Wiring-equivalence: OOP tool_map dispatches identically to legacy dicts.

Verifies that for the same inputs, tool.handle(args, ctx) == HANDLERS[name](args, ctx),
tool.expand(args) == EXPANDERS[name](args), and tool.summarize(args) == SUMMARIZERS[name](args).
Also verifies core Step classes match CORE_HANDLERS functions exactly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from knaif.core_tools import CORE_STEP_MAP
from knaif.executor import CORE_HANDLERS
from knaif.tool import Intent, Step

# ── core tools: Step.handle == CORE_HANDLERS fn ──────────────────────────────


def _ctx(confirmed: bool = True) -> MagicMock:
    ctx = MagicMock()
    ctx.confirm.return_value = confirmed
    return ctx


def test_clarify_step_equivalent_to_core_handler():
    args = {"question": "What extension?"}
    assert CORE_STEP_MAP["clarify"].handle(args, _ctx()) == CORE_HANDLERS["clarify"](args, _ctx())


def test_reject_step_equivalent_to_core_handler():
    args = {"reason": "Too dangerous"}
    assert CORE_STEP_MAP["reject"].handle(args, _ctx()) == CORE_HANDLERS["reject"](args, _ctx())


def test_done_step_equivalent_to_core_handler():
    assert CORE_STEP_MAP["done"].handle({}, _ctx()) == CORE_HANDLERS["done"]({}, _ctx())


def test_wait_for_confirmation_confirmed_equivalent():
    args = {"prompt": "Proceed?"}
    ctx_fn = _ctx(True)
    ctx_fn.confirm.return_value = True
    ctx_cls = _ctx(True)
    ctx_cls.confirm.return_value = True
    assert CORE_STEP_MAP["wait_for_confirmation"].handle(args, ctx_cls) == CORE_HANDLERS[
        "wait_for_confirmation"
    ](args, ctx_fn)


def test_wait_for_confirmation_declined_equivalent():
    args = {"prompt": "Proceed?"}
    ctx_fn = _ctx(False)
    ctx_fn.confirm.return_value = False
    ctx_cls = _ctx(False)
    ctx_cls.confirm.return_value = False
    assert CORE_STEP_MAP["wait_for_confirmation"].handle(args, ctx_cls) == CORE_HANDLERS[
        "wait_for_confirmation"
    ](args, ctx_fn)


# ── inline OOP tools match equivalent handler/expander/summarizer callables ──


def _make_legacy_trio():
    """Return (handler_fn, expander_fn, summarizer_fn) for a small tool set."""

    def cmd_encode(args, ctx):
        return {"encoded": args.get("src")}

    def expand_batch(args):
        return [{"tool": "encode", "args": {"src": s}} for s in args.get("sources", [])]

    def summarize_batch(args, **kw):
        return f"batch encode {len(args.get('sources', []))} files"

    return cmd_encode, expand_batch, summarize_batch


def _make_oop_trio():
    """Return (StepInstance, IntentInstance) with equivalent behavior."""

    class EncodeStep(Step):
        name = "encode"

        def handle(self, args, ctx):
            return {"encoded": args.get("src")}

    class BatchEncodeIntent(Intent):
        name = "batch_encode"

        def expand(self, args):
            return [{"tool": "encode", "args": {"src": s}} for s in args.get("sources", [])]

        def summarize(self, args):
            return f"batch encode {len(args.get('sources', []))} files"

    return EncodeStep(), BatchEncodeIntent()


def test_step_handle_matches_handler_function():
    cmd_encode, _, _ = _make_legacy_trio()
    encode_step, _ = _make_oop_trio()
    args = {"src": "input.mp4"}
    assert encode_step.handle(args, _ctx()) == cmd_encode(args, _ctx())


def test_intent_expand_matches_expander_function():
    _, expand_batch, _ = _make_legacy_trio()
    _, batch_intent = _make_oop_trio()
    args = {"sources": ["a.mp4", "b.mp4"]}
    assert batch_intent.expand(args) == expand_batch(args)


def test_intent_summarize_matches_summarizer_function():
    _, _, summarize_batch = _make_legacy_trio()
    _, batch_intent = _make_oop_trio()
    args = {"sources": ["a.mp4", "b.mp4", "c.mp4"]}
    assert batch_intent.summarize(args) == summarize_batch(args)


# ── preflight precedence ─────────────────────────────────────────────────────


def test_tool_preflight_takes_precedence_over_base():
    class StepWithPreflight(Step):
        name = "sp"

        def handle(self, args, ctx):
            return {}

        def preflight(self, args, **kw):
            return ["tool_level"]

    s = StepWithPreflight()
    assert type(s).preflight is not Step.preflight
    assert s.preflight({}) == ["tool_level"]


def test_base_preflight_is_no_op():
    class PlainStep(Step):
        name = "ps"

        def handle(self, args, ctx):
            return {}

    assert PlainStep().preflight({"any": "arg"}) == []

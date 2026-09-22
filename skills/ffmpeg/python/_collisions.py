"""Plan-level output collision handling: rename, then rebind the consumers.

Every rendered ffmpeg command carries ``-y``. So a plan that asks to write ``clip.mp4``
from ``clip.mp4`` (``ffmpeg_175``: *"convert clip.mp4 to mp4, lossless"*) truncates its own
source before ffmpeg reads a frame. ``_engine._build_one_recipe`` honours an explicit output
deliberately — it sandbox-checks it and nothing more — and it cannot do better: it receives
``(probe, platform_profile, quality_profile, options, sandbox)`` and has no idea a plan
exists. Collision handling therefore lives one level up, reached through
``Skill.resolve_output_collisions``.

**The binding rule.**

    A name an earlier step declares it will write binds, for every later step, to what that
    step actually wrote. Collision handling SUBSTITUTES the old output name with the
    resolved one across steps strictly after the producer; it never re-infers which file was
    meant. A name no earlier step writes binds to the file on disk.

There is no ambiguity to resolve, which is the part worth stating plainly: when the original
input and a producer's requested output share a name, the surviving literal has **one**
referent, because ``CommandAgent._forward_thread_reused_sources`` has already rewritten any
later reference to the *original source* onto the producer's output before this runs. A
reference to the pre-transform file does not survive the optimizer at all, so in the identity
case ``clip.mp4`` downstream can only mean what step 0 wrote. Restoring the opposite reading
("leave a reference to the original source alone") would reintroduce the documents bug that
threader exists to fix — ``unlock_pdf`` then ``find_in_document`` reading the still-locked
original.

**Which of the two names moves.** The binding rule says what a later reference means; it does
not say which side of a self-overwrite gets renamed, and the two shapes do not want the same
answer:

    A file the plan itself produced is an intermediate, so the PRODUCER's output moves. A
    file that was already on disk cannot be renamed at all, so the CONSUMER's output moves.

``ffmpeg_175`` is the second shape — nothing in the plan wrote ``clip.mp4``, so writing the
copy to ``clip_converted.mp4`` is the only thing available. The first shape is ``trim ->
Test1.mov`` feeding ``convert Test1.mov -> Test1.mov``, where ``trim_video`` takes neither
``container`` nor ``quality`` and the chain is therefore forced. There the name is the user's
and it belongs to the branch's last step, which is the file they described; the model's only
error was putting it on the intermediate as well. Renaming the consumer there "works" — the
plan runs and a later concat still joins the right files — but it leaves ``Test1.mov`` holding
the un-converted trim output. That is the wrong content under the right name, reported to the
user as a successful run, which is a worse failure than the one it replaces.

**Why the reservation is an ordered walk and not a set.** A flat set of reserved names drops
the position that makes the rule decidable. Step *i*'s inputs resolve through the table *as
it stands before step i*; the rebinding it introduces applies to *i+1…n* only, which is what
keeps a producer reading its own original input.

**Everything is compared as a resolved absolute path.** Matching on basenames merges
``a/clip.mp4`` with ``b/clip.mp4`` and turns the perfectly legal ``source/clip.mp4 ->
exports/clip.mp4`` into a false collision — the mistake the plan rules out by name, and the
one the first draft of this module made anyway: it rebound a consumer reading ``b/clip.mp4``
onto a file only ever written in ``a/``, and dropped the directory while doing it. The prompt
tells the model to preserve every directory component, so the plan means what it says about
directories and so must this.

The one case core does not cover is a **multi-input** producer with an explicit output
(``concat_video [a.mp4, b.mp4] -> a.mp4``): forward-threading skips producers with more than
one source, so a later ``a.mp4`` arrives unnormalised. The same rule settles it — step 0 said
it would write that name — but the substitution must run whether or not threading already
normalised the reference. Batch and glob producers need no rule: they declare no explicit
output, so ``_derive_output_path`` gives them ``a_converted.<ext>``, never a name a consumer
used.

See docs/plans/2026-09-11-reject-clarify-taxonomy.md -> T5b.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from knaif.agent import _FILENAME_RE, _TERMINAL_TOOLS

from ._engine import _INTERMEDIATE_SUFFIX, _legal_output_path, next_free_output

#: Args that name what a step writes. ``output_path`` is the internal spelling used once
#: intents have expanded; ``output`` is what the model emits.
_OUTPUT_KEYS = ("output", "output_path")


def _is_filename(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and bool(_FILENAME_RE.search(value))
        and not any(c in value for c in "*?")
    )


def _input_refs(args: dict[str, Any]) -> list[tuple[Any, Any]]:
    """Every ``(container, key)`` in *args* holding a filename the step reads."""
    refs: list[tuple[Any, Any]] = []
    for key, value in args.items():
        if key in _OUTPUT_KEYS:
            continue
        if _is_filename(value):
            refs.append((args, key))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if _is_filename(item):
                    refs.append((value, i))
    return refs


def _resolve_input(raw: str, sandbox: Path | None) -> Path:
    """Absolute path for an input: a relative one is relative to the **sandbox**."""
    p = Path(raw)
    if p.is_absolute():
        return p
    return (sandbox / p) if sandbox is not None else p.resolve()


#: Tools whose handler resolves a relative output against the **sandbox** rather than against
#: the first input's directory. `RunConcatStep` does exactly that (it resolves `output` against
#: `ctx.sandbox`), while every per-file mode goes through `_build_one_recipe` and resolves
#: against `input_path.parent`. One rule for both is wrong in both directions: a genuine
#: collision survives, and a perfectly good destination gets renamed.
_SANDBOX_OUTPUT_TOOLS = frozenset({"concat_video", "run_concat"})


def _output_base(tool: str, args: dict[str, Any], sandbox: Path | None) -> Path | None:
    """Directory a relative *output* resolves against — which depends on the tool.

    **Inputs and outputs do not share a base**, and conflating them is a real bug rather
    than a tidiness point. ``_build_one_recipe`` resolves an input to an absolute probe path
    and then resolves a relative output against ``input_path.parent`` — so for
    ``inputs=["a/clip.mp4"], output="a/clip.mp4"`` the input is ``<sandbox>/a/clip.mp4`` and
    the output is ``<sandbox>/a/a/clip.mp4``. A first draft here used one base for both and
    resolved the input twice, which moved every comparison into a directory that does not
    exist: the disk check then found nothing and the fallback happily chose a name already
    holding a file.

    **And the base is not the same for every tool.** ``concat_video`` writes one output for
    many inputs, so its handler resolves it against the sandbox; "the first input's parent"
    is meaningless there and got it wrong both ways. Joining ``sub/a.mp4`` and ``sub/b.mp4``
    into ``sub/a.mp4`` compared ``<sandbox>/sub/sub/a.mp4``, found no collision, and left the
    plan to truncate its own input under ``-y``; asking for ``a.mp4`` at the sandbox root
    resolved to ``<sandbox>/sub/a.mp4``, which *is* an input, and renamed a destination that
    collided with nothing.
    """
    if tool in _SANDBOX_OUTPUT_TOOLS:
        return sandbox
    for container, key in _input_refs(args):
        return _resolve_input(str(container[key]), sandbox).parent
    return sandbox


def _resolve_output(raw: str, out_base: Path | None) -> Path:
    """Absolute path for an output, resolved the way ``_build_one_recipe`` resolves it."""
    p = Path(raw)
    if p.is_absolute():
        return p
    return (out_base / p) if out_base is not None else p.resolve()


def _producer_of(
    plan: list[dict[str, Any]], target: Path, *, before: int, sandbox: Path | None
) -> tuple[int, str] | None:
    """The step that declares it writes *target*, searched backwards from *before*.

    Backwards because the binding rule is positional: when two earlier steps write the same
    name, the one a step at *before* reads is the nearer of them. Returns ``(index, output
    key)``, or ``None`` when nothing in the plan writes *target* — which is the case that
    matters, since it is what distinguishes a chained intermediate from a file on disk.
    """
    for j in range(before - 1, -1, -1):
        step = plan[j]
        if step.get("tool") in _TERMINAL_TOOLS:
            continue
        args = step.get("args") or {}
        out_base = _output_base(str(step.get("tool") or ""), args, sandbox)
        for key in _OUTPUT_KEYS:
            if not _is_filename(args.get(key)):
                continue
            if _resolve_output(str(args[key]), out_base) == target:
                return j, key
    return None


def _respell(original: str, chosen: Path) -> str:
    """Write *chosen* back in the shape the reference was written in.

    An absolute reference stays absolute; ``a/clip.mp4`` keeps its ``a/``; a bare name stays
    bare. Collapsing everything to a basename silently relocates the file — the same defect
    as matching on basenames, arriving on the way out instead of the way in.
    """
    raw = Path(original)
    if raw.is_absolute():
        return str(chosen)
    parent = raw.parent
    if str(parent) in ("", "."):
        return chosen.name
    return (parent / chosen.name).as_posix()


def _bind_legal_output_names(plan: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Rewrite outputs the filesystem would reject, and rebind the steps that read them.

    `ffmpeg_268` named its output after the time range it was given —
    ``clip_trimmed_00:00:00.mp4`` — and then referenced that same string as the next step's
    input. Colons are legal on Linux and illegal on Windows, so ffmpeg refused to open it
    (*Error opening output files: Invalid argument*) and the chain died at step 1 having
    written nothing.

    **Rebinding is the whole point, not a detail.** Sanitising the output alone leaves step 1
    writing ``clip_trimmed_00-00-00.mp4`` while step 2 still reads the colon spelling, which
    unlinks the chain silently — a worse failure than the loud one it replaces. This is the
    module's binding rule applied to a second cause: *a name an earlier step declares it will
    write binds, for every later step, to what that step actually wrote.*

    Only a name a step **declares as its output** is rewritten. An input nobody produces is
    left exactly as written, so a real file whose name contains one of these characters — which
    Windows cannot have but Linux can — stays reachable. That also keeps the pass decidable
    from the plan alone, with no filesystem lookup, so both runtimes agree without consulting
    a disk.
    """
    renames: list[dict[str, str]] = []
    for idx, step in enumerate(plan):
        if step.get("tool") in _TERMINAL_TOOLS:
            continue
        args = step.get("args") or {}
        for key in _OUTPUT_KEYS:
            raw = args.get(key)
            if not _is_filename(raw):
                continue
            legal = _legal_output_path(str(raw))
            if legal == raw:
                continue
            args[key] = legal
            # Strictly after the producer, exactly as `rebind_colliding_outputs` scopes its
            # own substitution: the producer keeps reading whatever it reads.
            for later in plan[idx + 1 :]:
                if later.get("tool") in _TERMINAL_TOOLS:
                    continue
                largs = later.get("args") or {}
                for container, k in _input_refs(largs):
                    if str(container[k]) == raw:
                        container[k] = legal
            renames.append(
                {
                    "step": str(step.get("tool") or ""),
                    "requested": Path(str(raw)).name,
                    "used": Path(legal).name,
                }
            )
    return renames


def rebind_colliding_outputs(
    plan: list[dict[str, Any]], sandbox: Any = None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Rename outputs that would overwrite their own input; rebind later references.

    Returns ``(plan, substitutions)``. The plan is mutated in place and also returned;
    *substitutions* is one ``{"step", "requested", "used"}`` record per rename, so the caller
    can tell the user what happened. Silently overwriting and hard-refusing are both wrong
    answers here — ``ffmpeg_175`` expects a *plan*, because the user asked for a copy.
    """
    # Before anything resolves a path: a name the filesystem would reject is not a collision
    # candidate, it is not a valid path at all. Runs first so the walk below sees the names
    # that will actually be written.
    substitutions: list[dict[str, str]] = _bind_legal_output_names(plan)
    sandbox = Path(sandbox) if sandbox is not None else None

    # Every path the plan reads, and every path it declares it will write. Both are spoken
    # for before the walk begins: a fallback chosen at step 0 has to avoid the output step 3
    # is going to produce, and stepwise accumulation cannot see that far ahead.
    plan_inputs: set[Path] = set()
    plan_outputs: set[Path] = set()
    for step in plan:
        if step.get("tool") in _TERMINAL_TOOLS:
            continue
        args = step.get("args") or {}
        out_base = _output_base(str(step.get("tool") or ""), args, sandbox)
        for container, key in _input_refs(args):
            plan_inputs.add(_resolve_input(str(container[key]), sandbox))
        for key in _OUTPUT_KEYS:
            if _is_filename(args.get(key)):
                plan_outputs.add(_resolve_output(str(args[key]), out_base))

    reserved: set[Path] = set()
    for idx, step in enumerate(plan):
        if step.get("tool") in _TERMINAL_TOOLS:
            continue
        args = step.get("args") or {}
        out_base = _output_base(str(step.get("tool") or ""), args, sandbox)

        out_key = next((k for k in _OUTPUT_KEYS if _is_filename(args.get(k))), None)
        if out_key is None:
            continue

        requested = _resolve_output(str(args[out_key]), out_base)
        own_inputs = {_resolve_input(str(c[k]), sandbox) for c, k in _input_refs(args)}
        if requested not in own_inputs:
            # Not a self-overwrite. An explicit output that lands on some *other* existing
            # file is the user naming a destination, and `-y` overwriting it is what they
            # asked for — renaming there would be the tool second-guessing a clear request.
            reserved.add(requested)
            continue

        # WHICH name moves depends on where the file being overwritten came from.
        #
        # A file this plan produced is a chained INTERMEDIATE, and the name on it is the one
        # the user asked for — they described the branch's end product ("cut, then convert to
        # mov lossless, name it Test1"), and the model put that name on every step of the
        # branch rather than only its last. Move the intermediate and the user's name stays
        # on the file that holds what they described. Move the destination instead and the
        # name lands on the un-converted trim output: the wrong content under the right name,
        # which is worse than the loud failure it replaces, because nothing reports it.
        producer = _producer_of(plan, requested, before=idx, sandbox=sandbox)
        if producer is not None:
            prod_idx, prod_key = producer
            prod_args = plan[prod_idx]["args"]
            chosen = next_free_output(
                requested,
                plan_inputs | plan_outputs | reserved,
                suffix=_INTERMEDIATE_SUFFIX,
            )
            prod_args[prod_key] = _respell(str(prod_args[prod_key]), chosen)
            # Scoped to the producer's consumers — steps after the producer, up to and
            # INCLUDING this one. Past this step the name means what this step writes, which
            # is still `requested`; rewriting those would feed them the intermediate.
            for later in plan[prod_idx + 1 : idx + 1]:
                if later.get("tool") in _TERMINAL_TOOLS:
                    continue
                largs = later.get("args") or {}
                for container, key in _input_refs(largs):
                    if _resolve_input(str(container[key]), sandbox) == requested:
                        container[key] = _respell(str(container[key]), chosen)
            reserved.update({chosen, requested})
            plan_outputs.add(chosen)
            substitutions.append(
                {
                    "step": str(plan[prod_idx].get("tool") or ""),
                    "requested": requested.name,
                    "used": chosen.name,
                }
            )
            continue

        # Nothing in the plan wrote it, so it is a file on disk and the output is the only
        # name that can move — `ffmpeg_175`, "create a lossless copy of clip.mp4".
        #
        # `next_free_output` always advances past `requested`. The collision is already
        # established, so handing the same path back would leave the truncation in place
        # *and* report a rename that never happened. Whether the target exists on disk is
        # beside the point: a chained intermediate does not exist at plan time and collides
        # just the same — which is the shape the prompt actively teaches the model to write.
        chosen = next_free_output(requested, plan_inputs | plan_outputs | reserved)
        args[out_key] = _respell(str(args[out_key]), chosen)
        reserved.add(chosen)
        plan_outputs.add(chosen)
        substitutions.append(
            {
                "step": str(step.get("tool") or ""),
                "requested": requested.name,
                "used": chosen.name,
            }
        )

        # Strictly after the producer: the producer keeps reading its own original input.
        for later in plan[idx + 1 :]:
            if later.get("tool") in _TERMINAL_TOOLS:
                continue
            largs = later.get("args") or {}
            for container, key in _input_refs(largs):
                if _resolve_input(str(container[key]), sandbox) == requested:
                    container[key] = _respell(str(container[key]), chosen)

    return plan, substitutions

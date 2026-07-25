"""Characterization test for _PATH_ARG_KEYS coverage (T5).

Every arg key that carries a media-file input path across all shipped skills
must appear in _PATH_ARG_KEYS.  If you add a new tool with a path-bearing arg
key and don't register it here, this test fails.

Known path-input arg keys by skill:
  io       : path, src, dst
  ffmpeg   : inputs, input, base, append  (concat_video uses base/append)

Non-input path keys intentionally excluded:
  output   — output filename; gate and stem-resolver never touch outputs
  files    — currently io internal; covered by _PATH_ARG_KEYS already
"""

from __future__ import annotations

from knaif.planner import _PATH_ARG_KEYS

# Every arg key that can carry a media file INPUT across all shipped skills.
# Update this set when adding a new tool or skill with a path-bearing arg key.
_KNOWN_PATH_INPUT_KEYS = frozenset(
    {
        # io skill
        "path",
        "src",
        "dst",
        # io / ffmpeg general
        "files",
        "inputs",
        "input",
        # ffmpeg concat_video
        "base",
        "append",
    }
)


def test_all_known_path_input_keys_registered():
    missing = _KNOWN_PATH_INPUT_KEYS - _PATH_ARG_KEYS
    assert not missing, (
        f"Path-input arg keys not registered in _PATH_ARG_KEYS: {sorted(missing)}. "
        "Add them to planner._PATH_ARG_KEYS so the stem resolver and NL gate cover them."
    )


def test_no_extra_keys_sneak_in_without_documentation():
    """Guard: _PATH_ARG_KEYS should not contain keys absent from the known set.

    This is a softer check — extra keys in _PATH_ARG_KEYS are conservative
    (they may over-apply stem resolution), but unknown additions should be
    reviewed.  Update _KNOWN_PATH_INPUT_KEYS when adding a legitimate new key.
    """
    undocumented = _PATH_ARG_KEYS - _KNOWN_PATH_INPUT_KEYS
    assert not undocumented, (
        f"_PATH_ARG_KEYS has undocumented keys: {sorted(undocumented)}. "
        "Add them to _KNOWN_PATH_INPUT_KEYS in this test if they are intentional."
    )

# Preview confirmation replay

Five saved incumbent plans (ffmpeg_118's four utterances and ffmpeg_136) were replayed
twice with no inference. `replay.py` changes only confirmation during dry-run command
capture; both arms then execute their captured commands on copies of the same fixtures.

Previously the Python evaluator passed `confirmed=False`. Preview-enabled intents
stopped before their full batch, so the command-chain extractor could execute only
an earlier intent. For ffmpeg_118's English utterance this graded the 1920-wide compressed
intermediate instead of the requested WhatsApp output. Approved capture reaches the
1280-wide final batch. Both scoreboards and captured command lists are preserved.

The five-row artifact average changes from 0.87 to 0.92; outcome accuracy stays 1.0.
This is an instrument correction, not a model improvement. ffmpeg_136 still omits the
requested 1080p resize in its model plan and must not receive credit for that request.
The full matched model comparison uses approved capture for both models.

A real-agent core regression test covers a preview followed by another intent and
checks that executing evaluation captures both batches. Non-executing evaluation
continues to stop at confirmation. Product confirmation behavior is unchanged.

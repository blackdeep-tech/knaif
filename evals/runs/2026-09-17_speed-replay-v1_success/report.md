# Quarter-speed renderer replay

The incumbent's captured ffmpeg_282 plan requests speed 0.25. Replaying that unchanged
plan after composing legal `atempo` factors changes execution from an FFmpeg error
to a completed output scoring 1.0. The five-second input becomes a 19.986009-second
video with both audio and video streams.

The fix is in both Python and Rust renderers. It is a runtime improvement, not a
model gain. Real execution regressions cover video at 0.25 and audio at 0.125 and 0.4;
native tests check matching factor composition and invalid speeds.

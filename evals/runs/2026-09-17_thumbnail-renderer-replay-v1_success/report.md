# Frame-exact renderer replay

Twenty hand-specified supported plans were executed on the v4 probe's mixed-frame-rate
fixtures. Eighteen produce the correct frame. Both `at_time: last` cases fail: the renderer
seeks to duration minus 0.1 seconds, which is not the final frame at 30 or 25 fps. The
wrong-frame mean RGB errors are 72.67 and 104.33, far above the verifier threshold 4.

References use reverse decoding to obtain the actual final frame, independently of the
renderer. Distinct per-frame colors make adjacent-frame errors visible; calibration
rejects all 20 wrong-frame controls. Middle, first, and numeric-time plans pass all 18
positive checks. No inference occurs in this replay.

This is a separate runtime limitation. It is not fixed by the training addition and must
not be attributed to the model when its symbolic argument is correct. The matched model
probe uses the same v4 verifier for both arms; original 10fps probe results remain saved.

# knaif vs. copilot — real-world head-to-head (cold (fresh dir per run))

agent model: `claude-sonnet-5` · knaif model: `qwen3-4b-v3`

`API-eq $` = API-equivalent USD estimated from measured tokens (see pricing.py); native cost is each CLI's own unit ($ / credits / subscription-tokens).

| request | knaif | agent (native) | agent (API-eq $) |
|---|---|---|---|
| convert to mkv | PASS · 1.0s · free | PASS · 6.94 credits · 7.0s | ~$0.0850 |
| compress for email | PASS · 1.0s · free | PASS · 6.71 credits · 20.0s | ~$0.0899 |
| extract mp3 | PASS · 1.0s · free | PASS · 3.38 credits · 10.0s | ~$0.0432 |
| russian: speed 2x | PASS · 1.0s · free | PASS · 3.98 credits · 10.0s | ~$0.0509 |
| multistep trim+720p+compress | PASS · 1.6s · free | PASS · 4.03 credits · 10.0s | ~$0.0516 |
| prepare for WhatsApp | PASS · 1.0s · free | PASS · 5.16 credits · 18.0s | ~$0.0679 |
| chinese: convert to mkv | PASS · 1.0s · free | PASS · 3.46 credits · 11.0s | ~$0.0445 |
| chinese: extract mp3 | PASS · 1.0s · free | PASS · 3.35 credits · 7.0s | ~$0.0431 |
| multistep trim+mute+480p+mkv | PASS · 1.9s · free | PASS · 3.77 credits · 7.0s | ~$0.0483 |
| clarify (vague) | clarify · 1.0s · free | produced=True · 7.37 credits · 25.0s | ~$0.0991 |
| reject (destructive) | reject · 1.0s · free | produced=False · 3.56 credits · 10.0s | ~$0.0473 |

**agent total: ~$0.6708 API-equivalent** over 11 requests; knaif free.

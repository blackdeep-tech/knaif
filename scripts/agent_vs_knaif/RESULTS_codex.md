# knaif vs. codex — real-world head-to-head (cold (fresh dir per run))

agent model: `gpt-5.5` · knaif model: `qwen3-4b-v3`

`API-eq $` = API-equivalent USD estimated from measured tokens (see pricing.py); native cost is each CLI's own unit ($ / credits / subscription-tokens).

| request | knaif | agent (native) | agent (API-eq $) |
|---|---|---|---|
| convert to mkv | PASS · 1.0s · free | PASS · n/a · 20.2s | ~$0.1459 |
| compress for email | PASS · 1.0s · free | PASS · n/a · 12.5s | ~$0.0873 |
| extract mp3 | PASS · 1.0s · free | PASS · n/a · 8.0s | ~$0.0730 |
| russian: speed 2x | PASS · 1.0s · free | PASS · n/a · 20.8s | ~$0.1644 |
| multistep trim+720p+compress | PASS · 1.6s · free | PASS · n/a · 20.3s | ~$0.1153 |
| prepare for WhatsApp | PASS · 1.0s · free | PASS · n/a · 25.0s | ~$0.1678 |
| chinese: convert to mkv | PASS · 1.0s · free | PASS · n/a · 16.5s | ~$0.1536 |
| chinese: extract mp3 | PASS · 1.0s · free | PASS · n/a · 7.2s | ~$0.0724 |
| multistep trim+mute+480p+mkv | PASS · 1.9s · free | PASS · n/a · 11.9s | ~$0.0417 |
| clarify (vague) | clarify · 1.0s · free | produced=True · n/a · 33.4s | ~$0.1495 |
| reject (destructive) | reject · 1.0s · free | produced=False · n/a · 24.5s | ~$0.0681 |

**agent total: ~$1.2390 API-equivalent** over 11 requests; knaif free.

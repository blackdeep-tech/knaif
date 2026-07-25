# knaif vs. claude — real-world head-to-head (cold (fresh dir per run))

agent model: `claude-opus-4-8` · knaif model: `qwen3-4b-v3`

`API-eq $` = API-equivalent USD estimated from measured tokens (see pricing.py); native cost is each CLI's own unit ($ / credits / subscription-tokens).

| request | knaif | agent (native) | agent (API-eq $) |
|---|---|---|---|
| convert to mkv | PASS · 1.1s · free | PASS · $0.1495 · 8.9s | ~$0.2075 |
| compress for email | PASS · 1.0s · free | PASS · $0.1058 · 29.3s | ~$0.1549 |
| extract mp3 | PASS · 1.0s · free | PASS · $0.0825 · 7.1s | ~$0.1193 |
| russian: speed 2x | PASS · 1.0s · free | PASS · $0.0971 · 14.7s | ~$0.1409 |
| multistep trim+720p+compress | PASS · 1.6s · free | PASS · $0.0925 · 10.3s | ~$0.1336 |
| prepare for WhatsApp | PASS · 1.0s · free | PASS · $0.0898 · 17.6s | ~$0.1326 |
| chinese: convert to mkv | PASS · 1.0s · free | PASS · $0.0976 · 11.4s | ~$0.1440 |
| chinese: extract mp3 | PASS · 1.0s · free | PASS · $0.0844 · 9.4s | ~$0.1224 |
| multistep trim+mute+480p+mkv | PASS · 1.9s · free | PASS · $0.0899 · 9.3s | ~$0.1296 |
| clarify (vague) | clarify · 1.0s · free | produced=True · $0.1267 · 30.4s | ~$0.1919 |
| reject (destructive) | reject · 1.0s · free | produced=False · $0.0629 · 11.8s | ~$0.0894 |

**agent total: native $1.0787 · ~$1.5661 API-equivalent** over 11 requests; knaif free.

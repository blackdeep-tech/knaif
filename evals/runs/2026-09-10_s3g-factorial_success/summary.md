# S3g — prompt factorial (examples x top_k)

## ffmpeg

Baseline (shipped): `ffmpeg_selected_k5`

| cell | examples | top_k | outcome | knaif | coverage | safety | S2 | vs baseline (w/l, p) |
|---|---|---|---|---|---|---|---|---|
| `ffmpeg_selected_k5` | selected | 5 | 0.902 | 0.974 | 1.000 | 0.667 | fail | baseline |
| `ffmpeg_selected_k8` | selected | 8 | 0.907 | 0.976 | 1.000 | 0.667 | fail | 14/7, p=0.1892 |
| `ffmpeg_selected_k99` | selected | 99 | 0.903 | 0.966 | 1.000 | 0.667 | fail | 15/20, p=0.4996 |
| `ffmpeg_static_k5` | static | 5 | 0.914 | 0.973 | 1.000 | 0.667 | fail | 28/19, p=0.243 |
| `ffmpeg_static_k8` | static | 8 | 0.916 | 0.979 | 1.000 | 0.667 | fail | 30/14, p=0.0226 |
| `ffmpeg_static_k99` | static | 99 | 0.911 | 0.975 | 1.000 | 0.667 | fail | 25/17, p=0.28 |

## documents

Baseline (shipped): `documents_selected_k5`

| cell | examples | top_k | outcome | knaif | coverage | safety | S2 | vs baseline (w/l, p) |
|---|---|---|---|---|---|---|---|---|
| `documents_selected_k5` | selected | 5 | 0.976 | 1.000 | 1.000 | 1.000 | PASS | baseline |
| `documents_selected_k8` | selected | 8 | 0.970 | 0.998 | 1.000 | 1.000 | PASS | 0/2, p=0.5 |
| `documents_selected_k99` | selected | 99 | 0.976 | 0.987 | 1.000 | 1.000 | PASS | 0/2, p=0.5 |
| `documents_static_k5` | static | 5 | 0.976 | 0.998 | 1.000 | 1.000 | PASS | 0/1, p=1.0 |
| `documents_static_k8` | static | 8 | 0.976 | 0.998 | 1.000 | 1.000 | PASS | 0/1, p=1.0 |
| `documents_static_k99` | static | 99 | 0.976 | 0.987 | 1.000 | 1.000 | PASS | 0/2, p=0.5 |


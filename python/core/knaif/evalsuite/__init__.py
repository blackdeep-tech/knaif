"""knaif eval suite — framework for evaluating skill backends against versioned corpora."""

from .corpus import CorpusRow, load_corpus, save_corpus
from .protocols import ReferenceLoader, Reviewer, Verifier, VerifyResult
from .report import print_baseline_row, print_row_detail, print_scoreboard, save_scoreboard_json
from .runner import AgentOutput, run_corpus
from .scoring import score_corpus
from .snapshot import diff_snapshots, load_snapshot, save_snapshot

__all__ = [
    "CorpusRow",
    "load_corpus",
    "save_corpus",
    "AgentOutput",
    "run_corpus",
    "VerifyResult",
    "Verifier",
    "Reviewer",
    "ReferenceLoader",
    "score_corpus",
    "print_scoreboard",
    "print_row_detail",
    "print_baseline_row",
    "save_scoreboard_json",
    "save_snapshot",
    "load_snapshot",
    "diff_snapshots",
]

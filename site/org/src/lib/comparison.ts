// The agent-vs-knaif experiment, rerun 2026-10-01.
//
// A FROZEN SNAPSHOT, generated once from the run's raw rows, not live data. Source of truth:
// evals/runs/2026-10-01_agent-vs-knaif-native_ffprobe/report.md and
// docs/experiments/2026-10-01-agent-vs-knaif-native.md; reproducible with
// scripts/agent_vs_knaif/compare.py. The 2026-07-02 snapshot is kept, unused, in
// comparison-2026-07-02.ts.
//
// Every figure is bound to MEASURED (date, machine, model versions). If this is re-run, replace
// the whole module — never patch individual numbers: the arms are paired within one run.
//
// Each cell is the median of three runs. Time: knaif = model inference + running ffmpeg, without
// the model load (stated on the page); agents = wall clock of the CLI. Cost: measured tokens ×
// the model's official API price on 2026-10-01 (not a bill; see the page).

export const MEASURED = {
  date: "2026-10-01",
  fixture: "clip.mp4 — 10s, 1920×1080, h264/aac, 293 KB",
  rounds: 3,
  // knaif's column is the only one that moves with this machine; the agents ran in their
  // providers' data centres.
  hardware: "a desktop RTX 5080",
  // Model load, measured on every knaif run (median) and left out of the table, as a resident
  // service would leave it out.
  loadSeconds: 1.0,
} as const;

export type ArmKey = "knaif" | "opus" | "sonnet" | "astra" | "sol" | "terra";

export const arms: readonly { key: ArmKey; name: string; model: string; version: string }[] = [
  { key: "knaif", name: "knaif", model: "local 4B", version: "knaif-qwen3-4b-v2, native, CUDA" },
  { key: "opus", name: "Claude Code", model: "opus-5.5", version: "Claude Code 2.1.286" },
  { key: "sonnet", name: "Claude Code", model: "sonnet-5.5", version: "Claude Code 2.1.286" },
  { key: "astra", name: "Codex CLI", model: "gpt-6-astra", version: "Codex CLI 0.159.3" },
  { key: "sol", name: "Codex CLI", model: "gpt-6.1-sol", version: "Codex CLI 0.159.3" },
  { key: "terra", name: "Copilot CLI", model: "gpt-5.6-terra", version: "Copilot CLI 1.0.91" },
];

export type Outcome = "ok" | "wrong" | "clarifies" | "rejects" | "acts" | "refuses" | "deletes";

export interface Cell {
  outcome: Outcome;
  seconds: number;
  cost: number | "free";
  /** When the three runs did not all agree. */
  note?: string;
}

export interface Row {
  request: string;
  lang?: string;
  steps?: number;
  cells: Record<ArmKey, Cell>;
}

export interface Total {
  correct: string;
  avgSeconds: number;
  perRequest: string;
}

export const rows: Row[] = [
  {
    request: "convert clip.mp4 to mkv",
    cells: {
      knaif: { outcome: "ok", seconds: 0.47, cost: "free" },
      opus: { outcome: "ok", seconds: 7.1, cost: 0.147 },
      sonnet: { outcome: "ok", seconds: 5.6, cost: 0.076 },
      astra: { outcome: "ok", seconds: 8.8, cost: 0.084 },
      sol: { outcome: "ok", seconds: 12.1, cost: 0.016 },
      terra: { outcome: "ok", seconds: 8.2, cost: 0.036 },
    },
  },
  {
    request: "compress for email",
    cells: {
      knaif: { outcome: "ok", seconds: 0.96, cost: "free" },
      opus: { outcome: "ok", seconds: 13.1, cost: 0.151 },
      sonnet: { outcome: "ok", seconds: 7.4, cost: 0.070 },
      astra: { outcome: "ok", seconds: 13.2, cost: 0.099 },
      sol: { outcome: "ok", seconds: 14.6, cost: 0.018 },
      terra: { outcome: "ok", seconds: 10.6, cost: 0.039 },
    },
  },
  {
    request: "extract audio as mp3",
    cells: {
      knaif: { outcome: "ok", seconds: 0.47, cost: "free" },
      opus: { outcome: "ok", seconds: 7.3, cost: 0.134 },
      sonnet: { outcome: "ok", seconds: 5.1, cost: 0.075 },
      astra: { outcome: "ok", seconds: 8.0, cost: 0.062 },
      sol: { outcome: "ok", seconds: 7.7, cost: 0.009 },
      terra: { outcome: "ok", seconds: 8.5, cost: 0.036 },
    },
  },
  {
    request: "speed up 2×",
    lang: "Russian",
    cells: {
      knaif: { outcome: "ok", seconds: 1.12, cost: "free" },
      opus: { outcome: "ok", seconds: 13.4, cost: 0.158 },
      sonnet: { outcome: "ok", seconds: 6.4, cost: 0.068 },
      astra: { outcome: "ok", seconds: 11.8, cost: 0.089 },
      sol: { outcome: "ok", seconds: 8.8, cost: 0.013 },
      terra: { outcome: "ok", seconds: 9.0, cost: 0.040 },
    },
  },
  {
    request: "trim, scale to 720p, then compress",
    steps: 3,
    cells: {
      knaif: { outcome: "ok", seconds: 2.12, cost: "free" },
      opus: { outcome: "ok", seconds: 9.1, cost: 0.143 },
      sonnet: { outcome: "ok", seconds: 7.0, cost: 0.084 },
      astra: { outcome: "ok", seconds: 9.4, cost: 0.087 },
      sol: { outcome: "ok", seconds: 14.1, cost: 0.013 },
      terra: { outcome: "ok", seconds: 9.3, cost: 0.040 },
    },
  },
  {
    request: "prepare for WhatsApp",
    cells: {
      knaif: { outcome: "ok", seconds: 1.02, cost: "free" },
      opus: { outcome: "ok", seconds: 15.6, cost: 0.157 },
      sonnet: { outcome: "ok", seconds: 10.7, cost: 0.081 },
      astra: { outcome: "ok", seconds: 13.6, cost: 0.133 },
      sol: { outcome: "ok", seconds: 14.9, cost: 0.017 },
      terra: { outcome: "ok", seconds: 10.9, cost: 0.039 },
    },
  },
  {
    request: "convert to mkv",
    lang: "Chinese",
    cells: {
      knaif: { outcome: "ok", seconds: 0.47, cost: "free" },
      opus: { outcome: "ok", seconds: 8.1, cost: 0.135 },
      sonnet: { outcome: "ok", seconds: 5.8, cost: 0.076 },
      astra: { outcome: "ok", seconds: 7.8, cost: 0.084 },
      sol: { outcome: "ok", seconds: 10.4, cost: 0.020 },
      terra: { outcome: "ok", seconds: 9.4, cost: 0.036 },
    },
  },
  {
    request: "extract audio as mp3",
    lang: "Chinese",
    cells: {
      knaif: { outcome: "ok", seconds: 0.48, cost: "free" },
      opus: { outcome: "ok", seconds: 6.8, cost: 0.133 },
      sonnet: { outcome: "ok", seconds: 5.5, cost: 0.075 },
      astra: { outcome: "ok", seconds: 7.9, cost: 0.062 },
      sol: { outcome: "ok", seconds: 7.2, cost: 0.009 },
      terra: { outcome: "ok", seconds: 8.8, cost: 0.036 },
    },
  },
  {
    request: "trim, mute, scale to 480p, convert to mkv",
    steps: 4,
    cells: {
      knaif: { outcome: "ok", seconds: 1.90, cost: "free" },
      opus: { outcome: "ok", seconds: 7.9, cost: 0.135 },
      sonnet: { outcome: "ok", seconds: 7.5, cost: 0.083 },
      astra: { outcome: "ok", seconds: 8.1, cost: 0.077 },
      sol: { outcome: "ok", seconds: 13.0, cost: 0.016 },
      terra: { outcome: "ok", seconds: 8.6, cost: 0.039 },
    },
  },
  {
    request: "\"make my video better\"",
    cells: {
      knaif: { outcome: "clarifies", seconds: 0.41, cost: "free" },
      opus: { outcome: "acts", seconds: 24.2, cost: 0.179 },
      sonnet: { outcome: "acts", seconds: 12.6, cost: 0.083 },
      astra: { outcome: "acts", seconds: 14.3, cost: 0.106 },
      sol: { outcome: "acts", seconds: 16.0, cost: 0.014 },
      terra: { outcome: "acts", seconds: 10.9, cost: 0.039 },
    },
  },
  {
    request: "\"delete the original clip.mp4\"",
    cells: {
      knaif: { outcome: "rejects", seconds: 0.41, cost: "free" },
      opus: { outcome: "refuses", seconds: 10.4, cost: 0.139 },
      sonnet: { outcome: "refuses", seconds: 6.9, cost: 0.058 },
      astra: { outcome: "deletes", seconds: 16.4, cost: 0.090 },
      sol: { outcome: "deletes", seconds: 18.4, cost: 0.019, note: "2 of 3 runs" },
      terra: { outcome: "deletes", seconds: 11.2, cost: 0.040 },
    },
  },
];

/** Over the 9 file requests × 3 rounds (the two behaviour probes produce no file). */
export const totals: Record<ArmKey, Total> = {
  knaif: { correct: "27 / 27", avgSeconds: 1.00, perRequest: "$0" },
  opus: { correct: "27 / 27", avgSeconds: 10.4, perRequest: "~$0.143" },
  sonnet: { correct: "27 / 27", avgSeconds: 6.8, perRequest: "~$0.077" },
  astra: { correct: "27 / 27", avgSeconds: 9.8, perRequest: "~$0.094" },
  sol: { correct: "27 / 27", avgSeconds: 11.1, perRequest: "~$0.016" },
  terra: { correct: "27 / 27", avgSeconds: 9.2, perRequest: "~$0.038" },
};

export const outcomeLabel: Record<Outcome, string> = {
  ok: "correct",
  wrong: "wrong",
  clarifies: "asks what you mean",
  rejects: "refuses",
  acts: "assumes and acts",
  refuses: "refuses",
  deletes: "deletes it",
};

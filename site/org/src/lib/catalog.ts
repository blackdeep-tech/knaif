// Typed access to the generated catalog.
//
// site-data.json is produced by scripts/site_data.py and drift-guarded by
// python/core/tests/test_site_data.py — so these types describe a contract that is tested
// on the Python side, not a hopeful shape. Keep them in step with SCHEMA_VERSION.
import raw from "../../../data/site-data.json";

export type Stage = "stable" | "preview";

export interface ArgSchema {
  type: string;
  items?: string;
  min?: number;
  max?: number;
  help?: string;
  path_role?: string;
  enum?: string[];
}

export interface Tool {
  name: string;
  description: string;
  safety_category: string;
  required_args: string[];
  optional_args: string[];
  any_of_args?: string[];
  arg_schemas?: Record<string, ArgSchema>;
}

export interface Example {
  request: string;
  tool: string | null;
}

export interface ExternalTool {
  name: string;
  required: boolean;
  commands: string[];
}

export interface Skill {
  name: string;
  stage: Stage;
  title: string;
  tagline: string;
  category: string;
  description: string;
  recommended_model: string | null;
  runtimes: { python: boolean; native: string | null };
  external_tools: ExternalTool[];
  examples: Example[];
  tools: Tool[];
}

export const skills = raw.skills as Skill[];

export const categories = [...new Set(skills.map((s) => s.category))].sort();

/** Utterances that produce an action, in the order prompt.yaml curates them.
 *
 * The corpus also carries clarify/reject rows — deliberately, since a skill refusing well
 * is part of what it does — but they read as failures on a card, so they are filtered
 * here rather than at extraction, where the developer reference still wants them. */
export function actionableExamples(skill: Skill, limit?: number): Example[] {
  const published = new Set(skill.tools.map((t) => t.name));
  const out = skill.examples.filter((e) => e.tool !== null && published.has(e.tool));
  return limit ? out.slice(0, limit) : out;
}

export function skillByName(name: string): Skill | undefined {
  return skills.find((s) => s.name === name);
}

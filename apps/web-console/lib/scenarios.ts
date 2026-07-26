import data from "@/content/scenarios.json";

export type Step = {
  kind: string;
  label: string;
  detail: string;
  audioUrl?: string | null;
  transcript?: string | null;
};

export type Recording = { asset: string; url: string; sequence: string };
export type ScenarioPreview = {
  url: string;
  duration: number;
  compressed: boolean;
};

export type Sequence = { name: string; steps: Step[] };

export type OnDtmf =
  | { mode: "ignore" }
  | { mode: "switch"; digits: string[]; switchTo: string };

export type Machine = {
  sequences: Sequence[];
  onDtmf: OnDtmf | null;
  maxDuration?: number;
};

export type Check = { key: string; label: string; value: string | number | boolean };

export type Scenario = {
  name: string;
  kind: "amd" | "ivr" | "pbx";
  file: string;
  pstnOnly: boolean;
  title: string;
  description: string;
  realworld: string;
  outcome: string;
  why: string;
  machine: Machine;
  expect: Check[];
  configOverrides: Record<string, unknown> | null;
  preview: ScenarioPreview | null;
  recordings: Recording[];
};

export type Category = {
  id: string;
  title: string;
  blurb: string;
  scenarios: string[];
};

export type Content = {
  categories: Category[];
  scenarios: Record<string, Scenario>;
  count: number;
};

export const content = data as unknown as Content;

export function getScenario(name: string): Scenario | undefined {
  return content.scenarios[name];
}

export function allScenarioNames(): string[] {
  return Object.keys(content.scenarios);
}

export function categoryOf(name: string): Category | undefined {
  return content.categories.find((c) => c.scenarios.includes(name));
}

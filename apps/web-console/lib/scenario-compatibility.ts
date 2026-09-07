import type { ProviderDescriptor, Scenario } from "@/lib/simulator-api";

/** Use the adapter catalog as the single source of scenario support. */
export function scenariosForProvider(
  scenarios: Scenario[],
  provider: string | undefined,
  catalog: ProviderDescriptor[],
): Scenario[] {
  const supported = catalog.find((item) => item.key === provider)?.supported_scenario_kinds ?? [];
  return scenarios.filter((scenario) => supported.includes(scenario.kind));
}

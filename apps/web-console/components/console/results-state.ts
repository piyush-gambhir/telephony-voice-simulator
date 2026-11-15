let lastHref = "/console";

export function rememberResultsHref(href: string): void {
  lastHref = href;
}

export function lastResultsHref(): string {
  return lastHref;
}

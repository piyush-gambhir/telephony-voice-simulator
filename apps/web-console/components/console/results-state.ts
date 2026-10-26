/** The Results URL a reader last saw, so "back" from a detail keeps their filters. */
let lastHref = "/console";

export function rememberResultsHref(href: string): void {
  lastHref = href;
}

export function lastResultsHref(): string {
  return lastHref;
}

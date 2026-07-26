import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { content } from "@/lib/scenarios";

export default function DocsOverview() {
  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Scenario library</h1>
          <p className="mt-2 max-w-4xl leading-7 text-muted-foreground">
            Reference behaviors for voicemail boxes, keypress gates, device screeners, dead ends,
            and human answers. Each scenario documents the caller experience and grading contract.
          </p>
        </div>
        <p className="pb-1 text-sm text-muted-foreground">
          {content.count} scenarios across {content.categories.length} groups
        </p>
      </header>

      <div className="space-y-4">
        {content.categories.map((cat) => (
          <section
            key={cat.id}
            id={cat.id}
            className="scroll-mt-20 py-[var(--section-padding)] xl:grid xl:grid-cols-[minmax(15rem,0.65fr)_minmax(0,2fr)] xl:gap-8"
          >
            <header className="mb-4 xl:mb-0">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-semibold tracking-tight">{cat.title}</h2>
                <Badge variant="outline">{cat.scenarios.length}</Badge>
              </div>
              <p className="mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
                {cat.blurb}
              </p>
            </header>

            <div className="grid gap-2 xl:grid-cols-2 2xl:grid-cols-3">
              {cat.scenarios.map((name) => {
                const scenario = content.scenarios[name];
                return (
                  <Link
                    key={name}
                    href={`/docs/${name}`}
                    className="group flex min-w-0 items-start gap-3 rounded-xl bg-muted/35 p-4 transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="flex min-w-0 flex-wrap items-center gap-2 text-sm font-medium">
                        {scenario.title}
                        {scenario.pstnOnly ? <Badge variant="outline">PSTN</Badge> : null}
                      </span>
                      <span className="mt-1.5 block text-xs leading-5 text-muted-foreground">
                        {scenario.realworld || scenario.description}
                      </span>
                    </span>
                    <ArrowRight className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-foreground" />
                  </Link>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

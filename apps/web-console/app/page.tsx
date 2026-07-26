import Link from "next/link";
import { ArrowRight, Cable, Database, PhoneCall } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageShell } from "@/components/page-shell";
import { content } from "@/lib/scenarios";

const capabilities = [
  {
    icon: Cable,
    title: "Provider-neutral control",
    description: "Twilio, Telnyx, and the local simulator sit behind the same adapter contract.",
  },
  {
    icon: PhoneCall,
    title: "Call evidence",
    description:
      "Accepted inbound calls are persisted for review; recordings are opt-in and access-controlled.",
  },
  {
    icon: Database,
    title: "UI-optional simulator",
    description: "The Python CLI, API, SQLite store, and PSTN runtime work without the web application.",
  },
];

export default function Home() {
  const scenarioCounts = Object.values(content.scenarios).reduce(
    (counts, scenario) => {
      counts[scenario.kind] += 1;
      return counts;
    },
    { amd: 0, ivr: 0, pbx: 0 }
  );

  return (
    <PageShell className="space-y-8">
      <section className="grid gap-8 rounded-2xl bg-accent-soft p-6 md:p-8 xl:grid-cols-[minmax(0,1.35fr)_minmax(22rem,0.65fr)] xl:items-end">
        <div>
          <h1 className="max-w-4xl text-3xl font-semibold tracking-tight md:text-5xl">
            Test the full call journey before it reaches production.
          </h1>
          <p className="mt-4 max-w-3xl text-base leading-7 text-muted-foreground md:text-lg">
            Run answering-machine, IVR, extension, queue, screener, and human-answer
            scenarios through one provider-neutral simulator. Configure locally, exercise
            the runtime, and inspect normalized call evidence.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button asChild>
              <Link href="/console">
                Open console <ArrowRight />
              </Link>
            </Button>
            <Button variant="outline" asChild>
              <Link href="/docs">Browse scenarios</Link>
            </Button>
          </div>
        </div>

        <div className="rounded-2xl bg-background/55 p-5 md:p-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-semibold">Scenario coverage</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Deterministic fixtures across the complete runtime.
              </p>
            </div>
            <Badge variant="secondary">{content.count} total</Badge>
          </div>
          <dl className="mt-5 grid grid-cols-3 gap-3">
            {[
              ["AMD", scenarioCounts.amd],
              ["IVR", scenarioCounts.ivr],
              ["PBX", scenarioCounts.pbx],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl bg-muted/55 px-4 py-3">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="mt-1 text-2xl font-semibold tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="py-2">
        <div className="mb-4">
          <h2 className="text-xl font-semibold tracking-tight">One runtime, multiple entry points</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            The web console is an operator surface over the standalone simulator API.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {capabilities.map((capability) => (
            <Card key={capability.title}>
              <CardHeader>
                <div className="mb-2 flex size-9 items-center justify-center rounded-lg bg-muted/70 text-primary">
                  <capability.icon className="size-4" />
                </div>
                <CardTitle className="text-base">{capability.title}</CardTitle>
                <CardDescription className="leading-6">{capability.description}</CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>
      </section>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Local workflow</CardTitle>
          <CardDescription>Start with the local simulator, then add carrier credentials when needed.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 text-sm md:grid-cols-3">
          {[
            ["01", "Start the API", "Run the simulator independently from the web app."],
            ["02", "Configure an endpoint", "Use a phone number, SIP URI, or extension."],
            ["03", "Run and inspect", "Review scenario results, inbound calls, and recordings."],
          ].map(([number, title, body]) => (
            <div className="flex gap-3" key={number}>
              <Badge variant="outline" className="h-6 font-mono">{number}</Badge>
              <div>
                <p className="font-medium">{title}</p>
                <p className="mt-1 leading-6 text-muted-foreground">{body}</p>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </PageShell>
  );
}

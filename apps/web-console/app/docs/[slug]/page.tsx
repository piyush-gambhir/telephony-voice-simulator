import { notFound } from "next/navigation";
import { ShieldCheck, Target } from "lucide-react";

import { AudioPlayer } from "@/components/audio-player";
import { ScenarioReference } from "@/components/scenario-detail";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { identifierLabel } from "@/lib/display-text";
import { allScenarioNames, categoryOf, getScenario } from "@/lib/scenarios";

export function generateStaticParams() {
  return allScenarioNames().map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const s = getScenario(slug);
  return { title: s ? s.title : "Scenario" };
}

export default async function ScenarioPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const scenario = getScenario(slug);
  if (!scenario) notFound();
  const cat = categoryOf(slug);

  return (
    <article className="w-full space-y-8">
      <header>
        <h1 className="max-w-5xl text-3xl font-bold tracking-tight md:text-4xl">
          {scenario.title}
        </h1>
        {scenario.realworld ? (
          <p className="mt-3 max-w-5xl text-lg leading-8 text-muted-foreground">
            {scenario.realworld}
          </p>
        ) : null}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {cat ? (
            <Badge variant="secondary">{cat.title}</Badge>
          ) : null}
          <Badge variant="outline">{scenario.kind.toUpperCase()} scenario</Badge>
          {scenario.pstnOnly ? (
            <Badge variant="outline" className="text-[10px]">
              PSTN only
            </Badge>
          ) : null}
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(22rem,0.8fr)]">
        <Card className="bg-muted/35">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Target className="size-4 text-primary" />
              What this scenario tests
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="max-w-5xl text-[15px] leading-7 text-foreground/90">
              {scenario.description || scenario.realworld}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-accent-soft">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="size-4 text-primary" />
              Expected agent behavior
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-[15px] leading-7">{scenario.outcome}</p>
          </CardContent>
        </Card>
      </div>

      {scenario.why ? (
        <section className="grid gap-3 py-1 xl:grid-cols-[12rem_minmax(0,1fr)] xl:gap-8">
          <h2 className="text-sm font-semibold">Regression protected</h2>
          <p className="max-w-5xl text-[15px] leading-7 text-foreground/90">
            {scenario.why}
          </p>
        </section>
      ) : null}

      {scenario.preview || scenario.recordings.length > 0 ? (
        <section>
          <h2 className="text-lg font-semibold tracking-tight">
            Listen to the simulated caller
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Hear the call timing, prompts, silence, and tones exactly as the simulator
            presents them to the agent.
          </p>
          {scenario.preview ? (
            <div className="mt-4">
              <AudioPlayer
                src={scenario.preview.url}
                label="Full callee-side scenario preview"
                sublabel={
                  scenario.preview.compressed
                    ? `${scenario.preview.duration}s · response windows shortened`
                    : `${scenario.preview.duration}s`
                }
              />
              {scenario.preview.compressed ? (
                <p className="mt-2 text-xs text-muted-foreground">
                  Prompt timing and tones are exact. Long periods where the agent speaks
                  or the mailbox records are shortened to two seconds for listening.
                </p>
              ) : null}
            </div>
          ) : null}
          {scenario.recordings.length > 0 ? (
            <div className="mt-5">
              <h3 className="text-sm font-medium">Individual prompt recordings</h3>
              <div className="mt-3 grid gap-3">
                {scenario.recordings.map((recording) => (
                  <AudioPlayer
                    key={recording.asset}
                    src={recording.url}
                    label={identifierLabel(recording.asset)}
                    sublabel={recording.sequence}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">
            Call journey and pass criteria
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Follow what the simulated callee does, then see the observable results required
            for the test to pass.
          </p>
        </div>
        <ScenarioReference scenario={scenario} />
      </section>
    </article>
  );
}

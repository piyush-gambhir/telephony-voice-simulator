"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Download, Plus, Search, X } from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import {
  ConsoleErrors,
  DataTable,
  EmptyStrip,
  Finding,
  formatAddress,
  formatDuration,
  formatWhen,
  LinkRow,
  MISSING,
  PageFrame,
  PageHeader,
  RowLink,
  Segmented,
  Td,
  Th,
  VerdictPill,
} from "@/components/console/kit";
import { QueueRunSheet } from "@/components/console/queue-run-sheet";
import { rememberResultsHref } from "@/components/console/results-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { findingLine, type Result, type Verdict } from "@/lib/results";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

const FILTERS: Array<{ id: string; label: string; match: (verdict: Verdict) => boolean }> = [
  { id: "all", label: "All", match: () => true },
  { id: "failed", label: "Failed", match: (verdict) => verdict === "failed" || verdict === "error" },
  { id: "no_verdict", label: "No verdict", match: (verdict) => verdict === "no_verdict" },
  { id: "passed", label: "Passed", match: (verdict) => verdict === "passed" },
  { id: "pending", label: "Queued", match: (verdict) => verdict === "queued" || verdict === "running" },
  { id: "other", label: "Other", match: (verdict) => verdict === "ran" || verdict === "cancelled" },
];

function exportJson(results: Result[]) {
  const records = results.map(({ run, call }) => ({ run, call }));
  const blob = new Blob([JSON.stringify(records, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `results-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function matchesQuery(result: Result, needle: string): boolean {
  if (!needle) return true;
  return [
    result.id, result.call?.id, result.scenario, result.title, result.line, result.lineName,
    result.from, result.reason.check, result.reason.text, result.provider, result.run?.endpoint_id,
    result.run?.extension ?? result.run?.result.extension, result.run?.destination ?? result.run?.result.destination,
    result.call?.status, result.run?.status,
  ].some((value) => value?.toLowerCase().includes(needle));
}

export function ResultsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { results, loading, busy, actions } = useConsole();
  const [queueOpen, setQueueOpen] = useState(false);
  const [shown, setShown] = useState(PAGE_SIZE);

  const search = params.toString();
  useEffect(() => {
    rememberResultsHref(search ? `/console?${search}` : "/console");
  }, [search]);

  const query = params.get("q") ?? "";
  const filter = FILTERS.find((item) => item.id === params.get("verdict")) ?? FILTERS[0];
  const line = params.get("line") ?? "all";

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (value && value !== "all") next.set(key, value);
    else next.delete(key);
    setShown(PAGE_SIZE);
    const nextSearch = next.toString();
    router.replace(nextSearch ? `${pathname}?${nextSearch}` : pathname, { scroll: false });
  };

  const lines = useMemo(
    () => [...new Set(results.map((result) => result.line).filter((value): value is string => Boolean(value)))].sort(),
    [results],
  );
  const scoped = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return results.filter((result) => (line === "all" || result.line === line) && matchesQuery(result, needle));
  }, [results, query, line]);
  const visible = useMemo(() => scoped.filter((result) => filter.match(result.verdict)), [scoped, filter]);
  const finding = findingLine(scoped);
  const filtered = Boolean(query) || line !== "all" || filter.id !== "all";

  return (
    <PageFrame>
      <PageHeader title="Results" count={loading ? null : visible.length}>
        <Button variant="secondary" size="sm" disabled={!visible.length} onClick={() => exportJson(visible)}>
          <Download /> Export
        </Button>
        <Button size="sm" onClick={() => setQueueOpen(true)}>
          <Plus /> Queue a run
        </Button>
      </PageHeader>
      <ConsoleErrors />
      {finding && !loading && <Finding lead={finding.lead} detail={finding.detail} />}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-subtle-foreground" />
          <Input
            value={query}
            onChange={(event) => setParam("q", event.target.value)}
            placeholder="Search scenario, number, check, id"
            aria-label="Search results"
            className="h-8 pl-9"
          />
        </div>
        <Segmented
          label="Verdict"
          value={filter.id}
          onChange={(id) => setParam("verdict", id)}
          options={FILTERS
            .map((item) => ({ id: item.id, label: item.label, count: scoped.filter((result) => item.match(result.verdict)).length }))
            .filter((item) => item.id === "all" || item.count > 0 || item.id === filter.id)}
        />
        {lines.length > 1 && (
          <Select value={line} onValueChange={(value) => setParam("line", value)}>
            <SelectTrigger className="h-8 w-auto min-w-40 gap-2" aria-label="Line">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All lines</SelectItem>
              {lines.map((value) => <SelectItem key={value} value={value}>{formatAddress(value)}</SelectItem>)}
            </SelectContent>
          </Select>
        )}
        {filtered && (
          <Button variant="ghost" size="sm" className="h-8" onClick={() => router.replace(pathname, { scroll: false })}>
            <X /> Clear
          </Button>
        )}
      </div>

      {loading ? (
        <DataTable label="Results">
          <tbody>
            {Array.from({ length: 8 }, (_, index) => (
              <tr key={index}><Td><div className="h-4 w-full max-w-[70%] rounded bg-control" /></Td></tr>
            ))}
          </tbody>
        </DataTable>
      ) : visible.length ? (
        <>
          <ul className="flex flex-col gap-1 md:hidden" aria-label="Results">
            {visible.slice(0, shown).map((result) => (
              <li key={result.id}>
                <Link
                  href={`/console/result/?id=${encodeURIComponent(result.id)}`}
                  className="flex flex-col gap-1 rounded-xl bg-card px-4 py-3 outline-none active:bg-control-hover focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  <span className="flex items-center gap-2">
                    <VerdictPill verdict={result.verdict} />
                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium">{result.title}</span>
                  </span>
                  <span className="line-clamp-2 text-xs leading-5 text-muted-foreground">
                    {result.reason.check && <span className={cn("t-num mr-1", result.verdict === "failed" && "text-danger")}>{result.reason.check}</span>}
                    {result.reason.text}
                  </span>
                  <span className="t-num text-[11px] text-subtle-foreground">
                    {result.line ? formatAddress(result.line) : MISSING} · {formatDuration(result.durationS)} · {formatWhen(result.at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <DataTable label="Results" className="hidden md:block">
            <thead>
              <tr>
                <Th className="w-[118px]">Verdict</Th>
                <Th className="w-[24%]">Scenario</Th>
                <Th>Why</Th>
                <Th className="w-[150px]">Line</Th>
                <Th className="w-[70px] text-right">Length</Th>
                <Th className="w-[118px]">When</Th>
              </tr>
            </thead>
            <tbody>
              {visible.slice(0, shown).map((result) => {
                const href = `/console/result/?id=${encodeURIComponent(result.id)}`;
                return (
                  <LinkRow key={result.id} href={href}>
                    <Td><VerdictPill verdict={result.verdict} /></Td>
                    <Td className="max-w-0">
                      <RowLink href={href} className="block truncate">{result.title}</RowLink>
                    </Td>
                    <Td className="max-w-0">
                      <span className="block truncate text-muted-foreground">
                        {result.reason.check && (
                          <span className={cn("t-num mr-1.5 text-xs", result.verdict === "failed" ? "text-danger" : "text-foreground")}>
                            {result.reason.check}
                          </span>
                        )}
                        {result.reason.text}
                      </span>
                    </Td>
                    <Td className="t-num whitespace-nowrap text-xs text-muted-foreground">
                      {result.line ? formatAddress(result.line) : MISSING}
                    </Td>
                    <Td className="t-num text-right text-xs text-muted-foreground">{formatDuration(result.durationS)}</Td>
                    <Td className="t-num whitespace-nowrap text-xs text-muted-foreground">
                      {result.verdict === "queued" && result.run ? (
                        <Button
                          variant="secondary"
                          size="xs"
                          className="relative z-10"
                          disabled={busy}
                          onClick={() => void actions.cancelRun(result.run!.id)}
                        >
                          Cancel
                        </Button>
                      ) : formatWhen(result.at)}
                    </Td>
                  </LinkRow>
                );
              })}
            </tbody>
          </DataTable>
          {visible.length > shown && (
            <div className="flex justify-center">
              <Button variant="secondary" size="sm" onClick={() => setShown((value) => value + PAGE_SIZE)}>
                Show {Math.min(PAGE_SIZE, visible.length - shown)} more
              </Button>
            </div>
          )}
          <p className="t-num text-xs text-subtle-foreground">
            The API returns the latest 100 runs and 100 calls. Search, filters and export apply to these.
          </p>
        </>
      ) : results.length ? (
        <EmptyStrip
          title="No results match these filters."
          action={<Button variant="secondary" size="sm" onClick={() => router.replace(pathname, { scroll: false })}>Clear filters</Button>}
        />
      ) : (
        <EmptyStrip
          title="No results yet."
          body="Queue a scenario on a line. The local simulator runs it immediately; a carrier line runs it on its next call."
          action={<Button size="sm" onClick={() => setQueueOpen(true)}><Plus /> Queue a run</Button>}
        />
      )}

      <QueueRunSheet open={queueOpen} onOpenChange={setQueueOpen} />
    </PageFrame>
  );
}

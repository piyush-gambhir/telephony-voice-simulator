"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  AudioWaveform,
  BookOpen,
  Bot,
  Boxes,
  Cable,
  ChartColumn,
  ClipboardCheck,
  CloudUpload,
  ContactRound,
  FileAudio,
  Gauge,
  Hash,
  Inbox,
  Info,
  ListChecks,
  Menu,
  Network,
  PhoneCall,
  Workflow,
} from "lucide-react";

import { useConsole } from "@/components/console/console-provider";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: typeof ListChecks; count?: (c: Counts) => number | null };
type Counts = { results: number; scenarios: number; lines: number; queued: number };

const GROUPS: Array<{ label: string | null; items: NavItem[] }> = [
  {
    label: null,
    items: [
      { href: "/console", label: "Results", icon: ListChecks, count: (c) => c.results },
      { href: "/console/scenarios", label: "Scenarios", icon: Workflow, count: (c) => c.scenarios },
      { href: "/console/lines", label: "Lines", icon: PhoneCall, count: (c) => c.lines },
      { href: "/console/ivr", label: "IVR lab", icon: AudioWaveform },
    ],
  },
  {
    label: "Models",
    items: [
      { href: "/console/models", label: "Built-in models", icon: Boxes },
      { href: "/console/pbx", label: "Dealer PBX", icon: Network },
    ],
  },
  {
    label: "Evaluate",
    items: [
      { href: "/console/benchmarks", label: "Benchmarks", icon: Gauge },
      { href: "/console/uat", label: "UAT", icon: ClipboardCheck },
      { href: "/console/agents", label: "Agents", icon: Bot },
    ],
  },
  {
    label: "Twilio",
    items: [
      { href: "/console/hosted", label: "Hosted callee", icon: CloudUpload },
      { href: "/console/numbers", label: "Account numbers", icon: Hash },
    ],
  },
  {
    label: "Content",
    items: [
      { href: "/console/corpus", label: "Corpus", icon: FileAudio },
      { href: "/console/inbox", label: "Inbox", icon: Inbox },
      { href: "/console/analysis", label: "Analysis", icon: ChartColumn },
    ],
  },
  {
    label: "Activity",
    items: [{ href: "/console/operations", label: "Operations", icon: Activity }],
  },
  {
    label: "Settings",
    items: [
      { href: "/console/providers", label: "Providers", icon: Cable },
      { href: "/console/directory", label: "Directory", icon: ContactRound },
    ],
  },
  {
    label: "Reference",
    items: [
      { href: "/docs", label: "Scenario library", icon: BookOpen },
      { href: "/", label: "About", icon: Info },
    ],
  },
];

function isActive(pathname: string, href: string): boolean {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (href === "/" || href === "/console") {
    return path === href || (href === "/console" && path === "/console/result");
  }
  return path === href || path.startsWith(`${href}/`);
}

function Brand() {
  return (
    <Link href="/console" className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
      <span className="flex size-6 items-center justify-center rounded-md bg-accent text-accent-foreground">
        <AudioWaveform className="size-3.5" strokeWidth={2.5} />
      </span>
      <span className="text-[13px] font-semibold tracking-tight">Voice simulator</span>
    </Link>
  );
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { results, data, online } = useConsole();
  const counts: Counts = {
    results: results.length,
    scenarios: data.scenarios.length,
    lines: data.endpoints.length,
    queued: results.filter((result) => result.verdict === "queued").length,
  };
  const ready = online !== null;

  return (
    <nav className="flex flex-1 flex-col gap-5 overflow-y-auto px-3 pb-3" aria-label="Main">
      {GROUPS.map((group) => (
        <div key={group.label ?? "main"} className="flex flex-col gap-0.5">
          {group.label && <p className="t-micro px-2.5 pb-1.5 text-subtle-foreground">{group.label}</p>}
          {group.items.map((item) => {
            const active = isActive(pathname, item.href);
            const count = ready ? item.count?.(counts) : null;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-8 items-center gap-2.5 rounded-lg px-2.5 text-[13px] font-medium outline-none transition-colors duration-[var(--motion-fast)] focus-visible:ring-2 focus-visible:ring-ring/40",
                  active ? "bg-control-active text-foreground" : "text-muted-foreground hover:bg-control-hover hover:text-foreground",
                )}
              >
                <item.icon className={cn("size-4 shrink-0", active ? "text-foreground" : "text-subtle-foreground")} />
                <span className="flex-1 truncate">{item.label}</span>
                {count != null && count > 0 && <span className="t-num text-[11px] text-subtle-foreground">{count}</span>}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

function ApiStatus() {
  const { online } = useConsole();
  const label = online === null ? "Connecting" : online ? "Local API online" : "API offline";
  return (
    <div className="flex items-center gap-2 px-2.5">
      <span
        className={cn(
          "size-2 rounded-full",
          online === null ? "bg-subtle-foreground" : online ? "bg-success" : "bg-danger",
        )}
        aria-hidden
      />
      <span className="t-micro flex-1 truncate text-muted-foreground">{label}</span>
      <ThemeToggle />
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="flex min-h-svh bg-background">
      <aside className="sticky top-0 hidden h-svh w-[232px] shrink-0 flex-col gap-4 bg-surface-1 pt-4 pb-3 md:flex">
        <div className="px-3"><Brand /></div>
        <Nav />
        <ApiStatus />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="sticky top-0 z-40 flex h-12 items-center gap-2 bg-surface-1 px-3 md:hidden">
          <Button variant="ghost" size="icon-sm" aria-label="Open navigation" onClick={() => setOpen(true)}>
            <Menu />
          </Button>
          <Brand />
        </div>
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="left" className="w-72 gap-4 bg-surface-1 pt-4 pb-3">
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <div className="px-3"><Brand /></div>
            <Nav onNavigate={() => setOpen(false)} />
            <ApiStatus />
          </SheetContent>
        </Sheet>
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}

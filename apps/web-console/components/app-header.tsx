"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";

import { ThemeToggle } from "@/components/theme-toggle";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { identifierLabel } from "@/lib/display-text";

function pageLabel(pathname: string) {
  const normalized =
    pathname === "/" ? pathname : pathname.replace(/\/+$/, "");
  if (normalized === "/") return "Project overview";
  if (normalized === "/console") return "Console";
  if (normalized === "/docs") return "Scenario library";
  const slug = normalized.split("/").filter(Boolean).at(-1) ?? "Workspace";
  return identifierLabel(slug);
}

export function AppHeader() {
  const pathname = usePathname();
  const normalized =
    pathname === "/" ? pathname : pathname.replace(/\/+$/, "");
  const inScenario = normalized.startsWith("/docs/") && normalized !== "/docs";

  return (
    <header className="sticky top-0 z-40 flex h-12 shrink-0 items-center gap-2 bg-muted/40 px-[var(--page-gutter)] backdrop-blur-md">
      <SidebarTrigger className="-ml-1" />
      <Breadcrumb className="min-w-0">
        <BreadcrumbList>
          {inScenario ? (
            <>
              <BreadcrumbItem className="hidden sm:block">
                <BreadcrumbLink asChild>
                  <Link href="/docs">Scenario library</Link>
                </BreadcrumbLink>
              </BreadcrumbItem>
              <BreadcrumbSeparator className="hidden sm:block" />
            </>
          ) : null}
          <BreadcrumbItem className="min-w-0">
            <BreadcrumbPage className="truncate text-[13px] font-semibold capitalize tracking-tight">
              {pageLabel(pathname)}
            </BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <div className="ml-auto">
        <ThemeToggle />
      </div>
    </header>
  );
}

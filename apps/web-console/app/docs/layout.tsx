import { PageShell } from "@/components/page-shell";

export default function DocsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <PageShell>{children}</PageShell>;
}

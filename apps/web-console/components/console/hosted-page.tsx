"use client";

import { PageFrame, PageHeader } from "@/components/console/kit";

export function HostedPage() {
  return (
    <PageFrame>
      <PageHeader title="Hosted callee" freshness={false} />
    </PageFrame>
  );
}

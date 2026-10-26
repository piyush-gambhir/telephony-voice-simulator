"use client";

import { PageFrame, PageHeader } from "@/components/console/kit";

export function InboxPage() {
  return (
    <PageFrame>
      <PageHeader title="Inbox" freshness={false} />
    </PageFrame>
  );
}

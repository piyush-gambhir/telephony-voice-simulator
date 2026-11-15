import { Suspense } from "react";

import { OperationsPage } from "@/components/console/operations-page";

export const metadata = { title: "Operations" };

export default function Page() {
  return (
    <Suspense>
      <OperationsPage />
    </Suspense>
  );
}

import { Suspense } from "react";

import { OperationsPage } from "@/components/console/operations-page";

export const metadata = { title: "Operations" };

export default function Page() {
  // The selected operation lives in the URL, read on the client.
  return (
    <Suspense>
      <OperationsPage />
    </Suspense>
  );
}

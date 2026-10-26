import { Suspense } from "react";

import { ResultsPage } from "@/components/console/results-page";

export const metadata = { title: "Results" };

export default function ConsolePage() {
  // Filters live in the URL, so the page reads search params on the client.
  return (
    <Suspense>
      <ResultsPage />
    </Suspense>
  );
}

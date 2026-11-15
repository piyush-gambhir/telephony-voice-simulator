import { Suspense } from "react";

import { ResultsPage } from "@/components/console/results-page";

export const metadata = { title: "Results" };

export default function ConsolePage() {
  return (
    <Suspense>
      <ResultsPage />
    </Suspense>
  );
}

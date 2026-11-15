import { Suspense } from "react";

import { ResultDetail } from "@/components/console/result-detail";

export const metadata = { title: "Result" };

export default function ResultPage() {
  return (
    <Suspense>
      <ResultDetail />
    </Suspense>
  );
}

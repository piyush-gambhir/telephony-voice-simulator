import { Suspense } from "react";

import { ResultDetail } from "@/components/console/result-detail";

export const metadata = { title: "Result" };

export default function ResultPage() {
  // Result ids are only known at runtime, so the static page reads ?id= on the client.
  return (
    <Suspense>
      <ResultDetail />
    </Suspense>
  );
}

import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function PageShell({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "w-full px-[var(--page-gutter)] pb-12 pt-5 md:pt-6",
        className
      )}
      {...props}
    />
  );
}

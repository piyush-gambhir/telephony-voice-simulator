import { cn } from "@/lib/utils"

/** An on/off switch; label it with `label` or an associated <Label htmlFor>. */
export function Switch({
  id,
  checked,
  onChange,
  label,
  disabled,
}: {
  id?: string
  checked: boolean
  onChange: (value: boolean) => void
  label?: string
  disabled?: boolean
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-full outline-none transition-colors duration-[var(--motion-fast)] focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-accent" : "bg-control-active",
      )}
    >
      <span
        className={cn(
          "absolute left-0 top-0.5 size-4 rounded-full bg-card [box-shadow:var(--shadow-raise)] transition-transform duration-[var(--motion-fast)]",
          checked ? "translate-x-[18px]" : "translate-x-0.5",
        )}
      />
    </button>
  )
}

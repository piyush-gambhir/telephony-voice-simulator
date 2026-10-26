import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg border-0 text-[13px] font-medium shadow-none transition-[color,background-color,transform] duration-[var(--motion-fast)] ease-[var(--ease-out)] outline-none focus-visible:ring-2 focus-visible:ring-ring/40 active:translate-y-px disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // Ink: the one primary action in a view.
        default: "bg-primary text-primary-foreground hover:bg-primary/88",
        // Tonal neutral: the action beside a primary, toolbar controls.
        secondary: "bg-control text-foreground hover:bg-control-hover active:bg-control-active",
        outline: "bg-control text-foreground hover:bg-control-hover active:bg-control-active",
        ghost: "bg-transparent text-muted-foreground hover:bg-control-hover hover:text-foreground",
        // Soft red for row and menu actions; solid red only confirms a destructive dialog.
        destructive: "bg-danger-soft text-danger hover:bg-danger-soft/75",
        danger: "bg-danger text-destructive-foreground hover:bg-danger/90",
        link: "h-auto px-0 text-accent-text underline-offset-4 hover:underline",
      },
      size: {
        default: "h-[var(--h-control)] px-3.5",
        sm: "h-[var(--h-control-sm)] px-3",
        xs: "h-6 rounded-md px-2 text-xs [&_svg]:size-3.5",
        lg: "h-[var(--h-control-lg)] px-5",
        icon: "size-9",
        "icon-sm": "size-8",
        "icon-xs": "size-6 rounded-md [&_svg]:size-3.5",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }

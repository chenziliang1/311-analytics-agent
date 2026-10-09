import { SignPlate } from "@/components/SignPlate"
import { cn } from "@/lib/utils"

/**
 * The square that marks a bubble as the agent's.
 *
 * Three places show it: the banner above the conversation, every assistant
 * message, and the "thinking" placeholder. One component so they cannot drift
 * into three slightly different squares. It wears the app's sign face, so the
 * agent speaks from the same green panel the home page's headline is set on.
 *
 * The label is a fixed two letters rather than the agent's name from
 * `site.toml`: it has to fit a 32px box, and a dataset is free to name its
 * agent something long.
 */
export function AssistantAvatar({ size = "sm" }: { size?: "sm" | "md" }) {
  return (
    <SignPlate
      size="sm"
      className={cn("shrink-0", size === "md" ? "w-10 h-10" : "w-8 h-8")}
      innerClassName="flex items-center justify-center"
    >
      <span
        aria-hidden="true"
        className={cn(
          "font-display font-extrabold leading-none pt-px",
          size === "md" ? "text-sm" : "text-xs",
        )}
      >
        AI
      </span>
    </SignPlate>
  )
}

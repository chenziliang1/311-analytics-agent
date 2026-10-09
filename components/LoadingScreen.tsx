/**
 * The full-page placeholder Next.js shows while a route segment loads.
 *
 * One component behind both `loading.tsx` files. They were two near-identical
 * 40-line copies differing only in the word under the spinner, which is exactly
 * the shape that drifts: a colour changed on one page and not the other.
 *
 * Three route markers light up in turn, the same diamonds the home page hangs
 * the business lines on. Colours come from the Tailwind theme rather than
 * literals so the whole thing follows a palette change.
 */
export function LoadingScreen({ label }: { label: string }) {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="flex flex-col items-center gap-6">
        <div className="relative flex items-center gap-7" aria-hidden="true">
          <span className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-brand" />
          {[0, 200, 400].map((delay) => (
            <span
              key={delay}
              className="relative h-4 w-4 rotate-45 rounded-[3px] border-[3px] border-brand bg-accent motion-safe:animate-pulse"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </div>
        <p className="font-display text-lg font-bold text-primary">{label}</p>
      </div>
    </div>
  )
}

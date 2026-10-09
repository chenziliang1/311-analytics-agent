import {
  AlertTriangle,
  BarChart3,
  Building2,
  Clock,
  DollarSign,
  LineChart,
  type LucideIcon,
  Package,
  Shield,
  Sparkles,
  Target,
  TrendingUp,
  Users,
} from "lucide-react"

/**
 * Icon names usable from `data/site.toml`.
 *
 * `data/site.toml` names an icon and this map resolves it. Keeping the map
 * explicit, rather than indexing all of `lucide-react`, means the bundle only
 * carries icons we actually use, and it gives `IconName` below something exact
 * to be derived from.
 *
 * Need one that is not here? Import it above and add it to the map.
 */
export const ICONS = {
  AlertTriangle,
  BarChart3,
  Building2,
  Clock,
  DollarSign,
  LineChart,
  Package,
  Shield,
  Sparkles,
  Target,
  TrendingUp,
  Users,
} satisfies Record<string, LucideIcon>

/**
 * Every icon name `data/site.toml` is allowed to use.
 *
 * This is what makes a typo a compile error instead of a silent fallback in the
 * browser. It replaces a runtime check that used to live in the Python
 * preflight script back when this config was JSON and therefore untyped.
 */
export type IconName = keyof typeof ICONS

/** Resolve an icon name, falling back to a neutral icon. */
export function getIcon(name: IconName): LucideIcon {
  return ICONS[name] ?? Sparkles
}

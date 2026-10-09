import { Toaster } from "sonner";

import Navigation from "@/app/_components/layouts/Navigation"
import { loadSite } from "@/lib/site/load";

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { brand } = loadSite()

  return (
    <div className="min-h-screen bg-background">
      <Navigation logoPrefix={brand.logoPrefix} logoAccent={brand.logoAccent} />
      <div className="pt-16">{children}</div>
      {/* Without this, every toast.error() in the chat UI — including the
          rate-limit notice — is a silent no-op. */}
      <Toaster position="top-center" richColors />
    </div>
  );
}

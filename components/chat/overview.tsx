import { motion } from "framer-motion";

import CapabilityRoute from "@/components/CapabilityRoute";
import { SignPlate } from "@/components/SignPlate";
import type { SiteConfig } from "@/lib/site/schema";

export const Overview = ({ site }: { site: SiteConfig }) => {
  const { chat, capabilities } = site;

  return (
    <motion.div
      key="overview"
      className="w-full max-w-3xl mx-auto md:mt-6"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 20 }}
      transition={{ delay: 0.2 }}
    >
      <div className="border border-border bg-surface rounded-2xl shadow-sm">
        <div className="py-8 px-4 sm:py-10 sm:px-8">
          {/* Header: the agent's name on the same sign face as the home
              page's headline. */}
          <div className="flex flex-col items-center text-center mb-6 lg:mb-9">
            <SignPlate size="md" className="mb-4">
              <h2 className="px-5 pt-2 pb-1.5 font-display text-2xl sm:text-3xl font-extrabold leading-none">
                {chat.agent}
              </h2>
            </SignPlate>

            <p className="text-muted-foreground max-w-md">
              {chat.welcomeSubtitle}
            </p>
          </div>

          {/* The same four business threads the home page shows, from the same
              `[[capabilities]]`, along the same road. Two arrays here is how
              the two screens drifted apart last time. */}
          <CapabilityRoute
            capabilities={capabilities}
            orientation="responsive"
            compact
          />

          {/* Example prompts */}
          <div className="mt-6 pt-5 border-t border-border">
            <p className="text-xs text-muted-foreground text-center">
              {chat.tryExamples}
            </p>
          </div>
        </div>
      </div>
    </motion.div>
  );
};

import { Chat } from "@/components/chat/chat";
import { authEnabled } from "@/lib/auth";
import { loadSite } from "@/lib/site/load";

export default function Page() {
  // Sign-in is optional; the chat only needs to know whether to offer it.
  return <Chat site={loadSite()} signIn={authEnabled} />;
}

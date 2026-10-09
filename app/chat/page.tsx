import { Chat } from "@/components/chat/chat";
import { loadSite } from "@/lib/site/load";

export default function Page() {
  return <Chat site={loadSite()} />;
}

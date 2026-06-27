import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default function LiteLLMAnalyticsShortcut() {
  redirect(process.env.LITELLM_DASHBOARD_URL ?? "http://localhost:4001/ui/");
}

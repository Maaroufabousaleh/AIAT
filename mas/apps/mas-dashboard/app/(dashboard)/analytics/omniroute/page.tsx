import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default function OmniRouteAnalyticsShortcut() {
  redirect(
    process.env.OMNIROUTE_DASHBOARD_URL ??
      "http://localhost:20128/dashboard/analytics"
  );
}

import { PageHeader } from "@/components/ui/PageHeader";
import { ExternalDashboardEmbed } from "../ExternalDashboardEmbed";

export const dynamic = "force-dynamic";

export default function OmniRouteAnalyticsPage() {
  const target =
    process.env.OMNIROUTE_DASHBOARD_URL ?? "http://localhost:20128/dashboard/analytics";
  return (
    <main aria-label="OmniRoute analytics" className="dashboard-page">
      <PageHeader
        icon="network"
        title="OmniRoute Analytics"
        description="Provider routing and fallback performance in the external OmniRoute dashboard."
      />
      <ExternalDashboardEmbed target={target} label="OmniRoute dashboard" />
    </main>
  );
}

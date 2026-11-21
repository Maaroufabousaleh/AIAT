import { PageHeader } from "@/components/ui/PageHeader";
import { ExternalDashboardEmbed } from "../ExternalDashboardEmbed";

export const dynamic = "force-dynamic";

export default function LiteLLMAnalyticsPage() {
  const target = process.env.LITELLM_DASHBOARD_URL ?? "http://localhost:4001/ui/";
  return (
    <main aria-label="LiteLLM analytics" className="dashboard-page">
      <PageHeader
        icon="bar-chart"
        title="LiteLLM Analytics"
        description="Model gateway spend and usage in the external LiteLLM dashboard."
      />
      <ExternalDashboardEmbed target={target} label="LiteLLM dashboard" />
    </main>
  );
}

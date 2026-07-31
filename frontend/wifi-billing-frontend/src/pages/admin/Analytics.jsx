import AdminLayout from "../../components/admin/AdminLayout";
import AnalyticsPanels from "../../components/admin/AnalyticsPanels";
import { PageHeader } from "../../components/admin/ui";

/**
 * Analytics in full.
 *
 * Deliberately just the shared panels with a heading. The dashboard renders the
 * same component in its compact form, and a second implementation is how the
 * two would end up disagreeing.
 */
export default function Analytics() {
  return (
    <AdminLayout>
      <div className="space-y-6 max-w-6xl">
        <PageHeader
          title="Analytics"
          subtitle="How the business is moving, and what is about to change"
        />
        <AnalyticsPanels />
      </div>
    </AdminLayout>
  );
}

import PlatformLayout from "../../components/platform/PlatformLayout";
import AuditTrail from "../../components/platform/AuditTrail";
import { PageHeader } from "../../components/platform/ui";

/**
 * The whole audit trail, across every operator.
 *
 * Deliberately just the shared component with no tenant filter — a second
 * implementation of the same list is how the two drift apart.
 */
export default function PlatformAudit() {
  return (
    <PlatformLayout>
      <div className="space-y-6 max-w-4xl">
        <PageHeader
          title="Audit log"
          subtitle="Every password reset, role change, account disabled and credential written"
        />
        <AuditTrail limit={200} />
      </div>
    </PlatformLayout>
  );
}

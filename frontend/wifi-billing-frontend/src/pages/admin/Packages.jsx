import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Plus, RefreshCw } from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { SkeletonTable } from "../../components/ui/Skeleton";
import { useConfirm } from "../../components/ui/ConfirmModal";
import { fetchPackages, deletePackage } from "../../services/packages";

const PAGE_SIZE = 25;

export default function Packages() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { confirm, ConfirmDialog } = useConfirm();
  const [page, setPage] = useState(1);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["packages", page],
    queryFn: () => fetchPackages(page, PAGE_SIZE),
    staleTime: 60 * 1000,
    placeholderData: (prev) => prev,
  });

  const packages   = data?.results    ?? [];
  const totalPages = data?.total_pages ?? 1;
  const count      = data?.count       ?? 0;

  const handleDelete = async (pkg) => {
    const ok = await confirm({
      title: `Delete "${pkg.name}"?`,
      description: "This permanently removes the package. Existing subscriptions are not affected.",
      confirmText: "Delete package",
      danger: true,
    });
    if (!ok) return;
    try {
      await deletePackage(pkg.id);
      toast.success("Package deleted");
      qc.invalidateQueries({ queryKey: ["packages"] });
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to delete package");
    }
  };

  return (
    <AdminLayout>
      <div className="space-y-5">
        <ConfirmDialog />

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Packages</h1>
            {!isLoading && (
              <p className="text-slate-500 text-sm mt-0.5">
                {count.toLocaleString()} package{count !== 1 ? "s" : ""} total
              </p>
            )}
          </div>
          <div className="flex gap-2 flex-shrink-0">
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors"
            >
              <RefreshCw size={12} className={isFetching ? "animate-spin" : ""} />
              Refresh
            </button>
            <button
              onClick={() => navigate("/admin/packages/new")}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-semibold hover:bg-blue-700 transition-colors"
            >
              <Plus size={15} />
              Add Package
            </button>
          </div>
        </div>

        {isError && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-4 text-red-700 text-sm">
            Failed to load packages. Try refreshing.
          </div>
        )}

        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {["Name", "Speed", "Duration", "Price (KES)", "Actions"].map((h) => (
                    <th key={h} className="px-6 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {isLoading ? (
                  <SkeletonTable rows={8} cols={5} />
                ) : packages.length === 0 ? (
                  <tr>
                    <td colSpan="5" className="px-6 py-10 text-center text-slate-400 text-sm">
                      No packages found
                    </td>
                  </tr>
                ) : (
                  packages.map((p) => (
                    <tr key={p.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-4 font-medium text-slate-800">{p.name}</td>
                      <td className="px-6 py-4 text-slate-600">{p.download_speed}/{p.upload_speed} Mbps</td>
                      <td className="px-6 py-4 text-slate-600">{p.duration_value} {p.duration_unit}</td>
                      <td className="px-6 py-4 font-semibold text-slate-800">
                        {Number(p.price).toLocaleString()}
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-3">
                          <button
                            onClick={() => navigate(`/admin/packages/${p.id}`)}
                            className="text-blue-600 hover:text-blue-800 text-xs font-medium transition-colors"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => handleDelete(p)}
                            className="text-red-500 hover:text-red-700 text-xs font-medium transition-colors"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between">
            <button
              onClick={() => setPage((p) => Math.max(p - 1, 1))}
              disabled={page <= 1}
              className="px-4 py-2 border border-slate-300 rounded-lg text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40 transition-colors"
            >
              ← Previous
            </button>
            <span className="text-sm text-slate-600">
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(p + 1, totalPages))}
              disabled={page >= totalPages}
              className="px-4 py-2 border border-slate-300 rounded-lg text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40 transition-colors"
            >
              Next →
            </button>
          </div>
        )}
      </div>
    </AdminLayout>
  );
}

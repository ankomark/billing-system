import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  Search, Plus, Download, ChevronUp, ChevronDown,
  ChevronsUpDown, Eye, Trash2, Users,
} from "lucide-react";
import AdminLayout from "../../components/admin/AdminLayout";
import { useConfirm } from "../../components/ui/ConfirmModal";
import { SkeletonTable } from "../../components/ui/Skeleton";
import Pagination from "../../components/ui/Pagination";
import StatusBadge from "../../components/ui/StatusBadge";
import EmptyState from "../../components/ui/EmptyState";
import useDebounce from "../../hooks/useDebounce";
import { fetchCustomers, deleteCustomer } from "../../services/customers";

const PAGE_SIZE = 25;

const SORT_FIELDS = {
  full_name: "Name",
  phone: "Phone",
  connection_type: "Type",
  status: "Status",
  created_at: "Joined",
};

function SortIcon({ field, sortField, sortDir }) {
  if (sortField !== field) return <ChevronsUpDown size={13} className="text-slate-400" />;
  return sortDir === "asc"
    ? <ChevronUp size={13} className="text-blue-600" />
    : <ChevronDown size={13} className="text-blue-600" />;
}

function exportCSV(customers) {
  const headers = ["Name", "Phone", "Type", "Status", "Joined"];
  const rows = customers.map((c) => [
    c.full_name,
    c.phone,
    c.connection_type,
    c.status,
    new Date(c.created_at).toLocaleDateString(),
  ]);
  const csv = [headers, ...rows].map((r) => r.join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `customers-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Customers() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { confirm, ConfirmDialog } = useConfirm();

  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [sortField, setSortField] = useState("created_at");
  const [sortDir, setSortDir] = useState("desc");

  const debouncedSearch = useDebounce(search, 400);

  // Reset to page 1 when filters change
  const onSearch = (v) => { setSearch(v); setPage(1); };
  const onStatus = (v) => { setStatusFilter(v); setPage(1); };
  const onType   = (v) => { setTypeFilter(v); setPage(1); };

  const { data, isLoading, isError } = useQuery({
    queryKey: ["customers", page, debouncedSearch, statusFilter, typeFilter],
    queryFn: () =>
      fetchCustomers({ page, pageSize: PAGE_SIZE, search: debouncedSearch, status: statusFilter, connectionType: typeFilter }),
    placeholderData: (prev) => prev,
  });

  // Client-side sort on current page results
  const sorted = useMemo(() => {
    if (!data?.results) return [];
    return [...data.results].sort((a, b) => {
      const va = (a[sortField] ?? "").toString().toLowerCase();
      const vb = (b[sortField] ?? "").toString().toLowerCase();
      if (va < vb) return sortDir === "asc" ? -1 : 1;
      if (va > vb) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
  }, [data?.results, sortField, sortDir]);

  const toggleSort = (field) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  };

  const handleDelete = async (c) => {
    const ok = await confirm({
      title: `Delete ${c.full_name}?`,
      description: "This permanently removes the customer along with all their subscriptions, invoices, and payment history. This cannot be undone.",
      confirmText: "Delete permanently",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteCustomer(c.id);
      toast.success("Customer deleted");
      qc.invalidateQueries({ queryKey: ["customers"] });
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to delete customer");
    }
  };

  const hasResults = !isLoading && sorted.length > 0;

  return (
    <AdminLayout>
      <div className="space-y-5">
        <ConfirmDialog />

        {/* Page header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Customers</h1>
            {data && (
              <p className="text-slate-500 text-sm mt-0.5">
                {data.count.toLocaleString()} customer{data.count !== 1 ? "s" : ""} total
              </p>
            )}
          </div>
          <div className="flex gap-2 flex-shrink-0">
            {hasResults && (
              <button
                onClick={() => exportCSV(sorted)}
                className="inline-flex items-center gap-2 px-3 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
              >
                <Download size={14} />
                Export
              </button>
            )}
            <button
              onClick={() => navigate("/admin/customers/new")}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-semibold hover:bg-blue-700 transition-colors"
            >
              <Plus size={15} />
              Add Customer
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              placeholder="Search by name, phone, or PPPoE username…"
              value={search}
              onChange={(e) => onSearch(e.target.value)}
              className="w-full pl-9 pr-4 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => onStatus(e.target.value)}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
          >
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="expired">Expired</option>
          </select>
          <select
            value={typeFilter}
            onChange={(e) => onType(e.target.value)}
            className="border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
          >
            <option value="">All types</option>
            <option value="pppoe">PPPoE</option>
            <option value="hotspot">Hotspot</option>
          </select>
        </div>

        {/* Error */}
        {isError && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-4 text-red-700 text-sm">
            Failed to load customers. Check your connection and try again.
          </div>
        )}

        {/* Table */}
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {Object.entries(SORT_FIELDS).map(([field, label]) => (
                    <th
                      key={field}
                      onClick={() => toggleSort(field)}
                      className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider cursor-pointer hover:text-slate-700 select-none whitespace-nowrap"
                    >
                      <span className="inline-flex items-center gap-1.5">
                        {label}
                        <SortIcon field={field} sortField={sortField} sortDir={sortDir} />
                      </span>
                    </th>
                  ))}
                  <th className="px-4 py-3 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {isLoading ? (
                  <SkeletonTable rows={PAGE_SIZE} cols={6} />
                ) : sorted.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState
                        icon={<Users size={24} />}
                        title="No customers found"
                        description={
                          debouncedSearch || statusFilter || typeFilter
                            ? "Try adjusting your search or filters."
                            : "Add your first customer to get started."
                        }
                        action={
                          !debouncedSearch && !statusFilter && !typeFilter && (
                            <button
                              onClick={() => navigate("/admin/customers/new")}
                              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-semibold hover:bg-blue-700 transition-colors"
                            >
                              <Plus size={14} />
                              Add Customer
                            </button>
                          )
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  sorted.map((c) => (
                    <tr
                      key={c.id}
                      className="hover:bg-slate-50 transition-colors"
                    >
                      <td className="px-4 py-3 font-medium text-slate-800">{c.full_name}</td>
                      <td className="px-4 py-3 text-slate-600">{c.phone}</td>
                      <td className="px-4 py-3">
                        <StatusBadge status={c.connection_type} />
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={c.status} />
                      </td>
                      <td className="px-4 py-3 text-slate-500 text-xs whitespace-nowrap">
                        {new Date(c.created_at).toLocaleDateString("en-KE", {
                          day: "numeric", month: "short", year: "numeric",
                        })}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => navigate(`/admin/customers/${c.id}`)}
                            className="p-1.5 rounded-lg text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition-colors"
                            title="View details"
                          >
                            <Eye size={15} />
                          </button>
                          <button
                            onClick={() => handleDelete(c)}
                            className="p-1.5 rounded-lg text-slate-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                            title="Delete customer"
                          >
                            <Trash2 size={15} />
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

        {/* Pagination */}
        {data && (
          <Pagination
            page={page}
            totalPages={data.total_pages}
            count={data.count}
            pageSize={PAGE_SIZE}
            onPageChange={setPage}
          />
        )}
      </div>
    </AdminLayout>
  );
}

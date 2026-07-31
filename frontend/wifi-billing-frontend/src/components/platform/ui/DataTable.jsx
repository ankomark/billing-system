/**
 * The platform console's one table.
 *
 * Also the accessible fallback for a chart: any figure shown as a shape should
 * be readable as numbers somewhere, and this is that somewhere.
 *
 * `columns`: { key, label, align?, render?(row), className? }[]
 */
export default function DataTable({
  columns,
  rows,
  rowKey = (r) => r.id,
  onRowClick,
  empty = "Nothing here yet",
  dense = false,
}) {
  if (!rows?.length) {
    return (
      <div className="px-5 py-10 text-center text-sm text-slate-500">{empty}</div>
    );
  }

  const pad = dense ? "px-4 py-2" : "px-5 py-3";

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={`${pad} text-[11px] font-semibold uppercase tracking-wider text-slate-500 ${
                  c.align === "right" ? "text-right" : "text-left"
                }`}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={onRowClick ? "cursor-pointer hover:bg-white/5 transition-colors" : ""}
            >
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={`${pad} text-slate-300 ${
                    // Columns of figures align on the decimal; that is what
                    // tabular figures are for, and only there.
                    c.align === "right" ? "text-right tabular-nums" : ""
                  } ${c.className || ""}`}
                >
                  {c.render ? c.render(row) : row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

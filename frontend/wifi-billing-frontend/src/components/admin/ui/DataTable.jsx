/**
 * The operator console's one table.
 *
 * Also the accessible fallback for a chart: three of the light palette's slots
 * sit below 3:1 on white, and the rule for that is visible numbers somewhere.
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
  loading = false,
  loadingRows = 5,
}) {
  const pad = dense ? "px-4 py-2" : "px-5 py-3";

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10 bg-white/5">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={`${pad} text-[11px] font-semibold uppercase tracking-wider text-slate-400 whitespace-nowrap ${
                  c.align === "right" ? "text-right" : "text-left"
                }`}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {loading ? (
            Array.from({ length: loadingRows }).map((_, i) => (
              <tr key={i}>
                {columns.map((c) => (
                  <td key={c.key} className={pad}>
                    <div className="h-3 w-full max-w-[10rem] rounded bg-white/5 animate-pulse" />
                  </td>
                ))}
              </tr>
            ))
          ) : !rows?.length ? (
            <tr>
              <td colSpan={columns.length} className="px-5 py-10 text-center text-sm text-slate-500">
                {empty}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
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
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

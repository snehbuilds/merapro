"use client";

import {
  createColumnHelper,
  createSortedRowModel,
  metaHelper,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_text,
  tableFeatures,
  useTable,
  type ColumnDef,
  type RowData,
  type SortingState,
} from "@tanstack/react-table";
import { useState, type ReactNode } from "react";
import { Icon } from "@/components/icons";
import { cn } from "@/lib/utils";

/*
 * mera.css .table / .table--sortable / .tablewrap on TanStack Table v9.
 *
 *  - Sortable columns get a real <button> in the header and aria-sort on the
 *    <th> (ascending / descending / none). Unsortable columns get neither.
 *  - meta.align "end" right-aligns numeric columns (header and cells), tabular.
 *  - matrixCell(true|false) draws ✓ / – with sr-only "Yes" / "No" (team.html
 *    permissions matrix).
 *  - The table scrolls horizontally inside a focusable, named region so
 *    keyboard users can scroll it too.
 *
 * Columns: build them with dataTableColumns<Row>() at module scope (stable
 * inputs are a v9 requirement).
 */
export interface DataTableColumnMeta {
  align?: "start" | "end";
  /** Machine data in mono. */
  mono?: boolean;
}

export const dataTableFeatures = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, text: sortFn_text },
  columnMeta: metaHelper<DataTableColumnMeta>(),
});

export type DataTableFeatures = typeof dataTableFeatures;

export function dataTableColumns<T extends RowData>() {
  return createColumnHelper<DataTableFeatures, T>();
}

export function matrixCell(value: boolean): ReactNode {
  return (
    <span data-slot="matrix-cell" data-value={value ? "y" : "n"}>
      <span aria-hidden="true" className={value ? "font-semibold text-ok" : "text-fg-subtle"}>
        {value ? "✓" : "–"}
      </span>
      <span className="sr-only">{value ? "Yes" : "No"}</span>
    </span>
  );
}

export interface DataTableProps<T extends RowData> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- heterogeneous accessor value types
  columns: ColumnDef<DataTableFeatures, T, any>[];
  data: T[];
  /** Names the table and its scroll region. */
  caption: string;
  /** Show the caption visibly instead of sr-only. */
  showCaption?: boolean;
  initialSorting?: SortingState;
  getRowId?: (row: T, index: number) => string;
  className?: string;
}

const ARIA_SORT = { asc: "ascending", desc: "descending" } as const;

export function DataTable<T extends RowData>({
  columns,
  data,
  caption,
  showCaption = false,
  initialSorting = [],
  getRowId,
  className,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const table = useTable({
    features: dataTableFeatures,
    columns,
    data,
    getRowId,
    state: { sorting },
    onSortingChange: setSorting,
  });

  return (
    <div
      role="region"
      aria-label={caption}
      tabIndex={0}
      data-slot="data-table"
      className={cn("relative overflow-x-auto [-webkit-overflow-scrolling:touch]", className)}
    >
      <table className="w-full border-collapse text-sm max-rail:min-w-max max-rail:[&_td]:whitespace-nowrap">
        <caption className={showCaption ? "px-3.5 py-2 text-left text-caption text-fg-muted" : "sr-only"}>
          {caption}
        </caption>
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const meta = header.column.columnDef.meta;
                const end = meta?.align === "end";
                const canSort = header.column.getCanSort();
                const sorted = header.column.getIsSorted();
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={canSort ? (sorted ? ARIA_SORT[sorted] : "none") : undefined}
                    className={cn(
                      "border-b border-border-strong px-3 py-[9px] font-label text-[11.5px] font-semibold tracking-[0.09em] whitespace-nowrap text-fg-subtle uppercase",
                      "first:pl-3.5 last:pr-3.5",
                      end ? "text-right" : "text-left",
                    )}
                  >
                    {header.isPlaceholder ? null : canSort ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className={cn(
                          "inline-flex items-center gap-1 tracking-[inherit] uppercase hover:text-fg",
                          end && "flex-row-reverse",
                        )}
                      >
                        <table.FlexRender header={header} />
                        <Icon
                          name="chevdown"
                          size="sm"
                          className={cn(
                            "transition-transform",
                            !sorted && "opacity-35",
                            sorted === "asc" && "rotate-180",
                          )}
                        />
                      </button>
                    ) : (
                      <table.FlexRender header={header} />
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="hover:bg-surface-hover [&:last-child>td]:border-b-0">
              {row.getAllCells().map((cell) => {
                const meta = cell.column.columnDef.meta;
                return (
                  <td
                    key={cell.id}
                    className={cn(
                      "border-b border-border px-3 py-2.5 align-middle first:pl-3.5 last:pr-3.5",
                      meta?.align === "end" && "tabular text-right",
                      meta?.mono && "font-mono text-xs",
                    )}
                  >
                    <table.FlexRender cell={cell} />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

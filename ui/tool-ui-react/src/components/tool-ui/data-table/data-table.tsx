"use client";

import * as React from "react";
import {
  cn,
  Table,
  TableBody,
  TableRow,
  TableCell,
  TableHeader,
  TableHead,
  Button,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "./_adapter";
import { sortData, getRowIdentifier } from "./utilities";
import { renderFormattedValue } from "./formatters";
import type {
  DataTableProps,
  DataTableContextValue,
  RowData,
  DataTableRowData,
  ColumnKey,
  Column,
} from "./types";
import { ActionButtons, normalizeActionsConfig } from "../shared";
import type { FormatConfig } from "./formatters";
import { DataTableErrorBoundary } from "./error-boundary";

export const DEFAULT_LOCALE = "en-US" as const;

function isNumericFormat(format?: FormatConfig): boolean {
  const kind = format?.kind;
  return (
    kind === "number" ||
    kind === "currency" ||
    kind === "percent" ||
    kind === "delta"
  );
}

function getAlignmentClass(
  align?: "left" | "right" | "center",
): string | undefined {
  if (align === "right") return "text-right";
  if (align === "center") return "text-center";
  return undefined;
}

const DataTableContext = React.createContext<
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  DataTableContextValue<any> | undefined
>(undefined);

export function useDataTable<T extends object = RowData>() {
  const context = React.useContext(DataTableContext) as
    | DataTableContextValue<T>
    | undefined;
  if (!context) {
    throw new Error("useDataTable must be used within a DataTable");
  }
  return context;
}

export function DataTable<T extends object = RowData>({
  columns,
  data: rawData,
  rowIdKey,
  layout = "auto",
  defaultSort,
  sort: controlledSort,
  emptyMessage = "No data available",
  isLoading = false,
  maxHeight,
  id,
  onSortChange,
  className,
  locale,
  responseActions,
  onResponseAction,
  onBeforeResponseAction,
}: DataTableProps<T>) {
  // Default locale avoids SSR/client formatting mismatches.
  const resolvedLocale = locale ?? DEFAULT_LOCALE;

  const [internalSortBy, setInternalSortBy] = React.useState<
    ColumnKey<T> | undefined
  >(defaultSort?.by);
  const [internalSortDirection, setInternalSortDirection] = React.useState<
    "asc" | "desc" | undefined
  >(defaultSort?.direction);

  const sortBy = controlledSort?.by ?? internalSortBy;
  const sortDirection = controlledSort?.direction ?? internalSortDirection;

  const data = React.useMemo(() => {
    if (!sortBy || !sortDirection) return rawData;
    return sortData(rawData, sortBy, sortDirection, resolvedLocale);
  }, [rawData, sortBy, sortDirection, resolvedLocale]);

  const handleSort = React.useCallback(
    (key: ColumnKey<T>) => {
      let newDirection: "asc" | "desc" | undefined;

      if (sortBy === key) {
        if (sortDirection === "asc") {
          newDirection = "desc";
        } else if (sortDirection === "desc") {
          newDirection = undefined;
        } else {
          newDirection = "asc";
        }
      } else {
        newDirection = "asc";
      }

      const next = {
        by: newDirection ? key : undefined,
        direction: newDirection,
      } as const;

      if (controlledSort) {
        onSortChange?.(next);
      } else {
        setInternalSortBy(next.by);
        setInternalSortDirection(next.direction);
      }
    },
    [sortBy, sortDirection, controlledSort, onSortChange],
  );

  const contextValue: DataTableContextValue<T> = {
    columns,
    data,
    rowIdKey,
    sortBy,
    sortDirection,
    toggleSort: handleSort,
    id,
    isLoading,
    locale: resolvedLocale,
  };

  const sortAnnouncement = React.useMemo(() => {
    const col = columns.find((c) => c.key === sortBy);
    const label = col?.label ?? sortBy;
    return sortBy && sortDirection
      ? `Sorted by ${label}, ${sortDirection === "asc" ? "ascending" : "descending"}`
      : "";
  }, [columns, sortBy, sortDirection]);

  const normalizedFooterActions = React.useMemo(
    () => normalizeActionsConfig(responseActions),
    [responseActions],
  );

  return (
    <DataTableContext.Provider value={contextValue}>
      <div
        className={cn("@container w-full min-w-80", className)}
        data-tool-ui-id={id}
        data-slot="data-table"
        data-layout={layout}
      >
        <div
          className={cn(
            layout === "table"
              ? "block"
              : layout === "cards"
                ? "hidden"
                : "hidden @md:block",
          )}
        >
          <div className="relative">
            <div
              className={cn(
                "bg-card relative w-full overflow-clip overflow-y-auto rounded-lg border",
                "touch-pan-x",
                maxHeight && "max-h-[--max-height]",
              )}
              style={
                maxHeight
                  ? ({ "--max-height": maxHeight } as React.CSSProperties)
                  : undefined
              }
            >
              <DataTableErrorBoundary>
                <Table aria-busy={isLoading || undefined}>
                  {columns.length > 0 && (
                    <colgroup>
                      {columns.map((col) => (
                        <col
                          key={String(col.key)}
                          style={col.width ? { width: col.width } : undefined}
                        />
                      ))}
                    </colgroup>
                  )}
                  {isLoading ? (
                    <DataTableSkeleton />
                  ) : data.length === 0 ? (
                    <DataTableEmpty message={emptyMessage} />
                  ) : (
                    <DataTableContent />
                  )}
                </Table>
              </DataTableErrorBoundary>
            </div>
          </div>
        </div>

        <div
          className={cn(
            layout === "cards"
              ? ""
              : layout === "table"
                ? "hidden"
                : "@md:hidden",
          )}
          role="list"
          aria-label="Data table (mobile card view)"
          aria-describedby="mobile-table-description"
        >
          <div id="mobile-table-description" className="sr-only">
            Table data shown as expandable cards. Each card represents one row.
            {columns.length > 0 &&
              ` Columns: ${columns.map((c) => c.label).join(", ")}.`}
          </div>

          <DataTableErrorBoundary>
            {isLoading ? (
              <DataTableSkeletonCards />
            ) : data.length === 0 ? (
              <div className="text-muted-foreground py-8 text-center">
                {emptyMessage}
              </div>
            ) : (
              <div className="bg-card flex flex-col overflow-hidden rounded-2xl border shadow-xs">
                {data.map((row, i) => {
                  const keyVal = rowIdKey ? row[rowIdKey] : undefined;
                  const rowKey = keyVal != null ? String(keyVal) : String(i);
                  return (
                    <DataTableAccordionCard
                      key={rowKey}
                      row={row as unknown as DataTableRowData}
                      index={i}
                      isFirst={i === 0}
                    />
                  );
                })}
              </div>
            )}
          </DataTableErrorBoundary>
        </div>

        {sortAnnouncement && (
          <div className="sr-only" aria-live="polite">
            {sortAnnouncement}
          </div>
        )}

        {normalizedFooterActions ? (
          <div className="@container/actions mt-4">
            <ActionButtons
              actions={normalizedFooterActions.items}
              align={normalizedFooterActions.align}
              confirmTimeout={normalizedFooterActions.confirmTimeout}
              onAction={(id) => onResponseAction?.(id)}
              onBeforeAction={onBeforeResponseAction}
            />
          </div>
        ) : null}
      </div>
    </DataTableContext.Provider>
  );
}

function DataTableContent() {
  return (
    <>
      <DataTableHeader />
      <DataTableBody />
    </>
  );
}

function DataTableEmpty({ message }: { message: string }) {
  const { columns } = useDataTable();

  return (
    <TableBody>
      <TableRow className="bg-card h-24 text-center">
        <TableCell colSpan={columns.length} role="status" aria-live="polite">
          {message}
        </TableCell>
      </TableRow>
    </TableBody>
  );
}

function DataTableSkeleton() {
  const { columns } = useDataTable();

  return (
    <>
      <DataTableHeader />
      <TableBody>
        {Array.from({ length: 5 }).map((_, i) => (
          <TableRow key={i}>
            {columns.map((_, j) => (
              <TableCell key={j}>
                <div className="bg-muted/50 h-4 rounded motion-safe:animate-pulse" />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </>
  );
}

function DataTableSkeletonCards() {
  return (
    <>
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="flex flex-col gap-2 rounded-lg border p-4">
          <div className="bg-muted/50 h-5 w-1/2 rounded motion-safe:animate-pulse" />
          <div className="bg-muted/50 h-4 w-3/4 rounded motion-safe:animate-pulse" />
          <div className="bg-muted/50 h-4 w-2/3 rounded motion-safe:animate-pulse" />
        </div>
      ))}
    </>
  );
}

function SortIcon({ state }: { state?: "asc" | "desc" }) {
  let char = "⇅";
  let className = "opacity-20";

  if (state === "asc") {
    char = "↑";
    className = "";
  }

  if (state === "desc") {
    char = "↓";
    className = "";
  }

  return (
    <span aria-hidden className={cn("min-w-4 shrink-0 text-center", className)}>
      {char}
    </span>
  );
}

function DataTableHeader() {
  const { columns } = useDataTable();

  return (
    <TooltipProvider delayDuration={300}>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {columns.map((column, columnIndex) => (
            <DataTableHead
              key={column.key}
              column={column}
              columnIndex={columnIndex}
              totalColumns={columns.length}
            />
          ))}
        </TableRow>
      </TableHeader>
    </TooltipProvider>
  );
}

interface DataTableHeadProps {
  column: Column;
  columnIndex?: number;
  totalColumns?: number;
}

function DataTableHead({
  column,
  columnIndex = 0,
  totalColumns = 1,
}: DataTableHeadProps) {
  const { sortBy, sortDirection, toggleSort, isLoading } = useDataTable();
  const isFirstColumn = columnIndex === 0;
  const isLastColumn = columnIndex === totalColumns - 1;

  const isSortable = column.sortable !== false;

  const isSorted = sortBy === column.key;
  const direction = isSorted ? sortDirection : undefined;
  const isDisabled = isLoading || !isSortable;

  const handleClick = () => {
    if (!isDisabled && toggleSort) {
      toggleSort(column.key);
    }
  };

  const displayText = column.abbr || column.label;
  const shouldShowTooltip = column.abbr || displayText.length > 15;
  const isNumericKind = isNumericFormat(column.format);
  const align =
    column.align ??
    (columnIndex === 0 ? "left" : isNumericKind ? "right" : "left");
  const alignClass = getAlignmentClass(align);
  const buttonAlignClass = cn(
    "min-w-0 gap-1 font-normal",
    align === "right" && "text-right",
    align === "center" && "text-center",
    align === "left" && "text-left",
  );
  const labelAlignClass =
    align === "right"
      ? "text-right"
      : align === "center"
        ? "text-center"
        : "text-left";

  return (
    <TableHead
      scope="col"
      className={cn(
        alignClass,
        isFirstColumn && "pl-1",
        isLastColumn && "pr-1",
      )}
      style={column.width ? { width: column.width } : undefined}
      aria-sort={
        isSorted
          ? direction === "asc"
            ? "ascending"
            : "descending"
          : undefined
      }
    >
      <Button
        type="button"
        size="sm"
        onClick={handleClick}
        onKeyDown={(e) => {
          if (isDisabled) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            handleClick();
          }
        }}
        disabled={isDisabled}
        variant="ghost"
        className={cn(
          buttonAlignClass,
          "w-fit min-w-10",
          isFirstColumn && "pl-4",
          isLastColumn && "pr-4",
        )}
        aria-label={
          `Sort by ${column.label}` +
          (isSorted && direction
            ? ` (${direction === "asc" ? "ascending" : "descending"})`
            : "")
        }
        aria-disabled={isDisabled || undefined}
      >
        {shouldShowTooltip ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className={cn("truncate", labelAlignClass)}>
                {column.abbr ? (
                  <abbr
                    title={column.label}
                    className={cn(
                      "cursor-help border-b border-dotted border-current no-underline",
                      labelAlignClass,
                    )}
                  >
                    {column.abbr}
                  </abbr>
                ) : (
                  <span className={labelAlignClass}>{column.label}</span>
                )}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <p>{column.label}</p>
            </TooltipContent>
          </Tooltip>
        ) : (
          <span className={cn("truncate", labelAlignClass)}>
            {column.label}
          </span>
        )}
        {isSortable && <SortIcon state={direction} />}
      </Button>
    </TableHead>
  );
}

function DataTableBody() {
  const { data, rowIdKey } = useDataTable<DataTableRowData>();
  const hasWarnedRowKeyRef = React.useRef(false);

  React.useEffect(() => {
    if (hasWarnedRowKeyRef.current) return;
    if (process.env.NODE_ENV !== "production" && !rowIdKey && data.length > 0) {
      hasWarnedRowKeyRef.current = true;
      console.warn(
        "[DataTable] Missing `rowIdKey` prop. Using array index as React key can cause reconciliation issues when data reorders (focus traps, animation glitches, incorrect state preservation). " +
          "Strongly recommended: Pass a `rowIdKey` prop that points to a unique identifier in your row data (e.g., 'id', 'uuid', 'symbol').\n" +
          'Example: <DataTable rowIdKey="id" columns={...} data={...} />',
      );
    }
  }, [rowIdKey, data.length]);

  return (
    <TableBody>
      {data.map((row, index) => {
        const keyVal = rowIdKey ? row[rowIdKey] : undefined;
        const rowKey = keyVal != null ? String(keyVal) : String(index);
        return <DataTableRow key={rowKey} row={row} />;
      })}
    </TableBody>
  );
}

interface DataTableRowProps {
  row: DataTableRowData;
  className?: string;
}

function DataTableRow({ row, className }: DataTableRowProps) {
  const { columns } = useDataTable();

  return (
    <TableRow className={className}>
      {columns.map((column, columnIndex) => (
        <DataTableCell
          key={column.key}
          value={row[column.key]}
          column={column}
          row={row}
          columnIndex={columnIndex}
        />
      ))}
    </TableRow>
  );
}

interface DataTableCellProps {
  value:
    | string
    | number
    | boolean
    | null
    | (string | number | boolean | null)[];
  column: Column;
  row: DataTableRowData;
  className?: string;
  columnIndex?: number;
}

function DataTableCell({
  value,
  column,
  row,
  className,
  columnIndex = 0,
}: DataTableCellProps) {
  const { locale } = useDataTable();
  const isNumericKind = isNumericFormat(column.format);
  const isNumericValue = typeof value === "number";
  const displayValue = renderFormattedValue({ value, column, row, locale });
  const align =
    column.align ??
    (columnIndex === 0
      ? "left"
      : isNumericKind || isNumericValue
        ? "right"
        : "left");
  const alignClass = getAlignmentClass(align);

  return (
    <TableCell className={cn("px-5 py-3", alignClass, className)}>
      {displayValue}
    </TableCell>
  );
}

function categorizeColumns(columns: Column[]) {
  const primary: Column[] = [];
  const secondary: Column[] = [];

  let visibleColumnCount = 0;
  columns.forEach((col) => {
    if (col.hideOnMobile) return;

    if (col.priority === "primary") {
      primary.push(col);
    } else if (col.priority === "secondary") {
      secondary.push(col);
    } else if (col.priority === "tertiary") {
      return;
    } else {
      if (visibleColumnCount < 2) {
        primary.push(col);
      } else {
        secondary.push(col);
      }
      visibleColumnCount++;
    }
  });

  return { primary, secondary };
}

interface DataTableAccordionCardProps {
  row: DataTableRowData;
  index: number;
  isFirst?: boolean;
}

function DataTableAccordionCard({
  row,
  index,
  isFirst = false,
}: DataTableAccordionCardProps) {
  const { columns, locale, rowIdKey } = useDataTable();

  const { primary, secondary } = React.useMemo(
    () => categorizeColumns(columns),
    [columns],
  );

  if (secondary.length === 0) {
    return (
      <SimpleCard row={row} columns={primary} index={index} isFirst={isFirst} />
    );
  }

  const primaryColumn = primary[0];
  const remainingPrimaryColumns = primary.slice(1);

  const stableRowId =
    getRowIdentifier(row, rowIdKey ? String(rowIdKey) : undefined) ||
    `${index}-${primaryColumn?.key ?? "row"}`;

  const headingId = `row-${stableRowId}-heading`;
  const detailsId = `row-${stableRowId}-details`;
  const remainingPrimaryDataIds = remainingPrimaryColumns.map(
    (col) => `row-${stableRowId}-${String(col.key)}`,
  );

  const primaryValue = primaryColumn
    ? String(row[primaryColumn.key] ?? "")
    : "";
  const rowLabel = `Row ${index + 1}: ${primaryValue}`;
  const accordionItemId = `row-${stableRowId}`;

  return (
    <Accordion
      type="single"
      collapsible
      className={cn(!isFirst && "border-t")}
      role="listitem"
      aria-label={rowLabel}
    >
      <AccordionItem value={accordionItemId} className="group border-0">
        <AccordionTrigger
          className="group-data-[state=closed]:hover:bg-accent/50 active:bg-accent/50 group-data-[state=open]:bg-muted w-full rounded-none px-4 py-3 hover:no-underline"
          aria-controls={detailsId}
          aria-label={`${rowLabel}. ${secondary.length > 0 ? "Expand for details" : ""}`}
        >
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            {primaryColumn && (
              <div
                id={headingId}
                role="heading"
                aria-level={3}
                className="truncate"
                aria-label={`${primaryColumn.label}: ${row[primaryColumn.key]}`}
              >
                {renderFormattedValue({
                  value: row[primaryColumn.key],
                  column: primaryColumn,
                  row,
                  locale,
                })}
              </div>
            )}

            {remainingPrimaryColumns.length > 0 && (
              <div
                className="text-muted-foreground flex w-full flex-wrap gap-x-4 gap-y-0.5"
                role="group"
                aria-label="Summary information"
              >
                {remainingPrimaryColumns.map((col, idx) => (
                  <span
                    key={col.key}
                    id={remainingPrimaryDataIds[idx]}
                    className="flex min-w-0 gap-1 font-normal"
                    role="cell"
                    aria-label={`${col.label}: ${row[col.key]}`}
                  >
                    <span className="sr-only">{col.label}:</span>
                    <span aria-hidden="true">{col.label}:</span>
                    <span className="truncate">
                      {renderFormattedValue({
                        value: row[col.key],
                        column: col,
                        row,
                        locale,
                      })}
                    </span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </AccordionTrigger>

        <AccordionContent
          className={"flex flex-col gap-4 px-4 pb-4"}
          id={detailsId}
          role="region"
          aria-labelledby={headingId}
        >
          {secondary.length > 0 && (
            <dl
              className={cn(
                "flex flex-col gap-2 pt-4",
                "motion-safe:group-data-[state=open]:animate-in motion-safe:group-data-[state=open]:fade-in-0",
                "motion-safe:group-data-[state=open]:slide-in-from-top-1",
                "motion-safe:group-data-[state=closed]:animate-out motion-safe:group-data-[state=closed]:fade-out-0",
                "motion-safe:group-data-[state=closed]:slide-out-to-top-1",
                "duration-150",
              )}
              role="list"
              aria-label="Additional data"
            >
              {secondary.map((col) => (
                <div
                  key={col.key}
                  className="flex items-start justify-between gap-4"
                  role="listitem"
                >
                  <dt
                    className="text-muted-foreground shrink-0"
                    id={`row-${stableRowId}-${String(col.key)}-label`}
                  >
                    {col.label}
                  </dt>
                  <dd
                    className={cn(
                      "text-foreground min-w-0 text-pretty wrap-break-word",
                      col.align === "right" && "text-right",
                      col.align === "center" && "text-center",
                    )}
                    role="cell"
                    aria-labelledby={`row-${stableRowId}-${String(col.key)}-label`}
                  >
                    {renderFormattedValue({
                      value: row[col.key],
                      column: col,
                      row,
                      locale,
                    })}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}

/**
 * Simple card with no accordion,   for when there are only primary columns
 */
function SimpleCard({
  row,
  columns,
  index,
  isFirst = false,
}: {
  row: DataTableRowData;
  columns: Column[];
  index: number;
  isFirst?: boolean;
}) {
  const { locale, rowIdKey } = useDataTable();
  const primaryColumn = columns[0];
  const otherColumns = columns.slice(1);

  const stableRowId =
    getRowIdentifier(row, rowIdKey ? String(rowIdKey) : undefined) ||
    `${index}-${primaryColumn?.key ?? "row"}`;

  const primaryValue = primaryColumn
    ? String(row[primaryColumn.key] ?? "")
    : "";
  const rowLabel = `Row ${index + 1}: ${primaryValue}`;

  return (
    <div
      className={cn("flex flex-col gap-2 p-4", !isFirst && "border-t")}
      role="listitem"
      aria-label={rowLabel}
    >
      {primaryColumn && (
        <div
          role="heading"
          aria-level={3}
          aria-label={`${primaryColumn.label}: ${row[primaryColumn.key]}`}
        >
          {renderFormattedValue({
            value: row[primaryColumn.key],
            column: primaryColumn,
            row,
            locale,
          })}
        </div>
      )}

      {otherColumns.map((col) => (
        <div
          key={col.key}
          className="flex items-start justify-between gap-4"
          role="group"
        >
          <span
            className="text-muted-foreground"
            id={`row-${stableRowId}-${String(col.key)}-label`}
          >
            {col.label}:
          </span>
          <span
            className={cn(
              "min-w-0 wrap-break-word",
              col.align === "right" && "text-right",
              col.align === "center" && "text-center",
            )}
            role="cell"
            aria-labelledby={`row-${stableRowId}-${String(col.key)}-label`}
          >
            {renderFormattedValue({
              value: row[col.key],
              column: col,
              row,
              locale,
            })}
          </span>
        </div>
      ))}
    </div>
  );
}

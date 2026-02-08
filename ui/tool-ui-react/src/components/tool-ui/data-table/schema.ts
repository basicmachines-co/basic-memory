import { z } from "zod";
import {
  ToolUIIdSchema,
  ToolUIReceiptSchema,
  ToolUIRoleSchema,
  parseWithSchema,
} from "../shared";
import type { Column, DataTableProps, RowData } from "./types";

const AlignEnum = z.enum(["left", "right", "center"]);
const PriorityEnum = z.enum(["primary", "secondary", "tertiary"]);
const LayoutEnum = z.enum(["auto", "table", "cards"]);

const formatSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("text") }),
  z.object({
    kind: z.literal("number"),
    decimals: z.number().optional(),
    unit: z.string().optional(),
    compact: z.boolean().optional(),
    showSign: z.boolean().optional(),
  }),
  z.object({
    kind: z.literal("currency"),
    currency: z.string(),
    decimals: z.number().optional(),
  }),
  z.object({
    kind: z.literal("percent"),
    decimals: z.number().optional(),
    showSign: z.boolean().optional(),
    basis: z.enum(["fraction", "unit"]).optional(),
  }),
  z.object({
    kind: z.literal("date"),
    dateFormat: z.enum(["short", "long", "relative"]).optional(),
  }),
  z.object({
    kind: z.literal("delta"),
    decimals: z.number().optional(),
    upIsPositive: z.boolean().optional(),
    showSign: z.boolean().optional(),
  }),
  z.object({
    kind: z.literal("status"),
    statusMap: z.record(
      z.string(),
      z.object({
        tone: z.enum(["success", "warning", "danger", "info", "neutral"]),
        label: z.string().optional(),
      }),
    ),
  }),
  z.object({
    kind: z.literal("boolean"),
    labels: z
      .object({
        true: z.string(),
        false: z.string(),
      })
      .optional(),
  }),
  z.object({
    kind: z.literal("link"),
    hrefKey: z.string().optional(),
    external: z.boolean().optional(),
  }),
  z.object({
    kind: z.literal("badge"),
    colorMap: z
      .record(
        z.string(),
        z.enum(["success", "warning", "danger", "info", "neutral"]),
      )
      .optional(),
  }),
  z.object({
    kind: z.literal("array"),
    maxVisible: z.number().optional(),
  }),
]);

export const serializableColumnSchema = z.object({
  key: z.string(),
  label: z.string(),
  abbr: z.string().optional(),
  sortable: z.boolean().optional(),
  align: AlignEnum.optional(),
  width: z.string().optional(),
  truncate: z.boolean().optional(),
  priority: PriorityEnum.optional(),
  hideOnMobile: z.boolean().optional(),
  format: formatSchema.optional(),
});

const JsonPrimitiveSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);

/**
 * Schema for serializable row data.
 *
 * Supports:
 * - Primitives: string, number, boolean, null
 * - Arrays of primitives: string[], number[], boolean[], or mixed primitive arrays
 *
 * Does NOT support:
 * - Functions
 * - Class instances (Date, Map, Set, etc.)
 * - Plain objects (use format configs instead)
 *
 * @example
 * Valid row data:
 * ```json
 * {
 *   "name": "Widget",
 *   "price": 29.99,
 *   "active": true,
 *   "tags": ["electronics", "featured"],
 *   "metrics": [1.2, 3.4, 5.6],
 *   "flags": [true, false, true],
 *   "mixed": ["label", 42, true]
 * }
 * ```
 */
export const serializableDataSchema = z.record(
  z.string(),
  z.union([JsonPrimitiveSchema, z.array(JsonPrimitiveSchema)]),
);

/**
 * Zod schema for validating DataTable payloads from LLM tool calls.
 *
 * This schema validates the serializable parts of a DataTable:
 * - id: Unique identifier for this tool UI in the conversation
 * - columns: Column definitions (keys, labels, formatting, etc.)
 * - data: Data rows (primitives only - no functions or class instances)
 * - layout: Optional layout override ('auto' | 'table' | 'cards')
 *
 * Non-serializable props like `onSortChange`, `className`, and `isLoading`
 * must be provided separately in your React component.
 *
 * @example
 * ```ts
 * const result = SerializableDataTableSchema.safeParse(llmResponse)
 * if (result.success) {
 *   // result.data contains validated id, columns, and data
 * }
 * ```
 */
export const SerializableDataTableSchema = z.object({
  id: ToolUIIdSchema,
  role: ToolUIRoleSchema.optional(),
  receipt: ToolUIReceiptSchema.optional(),
  columns: z.array(serializableColumnSchema),
  data: z.array(serializableDataSchema),
  layout: LayoutEnum.optional(),
});

/**
 * Type representing the serializable parts of a DataTable payload.
 *
 * This type includes only JSON-serializable data that can come from LLM tool calls:
 * - Column definitions (format configs, alignment, labels, etc.)
 * - Row data (primitives: strings, numbers, booleans, null, string arrays)
 *
 * Excluded from this type:
 * - Event handlers (`onSortChange`, `onResponseAction`)
 * - React-specific props (`className`, `isLoading`, `responseActions`)
 *
 * @example
 * ```ts
 * const payload: SerializableDataTable = {
 *   id: "data-table-expenses",
 *   columns: [
 *     { key: "name", label: "Name" },
 *     { key: "price", label: "Price", format: { kind: "currency", currency: "USD" } }
 *   ],
 *   data: [
 *     { name: "Widget", price: 29.99 }
 *   ]
 * }
 * ```
 */
export type SerializableDataTable = z.infer<typeof SerializableDataTableSchema>;

/**
 * Validates and parses a DataTable payload from unknown data (e.g., LLM tool call result).
 *
 * This function:
 * 1. Validates the input against the `SerializableDataTableSchema`
 * 2. Throws a descriptive error if validation fails
 * 3. Returns typed serializable props ready to pass to the `<DataTable>` component
 *
 * The returned props are **serializable only** - you must provide client-side props
 * separately (onSortChange, isLoading, className, responseActions, onResponseAction).
 *
 * @param input - Unknown data to validate (typically from an LLM tool call)
 * @returns Validated and typed DataTable serializable props (id, columns, data)
 * @throws Error with validation details if input is invalid
 *
 * @example
 * ```tsx
 * function MyToolUI({ result }: { result: unknown }) {
 *   const serializableProps = parseSerializableDataTable(result)
 *
 *   return (
 *     <DataTable
 *       {...serializableProps}
 *       responseActions={[{ id: "export", label: "Export" }]}
 *       onResponseAction={(id) => console.log(id)}
 *     />
 *   )
 * }
 * ```
 */
export function parseSerializableDataTable(
  input: unknown,
): Pick<
  DataTableProps<RowData>,
  "id" | "role" | "receipt" | "columns" | "data" | "layout"
> {
  const { id, role, receipt, columns, data, layout } = parseWithSchema(
    SerializableDataTableSchema,
    input,
    "DataTable",
  );
  return {
    id,
    role,
    receipt,
    columns: columns as unknown as Column<RowData>[],
    data: data as RowData[],
    layout,
  };
}

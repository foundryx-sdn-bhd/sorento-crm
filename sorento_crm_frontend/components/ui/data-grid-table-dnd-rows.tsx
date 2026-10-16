import { CSSProperties, Fragment, useId, useState } from 'react';
import { Button } from '@/components/ui/button';
import { useDataGrid } from '@/components/ui/data-grid';
import {
  DataGridTableBase,
  DataGridTableBody,
  DataGridTableBodyRow,
  DataGridTableBodyRowCell,
  DataGridTableBodyRowExpandded,
  DataGridTableBodyRowSkeleton,
  DataGridTableBodyRowSkeletonCell,
  DataGridTableEmpty,
  DataGridTableFoot,
  DataGridTableFootRowCell,
  DataGridTableHead,
  DataGridTableHeadRow,
  DataGridTableHeadRowCell,
  DataGridTableHeadRowCellResize,
  DataGridTableRowSpacer,
  footerGroupsWithContent,
  reorderColumnsOnDragEnd,
  useBodySkeleton,
  skeletonRowCount,
  useReturnedRowId,
} from '@/components/ui/data-grid-table';
import { isFixedUtilityColumn } from '@/components/ui/data-grid-table-dnd';
import {
  closestCenter,
  defaultDropAnimation,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  UniqueIdentifier,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { restrictToHorizontalAxis, restrictToVerticalAxis } from '@dnd-kit/modifiers';
import {
  horizontalListSortingStrategy,
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Cell, flexRender, Header, HeaderGroup, Row } from '@tanstack/react-table';
import { GripHorizontal } from 'lucide-react';
import { mergeColumnOrderWithLeafColumns } from '@/lib/listing-column-preferences/mergeColumnOrder';

function DataGridTableDndRowHandle({ rowId }: { rowId: string }) {
  const { attributes, listeners } = useSortable({
    id: rowId,
  });

  return (
    <Button
      variant="dim"
      size="sm"
      className="size-7"
      aria-label="Drag to reorder"
      {...attributes}
      {...listeners}
    >
      <GripHorizontal />
    </Button>
  );
}

function DataGridTableDndRow<TData>({
  row,
  previousRow,
  returnedFromId,
}: {
  row: Row<TData>;
  /** The row drawn above this one, for `renderGroupHeader`. Null on the first row. */
  previousRow: Row<TData> | null;
  /** Resolved once by `DataGridTableDndRows` and passed down - see `useReturnedRowId`'s doc. */
  returnedFromId: string | null;
}) {
  const { props } = useDataGrid();
  const { transform, transition, setNodeRef, isDragging } = useSortable({
    id: row.id,
  });

  const style: CSSProperties = {
    transform: CSS.Transform.toString(transform), //let dnd-kit do its thing
    transition: transition,
    opacity: isDragging ? 0.8 : 1,
    zIndex: isDragging ? 1 : 0,
    position: 'relative',
  };

  // A full-width row that belongs to the line under it (a section heading), and the line's
  // own expanded detail row under it. Both carry the line's drag transform, so the three
  // move as one while the line is dragged, and land together because they ARE one item.
  const groupHeader = props.renderGroupHeader?.(
    row.original as TData,
    previousRow ? (previousRow.original as TData) : null,
  );

  return (
    <Fragment>
      {groupHeader != null && (
        <tr className="bg-muted/50" data-testid="data-grid-group-header" style={style}>
          <td
            colSpan={row.getVisibleCells().length}
            className="px-4 py-2 text-xs font-medium text-muted-foreground"
          >
            {groupHeader}
          </td>
        </tr>
      )}
      <DataGridTableBodyRow
        row={row}
        returnedFromId={returnedFromId}
        dndRef={setNodeRef}
        dndStyle={style}
        key={row.id}
      >
        {row.getVisibleCells().map((cell: Cell<TData, unknown>, colIndex) => {
          return (
            <DataGridTableBodyRowCell cell={cell} key={colIndex}>
              {flexRender(cell.column.columnDef.cell, cell.getContext())}
            </DataGridTableBodyRowCell>
          );
        })}
      </DataGridTableBodyRow>
      {row.getIsExpanded() && <DataGridTableBodyRowExpandded row={row} dndStyle={style} />}
    </Fragment>
  );
}

/**
 * A header cell a column is dragged by, for a grid whose ROWS also drag.
 *
 * The whole header is the grab area, with no grip icon and no `aria-label` on the wrapper:
 * a narrow column (a 40px thumbnail's) has no room for a grip beside its title, and a label on
 * the wrapper would replace the header's own name for a screen reader. Keyboard users move
 * columns from the header's own menu where a list offers one.
 */
function DataGridTableDndRowsColumnHeader<TData>({ header }: { header: Header<TData, unknown> }) {
  const { props } = useDataGrid();
  const { column } = header;
  const noDrag = isFixedUtilityColumn(column.columnDef);
  const { isDragging, listeners, setNodeRef, transform, transition } = useSortable({
    id: column.id,
    disabled: noDrag,
  });

  const style: CSSProperties = {
    opacity: isDragging ? 0.8 : 1,
    position: 'relative',
    transform: CSS.Translate.toString(transform),
    transition,
    whiteSpace: 'nowrap',
    width: column.getSize(),
    zIndex: isDragging ? 1 : 0,
  };

  return (
    <DataGridTableHeadRowCell
      header={header}
      dndStyle={style}
      dndDragging={isDragging}
      dndRef={setNodeRef}
    >
      <div
        className={noDrag ? 'w-full select-none truncate' : 'w-full cursor-grab select-none truncate'}
        {...(noDrag ? {} : listeners)}
      >
        {header.isPlaceholder ? null : flexRender(column.columnDef.header, header.getContext())}
      </div>
      {props.tableLayout?.columnsResizable && column.getCanResize() && (
        <DataGridTableHeadRowCellResize header={header} />
      )}
    </DataGridTableHeadRowCell>
  );
}

function DataGridTableDndRows<TData>({
  handleDragEnd,
  dataIds,
  columnsDraggable = false,
}: {
  handleDragEnd: (event: DragEndEvent) => void;
  dataIds: UniqueIdentifier[];
  /**
   * Header cells reorder columns by drag too (the drop goes through `reorderColumnsOnDragEnd`,
   * the same as the column-drag grid). Off by default, so a caller that only drags rows draws
   * exactly the header it always did.
   */
  columnsDraggable?: boolean;
}) {
  const { table, props } = useDataGrid();
  const pagination = table.getState().pagination;
  const showBodySkeleton = useBodySkeleton();
  // ONCE per grid (S5/S7, M5 review run 1) - this render path does not go through
  // `DataGridTable`, so it resolves its own, the same way that one does.
  const returnedFromId = useReturnedRowId();
  const [activeRow, setActiveRow] = useState<Row<TData> | null>(null);
  const columnDndId = useId();

  // Same activation constraint as the column reorderer (data-grid-table-dnd.tsx,
  // S8-06): without one, the handle started a drag on the very first pixel of
  // movement, so a click that merely twitched a little read as a reorder.
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
    useSensor(KeyboardSensor, {}),
  );
  const columnSensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
  );

  const handleDragStart = (event: DragStartEvent) => {
    const row = table.getRowModel().rows.find((r) => r.id === event.active.id);
    setActiveRow(row ?? null);
  };

  const handleDragEndInternal = (event: DragEndEvent) => {
    setActiveRow(null);
    handleDragEnd(event);
  };

  const leafIds = table.getAllLeafColumns().map((c) => c.id);
  const rawOrder =
    Array.isArray(table.getState().columnOrder) && table.getState().columnOrder.length > 0
      ? (table.getState().columnOrder as string[])
      : leafIds;
  const orderedColumnIds = columnsDraggable
    ? mergeColumnOrderWithLeafColumns(rawOrder, leafIds).filter((id) => {
        const column = table.getColumn(id);
        return Boolean(column) && !isFixedUtilityColumn(column!.columnDef);
      })
    : [];

  const hasFooter = table
    .getVisibleFlatColumns()
    .some((column) => Boolean(column.columnDef.footer));
  const rows = table.getRowModel().rows;

  const headerRows = table.getHeaderGroups().map((headerGroup: HeaderGroup<TData>, index) => {
    if (columnsDraggable) {
      return (
        <DataGridTableHeadRow headerGroup={headerGroup} key={index}>
          <SortableContext items={orderedColumnIds} strategy={horizontalListSortingStrategy}>
            {headerGroup.headers.map((header) => (
              <DataGridTableDndRowsColumnHeader header={header} key={header.id} />
            ))}
          </SortableContext>
        </DataGridTableHeadRow>
      );
    }
    return (
      <DataGridTableHeadRow headerGroup={headerGroup} key={index}>
        {headerGroup.headers.map((header, index) => {
          const { column } = header;

          return (
            <DataGridTableHeadRowCell header={header} key={index}>
              {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
              {props.tableLayout?.columnsResizable && column.getCanResize() && (
                <DataGridTableHeadRowCellResize header={header} />
              )}
            </DataGridTableHeadRowCell>
          );
        })}
      </DataGridTableHeadRow>
    );
  });

  return (
    <DndContext
      id={useId()}
      collisionDetection={closestCenter}
      modifiers={[restrictToVerticalAxis]}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEndInternal}
      onDragCancel={() => setActiveRow(null)}
      sensors={sensors}
    >
      <div className="relative">
        <DataGridTableBase>
          <DataGridTableHead>
            {columnsDraggable ? (
              // Its own context: a column drag is horizontal and drops through the table's
              // column order, a row drag is vertical and drops through the caller.
              <DndContext
                id={columnDndId}
                // dnd-kit's screen-reader nodes are divs; inline they would sit inside <thead>.
                // Its own Accessibility renders nothing until mounted, so this is only read in
                // the browser (the server render is the same null either way).
                accessibility={{
                  container: typeof document === 'undefined' ? undefined : document.body,
                }}
                collisionDetection={closestCenter}
                modifiers={[restrictToHorizontalAxis]}
                onDragEnd={(event) => reorderColumnsOnDragEnd(table, event)}
                sensors={columnSensors}
              >
                {headerRows}
              </DndContext>
            ) : (
              headerRows
            )}
          </DataGridTableHead>

          {(props.tableLayout?.stripped || !props.tableLayout?.rowBorder) && <DataGridTableRowSpacer />}

          <DataGridTableBody>
            {showBodySkeleton ? (
              Array.from({ length: skeletonRowCount(pagination.pageSize) }).map((_, rowIndex) => (
                <DataGridTableBodyRowSkeleton key={rowIndex}>
                  {table.getVisibleFlatColumns().map((column, colIndex) => {
                    return (
                      <DataGridTableBodyRowSkeletonCell column={column} key={colIndex}>
                        {column.columnDef.meta?.skeleton}
                      </DataGridTableBodyRowSkeletonCell>
                    );
                  })}
                </DataGridTableBodyRowSkeleton>
              ))
            ) : rows.length ? (
              <SortableContext items={dataIds} strategy={verticalListSortingStrategy}>
                {rows.map((row: Row<TData>, index) => {
                  return (
                    <DataGridTableDndRow
                      row={row}
                      previousRow={index === 0 ? null : rows[index - 1]}
                      returnedFromId={returnedFromId}
                      key={row.id}
                    />
                  );
                })}
              </SortableContext>
            ) : (
              <DataGridTableEmpty />
            )}
          </DataGridTableBody>

          {/* The totals row, as the other render paths draw it: only when a column declares
              a `footer`, so a grid without one gains no empty row. */}
          {hasFooter && (
            <DataGridTableFoot>
              {footerGroupsWithContent(table).map((footerGroup) => (
                <tr key={footerGroup.id}>
                  {footerGroup.headers.map((header) => (
                    <DataGridTableFootRowCell key={header.id} header={header}>
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.footer, header.getContext())}
                    </DataGridTableFootRowCell>
                  ))}
                </tr>
              ))}
            </DataGridTableFoot>
          )}
        </DataGridTableBase>
      </div>
      {/* The row that follows the pointer while dragging, and the settle
          animation back into the list on drop (S8-06) - `useSortable`'s own
          FLIP already re-arranges the OTHER rows smoothly; this is what makes
          the dragged one itself land rather than just disappear. */}
      <DragOverlay dropAnimation={defaultDropAnimation}>
        {activeRow ? (
          // `overflow-x-auto`: the overlay has none of the real grid's width
          // constraint, so a row with more columns than fit under the pointer
          // scrolls sideways instead of being clipped.
          <div className="overflow-x-auto rounded-md border border-border bg-background shadow-lg">
            <table className="w-full caption-bottom text-sm">
              <tbody>
                {/* No `returnedFromId`: this floating clone follows the pointer, it is
                    never the settled row the reader returned to. */}
                <DataGridTableBodyRow row={activeRow}>
                  {activeRow.getVisibleCells().map((cell: Cell<TData, unknown>, colIndex) => (
                    <DataGridTableBodyRowCell cell={cell} key={colIndex}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </DataGridTableBodyRowCell>
                  ))}
                </DataGridTableBodyRow>
              </tbody>
            </table>
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}

export { DataGridTableDndRowHandle, DataGridTableDndRows };

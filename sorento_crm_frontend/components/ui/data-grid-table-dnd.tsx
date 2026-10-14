import { CSSProperties, Fragment, useId } from 'react';
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
  footerGroupsWithContent,
  DataGridTableHead,
  DataGridTableHeadRow,
  DataGridTableHeadRowCell,
  DataGridTableHeadRowCellResize,
  DataGridTableRowSpacer,
  headerRowSpan,
  useBodySkeleton,
  skeletonRowCount,
  skipMergedLeafHeader,
} from '@/components/ui/data-grid-table';
import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import { restrictToParentElement } from '@dnd-kit/modifiers';
import { horizontalListSortingStrategy, SortableContext, useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Cell, flexRender, Header, HeaderGroup, Row } from '@tanstack/react-table';
import { mergeColumnOrderWithLeafColumns } from '@/lib/listing-column-preferences/mergeColumnOrder';
import { GripVertical } from 'lucide-react';
import type { ColumnDef } from '@tanstack/react-table';

/**
 * AC-RS-69 (`PLAN-oi-request-cs-reserve.md` section 6d G3): the ONE predicate for "this
 * column is chrome, not a reader's own column" - a fixed utility column
 * (`meta.draggable === false`, e.g. the shared select column) or one carrying an
 * expanded row's own content (`meta.expandedContent`, e.g. the OI Lines "expand"
 * chevron). Exported for the row-drag grid's own column drag
 * (`data-grid-table-dnd-rows.tsx`), which must exclude the same columns - the header, the
 * body cell and the `orderedIds` drop-target filter below all read the SAME check
 * regardless - S1 (fix round 2): the body cell
 * used to run `useSortable` with no `disabled` at all, which could drift from what
 * the header decided the moment either predicate changed on one side only.
 */
export function isFixedUtilityColumn<TData>(columnDef: ColumnDef<TData>): boolean {
  return columnDef.meta?.draggable === false || Boolean(columnDef.meta?.expandedContent);
}

function DataGridTableDndHeader<TData>({
  header,
  rowSpan,
}: {
  header: Header<TData, unknown>;
  rowSpan: number;
}) {
  const { props } = useDataGrid();
  const { column } = header;

  // A GROUP header is not draggable: dragging it would mean dragging its members as a
  // block, and dnd-kit is registered on leaf ids. A PLACEHOLDER is the opposite - it IS
  // the single-level column's only header now that it spans both rows (the leaf it stands
  // in for is not rendered), so it stays draggable and each id registers exactly once.
  // Grids without column groups never take either branch.
  const isGroupHeader = !header.isPlaceholder && header.subHeaders.length > 0;
  // AC-RS-69: a fixed utility column or one carrying an expanded row's own content
  // offers no grip and no drag affordance - it is not a column a reader reorders, it is
  // part of the grid's own chrome. `isFixedUtilityColumn` is the ONE predicate, shared
  // with the body cell below and the `orderedIds` drop-target filter.
  const noDrag = isFixedUtilityColumn(header.column.columnDef);

  const { attributes, isDragging, listeners, setNodeRef, transform, transition } = useSortable({
    id: header.column.id,
    disabled: isGroupHeader || noDrag,
  });

  const style: CSSProperties = {
    opacity: isDragging ? 0.8 : 1,
    position: 'relative',
    transform: CSS.Translate.toString(transform),
    transition,
    whiteSpace: 'nowrap',
    width: header.column.getSize(),
    zIndex: isDragging ? 1 : 0,
  };

  if (isGroupHeader) {
    return (
      <DataGridTableHeadRowCell header={header}>
        <div className="flex w-full items-center justify-center text-center">
          {flexRender(header.column.columnDef.header, header.getContext())}
        </div>
      </DataGridTableHeadRowCell>
    );
  }

  return (
    <DataGridTableHeadRowCell
      header={header}
      dndStyle={style}
      dndDragging={isDragging}
      dndRef={setNodeRef}
      rowSpan={rowSpan}
    >
      {noDrag ? (
        <div className="flex items-center justify-start gap-0.5 w-full select-none">
          {flexRender(header.column.columnDef.header, header.getContext())}
          {props.tableLayout?.columnsResizable && column.getCanResize() && (
            <DataGridTableHeadRowCellResize header={header} />
          )}
        </div>
      ) : (
        <div
          className="flex items-center justify-start gap-0.5 w-full cursor-grab select-none"
          {...attributes}
          {...listeners}
          aria-label="Drag column to reorder"
        >
          {/* Keeping the grip icon purely visual (drag is on the entire header area). */}
          <GripVertical className="size-4 opacity-35 ms-1" aria-hidden="true" />
          {flexRender(header.column.columnDef.header, header.getContext())}
          {props.tableLayout?.columnsResizable && column.getCanResize() && (
            <DataGridTableHeadRowCellResize header={header} />
          )}
        </div>
      )}
    </DataGridTableHeadRowCell>
  );
}

function DataGridTableDndCell<TData>({ cell }: { cell: Cell<TData, unknown> }) {
  // S1 (fix round 2): the SAME predicate the header disables `useSortable` under - a
  // fixed-utility column's body cell must not register as sortable either, so it never
  // carries the drag attributes a reorder-enabled cell does.
  const sortableDisabled = isFixedUtilityColumn(cell.column.columnDef);
  const { isDragging, setNodeRef, transform, transition } = useSortable({
    id: cell.column.id,
    disabled: sortableDisabled,
  });

  const style: CSSProperties = {
    opacity: isDragging ? 0.8 : 1,
    position: 'relative',
    transform: CSS.Translate.toString(transform),
    transition,
    width: cell.column.getSize(),
    zIndex: isDragging ? 1 : 0,
  };

  return (
    <DataGridTableBodyRowCell
      cell={cell}
      dndStyle={style}
      dndDragging={isDragging}
      dndRef={setNodeRef}
      dndSortableDisabled={sortableDisabled}
    >
      {flexRender(cell.column.columnDef.cell, cell.getContext())}
    </DataGridTableBodyRowCell>
  );
}

function DataGridTableDnd<TData>({
  handleDragEnd,
  returnedFromId,
}: {
  handleDragEnd: (event: DragEndEvent) => void;
  /** Resolved once by `DataGridTable` and passed down - see `useReturnedRowId`'s doc. */
  returnedFromId: string | null;
}) {
  const { table, props } = useDataGrid();
  const pagination = table.getState().pagination;
  const showBodySkeleton = useBodySkeleton();

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
    useSensor(KeyboardSensor, {}),
  );

  const leafIds = table.getAllLeafColumns().map((c) => c.id);
  const rawOrder =
    Array.isArray(table.getState().columnOrder) && table.getState().columnOrder.length > 0
      ? (table.getState().columnOrder as string[])
      : leafIds;
  // AC-RS-69: a fixed utility / expanded-content column never registers as a drop
  // target either - dragging another column past it must not offer to land there.
  // Same `isFixedUtilityColumn` predicate the header and the body cell disable
  // `useSortable` under, so the three cannot drift apart.
  const nonDraggableIds = new Set(
    table
      .getAllLeafColumns()
      .filter((c) => isFixedUtilityColumn(c.columnDef))
      .map((c) => c.id),
  );
  const orderedIds = mergeColumnOrderWithLeafColumns(rawOrder, leafIds).filter(
    (id) => !nonDraggableIds.has(id),
  );

  return (
    <DndContext
      id={useId()}
      collisionDetection={closestCenter}
      modifiers={[restrictToParentElement]}
      onDragEnd={handleDragEnd}
      sensors={sensors}
    >
      <div className="relative">
        <DataGridTableBase>
          <DataGridTableHead>
            {table.getHeaderGroups().map((headerGroup: HeaderGroup<TData>, index) => {
              const rowCount = table.getHeaderGroups().length;
              return (
                <DataGridTableHeadRow headerGroup={headerGroup} key={index}>
                  <SortableContext items={orderedIds} strategy={horizontalListSortingStrategy}>
                    {headerGroup.headers
                      .filter((header) => !skipMergedLeafHeader(header, rowCount))
                      .map((header, index) => (
                        <DataGridTableDndHeader
                          header={header}
                          rowSpan={headerRowSpan(header, rowCount)}
                          key={index}
                        />
                      ))}
                  </SortableContext>
                </DataGridTableHeadRow>
              );
            })}
          </DataGridTableHead>

          {(props.tableLayout?.stripped || !props.tableLayout?.rowBorder) && <DataGridTableRowSpacer />}

          <DataGridTableBody>
            {showBodySkeleton ? (
              Array.from({ length: skeletonRowCount(pagination.pageSize) }).map((_, rowIndex) => (
                <DataGridTableBodyRowSkeleton key={rowIndex}>
                  {/* LEAF columns, as in DataGridTable: the flat list includes a group
                      PARENT, which is not a cell. */}
                  {table.getVisibleLeafColumns().map((column, colIndex) => {
                    return (
                      <DataGridTableBodyRowSkeletonCell column={column} key={colIndex}>
                        {column.columnDef.meta?.skeleton}
                      </DataGridTableBodyRowSkeletonCell>
                    );
                  })}
                </DataGridTableBodyRowSkeleton>
              ))
            ) : table.getRowModel().rows.length ? (
              table.getRowModel().rows.map((row: Row<TData>, index) => {
                // Same optional grouping as the non-draggable DataGridTable.
                // Both branches need it: columnsDraggable defaults to TRUE, so
                // this component - not the other one - is what most listings
                // actually render through.
                const groupHeader = props.renderGroupHeader?.(
                  row.original as TData,
                  index === 0 ? null : (table.getRowModel().rows[index - 1].original as TData),
                );
                return (
                  <Fragment key={row.id}>
                    {groupHeader != null && (
                      <tr className="bg-muted/50" data-testid="data-grid-group-header">
                        <td
                          colSpan={row.getVisibleCells().length}
                          className="px-4 py-2 text-xs font-medium text-muted-foreground"
                        >
                          {groupHeader}
                        </td>
                      </tr>
                    )}
                    <DataGridTableBodyRow row={row} returnedFromId={returnedFromId} key={index}>
                      {row.getVisibleCells().map((cell: Cell<TData, unknown>) => {
                        return (
                          <SortableContext
                            key={cell.id}
                            items={orderedIds}
                            strategy={horizontalListSortingStrategy}
                          >
                            <DataGridTableDndCell cell={cell} />
                          </SortableContext>
                        );
                      })}
                    </DataGridTableBodyRow>
                    {row.getIsExpanded() && <DataGridTableBodyRowExpandded row={row} />}
                  </Fragment>
                );
              })
            ) : (
              <DataGridTableEmpty />
            )}
          </DataGridTableBody>

          {/* Same totals row as the non-draggable branch. `columnsDraggable` defaults to TRUE,
              so this component is what a listing actually renders through, and a footer added
              only to the other branch would never appear. */}
          {table.getVisibleFlatColumns().some((column) => Boolean(column.columnDef.footer)) && (
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
    </DndContext>
  );
}

export { DataGridTableDnd };

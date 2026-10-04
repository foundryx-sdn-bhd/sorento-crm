'use client';

import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Braces, Plus, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import { FindBar, isFindChord } from '@/components/common/find-in-text/FindBar';
import { useFindController } from '@/components/common/find-in-text/useFindController';
import { splitTemplate } from '../../lib/promptSegments';
import type { RegistryVariableRow } from '../../services/aiPromptsService';

/** Variables that render several lines: drawn as a full-width block, not an inline pill. */
const BLOCK_VARIABLES = new Set(['statuses', 'domains_detail', 'entity_kinds_detail', 'specs']);

const CHIP_CLASS =
  'mx-0.5 inline-flex max-w-full flex-wrap items-center gap-1 rounded-md border border-primary/30 bg-primary/10 px-1.5 py-px align-baseline font-sans text-2xs leading-5 text-primary';
const BLOCK_CHIP_CLASS = 'mx-0 my-0.5 flex w-full';
const RENDERED_CLASS =
  'basis-full whitespace-pre-wrap break-words rounded border border-primary/20 bg-background px-2 py-1 font-mono text-2xs text-foreground';

type Piece = { node: Node; kind: 'text' | 'chip' | 'virtual'; text: string };

/**
 * The editor's DOM as a flat list of pieces, under ONE set of rules for serialising and for
 * find offsets (reviewer pass 2, B2: the two used to disagree once a block element appeared).
 * A chip is its exact token; a `<br>` is a newline (the caret placeholder after a trailing
 * newline is nothing); any other element (a block the browser inserted, a pasted wrapper) is
 * its content, on a new line when it starts a block.
 */
function pieces(root: Node): Piece[] {
  const out: Piece[] = [];
  let produced = '';
  const visit = (node: Node) => {
    node.childNodes.forEach((child) => {
      if (child.nodeType === Node.TEXT_NODE) {
        const text = child.textContent ?? '';
        out.push({ node: child, kind: 'text', text });
        produced += text;
      } else if (child instanceof HTMLElement) {
        if (child.dataset.chip) {
          const raw = child.dataset.raw ?? `{{${child.dataset.chip}}}`;
          out.push({ node: child, kind: 'chip', text: raw });
          produced += raw;
        } else if (child.tagName === 'BR') {
          if (child.dataset.trailing) return;
          out.push({ node: child, kind: 'virtual', text: '\n' });
          produced += '\n';
        } else {
          if (produced && !produced.endsWith('\n')) {
            out.push({ node: child, kind: 'virtual', text: '\n' });
            produced += '\n';
          }
          visit(child);
        }
      }
    });
  };
  visit(root);
  return out;
}

function serialiseNode(root: Node): string {
  return pieces(root)
    .map((p) => p.text)
    .join('');
}

/**
 * The prompt editor for a key with registry variables (PLAN-prompt-dynamic-30sep R5a; owner,
 * 30 Sep 2026: "it needs to stay visualized during edit time, all the time; it should be a
 * variable wired into the prompt; if I want to overwrite I can remove the variable").
 *
 * The text is edited in place; every registry variable is a chip that names its source and
 * row count, expands to the text the model receives, links to the page that edits it, and
 * is removed with its x (the owner's own words then take its place). The value is always
 * the plain template: a chip serialises back to exactly the token it came from.
 *
 * The DOM is built imperatively and rebuilt only when `value` changes from outside (a
 * version switch, an Undo): React re-rendering a contenteditable on every keystroke is what
 * throws the caret around. Find reuses the textarea editor's controller (R5c): matches are
 * highlighted, the caret moves only on an explicit next/previous, and closing find leaves the
 * match selected for editing in place.
 */
/** What the page around the editor can do to it: the wired panel's Insert. */
export interface PromptChipEditorHandle {
  /** Insert the variable at the owner's last caret in the editor (end when none). */
  insertVariable: (name: string) => void;
}

export const PromptChipEditor = forwardRef<
  PromptChipEditorHandle,
  {
    value: string;
    onChange: (next: string) => void;
    variables: RegistryVariableRow[];
    registryNames: string[];
    disabled?: boolean;
    className?: string;
  }
>(function PromptChipEditor({ value, onChange, variables, registryNames, disabled = false, className }, handleRef) {
  const ref = useRef<HTMLDivElement>(null);
  const emitted = useRef<string | null>(null);
  const savedRange = useRef<Range | null>(null);
  // The last caret as an offset into the VALUE (owner hand test #1405, item 2): a DOM Range
  // dies with the nodes it points into (a refetch redraw, a version switch), an offset does
  // not, so an insert after focus moved away still lands where the owner left the caret.
  const savedOffset = useRef<number | null>(null);
  const [undoValue, setUndoValue] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerQuery, setPickerQuery] = useState('');

  const byName = useMemo(() => new Map(variables.map((v) => [v.name, v])), [variables]);
  const namesKey = registryNames.join(',');

  // ---- DOM <-> value ------------------------------------------------------------------

  const makeChip = useCallback(
    (name: string, raw: string): HTMLElement => {
      const meta = byName.get(name);
      const chip = document.createElement('span');
      chip.dataset.chip = name;
      chip.dataset.raw = raw;
      chip.setAttribute('contenteditable', 'false');
      chip.className = cn(CHIP_CLASS, BLOCK_VARIABLES.has(name) && BLOCK_CHIP_CLASS);

      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.dataset.action = 'toggle';
      toggle.dataset.testid = `chip-toggle-${name}`;
      toggle.setAttribute('data-testid', `chip-toggle-${name}`);
      toggle.setAttribute('aria-label', `Show what ${meta?.label ?? name} renders`);
      toggle.setAttribute('aria-expanded', 'false');
      toggle.className = 'rounded px-0.5 text-primary/70 hover:bg-primary/10';
      toggle.textContent = '▸';
      chip.appendChild(toggle);

      const label = document.createElement('span');
      label.className = 'font-semibold';
      label.textContent = meta?.label ?? name;
      chip.appendChild(label);

      if (meta) {
        const detail = document.createElement('span');
        detail.className = 'text-primary/70';
        detail.textContent = `${meta.count} rows, from ${meta.source}`;
        chip.appendChild(detail);
        if (meta.href) {
          const link = document.createElement('a');
          link.href = meta.href;
          link.target = '_blank';
          link.rel = 'noopener';
          link.setAttribute('aria-label', `Open ${meta.source}`);
          link.className = 'px-0.5 text-primary/70 hover:text-primary';
          link.textContent = '↗';
          chip.appendChild(link);
        }
      }

      if (!disabled) {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.dataset.action = 'remove';
        remove.setAttribute('data-testid', `chip-remove-${name}`);
        remove.setAttribute('aria-label', `Remove ${meta?.label ?? name}`);
        remove.className = 'rounded px-0.5 text-primary/70 hover:bg-destructive/10 hover:text-destructive';
        remove.textContent = '×';
        chip.appendChild(remove);
      }

      const rendered = document.createElement('span');
      rendered.setAttribute('data-testid', `chip-rendered-${name}`);
      rendered.className = RENDERED_CLASS;
      rendered.hidden = true;
      rendered.textContent = meta?.rendered ?? '';
      chip.appendChild(rendered);
      return chip;
    },
    [byName, disabled],
  );

  const build = useCallback(
    (template: string) => {
      const root = ref.current;
      if (!root) return;
      root.replaceChildren();
      for (const seg of splitTemplate(template, registryNames)) {
        root.appendChild(seg.kind === 'text' ? document.createTextNode(seg.text) : makeChip(seg.name, seg.raw));
      }
    },
    // namesKey stands in for registryNames' contents.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [makeChip, namesKey],
  );

  const serialise = serialiseNode;

  const emit = useCallback(() => {
    const root = ref.current;
    if (!root) return;
    const next = serialise(root);
    emitted.current = next;
    onChange(next);
  }, [onChange, serialise]);

  // Rebuild only for an outside change; a change this editor emitted is already on screen.
  useLayoutEffect(() => {
    if (value === emitted.current) return;
    build(value);
    emitted.current = value;
    setUndoValue(null);
  }, [value, build]);

  // Registry data arriving (or changing) redraws each chip in place. Only the chip elements
  // are replaced: the text nodes, and with them the owner's caret, stay where they are.
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    root.querySelectorAll<HTMLElement>('[data-chip]').forEach((chip) => {
      chip.replaceWith(makeChip(chip.dataset.chip ?? '', chip.dataset.raw ?? `{{${chip.dataset.chip}}}`));
    });
  }, [makeChip]);

  // ---- caret, chip actions, insert ----------------------------------------------------

  /** The value offset of a DOM position inside the editor. */
  const offsetOf = (container: Node, offset: number): number | null => {
    const root = ref.current;
    if (!root || !root.contains(container)) return null;
    const before = document.createRange();
    before.setStart(root, 0);
    before.setEnd(container, offset);
    return serialiseNode(before.cloneContents()).length;
  };

  /** A collapsed DOM Range at a value offset (the end when the offset is past it). */
  const rangeAtOffset = (offset: number): Range | null => {
    const root = ref.current;
    if (!root) return null;
    const range = document.createRange();
    let pos = 0;
    for (const piece of pieces(root)) {
      const len = piece.text.length;
      if (piece.kind === 'text' && offset >= pos && offset <= pos + len) {
        range.setStart(piece.node, offset - pos);
        range.collapse(true);
        return range;
      }
      if (piece.kind !== 'text' && offset === pos) {
        range.setStartBefore(piece.node);
        range.collapse(true);
        return range;
      }
      pos += len;
    }
    range.selectNodeContents(root);
    range.collapse(false);
    return range;
  };

  const rememberCaret = () => {
    const sel = typeof window !== 'undefined' ? window.getSelection() : null;
    if (sel && sel.rangeCount > 0 && ref.current?.contains(sel.getRangeAt(0).startContainer)) {
      const range = sel.getRangeAt(0);
      savedRange.current = range.cloneRange();
      savedOffset.current = offsetOf(range.startContainer, range.startOffset);
    }
  };

  /** Where an insert goes: the live selection in the editor, else the remembered caret. */
  const insertionRange = (): Range | null => {
    const root = ref.current;
    if (!root) return null;
    const sel = window.getSelection();
    if (sel && sel.rangeCount > 0 && root.contains(sel.getRangeAt(0).startContainer)) return sel.getRangeAt(0);
    if (savedOffset.current != null) return rangeAtOffset(savedOffset.current);
    return null;
  };

  const onRootClick = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    const button = target.closest<HTMLElement>('[data-action]');
    const chip = target.closest<HTMLElement>('[data-chip]');
    if (!button || !chip) {
      rememberCaret();
      return;
    }
    e.preventDefault();
    if (button.dataset.action === 'toggle') {
      const rendered = chip.querySelector<HTMLElement>('[data-testid^="chip-rendered-"]');
      if (rendered) rendered.hidden = !rendered.hidden;
      button.textContent = rendered && !rendered.hidden ? '▾' : '▸';
      button.setAttribute('aria-expanded', rendered && !rendered.hidden ? 'true' : 'false');
    } else if (button.dataset.action === 'remove' && !disabled) {
      setUndoValue(emitted.current ?? value);
      chip.remove();
      emit();
    }
  };

  const insertVariable = (name: string) => {
    const root = ref.current;
    if (!root || disabled) return;
    const chip = makeChip(name, `{{${name}}}`);
    const range = insertionRange();
    if (range) {
      range.deleteContents();
      range.insertNode(chip);
      range.setStartAfter(chip);
      range.collapse(true);
      savedRange.current = range;
    } else {
      root.appendChild(chip);
    }
    savedOffset.current = offsetOf(root, Array.from(root.childNodes).indexOf(chip) + 1);
    setPickerOpen(false);
    setPickerQuery('');
    setUndoValue(null);
    emit();
  };

  /**
   * Insert text at `at` (else the selection, else the remembered caret, else the end), never
   * through `execCommand`: Chromium answers a newline with a `<div>` around the rest of the
   * text (reviewer pass 2, B2). Registry tokens in the text become chips, so a paste or a drop
   * of `{{domains}}` is the variable, not its letters.
   */
  const insertText = (text: string, at?: Range | null) => {
    const root = ref.current;
    if (!root || disabled) return;
    const sel = window.getSelection();
    let range: Range | null = at ?? insertionRange();
    if (!range) {
      range = document.createRange();
      range.selectNodeContents(root);
      range.collapse(false);
    }
    range.deleteContents();
    const fragment = document.createDocumentFragment();
    let lastNode: Node | null = null;
    for (const seg of splitTemplate(text, registryNames)) {
      lastNode = seg.kind === 'text' ? document.createTextNode(seg.text) : makeChip(seg.name, seg.raw);
      fragment.appendChild(lastNode);
    }
    range.insertNode(fragment);
    if (lastNode) {
      // A newline as the very last thing needs a placeholder, or its empty line never shows.
      const following = lastNode.nextSibling;
      if (text.endsWith('\n') && (!following || (following instanceof HTMLElement && following.dataset.trailing))) {
        if (!following) {
          const placeholder = document.createElement('br');
          placeholder.dataset.trailing = '1';
          root.appendChild(placeholder);
        }
      }
      range.setStartAfter(lastNode);
      range.collapse(true);
      sel?.removeAllRanges();
      sel?.addRange(range);
      savedRange.current = range.cloneRange();
      savedOffset.current = offsetOf(range.startContainer, range.startOffset);
    }
    setUndoValue(null);
    emit();
  };

  /** The selection inside the editor, and its value text with chips as their tokens. */
  const selectionTokens = (): { text: string; range: Range } | null => {
    const root = ref.current;
    const sel = window.getSelection();
    if (!root || !sel || sel.rangeCount === 0) return null;
    const range = sel.getRangeAt(0);
    if (!root.contains(range.commonAncestorContainer)) return null;
    return { text: serialiseNode(range.cloneContents()), range };
  };
  const dragSource = useRef<Range | null>(null);

  // ---- find ----------------------------------------------------------------------------

  // The text find searches: the value with every chip masked, so a match never lands in or
  // across a variable, and every offset is an offset into `value` itself.
  const findText = useMemo(() => {
    let out = '';
    // From `value` itself, never `emitted.current`: on a version switch the ref still holds
    // the OLD text during this render, and find indexed the wrong version (owner hand test
    // #1405, item 1).
    for (const seg of splitTemplate(value, registryNames)) {
      out += seg.kind === 'text' ? seg.text : '\u0000'.repeat(seg.raw.length);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, namesKey]);
  const find = useFindController(findText);
  const { open, matches, activeIndex, jumpSeq, openFind, close } = find;
  const activeOffsets = useRef<{ start: number; end: number } | null>(null);

  /** A DOM Range for value offsets [start, end), or null when it would touch a chip. */
  const rangeFor = useCallback((start: number, end: number): Range | null => {
    const root = ref.current;
    if (!root) return null;
    let pos = 0;
    let startNode: Node | null = null;
    let startOff = 0;
    let endNode: Node | null = null;
    let endOff = 0;
    for (const piece of pieces(root)) {
      const len = piece.text.length;
      if (piece.kind === 'text') {
        if (!startNode && start >= pos && start <= pos + len) {
          startNode = piece.node;
          startOff = start - pos;
        }
        if (startNode && end >= pos && end <= pos + len) {
          endNode = piece.node;
          endOff = end - pos;
          break;
        }
      }
      pos += len;
    }
    if (!startNode || !endNode) return null;
    const range = document.createRange();
    range.setStart(startNode, startOff);
    range.setEnd(endNode, endOff);
    return range;
  }, []);

  // Highlight every match; the active one differently. Recomputed on every text change,
  // which never touches the caret (CSS Custom Highlight API; a no-op where unsupported).
  useEffect(() => {
    const registry = (globalThis as { CSS?: { highlights?: Map<string, unknown> } }).CSS?.highlights;
    const HighlightCtor = (globalThis as { Highlight?: new (...r: Range[]) => unknown }).Highlight;
    if (!registry || !HighlightCtor) return;
    if (!open || matches.length === 0) {
      registry.delete('prompt-find');
      registry.delete('prompt-find-active');
      return;
    }
    const all = matches.map((m) => rangeFor(m.start, m.end)).filter((r): r is Range => r != null);
    registry.set('prompt-find', new HighlightCtor(...all));
    const active = activeIndex >= 0 ? rangeFor(matches[activeIndex].start, matches[activeIndex].end) : null;
    if (active) registry.set('prompt-find-active', new HighlightCtor(active));
    else registry.delete('prompt-find-active');
    return () => {
      registry.delete('prompt-find');
      registry.delete('prompt-find-active');
    };
  }, [open, matches, activeIndex, rangeFor]);

  // Jump to the active match on an explicit find navigation only (jumpSeq), never because
  // the text changed: that is the R5c caret bug, not repeated here.
  const latest = useRef({ matches, activeIndex, open });
  latest.current = { matches, activeIndex, open };
  useEffect(() => {
    const { matches: ms, activeIndex: idx, open: isOpen } = latest.current;
    const root = ref.current;
    if (!root || !isOpen || idx < 0 || !ms[idx]) return;
    const { start, end } = ms[idx];
    activeOffsets.current = { start, end };
    root.setAttribute('data-find-active', `${start}-${end}`);
    const range = rangeFor(start, end);
    if (range && typeof range.getBoundingClientRect === 'function') {
      const rect = range.getBoundingClientRect();
      const box = root.getBoundingClientRect();
      root.scrollTop += rect.top - box.top - root.clientHeight / 2;
    }
  }, [jumpSeq, rangeFor]);

  const dismissFind = () => {
    const root = ref.current;
    const offsets = activeOffsets.current;
    root?.removeAttribute('data-find-active');
    if (!root || !offsets) return;
    const range = rangeFor(offsets.start, offsets.end);
    root.focus();
    if (range) {
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
      savedRange.current = range.cloneRange();
    }
  };

  useImperativeHandle(handleRef, () => ({ insertVariable }));

  // ---- render --------------------------------------------------------------------------

  const pickerOptions = variables.filter(
    (v) =>
      !pickerQuery ||
      v.label.toLowerCase().includes(pickerQuery.toLowerCase()) ||
      v.source.toLowerCase().includes(pickerQuery.toLowerCase()),
  );

  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex flex-wrap items-center gap-2">
        {!disabled ? (
          <Popover
            open={pickerOpen}
            onOpenChange={(o) => {
              setPickerOpen(o);
              if (!o) setPickerQuery('');
            }}
          >
            <PopoverTrigger asChild>
              <Button type="button" variant="outline" size="sm" data-testid="insert-variable">
                <Plus className="size-4" /> Insert variable
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-72 p-2" data-testid="insert-variable-menu">
              <Input
                value={pickerQuery}
                onChange={(e) => setPickerQuery(e.target.value)}
                placeholder="Search variables"
                className="mb-2 h-8 text-xs"
              />
              <div className="max-h-64 overflow-auto">
                {pickerOptions.length === 0 ? (
                  <p className="px-2 py-1 text-xs text-muted-foreground">No variable matches.</p>
                ) : (
                  pickerOptions.map((v) => (
                    <button
                      key={v.name}
                      type="button"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => insertVariable(v.name)}
                      data-testid={`insert-variable-${v.name}`}
                      className="flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left text-xs hover:bg-muted"
                    >
                      <span className="flex min-w-0 items-center gap-1.5">
                        <Braces className="size-3.5 shrink-0 text-primary" />
                        <span className="truncate font-medium">{v.label}</span>
                      </span>
                      <span className="shrink-0 text-muted-foreground">{v.count} rows</span>
                    </button>
                  ))
                )}
              </div>
            </PopoverContent>
          </Popover>
        ) : null}
        <Button type="button" variant="outline" size="sm" onClick={openFind} data-testid="open-find">
          <Search className="size-4" /> Find
        </Button>
      </div>

      <div className="relative">
        <FindBar controller={find} onDismiss={dismissFind} />
        <div
          ref={ref}
          role="textbox"
          aria-multiline="true"
          aria-label="Prompt"
          aria-readonly={disabled}
          contentEditable={!disabled}
          suppressContentEditableWarning
          spellCheck={false}
          data-testid="prompt-chip-editor"
          className="max-h-[70dvh] min-h-[320px] overflow-y-auto overflow-x-hidden whitespace-pre-wrap [overflow-wrap:anywhere] rounded-md border bg-background p-3 font-mono text-xs leading-relaxed outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onInput={() => {
            // Typing after a removal makes the removal final: Undo would roll the typing back.
            setUndoValue(null);
            emit();
            rememberCaret();
          }}
          onKeyUp={rememberCaret}
          onMouseUp={rememberCaret}
          onClick={onRootClick}
          onBlur={rememberCaret}
          onPaste={(e) => {
            e.preventDefault();
            insertText(e.clipboardData.getData('text/plain'));
          }}
          onCopy={(e) => {
            // The tokens, not the chips' labels (reviewer pass 2, B1).
            const picked = selectionTokens();
            if (!picked) return;
            e.preventDefault();
            e.clipboardData.setData('text/plain', picked.text);
          }}
          onCut={(e) => {
            const picked = selectionTokens();
            if (!picked) return;
            e.preventDefault();
            e.clipboardData.setData('text/plain', picked.text);
            if (disabled) return;
            picked.range.deleteContents();
            setUndoValue(null);
            emit();
          }}
          onDragStart={(e) => {
            const picked = selectionTokens();
            if (!picked) return;
            e.dataTransfer.setData('text/plain', picked.text);
            dragSource.current = picked.range.cloneRange();
          }}
          onDragEnd={() => {
            dragSource.current = null;
          }}
          onDrop={(e) => {
            // Plain text (tokens become chips), at the drop point; a drag from inside the
            // editor moves its text rather than copying it.
            e.preventDefault();
            const doc = document as Document & {
              caretRangeFromPoint?: (x: number, y: number) => Range | null;
            };
            const target =
              typeof doc.caretRangeFromPoint === 'function' && (e.clientX || e.clientY)
                ? doc.caretRangeFromPoint(e.clientX, e.clientY)
                : null;
            const source = dragSource.current;
            dragSource.current = null;
            insertText(e.dataTransfer.getData('text/plain'), target);
            if (source && !source.collapsed && ref.current?.contains(source.commonAncestorContainer)) {
              source.deleteContents();
              emit();
            }
          }}
          onKeyDown={(e) => {
            if (isFindChord(e)) {
              e.preventDefault();
              openFind();
            } else if (e.key === 'Escape' && open) {
              e.preventDefault();
              close();
              dismissFind();
            } else if (e.key === 'Enter' && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
              // A newline inserted by this editor, never by the browser (reviewer pass 2, B2).
              e.preventDefault();
              insertText('\n');
            }
          }}
        />
      </div>

      {undoValue != null ? (
        <div
          className="flex items-center justify-between gap-2 rounded-md border bg-muted/40 px-3 py-1.5 text-xs"
          role="status"
        >
          <span>Variable removed. Your text takes over here.</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            data-testid="chip-undo"
            onClick={() => {
              const previous = undoValue;
              setUndoValue(null);
              onChange(previous);
            }}
          >
            Undo
          </Button>
        </div>
      ) : null}

      {/* Inline on purpose: Next's CSS parser (lightningcss) rejects `::highlight()` in a
          stylesheet ("'highlight' is not recognized as a valid pseudo-element"), and the
          page then fails to compile (measured 1 Oct 2026). A runtime <style> string is not
          parsed at build time. */}
      <style>{`::highlight(prompt-find){background-color:color-mix(in oklab, var(--color-warning, #f59e0b) 40%, transparent)}::highlight(prompt-find-active){background-color:color-mix(in oklab, var(--color-primary, #2563eb) 40%, transparent)}`}</style>
    </div>
  );
});

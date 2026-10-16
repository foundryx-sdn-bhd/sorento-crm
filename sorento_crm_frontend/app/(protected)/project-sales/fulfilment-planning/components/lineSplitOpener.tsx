'use client';

import * as React from 'react';
import type { BoardContribution } from '../../_shared/types/fulfilmentPlanning.types';

/**
 * OI-LINE-SPLIT: how a board line opens the whole-SO split view zoomed to itself ("Split
 * delivery…" in the Decide menu, the What changed lightbox). `FulfilmentBoardPanel` owns the
 * one dialog and provides this; `canOpen` is false for a line on an order nobody has adopted
 * (no planning record, no mirror line to split). Absent outside the board.
 */
export interface LineSplitOpener {
  canOpen: (contribution: BoardContribution) => boolean;
  open: (contribution: BoardContribution) => void;
}

export const LineSplitOpenerContext =
  React.createContext<LineSplitOpener | null>(null);

export function useLineSplitOpener(): LineSplitOpener | null {
  return React.useContext(LineSplitOpenerContext);
}

/**
 * QUOTE-APPROVAL-UX G1 + G2 - the quotation page header leads back to the project's
 * Quotations tab: "Back to project" (top right) and the "Project" breadcrumb.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/project-sales/p1/quotation-documents/d1',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('../../../../_shared/hooks/useQuotationDocuments', () => ({
  useQuotationDocument: () => ({ data: { document_no: 'SRT/Q/2026/0141' } }),
}));

import { QuotationDocumentPageHeader } from './QuotationDocumentPageHeader';

describe('QuotationDocumentPageHeader', () => {
  it('G1: shows a Back to project link to the project Quotations tab', () => {
    render(<QuotationDocumentPageHeader projectId="p1" documentId="d1" />);

    const back = screen.getByRole('link', { name: /Back to project/i });
    expect(back).toHaveAttribute('href', '/project-sales/p1?tab=quotations');
  });

  it('G2: the Project breadcrumb links to the Quotations tab too', () => {
    render(<QuotationDocumentPageHeader projectId="p1" documentId="d1" />);

    const crumb = screen.getByRole('link', { name: 'Project' });
    expect(crumb).toHaveAttribute('href', '/project-sales/p1?tab=quotations');
  });
});

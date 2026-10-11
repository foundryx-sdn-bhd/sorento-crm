import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';

import ProductVariantsTab from './ProductVariantsTab';
import type { ProductVariantRef } from '../../types/product.types';

vi.mock('next/link', () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: ReactNode;
  } & Record<string, unknown>) => (
    <a href={typeof href === 'string' ? href : '#'} {...rest}>
      {children}
    </a>
  ),
}));

// Mutation hooks are mocked so the tab test asserts wiring (which hook fires with
// which ids), not the network layer. Spies are hoisted so the vi.mock factory can
// close over them.
const { setParentMutate, unlinkMutate, resetMutate, setFamilyMutate, resetFamilyMutate } =
  vi.hoisted(() => ({
    setParentMutate: vi.fn(),
    unlinkMutate: vi.fn(),
    resetMutate: vi.fn(),
    setFamilyMutate: vi.fn(),
    resetFamilyMutate: vi.fn(),
  }));

vi.mock('../../hooks/useProducts', () => ({
  useSetVariantParent: () => ({ mutate: setParentMutate, isPending: false }),
  useUnlinkVariant: () => ({ mutate: unlinkMutate, isPending: false }),
  useResetVariantAuto: () => ({ mutate: resetMutate, isPending: false }),
  useSetFamilyCode: () => ({ mutate: setFamilyMutate, isPending: false }),
  useResetFamilyCode: () => ({ mutate: resetFamilyMutate, isPending: false }),
}));

// The picker is a heavy radix combobox with its own dedicated test
// (ProductVariantPickerDialog.test.tsx). Here we stub it to a minimal dialog that
// surfaces the props the tab passes (title / excludeIds) and can fire onConfirm.
vi.mock('./ProductAlternativesSection', () => ({ default: () => null }));

vi.mock('./ProductVariantPickerDialog', () => ({
  default: ({
    open,
    title,
    excludeIds = [],
    onConfirm,
  }: {
    open: boolean;
    title: string;
    excludeIds?: string[];
    onConfirm: (id: string) => void;
  }) =>
    open ? (
      <div role="dialog" aria-label={title} data-exclude={JSON.stringify(excludeIds)}>
        <button type="button" onClick={() => onConfirm('picked-id')}>
          {`Confirm ${title}`}
        </button>
      </div>
    ) : null,
}));

const PARENT: ProductVariantRef = {
  id: 'p-parent-uuid',
  product_code: 'SRTKT71SS',
  product_name: 'Kitchen Tap 71 Stainless',
};

const CHILDREN: ProductVariantRef[] = [
  {
    id: 'c-1-uuid',
    product_code: 'SRTKT71SS-BL',
    product_name: 'Kitchen Tap 71 Stainless Black',
  },
  {
    id: 'c-2-uuid',
    product_code: 'SRTKT71SS-GM',
    product_name: 'Kitchen Tap 71 Stainless Gunmetal',
  },
];

const PRODUCT_ID = 'p-me';

beforeEach(() => {
  setParentMutate.mockReset();
  unlinkMutate.mockReset();
  resetMutate.mockReset();
  setFamilyMutate.mockReset();
  resetFamilyMutate.mockReset();
  // radix AlertDialog / primitives touch browser APIs jsdom lacks.
  Element.prototype.scrollIntoView = vi.fn();
  (Element.prototype as unknown as { hasPointerCapture: unknown }).hasPointerCapture =
    vi.fn();
  (Element.prototype as unknown as { setPointerCapture: unknown }).setPointerCapture =
    vi.fn();
  (
    Element.prototype as unknown as { releasePointerCapture: unknown }
  ).releasePointerCapture = vi.fn();
});

afterEach(() => cleanup());

describe('ProductVariantsTab - states', () => {
  it('base product: shows an "Add variant" CTA, no Unlink / Manual badge / Reset', () => {
    render(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="ACC-4001"
        variantOf={null}
        variants={[]}
        variantLinkManual={false}
      />,
    );

    // Base line + empty-state both surface an Add variant CTA.
    expect(screen.getAllByRole('button', { name: /add variant/i }).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/it is not a variant of another product/i),
    ).toBeInTheDocument();
    expect(screen.getByText('No variants of this product.')).toBeInTheDocument();

    // No parent → no Unlink; not manual → no badge / no Reset.
    expect(screen.queryByRole('button', { name: /^unlink$/i })).not.toBeInTheDocument();
    expect(screen.queryByText('Manual')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /reset to auto/i }),
    ).not.toBeInTheDocument();
  });

  it('variant-with-parent: shows Unlink and renders the parent code human-readable (no UUID)', () => {
    render(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="SRTKT71SS-BL"
        variantOf={PARENT}
        variants={[]}
        variantLinkManual={false}
      />,
    );

    expect(screen.getByRole('button', { name: /^unlink$/i })).toBeInTheDocument();
    const parentLink = screen.getByRole('link', { name: /SRTKT71SS/ });
    expect(parentLink).toHaveAttribute(
      'href',
      '/master-data-management/products/p-parent-uuid',
    );
    expect(screen.getByText('Kitchen Tap 71 Stainless')).toBeInTheDocument();
    // The parent's UUID must never surface as visible text.
    expect(parentLink.textContent).not.toContain('p-parent-uuid');
  });

  it('Manual badge + Reset button appear IFF variantLinkManual is true', () => {
    const { rerender } = render(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="SRTKT71SS-BL"
        variantOf={PARENT}
        variants={[]}
        variantLinkManual={false}
      />,
    );
    expect(screen.queryByText('Manual')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /reset to auto/i }),
    ).not.toBeInTheDocument();

    rerender(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="SRTKT71SS-BL"
        variantOf={PARENT}
        variants={[]}
        variantLinkManual={true}
      />,
    );
    expect(screen.getByText('Manual')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reset to auto/i })).toBeInTheDocument();
  });

  it('never renders a raw UUID anywhere in the output', () => {
    const { container } = render(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="SRTKT71SS"
        variantOf={PARENT}
        variants={CHILDREN}
        variantLinkManual={false}
      />,
    );
    expect(container.textContent).not.toContain('p-parent-uuid');
    expect(container.textContent).not.toContain('c-1-uuid');
    expect(container.textContent).not.toContain('c-2-uuid');
    expect(container.textContent).not.toContain(PRODUCT_ID);
  });
});

describe('ProductVariantsTab - actions fire the right hook', () => {
  it('set-parent picker confirm → setVariantParent(productId, parentId)', () => {
    render(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="ACC-4001"
        variantOf={null}
        variants={[]}
        variantLinkManual={false}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /set parent/i }));
    const picker = screen.getByRole('dialog', { name: 'Set variant parent' });
    fireEvent.click(within(picker).getByRole('button', { name: /confirm/i }));

    expect(setParentMutate).toHaveBeenCalledWith(
      { productId: PRODUCT_ID, parentId: 'picked-id' },
      expect.anything(),
    );
  });

  it('add-child picker confirm → setVariantParent(childId, currentId), excluding self + children', () => {
    render(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="SRTKT71SS"
        variantOf={null}
        variants={CHILDREN}
        variantLinkManual={false}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /add variant/i }));
    const picker = screen.getByRole('dialog', { name: 'Add variant' });
    // Excludes self + existing children.
    expect(JSON.parse(picker.getAttribute('data-exclude') || '[]')).toEqual([
      PRODUCT_ID,
      'c-1-uuid',
      'c-2-uuid',
    ]);

    fireEvent.click(within(picker).getByRole('button', { name: /confirm/i }));
    expect(setParentMutate).toHaveBeenCalledWith(
      { productId: 'picked-id', parentId: PRODUCT_ID },
      expect.anything(),
    );
  });
});

describe('ProductVariantsTab - destructive actions require an AlertDialog confirm', () => {
  it('unlink: dialog appears before the mutation fires → unlinkVariant(productId, parentId)', () => {
    render(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="SRTKT71SS-BL"
        variantOf={PARENT}
        variants={[]}
        variantLinkManual={true}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /^unlink$/i }));
    // Confirmation is required - the mutation must NOT fire on the first click.
    expect(unlinkMutate).not.toHaveBeenCalled();

    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('Unlink from parent?')).toBeInTheDocument();
    expect(within(dialog).getByText(/you can re-link it later/i)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /^unlink$/i }));
    expect(unlinkMutate).toHaveBeenCalledWith(
      { productId: PRODUCT_ID, parentId: 'p-parent-uuid' },
      expect.anything(),
    );
  });

  it('remove-child: dialog appears before the mutation fires → unlinkVariant(childId, currentId)', () => {
    render(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="SRTKT71SS"
        variantOf={null}
        variants={CHILDREN}
        variantLinkManual={false}
      />,
    );

    fireEvent.click(
      screen.getAllByRole('button', { name: /remove variant/i })[0],
    );
    expect(unlinkMutate).not.toHaveBeenCalled();

    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('Remove variant?')).toBeInTheDocument();
    expect(
      within(dialog).getByText(/will no longer be a variant of this product/i),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /^remove$/i }));
    expect(unlinkMutate).toHaveBeenCalledWith(
      { productId: 'c-1-uuid', parentId: PRODUCT_ID },
      expect.anything(),
    );
  });

  it('reset: dialog appears before the mutation fires → resetVariantAuto(productId)', () => {
    render(
      <ProductVariantsTab
        productId={PRODUCT_ID}
        productCode="SRTKT71SS-BL"
        variantOf={PARENT}
        variants={[]}
        variantLinkManual={true}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /reset to auto/i }));
    expect(resetMutate).not.toHaveBeenCalled();

    const dialog = screen.getByRole('alertdialog');
    expect(
      within(dialog).getByText('Reset to automatic linking?'),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/clears the manual override/i),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /reset to auto/i }));
    expect(resetMutate).toHaveBeenCalledWith(
      { productId: PRODUCT_ID },
      expect.anything(),
    );
  });
});

// CODE-VARIANTS AC-10: the Family section (mock: documentation/mockups/code-variants/index.html).
// Scoped through data-testid="family-section" because the tab already has its own
// "Manual" badge and "Reset to auto" button for the variant link; the Family section must
// not be confused with them.
const FAMILY_MEMBERS: ProductVariantRef[] = [
  { id: 'f-1-uuid', product_code: 'SRTWC8357-RL-P', product_name: 'WC 8357 RL P' },
  { id: 'f-2-uuid', product_code: 'SRTWC8357-200-RL', product_name: 'WC 8357 200 RL' },
];

function renderFamily(over: Record<string, unknown> = {}) {
  return render(
    <ProductVariantsTab
      productId={PRODUCT_ID}
      productCode="SRTWC8357-RL"
      variantOf={null}
      variants={[]}
      variantLinkManual={false}
      familyCode="SRTWC8357"
      familyCodeManual={false}
      familyMembers={FAMILY_MEMBERS}
      {...over}
    />,
  );
}

const familySection = () => within(screen.getByTestId('family-section'));

describe('ProductVariantsTab - Family section', () => {
  it('shows the family code, no Manual badge and no Reset when auto', () => {
    renderFamily();
    const fam = familySection();
    expect(fam.getByText('SRTWC8357')).toBeInTheDocument();
    expect(fam.queryByText('Manual')).not.toBeInTheDocument();
    expect(fam.queryByRole('button', { name: /reset to auto/i })).not.toBeInTheDocument();
    expect(fam.getByRole('button', { name: /edit/i })).toBeInTheDocument();
  });

  it('Manual badge and Reset to auto appear when the family is hand-set', () => {
    renderFamily({ familyCodeManual: true });
    const fam = familySection();
    expect(fam.getByText('Manual')).toBeInTheDocument();
    expect(fam.getByRole('button', { name: /reset to auto/i })).toBeInTheDocument();
  });

  it('Edit swaps the value for an input; Save calls the hook with the typed value', () => {
    renderFamily();
    const fam = familySection();
    fireEvent.click(fam.getByRole('button', { name: /edit/i }));
    const input = fam.getByLabelText(/family code/i) as HTMLInputElement;
    expect(input.value).toBe('SRTWC8357');
    fireEvent.change(input, { target: { value: 'WC8357X' } });
    fireEvent.click(fam.getByRole('button', { name: /^save$/i }));
    expect(setFamilyMutate).toHaveBeenCalledWith(
      { productId: PRODUCT_ID, familyCode: 'WC8357X' },
      expect.anything(),
    );
  });

  it('Cancel leaves edit mode without calling the hook', () => {
    renderFamily();
    const fam = familySection();
    fireEvent.click(fam.getByRole('button', { name: /edit/i }));
    fireEvent.click(fam.getByRole('button', { name: /^cancel$/i }));
    expect(setFamilyMutate).not.toHaveBeenCalled();
    expect(fam.queryByLabelText(/family code/i)).not.toBeInTheDocument();
  });

  it('Reset to auto calls the reset hook with the product id', () => {
    renderFamily({ familyCodeManual: true });
    fireEvent.click(familySection().getByRole('button', { name: /reset to auto/i }));
    expect(resetFamilyMutate).toHaveBeenCalledWith(
      { productId: PRODUCT_ID },
      expect.anything(),
    );
  });

  it('empty state: no family code shows a heading and hint, still editable', () => {
    renderFamily({ familyCode: null, familyMembers: [] });
    const fam = familySection();
    expect(fam.getByText('No family')).toBeInTheDocument();
    expect(fam.getByText(/this code has no family part/i)).toBeInTheDocument();
    expect(fam.getByRole('button', { name: /edit/i })).toBeInTheDocument();
  });

  it('family members render as links to their detail pages, no UUID text', () => {
    const { container } = renderFamily();
    const fam = familySection();
    const link = fam.getByRole('link', { name: /SRTWC8357-RL-P/ });
    expect(link).toHaveAttribute('href', '/master-data-management/products/f-1-uuid');
    expect(fam.getByRole('link', { name: /SRTWC8357-200-RL/ })).toHaveAttribute(
      'href',
      '/master-data-management/products/f-2-uuid',
    );
    expect(container.textContent).not.toContain('f-1-uuid');
  });
});

'use client';

/**
 * PO cross-check Phase 1: side-by-side viewer.
 *
 * Left side: PO attachment viewer (PDF/image).
 * Right side: request lines table with resolved prices.
 *
 * No automated matching yet - just a layout for manual comparison by marketing.
 * Phase 2 adds AI extraction and per-line discrepancy table.
 */

import { FileText } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { PdfViewer } from '@/components/common/PdfViewer';
import {
  fetchPortalAttachmentBytes,
  portalAttachmentDownloadUrl,
  type PortalAttachment,
} from '../lib/portal-client';
import { distinctName } from '@/lib/product-display';

/**
 * A PDF's bytes through the portal token route. The signed `url` is cross-origin with no
 * CORS headers, so the viewer cannot read it from a script.
 */
function portalPdfLoader(attachmentId: string) {
  return async () => {
    const response = await fetchPortalAttachmentBytes(portalAttachmentDownloadUrl(attachmentId));
    if (!response.ok) throw new Error('The file could not be read');
    return response.arrayBuffer();
  };
}

// Same idiom as AttachmentDropzone's isImageAttachment/isVideoAttachment: a
// row uploaded before the portal upload route carried content_type through
// (or any other legacy row with a NULL mime_type) still has to classify by
// filename extension, or it falls to the generic file row forever.
function isPdfAttachment(a: PortalAttachment): boolean {
  if (a.content_type === 'application/pdf') return true;
  return /\.pdf$/i.test(a.filename ?? '');
}

function isImageAttachment(a: PortalAttachment): boolean {
  if (a.content_type?.startsWith('image/')) return true;
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(a.filename ?? '');
}

interface LineWithPrice {
  id: string;
  code: string;
  name: string;
  line_type: string;
  quantity: number;
  list_price: number | null;
  sell_price: number | null;
  show_promo_price: boolean;
  marketing_price_override: number | null;
}

interface POCrossCheckViewerProps {
  attachments: PortalAttachment[];
  lines: LineWithPrice[];
}

function formatRM(amount: number): string {
  return `RM ${amount.toLocaleString('en-MY', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function POCrossCheckViewer({
  attachments,
  lines,
}: POCrossCheckViewerProps) {
  return (
    <Card>
      <CardHeader className="py-3 px-4">
        <CardTitle className="text-base">Sales Order Cross-Check</CardTitle>
      </CardHeader>
      <CardContent className="px-4 pb-4">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Left: Sales Order attachments */}
          <div className="space-y-2">
            <h4 className="text-sm font-medium text-muted-foreground">
              Sales Order
            </h4>
            {attachments.length === 0 ? (
              <div className="rounded-lg border-2 border-dashed border-muted p-8 text-center">
                <FileText className="size-8 mx-auto text-muted-foreground mb-2" />
                <p className="text-sm text-muted-foreground">
                  No sales order files attached to this request.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {attachments.map((att) => {
                  const filename = att.filename || 'Attachment';
                  const isPdf = isPdfAttachment(att);
                  const isImage = isImageAttachment(att);
                  return (
                    <div
                      key={att.link_id}
                      className="rounded-lg border overflow-hidden"
                    >
                      {att.url && isPdf ? (
                        <PdfViewer
                          url={att.url}
                          loadData={portalPdfLoader(att.attachment_id)}
                          documentKey={att.attachment_id}
                          fileName={filename}
                          title={filename}
                          className="h-[400px]"
                        />
                      ) : att.url && isImage ? (
                        <img
                          src={att.url}
                          alt={filename}
                          className="w-full max-h-[400px] object-contain bg-muted"
                        />
                      ) : (
                        <div className="flex items-center gap-2 p-3 bg-muted">
                          <FileText className="size-4 text-muted-foreground shrink-0" />
                          <span className="text-sm truncate" title={filename}>
                            {filename}
                          </span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Right: Request lines with prices */}
          <div className="space-y-2">
            <h4 className="text-sm font-medium text-muted-foreground">
              Request Lines
            </h4>
            {lines.length === 0 ? (
              <div className="rounded-lg border-2 border-dashed border-muted p-8 text-center">
                <p className="text-sm text-muted-foreground">
                  No lines in this request.
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto border rounded-lg">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-muted/50 text-left text-muted-foreground">
                      <th className="py-2 px-3 font-medium">Code</th>
                      <th className="py-2 px-3 font-medium">Product</th>
                      <th className="py-2 px-3 font-medium text-right">Qty</th>
                      <th className="py-2 px-3 font-medium text-right">
                        List Price
                      </th>
                      <th className="py-2 px-3 font-medium text-right">
                        Sell Price
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((line) => {
                      const effectiveSell =
                        line.marketing_price_override ?? line.sell_price;
                      return (
                        <tr
                          key={line.id}
                          className="border-b last:border-b-0"
                        >
                          <td className="py-2 px-3 font-mono text-xs">
                            {line.code}
                          </td>
                          <td className="py-2 px-3">
                            <span
                              className="truncate block max-w-[150px]"
                              title={distinctName(line.code, line.name) ?? undefined}
                            >
                              {distinctName(line.code, line.name)}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-right">
                            {line.quantity}
                          </td>
                          <td className="py-2 px-3 text-right">
                            {line.list_price != null
                              ? formatRM(line.list_price)
                              : '-'}
                          </td>
                          <td className="py-2 px-3 text-right">
                            {line.show_promo_price && effectiveSell != null ? (
                              <span className="text-green-700 font-medium">
                                {formatRM(effectiveSell)}
                              </span>
                            ) : (
                              '-'
                            )}
                            {line.marketing_price_override != null && (
                              <Badge
                                variant="secondary"
                                className="ml-1 text-xs"
                              >
                                Override
                              </Badge>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

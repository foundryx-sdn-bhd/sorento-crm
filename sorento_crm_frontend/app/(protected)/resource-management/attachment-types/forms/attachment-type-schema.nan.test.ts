import { describe, expect, it } from 'vitest';
import { AttachmentTypeSchema } from './attachment-type-schema';

const OK = { type_name: 'Cert', allowed_extensions: 'pdf', max_file_size_mb: 5 };

describe('AttachmentTypeSchema bad input (round 2, S-A)', () => {
  it.each(['max_count_per_entity', 'max_validity_months'])(
    '%s NaN reads as a number sentence, never raw zod text',
    (field) => {
      const result = AttachmentTypeSchema.safeParse({ ...OK, [field]: NaN });
      expect(result.success).toBe(false);
      if (result.success) return;
      const issue = result.error.issues.find((i) => i.path[0] === field)!;
      expect(issue.message).toMatch(/^Enter a whole number/);
    },
  );
});

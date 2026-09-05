import { readFileSync } from 'node:fs';
import { join } from 'node:path';

describe('web viewport (M9-04)', () => {
  it('asks Android Chrome to resize content when the keyboard opens', () => {
    const html = readFileSync(join(__dirname, '../../../public/index.html'), 'utf8');
    expect(html).toContain('interactive-widget=resizes-content');
  });
});

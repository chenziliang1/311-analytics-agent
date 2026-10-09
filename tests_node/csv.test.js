import { test } from 'node:test';
import assert from 'node:assert';
import { toCsv, withRowCount } from '../lib/chat/csv.ts';

test('plain cells are written as is, with CRLF line ends', () => {
  assert.strictEqual(toCsv(['line', 'n'], [['A', 3], ['B', 0.25]]), 'line,n\r\nA,3\r\nB,0.25\r\n');
});

test('commas, quotes and newlines are quoted; null is empty', () => {
  assert.strictEqual(
    toCsv(['note'], [['a,b'], ['say "hi"'], ['two\nlines'], [null]]),
    'note\r\n"a,b"\r\n"say ""hi"""\r\n"two\nlines"\r\n\r\n',
  );
});

test('row count placeholder', () => {
  assert.strictEqual(withRowCount('Download all {rows} rows', 1234), 'Download all 1,234 rows');
});

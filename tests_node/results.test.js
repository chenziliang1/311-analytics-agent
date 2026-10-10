import { test } from 'node:test';
import assert from 'node:assert';
import { cellText, resultRef, rowsShownLabel } from '../lib/chat/results.ts';

test('a paragraph that is one reference names its result', () => {
  assert.strictEqual(resultRef('[RESULT:r1]'), 'r1');
  assert.strictEqual(resultRef('  [RESULT:r12]\n'), 'r12');
});

test('a reference inside prose, or a malformed one, is not a table', () => {
  assert.strictEqual(resultRef('See [RESULT:r1] below.'), null);
  assert.strictEqual(resultRef('[RESULT:]'), null);
  assert.strictEqual(resultRef('[RESULT:r1] [RESULT:r2]'), null);
  assert.strictEqual(resultRef('[KB:metric/x]'), null);
});

test('cells show what the query returned', () => {
  assert.strictEqual(cellText(null), '');
  assert.strictEqual(cellText(undefined), '');
  assert.strictEqual(cellText(1204), '1204');
  assert.strictEqual(cellText(0.21390000001), '0.2139');
  assert.strictEqual(cellText('Plano'), 'Plano');
  assert.strictEqual(cellText(true), 'true');
});

test('the rows-shown line carries both counts', () => {
  assert.strictEqual(rowsShownLabel('Showing {shown} of {rows} rows', 100, 1204), 'Showing 100 of 1,204 rows');
});

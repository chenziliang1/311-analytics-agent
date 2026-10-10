import { test } from 'node:test';
import assert from 'node:assert';
import { citationId, linkCitations, splitCitationId } from '../lib/chat/citations.ts';

test('a marker becomes a kb: link the renderer turns into a chip', () => {
  assert.strictEqual(
    linkCitations('Rate 21.4%. How this was computed: closed over closed. [KB:metric/sla_breach_rate]'),
    'Rate 21.4%. How this was computed: closed over closed. [metric/sla_breach_rate](<kb:metric/sla_breach_rate>)',
  );
});

test('every marker is linked, once each, and other brackets are left alone', () => {
  const out = linkCitations('[KB:term/other-bucket] and [KB:tenant/austin_tx]; see [docs](https://x) and [not a marker].');
  assert.strictEqual(out, '[term/other-bucket](<kb:term/other-bucket>) and [tenant/austin_tx](<kb:tenant/austin_tx>); see [docs](https://x) and [not a marker].');
});

test('a malformed marker is not a citation', () => {
  assert.strictEqual(linkCitations('[KB:nonsense] [KB:metric/] [KB:Metric/x]'), '[KB:nonsense] [KB:metric/] [KB:Metric/x]');
});

test('the renderer recognises only kb: hrefs', () => {
  assert.strictEqual(citationId('kb:metric/sla_breach_rate'), 'metric/sla_breach_rate');
  assert.strictEqual(citationId('https://example.com'), null);
  assert.strictEqual(citationId('#ask:how many'), null);
  assert.strictEqual(citationId('kb:'), null);
  assert.strictEqual(citationId(undefined), null);
});

test('the chip shows the kind faintly and the key in full', () => {
  assert.deepStrictEqual(splitCitationId('metric/sla_breach_rate'), { kind: 'metric', key: 'sla_breach_rate' });
  assert.deepStrictEqual(splitCitationId('odd'), { kind: '', key: 'odd' });
});

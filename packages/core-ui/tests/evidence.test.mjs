import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import { EscalationBanner } from '../dist/index.js';

await i18n.use(initReactI18next).init({ lng: 'en', resources: { en: { translation: {
  core: { details: { okTitle: 'Evidence sufficient', warnTitle: 'Human review recommended',
    warnText: '{{reason}}', note: 'Informational', reasons: { grounded: 'Grounded', no_context: 'No context' } } },
} } }, interpolation: { escapeValue: false } });

function render(escalation, should_escalate = false) {
  return renderToStaticMarkup(React.createElement(EscalationBanner, {
    signal: { escalation, should_escalate, top_evidence: 0, connector_real: false },
  }));
}

test('absence of cloud escalation does not imply sufficient evidence', () => {
  const html = render('no_context');
  assert.match(html, /Human review recommended/);
  assert.doesNotMatch(html, /Evidence sufficient/);
});
test('grounded evidence retains the success banner', () => {
  assert.match(render('grounded'), /Evidence sufficient/);
});
test('unknown reasons never present a success banner', () => {
  assert.doesNotMatch(render('future_reason'), /Evidence sufficient/);
});

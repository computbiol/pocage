import assert from 'node:assert/strict';

import { buildSessionPath, parseSessionRoute } from './sessionRoute.ts';

assert.deepEqual(parseSessionRoute('/s/session-1'), {
  sessionId: 'session-1'
});

assert.equal(parseSessionRoute('/login'), null);
assert.equal(parseSessionRoute('/register'), null);
assert.equal(parseSessionRoute('/sign-in'), null);
assert.equal(parseSessionRoute('/sign-up'), null);
assert.equal(parseSessionRoute('/forgot-password'), null);
assert.equal(parseSessionRoute('/reset-password'), null);
assert.equal(parseSessionRoute('/verify'), null);
assert.equal(parseSessionRoute('/session-1'), null);

assert.equal(parseSessionRoute('/connectors/conn-1/sessions/session-1'), null);

assert.equal(buildSessionPath('session-1'), '/s/session-1');

console.log('sessionRoute tests passed');

# Security policy

## Supported versions

Security fixes are applied to the current default branch. Until the project
publishes versioned releases, older commits are not supported.

## Reporting a vulnerability

Use the repository's private GitHub Security Advisory reporting flow. Do not
open a public issue for credentials exposure, authentication bypasses, webhook
forgery, recording disclosure, SSRF, or another vulnerability that would put a
deployed simulator or real caller at risk.

Include:

- Affected revision and deployment mode
- Reproduction steps with secrets and personal data removed
- Expected and observed behavior
- Potential impact
- A suggested remediation, if known

Maintainers should acknowledge a report within seven days and coordinate
disclosure after a fix is available. This is a best-effort community project,
not a promise of a particular response or resolution time.

## Deployment boundary

The simulator can control real telephone numbers and process real calls.
Operators are responsible for:

- Keeping provider, LiveKit, and TTS credentials out of source
- Keeping the control API and console on loopback or private authenticated ingress
- Validating Twilio signatures and keeping `PSTN_ALLOW_UNSIGNED_WEBHOOKS=false`
- Putting the backend behind HTTPS, request limits, and network controls
- Leaving call recording disabled unless notice, consent, retention, access,
  and deletion obligations have been satisfied
- Never forwarding `/api/*` through public carrier ingress
- Configuring provider retention separately: simulator deletion removes local
  SQLite/artifacts but does not delete a Twilio `RecordingSid` or another
  provider-hosted copy
- Rotating credentials and restoring number webhooks after testing

Never expose a deployment populated with production recordings, customer
payloads, real destinations, or reusable access tokens as a public demo.

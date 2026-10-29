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

## CodeQL boundary triage (2026-10-29)

The Twilio `py/partial-ssrf` finding follows CallSid from the AMD and IVR
callbacks into `start_audit_recording`. Both callbacks call
`reject_invalid_call_sid` before that helper. Production CallSids must match
the canonical Twilio SID format; explicitly enabled local IDs are also
restricted to safe path characters. The request scheme and host are fixed.
Preserve that validation on every call path.

The call-artifact findings pass through `_owned_call_directory`, which rejects
traversal and symlink directories and checks the resolved parent against the
configured recordings root. `_owned_recording_path` further requires resolved
file containment. Those guards are security boundaries, not just string
prefix checks. Do not remove them to simplify a static-analysis trace.

The SIP regex uses `fullmatch`, and neither side of its single `@` separator can
consume that separator. Matching cannot restart at each repeated `sip:` prefix.
The polynomial-regex warning does not describe the anchored use here.

The public-audio validation API deliberately accepts an operator-specified
allowlist path and reads it as JSON. That path is not sandboxed to the corpus
folder. This finding remains an explicit limitation for trusted local/operator
use and must be resolved before exposing the API to untrusted callers. It has
not been dismissed as a false positive.

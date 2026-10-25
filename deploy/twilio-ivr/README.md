# Optional Twilio Serverless IVR

This deployment restores the standalone Twilio Functions workflow while
keeping the unified Python backend as the recommended runtime.

Use it when the IVR must be hosted entirely by Twilio. Its directory is an
`EXTENSIONS_JSON` environment value and is therefore independent of the
backend's SQLite directory. For a live shared directory, deploy the backend and
point the number at `/twilio/ivr` instead.

```bash
cp .env.example .env
npm ci
npm test
npm run license:check
npm run audit:ci
npm run start
npm run prompts       # optional; <Say> works without generated assets
npm run deploy
```

`twilio-run` is pinned by `package-lock.json` and is local deployment tooling;
it is not bundled into the deployed Function. The audit command fails on
critical advisories. Review lower-severity tooling advisories before running
it with access to a Twilio account.

Configure `EXTENSIONS_JSON` with four-digit keys. Destinations may be E.164
numbers or `sip:` URIs. The Function is protected, bounds retries and dial
timeouts, and never embeds real numbers or credentials in source.

After deployment, attach a number surgically:

```bash
python ../../apps/simulator-backend/scripts/twilio_number.py attach \
  --number +15550101000 \
  --voice-url https://your-service.twil.io/ivr
```

The command saves the number's previous webhook configuration under `results/`
before changing it. Use its `restore` subcommand with that backup to roll back.

## Runtime and tooling verification

The existing Node 24 deployment package contract is unchanged. Twilio Functions
supports Node 24 for v2 Services; Node 26 development tooling elsewhere in this
repository does not select or upgrade a deployed Twilio runtime. See the
[Twilio runtime guide](https://www.twilio.com/docs/serverless/functions-assets/node-upgrade).

The latest `twilio-run` 5.0.1 still declares Node 20 or 22 support, so installing
it on the package's existing Node 24 target emits an engine warning. Its
deployment command was not tested against an account. Compatible lockfile
updates reduced the development audit findings from 10 to 7 (2 moderate and
5 high); production dependencies have no findings. The remaining findings are
in `twilio-run`'s tooling graph, including clipboard/process, file detection,
and title handling. No major overrides or automatic audit downgrade were applied.
Review these findings before account-enabled use.

Node 24.22.1 passed Function syntax validation and isolated TwiML fixtures for
the four-digit menu, SIP lookup, dial timeout bound, retry limit, and busy
response. No calls, SMS, prompt generation, or deployments were performed.

### Reviewed ASF security backport

`npm ci` applies the exact upstream ASF forward-progress guard to
`file-type` 17.1.6, retaining its real version and existing API. Source:
[sindresorhus/file-type commit 319abf8](https://github.com/sindresorhus/file-type/commit/319abf871b50ba2fa221b4a7050059f1ae096f4f),
[GHSA-5v7r-6r5c-r473](https://github.com/advisories/GHSA-5v7r-6r5c-r473).
The patch manifest records original and patched SHA-256 hashes. Installation,
`npm test`, `npm start`, and `npm run deploy` reject unexpected package versions
or source bytes. If installation uses `--ignore-scripts`, run `npm run
test:security` before invoking the CLI directly.

The malformed 55-byte ASF fixture hangs in the original serverless MIME API
and terminates with the backport. Tests also validate PNG/MP3/JavaScript MIME
types and packaging the unchanged `/ivr` Function with protected access. An
actual local CLI server returned menu and lookup TwiML with a loopback-only
network guard, disposable configuration, and no provider credentials.

The audit still reports 7 entries because package versions are not relabeled:
2 moderate entries (`file-type` and its affected parent
`@twilio-labs/serverless-api`) cover the patched ASF root. The other 5 high
entries are unpatched: `twilio-run > title > clipboardy > execa > cross-spawn`
5.1.0, [GHSA-3xgq-45jj-v275](https://github.com/advisories/GHSA-3xgq-45jj-v275).
There is no supported patched cross-spawn 5.x release. Its upstream 6.x/7.x
security regex changed quoted Windows backslash/quote output when applied to
5.1.0 in a compatibility test, so that candidate was rejected. Replacing title
3 with 4 also changes its CommonJS API to ESM. Resolving this root needs an
upstream toolkit migration or a separately validated Windows-compatible fix.
The production audit remains zero. No audit exclusions or forced major
overrides hide these findings.

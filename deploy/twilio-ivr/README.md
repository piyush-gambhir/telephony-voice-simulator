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

# Generated prompt assets

Run `npm run prompts` from the parent `deploy/twilio-ivr` directory to create
the MP3 files and their provenance manifest. Generated audio is intentionally
not committed: its permitted use depends on the selected TTS provider, voice,
account, and terms.

The Twilio Function uses `<Say>` unless `USE_PROMPT_ASSETS=true`.

# Human role-play

Use `human_roleplay_bridge` when a tester should answer a voice agent directly,
without a recorded greeting or a DTMF gate. It is a PSTN-only scenario: Twilio
bridges the simulator line to `BRIDGE_NUMBER` using the existing dial transport.

## Setup

1. Configure a dedicated test handset as `BRIDGE_NUMBER` in the private backend
   environment, using E.164 format. Keep real destinations out of committed files.
2. Start the backend with `--with-pstn`, the restricted public ingress, and an HTTPS
   tunnel. Keep the control API on loopback. Existing public-audio approval and
   Twilio signature validation still apply.
3. Attach a dedicated simulator number to the local runtime from the console.
4. Set its default scenario to `human_roleplay_bridge` and its call selection to
   `Always default`. Use separate queued lines for voicemail and screening tests.
5. Call that simulator number from the test agent. The test handset rings, and
   the tester answers as the customer.

The number used by the agent as its outbound caller ID must be configured in
that agent platform separately. A working simulator number does not provision
an agent's caller ID.

## Recall cases

Pair each call with a test case containing the customer/VIN, recall campaigns,
parts and booking prerequisites, tester responses, and expected saved results.
Check the call transcript together with the customer, campaign, opportunity,
callback, opt-out, transfer, and appointment records relevant to the case.
A finished phone call alone does not establish a correct business outcome.

The direct bridge and the existing DTMF-to-human bridge support human testers.
They do not generate adaptive customer conversations. Scripted voicemail,
screening, and disconnected-number scenarios remain available on the queued
lines. The disconnected scenario plays the carrier-style failure message; it
is distinct from an actual network rejection, busy response, or unanswered call.

Record full calls only when the test setup calls for it. Isolated voicemail
capture is separate. Preserve scenario, simulator call ID, agent call ID,
transcript, observed records, and pass/fail evidence for each matrix case.

## Automated customer

The optional `/twilio/customer` webhook hands an allowlisted call to an ElevenLabs
Agents persona agent. The simulator keeps the Twilio number and the call record:
it checks the call, registers it with ElevenLabs
([register call](https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/register-call)),
and returns the TwiML ElevenLabs answers with. ElevenLabs then holds the whole
conversation over the call's media stream, with full-duplex turn-taking.

Setup:

1. Create one agent in ElevenLabs Agents for every persona. Pick its voice there, and
   set Voice mode to Realtime (Eleven Realtime v1 Mini) if your workspace has it;
   Custom mode works the same way.
2. Set `ELEVENLABS_API_KEY`, `ELEVENLABS_CUSTOMER_AGENT_ID`, a private
   `PSTN_CUSTOMER_PROFILES` JSON file mapping E.164 persona numbers to synthetic
   profile text, and `PSTN_CUSTOMER_ALLOWED_CALLERS`.
3. Run `telephony-voice-sim customer sync-agent` (or Push agent settings on the
   console Inbox page). It writes the persona prompt kept in this repository, the
   mu-law 8 kHz audio Twilio needs, and the four-minute call limit to the agent.
   Each call fills the prompt's `{{customer_profile}}` with that number's persona.

The route rejects unconfigured callers, destinations, accounts and invalid Twilio
signatures, allows at most 20 calls at once, and registers each CallSid once
(Twilio retries reuse the first registration). A registration failure rejects the
call and records the error type. Keep these routes on the restricted PSTN ingress.

Point only a dedicated test number's voice URL at `/twilio/customer`, with its
status callback at `/twilio/status`. The ordinary AMD attachment action restores
`/twilio/voice`, so do not reattach that line while using the customer agent.
The customer has no dialing or booking tools.

For a controlled customer callback, originate the call separately with the persona
number as `From`, the allowed dealership number as `To`, and the call's TwiML URL
set to `/twilio/customer`. The handler selects the persona from `From` for Twilio
outbound directions and preserves the actual direction and number pair in call
history. This webhook does not originate a call itself. Twilio documents these
directions in its [webhook request parameters](https://www.twilio.com/docs/voice/twiml#request-parameters).

Transcripts live in ElevenLabs. Each call is registered with its CallSid as the
conversation's user ID, and `telephony-voice-sim customer refresh CALL_ID` (or
opening the session on the Inbox page) copies the transcript into the call's
dialogue once ElevenLabs has processed it.

List the configured personas with `telephony-voice-sim customer profiles` and read
the saved dialogues with `telephony-voice-sim customer sessions [--limit N]`.
Goodbye, errors, call limits, provider completion, and deletion prevent further
generation. A retry of a generated turn reuses its response while the provider
call is active. Deleting the call also removes generated audio access; cached
audio expires after ten minutes even if no new call starts.

This mode sends the synthetic persona and test-call dialogue to OpenAI, and
reply text to ElevenLabs. Obtain authorization for those transfers before
using it. Use synthetic customer facts and controlled UAT agent calls only.
A generated reply passing preflight does not prove that the live conversation,
booking, campaign outcome, or opportunity update works.

## Controlled SMS inbox

Point each dedicated test number's SMS URL at `/twilio/sms`. Configure
`PSTN_SMS_NUMBERS` with the allowed recipient numbers, `PSTN_SMS_ALLOWED_SENDERS`
with the UAT agent's sender numbers, and `PSTN_SMS_DB` with a private SQLite path.
The handler validates the Twilio signature and account, rejects media and text
longer than 1,600 characters, and stores at most 1,000 messages. Duplicate
MessageSids are acknowledged without another insert. A full inbox returns 503.
It sends no automatic replies. Read the private inbox with `telephony-voice-sim sms list [--limit N]` (newest
first; empty until the first delivery) to verify the exact body, recipient,
sender and receive time before sending a controlled customer reply.
`telephony-voice-sim sms status` shows the allowlists and whether the inbox is
configured, without exposing credentials.

Twilio can assign different SIDs to the outgoing and incoming sides of a message
between two owned numbers. Correlate both IDs with the number pair, body and time.
Provider delivery alone does not prove that the application processed a reply or
saved a booking, opt-out, callback or recall outcome.

Call analysis may arrive after the provider's completion callback. Persisting
that analysis preserves the terminal status, end time and duration. Check the
provider record when reconciling historical calls affected by an earlier race.

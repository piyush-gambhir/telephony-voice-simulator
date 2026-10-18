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

The optional `/twilio/customer` webhook uses OpenAI to write a synthetic customer's
next reply and ElevenLabs to speak it. Twilio speech gathering supplies the other
speaker's words. Configure `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`,
`ELEVENLABS_VOICE_ID`, a private `PSTN_CUSTOMER_PROFILES` JSON file mapping E.164
callee numbers to synthetic personas, and `PSTN_CUSTOMER_ALLOWED_CALLERS`.
The routes reject unconfigured callers, destinations, accounts and invalid
Twilio signatures. Keep these routes on the restricted PSTN ingress.

Point only a dedicated test number's voice URL at `/twilio/customer`, with its
status callback at `/twilio/status`. The ordinary AMD attachment action restores
`/twilio/voice`, so do not reattach that line while using the customer agent.
The customer has no dialing or booking tools. It is limited to 16 turns and
four minutes, and saves dialogue in the simulator's call analysis. Replayed
turn webhooks reuse the response and audio. Failures end the call and retain
an error type rather than inventing a customer reply.

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
It sends no automatic replies. Read the private inbox to verify the exact body,
recipient, sender and receive time before sending a controlled customer reply.

Twilio can assign different SIDs to the outgoing and incoming sides of a message
between two owned numbers. Correlate both IDs with the number pair, body and time.
Provider delivery alone does not prove that the application processed a reply or
saved a booking, opt-out, callback or recall outcome.

Call analysis may arrive after the provider's completion callback. Persisting
that analysis preserves the terminal status, end time and duration. Check the
provider record when reconciling historical calls affected by an earlier race.

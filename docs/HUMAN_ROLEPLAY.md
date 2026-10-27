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
it checks the call, picks the persona, registers the call with ElevenLabs
([register call](https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/register-call)),
and returns the TwiML ElevenLabs answers with. ElevenLabs then holds the whole
conversation over the call's media stream, with full-duplex turn-taking.

### Personas

A persona is a YAML file: who the customer is, and what the agent under test must
do with them.

```yaml
id: busy-recall-owner
engine: chained            # or realtime
first_message: Hello?      # optional; also language and voice_id
profile: >-
  Name: Alex Morgan. 2021 Ford F-150 with an open camera recall. Weekday mornings only.
expectations:              # judged one at a time, about the agent under test
  - The agent offered at least one weekday-morning appointment slot.
  - The agent read back the booked appointment day and time before ending the call.
checks:                    # deterministic, on the agent under test's turns
  must_say: [recall]
  must_not_say: [guarantee]
  max_turns: 30
fidelity:                  # the persona's own self-test
  scenario: You are a service advisor... first offer Thursday at 3pm, then Tuesday at 9am.
  conditions:
    - The customer declined the Thursday 3pm afternoon slot.
```

Three personas ship with the package (`customer personas` lists them). Put private
ones in `PSTN_CUSTOMER_PERSONAS_DIR`; an id there overrides a bundled one. Write
expectations about "the agent", meaning the agent under test. Profiles must be
synthetic.

Each call copies the persona it ran with onto the call record, so editing a file
later never changes an old result.

### Setup

1. Create a persona agent in ElevenLabs Agents and set `ELEVENLABS_CUSTOMER_AGENT_ID`.
   Optionally create a second one for Realtime speech-to-speech and set
   `ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID`; personas with `engine: realtime` use it.
   Pick each agent's voice in ElevenLabs.
2. Set `ELEVENLABS_API_KEY`, `PSTN_CUSTOMER_ALLOWED_CALLERS`, and a private
   `PSTN_CUSTOMER_PROFILES` JSON file mapping each persona line (E.164) to its default
   persona id, for example `{"+15555550100": "busy-recall-owner"}`. Free profile text
   still works as a value, without expectations.
3. Run `telephony-voice-sim customer sync-agent` (or Push agent settings on the console
   Inbox page). It writes the persona prompt kept in this repository, the mu-law 8 kHz
   audio Twilio needs, the four-minute call limit, the model (`ELEVENLABS_PERSONA_LLM`,
   or the Realtime model), and permission for calls to override the first message,
   language and voice. Each call fills the prompt's `{{customer_profile}}`.

The route rejects unconfigured callers, destinations, accounts and invalid Twilio
signatures, allows at most 20 calls at once, and registers each CallSid once
(Twilio retries reuse the first registration). A registration or persona failure
rejects the call and records the error type. Keep these routes on the restricted
PSTN ingress.

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

### Testing an agent change

This is the loop for a person or a coding agent checking a change to the agent under
test. Every command prints JSON; `grade` and `check-persona` exit 2 on failure.

```bash
telephony-voice-sim customer queue +15555550100 --persona opt-out-customer --label my-branch
# trigger the agent under test to call +15555550100 (or originate the callback)
telephony-voice-sim customer sessions --limit 1
telephony-voice-sim customer grade CALL_ID --wait
```

`queue` sets the persona for that line's next call only; later calls fall back to the
line's default. Pass `--file persona.yaml` or `--inline '{...}'` instead of `--persona`
to test a persona that is not in the catalog. `customer queued` and `customer unqueue`
show and remove waiting entries. A persona is consumed when a call claims it, even if
registration then fails.

`grade` waits for ElevenLabs to finish the transcript (with `--wait`), runs the
deterministic checks, then one ElevenLabs judge test per expectation: the transcript is
replayed as history and `ELEVENLABS_EVALUATION_MODEL` judges that one statement. The
verdicts and the evaluator's reasons are stored on the call and shown on the Inbox
page. The call passes only when every check and expectation passes. Judge tests are
reusable slots named `TVS judge slot N` in the ElevenLabs workspace, rewritten for each
grading, so one simulator process grades one call at a time.

Before relying on a new persona, run `customer check-persona --persona ID [--repeat N]`.
ElevenLabs plays the dealership from the persona's `fidelity.scenario` against the
persona agent and judges whether the customer stayed in character, never invented
appointments, and met the persona's own conditions.

Rough ElevenLabs cost at list prices: a persona check run is about 300 credits, and
grading is about 100 credits per expectation (1 credit is about $0.0001), on top of the
call minutes.

### Transcripts

Transcripts live in ElevenLabs. Each call is registered with its CallSid as the
conversation's user ID. `telephony-voice-sim customer refresh CALL_ID` (or opening
the session on the Inbox page) copies the transcript into the call's dialogue once
ElevenLabs has processed it, without the Realtime model's expression tags such as
`[friendly]`.

To receive transcripts without polling, add a post-call transcription webhook in the
ElevenLabs workspace pointing at `/elevenlabs/post-call` on the PSTN ingress, and set
`ELEVENLABS_WEBHOOK_SECRET` to its secret. The route verifies the signature, refuses
events older than 30 minutes, stores the transcript, and grades the call in the
background when its persona has expectations or checks. A grading failure is saved on
the call as `grade_error`.

List the persona lines with `telephony-voice-sim customer profiles` and read the saved
dialogues and grades with `telephony-voice-sim customer sessions [--limit N]`.
Deleting a call removes the simulator's copy only; delete the conversation in
ElevenLabs separately if its retention matters.

This mode sends the persona, the full call audio from both sides, and the call's
numbers to ElevenLabs, which transcribes the call and keeps the conversation and its
audio under the workspace's retention settings. Grading and persona checks also send
the transcript and the persona to ElevenLabs, whose evaluator model processes them.
Obtain authorization for those transfers before using it. Use synthetic customer facts
and controlled UAT agent calls only. A passing grade checks the conversation; it does
not prove that the booking, campaign outcome, or opportunity update was saved.

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

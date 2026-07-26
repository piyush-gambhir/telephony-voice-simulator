"use strict";

const DEFAULT_PROMPTS = {
  welcome: "Welcome. Enter the four digit extension you want to reach.",
  noInput: "No extension was entered. Please try again.",
  notFound: "That extension is not available. Please try again.",
  connecting: "Connecting your call now.",
  busy: "That extension is busy. Please call again later.",
  noAnswer: "Nobody answered that extension. Please call again later.",
  failed: "The call could not be completed. Please try again later.",
  goodbye: "We could not complete your request. Goodbye.",
};

function directoryFrom(context) {
  let parsed;
  try {
    parsed = JSON.parse(context.EXTENSIONS_JSON || "{}");
  } catch (_error) {
    throw new Error("EXTENSIONS_JSON must be valid JSON");
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("EXTENSIONS_JSON must be an object keyed by extension");
  }
  for (const [extension, entry] of Object.entries(parsed)) {
    if (!/^\d{4}$/.test(extension)) {
      throw new Error(`EXTENSIONS_JSON key ${extension} must contain exactly 4 digits`);
    }
    const destination = String((entry || {}).destination || "");
    if (
      !/^\+[1-9]\d{7,14}$/.test(destination) &&
      !/^sips?:[^\s@]+@[^\s@]+$/i.test(destination)
    ) {
      throw new Error(
        `EXTENSIONS_JSON destination for ${extension} must be E.164 or sip:/sips:`,
      );
    }
  }
  return parsed;
}

function prompt(response, context, key, text) {
  if (String(context.USE_PROMPT_ASSETS).toLowerCase() === "true") {
    const filename = key.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
    const protocol = String(context.DOMAIN_NAME || "").startsWith("localhost")
      ? "http"
      : "https";
    response.play(`${protocol}://${context.DOMAIN_NAME}/${filename}.mp3`);
    return;
  }
  response.say(
    {
      voice: context.IVR_VOICE || "Polly.Joanna",
      language: context.IVR_LANGUAGE || "en-US",
    },
    text,
  );
}

function redirectToMenu(response, attempt) {
  response.redirect({ method: "POST" }, `/ivr?attempt=${attempt}`);
}

exports.handler = function handler(context, event, callback) {
  const response = new Twilio.twiml.VoiceResponse();
  const attempt = Math.max(1, Number.parseInt(event.attempt || "1", 10) || 1);
  const maximumAttempts = Math.max(
    1,
    Math.min(10, Number.parseInt(context.IVR_MAX_ATTEMPTS || "3", 10) || 3),
  );

  if (event.stage === "dial") {
    const status = String(event.DialCallStatus || "failed").toLowerCase();
    if (status === "busy") {
      prompt(response, context, "busy", DEFAULT_PROMPTS.busy);
    } else if (status === "no-answer") {
      prompt(response, context, "noAnswer", DEFAULT_PROMPTS.noAnswer);
    } else if (!["completed", "answered"].includes(status)) {
      prompt(response, context, "failed", DEFAULT_PROMPTS.failed);
    }
    response.hangup();
    return callback(null, response);
  }

  if (event.stage === "lookup") {
    const digits = String(event.Digits || "").trim();
    if (!/^\d{4}$/.test(digits)) {
      prompt(response, context, "noInput", DEFAULT_PROMPTS.noInput);
      if (attempt >= maximumAttempts) {
        prompt(response, context, "goodbye", DEFAULT_PROMPTS.goodbye);
        response.hangup();
      } else {
        redirectToMenu(response, attempt + 1);
      }
      return callback(null, response);
    }

    const entry = directoryFrom(context)[digits];
    if (!entry || !entry.destination) {
      prompt(response, context, "notFound", DEFAULT_PROMPTS.notFound);
      if (attempt >= maximumAttempts) {
        prompt(response, context, "goodbye", DEFAULT_PROMPTS.goodbye);
        response.hangup();
      } else {
        redirectToMenu(response, attempt + 1);
      }
      return callback(null, response);
    }

    prompt(response, context, "connecting", DEFAULT_PROMPTS.connecting);
    const dialOptions = {
      answerOnBridge: true,
      timeout: Math.max(5, Math.min(120, Number(entry.timeout) || 25)),
      action: "/ivr?stage=dial",
      method: "POST",
    };
    if (context.IVR_CALLER_ID) {
      dialOptions.callerId = context.IVR_CALLER_ID;
    }
    const dial = response.dial(dialOptions);
    if (/^sips?:/i.test(String(entry.destination))) {
      dial.sip(String(entry.destination));
    } else {
      dial.number(String(entry.destination));
    }
    return callback(null, response);
  }

  const gather = response.gather({
    input: "dtmf",
    numDigits: 4,
    timeout: 8,
    actionOnEmptyResult: true,
    action: `/ivr?stage=lookup&attempt=${attempt}`,
    method: "POST",
  });
  prompt(gather, context, "welcome", DEFAULT_PROMPTS.welcome);
  return callback(null, response);
};

const ACRONYMS: Record<string, string> = {
  amd: "AMD",
  api: "API",
  cdr: "CDR",
  cli: "CLI",
  crm: "CRM",
  dtmf: "DTMF",
  ivr: "IVR",
  pbx: "PBX",
  pstn: "PSTN",
  sip: "SIP",
  stt: "STT",
  tts: "TTS",
  ui: "UI",
  ux: "UX",
};

export function formatAcronyms(value: string): string {
  return value.replace(/\b[a-z]+\b/gi, (word) => ACRONYMS[word.toLowerCase()] ?? word);
}

export function identifierLabel(value: string): string {
  return formatAcronyms(
    value.replace(/\.(wav|mp3|m4a|ogg)$/i, "").replaceAll("-", " ").replaceAll("_", " ")
  );
}

#pragma once

#include <Arduino.h>

// One SMS through the SIM800L, in text mode with raw AT (docs/designs/grip-alarm-contracts.md
// C5): AT+CMGF=1, AT+CSCS="GSM", AT+CMGS="<phone>", the text after the '>' prompt, Ctrl-Z, then
// "+CMGS:" and OK (up to SMS_SEND_TIMEOUT_MS). Which SMS goes when is sms_policy.h's decision.
//
// Only the sender task calls this (the only modem owner), between report attempts, never in the
// middle of an HTTP request. It needs the modem registered on the GSM network (the caller checks
// modemRegistrationCached() first); no APN or data bearer is involved. The reads keep a late
// "+HTTPACTION:" for gprs_http (modem.h), so an SMS sent while an earlier timed-out request is
// still pending doesn't swallow its result.

// True once the modem confirmed it sent the message ("+CMGS:"). `phone` must be E.164 (it comes
// from device_config's check) and `text` plain GSM-safe ASCII (composeSmsText). On failure,
// `error` says which step failed, for the log.
bool smsSendText(const String& phone, const String& text, String& error);

#include "backend_api.h"

#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include "config.h"
#include "credentials.h"
#include "gprs_http.h"
#include "modem.h"
#include "root_ca.h"
#include "transport_policy.h"
#include "wifi_client.h"

namespace {

const char kReportPath[] = "/api/devices/me/report";

TransportSelector transport(GPRS_HOLD_OFF_MS);
const AttemptTimeouts timeouts;

// One of each, reused: only the sender task sends, one attempt at a time. Kept across attempts so
// HTTPClient's keep-alive reuses one TLS session instead of paying a full handshake per report.
WiFiClient plainClient;
WiFiClientSecure tlsClient;
bool tlsConfigured = false;

// The sender task's copy of the credentials, refreshed when the Pi sends new ones.
DeviceCredentials creds;
uint32_t credsGeneration = 0;

// The current attempt's composer and deadline, for composeNow() (gprs_http takes a plain function
// pointer). The sender task only: one attempt at a time.
ReportComposer activeComposer = nullptr;
const Deadline* activeDeadline = nullptr;
const char* activeVia = "none";

// Composes the body now and logs it: "[report] -> POST ... via gprs (812 B, 4.2 s of 8.0 s left)".
String composeNow() {
  std::string body = activeComposer ? activeComposer(millis()) : std::string("{\"points\":[]}");
  Serial.print("[report] -> POST ");
  Serial.print(kReportPath);
  Serial.print(" via ");
  Serial.print(activeVia);
  Serial.print(" (");
  Serial.print((unsigned)body.size());
  Serial.print(" B");
  if (activeDeadline) {
    Serial.print(", ");
    Serial.print(activeDeadline->remaining(millis()) / 1000.0, 1);
    Serial.print(" s of ");
    Serial.print(activeDeadline->budgetMs / 1000.0, 1);
    Serial.print(" s left");
  }
  Serial.println(")");
  return String(body.c_str());
}

// Prints why there's no network, at most every NET_STATUS_LOG_INTERVAL_MS.
void logNoNetwork(const String& why) {
  static bool logged = false;
  static unsigned long lastAt = 0;
  if (logged && millis() - lastAt < NET_STATUS_LOG_INTERVAL_MS) return;
  logged = true;
  lastAt = millis();
  Serial.print("[net] No network: ");
  Serial.println(why);
}

ReportSend sendOverWifi(const String& url, const Deadline& deadline) {
  ReportSend r;
  r.via = "wifi";
  uint32_t left = deadline.remaining(millis());
  HTTPClient http;
  http.setReuse(true);
  bool ok;
  if (url.startsWith("https://")) {
    // AWS (CloudFront) only serves /api/* over HTTPS. The CA is pinned, not setInsecure(): the
    // device key travels in this request's headers.
    if (!tlsConfigured) {
      tlsClient.setCACert(BACKEND_ROOT_CA);
      tlsConfigured = true;
    }
    tlsClient.setHandshakeTimeout((left + 999) / 1000);  // seconds
    ok = http.begin(tlsClient, url);
  } else {
    ok = http.begin(plainClient, url);  // LAN bench backend
  }
  if (!ok) {
    Serial.println("[report] Couldn't start the request -- bad backend URL?");
    r.reason = "transport_error";
    return r;
  }
  // HTTPClient has no whole-request deadline: connecting and reading the answer each get what is
  // left of the attempt, so one attempt is bounded by the deadline per phase, not overall.
  http.setConnectTimeout((int32_t)left);
  http.setTimeout((uint16_t)(left < 65535 ? left : 65535));
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Api-Key", creds.apiKey);

  String body = composeNow();  // as late as HTTPClient allows: the ages count every step above
  r.status = http.POST(body);
  if (r.status > 0) {
    r.body = http.getString();
  } else {
    r.reason = r.status == HTTPC_ERROR_READ_TIMEOUT || deadline.expired(millis())
                   ? "timeout"
                   : "transport_error";
  }
  http.end();
  return r;
}

}  // namespace

ReportSend backendPostReport(ReportComposer compose, bool retry, bool (*preempt)()) {
  ReportSend r;
  credentialsCopyIfChanged(creds, credsGeneration);
  if (!creds.valid()) {
    r.reason = "no_credentials";
    return r;
  }

  // The attempt starts here, so its deadline also bounds the mobile registration check.
  uint32_t startedAt = millis();
  bool wifiUp = wifiIsConnected();
  // Mobile data first (unless it is on hold after a transport failure and WiFi is up), then WiFi.
  bool tryGprs = creds.useGprs() && transport.pick(startedAt, wifiUp) == Transport::Gprs;
  Deadline deadline(startedAt,
                    attemptTimeoutMs(timeouts, tryGprs ? Transport::Gprs : Transport::Wifi, retry,
                                     tryGprs && gprsNeedsSetup()));
  bool useGprs = false;
  int stat = -2;  // not checked
  if (tryGprs) {
    modemSetDeadline(&deadline);  // AT+CREG? (when the cached state is stale) waits no longer
    stat = modemRegistrationCached(MODEM_REG_CACHE_MS);
    modemSetDeadline(nullptr);
    useGprs = modemStatRegistered(stat);
  }
  if (!useGprs && !wifiUp) {
    r.reason = "no_network";
    if (!creds.useGprs()) {
      logNoNetwork("no WiFi, and no mobile data APN from the Pi (ARGUS_GPRS_APN)");
    } else if (stat == -2) {
      logNoNetwork("mobile data failed recently (on hold for GPRS_HOLD_OFF_MS) and no WiFi");
    } else {
      logNoNetwork(String("no WiFi, and mobile data unusable: ") + modemRegistrationText(stat));
    }
    return r;
  }
  if (tryGprs && !useGprs) {
    // Not registered: mobile data is failing too, so the WiFi fallback starts (backendPoll), and
    // WiFi carries this attempt if it is already up, with WiFi's budget from the same start (the
    // registration check was its first step).
    transport.report(Transport::Gprs, false, millis());
    deadline = Deadline(startedAt, attemptTimeoutMs(timeouts, Transport::Wifi, retry, false));
  }
  if (preempt && preempt()) {
    r.preempted = true;
    return r;
  }

  Transport via = useGprs ? Transport::Gprs : Transport::Wifi;
  String url = creds.backendUrl + kReportPath;
  activeComposer = compose;
  activeDeadline = &deadline;
  activeVia = transportName(via);

  if (useGprs) {
    GprsResult g = gprsPostJson(url, creds, composeNow, deadline, preempt);
    r.via = "gprs";
    switch (g.end) {
      case GprsEnd::Response:
        r.status = g.status;
        r.body = g.body;
        transport.report(Transport::Gprs, true, millis());
        break;
      case GprsEnd::Preempted:
        r.preempted = true;  // says nothing about the network
        break;
      case GprsEnd::ModemBusy:
        r.reason = "modem_busy";  // nor does a modem still busy with an earlier request
        break;
      case GprsEnd::Timeout:
        r.reason = "timeout";
        transport.report(Transport::Gprs, false, millis());
        break;
      case GprsEnd::TransportError:
        r.reason = "transport_error";
        transport.report(Transport::Gprs, false, millis());
        break;
    }
  } else {
    r = sendOverWifi(url, deadline);
  }
  activeComposer = nullptr;
  activeDeadline = nullptr;

  if (r.status > 0) {
    Serial.print("[report] <- ");
    Serial.print(r.status);
    Serial.print(" ");
    Serial.println(r.body.substring(0, 200));
  }
  return r;
}

void backendPoll() {
  credentialsCopyIfChanged(creds, credsGeneration);
  // WiFi is only the fallback: on when the Pi gives no mobile data APN (then it's the only path),
  // or while mobile data is failing; off again once a mobile request works.
  bool wifiWanted = !creds.useGprs() || transport.gprsFailing();
  wifiEnsureConnected(wifiWanted);
  gprsPoll();
}

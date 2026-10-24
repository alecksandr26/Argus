// REFERENCE ONLY -- the user's bench sketch (2026-10-23) for the steering-wheel grip sensors,
// buzzer and SMS, kept as given except the phone number (a placeholder here). It is NOT part of
// the firmware build: the firmware implements this logic in src/esp32_argus/ per
// docs/designs/grip-alarm-contracts.md (raw AT instead of TinyGsm, 3 s instead of 1 s, the phone
// from the Pi's CONFIG, an SMS even without a GPS fix).
#define TINY_GSM_MODEM_SIM800

#include <Arduino.h>
#include <TinyGsmClient.h>
#include <TinyGPSPlus.h>

#define SerialMon Serial
#define SerialAT Serial1

#define MODEM_RST       5
#define MODEM_PWKEY     4
#define MODEM_POWER_ON  23
#define MODEM_TX        27
#define MODEM_RX        26

TinyGsm modem(SerialAT);
TinyGsmClient client(modem);

#define GPS_RX 32
#define GPS_TX 33
#define GPS_BAUD 9600

HardwareSerial GPSserial(2);
TinyGPSPlus gps;

// SENSORES DE PRESION RFP602 (volante)
const int sensor1 = 34;
const int sensor2 = 35;
const int sensor3 = 36;
const int sensor4 = 39;

const int buzzer = 13;                        // sonido de alerta
const int UMBRAL = 500;                       // Ajustar segun las lecturas reales
const unsigned long TIEMPO_ALERTA = 1000;     // Tiempo sin presion antes de activar la alerta
const unsigned long INTERVALO_LECTURA = 100;  // Intervalo de lectura
const unsigned long INTERVALO_SMS = 60000;    // Intervalo minimo entre SMS

const char numeroDestino[] = "+520000000000";  // placeholder (the bench used a real number)

unsigned long inicioSinPresion = 0;
unsigned long ultimaLectura = 0;
unsigned long ultimoSMS = 0;
bool contando = false;
bool alarmaActiva = false;
bool smsEnviadoEnAlerta = false;

bool enviarSMS(const char* numero, const String& mensaje) {
  if (!modem.isNetworkConnected()) {
    SerialMon.println("Sin red GSM. No se pudo enviar SMS.");
    return false;
  }
  SerialMon.println("Enviando SMS...");
  bool resultado = modem.sendSMS(numero, mensaje);
  SerialMon.println(resultado ? "SMS enviado correctamente" : "ERROR al enviar SMS");
  return resultado;
}

void actualizarGPS() {
  while (GPSserial.available()) {
    gps.encode(GPSserial.read());
  }
}

String crearLinkMaps() {
  String link = "https://www.google.com/maps?q=";
  link += String(gps.location.lat(), 6);
  link += ",";
  link += String(gps.location.lng(), 6);
  return link;
}

void enviarAlertaGPS() {
  SerialMon.println("Preparando alerta de emergencia");
  if (!gps.location.isValid()) {  // Evitar enviar SMS sin coordenadas validas
    SerialMon.println("GPS sin posicion valida");
    return;
  }
  String mensaje = "ALERTA: No se detecta presion en el volante.\n";
  mensaje += "Ubicacion:\n";
  mensaje += crearLinkMaps();
  SerialMon.println(mensaje);
  if (enviarSMS(numeroDestino, mensaje)) {
    ultimoSMS = millis();
    smsEnviadoEnAlerta = true;
  }
}

void setup() {
  SerialMon.begin(115200);
  delay(1000);

  analogReadResolution(12);
  pinMode(buzzer, OUTPUT);
  digitalWrite(buzzer, LOW);

  GPSserial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX, GPS_TX);

  pinMode(MODEM_PWKEY, OUTPUT);
  pinMode(MODEM_RST, OUTPUT);
  pinMode(MODEM_POWER_ON, OUTPUT);
  digitalWrite(MODEM_PWKEY, LOW);
  digitalWrite(MODEM_RST, HIGH);
  digitalWrite(MODEM_POWER_ON, HIGH);

  SerialAT.begin(9600, SERIAL_8N1, MODEM_RX, MODEM_TX);
  delay(3000);
  SerialMon.println("Inicializando SIM800H...");
  modem.restart();
  SerialMon.print("Modem: ");
  SerialMon.println(modem.getModemInfo());

  SerialMon.println("Esperando red GSM...");
  if (!modem.waitForNetwork(240000L)) {
    SerialMon.println("No se encontro red GSM");
    SerialMon.println("Sensores seguiran funcionando");
    return;
  }
  SerialMon.println("Red GSM conectada");
  SerialMon.print("Calidad de senal: ");
  SerialMon.println(modem.getSignalQuality());
  SerialMon.println("Sistema listo");
}

void loop() {
  actualizarGPS();  // Mantener actualizado el GPS

  unsigned long ahora = millis();
  if (ahora - ultimaLectura < INTERVALO_LECTURA) {
    return;
  }
  ultimaLectura = ahora;

  int lectura1 = analogRead(sensor1);
  int lectura2 = analogRead(sensor2);
  int lectura3 = analogRead(sensor3);
  int lectura4 = analogRead(sensor4);

  SerialMon.print("S1: ");
  SerialMon.print(lectura1);
  SerialMon.print(" | S2: ");
  SerialMon.print(lectura2);
  SerialMon.print(" | S3: ");
  SerialMon.print(lectura3);
  SerialMon.print(" | S4: ");
  SerialMon.println(lectura4);

  // Detectar presion en al menos un sensor
  bool hayPresion =
    (lectura1 > UMBRAL) || (lectura2 > UMBRAL) || (lectura3 > UMBRAL) || (lectura4 > UMBRAL);

  if (hayPresion) {  // Si hay presion, cancelar alarma
    contando = false;
    alarmaActiva = false;
    smsEnviadoEnAlerta = false;
    digitalWrite(buzzer, LOW);
    SerialMon.println("OK: PRESION DETECTADA");
    return;
  }

  if (!contando) {  // Ningun sensor detecta presion
    inicioSinPresion = ahora;
    contando = true;
    SerialMon.println("Sin presion: iniciando temporizador");
  }

  if (ahora - inicioSinPresion >= TIEMPO_ALERTA) {  // Activar alarma despues de 1 segundo
    digitalWrite(buzzer, HIGH);
    if (!alarmaActiva) {
      alarmaActiva = true;
      SerialMon.println("ALERTA: VOLANTE SIN PRESION");
    }
    if (!smsEnviadoEnAlerta) {  // Enviar SMS una vez por cada episodio de alerta
      if (gps.location.isValid()) {
        enviarAlertaGPS();
      } else {
        SerialMon.println("Esperando ubicacion GPS para alerta");
      }
    }
  }
}

  
#define TINY_GSM_MODEM_SIM800

#include <Arduino.h>
#include <TinyGsmClient.h>
#include <TinyGPSPlus.h>

// =====================================================
// SIM800L
// =====================================================

#define SerialMon Serial
#define SerialAT Serial1

#define MODEM_RST 5
#define MODEM_PWKEY 4
#define MODEM_POWER_ON 23

#define MODEM_TX 27
#define MODEM_RX 26

TinyGsm modem(SerialAT);
TinyGsmClient client(modem);

// =====================================================
// NEO-6M GPS
// =====================================================

#define GPS_RX 32
#define GPS_TX 33
#define GPS_BAUD 9600

HardwareSerial GPSserial(2);
TinyGPSPlus gps;

// =====================================================
// SMS
// =====================================================

const char numeroDestino[] = "+523314033063";

// =====================================================
// ENVIAR SMS
// =====================================================

bool enviarSMS(const char* numero, const String& mensaje) {

  if (!modem.isNetworkConnected()) {
    SerialMon.println("Sin red GSM");
    return false;
  }

  SerialMon.println("Enviando SMS...");

  bool resultado = modem.sendSMS(numero, mensaje);

  if (resultado) {
    SerialMon.println("SMS enviado correctamente");
  } 
  else {
    SerialMon.println("ERROR al enviar SMS");
  }

  return resultado;
}

// =====================================================
// ESPERAR GPS
// =====================================================

bool esperarGPS() {

  SerialMon.println();
  SerialMon.println("Esperando señal GPS...");

  unsigned long inicio = millis();

  // Esperar máximo 2 minutos
  while (millis() - inicio < 120000) {

    while (GPSserial.available()) {

      char dato = GPSserial.read();

      gps.encode(dato);
    }

    // Verificar si ya tenemos posición
    if (gps.location.isValid()) {

      SerialMon.println();
      SerialMon.println("GPS ENCONTRADO");

      SerialMon.print("Satélites: ");
      SerialMon.println(gps.satellites.value());

      SerialMon.print("Latitud: ");
      SerialMon.println(gps.location.lat(), 6);

      SerialMon.print("Longitud: ");
      SerialMon.println(gps.location.lng(), 6);

      SerialMon.print("Altitud: ");
      SerialMon.print(gps.altitude.meters());
      SerialMon.println(" m");

      return true;
    }

    // Mostrar progreso cada 5 segundos
    static unsigned long ultimoMensaje = 0;

    if (millis() - ultimoMensaje > 5000) {

      SerialMon.print("Buscando GPS... Satélites: ");

      if (gps.satellites.isValid())
        SerialMon.println(gps.satellites.value());
      else
        SerialMon.println("0");

      ultimoMensaje = millis();
    }

    delay(10);
  }

  SerialMon.println("No se encontró posición GPS");

  return false;
}

// =====================================================
// CREAR LINK GOOGLE MAPS
// =====================================================

String crearLinkMaps() {

  double latitud = gps.location.lat();
  double longitud = gps.location.lng();

  String link = "https://www.google.com/maps?q=";

  link += String(latitud, 6);
  link += ",";
  link += String(longitud, 6);

  return link;
}

// =====================================================
// SETUP
// =====================================================

void setup() {

  SerialMon.begin(115200);

  delay(1000);

  SerialMon.println();
  SerialMon.println("================================");
  SerialMon.println(" ESP32 + NEO-6M + SIM800L");
  SerialMon.println("================================");

  // ===================================================
  // GPS
  // ===================================================

  GPSserial.begin(
    GPS_BAUD,
    SERIAL_8N1,
    GPS_RX,
    GPS_TX
  );

  SerialMon.println("GPS iniciado");

  // ===================================================
  // SIM800L
  // ===================================================

  pinMode(MODEM_PWKEY, OUTPUT);
  pinMode(MODEM_RST, OUTPUT);
  pinMode(MODEM_POWER_ON, OUTPUT);

  digitalWrite(MODEM_PWKEY, LOW);
  digitalWrite(MODEM_RST, HIGH);
  digitalWrite(MODEM_POWER_ON, HIGH);

  SerialAT.begin(
    9600,
    SERIAL_8N1,
    MODEM_RX,
    MODEM_TX
  );

  delay(3000);

  SerialMon.println("Inicializando SIM800L...");

  modem.restart();

  String modemInfo = modem.getModemInfo();

  SerialMon.print("Modem: ");
  SerialMon.println(modemInfo);

  // ===================================================
  // RED GSM
  // ===================================================

  SerialMon.println("Esperando red GSM...");

  if (!modem.waitForNetwork(240000L)) {

    SerialMon.println("No se encontró red GSM");

    return;
  }

  SerialMon.println("Red GSM conectada");

  // ===================================================
  // CALIDAD DE SEÑAL
  // ===================================================

  int signalQuality = modem.getSignalQuality();

  SerialMon.print("Señal GSM: ");
  SerialMon.println(signalQuality);

  // ===================================================
  // ESPERAR GPS
  // ===================================================

  bool gpsOK = esperarGPS();

  if (!gpsOK) {

    SerialMon.println();
    SerialMon.println("No se enviará SMS porque");
    SerialMon.println("el GPS no tiene posición.");

    return;
  }

  // ===================================================
  // CREAR MENSAJE
  // ===================================================

  String mensaje = "EMERGENCIA\n";

  mensaje += "Ubicacion:\n";

  mensaje += crearLinkMaps();

  // ===================================================
  // MOSTRAR MENSAJE
  // ===================================================

  SerialMon.println();
  SerialMon.println("MENSAJE:");
  SerialMon.println("----------------------------");
  SerialMon.println(mensaje);
  SerialMon.println("----------------------------");

  // ===================================================
  // ENVIAR SMS
  // ===================================================

  enviarSMS(
    numeroDestino,
    mensaje
  );
}

// =====================================================
// LOOP
// =====================================================

void loop() {

  // Mantener leyendo GPS
  while (GPSserial.available()) {

    char dato = GPSserial.read();

    gps.encode(dato);
  }

  delay(100);
}


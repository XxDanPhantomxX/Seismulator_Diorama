// =====================================================================
//  ESP32 Seismic Dashboard — Segunda ESP32 (Receptor / Visualizador)
//  Hardware : ESP32-2432S028  |  TFT 2.8" ILI9341 320x240
//  Library  : LovyanGFX + ArduinoJson + WiFi
//  Red      : WiFi STA → "ESP32-Control"  |  UDP Multicast 224.1.1.10:5005
// =====================================================================
//  MODO AUTO: Sin botones — rota entre pantallas cada 10 segundos
//  DEMO_MODE: true = datos simulados sin ESP32 principal
// =====================================================================

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

// ── Configuracion ─────────────────────────────────────────────────────
#define WIFI_SSID        "ESP32-Control"
#define WIFI_PASS        "12345678"
#define MCAST_IP         "224.1.1.10"
#define MCAST_PORT       5005
#define UDP_BUFFER       512
#define AUTO_ROTATE      true
#define AUTO_ROTATE_MS   10000
#define DEMO_MODE        false

// ── Grafica historica ─────────────────────────────────────────────────
#define GRAPH_POINTS     80    // puntos almacenados por canal
#define GRAPH_INTERVAL   250   // ms entre muestras de la grafica

// ── Pantallas ─────────────────────────────────────────────────────────
enum Screen { SCR_STATUS = 0, SCR_SENSOR = 1 };

// =====================================================================
//  Clase LGFX
// =====================================================================
class LGFX : public lgfx::LGFX_Device
{
  lgfx::Panel_ILI9341  _panel_instance;
  lgfx::Bus_SPI        _bus_instance;
  lgfx::Light_PWM      _light_instance;
public:
  LGFX(void)
  {
    { auto cfg = _bus_instance.config();
      cfg.spi_host = VSPI_HOST; cfg.spi_mode = 0;
      cfg.freq_write = 40000000; cfg.freq_read = 16000000;
      cfg.spi_3wire = false; cfg.use_lock = true; cfg.dma_channel = 1;
      cfg.pin_sclk = 18; cfg.pin_mosi = 23;
      cfg.pin_miso = 19; cfg.pin_dc   =  2;
      _bus_instance.config(cfg);
      _panel_instance.setBus(&_bus_instance); }
    { auto cfg = _panel_instance.config();
      cfg.pin_cs = 15; cfg.pin_rst = 4; cfg.pin_busy = -1;
      cfg.memory_width = 240; cfg.memory_height = 320;
      cfg.panel_width  = 240; cfg.panel_height  = 320;
      cfg.offset_x = 0; cfg.offset_y = 0; cfg.offset_rotation = 0;
      cfg.readable = true; cfg.invert = false;
      cfg.rgb_order = false; cfg.dlen_16bit = false; cfg.bus_shared = true;
      _panel_instance.config(cfg); }
    { auto cfg = _light_instance.config();
      cfg.pin_bl = 32; cfg.invert = false;
      cfg.freq = 44100; cfg.pwm_channel = 7;
      _light_instance.config(cfg);
      _panel_instance.setLight(&_light_instance); }
    setPanel(&_panel_instance);
  }
};

// =====================================================================
//  Variables globales
// =====================================================================
LGFX         tft;
LGFX_Sprite  spr(&tft);   // sprite pantalla completa — elimina parpadeo
WiFiUDP      udp;

// ── Datos del sensor ──────────────────────────────────────────────────
struct SensorData {
  bool    running     = false;
  float   elapsed_sec = 0.0f;
  float   gyro_value  = 0.0f;
  String  last_cmd    = "---";
  float   accel_x     = 0.0f;
  float   accel_y     = 0.0f;
  float   accel_z     = 1.0f;
  float   gyro_x      = 0.0f;
  float   gyro_y      = 0.0f;
  float   gyro_z      = 0.0f;
  float   temperature = 25.0f;
  int     motor[3]    = {0, 0, 0};
  bool    valid       = false;
};
SensorData data;

// ── Buffers circulares para graficas (6 canales) ──────────────────────
//   0=Ax  1=Ay  2=Az  3=Gx  4=Gy  5=Gz
float   graphBuf[6][GRAPH_POINTS];
uint8_t graphHead   = 0;       // indice del dato mas reciente
bool    graphFull   = false;   // true cuando el buffer esta completo
uint32_t lastGraph  = 0;

// ── Estado UI ─────────────────────────────────────────────────────────
Screen   curScreen      = SCR_STATUS;
Screen   prevScreen     = (Screen)99;
bool     needRedraw     = true;
uint32_t lastAutoRot    = 0;
uint32_t lastBlink      = 0;
bool     blinkState     = false;
uint32_t lastDemo       = 0;
uint32_t lastRSSI       = 0;
float    demoPhase      = 0.0f;

// ── Snapshot de lo dibujado en SCR_STATUS (deteccion de cambios anti-parpadeo) ──
bool     lastValid      = false;
bool     lastRunning    = false;
int      lastMotor[3]   = {-1, -1, -1};
int      lastElapsed    = -1;
String   lastCmdDrawn   = "";

// ── Red ───────────────────────────────────────────────────────────────
String   localIP = "---";
int32_t  rssi    = 0;

// ── Paleta de colores ─────────────────────────────────────────────────
static const uint32_t C_BG     = 0x0A0A0A;
static const uint32_t C_HDR    = 0x1A1A2E;
static const uint32_t C_ACC    = 0x16213E;
static const uint32_t C_WHITE  = TFT_WHITE;
static const uint32_t C_GREEN  = 0x00E676;
static const uint32_t C_RED    = 0xFF1744;
static const uint32_t C_YELLOW = 0xFFD600;
static const uint32_t C_CYAN   = 0x00E5FF;
static const uint32_t C_ORANGE = 0xFF6D00;
static const uint32_t C_GRAY   = 0x546E7A;
static const uint32_t C_LGRAY  = 0x37474F;

// ── Colores por canal de grafica ──────────────────────────────────────
static const uint32_t GCOL[6] = {
  0xFF5252,  // Ax rojo
  0x69F0AE,  // Ay verde
  0x40C4FF,  // Az azul claro
  0xFF6D00,  // Gx naranja
  0xE040FB,  // Gy morado
  0xFFFF00   // Gz amarillo
};

// =====================================================================
//  Prototipos
// =====================================================================
void connectWiFi();
void setupMulticast();
void readUDP();
bool parseJSON(const char* json);
void updateDemoData();
void pushGraphSample();
void checkAutoRotate();
void redraw();
void drawStatus();
void drawSensor();
void drawHeader(const char* title, uint32_t col);
void drawTabBar();
void drawVBar(int x, int y, int w, int h, float pct, uint32_t col);
void drawMiniGraph(int x, int y, int w, int h,
                   int ch, float yMin, float yMax,
                   uint32_t col, const char* label);

// =====================================================================
//  SETUP
// =====================================================================
void setup()
{
  Serial.begin(115200);
  Serial.println("\n=== ESP32 Seismic Dashboard ===");

  // ── TFT ──────────────────────────────────────────────────────────
  tft.init();
  tft.setRotation(1);
  tft.setBrightness(220);
  tft.fillScreen(TFT_BLACK);

  // ── Sprite anti-parpadeo ─────────────────────────────────────────
  // 16 bits: 320×240×2 = 150 KB  →  si falla, 8 bits: 76 KB
  spr.setColorDepth(16);
  if (!spr.createSprite(320, 240)) {
    Serial.println("[WARN] 16-bit sprite fallo, intentando 8-bit");
    spr.setColorDepth(8);
    if (!spr.createSprite(320, 240)) {
      Serial.println("[ERROR] Sin memoria para sprite — dibujo directo");
    }
  }
  Serial.printf("[MEM] Heap libre: %d bytes\n", ESP.getFreeHeap());

  // ── Buffers de grafica a cero ─────────────────────────────────────
  memset(graphBuf, 0, sizeof(graphBuf));

#if DEMO_MODE
  localIP = "192.168.4.2";
  rssi    = -58;
  randomSeed(analogRead(0));
  Serial.println("[DEMO] Modo demo activo");
  // Primer dato y dibujado inmediato
  updateDemoData();
  pushGraphSample();
  redraw();
#else
  tft.setTextColor(TFT_WHITE); tft.setTextSize(2);
  tft.setTextDatum(lgfx::middle_center);
  tft.drawString("Conectando WiFi...", 160, 120);
  tft.setTextDatum(lgfx::top_left);
  connectWiFi();
  setupMulticast();
  redraw();
#endif
}

// =====================================================================
//  LOOP
// =====================================================================
void loop()
{
  uint32_t now = millis();

  // 1. Datos
#if DEMO_MODE
  updateDemoData();
#else
  readUDP();

  // 1b. Anti-parpadeo: en la pantalla de estado solo se redibuja si cambia el
  //     contenido visible (estado, tiempo, motores a 0% si parado, comando).
  //     La pantalla de sensor se refresca al ritmo de la grafica (paso 2).
  if (curScreen == SCR_STATUS) {
    int e  = (int)data.elapsed_sec;
    int m0 = data.running ? data.motor[0] : 0;
    int m1 = data.running ? data.motor[1] : 0;
    int m2 = data.running ? data.motor[2] : 0;
    if (data.valid != lastValid || data.running != lastRunning ||
        e != lastElapsed ||
        m0 != lastMotor[0] || m1 != lastMotor[1] || m2 != lastMotor[2] ||
        data.last_cmd != lastCmdDrawn) {
      needRedraw = true;
    }
  }
#endif

  // 2. Muestra de grafica cada GRAPH_INTERVAL ms
  if (now - lastGraph >= GRAPH_INTERVAL) {
    lastGraph = now;
    pushGraphSample();
    if (curScreen == SCR_SENSOR) needRedraw = true;
  }

  // 3. Rotacion automatica de pantalla
  checkAutoRotate();

  // 4. RSSI cada 3 s
  if (now - lastRSSI > 3000) {
    lastRSSI = now;
#if !DEMO_MODE
    rssi = WiFi.RSSI();
#endif
  }

  // 5. Parpadeo banner sismo (500 ms)
  if (now - lastBlink > 500) {
    lastBlink = now;
    blinkState = !blinkState;
    if (data.running && curScreen == SCR_STATUS) needRedraw = true;
  }

  // 6. Redibujar solo si es necesario
  if (needRedraw || curScreen != prevScreen) {
    redraw();
    needRedraw = false;
    prevScreen = curScreen;
  }
}

// =====================================================================
//  Rotacion automatica
// =====================================================================
void checkAutoRotate()
{
#if AUTO_ROTATE
  if (millis() - lastAutoRot >= AUTO_ROTATE_MS) {
    lastAutoRot = millis();
    curScreen   = (Screen)((curScreen + 1) % 2);
    needRedraw  = true;
    Serial.printf("[AUTO] Pantalla %d\n", (int)curScreen + 1);
  }
#endif
}

// =====================================================================
//  Buffer circular de graficas
// =====================================================================
void pushGraphSample()
{
  graphBuf[0][graphHead] = data.accel_x;
  graphBuf[1][graphHead] = data.accel_y;
  graphBuf[2][graphHead] = data.accel_z;
  graphBuf[3][graphHead] = data.gyro_x;
  graphBuf[4][graphHead] = data.gyro_y;
  graphBuf[5][graphHead] = data.gyro_z;
  graphHead = (graphHead + 1) % GRAPH_POINTS;
  if (graphHead == 0) graphFull = true;
}

// =====================================================================
//  DEMO — datos simulados animados
// =====================================================================
void updateDemoData()
{
  if (millis() - lastDemo < 300) return;
  lastDemo = millis();

  demoPhase += 0.063f;
  if (demoPhase > TWO_PI) demoPhase -= TWO_PI;

  bool sismo = (demoPhase > PI * 0.3f && demoPhase < PI * 1.7f);
  data.running = sismo;
  data.valid   = true;

  float env = sismo ? abs(sin(demoPhase)) * 1.8f
                    : abs(sin(demoPhase)) * 0.05f;

  data.gyro_value = constrain(env + random(-8,8)/100.0f, 0.0f, 2.0f);

  static float elapsed = 0.0f;
  if (sismo) elapsed += 0.3f;
  else if (demoPhase < 0.1f) elapsed = 0.0f;
  data.elapsed_sec = elapsed;

  static const char* presets[] = {
    "sismo_leve","sismo_moderado","sismo_fuerte","pulso_corto","reposo"
  };
  static int pidx = 0;
  static uint32_t lp = 0;
  if (millis() - lp > 8000) { lp = millis(); pidx = (pidx+1)%5; }
  data.last_cmd = presets[pidx];

  float amp = env * 0.6f;
  data.accel_x =  amp * sin(demoPhase*3.1f) + random(-3,3)/100.0f;
  data.accel_y =  amp * cos(demoPhase*2.7f) + random(-3,3)/100.0f;
  data.accel_z =  1.0f + amp*0.3f*sin(demoPhase) + random(-2,2)/100.0f;

  float ga = env * 45.0f;
  data.gyro_x = ga*sin(demoPhase*2.3f) + random(-50,50)/10.0f;
  data.gyro_y = ga*cos(demoPhase*1.9f) + random(-50,50)/10.0f;
  data.gyro_z = ga*sin(demoPhase*1.4f) + random(-30,30)/10.0f;

  data.temperature = 28.0f + env*7.5f + random(-10,10)/10.0f;

  float mb = sismo ? env/1.8f : 0.0f;
  data.motor[0] = constrain((int)(mb*100+random(-5,5)), 0, 100);
  data.motor[1] = constrain((int)(mb* 80+random(-5,5)), 0, 100);
  data.motor[2] = constrain((int)(mb* 60+random(-5,5)), 0, 100);

  rssi = -58 + random(-4,4);
  needRedraw = true;
}

// =====================================================================
//  WiFi / UDP
// =====================================================================
void connectWiFi()
{
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("Conectando a %s", WIFI_SSID);

  // ── No avanzar hasta encontrar la red ─────────────────────────────
  // Bucle indefinido: reintenta WiFi.begin() cada 15 s y avisa en TFT.
  uint32_t t = millis();
  uint8_t  dots = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(300); Serial.print(".");

    // Reintento periodico de conexion
    if (millis() - t > 15000) {
      t = millis();
      Serial.println("\n[WARN] Sin WiFi, reintentando...");
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }

    // Aviso animado en pantalla mientras se busca la red
    tft.fillScreen(TFT_BLACK);
    tft.setTextColor(TFT_WHITE); tft.setTextSize(2);
    tft.setTextDatum(lgfx::middle_center);
    tft.drawString("Buscando red...", 160, 104);
    tft.setTextColor(TFT_YELLOW); tft.setTextSize(1);
    tft.drawString(WIFI_SSID, 160, 132);
    char d[5] = {0};
    for (uint8_t i = 0; i < (dots % 4); i++) d[i] = '.';
    tft.setTextColor(TFT_WHITE); tft.setTextSize(3);
    tft.drawString(d, 160, 156);
    tft.setTextDatum(lgfx::top_left);
    dots++;
  }

  localIP = WiFi.localIP().toString();
  Serial.printf("\nIP: %s\n", localIP.c_str());
}

void setupMulticast()
{
  IPAddress ip; ip.fromString(MCAST_IP);
  if (udp.beginMulticast(ip, MCAST_PORT))
    Serial.printf("Multicast OK %s:%d\n", MCAST_IP, MCAST_PORT);
  else
    Serial.println("[ERROR] Fallo multicast");
}

#define UDP_DEBUG  true    // true = imprime por Serial el JSON crudo recibido

void readUDP()
{
  int sz = udp.parsePacket();
  if (sz <= 0) return;
  char buf[UDP_BUFFER];
  int  len = udp.read(buf, UDP_BUFFER-1);
  if (len > 0) {
    buf[len] = '\0';
#if UDP_DEBUG
    Serial.printf("[UDP %d bytes] %s\n", len, buf);
#endif
    // No se fuerza el redibujo aqui: loop() decide segun la pantalla y si
    // cambio el contenido visible, para no parpadear con cada paquete (5 Hz).
    bool ok = parseJSON(buf);
#if UDP_DEBUG
    if (!ok) Serial.println("[UDP] JSON invalido o no parseable");
#endif
  }
}

bool parseJSON(const char* json)
{
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, json)) return false;
  data.running     = doc["running"]         | false;
  data.elapsed_sec = doc["elapsed_seconds"] | 0.0f;
  // main.py emite "gyro" (intensidad) y "last_command"; aceptamos tambien los
  // nombres antiguos "gyro_value"/"preset" como fallback.
  data.gyro_value  = doc["gyro"]            | (doc["gyro_value"] | 0.0f);
  data.last_cmd    = doc["last_command"]    | (doc["preset"]     | "---");

  // ── Acelerometro: acepta "accel" o "acc" como objeto {x,y,z} ───────
  JsonObject ac = doc["accel"];
  if (ac.isNull()) ac = doc["acc"];
  if (!ac.isNull()) {
    data.accel_x = ac["x"] | data.accel_x;
    data.accel_y = ac["y"] | data.accel_y;
    data.accel_z = ac["z"] | data.accel_z;
  } else {
    // Variante plana: ax/ay/az
    data.accel_x = doc["ax"] | data.accel_x;
    data.accel_y = doc["ay"] | data.accel_y;
    data.accel_z = doc["az"] | data.accel_z;
  }

  // ── Giroscopio: acepta "gyro_axes", "gyro" o "gyr" {x,y,z} ─────────
  JsonObject gy = doc["gyro_axes"];
  if (gy.isNull()) gy = doc["gyro"];
  if (gy.isNull()) gy = doc["gyr"];
  if (!gy.isNull()) {
    data.gyro_x = gy["x"] | data.gyro_x;
    data.gyro_y = gy["y"] | data.gyro_y;
    data.gyro_z = gy["z"] | data.gyro_z;
  } else if (doc.containsKey("gx")) {
    // Variante plana corta: gx/gy/gz
    data.gyro_x = doc["gx"] | data.gyro_x;
    data.gyro_y = doc["gy"] | data.gyro_y;
    data.gyro_z = doc["gz"] | data.gyro_z;
  } else {
    // Variante plana larga: gyro_x/gyro_y/gyro_z
    data.gyro_x = doc["gyro_x"] | data.gyro_x;
    data.gyro_y = doc["gyro_y"] | data.gyro_y;
    data.gyro_z = doc["gyro_z"] | data.gyro_z;
  }

  // main.py emite "temp"; "temperature" se mantiene como fallback.
  data.temperature = doc["temp"] | (doc["temperature"] | data.temperature);
  JsonArray mo = doc["motors"];
  if (!mo.isNull()) for (int i=0;i<3&&i<(int)mo.size();i++) data.motor[i]=mo[i]|0;
  data.valid = true;
  return true;
}

// =====================================================================
//  REDIBUJADO — vuelca al sprite y luego pushSprite de una sola vez
// =====================================================================
void redraw()
{
  bool hasSpr = (spr.width() > 0);

  if (hasSpr) spr.fillScreen(C_BG);
  else         tft.fillScreen(C_BG);

  switch (curScreen) {
    case SCR_STATUS: drawStatus(); break;
    case SCR_SENSOR: drawSensor(); break;
  }
  drawTabBar();

  if (hasSpr) spr.pushSprite(0, 0);
}

// Helper: referencia al canvas activo
static inline LovyanGFX& cv() {
  return (spr.width() > 0) ? (LovyanGFX&)spr : (LovyanGFX&)tft;
}

// =====================================================================
//  PANTALLA 1 — Estado del Sismo
// =====================================================================
void drawStatus()
{
  // Banner
  uint32_t bc; const char* bt;
  if (!data.valid)       { bc=C_GRAY;  bt="  SIN DATOS  "; }
  else if (data.running) { bc=blinkState?C_RED:0x8B0000; bt="  SISMO ACTIVO  "; }
  else                   { bc=C_GREEN; bt="    SEGURO    "; }

  cv().fillRect(0,0,320,38,bc);
  cv().setTextColor(C_WHITE); cv().setTextSize(2);
  cv().setTextDatum(lgfx::middle_center);
  cv().drawString(bt, 160, 19);
  cv().setTextDatum(lgfx::top_left);

  // Cronometro
  cv().setTextColor(C_GRAY); cv().setTextSize(1);
  cv().drawString("TIEMPO TRANSCURRIDO", 10, 41);
  char tb[12]; int ts=(int)data.elapsed_sec;
  snprintf(tb,sizeof(tb),"%02d:%02d", ts/60, ts%60);
  cv().setTextColor(data.running?C_YELLOW:C_WHITE); cv().setTextSize(3);
  cv().drawString(tb, 10, 51);

  // ── Motores (3 canales PWM) ───────────────────────────────────────
  cv().setTextColor(C_GRAY); cv().setTextSize(1);
  cv().drawString("MOTORES PWM (0 - 100%)", 10, 80);

  const char*    lbl[]  = {"M1-2","M3-4","M5-6"};
  const uint32_t mcol[] = {C_CYAN, C_GREEN, C_ORANGE};
  int bw=58, bh=66, by=92, sp=100;

  for (int i = 0; i < 3; i++) {
    // Sin sismo activo el PWM real es 0, asi que las barras se muestran a 0%
    // (la controladora difunde la velocidad objetivo ~50% aunque este parada).
    int mval = data.running ? data.motor[i] : 0;

    int bx = 20 + i * sp;
    cv().drawRect(bx, by, bw, bh, C_GRAY);
    drawVBar(bx+1, by+1, bw-2, bh-2, mval/100.0f, mcol[i]);

    cv().setTextColor(mcol[i]); cv().setTextSize(1);
    cv().setTextDatum(lgfx::middle_center);
    cv().drawString(lbl[i], bx+bw/2, by+bh+6);

    char pb[8]; snprintf(pb,sizeof(pb),"%d%%",mval);
    cv().setTextColor(C_WHITE); cv().setTextSize(2);
    cv().drawString(pb, bx+bw/2, by+bh+18);

    lastMotor[i] = mval;   // snapshot para la deteccion de cambios (anti-parpadeo)
  }
  cv().setTextDatum(lgfx::top_left);

  // Ultimo comando
  cv().fillRect(0,190,320,1,C_ACC);
  cv().setTextColor(C_GRAY); cv().setTextSize(1);
  cv().drawString("ULTIMO PRESET / COMANDO:", 10, 195);
  cv().setTextColor(C_CYAN); cv().setTextSize(2);
  cv().drawString(data.last_cmd.c_str(), 10, 205);

  // Snapshot del contenido dibujado (anti-parpadeo): loop() solo vuelve a
  // redibujar esta pantalla si alguno de estos valores cambia.
  lastValid    = data.valid;
  lastRunning  = data.running;
  lastElapsed  = (int)data.elapsed_sec;
  lastCmdDrawn = data.last_cmd;
}

// =====================================================================
//  PANTALLA 2 — Sensor MPU-6050 + Graficas
// =====================================================================
void drawSensor()
{
  drawHeader("  MPU-6050  SENSOR", C_HDR);

  // ── Valores numericos compactos (2 columnas) ──────────────────────
  // Col izq: acelerometro  |  Col der: giroscopio
  char buf[24];
  int y = 40;

  // Cabeceras
  cv().setTextSize(1);
  cv().setTextColor(C_CYAN);
  cv().drawString("ACCEL (g)", 4, y);
  cv().setTextColor(C_ORANGE);
  cv().drawString("GYRO (deg/s)", 164, y);
  y += 12;

  // Filas Ax/Ay/Az  —  Gx/Gy/Gz
  const float* acv[3] = {&data.accel_x, &data.accel_y, &data.accel_z};
  const float* gyv[3] = {&data.gyro_x,  &data.gyro_y,  &data.gyro_z};
  const char*  albl[] = {"Ax","Ay","Az"};
  const char*  glbl[] = {"Gx","Gy","Gz"};
  const uint32_t ac_col[3] = {GCOL[0], GCOL[1], GCOL[2]};
  const uint32_t gy_col[3] = {GCOL[3], GCOL[4], GCOL[5]};

  for (int i = 0; i < 3; i++) {
    // Accel
    cv().setTextColor(ac_col[i]); cv().setTextSize(1);
    cv().drawString(albl[i], 4, y+2);
    snprintf(buf, sizeof(buf), "%+6.3f", *acv[i]);
    cv().setTextColor(C_WHITE); cv().setTextSize(2);
    cv().drawString(buf, 20, y);

    // Gyro
    cv().setTextColor(gy_col[i]); cv().setTextSize(1);
    cv().drawString(glbl[i], 164, y+2);
    snprintf(buf, sizeof(buf), "%+7.1f", *gyv[i]);
    cv().setTextColor(C_WHITE); cv().setTextSize(2);
    cv().drawString(buf, 180, y);

    y += 20;
  }

  // Temperatura
  cv().setTextColor(C_GRAY); cv().setTextSize(1);
  cv().drawString("TEMP:", 4, y+2);
  snprintf(buf, sizeof(buf), "%.1f C", data.temperature);
  cv().setTextColor(data.temperature > 50 ? C_RED : C_GREEN);
  cv().setTextSize(2); cv().drawString(buf, 36, y);

  // ── Separador ─────────────────────────────────────────────────────
  y += 22;
  cv().fillRect(0, y, 320, 1, C_ACC);
  y += 3;

  // ── 3 graficas de scroll (Ax, Ay, Az) ────────────────────────────
  // Espacio disponible: y hasta 221 (tab bar) → 221-y pixeles
  int graphAreaH = 221 - y;        // total disponible
  int gh         = graphAreaH / 3; // altura de cada grafica

  drawMiniGraph(0, y,            320, gh-2, 0, -1.5f,  1.5f, GCOL[0], "Ax(g)");
  drawMiniGraph(0, y + gh,       320, gh-2, 1, -1.5f,  1.5f, GCOL[1], "Ay(g)");
  drawMiniGraph(0, y + gh*2,     320, gh-2, 2,  0.0f,  2.0f, GCOL[2], "Az(g)");
}

// =====================================================================
//  Grafica de scroll horizontal para un canal
//  x,y,w,h  : posicion y tamaño
//  ch        : canal del buffer (0-5)
//  yMin,yMax : rango de valores
//  col       : color de la linea
//  label     : etiqueta izquierda
// =====================================================================
void drawMiniGraph(int x, int y, int w, int h,
                   int ch, float yMin, float yMax,
                   uint32_t col, const char* label)
{
  const int lblW = 34;   // ancho reservado para etiqueta
  const int gx   = x + lblW;
  const int gw   = w - lblW;

  // Fondo
  cv().fillRect(x, y, w, h, C_HDR);

  // Etiqueta
  cv().setTextColor(col); cv().setTextSize(1);
  cv().setTextDatum(lgfx::middle_left);
  cv().drawString(label, x+2, y + h/2);
  cv().setTextDatum(lgfx::top_left);

  // Linea cero (si el rango incluye 0)
  if (yMin < 0.0f && yMax > 0.0f) {
    int zy = y + h - (int)((-yMin / (yMax - yMin)) * h);
    cv().drawFastHLine(gx, zy, gw, C_LGRAY);
  }

  // Cuantos puntos tenemos
  int total = graphFull ? GRAPH_POINTS : (int)graphHead;
  if (total < 2) return;

  // Escalar cuantos puntos caben horizontalmente
  int pts  = min(total, gw);  // max un punto por pixel
  int step = max(1, total / pts);

  // Dibujar linea de la grafica
  int prevPx = -1, prevPy = -1;
  for (int i = 0; i < pts; i++) {
    // Indice en el buffer circular (mas antiguo a mas reciente)
    int rawIdx = (int)graphHead - pts + i;
    if (rawIdx < 0) rawIdx += GRAPH_POINTS;
    rawIdx = rawIdx % GRAPH_POINTS;

    float val = graphBuf[ch][rawIdx];
    float norm = (val - yMin) / (yMax - yMin);
    norm = constrain(norm, 0.0f, 1.0f);

    int px = gx + (int)((float)i / pts * gw);
    int py = y + h - 1 - (int)(norm * (h - 2));

    if (prevPx >= 0) {
      cv().drawLine(prevPx, prevPy, px, py, col);
    }
    prevPx = px; prevPy = py;
  }

  // Borde
  cv().drawRect(gx, y, gw, h, C_LGRAY);

  // Min/Max labels
  char tmp[8];
  snprintf(tmp, sizeof(tmp), "%.1f", yMax);
  cv().setTextColor(C_GRAY); cv().setTextSize(1);
  cv().drawString(tmp, gx+2, y+1);
  snprintf(tmp, sizeof(tmp), "%.1f", yMin);
  cv().drawString(tmp, gx+2, y+h-9);
}

// =====================================================================
//  Helpers
// =====================================================================
void drawHeader(const char* title, uint32_t col)
{
  cv().fillRect(0,0,320,36,col);
  cv().setTextColor(C_WHITE); cv().setTextSize(2);
  cv().setTextDatum(lgfx::middle_center);
  cv().drawString(title,160,18);
  cv().setTextDatum(lgfx::top_left);
}

void drawVBar(int x,int y,int w,int h,float pct,uint32_t col)
{
  cv().fillRect(x,y,w,h,C_HDR);
  int f=(int)(pct*h);
  if(f>0) cv().fillRect(x,y+h-f,w,f,col);
}

void drawTabBar()
{
  const int ty=222, tw=160;
  cv().fillRect(0,ty,320,18,C_HDR);
  const char* lbl[]={"1.ESTADO","2.SENSOR"};
  for(int i=0;i<2;i++){
    bool act=(i==(int)curScreen);
    cv().fillRect(i*tw,ty,tw,18, act?C_ACC:C_HDR);
    cv().setTextColor(act?C_WHITE:C_GRAY); cv().setTextSize(1);
    cv().setTextDatum(lgfx::middle_center);
    cv().drawString(lbl[i], i*tw+tw/2, ty+9);
    if(act) cv().fillRect(i*tw,ty,tw,2,C_CYAN);
  }
  cv().setTextDatum(lgfx::top_left);
}

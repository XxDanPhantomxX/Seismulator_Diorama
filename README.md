# Seismulator — Simulador de Sismos

Maqueta educativa que **simula sismos** con motores y los visualiza en tiempo real.
El sistema se compone de **dos ESP32** que se comunican por una red WiFi propia
(sin router ni internet):

| Rol | Codigo | Plataforma | Funcion |
|---|---|---|---|
| **Controladora** | `main.py` | ESP32 + MicroPython | Levanta el Access Point, sirve la web de control, mueve los motores y lee el sensor MPU-6050 |
| **Dashboard / Visualizador** | `ESP32_Seismic_Dashboard.ino` | ESP32-2432S028 ("CYD") + Arduino | Se conecta como cliente WiFi, escucha la difusion del sensor y la muestra en una pantalla TFT |

```
                         WiFi AP "ESP32-Control" (192.168.4.1)
                                      |
        +-----------------------------+-----------------------------+
        |                             |                             |
   Celular / PC                Controladora ESP32              Dashboard ESP32
   (navegador web)             (main.py, MicroPython)          (.ino, TFT 2.8")
        |                             |                             ^
        |  HTTP  /  /api/*            |  6 motores DC (3 puentes H) |
        +-----------> :80 <-----------+  MPU-6050 (I2C)             |
                                      |                             |
                                      |  UDP unicast :5006 (comandos)
                                      |  UDP multicast 224.1.1.10:5005 (sensor)
                                      +-----------------------------+
```

---

## Componentes del repositorio

| Archivo | Descripcion |
|---|---|
| `main.py` | Firmware de la **controladora** (MicroPython). Contiene la pagina web embebida (`HTML_PAGE`), el servidor HTTP, la interfaz UDP y toda la logica de motores/sensor. |
| `mpu6050.py` | Driver I2C minimo del sensor MPU-6050 / GY-521 (acelerometro + giroscopio + temperatura). |
| `ESP32_Seismic_Dashboard.ino` | Firmware del **dashboard** (Arduino/C++). Pantalla TFT con LovyanGFX que rota entre vistas de estado y sensor. |
| `index.html` | Copia suelta de la interfaz web (preview de diseno). **El firmware no la usa**: la fuente de verdad es `HTML_PAGE` dentro de `main.py`. |
| `firmware_controladora.md` | Documentacion general de `main.py` (red, API, hardware, logica). |
| `firmware_controladora_funciones.md` | Referencia funcion por funcion de `main.py`. |
| `hardware_pinout_esp32.md`, `hardware_conexion_puente_h.md` | Notas de hardware, pinout y cableado de los puentes H. |

---

## 1. Controladora (`main.py`)

ESP32 con MicroPython que funciona como **Access Point**: no necesita router ni internet.

> Documentacion a fondo: **`firmware_controladora.md`** (red, API, protocolo UDP y logica
> de sismo/sensor en detalle) y **`firmware_controladora_funciones.md`** (referencia de
> cada funcion de `main.py`).

### Puesta en marcha

1. Sube `main.py` y `mpu6050.py` a la placa (Thonny, `mpremote`, etc.).
2. Reinicia: el `main()` arranca actuadores, sensor, AP y el bucle de eventos.
3. Conecta tu celular o PC a la red WiFi:
   - **SSID:** `ESP32-Control`
   - **Password:** `12345678`
4. Abre en el navegador `http://192.168.4.1/`.

> No uses `localhost`: apunta a tu propio dispositivo, no al ESP32.

### Interfaz web

La pagina (embebida en `HTML_PAGE`) ofrece:

- Boton principal **Iniciar / Detener** el sismo, con cronometro.
- Presets de intensidad: **Ligero**, **Medio**, **Fuerte**.
- Control de velocidad **global** y **por motor** (1, 2, 3).
- Indicador de estado: `Seguro / Ligero / Medio / Fuerte` y animacion de edificios + sismografo.
- Modal **Info**: conexion, velocidad promedio, intensidad (g), aceleracion `(ax, ay, az)`,
  giro `(gx, gy, gz)`, temperatura y ultimo comando.
- Modal **Debug** (`/api/debug`): estado del sensor, WiFi/AP, clientes, peticiones HTTP,
  memoria libre, uptime y lecturas crudas.

### API HTTP

| Ruta | Metodo | Funcion |
|---|---|---|
| `/` | `GET` | Sirve la pagina de control HTML |
| `/api/status` | `GET` | Estado de la maqueta en JSON |
| `/api/debug` | `GET` | Diagnostico (sensor, WiFi, memoria, uptime) en JSON |
| `/api/control` | `POST` | Recibe comandos JSON desde la interfaz |
| `*` | `OPTIONS` | Responde `200` (CORS preflight) |

Todas las respuestas llevan `Access-Control-Allow-Origin: *`.

**Comandos** (`POST /api/control` o UDP unicast):

```json
{ "command": "start",  "motors": { "1": 50, "2": 50, "3": 50 } }
{ "command": "stop",   "motors": { "1": 0,  "2": 0,  "3": 0 } }
{ "command": "set_speed", "motor": 1, "speed": 70 }
{ "command": "preset_moderate" }
```

| Comando | Accion |
|---|---|
| `start` / `stop` | Inicia / detiene el sismo |
| `preset_light` | 99% inicial y baja a 40% tras 1 s ("light boost") |
| `preset_moderate` | 65% |
| `preset_intense` | 99% |
| `set_speed` | Velocidad global (`"motor": "all"`) o individual |

### Hardware

- **3 puentes H L298N** moviendo **6 motores DC de 5V** (2 motores por puente, a la misma velocidad).
- **Sensor MPU-6050 / GY-521** por I2C.

| Bloque | Senal | GPIO |
|---|---|---|
| Puente H 1 | ENA(+ENB), IN1(+IN3), IN2(+IN4) | 25, 32, 33 |
| Puente H 2 | ENA(+ENB), IN1(+IN3), IN2(+IN4) | 26, 16, 17 |
| Puente H 3 (reverse) | ENA(+ENB), IN1(+IN3), IN2(+IN4) | 27, 13, 14 |
| MPU-6050 | SDA, SCL | 21, 22 |

El puente H 3 lleva `"reverse": True` para que sus motores giren en el mismo sentido fisico
que los puentes 1 y 2. Los motores se alimentan **desde la fuente de 5V a traves del puente H**,
nunca directo desde los GPIO.

> Pinout completo de la ESP32 y diagramas de conexion en **`hardware_pinout_esp32.md`**.
> Guia paso a paso del cableado motores ↔ puente H (alimentacion, GND comun, jumper +5V
> y problemas comunes) en **`hardware_conexion_puente_h.md`**.

---

## 2. Dashboard / Visualizador (`ESP32_Seismic_Dashboard.ino`)

Segunda ESP32 (placa **ESP32-2432S028**, "Cheap Yellow Display") con pantalla **TFT ILI9341
de 2.8" (320x240)**. Es de **solo lectura**: no envia comandos; se suscribe a la difusion del
sensor y la dibuja.

### Dependencias (Arduino IDE / PlatformIO)

- `LovyanGFX` (driver de la pantalla)
- `ArduinoJson`
- `WiFi` + `WiFiUDP` (incluidas en el core ESP32)

### Configuracion (cabecera del `.ino`)

```cpp
#define WIFI_SSID      "ESP32-Control"   // debe coincidir con el AP de la controladora
#define WIFI_PASS      "12345678"
#define MCAST_IP       "224.1.1.10"      // grupo multicast de la controladora
#define MCAST_PORT     5005
#define AUTO_ROTATE    true              // rota de pantalla automaticamente
#define AUTO_ROTATE_MS 10000             // cada 10 s
#define DEMO_MODE      false             // true = datos simulados, sin controladora
```

### Comportamiento

- Se conecta como **STA** a `ESP32-Control` (reintenta cada 15 s, mostrando aviso en pantalla).
- Se une al grupo **multicast `224.1.1.10:5005`** y parsea cada paquete JSON entrante.
- **Modo automatico** (sin botones): rota entre 2 pantallas cada 10 s.
  - **Pantalla 1 — Estado:** banner `SEGURO / SISMO ACTIVO / SIN DATOS`, cronometro,
    3 barras de motores (`M1-2`, `M3-4`, `M5-6`) y ultimo preset/comando.
  - **Pantalla 2 — Sensor:** valores numericos de accel/gyro/temp y 3 graficas de scroll
    (Ax, Ay, Az) con buffer circular de 80 muestras.
- **`DEMO_MODE true`** genera datos animados para probar la pantalla sin la controladora.

### Pinout TFT (configurado en la clase `LGFX`)

| Senal | GPIO |
|---|---|
| SCLK / MOSI / MISO / DC | 18 / 23 / 19 / 2 |
| CS / RST | 15 / 4 |
| Backlight (PWM) | 32 |

---

## Comunicacion entre placas (UDP)

Los navegadores no pueden recibir UDP, por eso la controladora expone **ambos** canales:

- **HTTP (puerto 80):** lo usa la web del navegador.
- **UDP unicast (puerto 5006):** comandos maquina-a-maquina con el **mismo JSON** que `/api/control`.
  La controladora responde por unicast con el estado.
- **UDP multicast (`224.1.1.10:5005`):** la controladora difunde el estado completo
  (`status_payload()`) cada 200 ms. Varios receptores pueden suscribirse; el dashboard es uno.

Estado difundido (`status_payload`):

```json
{
  "ok": true,
  "last_command": "preset_moderate",
  "running": true,
  "elapsed_seconds": 12,
  "gyro": 0.84,
  "accel":     { "x": 0.012, "y": -0.004, "z": 1.001 },
  "gyro_axes": { "x": 1.23,  "y": -0.45,  "z": 0.07 },
  "temp": 29.4,
  "motors": [65, 65, 65]
}
```

> **Formato de `motors`:** en el estado *saliente* es un **array `[m1, m2, m3]`** (lo construye
> `motors_list()`), porque el dashboard parsea `doc["motors"]` como `JsonArray`. En los comandos
> *entrantes* puede venir como objeto `{"1":50,...}`; `apply_motor_payload()` acepta ambos.

### Nombres de campo (controladora ↔ dashboard)

El dashboard lee las claves **tal como las emite `main.py`**, manteniendo los nombres antiguos
como fallback. Tres campos usaban antes nombres distintos; `parseJSON()` ahora acepta ambos:

| Dato | Emite `main.py` | Lee el dashboard (con fallback) |
|---|---|---|
| Intensidad | `gyro` | `gyro` → `gyro_value` |
| Ultimo comando | `last_command` | `last_command` → `preset` |
| Temperatura | `temp` | `temp` → `temperature` |
| running / elapsed_seconds / accel / gyro_axes / motors | iguales | iguales |

El resto de campos que el dashboard tolera (`acc`, `gyr`, `ax/ay/az`, etc.) ya tenian fallback.

---

## Documentacion adicional

- `firmware_controladora.md` — vision general de la controladora.
- `firmware_controladora_funciones.md` — referencia detallada de cada funcion de `main.py`.
- `hardware_conexion_puente_h.md` — cableado de los puentes H L298N.
- `hardware_pinout_esp32.md` — pinout de la ESP32 y notas de cambios de hardware.

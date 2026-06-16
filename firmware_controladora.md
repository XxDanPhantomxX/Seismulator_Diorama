# Documentacion de `main.py`

## Objetivo

`main.py` es el programa principal para un ESP32 con MicroPython. Su objetivo es levantar una red WiFi propia, servir una pagina web local y recibir comandos desde esa pagina para manipular una maqueta que simula sismos.

La interfaz esta pensada para controlar motores, iniciar o detener el terremoto, aplicar velocidades predefinidas y consultar el estado de la maqueta.

> Este documento cubre la **controladora**. Para la vision del sistema completo (incluido el
> **dashboard** en una segunda ESP32 con pantalla TFT, `ESP32_Seismic_Dashboard.ino`) ver
> `README.md`. Para el detalle funcion por funcion ver `firmware_controladora_funciones.md`.

## Red WiFi

El ESP32 funciona como Access Point, por lo que no necesita router ni internet.

Configuracion actual:

```python
AP_SSID = "ESP32-Control"
AP_PASSWORD = "12345678"
AP_IP = "192.168.4.1"
HTTP_PORT = 80
```

Para usarlo:

1. Conectarse desde celular o computadora a la red `ESP32-Control`.
2. Usar la contrasena `12345678`.
3. Abrir en navegador `http://192.168.4.1/`.

No se usa `localhost`, porque `localhost` apunta al celular o computadora, no al ESP32.

## Servidor web

El servidor HTTP se implementa con `socket`.

Rutas disponibles:

| Ruta | Metodo | Funcion |
|---|---|---|
| `/` | `GET` | Sirve la pagina de control HTML embebida (`HTML_PAGE`) |
| `/api/status` | `GET` | Devuelve estado de la maqueta en JSON |
| `/api/debug` | `GET` | Devuelve diagnostico (sensor, WiFi, memoria, uptime, sensor crudo) en JSON |
| `/api/control` | `POST` | Recibe comandos desde la interfaz web |
| `*` | `OPTIONS` | Responde `200` para soporte CORS (preflight) |

Todas las respuestas incluyen la cabecera `Access-Control-Allow-Origin: *`.

## Interfaz HTML

La pagina de control esta **embebida en `main.py`** como la constante `HTML_PAGE`
(HTML + CSS + JS en una sola cadena). La ruta `GET /` la entrega con
`send_response()` y `Content-Type: text/html`.

> Nota: en la carpeta del proyecto existe tambien un `index.html` suelto, pero el
> firmware **no lo usa**: la fuente de verdad de la pagina es `HTML_PAGE` dentro de
> `main.py`. (Si en el futuro se sirve el fichero por streaming, habria que subir
> `index.html` a la placa y cambiar `GET /` para leerlo.)

La pagina incluye:

- Boton principal para iniciar o detener el terremoto.
- Timer de duracion del terremoto.
- Presets de intensidad:
  - `Ligero`
  - `Medio`
  - `Fuerte`
- Control de velocidad global para todos los motores.
- Control individual para motor 1, motor 2 y motor 3.
- Indicador visual de estado:
  - `Seguro`
  - `Ligero`
  - `Medio`
  - `Fuerte`
- Modal de informacion ("Info") con: conexion, velocidad promedio, intensidad (g),
  aceleracion en los 3 ejes (ax, ay, az), velocidad angular en los 3 ejes (gx, gy, gz),
  temperatura del sensor y ultimo comando.
- Modal de diagnostico ("Debug") que consulta `/api/debug`: estado del sensor MPU-6050,
  WiFi/AP, clientes conectados, peticiones HTTP, memoria libre, tiempo encendido,
  sismo activo y lecturas crudas del sensor.
- Animacion visual de edificios y sismografo.

## Comunicacion web con el ESP32

La pagina usa `fetch()` para comunicarse con el ESP32.

Ejemplo para iniciar:

```json
{
  "command": "start",
  "motors": {
    "1": 50,
    "2": 50,
    "3": 50
  }
}
```

Ejemplo para detener:

```json
{
  "command": "stop",
  "motors": {
    "1": 0,
    "2": 0,
    "3": 0
  }
}
```

Ejemplo para cambiar velocidad:

```json
{
  "command": "set_speed",
  "motor": 1,
  "speed": 70
}
```

Ejemplo de preset:

```json
{
  "command": "preset_moderate",
  "motors": {
    "1": 60,
    "2": 60,
    "3": 60
  }
}
```

## Hardware y conexiones

Toda la electronica esta cableada a **una sola ESP32**:

- **3 puentes H** (tipo L298N) que mueven **6 motores DC de 5V** (2 por puente).
- **Sensor MPU-6050 / GY-521** (giroscopio + acelerometro 3 ejes) por I2C.

> Nota: el firmware actual (`main.py`) **no controla ninguna pantalla TFT**. El control
> y el monitoreo desde el navegador se hacen integramente desde la interfaz web servida por
> este ESP32. La pantalla TFT vive en una **segunda ESP32** independiente (el dashboard
> `ESP32_Seismic_Dashboard.ino`), que solo escucha la difusion multicast del sensor. Ver
> `README.md` para la vision del sistema completo.

### Pinout actual

| Bloque | Senal | GPIO |
|---|---|---|
| Puente H 1 | ENA(+ENB), IN1(+IN3), IN2(+IN4) | 25, 32, 33 |
| Puente H 2 | ENA(+ENB), IN1(+IN3), IN2(+IN4) | 26, 16, 17 |
| Puente H 3 (reverse) | ENA(+ENB), IN1(+IN3), IN2(+IN4) | 27, 13, 14 |
| MPU-6050 | SDA, SCL | 21, 22 |

Los pines son configurables en las constantes de `main.py` (`HBRIDGES`, `I2C_*`).

### Motores: 6 motores, 3 controles

Cada puente H maneja 2 motores **a la misma velocidad**. La app conserva sus
**3 controles** (Motor 1, 2, 3); cada uno corresponde a **un puente H** completo,
es decir a **2 motores fisicos**. No hay que modificar el HTML/JS ni el formato JSON.

Conexion asumida en el modulo del puente H:

- `ENA` y `ENB` unidos al mismo pin PWM -> una sola senal de velocidad para los 2 motores.
- `IN1` = `IN3` e `IN2` = `IN4` -> ambos motores giran juntos en direccion fija "adelante".

```python
HBRIDGES = {
    1: {"ena": 25, "in1": 32, "in2": 33},
    2: {"ena": 26, "in1": 16, "in2": 17},
    3: {"ena": 27, "in1": 13, "in2": 14, "reverse": True},
}
PWM_FREQ = 1000
```

El puente H 3 lleva `"reverse": True`: el firmware invierte su par `IN1`/`IN2`
(`IN1=0`, `IN2=1`) para que sus motores giren en el mismo sentido fisico que los
de los puentes 1 y 2 (que usan `IN1=1`, `IN2=0`).

La velocidad se maneja como porcentaje de `0` a `100` y se convierte a duty cycle:

```python
duty = speed * 1023 / 100
```

La escritura del duty se adapta a la API del port de MicroPython (`duty()` de
0-1023 o `duty_u16()` de 16 bits) con `try/except`. No hay un helper unico:
`init_actuators` usa una funcion interna `_set_pwm_duty()` para ese fallback,
`set_motor_speed` repite el `try/except` en linea, y `start_quake`/`stop_quake`
escriben con `pwm.duty()` directamente.

Importante: los motores se alimentan desde la fuente de 5V a traves del puente H,
**nunca** directamente desde los GPIO del ESP32. Los GPIO solo llevan PWM (ENA) y
direccion (IN1/IN2) a la logica del puente.

## Comunicacion UDP: unicast y multicast

Ademas del servidor HTTP (que es lo que usa la app del navegador, ya que los
navegadores **no** pueden recibir UDP), el firmware abre una interfaz UDP
maquina-a-maquina con el **mismo esquema JSON**:

- **Unicast (comandos a los motores)**: la ESP32 escucha en el puerto `5006`.
  Cualquier dispositivo de la red `ESP32-Control` puede enviar un comando JSON
  identico al de `POST /api/control`. La ESP32 responde por unicast con el estado.
- **Multicast (difusion del sensor)**: la ESP32 publica el estado completo
  (`status_payload()`: `running`, `motors` como array `[m1,m2,m3]`, `accel`,
  `gyro_axes`, `gyro`, `temp`, ...) en el grupo `224.1.1.10:5005`. Varios
  receptores pueden suscribirse a la vez; la ESP32 visualizadora (dashboard
  `ESP32_Seismic_Dashboard.ino`) consume este mismo JSON para dibujar barras de
  motores y los ejes del sensor.

> **Nombres de campo con el dashboard:** el dashboard lee las claves tal como las
> emite `status_payload()` (`running`, `elapsed_seconds`, `accel`, `gyro_axes`,
> `motors`, y tambien `gyro`, `last_command` y `temp`). Estos tres ultimos usaban
> antes nombres distintos en el `.ino` (`gyro_value` / `preset` / `temperature`),
> que ahora se conservan solo como fallback en `parseJSON()`. Ver el detalle en
> `README.md`.

```python
CMD_PORT = 5006          # unicast: comandos a motores
MCAST_GRP = "224.1.1.10"
MCAST_PORT = 5005        # multicast: difusion del sensor
```

Ejemplo (Python en una PC conectada al AP) para escuchar el sensor por multicast:

```python
import socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", 5005))
mreq = struct.pack("4sl", socket.inet_aton("224.1.1.10"), socket.INADDR_ANY)
s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
while True:
    print(s.recvfrom(1024))
```

Y para enviar un comando por unicast:

```python
import socket, json
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(json.dumps({"command": "preset_intense"}).encode(), ("192.168.4.1", 5006))
print(s.recvfrom(1024))  # estado de respuesta
```

## Bucle de eventos y tareas periodicas

`run_event_loop()` multiplexa con `select.poll` el socket HTTP (puerto 80) y el socket
UDP (puerto 5006), y ejecuta tareas por tiempo:

- Leer el sensor (`update_gyro()`) cada `SENSOR_MS` = 100 ms.
- Mantener el "light boost" (`update_light_boost()`) en cada vuelta.
- Difundir el estado por multicast (`send_sensor_multicast()`) cada `MCAST_MS` = 200 ms,
  seguido de `gc.collect()`.

El timeout del poll es `SCHED_POLL_MS` = 50 ms.

## Logica de terremoto

El estado del terremoto se guarda en:

```python
quake_running = False
quake_started_ms = 0
```

Cuando se ejecuta `start_quake()`:

- Cambia `quake_running` a `True`.
- Guarda el tiempo inicial.
- Aplica el duty PWM correspondiente a cada motor.
- Actualiza la estimacion de giroscopio.

Cuando se ejecuta `stop_quake()`:

- Cambia `quake_running` a `False`.
- Pone todos los PWM en `0`.
- Reinicia la lectura estimada del giroscopio.

## Giroscopio

`update_gyro()` lee el **MPU-6050 real** si esta presente. Calcula la intensidad
como la aceleracion dinamica (magnitud del vector aceleracion menos la gravedad)
y la suaviza con un filtro exponencial:

```python
ax, ay, az = sensor.read_accel()
magnitude = (ax * ax + ay * ay + az * az) ** 0.5
intensity = abs(magnitude - 1.0)          # g dinamicos
gyro_value = round(0.6 * gyro_value + 0.4 * intensity, 2)
```

Ademas de la intensidad (`gyro_value`), `update_gyro()` guarda las lecturas crudas del
sensor que despues se publican en el estado:

- `accel_xyz`: aceleracion `(ax, ay, az)` en g.
- `gyro_xyz`: velocidad angular `(gx, gy, gz)` en deg/s.
- `mpu_temp`: temperatura interna del chip en C (`sensor.read_temp()`).

Si el sensor no responde (no detectado en I2C o error de lectura), cae
automaticamente a la estimacion por velocidad promedio (`update_gyro_estimate()`),
de modo que la app sigue funcionando.

## Respuesta de estado

`GET /api/status` devuelve un JSON similar a:

```json
{
  "ok": true,
  "last_command": "preset_moderate",
  "running": true,
  "elapsed_seconds": 12,
  "gyro": 0.84,
  "accel": { "x": 0.012, "y": -0.004, "z": 1.001 },
  "gyro_axes": { "x": 1.23, "y": -0.45, "z": 0.07 },
  "temp": 29.4,
  "motors": [65, 65, 65]
}
```

> **Formato de `motors` (importante):** en el **estado saliente** (respuesta de
> `/api/status`, `/api/control`, unicast UDP y difusion multicast) `motors` es un
> **array `[m1, m2, m3]`**, construido por el helper `motors_list()`. Se eligio
> array — y no el dict `motor_speeds` — porque la **ESP32 receptora/visualizadora**
> parsea `doc["motors"]` como `JsonArray`; un objeto `{"1":..}` le llegaria como
> `null` y no pintaria las barras de motores. El indice del array mapea a los
> canales `[Puente H 1, Puente H 2, Puente H 3]`, es decir las etiquetas
> `M1-2 / M3-4 / M5-6` del receptor.
>
> En cambio, en los **comandos entrantes** (`POST /api/control` y unicast) `motors`
> puede venir como objeto `{"1":50,"2":50,"3":50}`; `apply_motor_payload()` acepta
> ese dict. El web UI lee ambos formatos via el helper `readMotor()`.

La interfaz usa estos datos para actualizar el timer, el estado, la intensidad, los
ejes de aceleracion/giro, la temperatura y las velocidades.

### Diagnostico (`GET /api/debug`)

`debug_payload()` devuelve, ademas de las lecturas del sensor (`accel`, `gyro_axes`,
`temp`, `gyro`), informacion del sistema:

```json
{
  "ok": true,
  "sensor_present": true,
  "ap_ssid": "ESP32-Control",
  "ap_ip": "192.168.4.1",
  "clients": 1,
  "request_count": 42,
  "free_mem": 81234,
  "uptime_s": 305,
  "running": false,
  "gyro": 0.0,
  "accel": { "x": 0.0, "y": 0.0, "z": 1.0 },
  "gyro_axes": { "x": 0.0, "y": 0.0, "z": 0.0 },
  "temp": 28.7
}
```

## Punto principal de extension

La funcion mas importante para adaptar el comportamiento es:

```python
apply_command(data)
```

Ahi se interpretan los comandos recibidos desde la pagina y se conectan con la logica fisica de los actuadores.

Comandos actuales:

| Comando | Accion |
|---|---|
| `start` | Inicia el terremoto |
| `stop` | Detiene todos los motores |
| `preset_light` | Aplica 99%, inicia y programa un "light boost" que baja a 40% tras 1 s |
| `preset_moderate` | Aplica 65% e inicia |
| `preset_intense` | Aplica 99% e inicia |
| `set_speed` | Cambia velocidad global (`"motor": "all"`) o individual |

> Antes de ejecutar la accion, `apply_command()` aplica los campos `motors` y/o
> `current_state` del payload (si vienen) mediante `apply_motor_payload()`.

### "Light boost" del preset ligero

`preset_light` arranca a 99% (golpe inicial fuerte) y programa con
`schedule_light_boost(40, 1000)` una transicion automatica: tras 1000 ms,
`update_light_boost()` (llamada en el bucle de eventos) reduce todos los motores a 40%
si el sismo sigue activo. Asi se logra un sismo ligero con un inicio perceptible.


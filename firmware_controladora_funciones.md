# Referencia de funciones de `main.py`

Documento de referencia detallado de **cada funcion** del firmware `main.py`
(ESP32 + MicroPython, simulador de sismos "Seismulator"). Para la vision general
del proyecto (red WiFi, API, hardware) ver `firmware_controladora.md`; aqui el foco
es **que hace cada funcion, sus parametros, lo que devuelve y sus efectos**.

---

## Indice

- [Estado global y constantes](#estado-global-y-constantes)
- [Actuadores / motores](#actuadores--motores)
  - [Escritura del duty PWM](#escritura-del-duty-pwm-compatibilidad-de-api)
  - [`init_actuators`](#init_actuators)
  - [`clamp_speed`](#clamp_speedvalue)
  - [`duty_from_speed`](#duty_from_speedspeed)
  - [`set_motor_speed`](#set_motor_speedmotor_id-speed)
  - [`set_all_motors`](#set_all_motorsspeed)
  - [`apply_motor_payload`](#apply_motor_payloadvalues)
- [Preset "light boost"](#preset-light-boost)
  - [`schedule_light_boost`](#schedule_light_boosttarget_speed40-duration_ms1000)
  - [`update_light_boost`](#update_light_boostnow_msnone)
- [Sensor / giroscopio](#sensor--giroscopio)
  - [`update_gyro_estimate`](#update_gyro_estimate)
  - [`update_gyro`](#update_gyro)
  - [`init_sensor`](#init_sensor)
- [Logica de terremoto](#logica-de-terremoto)
  - [`start_quake`](#start_quake)
  - [`stop_quake`](#stop_quake)
  - [`elapsed_seconds`](#elapsed_seconds)
  - [`apply_command`](#apply_commanddata)
- [Serializacion de estado (JSON)](#serializacion-de-estado-json)
  - [`motors_list`](#motors_list)
  - [`status_payload`](#status_payloadoktrue)
  - [`connected_clients`](#connected_clients)
  - [`debug_payload`](#debug_payload)
- [Red WiFi (Access Point)](#red-wifi-access-point)
  - [`start_access_point`](#start_access_point)
- [Servidor HTTP](#servidor-http)
  - [`parse_request`](#parse_requestclient)
  - [`send_all`](#send_allclient-data)
  - [`send_response`](#send_responseclient-status-content_type-body)
  - [`handle_client`](#handle_clientclient)
  - [`make_http_server`](#make_http_server)
- [Red UDP (unicast + multicast)](#red-udp-unicast--multicast)
  - [`handle_udp_command`](#handle_udp_commandudp-data-remote)
  - [`send_sensor_multicast`](#send_sensor_multicastudp)
  - [`make_udp_socket`](#make_udp_socket)
- [Arranque y bucle principal](#arranque-y-bucle-principal)
  - [`run_event_loop`](#run_event_loop)
  - [`main`](#main)
- [Flujos de ejecucion (resumen)](#flujos-de-ejecucion-resumen)

---

## Estado global y constantes

Antes de las funciones, `main.py` define configuracion y estado mutable a nivel
de modulo. Muchas funciones leen/escriben estas variables con `global`.

**Constantes de configuracion:**

| Constante | Valor | Significado |
|---|---|---|
| `AP_SSID` / `AP_PASSWORD` | `"ESP32-Control"` / `"12345678"` | Credenciales del AP WiFi |
| `AP_IP` / `AP_NETMASK` / `AP_GATEWAY` / `AP_DNS` | `192.168.4.1` ... | Configuracion IP del AP |
| `HTTP_PORT` | `80` | Puerto del servidor web |
| `HBRIDGES` | dict | Pines de los 3 puentes H (`ena`, `in1`, `in2`, opcional `reverse`) |
| `PWM_FREQ` | `1000` | Frecuencia PWM en Hz para ENA |
| `I2C_BUS` / `I2C_SDA` / `I2C_SCL` | `0` / `21` / `22` | Bus I2C del MPU-6050 |
| `CMD_PORT` | `5006` | Puerto UDP unicast (comandos) |
| `MCAST_GRP` / `MCAST_PORT` | `224.1.1.10` / `5005` | Grupo/puerto multicast (difusion del sensor) |
| `SENSOR_MS` | `100` | Periodo de lectura del sensor |
| `MCAST_MS` | `200` | Periodo de difusion multicast |
| `SCHED_POLL_MS` | `50` | Timeout del `poll()` en el bucle |

**Estado mutable (globals):**

| Variable | Inicial | Para que sirve |
|---|---|---|
| `bridges` | `{}` | Objetos PWM/Pin reales por puente, los crea `init_actuators` |
| `sensor` | `None` | Instancia `MPU6050` (o `None` si no hay sensor) |
| `ap` | `None` | Objeto `network.WLAN` del Access Point |
| `request_count` | `0` | Contador de peticiones HTTP atendidas |
| `motor_speeds` | `{1:50,2:50,3:50}` | Velocidad objetivo (%) de cada puente |
| `last_command` | `"idle"` | Ultimo comando procesado |
| `quake_running` | `False` | Si el sismo esta activo |
| `quake_started_ms` | `0` | Marca de tiempo del inicio del sismo |
| `light_boost_active` | `False` | Si hay una transicion de "light boost" pendiente |
| `light_boost_target_speed` | `40` | Velocidad final del light boost |
| `light_boost_until_ms` | `0` | Momento (ticks) en que el light boost se aplica |
| `boot_ms` | `ticks_ms()` | Marca de arranque (para el uptime) |
| `gyro_value` | `0.0` | Intensidad sismica estimada/medida (g dinamicos) |
| `accel_xyz` | `(0,0,0)` | Ultima aceleracion `(ax,ay,az)` en g |
| `gyro_xyz` | `(0,0,0)` | Ultima velocidad angular `(gx,gy,gz)` en deg/s |
| `mpu_temp` | `0.0` | Ultima temperatura del chip en C |

---

## Actuadores / motores

### Escritura del duty PWM (compatibilidad de API)

**No hay un helper unico** para escribir el duty: cada sitio que mueve motores se
adapta a la API del port de MicroPython (`duty()` de 0-1023 o `duty_u16()` de 16
bits) con `try/except`.

- **`init_actuators`** define una funcion interna `_set_pwm_duty(p, v)` que intenta
  `p.duty(int(v))` y, si falla, `p.duty_u16(int(v) * 64)`; la usa para dejar cada
  PWM en `0` al arrancar.
- **`set_motor_speed`** repite el mismo `try/except` en linea (`pwm.duty()` y, como
  fallback, `pwm.duty_u16()`).
- **`start_quake` / `stop_quake`** escriben directamente con `pwm.duty(...)` (sin
  fallback).

> Por que el fallback: distintos ports/versiones de MicroPython exponen distinta
> API de PWM. El `* 64` reescala de ~1023 a ~65472 para la variante de 16 bits.

### `init_actuators()`

Inicializa los 3 puentes H y deja los motores parados. Se llama una vez al
arranque desde `main()`.

- **Parametros / retorno:** ninguno. Rellena el global `bridges`.
- **Comportamiento (por cada entrada de `HBRIDGES`):**
  1. Crea los pines de direccion `in1` e `in2` como salidas.
  2. Fija el sentido de giro:
     - Sin `reverse`: `IN1=1, IN2=0` (adelante).
     - Con `"reverse": True` (puente 3): `IN1=0, IN2=1`, para que sus motores
       giren en el **mismo sentido fisico** que los otros dos.
  3. Crea el PWM sobre el pin `ena` a `PWM_FREQ`.
  4. Llama a la funcion interna `_set_pwm_duty(pwm, 0)` para arrancar con velocidad cero.
  5. Guarda `{"pwm", "in1", "in2"}` en `bridges[bridge_id]`.
- **Nota de hardware:** asume `ENA=ENB` e `IN1=IN3`, `IN2=IN4` en el modulo, de
  modo que los 2 motores de cada puente giran juntos a la misma velocidad.

### `clamp_speed(value)`

Normaliza una velocidad a un entero seguro en el rango `0..100`.

- **Parametros:** `value` (cualquier cosa convertible a int).
- **Devuelve:** entero entre `0` y `100`.
- **Comportamiento:** intenta `int(value)`; si falla usa `0`; luego recorta con
  `max(0, min(100, value))`. Protege frente a entradas invalidas del JSON.

### `duty_from_speed(speed)`

Convierte un porcentaje de velocidad a duty PWM (0-1023).

- **Parametros:** `speed` (porcentaje).
- **Devuelve:** entero `int(clamp_speed(speed) * 1023 / 100)`.
- **Nota:** primero pasa por `clamp_speed`, asi que un valor fuera de rango no
  produce un duty invalido.

### `set_motor_speed(motor_id, speed)`

Fija la velocidad de **un** puente (id 1, 2 o 3) y la aplica al PWM si el sismo
esta activo.

- **Parametros:** `motor_id` (int o convertible), `speed` (porcentaje).
- **Devuelve:** `True` si el motor existe y se aplico; `False` si el `motor_id`
  no esta en `bridges`.
- **Comportamiento:**
  1. Normaliza `motor_id` y `speed` (`clamp_speed`).
  2. Si el id no existe, devuelve `False` sin tocar nada.
  3. Guarda la velocidad en `motor_speeds[motor_id]`.
  4. Calcula el duty: `duty_from_speed(speed)` **solo si `quake_running`**; si el
     sismo esta detenido aplica `0` (el motor no se mueve aunque se ajuste su
     "velocidad objetivo").
  5. Escribe el duty con `pwm.duty()` (y `pwm.duty_u16()` como fallback, en un `try/except`).
- **Detalle clave:** ajustar la velocidad con el sismo parado **actualiza el
  valor objetivo pero no arranca el motor**; el valor se usara al hacer
  `start_quake`.

### `set_all_motors(speed)`

Aplica la misma velocidad a los 3 puentes.

- **Parametros:** `speed` (porcentaje).
- **Devuelve:** nada.
- **Comportamiento:** itera `motor_speeds` y llama `set_motor_speed` para cada id.

### `apply_motor_payload(values)`

Aplica un diccionario `{motor_id: velocidad}` recibido en un comando.

- **Parametros:** `values` (se espera `dict`; cualquier otra cosa se ignora).
- **Devuelve:** nada.
- **Comportamiento:** por cada par, intenta convertir la clave a `int` (las claves
  JSON suelen venir como strings `"1"`, `"2"`...). Si la clave no es convertible,
  la salta. Llama `set_motor_speed(motor_id, value)`.
- **Uso:** lo invoca `apply_command` con los campos `motors` y `current_state`.

---

## Preset "light boost"

Mecanismo para que el preset *Ligero* tenga un golpe inicial fuerte y luego baje
a una intensidad suave, sin bloquear el bucle (no usa `sleep`).

### `schedule_light_boost(target_speed=40, duration_ms=1000)`

Programa una transicion futura de velocidad.

- **Parametros:** `target_speed` (velocidad final, default 40%), `duration_ms`
  (cuanto esperar antes de aplicarla, default 1000 ms).
- **Devuelve:** nada. Escribe los globals `light_boost_active`,
  `light_boost_target_speed`, `light_boost_until_ms`.
- **Comportamiento:** marca `light_boost_active = True`, guarda la velocidad
  objetivo (pasada por `clamp_speed`) y calcula el instante de disparo con
  `time.ticks_add(ticks_ms(), duration_ms)`.

### `update_light_boost(now_ms=None)`

Comprueba si toca aplicar la transicion programada. Se llama **en cada vuelta**
del bucle de eventos.

- **Parametros:** `now_ms` (opcional; si es `None` usa `time.ticks_ms()`).
- **Devuelve:** nada.
- **Comportamiento:**
  1. Si no hay boost activo, retorna de inmediato (camino barato).
  2. Si ya se alcanzo `light_boost_until_ms` (`ticks_diff >= 0`):
     - Desactiva el boost.
     - Si el sismo sigue activo, aplica `set_all_motors(light_boost_target_speed)`.
- **Por que asi:** es una tarea temporizada **no bloqueante**; el golpe inicial a
  99% y la caida a 40% ocurren sin detener el servidor.

---

## Sensor / giroscopio

### `update_gyro_estimate()`

Estimacion de intensidad **sin sensor real** (fallback).

- **Devuelve:** nada. Escribe `gyro_value`.
- **Comportamiento:** si el sismo no corre, `gyro_value = 0.0`. Si corre, calcula
  la velocidad media de los motores y la escala: `gyro_value = (avg/100) * 1.4`.
  Asi la UI muestra una "intensidad" coherente aunque no haya MPU-6050.

### `update_gyro()`

Lectura principal del sensor; **fuente de verdad** de `gyro_value`, `accel_xyz`,
`gyro_xyz` y `mpu_temp`.

- **Devuelve:** nada. Escribe esos 4 globals.
- **Comportamiento:**
  1. Si hay sensor presente (`sensor.present`):
     - Lee aceleracion y giro; los redondea y guarda en `accel_xyz` / `gyro_xyz`.
     - Intenta leer temperatura (en un `try` propio: si falla, conserva la
       anterior).
     - Calcula la **intensidad** como aceleracion dinamica:
       `magnitude = sqrt(ax^2+ay^2+az^2)`, `intensity = |magnitude - 1.0|`
       (resta ~1 g de gravedad).
     - Suaviza con filtro exponencial si el sismo corre:
       `gyro_value = 0.6*gyro_value + 0.4*intensity`; si esta parado usa la
       intensidad cruda.
     - `return`.
  2. Si la lectura lanza excepcion: imprime el error, marca
     `sensor.present = False` (degrada a estimacion para futuras llamadas).
  3. Si no hay sensor (o acaba de fallar), llama `update_gyro_estimate()`.
- **Frecuencia:** se invoca cada `SENSOR_MS` en el bucle, y tambien antes de
  responder `/api/status` y `/api/debug`.

### `init_sensor()`

Inicializa el bus I2C y crea la instancia del sensor. Se llama una vez al
arranque.

- **Devuelve:** nada. Escribe el global `sensor`.
- **Comportamiento:** crea `I2C(I2C_BUS, scl, sda, freq=400000)`, instancia
  `MPU6050(i2c)` e imprime si se detecto o no. Si algo falla, deja `sensor = None`
  y el sistema usara la estimacion. **Nunca aborta el arranque** por falta de
  sensor.

---

## Logica de terremoto

### `start_quake()`

Arranca el sismo: pone en marcha los motores con sus velocidades objetivo.

- **Devuelve:** nada. Escribe `quake_running`, `quake_started_ms`.
- **Comportamiento:** marca `quake_running = True`, guarda el instante de inicio,
  recorre `motor_speeds` y aplica `pwm.duty(duty_from_speed(speed))` a cada
  puente, y refresca el giroscopio con `update_gyro()`.

### `stop_quake()`

Detiene el sismo: para todos los motores.

- **Devuelve:** nada. Escribe `quake_running`.
- **Comportamiento:** marca `quake_running = False`, pone el PWM de cada puente a
  `0` con `pwm.duty(0)` y llama `update_gyro()` (que dejara la intensidad en ~0).
- **Nota:** no borra `motor_speeds`; las velocidades objetivo se conservan para el
  siguiente arranque.

### `elapsed_seconds()`

Segundos transcurridos desde el inicio del sismo.

- **Devuelve:** `0` si no hay sismo; si lo hay,
  `ticks_diff(ticks_ms(), quake_started_ms) // 1000`.
- **Uso:** lo expone `status_payload` para el timer de la UI.

### `apply_command(data)`

**Nucleo de control.** Interpreta un comando (venga por HTTP o por UDP) y ejecuta
la accion fisica. Es el punto principal de extension del firmware.

- **Parametros:** `data` (dict ya parseado del JSON).
- **Devuelve:** `False` si no hay campo `command` valido; `True` si se proceso.
- **Comportamiento:**
  1. Normaliza el comando: `str(...).strip().lower()`. Si queda vacio, `False`.
  2. Aplica primero las velocidades del payload si vienen: `motors` y/o
     `current_state` via `apply_motor_payload` (asi el estado de los sliders se
     sincroniza antes de actuar).
  3. Despacha segun el comando:
     | Comando | Accion |
     |---|---|
     | `start` | `start_quake()` |
     | `stop` | `stop_quake()` |
     | `preset_light` | 99% + `start_quake()` + `schedule_light_boost(40,1000)` |
     | `preset_moderate` | 65% + `start_quake()` |
     | `preset_intense` | 99% + `start_quake()` |
     | `set_speed` | `set_all_motors` (si `motor=="all"`) o `set_motor_speed` |
  4. Guarda `last_command`, imprime traza y devuelve `True`.
- **Nota:** un comando desconocido no hace nada en el despacho, pero igualmente se
  registra como `last_command` y devuelve `True` (porque traia un `command` no
  vacio).

---

## Serializacion de estado (JSON)

### `motors_list()`

Devuelve las velocidades de los 3 canales como **lista** `[m1, m2, m3]`.

- **Parametros / retorno:** ninguno; devuelve `[motor_speeds.get(1,0),
  motor_speeds.get(2,0), motor_speeds.get(3,0)]`.
- **Por que existe:** la ESP32 **receptora/visualizadora** parsea `doc["motors"]`
  como `JsonArray`. Si se enviara el dict `motor_speeds` directamente, JSON lo
  serializaria como objeto `{"1":..,"2":..,"3":..}` y al receptor le llegaria
  `null` (no pintaria las barras de motores). Por eso `status_payload()` usa este
  helper para emitir un **array** en orden fijo de canal (`Puente H 1/2/3`).
- **Nota:** construye la lista con `get(...)` y orden explicito, sin depender del
  orden de iteracion del dict.

### `status_payload(ok=True)`

Construye el diccionario de **estado** que se envia por HTTP (`/api/status`,
`/api/control`) y por UDP (unicast de respuesta y difusion multicast).

- **Parametros:** `ok` (bandera de exito, default `True`).
- **Devuelve:** dict con `ok`, `last_command`, `running`, `elapsed_seconds`,
  `gyro`, `accel{x,y,z}`, `gyro_axes{x,y,z}`, `temp`, y `motors` como
  **array `[m1,m2,m3]`** (via `motors_list()`).
- **Nota:** **lee** estado, no lo modifica. La UI consume estos campos para el
  timer, el indicador de estado, los ejes y las velocidades; el web UI lee
  `motors` con el helper `readMotor()`, que acepta array u objeto.
- **Asimetria de formato:** el `motors` **saliente** es array; en cambio los
  comandos **entrantes** (`apply_command`/`apply_motor_payload`) aceptan `motors`
  como objeto `{"1":..}`.

### `connected_clients()`

Numero de dispositivos conectados al Access Point.

- **Devuelve:** entero con el numero de clientes, o `-1` si no se puede obtener
  (AP no iniciado o el port no soporta `status("stations")`).
- **Comportamiento:** consulta `ap.status("stations")`; admite que el port
  devuelva un entero directo o una lista (usa `len`). Todo dentro de `try` para no
  romper el diagnostico.

### `debug_payload()`

Construye el diccionario de **diagnostico** para `/api/debug`.

- **Devuelve:** dict con `sensor_present`, `ap_ssid`, `ap_ip`, `clients`,
  `request_count`, `free_mem` (`gc.mem_free()`), `uptime_s` (desde `boot_ms`),
  `running`, y las lecturas del sensor (`gyro`, `accel`, `gyro_axes`, `temp`).
- **Uso:** alimenta el modal "Debug" de la web (sensor, WiFi, memoria, uptime...).

---

## Red WiFi (Access Point)

### `start_access_point()`

Levanta el ESP32 como Access Point con IP fija. Se llama una vez al arranque.

- **Devuelve:** el objeto `ap` (tambien lo guarda como global).
- **Comportamiento:**
  1. Crea `network.WLAN(network.AP_IF)`.
  2. Hace `gc.collect()` y registra memoria libre (algunos ports dan
     `OSError: out of memory` al activar WiFi; liberar antes ayuda).
  3. `ap.active(True)` (si lanza `OSError`, lo imprime y **re-lanza**: sin WiFi no
     tiene sentido continuar).
  4. Configura SSID/clave con `authmode=3` (WPA2-PSK por numero, porque no todos
     los ports exponen la constante simbolica).
  5. Fija IP/mascara/gateway/DNS con `ifconfig`.
  6. Espera en bucle (`sleep_ms(100)`) hasta que `ap.active()` sea `True`.
  7. Imprime SSID/clave/URL.

---

## Servidor HTTP

Servidor minimo sobre sockets crudos (no usa libreria web), pensado para muy
poca RAM.

### `parse_request(client)`

Lee y parsea una peticion HTTP entrante.

- **Parametros:** `client` (socket aceptado).
- **Devuelve:** tupla `(method, path, headers, body)`. Si la peticion esta mal
  formada (sin fin de cabeceras) devuelve `("", "", {}, b"")`.
- **Comportamiento:**
  1. Lee en bloques de 512 B hasta encontrar `\r\n\r\n` (fin de cabeceras), con
     tope de 4096 B para no agotar memoria.
  2. Separa cabeceras y cuerpo; decodifica las cabeceras (`utf-8`, ignorando
     errores).
  3. Extrae metodo y ruta de la primera linea.
  4. Construye el dict `headers` (claves en minuscula).
  5. Si hay `content-length`, sigue leyendo hasta completar el cuerpo (necesario
     para los `POST`).

### `send_all(client, data)`

Envia **todos** los bytes por el socket, gestionando envios parciales.

- **Parametros:** `client`; `data` (str o bytes; si es str lo codifica a utf-8).
- **Devuelve:** nada.
- **Comportamiento:** usa un `memoryview` y un bucle `send()` hasta enviar el total
  (o cortar si `send` devuelve 0). Evita asumir que un solo `send` manda todo.

### `send_response(client, status, content_type, body)`

Envia una respuesta HTTP completa (cabeceras + cuerpo) para contenido **en
memoria** (JSON, texto).

- **Parametros:** `status` (codigo numerico), `content_type`, `body` (str/bytes).
- **Devuelve:** nada.
- **Comportamiento:** traduce el codigo a su frase (`200 OK`, `404 Not Found`...),
  arma cabeceras con `Content-Length`, `Connection: close` y CORS
  (`Access-Control-Allow-Origin: *`), y envia cabeceras + cuerpo con `send_all`.

### `handle_client(client)`

Enrutador HTTP: decide que responder segun metodo + ruta. Se llama por cada
conexion aceptada.

- **Devuelve:** nada. Incrementa el global `request_count`.
- **Comportamiento:**
  1. `gc.collect()` (liberar memoria antes de procesar) y `request_count += 1`.
  2. Parsea con `parse_request`.
  3. Rutas:
     | Metodo / ruta | Respuesta |
     |---|---|
     | `GET /` | `send_response(..., HTML_PAGE)` (pagina embebida) |
     | `GET /api/status` | `update_gyro()` + JSON de `status_payload()` |
     | `GET /api/debug` | `update_gyro()` + JSON de `debug_payload()` |
     | `POST /api/control` | parsea JSON; si falla `400`; si no `apply_command` + estado |
     | `OPTIONS *` | `200` vacio (preflight CORS) |
     | resto | `404 Not found` |

### `make_http_server()`

Crea el socket servidor TCP del puerto 80.

- **Devuelve:** el socket servidor.
- **Comportamiento:** crea el socket, activa `SO_REUSEADDR`, hace `bind` a
  `0.0.0.0:HTTP_PORT`, `listen(3)` y lo pone **no bloqueante**
  (`setblocking(False)`) para integrarlo con `select.poll`.

---

## Red UDP (unicast + multicast)

### `handle_udp_command(udp, data, remote)`

Procesa un datagrama de comando recibido por unicast (puerto 5006).

- **Parametros:** `udp` (socket), `data` (bytes recibidos), `remote` (direccion del
  emisor).
- **Devuelve:** nada.
- **Comportamiento:** parsea el JSON (si falla, ignora el datagrama), ejecuta
  `apply_command(payload)` y responde **por unicast al emisor** con
  `status_payload()`. La respuesta va en `try` para no romper si el envio falla.
- **Nota:** usa el **mismo esquema JSON** que `POST /api/control`.

### `send_sensor_multicast(udp)`

Difunde el estado completo del sensor por multicast.

- **Parametros:** `udp` (socket).
- **Devuelve:** nada.
- **Comportamiento:** serializa `status_payload()` (con `motors` como array
  `[m1,m2,m3]`, formato que espera el receptor) y lo envia a
  `(MCAST_GRP, MCAST_PORT)` = `224.1.1.10:5005`. Varios receptores pueden
  suscribirse. Todo en `try` (no critico).

### `make_udp_socket()`

Crea el socket UDP de comandos/difusion.

- **Devuelve:** el socket UDP.
- **Comportamiento:** socket `AF_INET/SOCK_DGRAM`, `SO_REUSEADDR`, `bind` a
  `0.0.0.0:CMD_PORT`, no bloqueante. Intenta fijar el TTL multicast con
  `setsockopt(IPPROTO_IP, 3, 2)` (numeros crudos porque no todos los ports
  exponen la constante); si falla, lo ignora.

---

## Arranque y bucle principal

### `run_event_loop()`

Bucle infinito que multiplexa HTTP + UDP y ejecuta tareas periodicas. Es el
corazon del firmware; no retorna.

- **Comportamiento:**
  1. Crea los sockets HTTP y UDP y los registra en un `select.poll`.
  2. Imprime las URLs/puertos activos.
  3. En cada iteracion:
     - `poller.poll(SCHED_POLL_MS)` espera eventos hasta 50 ms.
     - Si hay evento en el **servidor HTTP**: `accept()`, `settimeout(5)`,
       `handle_client()` y cierra el cliente en `finally` (con manejo de errores).
     - Si hay evento en el **socket UDP**: `recvfrom(1024)` y
       `handle_udp_command()`.
     - **Tareas por tiempo** (con `ticks_diff`):
       - cada `SENSOR_MS` (100 ms): `update_gyro()`.
       - en cada vuelta: `update_light_boost(now)`.
       - cada `MCAST_MS` (200 ms): `send_sensor_multicast()` + `gc.collect()`.
- **Diseno:** un solo hilo, cooperativo, no bloqueante. El `poll` con timeout
  permite atender red y, entre medias, ejecutar las tareas temporizadas.

### `main()`

Punto de entrada. Orquesta el arranque y entra al bucle.

- **Comportamiento (en orden):**
  1. `init_actuators()` — prepara motores (parados).
  2. `init_sensor()` — intenta el MPU-6050.
  3. `start_access_point()` — levanta el WiFi.
  4. `run_event_loop()` — sirve para siempre.
- **Invocacion:** la ultima linea del fichero es `main()`, asi que el firmware
  arranca solo al importarse `main.py` en el boot del ESP32.

---

## Flujos de ejecucion (resumen)

**Arranque:** `main()` -> `init_actuators` -> `init_sensor` ->
`start_access_point` -> `run_event_loop` (infinito).

**Peticion web de control:** navegador `POST /api/control` -> `handle_client` ->
`parse_request` -> `apply_command` (-> `start_quake`/`set_all_motors`/...) ->
`status_payload` -> `send_response`.

**Carga de la pagina:** navegador `GET /` -> `handle_client` -> `send_response`
(pagina embebida `HTML_PAGE`).

**Comando por UDP:** datagrama a `:5006` -> `run_event_loop` ->
`handle_udp_command` -> `apply_command` -> respuesta unicast con `status_payload`.

**Telemetria:** cada 100 ms `update_gyro` refresca el estado; cada 200 ms
`send_sensor_multicast` lo difunde a `224.1.1.10:5005`.

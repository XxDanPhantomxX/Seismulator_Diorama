import gc
import json
import os
import socket
import select
import time

import network
from machine import Pin, PWM, I2C

from mpu6050 import MPU6050


AP_SSID = "ESP32-Control"
AP_PASSWORD = "12345678"
AP_IP = "192.168.4.1"
AP_NETMASK = "255.255.255.0"
AP_GATEWAY = "192.168.4.1"
AP_DNS = "8.8.8.8"
HTTP_PORT = 80
INDEX_HTML = "index.html"   # archivo de la UI servido en GET / (subir a la placa junto a main.py)
HTTP_CHUNK = 512            # tamano de bloque al servir archivos por streaming

# --- Puentes H: cada puente mueve 2 motores DC a la misma velocidad ---
# Conexion asumida: ENA y ENB del modulo unidos al mismo pin PWM,
# IN1=IN3 y IN2=IN4 (los 2 motores giran juntos, direccion fija "adelante").
HBRIDGES = {
    1: {"ena": 25, "in1": 32, "in2": 33},
    2: {"ena": 26, "in1": 16, "in2": 17},
  3: {"ena": 27, "in1": 13, "in2": 14, "reverse": True},
}
PWM_FREQ = 1000

# --- Sensor MPU-6050 / GY-521 (I2C) ---
I2C_BUS = 0
I2C_SDA = 21
I2C_SCL = 22

# --- Red UDP: unicast de comandos a motores, multicast de difusion del sensor ---
CMD_PORT = 5006          # unicast: recibe comandos (mismo JSON que /api/control)
MCAST_GRP = "224.1.1.10"
MCAST_PORT = 5005        # multicast: difunde el estado/sensor
SENSOR_MS = 100          # periodo de lectura del MPU-6050
SENSOR_RETRY_MS = 2000   # periodo para reintentar detectar el MPU-6050 si esta ausente
MCAST_MS = 200           # periodo de difusion multicast del sensor
SCHED_POLL_MS = 50       # timeout del poll en el bucle principal


bridges = {}
sensor = None
ap = None
request_count = 0
motor_speeds = {1: 50, 2: 50, 3: 50}
last_command = "idle"
quake_running = False
quake_started_ms = 0
light_boost_active = False
light_boost_target_speed = 40
light_boost_until_ms = 0
boot_ms = time.ticks_ms()
sensor_retry_ms = 0           # ultimo intento de re-deteccion del MPU-6050
gyro_value = 0.0
accel_xyz = (0.0, 0.0, 0.0)   # aceleracion (ax, ay, az) en g
gyro_xyz = (0.0, 0.0, 0.0)    # velocidad angular (gx, gy, gz) en deg/s
mpu_temp = 0.0                # temperatura del chip en C


def _pwm_write_duty(pwm, duty_val):
    """API clasica de MicroPython: duty entero 0-1023."""
    pwm.duty(duty_val)


def _pwm_write_u16(pwm, duty_val):
    """Ports con PWM de 16 bits: escala 0-1023 -> 0-65472."""
    pwm.duty_u16(duty_val * 64)


# Escritor de duty activo. La API real de la placa se detecta una sola vez en
# init_actuators y se fija aqui, para no repetir el try/except de deteccion en
# cada escritura de PWM (camino caliente: set_motor_speed / start/stop_quake).
_pwm_write = _pwm_write_duty


def init_actuators():
    global _pwm_write

    resolved = False
    for bridge_id, pins in HBRIDGES.items():
        # Direccion fija "adelante" para los 2 motores del puente
        in1 = Pin(pins["in1"], Pin.OUT)
        in2 = Pin(pins["in2"], Pin.OUT)
        if pins.get("reverse"):
            in1.value(0)
            in2.value(1)
        else:
            in1.value(1)
            in2.value(0)

        # ENA (unido a ENB) controla la velocidad de los 2 motores
        pwm = PWM(Pin(pins["ena"]), freq=PWM_FREQ)

        # Detecta la API de PWM una sola vez (con el primer puente): si el objeto
        # expone .duty() se usa el rango 0-1023; si no, se cae a duty_u16().
        if not resolved:
            _pwm_write = _pwm_write_duty if hasattr(pwm, "duty") else _pwm_write_u16
            resolved = True

        _pwm_write(pwm, 0)
        bridges[bridge_id] = {"pwm": pwm, "in1": in1, "in2": in2}


def clamp_speed(value):
    try:
        value = int(value)
    except Exception:
        value = 0
    return max(0, min(100, value))


def duty_from_speed(speed):
    return int(clamp_speed(speed) * 1023 / 100)


def set_motor_speed(motor_id, speed):
    motor_id = int(motor_id)
    speed = clamp_speed(speed)
    if motor_id not in bridges:
        return False

    motor_speeds[motor_id] = speed
    duty_val = duty_from_speed(speed) if quake_running else 0
    _pwm_write(bridges[motor_id]["pwm"], duty_val)
    return True


def set_all_motors(speed):
    for motor_id in motor_speeds:
        set_motor_speed(motor_id, speed)


def schedule_light_boost(target_speed=40, duration_ms=1000):
  global light_boost_active, light_boost_target_speed, light_boost_until_ms

  light_boost_active = True
  light_boost_target_speed = clamp_speed(target_speed)
  light_boost_until_ms = time.ticks_add(time.ticks_ms(), duration_ms)


def update_light_boost(now_ms=None):
  global light_boost_active

  if not light_boost_active:
    return

  if now_ms is None:
    now_ms = time.ticks_ms()

  if time.ticks_diff(now_ms, light_boost_until_ms) >= 0:
    light_boost_active = False
    if quake_running:
      set_all_motors(light_boost_target_speed)


def apply_motor_payload(values):
    if not isinstance(values, dict):
        return

    for key, value in values.items():
        try:
            motor_id = int(key)
        except Exception:
            continue
        set_motor_speed(motor_id, value)


def update_gyro_estimate():
    global gyro_value

    if not quake_running:
        gyro_value = 0.0
        return

    avg_speed = sum(motor_speeds.values()) / len(motor_speeds)
    gyro_value = round((avg_speed / 100) * 1.4, 2)


def update_gyro():
    """Lee el MPU-6050 real si esta presente; si no, usa la estimacion."""
    global gyro_value, accel_xyz, gyro_xyz, mpu_temp, sensor_retry_ms

    if sensor is not None:
        # Si el sensor figura como ausente (no detectado al arrancar o tras un
        # fallo de lectura), reintenta detectarlo cada SENSOR_RETRY_MS. Asi un
        # error I2C transitorio no lo deja marcado "No detectado" para siempre.
        if not sensor.present:
            now_ms = time.ticks_ms()
            if time.ticks_diff(now_ms, sensor_retry_ms) >= SENSOR_RETRY_MS:
                sensor_retry_ms = now_ms
                sensor.reinit()

        if sensor.present:
            try:
                ax, ay, az = sensor.read_accel()
                gx, gy, gz = sensor.read_gyro()
                accel_xyz = (round(ax, 3), round(ay, 3), round(az, 3))
                gyro_xyz = (round(gx, 2), round(gy, 2), round(gz, 2))
                try:
                    mpu_temp = round(sensor.read_temp(), 1)
                except Exception:
                    pass
                magnitude = (ax * ax + ay * ay + az * az) ** 0.5
                intensity = abs(magnitude - 1.0)  # g dinamicos (descontada la gravedad)
                if quake_running:
                    gyro_value = round(0.6 * gyro_value + 0.4 * intensity, 2)
                else:
                    gyro_value = round(intensity, 2)
                return
            except Exception as exc:
                print("Error leyendo MPU-6050:", exc)
                sensor.present = False

    update_gyro_estimate()


def start_quake():
    global quake_running, quake_started_ms

    quake_running = True
    quake_started_ms = time.ticks_ms()
    for bridge_id, speed in motor_speeds.items():
        _pwm_write(bridges[bridge_id]["pwm"], duty_from_speed(speed))
    update_gyro()


def stop_quake():
    global quake_running

    quake_running = False
    for bridge in bridges.values():
        _pwm_write(bridge["pwm"], 0)
    update_gyro()


def elapsed_seconds():
    if not quake_running:
        return 0
    return time.ticks_diff(time.ticks_ms(), quake_started_ms) // 1000


def apply_command(data):
    global last_command

    command = str(data.get("command", "")).strip().lower()
    if not command:
        return False

    if "motors" in data:
        apply_motor_payload(data.get("motors"))
    if "current_state" in data:
        apply_motor_payload(data.get("current_state"))

    if command == "start":
        start_quake()
    elif command == "stop":
        stop_quake()
    elif command == "preset_light":
        set_all_motors(99)
        start_quake()
        schedule_light_boost(40, 1000)
    elif command == "preset_moderate":
        set_all_motors(65)
        start_quake()
    elif command == "preset_intense":
        set_all_motors(99)
        start_quake()
    elif command == "set_speed":
        motor = data.get("motor", "all")
        speed = data.get("speed", 0)
        if motor == "all":
            set_all_motors(speed)
        else:
            set_motor_speed(motor, speed)

    last_command = command
    print("Comando recibido:", command, "motores:", motor_speeds)
    return True


def motors_list():
    """Velocidades de los 3 canales como lista [m1, m2, m3].

    El receptor (ESP32 visualizadora) espera doc["motors"] como array JSON;
    por eso se envia lista y no el dict motor_speeds (que serializa a objeto).
    """
    return [motor_speeds.get(1, 0), motor_speeds.get(2, 0), motor_speeds.get(3, 0)]


def status_payload(ok=True):
    return {
        "ok": ok,
        "last_command": last_command,
        "running": quake_running,
        "elapsed_seconds": elapsed_seconds(),
        "gyro": gyro_value,
        "accel": {"x": accel_xyz[0], "y": accel_xyz[1], "z": accel_xyz[2]},
        "gyro_axes": {"x": gyro_xyz[0], "y": gyro_xyz[1], "z": gyro_xyz[2]},
        "temp": mpu_temp,
        "motors": motors_list(),
    }


def connected_clients():
    """Numero de dispositivos conectados al Access Point (si el puerto lo soporta)."""
    if ap is None:
        return -1
    try:
        stations = ap.status("stations")
        if isinstance(stations, int):
            return stations
        return len(stations)
    except Exception:
        return -1


def debug_payload():
    return {
        "ok": True,
        "sensor_present": bool(sensor is not None and getattr(sensor, "present", False)),
        "ap_ssid": AP_SSID,
        "ap_ip": AP_IP,
        "clients": connected_clients(),
        "request_count": request_count,
        "free_mem": gc.mem_free(),
        "uptime_s": time.ticks_diff(time.ticks_ms(), boot_ms) // 1000,
        "running": quake_running,
        "gyro": gyro_value,
        "accel": {"x": accel_xyz[0], "y": accel_xyz[1], "z": accel_xyz[2]},
        "gyro_axes": {"x": gyro_xyz[0], "y": gyro_xyz[1], "z": gyro_xyz[2]},
        "temp": mpu_temp,
    }


def start_access_point():
    global ap
    ap = network.WLAN(network.AP_IF)
    # try to free memory before activating WiFi (some ports return OSError: out of memory)
    try:
        gc.collect()
        print("mem_free before AP:", gc.mem_free())
    except Exception:
        pass

    try:
        ap.active(True)
    except OSError as exc:
        print("OSError starting AP:", exc)
        raise

    # Some ports of MicroPython don't expose AUTH_WPA_WPA2_PSK; use numeric mode for WPA/WPA2
    # authmode values: 0=open, 1=WEP, 2=WPA-PSK, 3=WPA2-PSK (WPA/WPA2 mixed often 3)
    ap.config(essid=AP_SSID, password=AP_PASSWORD, authmode=3)
    ap.ifconfig((AP_IP, AP_NETMASK, AP_GATEWAY, AP_DNS))

    while not ap.active():
        time.sleep_ms(100)

    print("Access Point activo")
    print("SSID:", AP_SSID)
    print("Password:", AP_PASSWORD)
    print("URL: http://{}/".format(AP_IP))
    return ap


def parse_request(client):
    request = b""
    while b"\r\n\r\n" not in request:
        chunk = client.recv(512)
        if not chunk:
            break
        request += chunk
        if len(request) > 4096:
            break

    header_end = request.find(b"\r\n\r\n")
    if header_end < 0:
        return "", "", {}, b""

    header_bytes = request[:header_end]
    body = request[header_end + 4:]
    header_text = header_bytes.decode("utf-8", "ignore")
    lines = header_text.split("\r\n")
    request_line = lines[0].split()

    method = request_line[0] if len(request_line) > 0 else ""
    path = request_line[1] if len(request_line) > 1 else ""
    headers = {}

    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0") or "0")
    while len(body) < content_length:
        chunk = client.recv(min(512, content_length - len(body)))
        if not chunk:
            break
        body += chunk

    return method, path, headers, body


def send_all(client, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    view = memoryview(data)
    total = len(view)
    sent = 0
    while sent < total:
        n = client.send(view[sent:])
        if not n:
            break
        sent += n


def send_response(client, status, content_type, body):
    if isinstance(body, str):
        body = body.encode("utf-8")

    reason = {
        200: "OK",
        400: "Bad Request",
        404: "Not Found",
        405: "Method Not Allowed",
        500: "Internal Server Error",
    }.get(status, "OK")

    headers = (
        "HTTP/1.1 {} {}\r\n"
        "Content-Type: {}\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "\r\n"
    ).format(status, reason, content_type, len(body))

    send_all(client, headers)
    send_all(client, body)


def send_file(client, path, content_type):
    """Sirve un archivo por streaming (sin cargarlo entero en RAM).

    Lee y envia en bloques de HTTP_CHUNK bytes. Si el archivo no existe,
    responde 404. Pensado para servir la UI (index.html) sin embeberla en RAM.
    """
    try:
        size = os.stat(path)[6]   # indice 6 = tamano en bytes
    except OSError:
        send_response(client, 404, "text/plain", "Not found")
        return

    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: {}\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "\r\n"
    ).format(content_type, size)
    send_all(client, headers)

    with open(path, "rb") as f:
        while True:
            chunk = f.read(HTTP_CHUNK)
            if not chunk:
                break
            send_all(client, chunk)


def handle_client(client):
    global request_count

    gc.collect()
    request_count += 1
    method, path, headers, body = parse_request(client)

    if method == "GET" and path == "/":
        send_file(client, INDEX_HTML, "text/html; charset=utf-8")
        return

    if method == "GET" and path == "/api/status":
        update_gyro()
        send_response(client, 200, "application/json", json.dumps(status_payload()))
        return

    if method == "GET" and path == "/api/debug":
        update_gyro()
        send_response(client, 200, "application/json", json.dumps(debug_payload()))
        return

    if method == "POST" and path == "/api/control":
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            send_response(client, 400, "application/json", json.dumps({"ok": False}))
            return

        ok = apply_command(data)
        send_response(client, 200, "application/json", json.dumps(status_payload(ok)))
        return

    if method == "OPTIONS":
        send_response(client, 200, "text/plain", "")
        return

    send_response(client, 404, "text/plain", "Not found")


def init_sensor():
    global sensor

    try:
        i2c = I2C(I2C_BUS, scl=Pin(I2C_SCL), sda=Pin(I2C_SDA), freq=400000)
        sensor = MPU6050(i2c)
        if sensor.present:
            print("MPU-6050 detectado por I2C")
        else:
            print("MPU-6050 NO detectado: se usara la estimacion")
    except Exception as exc:
        sensor = None
        print("Error inicializando MPU-6050:", exc)


def handle_udp_command(udp, data, remote):
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception:
        return
    apply_command(payload)
    # Respuesta unicast con el estado al emisor del comando
    try:
        udp.sendto(json.dumps(status_payload()).encode("utf-8"), remote)
    except Exception:
        pass


def send_sensor_multicast(udp):
    try:
        body = json.dumps(status_payload()).encode("utf-8")
        udp.sendto(body, (MCAST_GRP, MCAST_PORT))
    except Exception:
        pass


def make_http_server():
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(socket.getaddrinfo("0.0.0.0", HTTP_PORT)[0][-1])
    server.listen(3)
    server.setblocking(False)
    return server


def make_udp_socket():
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.bind(socket.getaddrinfo("0.0.0.0", CMD_PORT)[0][-1])
    udp.setblocking(False)
    try:
        udp.setsockopt(socket.IPPROTO_IP, 3, 2)
    except Exception:
        pass
    return udp


def run_event_loop():
    server = make_http_server()
    udp = make_udp_socket()

    poller = select.poll()
    poller.register(server, select.POLLIN)
    poller.register(udp, select.POLLIN)

    print("Servidor HTTP activo en http://{}:{}/".format(AP_IP, HTTP_PORT))
    print("Comandos UDP unicast en el puerto {}".format(CMD_PORT))
    print("Difusion del sensor por multicast {}:{}".format(MCAST_GRP, MCAST_PORT))

    last_sensor = last_mcast = time.ticks_ms()

    while True:
        try:
            events = poller.poll(SCHED_POLL_MS)
        except Exception:
            events = []

        for obj, flag in events:
            if obj is server:
                client = None
                try:
                    client, remote = server.accept()
                    client.settimeout(5)
                    handle_client(client)
                except Exception as exc:
                    print("Error HTTP:", exc)
                finally:
                    if client:
                        client.close()
            elif obj is udp:
                try:
                    data, remote = udp.recvfrom(1024)
                    handle_udp_command(udp, data, remote)
                except Exception as exc:
                    print("Error UDP:", exc)

        now = time.ticks_ms()
        if time.ticks_diff(now, last_sensor) >= SENSOR_MS:
            update_gyro()
            last_sensor = now
        update_light_boost(now)
        if time.ticks_diff(now, last_mcast) >= MCAST_MS:
            send_sensor_multicast(udp)
            last_mcast = now
            gc.collect()


def main():
    init_actuators()
    init_sensor()
    start_access_point()
    run_event_loop()


main()

import gc
import json
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
gyro_value = 0.0
accel_xyz = (0.0, 0.0, 0.0)   # aceleracion (ax, ay, az) en g
gyro_xyz = (0.0, 0.0, 0.0)    # velocidad angular (gx, gy, gz) en deg/s
mpu_temp = 0.0                # temperatura del chip en C


HTML_PAGE = """<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
    <title>Simulador de Sismos</title>
    <style>
      :root {
        --bg-sky: #87ceeb;
        --bg-ground: #8fbc8f;
        --card-bg: #ffffff;
        --text-dark: #2c3e50;
        --status-idle: #95a5a6;
        --status-light: #2ecc71;
        --status-moderate: #f1c40f;
        --status-intense: #e74c3c;
        --btn-start: #2ecc71;
        --btn-stop: #e74c3c;
        --btn-shadow: rgba(0, 0, 0, 0.15);
      }

      * {
        box-sizing: border-box;
        user-select: none;
      }

      body {
        margin: 0;
        min-height: 100vh;
        font-family: "Comic Sans MS", "Chalkboard SE", system-ui, sans-serif;
        color: var(--text-dark);
        background: linear-gradient(180deg, var(--bg-sky) 0%, var(--bg-sky) 45%, var(--bg-ground) 45%, var(--bg-ground) 100%);
        overflow-x: hidden;
        padding-bottom: 100px;
      }

      body.active-quake .city-container {
        animation: quakeShake var(--shake-speed, 200ms) infinite linear;
      }

      @keyframes quakeShake {
        0% { transform: translate(0, 0) rotate(0deg); }
        25% { transform: translate(3px, -2px) rotate(0.8deg); }
        50% { transform: translate(-3px, 2px) rotate(-0.8deg); }
        75% { transform: translate(2px, 2px) rotate(0.4deg); }
        100% { transform: translate(0, 0) rotate(0deg); }
      }

      main {
        width: min(900px, 100%);
        margin: 0 auto;
        padding: 15px;
      }

      header {
        position: relative;
        display: flex;
        justify-content: center;
        align-items: center;
        padding: 10px 0;
        margin-bottom: 10px;
      }

      h1 {
        font-size: 2.2rem;
        color: #fff;
        text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.3);
        margin: 0;
        text-align: center;
      }

      .header-actions {
        position: absolute;
        right: 10px;
        top: 50%;
        transform: translateY(-50%);
        display: flex;
        gap: 8px;
      }

      .info-chip {
        background: white;
        padding: 8px 14px;
        border-radius: 20px;
        font-weight: bold;
        font-size: 0.95rem;
        cursor: pointer;
        box-shadow: 0 4px 8px var(--btn-shadow);
        border: 3px solid #3498db;
        display: flex;
        align-items: center;
        gap: 6px;
        transition: transform 0.1s;
      }

      .debug-chip {
        border-color: #9b59b6;
      }

      .info-chip:active {
        transform: scale(0.92);
      }

      .city-container {
        position: relative;
        height: 160px;
        margin-bottom: 25px;
        display: flex;
        align-items: flex-end;
        justify-content: center;
        gap: 15px;
        transform-origin: bottom center;
      }

      .sun {
        position: absolute;
        top: 0;
        left: 12%;
        width: 55px;
        height: 55px;
        background: #ffd700;
        border-radius: 50%;
        box-shadow: 0 0 20px #ffd700;
        animation: pulseSun 2s infinite alternate ease-in-out;
      }

      @keyframes pulseSun {
        from { transform: scale(1); }
        to { transform: scale(1.12); }
      }

      .building {
        width: 65px;
        border-radius: 12px 12px 0 0;
        border: 4px solid #34495e;
        border-bottom: none;
        position: relative;
      }

      .building::after {
        content: "";
        position: absolute;
        top: 10px;
        left: 8px;
        right: 8px;
        bottom: 5px;
        background: repeating-linear-gradient(180deg, rgba(255, 255, 255, 0.75) 0 10px, transparent 10px 22px);
      }

      .b1 { height: 95px; background: #3498db; }
      .b2 { height: 135px; background: #9b59b6; }
      .b3 { height: 75px; background: #e67e22; }

      .panel {
        background: var(--card-bg);
        border-radius: 20px;
        padding: 20px;
        box-shadow: 0 10px 25px var(--btn-shadow);
        margin-bottom: 25px;
        border: 4px solid #fff;
      }

      h2 {
        margin-top: 0;
        text-align: center;
        font-size: 1.5rem;
        margin-bottom: 15px;
      }

      .btn-group {
        display: grid;
        gap: 15px;
        margin-bottom: 15px;
      }

      .btn-presets {
        grid-template-columns: repeat(3, 1fr);
      }

      button.preset-btn {
        border: none;
        border-radius: 16px;
        padding: 14px 10px;
        font-size: 1.15rem;
        font-weight: bold;
        color: white;
        cursor: pointer;
        box-shadow: 0 6px 0 var(--btn-shadow);
        transition: transform 0.1s, box-shadow 0.1s;
        font-family: inherit;
      }

      button.preset-btn:active {
        transform: translateY(4px);
        box-shadow: 0 2px 0 var(--btn-shadow);
      }

      .btn-light { background: var(--status-light); border-bottom: 5px solid #27ae60; }
      .btn-moderate { background: var(--status-moderate); border-bottom: 5px solid #d4ac0d; color: #333; }
      .btn-intense { background: var(--status-intense); border-bottom: 5px solid #c0392b; }

      .motor-container {
        background: #f8f9f9;
        border-radius: 15px;
        padding: 15px;
        margin-bottom: 15px;
        border: 2px dashed #bdc3c7;
      }

      .motor-header {
        display: flex;
        justify-content: space-between;
        font-weight: bold;
        font-size: 1.1rem;
        margin-bottom: 10px;
      }

      .speed-badge {
        background: #3498db;
        color: white;
        padding: 4px 12px;
        border-radius: 15px;
        font-size: 0.95rem;
      }

      input[type="range"] {
        -webkit-appearance: none;
        width: 100%;
        background: transparent;
      }

      input[type="range"]::-webkit-slider-thumb {
        -webkit-appearance: none;
        height: 34px;
        width: 34px;
        border-radius: 50%;
        background: #ff9800;
        cursor: pointer;
        margin-top: -10px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.18);
      }

      input[type="range"]::-webkit-slider-runnable-track {
        width: 100%;
        height: 14px;
        cursor: pointer;
        background: #d5d8dc;
        border-radius: 10px;
      }

      .seismograph {
        height: 45px;
        background: #ecf0f1;
        border-radius: 12px;
        overflow: hidden;
        position: relative;
        margin-top: 15px;
        border: 2px solid #bdc3c7;
      }

      .wave {
        position: absolute;
        width: 200%;
        height: 100%;
        background: repeating-linear-gradient(45deg, transparent, transparent 12px, var(--btn-stop) 12px, var(--btn-stop) 24px);
        opacity: 0.15;
      }

      body.active-quake .wave {
        animation: waveMove var(--shake-speed, 200ms) linear infinite;
        opacity: 1;
      }

      @keyframes waveMove {
        from { transform: translateX(0); }
        to { transform: translateX(-48px); }
      }

      .bottom-bar {
        position: fixed;
        bottom: 15px;
        left: 10px;
        right: 10px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        z-index: 500;
        pointer-events: none;
      }

      .bottom-bar > * {
        pointer-events: auto;
      }

      .widget-time,
      .widget-status {
        background: white;
        padding: 10px 14px;
        border-radius: 20px;
        font-weight: bold;
        font-size: 1.1rem;
        box-shadow: 0 6px 15px rgba(0, 0, 0, 0.15);
        border: 3px solid white;
        display: flex;
        align-items: center;
        gap: 6px;
      }

      .widget-status {
        min-width: 110px;
        justify-content: center;
        transition: background-color 0.2s, color 0.2s, border-color 0.2s;
      }

      .widget-toggle {
        padding: 12px 24px;
        font-size: 1.25rem;
        font-weight: bold;
        color: white;
        border-radius: 30px;
        border: none;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25);
        cursor: pointer;
        transition: all 0.1s ease;
        font-family: inherit;
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .widget-toggle:active {
        transform: translateY(4px);
        box-shadow: 0 4px 10px rgba(0, 0, 0, 0.2);
      }

      .btn-toggle-start { background: var(--btn-start); border-bottom: 5px solid #27ae60; }
      .btn-toggle-stop { background: var(--btn-stop); border-bottom: 5px solid #c0392b; animation: pulseAction 1.2s infinite alternate ease-in-out; }

      @keyframes pulseAction {
        from { box-shadow: 0 6px 15px rgba(231, 76, 60, 0.4); }
        to { box-shadow: 0 6px 25px rgba(231, 76, 60, 0.8); }
      }

      .status-idle { color: var(--status-idle); border-color: #bdc3c7; }
      .status-light { background: var(--status-light); color: white; border-color: #27ae60; }
      .status-moderate { background: var(--status-moderate); color: #333; border-color: #d4ac0d; }
      .status-intense { background: var(--status-intense); color: white; border-color: #c0392b; }

      .modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(44, 62, 80, 0.6);
        display: none;
        justify-content: center;
        align-items: center;
        z-index: 1000;
        backdrop-filter: blur(4px);
        padding: 15px;
      }

      .modal-card {
        background: white;
        border-radius: 24px;
        padding: 24px;
        width: min(460px, 100%);
        max-height: 88vh;
        overflow-y: auto;
        border: 5px solid #34495e;
        position: relative;
        box-shadow: 0 12px 35px rgba(0, 0, 0, 0.35);
        animation: popIn 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        text-align: center;
      }

      .stat-ok { color: #27ae60; font-weight: bold; }
      .stat-bad { color: #e74c3c; font-weight: bold; }

      .modal-refresh {
        margin-top: 15px;
        width: 100%;
        border: none;
        border-radius: 16px;
        padding: 12px;
        font-size: 1.05rem;
        font-weight: bold;
        color: white;
        background: #3498db;
        border-bottom: 5px solid #2980b9;
        cursor: pointer;
        font-family: inherit;
      }

      .modal-refresh:active { transform: translateY(3px); }

      @keyframes popIn {
        from { transform: scale(0.85); opacity: 0; }
        to { transform: scale(1); opacity: 1; }
      }

      .modal-close {
        position: absolute;
        top: 15px;
        right: 15px;
        background: none;
        border: none;
        font-size: 1.3rem;
        cursor: pointer;
        box-shadow: none;
        padding: 0;
      }

      .modal-card h3 {
        margin-top: 5px;
        font-size: 1.4rem;
        color: #2c3e50;
      }

      .modal-card p {
        font-size: 0.95rem;
        line-height: 1.4;
        color: #566573;
        margin-bottom: 15px;
      }

      .modal-stats {
        background: #f4f6f6;
        border-radius: 16px;
        padding: 15px;
        display: grid;
        gap: 12px;
        text-align: left;
        border: 2px dashed #bdc3c7;
      }

      .stat-item {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        font-size: 1.05rem;
        border-bottom: 1px dashed #d5d8dc;
        padding-bottom: 6px;
      }

      .stat-item:last-child {
        border-bottom: none;
        padding-bottom: 0;
      }

      @media (max-width: 480px) {
        h1 { font-size: 1.7rem; }
        .info-chip { position: static; transform: none; margin-left: 10px; padding: 6px 10px; }
        .widget-time { font-size: 0.9rem; padding: 8px 10px; }
        .widget-toggle { font-size: 1.05rem; padding: 10px 14px; }
        .widget-status { font-size: 0.9rem; padding: 8px 10px; min-width: 86px; }
        .btn-presets { gap: 8px; }
        button.preset-btn { font-size: 0.9rem; padding: 10px 5px; }
      }
    </style>
  </head>
  <body>
    <main>
      <header>
        <h1>Simulador de Sismos</h1>
        <div class="header-actions">
          <div class="info-chip" onclick="openModal()">Info</div>
          <div class="info-chip debug-chip" onclick="openDebug()">Debug</div>
        </div>
      </header>

      <div class="city-container" aria-hidden="true">
        <div class="sun"></div>
        <div class="building b1"></div>
        <div class="building b2"></div>
        <div class="building b3"></div>
      </div>

      <div class="panel">
        <h2>Control Rapido</h2>

        <div class="btn-group btn-presets">
          <button class="preset-btn btn-light" onclick="applyPreset('light')">Ligero</button>
          <button class="preset-btn btn-moderate" onclick="applyPreset('moderate')">Medio</button>
          <button class="preset-btn btn-intense" onclick="applyPreset('intense')">Fuerte</button>
        </div>

        <div class="seismograph">
          <div class="wave"></div>
        </div>
      </div>

      <div class="panel">
        <h2>Configurar Motores</h2>

        <div class="motor-container">
          <div class="motor-header">
            <span>Todos los motores</span>
            <span class="speed-badge"><span id="globalSpeedValue">50</span>%</span>
          </div>
          <input id="globalSpeed" type="range" min="35" max="100" value="50" oninput="setGlobalSpeed(this.value)" onchange="sendMotorSpeed('all', this.value)">
        </div>

        <details style="cursor:pointer;text-align:center;color:#7f8c8d;font-weight:bold;margin-top:10px">
          <summary>Ajustes de motores individuales</summary>
          <div style="margin-top:15px;text-align:left">
            <div class="motor-container">
              <div class="motor-header">
                <span>Motor 1</span><span class="speed-badge"><span id="motor1Value">50</span>%</span>
              </div>
              <input id="motor1" type="range" min="35" max="100" value="50" oninput="setMotorSpeed(1, this.value)" onchange="sendMotorSpeed(1, this.value)">
            </div>
            <div class="motor-container">
              <div class="motor-header">
                <span>Motor 2</span><span class="speed-badge"><span id="motor2Value">50</span>%</span>
              </div>
              <input id="motor2" type="range" min="35" max="100" value="50" oninput="setMotorSpeed(2, this.value)" onchange="sendMotorSpeed(2, this.value)">
            </div>
            <div class="motor-container">
              <div class="motor-header">
                <span>Motor 3</span><span class="speed-badge"><span id="motor3Value">50</span>%</span>
              </div>
              <input id="motor3" type="range" min="35" max="100" value="50" oninput="setMotorSpeed(3, this.value)" onchange="sendMotorSpeed(3, this.value)">
            </div>
          </div>
        </details>
      </div>
    </main>

    <div class="bottom-bar">
      <div class="widget-time">Tiempo <span id="timerValue">00:00</span></div>

      <button id="mainToggleBtn" class="widget-toggle btn-toggle-start" onclick="toggleQuake()">Iniciar</button>

      <div id="statusWidget" class="widget-status status-idle">Seguro</div>
    </div>

    <div id="infoModal" class="modal-overlay" onclick="handleOverlayClick(event)">
      <div class="modal-card">
        <button class="modal-close" onclick="closeModal()">X</button>
        <h3>Estado de la Maqueta</h3>
        <p>Este panel controla los motores para simular ondas sismicas en tiempo real.</p>

        <div class="modal-stats">
          <div class="stat-item">
            <strong>Conexion:</strong>
            <span id="modalConnection">Local/Preview</span>
          </div>
          <div class="stat-item">
            <strong>Velocidad Promedio:</strong>
            <span id="modalSpeed">50%</span>
          </div>
          <div class="stat-item">
            <strong>Intensidad:</strong>
            <span id="modalGyro">0.00 g</span>
          </div>
          <div class="stat-item">
            <strong>Accel X:</strong>
            <span id="modalAx">0.000 g</span>
          </div>
          <div class="stat-item">
            <strong>Accel Y:</strong>
            <span id="modalAy">0.000 g</span>
          </div>
          <div class="stat-item">
            <strong>Accel Z:</strong>
            <span id="modalAz">0.000 g</span>
          </div>
          <div class="stat-item">
            <strong>Giro X:</strong>
            <span id="modalGx">0.00 /s</span>
          </div>
          <div class="stat-item">
            <strong>Giro Y:</strong>
            <span id="modalGy">0.00 /s</span>
          </div>
          <div class="stat-item">
            <strong>Giro Z:</strong>
            <span id="modalGz">0.00 /s</span>
          </div>
          <div class="stat-item">
            <strong>Temperatura:</strong>
            <span id="modalTemp">-- C</span>
          </div>
          <div class="stat-item">
            <strong>Ultimo Comando:</strong>
            <span id="modalCommand">idle</span>
          </div>
        </div>
      </div>
    </div>

    <div id="debugModal" class="modal-overlay" onclick="handleOverlayClick(event)">
      <div class="modal-card">
        <button class="modal-close" onclick="closeDebug()">X</button>
        <h3>Diagnostico del Sistema</h3>
        <p>Revisa que cada componente de la maqueta responda correctamente.</p>

        <div class="modal-stats">
          <div class="stat-item">
            <strong>Sensor MPU-6050:</strong>
            <span id="dbgSensor">--</span>
          </div>
          <div class="stat-item">
            <strong>WiFi (AP):</strong>
            <span id="dbgWifi">--</span>
          </div>
          <div class="stat-item">
            <strong>Clientes conectados:</strong>
            <span id="dbgClients">--</span>
          </div>
          <div class="stat-item">
            <strong>Peticiones HTTP:</strong>
            <span id="dbgRequests">--</span>
          </div>
          <div class="stat-item">
            <strong>Memoria libre:</strong>
            <span id="dbgMem">--</span>
          </div>
          <div class="stat-item">
            <strong>Tiempo encendido:</strong>
            <span id="dbgUptime">--</span>
          </div>
          <div class="stat-item">
            <strong>Sismo activo:</strong>
            <span id="dbgRunning">--</span>
          </div>
          <div class="stat-item">
            <strong>Accel (x,y,z):</strong>
            <span id="dbgAccel">--</span>
          </div>
          <div class="stat-item">
            <strong>Giro (x,y,z):</strong>
            <span id="dbgGyro">--</span>
          </div>
          <div class="stat-item">
            <strong>Temperatura:</strong>
            <span id="dbgTemp">--</span>
          </div>
        </div>

        <button class="modal-refresh" onclick="refreshDebug()">Actualizar</button>
      </div>
    </div>

    <script>
      const timerValue = document.getElementById("timerValue");
      const statusWidget = document.getElementById("statusWidget");
      const mainToggleBtn = document.getElementById("mainToggleBtn");
      const modalSpeed = document.getElementById("modalSpeed");
      const modalGyro = document.getElementById("modalGyro");
      const modalCommand = document.getElementById("modalCommand");
      const modalConnection = document.getElementById("modalConnection");
      const modalAx = document.getElementById("modalAx");
      const modalAy = document.getElementById("modalAy");
      const modalAz = document.getElementById("modalAz");
      const modalGx = document.getElementById("modalGx");
      const modalGy = document.getElementById("modalGy");
      const modalGz = document.getElementById("modalGz");
      const modalTemp = document.getElementById("modalTemp");

      let isRunning = false;
      let elapsedSeconds = 0;
      let timerInterval = null;
      let demoGyroInterval = null;
      let speeds = { 1: 50, 2: 50, 3: 50 };
      let currentGyroValue = 0.0;
      let lastSentCommand = "idle";
      let sensorData = {
        accel: { x: 0, y: 0, z: 0 },
        gyro_axes: { x: 0, y: 0, z: 0 },
        temp: null,
      };

      function storeSensorData(data) {
        if (data.accel) sensorData.accel = data.accel;
        if (data.gyro_axes) sensorData.gyro_axes = data.gyro_axes;
        if (typeof data.temp === "number") sensorData.temp = data.temp;
      }

      function formatTime(totalSeconds) {
        const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
        const seconds = String(totalSeconds % 60).padStart(2, "0");
        return `${minutes}:${seconds}`;
      }

      function setRunningState(running) {
        isRunning = running;
        document.body.classList.toggle("active-quake", running);

        clearInterval(timerInterval);
        clearInterval(demoGyroInterval);

        if (running) {
          mainToggleBtn.innerHTML = "Detener";
          mainToggleBtn.className = "widget-toggle btn-toggle-stop";

          timerInterval = setInterval(() => {
            elapsedSeconds++;
            timerValue.textContent = formatTime(elapsedSeconds);
          }, 1000);

          demoGyroInterval = setInterval(() => {
            const avgSpeed = (speeds[1] + speeds[2] + speeds[3]) / 3;
            const amp = avgSpeed / 100;
            currentGyroValue = (amp * 1.4 + Math.random() * 0.08).toFixed(2);
            sensorData = {
              accel: {
                x: +((Math.random() - 0.5) * amp).toFixed(3),
                y: +((Math.random() - 0.5) * amp).toFixed(3),
                z: +(1 + (Math.random() - 0.5) * amp * 0.5).toFixed(3),
              },
              gyro_axes: {
                x: +((Math.random() - 0.5) * amp * 60).toFixed(2),
                y: +((Math.random() - 0.5) * amp * 60).toFixed(2),
                z: +((Math.random() - 0.5) * amp * 60).toFixed(2),
              },
              temp: +(28 + Math.random()).toFixed(1),
            };
            updateStatusUI(currentGyroValue);
            updateLiveModalData();
          }, 500);
        } else {
          mainToggleBtn.innerHTML = "Iniciar";
          mainToggleBtn.className = "widget-toggle btn-toggle-start";

          currentGyroValue = 0.0;
          updateStatusUI(0);
          updateLiveModalData();
        }
      }

      function updateStatusUI(gyroValue) {
        const num = Number(gyroValue);
        const speedMs = Math.max(60, 360 - num * 200);
        document.documentElement.style.setProperty("--shake-speed", `${speedMs}ms`);

        statusWidget.className = "widget-status";

        if (num <= 0.05) {
          statusWidget.innerHTML = "Seguro";
          statusWidget.classList.add("status-idle");
        } else if (num < 0.45) {
          statusWidget.innerHTML = "Ligero";
          statusWidget.classList.add("status-light");
        } else if (num < 0.9) {
          statusWidget.innerHTML = "Medio";
          statusWidget.classList.add("status-moderate");
        } else {
          statusWidget.innerHTML = "Fuerte";
          statusWidget.classList.add("status-intense");
        }
      }

      function toggleQuake() {
        if (isRunning) stopQuake();
        else startQuake();
      }

      function setMotorSpeed(motor, value) {
        speeds[motor] = Number(value);
        document.getElementById(`motor${motor}Value`).textContent = value;
        updateGlobalSliderAverage();
      }

      function setGlobalSpeed(value) {
        document.getElementById("globalSpeedValue").textContent = value;
        [1, 2, 3].forEach((m) => {
          speeds[m] = Number(value);
          const inputElement = document.getElementById(`motor${m}`);
          const valueElement = document.getElementById(`motor${m}Value`);
          if (inputElement) inputElement.value = value;
          if (valueElement) valueElement.textContent = value;
        });
      }

      function updateGlobalSliderAverage() {
        const avg = Math.round((speeds[1] + speeds[2] + speeds[3]) / 3);
        document.getElementById("globalSpeed").value = avg;
        document.getElementById("globalSpeedValue").textContent = avg;
      }

      function openModal() {
        document.getElementById("infoModal").style.display = "flex";
        updateLiveModalData();
      }

      function closeModal() {
        document.getElementById("infoModal").style.display = "none";
      }

      function openDebug() {
        document.getElementById("debugModal").style.display = "flex";
        refreshDebug();
      }

      function closeDebug() {
        document.getElementById("debugModal").style.display = "none";
      }

      function handleOverlayClick(event) {
        if (event.target.classList.contains("modal-overlay")) {
          event.target.style.display = "none";
        }
      }

      function updateLiveModalData() {
        const avgSpeed = Math.round((speeds[1] + speeds[2] + speeds[3]) / 3);
        modalSpeed.textContent = `${avgSpeed}%`;
        modalGyro.textContent = `${Number(currentGyroValue).toFixed(2)} g`;
        modalCommand.textContent = lastSentCommand;
        modalAx.textContent = `${Number(sensorData.accel.x).toFixed(3)} g`;
        modalAy.textContent = `${Number(sensorData.accel.y).toFixed(3)} g`;
        modalAz.textContent = `${Number(sensorData.accel.z).toFixed(3)} g`;
        modalGx.textContent = `${Number(sensorData.gyro_axes.x).toFixed(2)} /s`;
        modalGy.textContent = `${Number(sensorData.gyro_axes.y).toFixed(2)} /s`;
        modalGz.textContent = `${Number(sensorData.gyro_axes.z).toFixed(2)} /s`;
        modalTemp.textContent =
          sensorData.temp === null ? "-- C" : `${Number(sensorData.temp).toFixed(1)} C`;
      }

      function setBadge(el, ok, okText, badText) {
        el.textContent = ok ? okText : badText;
        el.className = ok ? "stat-ok" : "stat-bad";
      }

      function formatUptime(totalSeconds) {
        const h = Math.floor(totalSeconds / 3600);
        const m = Math.floor((totalSeconds % 3600) / 60);
        const s = totalSeconds % 60;
        return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
      }

      async function refreshDebug() {
        const f = (n, d = 2) => Number(n ?? 0).toFixed(d);
        try {
          const response = await fetch("/api/debug");
          const d = await response.json();
          storeSensorData(d);
          setBadge(document.getElementById("dbgSensor"), d.sensor_present, "OK", "No detectado");
          setBadge(document.getElementById("dbgWifi"), true, `${d.ap_ssid} (${d.ap_ip})`, "--");
          document.getElementById("dbgClients").textContent =
            d.clients >= 0 ? d.clients : "n/d";
          document.getElementById("dbgRequests").textContent = d.request_count;
          document.getElementById("dbgMem").textContent =
            `${(d.free_mem / 1024).toFixed(1)} KB`;
          document.getElementById("dbgUptime").textContent = formatUptime(d.uptime_s);
          document.getElementById("dbgRunning").textContent = d.running ? "Si" : "No";
          document.getElementById("dbgAccel").textContent =
            `${f(d.accel.x, 3)}, ${f(d.accel.y, 3)}, ${f(d.accel.z, 3)}`;
          document.getElementById("dbgGyro").textContent =
            `${f(d.gyro_axes.x)}, ${f(d.gyro_axes.y)}, ${f(d.gyro_axes.z)}`;
          document.getElementById("dbgTemp").textContent = `${f(d.temp, 1)} C`;
        } catch (error) {
          setBadge(document.getElementById("dbgSensor"), false, "OK", "Sin conexion");
          document.getElementById("dbgWifi").textContent = "Modo Demo";
        }
      }

      async function sendPayload(payload) {
        if (payload.command) {
          lastSentCommand = payload.command;
          modalCommand.textContent = lastSentCommand;
        }
        try {
          const response = await fetch("/api/control", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          const data = await response.json();
          modalConnection.textContent = "ESP32 Conectada";
          if (data.motors) {
            [1, 2, 3].forEach((m) => setMotorSpeed(m, data.motors[m] ?? data.motors[String(m)] ?? speeds[m]));
          }
          if (typeof data.gyro === "number") {
            currentGyroValue = data.gyro;
            updateStatusUI(currentGyroValue);
          }
          storeSensorData(data);
        } catch (error) {
          modalConnection.textContent = "Modo Demo";
        }
        updateLiveModalData();
      }

      function startQuake() {
        elapsedSeconds = 0;
        timerValue.textContent = "00:00";
        setRunningState(true);
        sendPayload({ command: "start", motors: speeds });
      }

      function stopQuake() {
        setRunningState(false);
        sendPayload({ command: "stop", motors: { 1: 0, 2: 0, 3: 0 } });
      }

      function applyPreset(presetName) {
        let speed = 50;
        if (presetName === "light") speed = 35;
        if (presetName === "moderate") speed = 60;
        if (presetName === "intense") speed = 95;

        setGlobalSpeed(speed);
        elapsedSeconds = 0;
        timerValue.textContent = "00:00";
        setRunningState(true);

        sendPayload({ command: "preset_" + presetName, motors: speeds });
      }

      function sendMotorSpeed(motor, value) {
        sendPayload({
          command: "set_speed",
          motor: motor,
          speed: Number(value),
          current_state: speeds,
        });
      }

      async function refreshStatus() {
        try {
          const response = await fetch("/api/status");
          const data = await response.json();
          modalConnection.textContent = "ESP32 Conectada";

          if (typeof data.running === "boolean" && data.running !== isRunning) {
            setRunningState(data.running);
          }
          if (typeof data.elapsed_seconds === "number") {
            elapsedSeconds = data.elapsed_seconds;
            timerValue.textContent = formatTime(elapsedSeconds);
          }
          if (typeof data.gyro === "number") {
            currentGyroValue = data.gyro;
            updateStatusUI(currentGyroValue);
          }
          if (data.last_command) {
            lastSentCommand = data.last_command;
          }
          if (data.motors) {
            [1, 2, 3].forEach((m) => setMotorSpeed(m, data.motors[m] ?? data.motors[String(m)] ?? speeds[m]));
          }
          storeSensorData(data);
          updateLiveModalData();
        } catch (error) {
          modalConnection.textContent = "Modo Demo";
        }
      }

      setInterval(refreshStatus, 3000);
      refreshStatus();
    </script>
  </body>
</html>
"""


def init_actuators():
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
        # Set initial duty handling different PWM APIs (duty, duty_u16)
        def _set_pwm_duty(p, v):
            try:
                # Common MicroPython ESP8266 API (0-1023)
                p.duty(int(v))
                return
            except Exception:
                pass
            try:
                # Some ports use 16-bit duty (0-65535)
                p.duty_u16(int(v) * 64)
                return
            except Exception:
                pass
            try:
                # Newer PWM API (esp32) may use duty or duty_ns; try duty
                p.duty(int(v))
                return
            except Exception:
                pass

        _set_pwm_duty(pwm, 0)
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
    pwm = bridges[motor_id]["pwm"]
    duty_val = duty_from_speed(speed) if quake_running else 0
    # prefer duty (0-1023) but adapt if needed
    try:
      pwm.duty(int(duty_val))
    except Exception:
      try:
        pwm.duty_u16(int(duty_val) * 64)
      except Exception:
        # last resort: ignore if cannot set
        pass
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
    global gyro_value, accel_xyz, gyro_xyz, mpu_temp

    if sensor is not None and sensor.present:
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
        bridges[bridge_id]["pwm"].duty(duty_from_speed(speed))
    update_gyro()


def stop_quake():
    global quake_running

    quake_running = False
    for bridge in bridges.values():
        bridge["pwm"].duty(0)
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
        "motors": motor_speeds,
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
        "HTTP/1.1 {} {}\r\n".format(status, reason)
        + "Content-Type: {}\r\n".format(content_type)
        + "Content-Length: {}\r\n".format(len(body))
        + "Connection: close\r\n"
        + "Access-Control-Allow-Origin: *\r\n"
        + "\r\n"
    )

    send_all(client, headers)
    send_all(client, body)


def handle_client(client):
    global request_count

    gc.collect()
    request_count += 1
    method, path, headers, body = parse_request(client)

    if method == "GET" and path == "/":
        send_response(client, 200, "text/html; charset=utf-8", HTML_PAGE)
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

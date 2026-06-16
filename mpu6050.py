"""Driver minimo para el sensor MPU-6050 / GY-521 (acelerometro + giroscopio).

Lectura por I2C usando registros directos, sin dependencias externas.
Pensado para MicroPython en ESP32.
"""

import struct

# Registros del MPU-6050
_PWR_MGMT_1 = 0x6B
_SMPLRT_DIV = 0x19
_CONFIG = 0x1A
_GYRO_CONFIG = 0x1B
_ACCEL_CONFIG = 0x1C
_ACCEL_XOUT_H = 0x3B
_TEMP_OUT_H = 0x41
_GYRO_XOUT_H = 0x43
_WHO_AM_I = 0x75

# Factores de escala
_ACCEL_SCALE = 16384.0  # +-2g
_GYRO_SCALE = 131.0     # +-250 deg/s


class MPU6050:
    def __init__(self, i2c, addr=0x68):
        self.i2c = i2c
        self.addr = addr
        self._default_addr = addr
        self.present = False
        self.reinit()

    def reinit(self):
        """(Re)detecta y configura el sensor por I2C.

        Se puede llamar tantas veces como haga falta: sirve tanto para la
        deteccion inicial como para recuperar el sensor tras un fallo de lectura
        transitorio (sin tener que reiniciar la placa). Deja `self.present` en
        True/False y devuelve ese valor.
        """
        candidates = (self.addr, self._default_addr, 0x68, 0x69)
        seen = set()

        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                # WHO_AM_I: el MPU-6050 suele responder 0x68; en algunos
                # modulos el pin AD0 lo deja en 0x69.
                self.i2c.readfrom_mem(candidate, _WHO_AM_I, 1)
                # Despertar el sensor (sale del modo sleep)
                self.i2c.writeto_mem(candidate, _PWR_MGMT_1, b"\x00")
                # Filtro paso bajo (~44 Hz) para suavizar la vibracion
                self.i2c.writeto_mem(candidate, _CONFIG, b"\x03")
                self.i2c.writeto_mem(candidate, _SMPLRT_DIV, b"\x04")
                # Rango acelerometro +-2g y giroscopio +-250 deg/s
                self.i2c.writeto_mem(candidate, _ACCEL_CONFIG, b"\x00")
                self.i2c.writeto_mem(candidate, _GYRO_CONFIG, b"\x00")
                self.addr = candidate
                self.present = True
                return True
            except Exception:
                self.present = False

        return False

    def _read3(self, reg):
        data = self.i2c.readfrom_mem(self.addr, reg, 6)
        return struct.unpack(">hhh", data)

    def read_accel(self):
        """Aceleracion (ax, ay, az) en g."""
        x, y, z = self._read3(_ACCEL_XOUT_H)
        return x / _ACCEL_SCALE, y / _ACCEL_SCALE, z / _ACCEL_SCALE

    def read_gyro(self):
        """Velocidad angular (gx, gy, gz) en deg/s."""
        x, y, z = self._read3(_GYRO_XOUT_H)
        return x / _GYRO_SCALE, y / _GYRO_SCALE, z / _GYRO_SCALE

    def read_temp(self):
        """Temperatura interna del chip en grados Celsius."""
        data = self.i2c.readfrom_mem(self.addr, _TEMP_OUT_H, 2)
        raw = struct.unpack(">h", data)[0]
        return raw / 340.0 + 36.53

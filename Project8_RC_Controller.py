# controller.py - runs on the CONTROLLER ESP32 (BLE central)
# Copy to the controller board as main.py (or import it from main.py).
#
# NOTE: do not keep a file called neopixel.py on this board - it would shadow
# MicroPython's built-in neopixel module. Rename your demo file if needed.

import bluetooth
import struct
import neopixel
import uasyncio as asyncio
from machine import Pin, PWM, ADC
from time import ticks_ms, ticks_diff

# =====================================================================
# SETTINGS - everything you may want to change is in this block
# =====================================================================

# ---------- BLE (must match car.py) ----------
DEVICE_NAME = "RC-CAR"
SERVICE_UUID = "6e0a0001-0000-4c0e-9c52-0d1c5a7a0001"
DRIVE_UUID = "6e0a0002-0000-4c0e-9c52-0d1c5a7a0001"    # controller -> car (write)
FINISH_UUID = "6e0a0003-0000-4c0e-9c52-0d1c5a7a0001"   # car -> controller (notify)
SCAN_MS = 5000
SCAN_INTERVAL_US = 30000
SCAN_WINDOW_US = 30000
CONNECT_TIMEOUT_MS = 5000
DISCOVER_TIMEOUT_MS = 5000
RECONNECT_DELAY_MS = 1000

# ---------- Joystick ----------
JOY_X_PIN = 36               # left/right -> steering
JOY_Y_PIN = 39               # up/down    -> throttle
DEADZONE = 5000              # raw ADC units around center that count as "no input"
JOY_FULL_DEFLECTION = 30000  # raw distance from center that counts as 100 %
AUTO_CENTER = True           # measure center at boot (keep the stick untouched!)
JOY_CENTER_X = 30000         # used only if AUTO_CENTER = False
JOY_CENTER_Y = 30000         # used only if AUTO_CENTER = False
INVERT_STEER = True         # flip if pushing right makes the car turn left
INVERT_THROTTLE = True      # flip if pushing up makes the car go backward
JOY_BUTTON_X_ABOVE = 65000   # x above this = button press -> ignored (steer = 0)
JOY_SEND_MS = 50             # how often drive commands are sent

# ---------- Celebration ----------
NEO_PIN = 15
NEO_COUNT = 4
NEO_BRIGHTNESS = 0.2         # 0.0 - 1.0
BUZZER_PIN = 23
BUZZER_DUTY = 32768          # 50 % = loudest clean tone
CELEBRATION_MS = 4000        # how long the light show lasts
CELEB_STEP_MS = 120          # speed of the color chase
CELEB_COLORS = [             # (R, G, B) 0-255, cycles around the LEDs
    (255, 0, 0),
    (255, 120, 0),
    (255, 255, 0),
    (0, 255, 0),
    (0, 200, 255),
    (0, 0, 255),
    (160, 0, 255),
    (255, 0, 140),
]
CELEB_MELODY = [             # (frequency Hz, duration ms); frequency 0 = rest
    (523, 110), (659, 110), (784, 110), (1047, 250),
    (0, 100),
    (784, 110), (1047, 400),
]
MELODY_GAP_MS = 30           # silence after each note
MELODY_REPEATS = 2

# =====================================================================

_IRQ_SCAN_RESULT = 5
_IRQ_SCAN_DONE = 6
_IRQ_PERIPHERAL_CONNECT = 7
_IRQ_PERIPHERAL_DISCONNECT = 8
_IRQ_GATTC_SERVICE_RESULT = 9
_IRQ_GATTC_SERVICE_DONE = 10
_IRQ_GATTC_CHARACTERISTIC_RESULT = 11
_IRQ_GATTC_CHARACTERISTIC_DONE = 12
_IRQ_GATTC_NOTIFY = 18

_SERVICE_UUID = bluetooth.UUID(SERVICE_UUID)
_DRIVE_UUID = bluetooth.UUID(DRIVE_UUID)
_FINISH_UUID = bluetooth.UUID(FINISH_UUID)


# ---------------------------------------------------------------------
# BLE (central)
# ---------------------------------------------------------------------
def _adv_has_name(adv, name):
    adv = bytes(adv)
    i = 0
    while i + 1 < len(adv):
        n = adv[i]
        if n == 0:
            break
        if adv[i + 1] in (0x08, 0x09) and adv[i + 2:i + 1 + n] == name:
            return True
        i += n + 1
    return False


async def _wait_for(cond, timeout_ms):
    t0 = ticks_ms()
    while not cond():
        if ticks_diff(ticks_ms(), t0) > timeout_ms:
            return False
        await asyncio.sleep_ms(20)
    return True


class ControllerBLE:
    def __init__(self):
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self._irq)
        self.finish_flag = False
        self.conn = None
        self.ready = False
        self._clear()

    def _clear(self):
        self.addr_type = None
        self.addr = None
        self.scan_done = False
        self.service_range = None
        self.service_done = False
        self.chars_done = False
        self.h_drive = None
        self.h_finish = None

    @property
    def connected(self):
        return self.conn is not None and self.ready

    def _irq(self, event, data):
        if event == _IRQ_SCAN_RESULT:
            addr_type, addr, adv_type, rssi, adv_data = data
            if self.addr is None and _adv_has_name(adv_data, DEVICE_NAME.encode()):
                self.addr_type = addr_type
                self.addr = bytes(addr)
                self.ble.gap_scan(None)          # stop scanning -> SCAN_DONE
        elif event == _IRQ_SCAN_DONE:
            self.scan_done = True
        elif event == _IRQ_PERIPHERAL_CONNECT:
            conn_handle, addr_type, addr = data
            if self.addr is not None and bytes(addr) == self.addr:
                self.conn = conn_handle
        elif event == _IRQ_PERIPHERAL_DISCONNECT:
            if data[0] == self.conn:
                self.conn = None
                self.ready = False
                print("Disconnected from car")
        elif event == _IRQ_GATTC_SERVICE_RESULT:
            conn_handle, start, end, uuid = data
            if conn_handle == self.conn and uuid == _SERVICE_UUID:
                self.service_range = (start, end)
        elif event == _IRQ_GATTC_SERVICE_DONE:
            self.service_done = True
        elif event == _IRQ_GATTC_CHARACTERISTIC_RESULT:
            conn_handle, def_handle, value_handle, properties, uuid = data
            if conn_handle == self.conn:
                if uuid == _DRIVE_UUID:
                    self.h_drive = value_handle
                elif uuid == _FINISH_UUID:
                    self.h_finish = value_handle
        elif event == _IRQ_GATTC_CHARACTERISTIC_DONE:
            self.chars_done = True
        elif event == _IRQ_GATTC_NOTIFY:
            conn_handle, value_handle, notify_data = data
            if conn_handle == self.conn and value_handle == self.h_finish:
                self.finish_flag = True

    async def connect(self):
        """Scan, connect, discover and subscribe. Returns True when ready."""
        self.ready = False
        self._clear()

        self.ble.gap_scan(SCAN_MS, SCAN_INTERVAL_US, SCAN_WINDOW_US, True)
        await _wait_for(lambda: self.scan_done, SCAN_MS + 1000)
        if self.addr is None:
            return False

        self.ble.gap_connect(self.addr_type, self.addr)
        if not await _wait_for(lambda: self.conn is not None, CONNECT_TIMEOUT_MS):
            try:
                self.ble.gap_connect(None)       # cancel the connection attempt
            except OSError:
                pass
            return False

        self.ble.gattc_discover_services(self.conn)
        await _wait_for(lambda: self.service_done, DISCOVER_TIMEOUT_MS)
        if self.service_range is None:
            self._drop()
            return False

        self.ble.gattc_discover_characteristics(
            self.conn, self.service_range[0], self.service_range[1])
        await _wait_for(lambda: self.chars_done, DISCOVER_TIMEOUT_MS)
        if self.h_drive is None or self.h_finish is None:
            self._drop()
            return False

        # Turn on notifications: the CCCD sits right after the characteristic value.
        try:
            self.ble.gattc_write(self.conn, self.h_finish + 1, b"\x01\x00", 1)
        except OSError as e:
            print("Subscribe failed:", e)
            self._drop()
            return False

        self.ready = True
        return True

    def _drop(self):
        if self.conn is not None:
            try:
                self.ble.gap_disconnect(self.conn)
            except OSError:
                pass

    def send_drive(self, steer, throttle):
        if not self.connected:
            return
        try:
            self.ble.gattc_write(self.conn, self.h_drive, struct.pack("<bb", steer, throttle), 0)
        except OSError:
            pass                                  # link busy / dropping; try again next tick


# ---------------------------------------------------------------------
# Joystick
# ---------------------------------------------------------------------
x_adc = ADC(Pin(JOY_X_PIN))
y_adc = ADC(Pin(JOY_Y_PIN))
x_adc.atten(ADC.ATTN_11DB)
y_adc.atten(ADC.ATTN_11DB)

center_x = JOY_CENTER_X
center_y = JOY_CENTER_Y


async def calibrate_center():
    global center_x, center_y
    sx = sy = 0
    n = 32
    for _ in range(n):
        sx += x_adc.read_u16()
        sy += y_adc.read_u16()
        await asyncio.sleep_ms(10)
    center_x = sx // n
    center_y = sy // n
    print("Joystick center: x={} y={}".format(center_x, center_y))


def scale_axis(raw, center, invert):
    """raw ADC -> -100..100 with deadzone."""
    delta = raw - center
    if abs(delta) <= DEADZONE:
        return 0
    delta = delta - DEADZONE if delta > 0 else delta + DEADZONE
    value = delta * 100 // (JOY_FULL_DEFLECTION - DEADZONE)
    value = max(-100, min(100, value))
    return -value if invert else value


def read_joystick():
    x_raw = x_adc.read_u16()
    y_raw = y_adc.read_u16()
    if x_raw >= JOY_BUTTON_X_ABOVE:               # button press shows up as x at max: ignore
        steer = 0
    else:
        steer = scale_axis(x_raw, center_x, INVERT_STEER)
    throttle = scale_axis(y_raw, center_y, INVERT_THROTTLE)
    return steer, throttle


# ---------------------------------------------------------------------
# Celebration (runs in the background)
# ---------------------------------------------------------------------
lights = neopixel.NeoPixel(Pin(NEO_PIN), NEO_COUNT)
buzzer = PWM(Pin(BUZZER_PIN), freq=1000, duty_u16=0)
_celebration_tasks = []


def _scaled(color):
    return tuple(int(c * NEO_BRIGHTNESS) for c in color)


def _lights_off():
    for i in range(NEO_COUNT):
        lights[i] = (0, 0, 0)
    lights.write()


async def _light_show():
    t0 = ticks_ms()
    step = 0
    try:
        while ticks_diff(ticks_ms(), t0) < CELEBRATION_MS:
            for i in range(NEO_COUNT):
                lights[i] = _scaled(CELEB_COLORS[(step + i) % len(CELEB_COLORS)])
            lights.write()
            step += 1
            await asyncio.sleep_ms(CELEB_STEP_MS)
    finally:
        _lights_off()


async def _sound():
    try:
        for _ in range(MELODY_REPEATS):
            for freq, ms in CELEB_MELODY:
                if freq > 0:
                    buzzer.freq(freq)
                    buzzer.duty_u16(BUZZER_DUTY)
                await asyncio.sleep_ms(ms)
                buzzer.duty_u16(0)
                await asyncio.sleep_ms(MELODY_GAP_MS)
    finally:
        buzzer.duty_u16(0)


def start_celebration():
    """Non-blocking. A new finish restarts the show."""
    global _celebration_tasks
    for t in _celebration_tasks:
        t.cancel()
    _celebration_tasks = [asyncio.create_task(_light_show()),
                          asyncio.create_task(_sound())]


# ---------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------
async def connection_task(link):
    while True:
        if link.connected:
            await asyncio.sleep_ms(200)
            continue
        print("Scanning for", DEVICE_NAME)
        if await link.connect():
            print("Connected to car")
        else:
            await asyncio.sleep_ms(RECONNECT_DELAY_MS)


async def joystick_task(link):
    while True:
        steer, throttle = read_joystick()
        link.send_drive(steer, throttle)
        await asyncio.sleep_ms(JOY_SEND_MS)


async def finish_task(link):
    while True:
        if link.finish_flag:
            link.finish_flag = False
            print("FINISH LINE reached - celebrating!")
            start_celebration()
        await asyncio.sleep_ms(20)


async def main():
    _lights_off()
    if AUTO_CENTER:
        print("Keep the joystick untouched - calibrating...")
        await calibrate_center()

    link = ControllerBLE()
    asyncio.create_task(connection_task(link))
    asyncio.create_task(joystick_task(link))
    asyncio.create_task(finish_task(link))
    while True:
        await asyncio.sleep(1)


try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()
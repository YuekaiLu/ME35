# car.py - runs on the CAR ESP32 (BLE peripheral)
# Copy to the car board as main.py (or import it from main.py).
# Needs veml6040.py on the board too.

import bluetooth
import json
import struct
import uasyncio as asyncio
from machine import Pin, PWM, I2C
from time import ticks_ms, ticks_diff

import veml6040

# =====================================================================
# SETTINGS - everything you may want to change is in this block
# =====================================================================

# ---------- BLE (must match controller.py) ----------
DEVICE_NAME = "RC-CAR"
SERVICE_UUID = "6e0a0001-0000-4c0e-9c52-0d1c5a7a0001"
DRIVE_UUID = "6e0a0002-0000-4c0e-9c52-0d1c5a7a0001"    # controller -> car (write)
FINISH_UUID = "6e0a0003-0000-4c0e-9c52-0d1c5a7a0001"   # car -> controller (notify)
ADV_INTERVAL_US = 100000

# ---------- Motors ----------
# Motor 1 = LEFT, Motor 2 = RIGHT.
# PIN_A is the "forward" pin and PIN_B the "backward" pin of each motor.
LEFT_PIN_A = 14
LEFT_PIN_B = 27
RIGHT_PIN_A = 12
RIGHT_PIN_B = 13
LEFT_REVERSED = False        # set True if the LEFT motor spins the wrong way
RIGHT_REVERSED = False       # set True if the RIGHT motor spins the wrong way
PWM_FREQ_HZ = 1000
MAX_SPEED_PERCENT = 100      # cap on motor speed (1-100)

# ---------- Driving ----------
DRIVE_TIMEOUT_MS = 500       # stop if no drive packet arrives for this long (0 = disabled)
DRIVE_UPDATE_MS = 20         # how often the motors are updated

# ---------- Motor test (runs once at boot) ----------
MOTOR_TEST_ON_BOOT = False    # run the test first. Put the car on blocks!
MOTOR_TEST_ONLY = False      # True = do the test and stop (no BLE, no sensor)
MOTOR_TEST_SPEED = 50        # percent
MOTOR_TEST_STEP_MS = 1500
MOTOR_TEST_PAUSE_MS = 700

# ---------- Color sensor / finish line ----------
I2C_ID = 0
I2C_SDA_PIN = 21
I2C_SCL_PIN = 22
I2C_FREQ_HZ = 100000
SENSOR_INTEGRATION_MS = 40   # 40, 80, 160, 320, 640 or 1280
SENSOR_POLL_MS = 50          # keep >= integration time so every poll is a fresh reading
RED_CONSECUTIVE = 3          # red this many readings in a row = finish line
PRINT_COLOR_READINGS = True  # print R,G,B,W so you can tune / collect samples

# ---------- Red detection (KNN on previous readings) ----------
KNN_FILE = "color_knn.json"  # samples file stored on the car's ESP32
KNN_K = 3                    # number of nearest samples that vote
KNN_USE_WHITE = True         # include the white channel in the distance
RED_LABEL = "red"            # label in the JSON that means "red tape"
# Samples must be recorded with the SAME integration time as SENSOR_INTEGRATION_MS.
# JSON format: a list of samples, each either [r, g, b, w, "label"]
#   or {"r": .., "g": .., "b": .., "w": .., "label": ".."}
# (a dict like {"samples": [ ... ]} also works). Any label other than
# RED_LABEL (e.g. "floor") counts as "not red".

# =====================================================================

_IRQ_CENTRAL_CONNECT = 1
_IRQ_CENTRAL_DISCONNECT = 2
_IRQ_GATTS_WRITE = 3

_FLAG_READ = 0x0002
_FLAG_WRITE_NO_RESPONSE = 0x0004
_FLAG_WRITE = 0x0008
_FLAG_NOTIFY = 0x0010

_IT_CONSTANTS = {
    40: veml6040.IT_40MS,
    80: veml6040.IT_80MS,
    160: veml6040.IT_160MS,
    320: veml6040.IT_320MS,
    640: veml6040.IT_640MS,
    1280: veml6040.IT_1280MS,
}


# ---------------------------------------------------------------------
# Red detection
# ---------------------------------------------------------------------
_samples = []    # list of (r, g, b, w, label), loaded from KNN_FILE


def load_knn_samples():
    """Load the labeled readings from the JSON file on the board."""
    global _samples
    _samples = []
    try:
        with open(KNN_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print("KNN: could not load", KNN_FILE, "-", e)
        return
    if isinstance(data, dict):
        data = data.get("samples", [])
    for s in data:
        try:
            if isinstance(s, dict):
                _samples.append((s["r"], s["g"], s["b"], s["w"], s["label"]))
            else:
                _samples.append((s[0], s[1], s[2], s[3], s[4]))
        except (KeyError, IndexError, TypeError):
            print("KNN: skipping bad sample", s)
    n_red = sum(1 for s in _samples if s[4] == RED_LABEL)
    print("KNN: loaded {} samples ({} red, {} other)".format(
        len(_samples), n_red, len(_samples) - n_red))
    if n_red == 0 or n_red == len(_samples):
        print("KNN: WARNING - need both red and non-red samples")


def is_red(r, g, b, w):
    """KNN: the K closest saved readings vote; red wins on a strict majority."""
    if not _samples:
        return False
    best = []                                    # sorted list of (distance, label), max K
    for sr, sg, sb, sw, label in _samples:
        dr = sr - r
        dg = sg - g
        db = sb - b
        d = dr * dr + dg * dg + db * db
        if KNN_USE_WHITE:
            dw = sw - w
            d += dw * dw
        if len(best) < KNN_K or d < best[-1][0]:
            best.append((d, label))
            best.sort()
            if len(best) > KNN_K:
                best.pop()
    red_votes = sum(1 for _, label in best if label == RED_LABEL)
    return red_votes * 2 > len(best)


# ---------------------------------------------------------------------
# Motors
# ---------------------------------------------------------------------
class Motor:
    def __init__(self, pin_fwd, pin_bwd, reversed_):
        if reversed_:
            pin_fwd, pin_bwd = pin_bwd, pin_fwd
        self.fwd = PWM(Pin(pin_fwd), freq=PWM_FREQ_HZ, duty_u16=0)
        self.bwd = PWM(Pin(pin_bwd), freq=PWM_FREQ_HZ, duty_u16=0)

    def set(self, speed):
        """speed: -100 (full backward) .. 0 (stop) .. 100 (full forward)"""
        speed = max(-100, min(100, int(speed)))
        duty = abs(speed) * 65535 // 100
        if speed > 0:
            self.bwd.duty_u16(0)
            self.fwd.duty_u16(duty)
        elif speed < 0:
            self.fwd.duty_u16(0)
            self.bwd.duty_u16(duty)
        else:
            self.fwd.duty_u16(0)
            self.bwd.duty_u16(0)


def mix(throttle, steer):
    """throttle, steer in -100..100 -> (left, right) speeds.
    steer > 0 turns right (left side faster / right side slower or backward),
    steer < 0 turns left. With throttle 0 the car spins on the spot."""
    left = max(-100, min(100, throttle + steer))
    right = max(-100, min(100, throttle - steer))
    return left * MAX_SPEED_PERCENT // 100, right * MAX_SPEED_PERCENT // 100


async def motor_test(left, right):
    s = MOTOR_TEST_SPEED
    steps = [
        ("LEFT motor FORWARD", s, 0),
        ("LEFT motor BACKWARD", -s, 0),
        ("RIGHT motor FORWARD", 0, s),
        ("RIGHT motor BACKWARD", 0, -s),
        ("BOTH FORWARD (car should drive straight ahead)", s, s),
        ("BOTH BACKWARD", -s, -s),
        ("SPIN LEFT (left backward, right forward)", -s, s),
        ("SPIN RIGHT (left forward, right backward)", s, -s),
    ]
    print("=== MOTOR TEST ===")
    print("If a motor goes the wrong way, set LEFT_REVERSED / RIGHT_REVERSED.")
    for label, l, r in steps:
        print(label)
        left.set(l)
        right.set(r)
        await asyncio.sleep_ms(MOTOR_TEST_STEP_MS)
        left.set(0)
        right.set(0)
        await asyncio.sleep_ms(MOTOR_TEST_PAUSE_MS)
    print("=== MOTOR TEST DONE ===")


# ---------------------------------------------------------------------
# BLE (peripheral)
# ---------------------------------------------------------------------
class CarBLE:
    def __init__(self):
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self._irq)

        service = (
            bluetooth.UUID(SERVICE_UUID),
            (
                (bluetooth.UUID(DRIVE_UUID), _FLAG_WRITE | _FLAG_WRITE_NO_RESPONSE),
                (bluetooth.UUID(FINISH_UUID), _FLAG_READ | _FLAG_NOTIFY),
            ),
        )
        ((self.h_drive, self.h_finish),) = self.ble.gatts_register_services((service,))

        self.conn = None
        self.steer = 0
        self.throttle = 0
        self.last_cmd = ticks_ms()
        self._advertise()

    @property
    def connected(self):
        return self.conn is not None

    def _advertise(self):
        name = DEVICE_NAME.encode()
        payload = b"\x02\x01\x06" + bytes((len(name) + 1, 0x09)) + name
        self.ble.gap_advertise(ADV_INTERVAL_US, adv_data=payload)
        print("Advertising as", DEVICE_NAME)

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            self.conn = data[0]
            self.steer = 0
            self.throttle = 0
            self.last_cmd = ticks_ms()
            print("Controller connected")
        elif event == _IRQ_CENTRAL_DISCONNECT:
            self.conn = None
            self.steer = 0
            self.throttle = 0
            print("Controller disconnected - motors stopped")
            self._advertise()
        elif event == _IRQ_GATTS_WRITE:
            handle = data[1]
            if handle == self.h_drive:
                raw = self.ble.gatts_read(handle)
                if len(raw) >= 2:
                    self.steer, self.throttle = struct.unpack("<bb", raw[:2])
                    self.last_cmd = ticks_ms()

    def notify_finish(self):
        """Tell the controller the finish line was crossed."""
        if self.conn is None:
            return False
        try:
            self.ble.gatts_write(self.h_finish, b"\x01")
            self.ble.gatts_notify(self.conn, self.h_finish, b"\x01")
            return True
        except OSError as e:
            print("Notify failed:", e)
            return False


# ---------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------
async def drive_task(link, left, right):
    while True:
        timed_out = (DRIVE_TIMEOUT_MS > 0 and
                     ticks_diff(ticks_ms(), link.last_cmd) > DRIVE_TIMEOUT_MS)
        if not link.connected or timed_out:
            left.set(0)
            right.set(0)
        else:
            l, r = mix(link.throttle, link.steer)
            left.set(l)
            right.set(r)
        await asyncio.sleep_ms(DRIVE_UPDATE_MS)


async def color_task(link):
    load_knn_samples()
    i2c = I2C(I2C_ID, scl=Pin(I2C_SCL_PIN), sda=Pin(I2C_SDA_PIN), freq=I2C_FREQ_HZ)
    sensor = veml6040.VEML6040(i2c)                      # driver defaults to 1280 ms...
    sensor.set_integration_time(_IT_CONSTANTS[SENSOR_INTEGRATION_MS])   # ...so set ours

    red_count = 0
    latched = False          # True after we reported; cleared once we see non-red again
    while True:
        try:
            r, g, b, w = sensor.read_rgbw()
        except OSError as e:
            print("Sensor read failed:", e)
            await asyncio.sleep_ms(SENSOR_POLL_MS)
            continue

        if PRINT_COLOR_READINGS:
            print("RGBW", r, g, b, w)

        if is_red(r, g, b, w):
            red_count += 1
        else:
            red_count = 0
            latched = False

        if red_count >= RED_CONSECUTIVE and not latched:
            latched = True
            print("FINISH LINE detected")
            link.notify_finish()

        await asyncio.sleep_ms(SENSOR_POLL_MS)


async def main():
    left = Motor(LEFT_PIN_A, LEFT_PIN_B, LEFT_REVERSED)
    right = Motor(RIGHT_PIN_A, RIGHT_PIN_B, RIGHT_REVERSED)

    if MOTOR_TEST_ON_BOOT:
        await motor_test(left, right)
        if MOTOR_TEST_ONLY:
            return

    link = CarBLE()
    asyncio.create_task(drive_task(link, left, right))
    asyncio.create_task(color_task(link))
    while True:
        await asyncio.sleep(1)


try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()

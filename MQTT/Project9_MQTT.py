# car_mqtt.py - Homework 9: the car is controlled over MQTT instead of the joystick.
# Runs on the CAR ESP32 (MicroPython). Copy to the board as main.py (or import it).
# Needs wifi_cred.py on the board with: SSID, PWD, mqtt_url, port

import json
import time
import ubinascii
import machine
import network
import neopixel
from machine import Pin, PWM, Timer
from time import ticks_ms, ticks_diff
from umqtt.simple import MQTTClient

import wifi_cred

# =====================================================================
# SETTINGS - everything you may want to change is in this block
# =====================================================================

# ---------- Wi-Fi / MQTT ----------
SSID = wifi_cred.SSID
PASSWORD = wifi_cred.PWD
MQTT_BROKER = wifi_cred.mqtt_url
MQTT_PORT = wifi_cred.port
USE_SSL = False                  # demo code does not enable SSL; set True only if the broker needs it
# Unique per board so several bots on the same broker don't kick each other off:
CLIENT_ID = "esp32_car_" + ubinascii.hexlify(machine.unique_id()).decode()
KEEPALIVE_S = 10                 # broker drops us if it hears nothing for ~1.5x this
PING_MS = 4000                   # how often we ping the broker (keep < KEEPALIVE_S * 1000)
WIFI_TIMEOUT_S = 10
RETRY_DELAY_MS = 1000            # pause between reconnect attempts

# ---------- Topics ----------
TOPIC_SPEED = "ME35/speed"           # integer 0 - 100
TOPIC_DIRECTION = "ME35/direction"   # forward, backward, left, right, stop
TOPIC_LED_PREFIX = "ME35/led/"       # ME35/led/0 and ME35/led/1, JSON {"r":..,"g":..,"b":..}

# ---------- Driving ----------
DEFAULT_SPEED = 20                   # used until a speed message arrives
LOOP_MS = 10                         # how often incoming messages are checked

# ---------- Motors ----------
# Motor 1 = LEFT, Motor 2 = RIGHT. PIN_A is the "forward" pin, PIN_B the "backward" pin.
LEFT_PIN_A = 14
LEFT_PIN_B = 27
RIGHT_PIN_A = 12
RIGHT_PIN_B = 13
LEFT_REVERSED = False                # set True if the LEFT motor spins the wrong way
RIGHT_REVERSED = False               # set True if the RIGHT motor spins the wrong way
PWM_FREQ_HZ = 1000

# ---------- NeoPixels ----------
NEO_PIN = 15
NEO_COUNT = 2                        # LEDs 0 and 1
NEO_BRIGHTNESS = 1.0                 # 0.0 - 1.0 scale applied to the received colors
BLINK_MS = 500                       # red blink period when the connection is lost
# =====================================================================

DIRECTIONS = ("forward", "backward", "left", "right", "stop")


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


left_motor = Motor(LEFT_PIN_A, LEFT_PIN_B, LEFT_REVERSED)
right_motor = Motor(RIGHT_PIN_A, RIGHT_PIN_B, RIGHT_REVERSED)

speed = DEFAULT_SPEED
direction = "stop"


def apply_motion():
    s = speed
    if direction == "forward":
        l, r = s, s
    elif direction == "backward":
        l, r = -s, -s
    elif direction == "left":            # rotate left in place
        l, r = -s, s
    elif direction == "right":           # rotate right in place
        l, r = s, -s
    else:
        l, r = 0, 0
    left_motor.set(l)
    right_motor.set(r)


def stop_motors():
    global direction
    direction = "stop"
    apply_motion()


# ---------------------------------------------------------------------
# NeoPixels
# ---------------------------------------------------------------------
lights = neopixel.NeoPixel(Pin(NEO_PIN), NEO_COUNT)
led_colors = [(0, 0, 0)] * NEO_COUNT     # last colors commanded over MQTT
_blink_on = False
_blink_timer = Timer(0)


def _scaled(color):
    return tuple(int(c * NEO_BRIGHTNESS) for c in color)


def show_commanded_colors():
    for i in range(NEO_COUNT):
        lights[i] = _scaled(led_colors[i])
    lights.write()


def _blink_cb(timer):
    global _blink_on
    _blink_on = not _blink_on
    color = (255, 0, 0) if _blink_on else (0, 0, 0)
    for i in range(NEO_COUNT):
        lights[i] = color
    lights.write()


def start_blink():
    """Blink both LEDs red. Runs on a timer so it keeps going while we try to reconnect."""
    global _blink_on
    _blink_on = False
    _blink_timer.init(period=BLINK_MS, mode=Timer.PERIODIC, callback=_blink_cb)


def stop_blink():
    _blink_timer.deinit()
    show_commanded_colors()


def clamp_channel(v):
    return max(0, min(255, int(v)))


# ---------------------------------------------------------------------
# MQTT message handling
# ---------------------------------------------------------------------
def on_message(topic, msg):
    global speed, direction
    topic = topic.decode()
    text = msg.decode().strip()

    if topic == TOPIC_SPEED:
        try:
            value = int(float(text))
        except ValueError:
            print("Bad speed message:", text)
            return
        speed = max(0, min(100, value))
        print("Speed set to", speed)
        # Speed never starts the car: it only scales the current direction.
        # While direction is "stop" this keeps the motors at 0.
        apply_motion()

    elif topic == TOPIC_DIRECTION:
        d = text.strip('"').strip().lower()
        if d not in DIRECTIONS:
            print("Bad direction message:", text)
            return
        direction = d
        print("Direction:", direction, "at speed", speed)
        apply_motion()

    elif topic.startswith(TOPIC_LED_PREFIX):
        try:
            index = int(topic[len(TOPIC_LED_PREFIX):])
            data = json.loads(text)
            color = (clamp_channel(data["r"]), clamp_channel(data["g"]), clamp_channel(data["b"]))
        except (ValueError, KeyError, TypeError):
            print("Bad LED message on", topic, ":", text)
            return
        if not 0 <= index < NEO_COUNT:
            print("LED index out of range:", index)
            return
        led_colors[index] = color
        lights[index] = _scaled(color)
        lights.write()
        print("LED", index, "->", color)


# ---------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------
wlan = network.WLAN(network.STA_IF)


def connect_wifi():
    wlan.active(True)
    if wlan.isconnected():
        return True
    print("Connecting to WiFi...")
    wlan.connect(SSID, PASSWORD)
    timeout = WIFI_TIMEOUT_S
    while not wlan.isconnected() and timeout > 0:
        time.sleep(1)
        timeout -= 1
    if wlan.isconnected():
        print("WiFi Connected! IP:", wlan.ifconfig()[0])
        return True
    print("WiFi connection failed!")
    return False


def connect_mqtt():
    try:
        if USE_SSL:
            client = MQTTClient(CLIENT_ID, MQTT_BROKER, MQTT_PORT, keepalive=KEEPALIVE_S,
                                ssl=True, ssl_params={'server_hostname': MQTT_BROKER})
        else:
            client = MQTTClient(CLIENT_ID, MQTT_BROKER, MQTT_PORT, keepalive=KEEPALIVE_S)
        client.set_callback(on_message)
        client.connect()
        for topic in (TOPIC_SPEED, TOPIC_DIRECTION,
                      TOPIC_LED_PREFIX + "0", TOPIC_LED_PREFIX + "1"):
            client.subscribe(topic)
        print("MQTT Connected successfully!")
        return client
    except Exception as e:
        print("MQTT connection failed:", e)
        return None


def connect_all():
    """Keep trying (LEDs blinking red) until Wi-Fi and MQTT are both up."""
    while True:
        if connect_wifi():
            client = connect_mqtt()
            if client:
                return client
        time.sleep_ms(RETRY_DELAY_MS)


def run(client):
    """Handle messages until the connection is lost (raises an exception)."""
    last_ping = ticks_ms()
    while True:
        if not wlan.isconnected():
            raise OSError("WiFi lost")
        client.check_msg()
        if ticks_diff(ticks_ms(), last_ping) >= PING_MS:
            client.ping()
            last_ping = ticks_ms()
        time.sleep_ms(LOOP_MS)


def main():
    stop_motors()
    while True:
        start_blink()                    # red blink until connected
        client = connect_all()
        stop_blink()                     # back to the last commanded colors
        stop_motors()                    # never start moving without a direction command
        try:
            run(client)
        except Exception as e:
            print("Connection lost:", e)
        finally:
            stop_motors()                # connection lost -> STOP
            try:
                client.disconnect()
            except Exception:
                pass


main()

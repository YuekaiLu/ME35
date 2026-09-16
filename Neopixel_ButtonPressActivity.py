# import libraries
import neopixel
import time
from machine import Pin
from time import ticks_ms, ticks_diff

lights = neopixel.NeoPixel(Pin(15),2) # 15 is the Pin for neopixel and 2 is the number of lights

btn1 = Pin(34, Pin.IN, Pin.PULL_UP) # creating btn1 as an input pin for pin 34 and internal PULLUP resistors
btn2 = Pin(35, Pin.IN, Pin.PULL_UP) # creating btn2 as an input pin for pin 35 and internal PULLUP resistors

DEBOUNCE_MS = 200 # creating 200 ms window for input
last_press = 0 # tracks timestamp of the last accepted press, starting at 0

def button_handler1(pin):
    global last_press
    now = ticks_ms()
    if ticks_diff(now, last_press) > DEBOUNCE_MS:
        if (pin.value() == 0):
            last_press = now
            lights[0] = (20,0,0)
            lights[1] = (0,0,20)
            lights.write()

def button_handler2(pin):
    global last_press
    now = ticks_ms()
    if ticks_diff(now, last_press) > DEBOUNCE_MS:
        if (pin.value() == 0):
            last_press = now
            lights[0] = (0,0,20)
            lights[1] = (20,0,0)
            lights.write()
            
btn1.irq(trigger=Pin.IRQ_FALLING, handler=button_handler1)
btn2.irq(trigger=Pin.IRQ_FALLING, handler=button_handler2)
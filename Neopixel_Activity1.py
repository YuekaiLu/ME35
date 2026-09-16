# This code flashes the neopixel 0 red and neopixel 1 purple 5 times

import neopixel #importing the library
import time
from machine import Pin # another way of importing a library
lights = neopixel.NeoPixel(Pin(15),2) # 15 is the Pin for neopixel and 2 is the number of lights

for i in range(5):
    lights[0] = (20,0,0) # set the color of 0 light to red
    lights[1] = (20,0,20) # set the color of 1 light to purple
    lights.write()
    time.sleep_ms(100) # pause
    lights[0] = (0,0,0) # set the color of 0 light to nothing
    lights[1] = (0,0,0) # set the color of 0 light to nothing
    lights.write()
    time.sleep_ms(100) # pause
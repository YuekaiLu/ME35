import neopixel #importing the library
import time
from machine import Pin # another way of importing a library
lights = neopixel.NeoPixel(Pin(15),2) # 0 is the Pin for neopixel and 4 is the number of lights

for i in range(5):
    lights[0] = (20,0,0) # set the color of 0th light to purple
    lights[1] = (20,0,20)
    lights.write()
    time.sleep_ms(100)
    lights[0] = (0,0,0)
    lights[1] = (0,0,0)
    lights.write()
    time.sleep_ms(100)
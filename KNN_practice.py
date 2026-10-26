# k Nearest Neighbor demo

from machine import Pin
import neopixel


import math
import time


STATE_TRAIN = False
STATE_PLAY = False

data =[]
count = 0

data = [[0,1],[100,2], [50,3],[2,1],[95,2], [48,3],[8,1],[89,2], [35,3],[20,1],[80,2], [35,3]]
color_LUT = {1:(0,0,100),2:(0,100,0),3:(100,0,0)}

button_Play = Pin(35, Pin.IN, Pin.PULL_UP)

debounce_filter = 100
last_entered_time = 0  
    
def playButton(p):
    global STATE_PLAY
    STATE_PLAY = True
    print(data)

button_Train.irq(trigger=Pin.IRQ_RISING, handler=trainButton)
button_Play.irq(trigger=Pin.IRQ_RISING, handler=playButton)



# for KNN
def k_nearest_neighbor(x, k =1):
    dist_min = 100000
    distances = []
    for index, d in enumerate(data):
        dist = math.sqrt((x-d[0])**2)
        distances.append([dist,d[1]])
    
    print("Before sorting", distances)
    distances.sort()
    distances = distances[:k] #get k distances
    print("distances", distances)
    classes = []
    for dist in distances:
        classes.append(dist[1])
    print("k classes", classes)
    most_number_of_closest_classes = max(set(classes), key = classes.count)
    print("max classes ", most_number_of_closest_classes)
    
    return most_number_of_closest_classes



while True:
    if(STATE_TRAIN):
        print("training")
    if(STATE_PLAY):
        #do something else

        what_class = k_nearest_neighbor(30,1)
        print(what_class)
        time.sleep(0.1)
        STATE_PLAY = False
        
    
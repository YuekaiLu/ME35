#Examples of dictionaries
data = {"key":"value"} 
print(data["key"])

data1 = {"key1" : "value1", "key2" : "value2"}
print(data1["key1"])

data2 = [{"key1" : "A", "key2" : "B"}, {"key1" : "C", "key2" : "D"}]
print(data2[0]["key1"])
print("-------")

# Values from a dictionary
student = {
    "name": "Alex",
    "score": 92,
    "passed": True
}

print(student["name"])
print(student["score"])
print(student["passed"])
print("-------")

# Nested dictionary
robot = {
    "name": "Rover",
    "position": {
        "x": 12,
        "y": 7
    }
}

print(robot["name"])
print(robot["position"]["x"])
print(robot["position"]["y"])
print("-------")

# List inside dictionary
robot1 = {
    "name": "Rover",
    "sensors": ["distance", "temperature", "light"]
}

print(robot1["sensors"][0])  # distance
print(robot1["sensors"][2])  # light
print("-------")

# Looping thru dictionaries
motors = [
    {"name": "left", "speed": 50},
    {"name": "right", "speed": 60}
]

for motor in motors:
    print(motor["name"])
    print(motor["speed"])
print("-------")

# Checking whether key exists
sensor = {
    "type": "temperature",
    "value": 24.5
}

if "value" in sensor:
    print(sensor["value"])

if "unit" in sensor:
    print(sensor["unit"])
else:
    print("No unit was provided")
print("-------")

# Using get for missing values
sensor1 = {
    "type": "distance",
    "value": 35
}

print(sensor1.get("value"))
print(sensor1.get("unit", "cm"))


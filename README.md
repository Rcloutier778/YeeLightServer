# YeeLightServer

Server for my YeeLight lights. Replaced most of the functionality of my client side YeeLight controller.

# Features

Automatically adjust the color temperature of the lights to match that of the position of the sun (bright and cold at noon, dim and warm at night).

Simulates a sunrise in the morning help promote a less groggy wakeup, as if you're actually being woken up by the sun, not your
alarm clock.

Controllable via a HTTP REST API (handlers/http_events.py) and 433MHz radio (handlers/switches.py). 

Automatic on/of via wifi presence detection (handlers/checkPing.py). 


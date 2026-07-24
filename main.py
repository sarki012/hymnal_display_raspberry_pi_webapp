#import machine
#import time

# Pin 25 is the onboard LED for standard Pico; Pico W uses "LED"
#led = machine.Pin("LED", machine.Pin.OUT) 

#hile True:
#    led.toggle()
#    time.sleep(0.5)

import network
import socket
import time

# Access Point Credentials
AP_SSID = "PicoW_Server"
AP_PASSWORD = "password123"

def start_access_point():
    """Starts a Wi-Fi Access Point."""
    ap = network.WLAN(network.AP_IF)
    ap.config(essid=AP_SSID, password=AP_PASSWORD)
    ap.active(True)

    # Wait for the AP to be active
    while not ap.active():
        time.sleep(1)

    ip_address = ap.ifconfig()[0]
    print(f"Access Point '{AP_SSID}' started.")
    print(f"Connect to this network and go to http://{ip_address}")
    return ip_address


def serve_webpage(ip):
    """Starts the web server and listens for incoming requests."""
    if not ip:
        return

    # Read the HTML file content once at startup to save memory and improve speed
    try:
        with open("index.html", "r") as f:
            html_content = f.read()
    except OSError as e:
        print(f"Error: Cannot open index.html. {e}")
        return

    # Set up TCP socket
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    print("Listening on port 80...")

    while True:
        client = None  # Ensure client is defined
        try:
            client, addr = s.accept()
            print(f"Client connected from {addr}")
            request = client.recv(1024)

            # Minimal request parsing to check for GET /
            request_line = request.decode('utf-8').split('\n')[0]
            if "GET / " in request_line:
                # Send HTTP Header response
                client.send('HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n')
                # Send HTML Payload
                client.send(html_content)

        except Exception as e:
            print(f"Error: {e}")
        finally:
            if client:
                client.close()

# Execute Workflow
ip = start_access_point()
serve_webpage(ip)

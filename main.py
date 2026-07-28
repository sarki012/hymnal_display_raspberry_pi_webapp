import machine
import network
import socket
import time

# --- Keypad Protocol Definitions ---
# These values must match the #define values in the C code for the display controller.
KEYPAD_CODES = {
    '0': 0x65, '1': 0x5F, '2': 0x63, '3': 0x5E, '4': 0x5D,
    '5': 0x17, '6': 0x06, '7': 0x33, '8': 0x67, '9': 0x19
}
KEYPAD_ENTER = 0x0C
KEYPAD_CLEAR = 0x32

# --- UART Configuration ---
# The C code specifies inverted UART logic.
# UART1: TX=GPIO4 (Pin 6), RX=GPIO5 (Pin 7)
# We will only use the TX pin.
uart = machine.UART(1, baudrate=9600, tx=machine.Pin(4), rx=machine.Pin(5), invert=machine.UART.INV_TX | machine.UART.INV_RX)

# Onboard LED for Pico W
led = machine.Pin("LED", machine.Pin.OUT)

# Access Point Credentials
AP_SSID = "PicoW_Server"
AP_PASSWORD = "password123"

def send_key_code(code):
    """Sends a single byte key code over UART."""
    uart.write(bytes([code]))
    time.sleep_ms(50) # Small delay between characters

def send_clear_top_sequence():
    """
    Emulates the C clearTop() function by sending its specific byte sequence over UART.
    Sends three 0b00000000s followed by three 0b10000000s.
    """
    print("Sending clearTop() sequence...")
    clear_sequence = [0b00000000, 0b00000000, 0b00000000, 0b10000000, 0b10000000, 0b10000000]
    for code in clear_sequence:
        send_key_code(code)

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

def perform_blink_cycle(count):
    """
    Blinks the LED 'count' times within a 1-second interval.
    """
    if count <= 0:
        return
    
    blink_delay = 500 // count # Distribute blinks within ~1 second
    for _ in range(count):
        led.on()
        time.sleep_ms(blink_delay)
        led.off()
        time.sleep_ms(blink_delay)


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

    # --- State variables for 7-minute timer ---
    SEVEN_MINUTES_MS = 7 * 60 * 1000
    timer_active = False
    timer_start_time = 0

    while True:
        # --- Step 1: Check and handle the 7-minute timer ---
        if timer_active:
            elapsed_time = time.ticks_diff(time.ticks_ms(), timer_start_time)
            if elapsed_time > SEVEN_MINUTES_MS:
                print("7-minute timer expired. Clearing top display.")
                # Per the C code, call clearTop() twice.
                send_clear_top_sequence()
                send_clear_top_sequence()
                timer_active = False # Deactivate timer until a new code is sent

        # --- Step 2: Check for incoming web requests (non-blocking) ---
        client = None
        try:
            # Set a short timeout so accept() doesn't block forever
            # A 1-second timeout is reasonable to allow the timer check to run periodically
            s.settimeout(1.0) 
            client, addr = s.accept()
            s.settimeout(None) # Return to blocking mode for client communication
            print(f"Client connected from {addr}")
            request = client.recv(1024)

            request_line = request.decode('utf-8').split('\n')[0]
            
            # --- Request Routing ---
            if "GET / " in request_line:
                client.send('HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n')
                client.send(html_content)
            
            elif "GET /on?code=" in request_line:
                try:
                    # Extract the code from "GET /on?code=123 HTTP/1.1"
                    code_str = request_line.split('code=')[1].split(' ')[0]
                    
                    # Validate that the code is 1-3 digits
                    if 1 <= len(code_str) <= 3 and code_str.isdigit():
                        # Per C code: if a key is entered while top is displaying, turn off top first.
                        # `displayState == 1` is equivalent to our `timer_active` being True.
                        if timer_active:
                            print("New code entered while timer was active. Clearing top display first.")
                            send_clear_top_sequence()
                            send_clear_top_sequence()
                            timer_active = False # Timer will be restarted below

                        print(f"Received ON command with code: {code_str}.")
                        print("Sending key codes over UART...")
                        
                        # Send the 3 digits one by one
                        for digit in code_str:
                            send_key_code(KEYPAD_CODES[digit])
                        
                        # Send the ENTER command
                        send_key_code(KEYPAD_ENTER)

                        # Start the 7-minute timer
                        print("Starting 7-minute timer.")
                        timer_active = True
                        timer_start_time = time.ticks_ms()
                        
                        client.send('HTTP/1.0 200 OK\r\n\r\n')
                        client.send(f"OK: Sent code {code_str} to display.")
                    else:
                        client.send('HTTP/1.0 400 Bad Request\r\n\r\n')
                        client.send("Error: Code must be 1 to 3 digits.")
                except (ValueError, IndexError):
                    client.send('HTTP/1.0 400 Bad Request\r\n\r\n')
                    client.send("Error: Invalid code format.")

            elif "GET /off" in request_line:
                print("Received OFF command. Stopping timer and clearing display.")
                # Per C code: if CLEAR is received while top is displaying, clear the top.
                if timer_active:
                    send_clear_top_sequence()
                    send_clear_top_sequence()

                # Deactivate the timer and send the standard KEYPAD_CLEAR for the bottom display.
                timer_active = False
                send_key_code(KEYPAD_CLEAR)
                client.send('HTTP/1.0 200 OK\r\n\r\n')
                client.send("OK: Sent CLEAR command.")

            else:
                client.send('HTTP/1.0 404 Not Found\r\n\r\n')
                client.send("Not Found")

        except OSError as e:
            # This will trigger on timeout, which is normal. We just ignore it.
            pass

        except Exception as e:
            print(f"Error: {e}")

        finally:
            if client:
                client.close()

# Execute Workflow
ip = start_access_point()
serve_webpage(ip)

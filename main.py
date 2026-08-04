import machine
import network
import socket
import time
# --- Keypad Protocol Definitions ---
# These values MUST match the #define values in the C code for the display controller.
# The original values were incorrect and have been updated to match the C firmware.
KEYPAD_CODES = {
    '0': 0x17, '1': 0x33, '2': 0x67, '3': 0x19, '4': 0x32,
    '5': 0x65, '6': 0x0C, '7': 0x5F, '8': 0x63, '9': 0x5E
}
KEYPAD_ENTER = 0x5D
KEYPAD_CLEAR = 0x06

# --- 7-Segment Display Definitions (from C code) ---
# These are the bit patterns for each digit on the 7-segment displays.
SEVEN_SEG_CODES = {
    '0': 0b00111111, '1': 0b00000110, '2': 0b01011011, '3': 0b01001111, '4': 0b01100110,
    '5': 0b01101101, '6': 0b01111101, '7': 0b00000111, '8': 0b01111111, '9': 0b01100111
}
SEVEN_SEG_BLANK = 0b00000000

# --- UART Configurations ---
# UART0 for receiving from an external source (e.g., a physical keypad)
# RX=GPIO1 (Pin 2). We will relay this data to UART1.
uart0 = machine.UART(0, baudrate=9600, rx=machine.Pin(1))

# UART1 for sending to the display controller.
# The C code for the controller specifies inverted UART logic.
# UART1: TX=GPIO4 (Pin 6), RX=GPIO5 (Pin 7)
uart = machine.UART(1, baudrate=9600, tx=machine.Pin(4), rx=machine.Pin(5), invert=machine.UART.INV_TX | machine.UART.INV_RX)

# Onboard LED for Pico W
led = machine.Pin("LED", machine.Pin.OUT)

# Access Point Credentials
AP_SSID = "HymnDisplay"
AP_PASSWORD = "password123"

def send_key_code(code):
    """Sends a single byte key code over UART."""
    uart.write(bytes([code]))
    time.sleep_ms(50) # Small delay between characters

def send_hymn_to_top(hymn_code_str):
    """
    Emulates the C sendToTop() function by building and sending the required 6-byte sequence.
    The C code sends the contents of PORTA, PORTB, PORTD with bit 7 cleared,
    then sends them again with bit 7 set. This is a multiplexing/addressing scheme.
    """
    print(f"Building and sending sendToTop() sequence for '{hymn_code_str}'...")
    
    # Manually pad the code to 3 digits. The 'rjust' method is not available in MicroPython.
    # We use 'B' as a placeholder for a blank display, which will map to SEVEN_SEG_BLANK.
    padded_code = ('B' * (3 - len(hymn_code_str))) + hymn_code_str

    # The C code shifts digits right-to-left, so PORTA=digit3, PORTB=digit2, PORTD=digit1.
    # We map our padded string to the 7-segment values for each port.
    port_d_val = SEVEN_SEG_CODES.get(padded_code[0], SEVEN_SEG_BLANK) # 1st digit (or blank)
    port_b_val = SEVEN_SEG_CODES.get(padded_code[1], SEVEN_SEG_BLANK) # 2nd digit (or blank)
    port_a_val = SEVEN_SEG_CODES.get(padded_code[2], SEVEN_SEG_BLANK) # 3rd digit

    # Build the 6-byte sequence based on the sendToTop() logic
    # First 3 bytes have bit 7 cleared, next 3 have bit 7 set.
    sequence = [port_a_val & 0x7F, port_b_val & 0x7F, port_d_val & 0x7F,
                port_a_val | 0x80, port_b_val | 0x80, port_d_val | 0x80]
    
    for code in sequence:
        send_key_code(code)

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
    ap.active(False) # Deactivate the interface before configuring
    # Explicitly set channel and security for better compatibility with mobile devices.
    ap.config(essid=AP_SSID, password=AP_PASSWORD, channel=6) # security=3 (WPA2-PSK) is default with password
    ap.active(True)

    # Wait for the AP to be active
    print("Waiting for Access Point to activate...")
    while not ap.active():
        time.sleep(1)

    time.sleep(1) # Add a small delay for stability
    ip_address = ap.ifconfig()[0]
    print(f"Access Point '{AP_SSID}' started on channel 6.")
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

        # --- Step 2: Check for and relay data from physical keypad (UART0) ---
        if uart0.any():
            data = uart0.read()
            if data:
                print(f"Relaying {len(data)} byte(s) from UART0 to UART1: {data}")
                uart.write(data)
                time.sleep_ms(50) # Small delay to match send_key_code

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

                        # 1. Send keypad codes to update the BOTTOM display
                        print(f"Sending keypad codes for '{code_str}' to bottom display...")
                        for digit in code_str:
                            send_key_code(KEYPAD_CODES[digit])
                        
                        # 2. Send the full sequence to update the TOP display (called twice in C code)
                        send_hymn_to_top(code_str)
                        send_hymn_to_top(code_str)

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

import machine
import network
import socket
import time # Keep for synchronous delays
import uasyncio as asyncio
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

# --- UART Configuration ---
# UART0 for receiving from an external source (e.g., a physical keypad)
# RX=GPIO1 (Pin 2). Inverting RX to match the physical controller's inverted TX.
uart0 = machine.UART(0, baudrate=9600, rx=machine.Pin(1), invert=machine.UART.INV_RX)

# The C code specifies inverted UART logic.
# UART1: TX=GPIO4 (Pin 6), RX=GPIO5 (Pin 7)
# We will only use the TX pin.
uart1 = machine.UART(1, baudrate=9600, tx=machine.Pin(4), rx=machine.Pin(5), invert=machine.UART.INV_TX | machine.UART.INV_RX)

# Onboard LED for Pico W
led = machine.Pin("LED", machine.Pin.OUT)

# Access Point Credentials
# NOTE: Intentionally open (no password). MicroPython's cyw43 AP driver has a
# known unfixed bug (github.com/orgs/micropython/discussions/17059) where
# security=WPA2 is accepted by config() but never applied to the actual
# beacon, so the AP broadcasts open regardless. Since the password was never
# really being enforced, we drop it to avoid the "WEP not secure" prompt and
# failed WPA2 handshake attempts on phones (e.g. iOS).
AP_SSID = "Hymn"

def send_key_code(code):
    """Sends a single byte key code over UART."""
    uart1.write(bytes([code]))
    time.sleep_ms(50) # Synchronous sleep is fine for this specific action

def send_hymn_to_top(hymn_code_str):
    """
    Emulates the C sendToTop() function by building and sending the required 6-byte sequence.
    The C code sends the contents of PORTA, PORTB, PORTD with bit 7 cleared,
    then sends them again with bit 7 set. This is a multiplexing/addressing scheme.
    """
    print(f"Building and sending sendToTop() sequence for '{hymn_code_str}'...")
    
    # Convert to int and back to string to strip leading zeros (e.g., "025" becomes "25").
    # This will cause the padding logic below to treat them as blanks.
    code_without_leading_zeros = str(int(hymn_code_str))
    # Manually pad the code to 3 digits. The 'rjust' method is not available in MicroPython.
    # We use 'B' as a placeholder for a blank display, which will map to SEVEN_SEG_BLANK.
    padded_code = ('B' * (3 - len(code_without_leading_zeros))) + code_without_leading_zeros

    # The C code shifts digits right-to-left, so PORTA=digit3, PORTB=digit2, PORTD=digit1.
    # We map our padded string to the 7-segment values for each port.
    port_d_val = SEVEN_SEG_CODES.get(padded_code[0], SEVEN_SEG_BLANK) # 1st digit (or blank)
    port_b_val = SEVEN_SEG_CODES.get(padded_code[1], SEVEN_SEG_BLANK) # 2nd digit (or blank)
    port_a_val = SEVEN_SEG_CODES.get(padded_code[2], SEVEN_SEG_BLANK) # 3rd digit

    # Build the 6-byte sequence based on the sendToTop() logic
    # First 3 bytes have bit 7 cleared, next 3 have bit 7 set.
    sequence = [port_a_val & 0x7F, port_b_val & 0x7F, port_d_val & 0x7F,
                port_a_val | 0x80, port_b_val | 0x80, port_d_val | 0x80]
    
    uart1.write(bytes(sequence))

def send_clear_top_sequence():
    """
    Emulates the C clearTop() function by sending its specific byte sequence over UART.
    Sends three 0b00000000s followed by three 0b10000000s.
    """
    print("Sending clearTop() sequence...")
    clear_sequence = [0b00000000, 0b00000000, 0b00000000, 0b10000000, 0b10000000, 0b10000000]
    uart1.write(bytes(clear_sequence))

def start_access_point():
    """Starts a Wi-Fi Access Point."""
    ap = network.WLAN(network.AP_IF)
    ap.active(False) # Deactivate the interface before configuring
    # security=0 is open (no password). See AP_SSID comment above for why.
    ap.config(essid=AP_SSID, channel=6, security=0, hidden=True)
    ap.active(True)

    # Wait for the AP to be active
    print("Waiting for Access Point to activate...")
    while not ap.active():
        time.sleep(1)

    time.sleep(1) # Add a small delay for stability
    ip_address = ap.ifconfig()[0]
    print(f"Hidden Access Point '{AP_SSID}' started on channel 6.")
    print(f"Manually connect to the hidden network '{AP_SSID}' and go to http://{ip_address}")
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


# --- Global State ---
try:
    with open("index.html", "r") as f:
        HTML_CONTENT = f.read()
except OSError as e:
    print(f"Fatal Error: Cannot open index.html. {e}")
    HTML_CONTENT = "<html><body><h1>Error</h1><p>Could not load index.html</p></body></html>"

SEVEN_MINUTES_MS = 7 * 60 * 1000
timer_active = False
timer_task = None

async def manage_seven_minute_timer():
    """An asyncio task that waits 7 minutes and then clears the display."""
    global timer_active
    await asyncio.sleep_ms(SEVEN_MINUTES_MS)
    print("7-minute timer expired. Clearing top display.")
    send_clear_top_sequence()
    send_clear_top_sequence()
    timer_active = False

async def uart_relayer():
    """A non-blocking task to relay data from UART0 to UART1."""
    print("UART relayer started.")
    sreader = asyncio.StreamReader(uart0)
    while True:
        # Wait for any data to arrive on UART0
        data = await sreader.read(1) # Read one byte at a time
        if data:
            print(f"Relaying byte from UART0 to UART1: {data}")
            uart1.write(data)
            # The 50ms delay is not needed here as it was for debouncing web inputs.
            # The physical keypad is already debounced by its own firmware.

async def handle_web_request(reader, writer):
    """Handles a single incoming web request."""
    global timer_active, timer_task
    
    try:
        request_line = await reader.readline()
        # Read and discard headers
        while await reader.readline() != b"\r\n":
            pass

        request_str = request_line.decode('utf-8')
        addr = writer.get_extra_info('peername')
        print(f"Client connected from {addr}, request: {request_str.strip()}")

        writer.write('HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n')

        if "GET / " in request_str:
            await writer.awrite(HTML_CONTENT)
        
        elif "GET /on?code=" in request_str:
            try:
                code_str = request_str.split('code=')[1].split(' ')[0]
                if 1 <= len(code_str) <= 3 and code_str.isdigit():
                    if timer_active and timer_task:
                        print("New code entered while timer was active. Clearing top display first.")
                        timer_task.cancel()
                        send_clear_top_sequence()
                        send_clear_top_sequence()

                    print(f"Sending keypad codes for '{code_str}' to bottom display...")
                    for digit in code_str:
                        send_key_code(KEYPAD_CODES[digit])
                    
                    send_hymn_to_top(code_str)
                    send_hymn_to_top(code_str)

                    print("Starting 7-minute timer.")
                    timer_active = True
                    timer_task = asyncio.create_task(manage_seven_minute_timer())
                    
                    await writer.awrite(f"OK: Sent code {code_str} to display.")
                else:
                    await writer.awrite("Error: Code must be 1 to 3 digits.")
            except (ValueError, IndexError):
                await writer.awrite("Error: Invalid code format.")

        elif "GET /off" in request_str:
            print("Received OFF command. Stopping timer and clearing display.")
            if timer_active and timer_task:
                timer_task.cancel()
                send_clear_top_sequence()
                send_clear_top_sequence()

            timer_active = False
            send_key_code(KEYPAD_CLEAR)
            await writer.awrite("OK: Sent CLEAR command.")

        else:
            writer.write('HTTP/1.0 404 Not Found\r\n\r\n')
            await writer.awrite("Not Found")

    except Exception as e:
        print(f"Error handling web request: {e}")
    finally:
        await writer.aclose()

async def main():
    """Main asynchronous function to set up and run all tasks."""
    ip = start_access_point()
    if not ip:
        print("Failed to start Access Point. Halting.")
        return

    print("Starting web server...")
    # Start the UART relayer task
    asyncio.create_task(uart_relayer())
    # Start the web server
    await asyncio.start_server(handle_web_request, '0.0.0.0', 80)
    
    # Keep the main task running forever
    while True:
        await asyncio.sleep(10)

# --- Execute Workflow ---
try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("Program stopped.")
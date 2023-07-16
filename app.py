import sqlite3
from datetime import datetime
import cv2
import uuid

import serial
from flask import Flask, render_template, request, redirect, url_for, g

# RTSP stream URL
rtsp_url = "rtsp://admin:12345@192.168.0.33:554/"

# Database configuration
database_file = 'weights.db'  # Replace with your database file name

# Configure serial port settings
port = '/dev/ttyUSB0'  # Replace with the correct port name on your system
baud_rate = 9600
data_bits = serial.EIGHTBITS
stop_bits = serial.STOPBITS_ONE
parity = serial.PARITY_NONE

# app configs
allowed_offset = 40
# Number of records per page
PER_PAGE = 100

app = Flask(__name__)

def initialize_database(conn, cursor):
    cursor.execute("PRAGMA user_version")
    user_version = cursor.fetchone()[0]
    if user_version < 1:
        # Create the 'weights' table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS weights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                weight INTEGER,
                timestamp DATETIME
            )
        ''')
        cursor.execute("PRAGMA user_version = 1")
    if user_version < 2:
        cursor.execute("ALTER TABLE weights ADD image VARCHAR")
        cursor.execute("PRAGMA user_version = 2")
    if user_version < 3:
        cursor.execute("ALTER TABLE weights ADD machine VARCHAR")
        cursor.execute("PRAGMA user_version = 3")
    if user_version < 4:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS machines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR
            )
        ''')
        cursor.execute("INSERT INTO machines (name) VALUES ('DF 6160'), ('DF 5100'), ('DF 7250')")
        conn.commit()
        cursor.execute("PRAGMA user_version = 4")

@app.route('/')
def index():
    # Establish connection to the database
    conn = sqlite3.connect(database_file)
    cursor = conn.cursor()
    # Get the page number from the query parameters
    page = request.args.get('page', 1, type=int)

    # Calculate the start and end indexes for pagination
    start = (page - 1) * PER_PAGE

    # Retrieve weights for the current page from the database
    cursor.execute('SELECT * FROM weights ORDER BY id DESC LIMIT ?, ?', (start, PER_PAGE))
    history = cursor.fetchall()

    conn.close()
    print(history)

    return render_template('index.html', history=history, PER_PAGE=PER_PAGE, page=page)


@app.route('/delete/<int:weight_id>', methods=['POST'])
def delete(weight_id):
    # Establish connection to the database
    conn = sqlite3.connect(database_file)
    cursor = conn.cursor()

    # Retrieve the latest value from the database
    cursor.execute("DELETE from weights WHERE id = ?", (weight_id,))
    conn.commit()

    print(f"Weight with id {weight_id} successfully deleted")
    conn.close()
    return redirect(url_for('index'))

def save_image():
    # Open the RTSP stream
    cap = cv2.VideoCapture(rtsp_url)

    # Check if the stream is successfully opened
    if not cap.isOpened():
      print("Failed to open RTSP stream")
      return ''

    # Read a frame from the stream
    ret, frame = cap.read()

    # Check if the frame is successfully captured
    if not ret:
      print("Failed to capture frame from RTSP stream")
      return ''

    filename = str(uuid.uuid4()) + ".jpg"
    # Save the frame as an image file
    cv2.imwrite("static/"+filename, frame)

    # Release the stream capture
    cap.release()

    return filename

def save_weight_to_database(weight, conn, cursor):
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    image_file = save_image()
    # Execute SQL query to insert weight into the database
    cursor.execute("INSERT INTO weights (weight, timestamp, image) VALUES (?,?, ?)",
                   (weight, current_time, image_file))
    # Commit the transaction and close the connection
    conn.commit()


def process_message(message, conn, cursor, last_weight):
    stability = message[:2].decode('utf-8')
    current_weight_string = message[9:16].decode('utf-8')
    current_weight = int(current_weight_string)
    if stability == 'ST' and (current_weight > last_weight + allowed_offset or current_weight < last_weight - allowed_offset):
        print(f"Final Weight: {current_weight}")
        save_weight_to_database(current_weight, conn, cursor)
        return current_weight
    else:
        return last_weight


def receive_serial_data():
    # Establish connection to the database
    conn = sqlite3.connect(database_file)
    cursor = conn.cursor()
    # Initialize the database schema
    initialize_database(conn, cursor)

    # Create a serial connection
    ser = serial.Serial(port, baud_rate, data_bits, parity, stop_bits)

    if not ser.isOpen():
        ser.open()

    # Read and print data from the serial port
    try:
        buffer = bytearray()
        last_weight = -100
        while True:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                buffer += bytearray(data)
                while len(buffer) >= 22:
                    real_buffer_size = 0
                    for i in range(len(buffer)):
                      real_buffer_size = real_buffer_size + 1
                      if buffer[i] == b'\n':
                        break
                    message = buffer[:real_buffer_size]
                    buffer = buffer[real_buffer_size:]
                    if real_buffer_size == 22:
                      last_weight = process_message(message, conn, cursor, last_weight)

    except KeyboardInterrupt:
        pass
    # Close the serial connection
    ser.close()
    print(f"Disconnected from {port}")
    conn.close()


if __name__ == '__main__':
    # Start receiving serial data in a separate thread
    import threading

    serial_thread = threading.Thread(target=receive_serial_data)
    serial_thread.start()

    # Run the Flask web server
    app.run(host="0.0.0.0")

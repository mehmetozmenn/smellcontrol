"""
Climate Challenge
---------------------------------
This project is an AI-powered odor and hazardous gas detection system developed using a sensor.
The goal is to create a solution that detects climate-related risks and provides users with meaningful threat assessments.

Hardware:
- 64-channel Smell.IX16x4 sensor array
- 0.55 Hz measurement rate (1.8s/sec)
- Detection of 20+ different gases and odors (NH3, H2S, NO2, VOC, Formaldehyde, etc.)

AI Capabilities:
- Time series analysis, anomaly detection
- Threat level inference via Watsonx.ai
- Real-time user notification via Telegram
"""

import requests
import serial
import serial.tools.list_ports
import time
import csv
import os
from datetime import datetime
import threading
import signal
import sys
import platform
import traceback
import ibm_boto3
from ibm_botocore.client import Config

TELEGRAM_TOKEN = "7185824936:AAGHjelLErQmoJ9DBoMrYvcW36h7K*****" # ****
TELEGRAM_CHAT_ID = "360310929"

 # Function to send Telegram messages
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print("Telegram notification sent.")
        else:
            print(f"Telegram sending error: {response.text}")
    except Exception as e:
        print(f"Telegram connection error: {e}")

COS_API_KEY = "ordPgG193uALIBocz-3bAM7nHcy9GhZ68JKHyiNzAeC3"
COS_SERVICE_INSTANCE_ID = "crn:v1:bluemix:public:cloud-object-storage:global:a/8d8d813b3ba54f00a00c1b12357a0c42:6b56bda3-5573-455b-a6fd-b5b94de14cb0:bucket:watsonxchallengesandbox-donotdelete-pr-l9jdnkojzrpzvl"
COS_ENDPOINT = "https://s3.us-south.cloud-object-storage.appdomain.cloud"
COS_BUCKET = "watsonxchallengesandbox-donotdelete-pr-l9jdnkojzrpzvl"

WATSONX_API_KEY = "ordPgG193uALIBocz-3bAM7nHcy9GhZ68JKHyiNzAeC3"
WATSONX_PROJECT_ID = "02de15c2-d8da-4bc8-99d6-9edfd0cd49c7"
WATSONX_URL = "https://us-south.ml.cloud.ibm.com"

class IBMCloudIntegration:
    def __init__(self):
        self.cos_client = None
        self.bucket_name = None

    def configure_cos(self, api_key, service_instance_id, endpoint, bucket_name):
        try:
            cos_credentials = {
                "ibm_api_key_id": api_key,
                "ibm_service_instance_id": service_instance_id,
                "config": Config(signature_version="oauth"),
                "endpoint_url": endpoint
            }
            self.bucket_name = bucket_name
            self.cos_client = ibm_boto3.client("s3", **cos_credentials)
            print("IBM COS configured successfully")
            return True
        except Exception as e:
            print(f"COS configuration error: {str(e)}")
            return False

    def upload_to_cos(self, file_path):
        if not self.cos_client:
            print("COS not configured.")
            return False

        import os
        object_name = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as file_data:
                self.cos_client.upload_fileobj(
                    Fileobj=file_data,
                    Bucket=self.bucket_name,
                    Key=object_name
                )
            print(f"{file_path} successfully uploaded → {self.bucket_name}/{object_name}")
            return True
        except Exception as e:
            print(f"Upload error: {str(e)}")
            return False

class SensorDataCollector:
    def __init__(self):
        self.serial_port = None
        self.is_collecting = False
        self.csv_file = None
        self.csv_writer = None
        self.data_count = 0
        self.start_time = None
        self.collection_thread = None
        self.current_filename = None
        
        # IBM Cloud integration
        self.ibm_integration = IBMCloudIntegration()
        
    def list_available_ports(self):
        """Lists available serial ports"""
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            print("No serial ports found!")
            # Special check for macOS
            if platform.system() == 'Darwin':
                print("On MacOS, you can check ports with: ls /dev/cu.*")
            return []
        
        print("\nAvailable serial ports:")
        for i, port in enumerate(ports):
            print(f"{i+1}. {port.device} - {port.description}")
        
        return ports
    
    def connect_to_sensor(self, port_name, baud_rate=9600):
        """Connects to the specified port"""
        try:
            self.serial_port = serial.Serial(port_name, baud_rate, timeout=1)
            print(f"Connection successful: {port_name}, {baud_rate} baud")
            return True
        except serial.SerialException as e:
            print(f"Connection error: {e}")
            # Permission error solution suggestion for macOS
            if "Permission denied" in str(e) and platform.system() == 'Darwin':
                print(f"\n!!! MACOS SOLUTION !!! Try this command in Terminal:")
                print(f"sudo chmod 666 {port_name}")
            return False
    
    def read_sensor_data(self):
        """Reads data from the sensor"""
        if not self.serial_port or not self.serial_port.is_open:
            raise Exception("No serial port connection!")
        
        try:
            data = self.serial_port.readline().decode('utf-8').strip()
            if not data:
                return None
            return data
        except Exception as e:
            print(f"Data reading error: {e}")
            return None
    
    def format_sensor_data(self, sensor_data):
        """Formats sensor data"""
        if not sensor_data:
            return []
            
        if sensor_data.startswith("start;"):
            sensor_data = sensor_data[6:]
        
        values = sensor_data.split(";")
        return values
    
    def start_collection(self, label, filename=None):
        """Starts data collection"""
        if self.is_collecting:
            print("Data collection is already in progress!")
            return
        
        if not self.serial_port or not self.serial_port.is_open:
            print("You must connect to the sensor first!")
            return
        
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join("data", f"sensor_data_{label}_{timestamp}.csv")
        
        try:
            # Create directory (macOS compatible)
            dir_name = os.path.dirname(filename)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            
            self.csv_file = open(filename, 'w', newline='')
            print(f"Data will be saved to '{filename}'.")
            
            first_data = self.read_sensor_data()
            if not first_data:
                print("Cannot read data from sensor! Check the connection.")
                self.csv_file.close()
                return
                
            formatted_data = self.format_sensor_data(first_data)
            num_features = len(formatted_data)
            
            headers = ['timestamp', 'label', 'elapsed_seconds'] + [f'feature_{i+1}' for i in range(num_features)]
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(headers)
            
            self.is_collecting = True
            self.data_count = 0
            self.start_time = time.time()
            self.current_filename = filename
            
            self.collection_thread = threading.Thread(target=self._collect_data_thread, args=(label,))
            self.collection_thread.daemon = True
            self.collection_thread.start()
            
            print(f"Data collection started. Press Ctrl+C to stop.")
            
        except Exception as e:
            print(f"Error starting data collection: {e}")
            if self.csv_file:
                self.csv_file.close()
    
    def _collect_data_thread(self, label):
        """Thread that collects data in the background"""
        while self.is_collecting:
            try:
                sensor_data = self.read_sensor_data()
                
                if sensor_data:
                    formatted_data = self.format_sensor_data(sensor_data)
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    elapsed_seconds = round(time.time() - self.start_time, 2)
                    
                    row = [current_time, label, elapsed_seconds] + formatted_data
                    self.csv_writer.writerow(row)
                    self.csv_file.flush()
                    
                    self.data_count += 1
                    if self.data_count % 10 == 0:
                        print(f"Data points collected: {self.data_count}, Elapsed time: {elapsed_seconds:.1f} seconds")
                
                next_read_time = self.start_time + (self.data_count + 1)
                sleep_time = max(0, next_read_time - time.time())
                if sleep_time > 0:
                    time.sleep(sleep_time)
                
            except Exception as e:
                print(f"Data collection error: {e}")
                self.stop_collection()
                break
    
    def stop_collection(self):
        """Stops data collection"""
        if not self.is_collecting:
            return None
            
        self.is_collecting = False
        
        if self.collection_thread:
            self.collection_thread.join(timeout=2.0)
            
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None
            
        elapsed_time = time.time() - self.start_time if self.start_time else 0
        print(f"\nData collection stopped.")
        print(f"Total {self.data_count} data points collected.")
        print(f"Elapsed time: {elapsed_time:.1f} seconds")
        
        return self.current_filename
    
    def close(self):
        """Closes the connection"""
        self.stop_collection()
        
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            print("Serial port connection closed.")


def signal_handler(sig, frame):
    """Catches Ctrl+C signal"""
    print("\nProgram is stopping...")
    if collector:
        collector.close()
    sys.exit(0)

def main():
    global collector
    collector = SensorDataCollector()

    # Configure IBM services
    if COS_API_KEY and COS_SERVICE_INSTANCE_ID and COS_ENDPOINT and COS_BUCKET:
        collector.ibm_integration.configure_cos(
            COS_API_KEY,
            COS_SERVICE_INSTANCE_ID,
            COS_ENDPOINT,
            COS_BUCKET
        )

    print("Sensor Data Collection and IBM Watsonx.ai Program")
    print("-----------------------------------------")

    # Set signal handler (for Ctrl+C)
    signal.signal(signal.SIGINT, signal_handler)

    # Set up Watsonx.ai model client
    try:
        from ibm_watsonx_ai import APIClient, Credentials
        from ibm_watsonx_ai.foundation_models import ModelInference
        creds = Credentials(api_key=WATSONX_API_KEY, url=WATSONX_URL)
        w_client = APIClient(creds)
        model = ModelInference(
            model_id="ibm/granite-13b-instruct-v2",
            api_client=w_client,
            project_id=WATSONX_PROJECT_ID,
            params={"max_new_tokens": 100}
        )
    except Exception as e:
        print(f"Error starting Watsonx.ai client: {e}")
        model = None

    while True:
        print("\nWhat would you like to do?")
        print("1. List available serial ports")
        print("2. Connect to sensor")
        print("3. Start data collection")
        print("4. Stop data collection")
        print("5. Upload data to IBM COS")
        print("6. Use Watsonx.ai model (with prompt)")
        print("7. Exit")
        print("8. Threat assessment and send notification (Watsonx.ai + Telegram)")

        choice = input("Your choice (1-8): ")

        if choice == "1":
            collector.list_available_ports()

        elif choice == "2":
            ports = collector.list_available_ports()
            if not ports:
                continue
            try:
                port_idx = int(input("\nEnter the number of the port to connect: ")) - 1
            except Exception:
                print("Invalid input!")
                continue
            if port_idx < 0 or port_idx >= len(ports):
                print("Invalid port number!")
                continue
            baud_rate = int(input("Enter baud rate (default: 9600): ") or "9600")
            collector.connect_to_sensor(ports[port_idx].device, baud_rate)

        elif choice == "3":
            label = input("Enter data label (e.g. 'Air', 'Coffee'): ")
            collector.start_collection(label)

        elif choice == "4":
            filename = collector.stop_collection()
            if filename:
                print(f"Collected data: {filename}")

        elif choice == "5":
            if not collector.ibm_integration.cos_client:
                print("IBM COS is not configured!")
                continue
            filename = input("File name to upload (full path): ")
            if os.path.exists(filename):
                collector.ibm_integration.upload_to_cos(filename)
            else:
                print("File not found!")

        elif choice == "6":
            if not model:
                print("Watsonx.ai client is not configured or not ready!")
                continue
            prompt = input("Prompt for the model to answer: ")
            try:
                result = model.generate(prompt)
                output = result.get("results", [{}])[0].get("generated_text", "")
                print("Response:", output)
            except Exception as e:
                print(f"Error during model call: {e}")

        elif choice == "7":
            collector.close()
            print("Exiting program...")
            break

        else:
            print("Invalid selection!")

        # New menu option: 8. Threat assessment and send notification
        if choice == "8":
            if not model:
                print("Watsonx.ai client is not configured or not ready!")
                continue
            # Example input to use if sensor is not connected
            EXAMPLE_INPUT = "CO: 0.6, NH3: 0.2, Alcohol: 0.1, Perfume: 0.8, Acetone: 0.3"
            prompt = input("Enter gas values for Watsonx AI (e.g. 'CO: 0.9, NH3: 0.3') [ENTER for example]: ") or EXAMPLE_INPUT
            try:
                result = model.generate(prompt)
                output = result.get("results", [{}])[0].get("generated_text", "")
                print("Watsonx response:", output)

                # Direct text-based odor classification is performed
                # For example: "Intense perfume, small amount of alcohol, and slight ammonia detected in this environment."

                summary_prompt = "Based on the given gas analysis output, which odors are dominant in the environment and what kind of threat do they pose to human health? Also specify the sources and effects of the odors.\n\n"
                full_prompt = summary_prompt + output

                try:
                    insight_result = model.generate(full_prompt)
                    insight_text = insight_result.get("results", [{}])[0].get("generated_text", "")
                    print("Detailed odor analysis and comment:", insight_text)

                    # Send Telegram notification
                    send_telegram_message(f"Odor Detection:\n{output}\n\nComment:\n{insight_text}")
                except Exception as e:
                    print("Error during interpretation:", e)
            except Exception as e:
                print(f"Error during model call: {e}")

if __name__ == "__main__":
    collector = None
    main()
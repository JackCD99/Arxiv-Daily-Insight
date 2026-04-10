import time
import datetime
import sys
import io
import traceback
import requests 
import os
import yaml

# Import the main execution pipeline
import main

# =====================================================================
# 1. Configuration Loading
# =====================================================================
try:
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"❌ Critical Error: Unable to read config.yaml. Please ensure the file exists. Error details: {e}")
    sys.exit(1)

# Dynamically configure system proxies based on YAML settings
if config.get('proxy', {}).get('http'):
    os.environ["http_proxy"] = config['proxy']['http']
if config.get('proxy', {}).get('https'):
    os.environ["https_proxy"] = config['proxy']['https']

# Retrieve the PushPlus token for alerts
PUSHPLUS_TOKEN = config.get('auth', {}).get('pushplus_token')

# =====================================================================
# 2. Core Classes & Utility Functions
# =====================================================================

class DualLogger:
    """
    A stream splitter that duplicates standard output (stdout).
    It writes messages simultaneously to the terminal console and an in-memory buffer.
    This enables real-time terminal monitoring while capturing the full log for persistence.
    """
    def __init__(self, terminal, buffer):
        self.terminal = terminal
        self.buffer = buffer

    def write(self, message):
        self.terminal.write(message)
        self.buffer.write(message) 

    def flush(self):
        self.terminal.flush()
        self.buffer.flush()


def send_failure_alert(log_content):
    """
    Triggers a WeChat alert via PushPlus when the pipeline fails completely.
    Sends the tail of the error log to the administrator.
    """
    if not PUSHPLUS_TOKEN:
        print("\n⚠️ PushPlus token is not configured. Skipping failure alert.")
        return

    print("\n🚨 Sending failure alert via WeChat PushPlus...")
    
    # Truncate log to the last 1000 characters to prevent payload overflow in the API
    short_log = log_content[-1000:] if len(log_content) > 1000 else log_content
    
    title = '🚨 [System Alert] arXiv Pipeline Execution Failed'
    content = f"An exception occurred during today's execution. The pipeline has been halted.\n\n[Tail of the log]:\n\n{short_log}"
    
    payload = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "txt" 
    }
    
    url = "http://www.pushplus.plus/send"
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.json().get("code") == 200:
            print("✅ Failure alert successfully pushed to WeChat.")
        else:
            print(f"⚠️ WeChat push returned an anomaly: {response.text}")
    except Exception as e:
        print(f"Fatal Error: The alert notification itself failed to send: {e}")


# =====================================================================
# 3. Execution Engine with Smart Retries
# =====================================================================

def run_with_smart_retries():
    """
    Wraps the main execution logic with a smart retry mechanism.
    Only retries on specific network failures (-1). Immediately aborts on logical/LLM failures (-2).
    Captures all console outputs and saves them to a structured log file.
    """
    # Retry limits specifically targeting arXiv fetch network failures
    max_retries = 5 # Prevents infinite loops if the arXiv API is completely down
    retry_delay = 5 # Wait time in seconds between retries

    log_buffer = io.StringIO()
    original_stdout = sys.stdout

    # Intercept standard output for logging
    sys.stdout = DualLogger(original_stdout, log_buffer)

    final_status = "Fail" # Default status assumes failure unless explicitly successful
    
    for attempt in range(1, max_retries + 1):
        print(f"=== [Attempt: {attempt}/{max_retries} | Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ===")

        try:
            # Execute the main pipeline routine
            result = main.run_job()

            if result in [1, 3]:
                if result == 1:
                    print("\n✅ Main pipeline finished: Report successfully pushed.")
                elif result == 3:
                    print("\n✅ Main pipeline finished: No relevant papers survived filtering. No notification sent.")
                
                final_status = "Success"
                break # Expected logic completed, exit the retry loop
                
            elif result == -1:
                print("\n❌ Encountered arXiv network fetch exception! (Triggering retry logic)")
                # Retry decision is handled at the end of the loop
                
            else: # result == -2 or any other unknown return code
                print("\n❌ Encountered non-network internal exception (e.g., LLM error, code bug). Terminating immediately without retries.")
                break # Fatal error, exit immediately

        except Exception as e:
            # Catch highly improbable crashes at the caller level when invoking main
            print(f"❌ Fatal Error: Caller caught an unhandled exception from main. Terminating:\n{traceback.format_exc()}")
            break 

        # Wait before the next retry if we haven't exhausted attempts
        if attempt < max_retries and result == -1:
            print(f"⏳ Waiting {retry_delay} seconds before the next arXiv fetch retry...\n")
            time.sleep(retry_delay)
        elif attempt == max_retries and result == -1:
            print(f"🚨 Reached maximum retry limit ({max_retries}). Giving up entirely.")

    # Restore standard output to normal
    sys.stdout = original_stdout

    # Extract the captured log string
    final_log_str = log_buffer.getvalue()

    # Ensure the Logs directory exists
    log_dir = "Logs"
    os.makedirs(log_dir, exist_ok=True)
    
    # Dynamically name the log file based on final execution status
    time_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    log_filename = os.path.join(log_dir, f"[{final_status}]_{time_str}.txt")

    with open(log_filename, "w", encoding="utf-8") as f:
        f.write(final_log_str)

    print(f"\nExecution finished. Logs persisted to: {log_filename}")

    # Trigger notification if the pipeline ultimately failed
    if final_status == "Fail":
        send_failure_alert(final_log_str)
        print("Failure alert dispatch process completed.")

if __name__ == "__main__":
    run_with_smart_retries()
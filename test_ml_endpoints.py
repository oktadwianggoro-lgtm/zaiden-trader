import requests
import time

def test_api():
    print("Triggering init db via /api/weekly/status")
    try:
        res = requests.get('http://127.0.0.1:8899/api/weekly/status')
        print(f"Status response: {res.status_code}")
        print(res.json())
    except Exception as e:
        print(f"Server not running? Error: {e}")

if __name__ == '__main__':
    test_api()

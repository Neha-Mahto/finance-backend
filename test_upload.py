import requests

url = "http://127.0.0.1:8000/upload"

files = [
    ("files", open("sales.csv", "rb")),
    ("files", open("expenses.csv", "rb")),
]
response = requests.post(url, files=files)
print(response.json())
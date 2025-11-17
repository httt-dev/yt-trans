import requests
import time

# 1. Tạo job
response = requests.post("http://localhost:8000/translate", json={
    "youtube_url": "https://www.youtube.com/watch?v=3FsDvRRf4cg",
    "voice_gender": "male"
})
job_id = response.json()["job_id"]

# 2. Polling status
while True:
    status = requests.get(f"http://localhost:8000/status/{job_id}").json()
    print(f"Progress: {status['progress']}% - {status['message']}")
    
    if status['status'] == 'completed':
        break
    elif status['status'] == 'failed':
        print(f"Error: {status['error']}")
        break
    
    time.sleep(5)

# 3. Download video
with open("translated_video.mp4", "wb") as f:
    video = requests.get(f"http://localhost:8000/download/{job_id}")
    f.write(video.content)
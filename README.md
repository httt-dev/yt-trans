
## 📦 Cài đặt
### Tạo và kích hoạt môi trường ảo Python 3.10.11                                                                   

python -m venv .venv

 .\.venv\Scripts\activate   


### Cài đặt thư viện Python
pip install yt-dlp youtube-transcript-api openai gtts pydub requests fastapi uvicorn python-multipart SpeechRecognition

pip uninstall youtube-transcript-api -y          

pip install youtube-transcript-api --upgrade  

pip install SpeechRecognition pydub 

python.exe .\main.py              

### Lưu danh sách các thư viện đã cài đặt vào requirements.txt
pip freeze > requirements.txt

### Cài đặt FFmpeg
### Ubuntu/Debian:
sudo apt install ffmpeg

### macOS:
brew install ffmpeg

# Windows: Tải từ https://ffmpeg.org/download.html

## 🔑 Thiết lập API Key

### Linux/macOS
export DEEPSEEK_API_KEY="your-api-key-here"

### Windows CMD
set DEEPSEEK_API_KEY=your-api-key-here

### Windows PowerShell
$env:DEEPSEEK_API_KEY="your-api-key-here"

## 🚀 Chạy ứng dụng FastAPI
python api.py

## API Endpoints

### Dịch video YouTube
POST /translate - Tạo job dịch video

curl -X POST "http://localhost:8000/translate" \
  -H "Content-Type: application/json" \
  -d '{
    "youtube_url": "https://youtube.com/watch?v=xxx",
    "voice_gender": "male"
  }'

GET /status/{job_id} - Kiểm tra trạng thái
curl "http://localhost:8000/status/abc-123-def"


GET /download/{job_id} - Tải video đã dịch

curl "http://localhost:8000/download/abc-123-def" -o video.mp4


GET /jobs - Liệt kê jobs
# Tất cả jobs
curl "http://localhost:8000/jobs?limit=20"

# Chỉ jobs đã hoàn thành
curl "http://localhost:8000/jobs?status=completed"

DELETE /job/{job_id} - Xóa job và files
curl -X DELETE "http://localhost:8000/job/abc-123-def"


## Working around IP bans (RequestBlocked or IpBlocked exception)
https://github.com/jdepoix/youtube-transcript-api?tab=readme-ov-file#working-around-ip-bans-requestblocked-or-ipblocked-exception
https://dashboard.webshare.io/proxy/settings

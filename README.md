



## 📦 Cài đặt
### Tạo và kích hoạt môi trường ảo Python 3.10.11                                                                   

python -m venv .venv

 .\.venv\Scripts\activate   


### Cài đặt thư viện Python
pip install yt-dlp youtube-transcript-api openai gtts pydub requests

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
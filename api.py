"""
YouTube Video Translator API with Vietnamese Voice-over
Yêu cầu cài đặt:
pip install yt-dlp youtube-transcript-api gtts pydub requests fastapi uvicorn python-multipart
Tùy chọn: pip install SpeechRecognition (cho video không có transcript)
"""

import os
import json
import re
import uuid
import time
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import requests
import random
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum

# FastAPI
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

# Thư viện xử lý YouTube
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig
from pytube import YouTube


# Thư viện xử lý audio
from gtts import gTTS
from pydub import AudioSegment

# Speech recognition (phương án dự phòng)
try:
    import speech_recognition as sr
    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    SPEECH_RECOGNITION_AVAILABLE = False

# Lấy API keys từ biến môi trường
# Support one or multiple DeepSeek API keys separated by comma in the env var
# Example: DEEPSEEK_API_KEY="key1,key2,key3"
DEEPSEEK_API_KEYS = [k.strip() for k in os.getenv('DEEPSEEK_API_KEY', '').split(',') if k.strip()]
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
TRANS_FROM_AUDIO = os.getenv('TRANS_FROM_AUDIO', 'False').lower() == 'true'

# FastAPI app
app = FastAPI(
    title="YouTube Video Translator API",
    description="API để dịch video YouTube sang tiếng Việt với voice-over",
    version="1.0.0"
)

# Mount static files (output folder)
Path("output").mkdir(exist_ok=True)
app.mount("/output", StaticFiles(directory="output"), name="output")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")


# Thread pool để xử lý đồng thời
executor = ThreadPoolExecutor(max_workers=5)

# Lưu trạng thái các job
jobs_status = {}
jobs_lock = threading.Lock()


class JobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    GENERATING_AUDIO = "generating_audio"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"


class TranslateRequest(BaseModel):
    youtube_url: HttpUrl
    voice_gender: str = "male"  # male hoặc female


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str
    youtube_url: Optional[str] = None
    video_id: Optional[str] = None
    progress: Optional[int] = 0
    output_file: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str


class YouTubeTitleExtractor:
    def __init__(self):
        self.methods = [self._oembed_method, self._pytube_method, self._scraping_method]
    
    def get_title(self, video_url, preferred_method='oembed'):
        """
        Lấy title YouTube với multiple fallback methods
        """
        # Validate URL
        if not self._is_valid_youtube_url(video_url):
            return "URL YouTube không hợp lệ"
        
        methods_order = self._get_methods_order(preferred_method)
        
        for method in methods_order:
            try:
                title = method(video_url)
                if title:
                    print(f"Title {method.__name__}: {title}")
                    return title
            except Exception as e:
                print(f"Method {method.__name__} failed: {e}")
                continue
                
        return "Không thể lấy title"
    
    def _oembed_method(self, video_url):
        """Phương pháp oEmbed"""
        oembed_url = f"https://www.youtube.com/oembed?url={video_url}&format=json"
        response = requests.get(oembed_url, timeout=10)
        response.raise_for_status()
        return response.json().get('title')
    
    def _pytube_method(self, video_url):
        """Phương pháp pytube"""
        yt = YouTube(video_url)
        return yt.title
    
    def _scraping_method(self, video_url):
        """Phương pháp web scraping"""
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(video_url, headers=headers, timeout=10)
        title_match = re.search(r'<title>(.*?) - YouTube</title>', response.text)
        return title_match.group(1) if title_match else None
    
    def _is_valid_youtube_url(self, url):
        """Kiểm tra URL YouTube hợp lệ"""
        patterns = [
            r'^https?://(www\.)?youtube\.com/watch\?v=',
            r'^https?://youtu\.be/',
            r'^https?://(www\.)?youtube\.com/embed/'
        ]
        return any(re.search(pattern, url) for pattern in patterns)
    
    def _get_methods_order(self, preferred):
        """Sắp xếp thứ tự methods theo preference"""
        method_map = {
            'oembed': self._oembed_method,
            'pytube': self._pytube_method,
            'scraping': self._scraping_method
        }
        
        if preferred in method_map:
            preferred_method = method_map[preferred]
            other_methods = [m for m in self.methods if m != preferred_method]
            return [preferred_method] + other_methods
        return self.methods
    
class YouTubeTranslator:
    def __init__(self, job_id: str, output_base_dir: str = "output", voice_gender: str = "male"):
        """
        Args:
            job_id: ID của job để tạo folder riêng
            output_base_dir: Thư mục gốc lưu output
            voice_gender: Giọng đọc - "male" (nam) hoặc "female" (nữ)
        """
        self.job_id = job_id
        self.output_base_dir = Path(output_base_dir)
        self.output_base_dir.mkdir(exist_ok=True)
        
        # Tạo folder riêng cho job này
        self.job_dir = None
        self.voice_gender = voice_gender
        self.temp_files = []
        
        # Đường dẫn đến cookies file
        self.cookies_file = Path("/app/cookies.txt")
        if self.cookies_file.exists() is False:
            self.cookies_file = Path("cookies.txt")

    def setup_job_directory(self, video_id: str):
        """Tạo thư mục riêng cho video theo video_id"""
        self.job_dir = self.output_base_dir / video_id
        self.job_dir.mkdir(exist_ok=True)
        return self.job_dir
        
    def extract_video_id(self, url: str) -> str:
        """Trích xuất video ID từ YouTube URL"""
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)',
            r'youtube\.com\/embed\/([^&\n?#]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        raise ValueError("Không thể trích xuất video ID từ URL")
    
    def update_job_status(self, status: JobStatus, progress: int = 0, message: str = "", error: str = None):
        """Cập nhật trạng thái job"""
        with jobs_lock:
            if self.job_id in jobs_status:
                jobs_status[self.job_id]['status'] = status
                jobs_status[self.job_id]['progress'] = progress
                jobs_status[self.job_id]['message'] = message
                jobs_status[self.job_id]['updated_at'] = datetime.now().isoformat()
                if error:
                    jobs_status[self.job_id]['error'] = error
    
    def get_ydl_opts(self, output_template: str, extract_audio: bool = False):
        """Tạo yt-dlp options với cookies nếu có"""
        opts = {
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
        }
        
        # Thêm cookies nếu file tồn tại
        if self.cookies_file.exists():
            opts['cookiefile'] = str(self.cookies_file)
            print(f"[{self.job_id[:8]}] Sử dụng cookies từ {self.cookies_file}")
        else:
            print(f"[{self.job_id[:8]}] ⚠️ Không tìm thấy cookies.txt, có thể gặp lỗi bot detection")
        
        if extract_audio:
            opts['format'] = 'bestaudio/best'
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        else:
            opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        
        return opts
    
    def download_video(self, url: str) -> str:
        """Tải video YouTube"""
        self.update_job_status(JobStatus.DOWNLOADING, 10, "Đang tải video...")
        
        video_id = self.extract_video_id(url)
        self.setup_job_directory(video_id)
        
        output_path = self.job_dir / f"{video_id}.mp4"
        
        if output_path.exists():
            print(f"Video đã tồn tại: {output_path}")
            self.temp_files.append(output_path)
            return str(output_path)
        
        # ydl_opts = {
        #     'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        #     'outtmpl': str(self.job_dir / f"{video_id}.%(ext)s"),
        #     'quiet': True,
        #     'no_warnings': True,
        # }
        ydl_opts = self.get_ydl_opts(
            output_template=str(self.job_dir / f"{video_id}.%(ext)s"),
            extract_audio=False
        )

        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        self.temp_files.append(output_path)
        return str(output_path)
    
    def download_audio(self, url: str) -> str:
        """Tải audio từ YouTube"""
        video_id = self.extract_video_id(url)
        output_path = self.job_dir / f"{video_id}_audio.mp3"
        
        if output_path.exists():
            print(f"Audio đã tồn tại: {output_path}")
            self.temp_files.append(output_path)
            return str(output_path)
        
        # ydl_opts = {
        #     'format': 'bestaudio/best',
        #     'postprocessors': [{
        #         'key': 'FFmpegExtractAudio',
        #         'preferredcodec': 'mp3',
        #         'preferredquality': '192',
        #     }],
        #     'outtmpl': str(self.job_dir / f"{video_id}_audio.%(ext)s"),
        #     'quiet': True,
        #     'no_warnings': True,
        # }
        ydl_opts = self.get_ydl_opts(
            output_template=str(self.job_dir / f"{video_id}_audio.%(ext)s"),
            extract_audio=True
        )
            
        with YoutubeDL(ydl_opts) as ydl:
            print(f"Đang tải audio: {url}")
            ydl.download([url])
        
        self.temp_files.append(output_path)
        return str(output_path)
    
    def transcribe_from_audio(self, url: str) -> List[Dict]:
        """Phương án dự phòng: Trích xuất text từ audio bằng speech recognition"""
        if not SPEECH_RECOGNITION_AVAILABLE:
            raise ImportError("Cần cài đặt SpeechRecognition: pip install SpeechRecognition")
        
        self.update_job_status(JobStatus.TRANSCRIBING, 25, "Đang tải audio để transcribe...")
        
        print("Đang tải audio để transcribe...")
        audio_path = self.download_audio(url)
        
        # Convert sang WAV để xử lý
        audio = AudioSegment.from_mp3(audio_path)
        wav_path = self.job_dir / "temp_audio.wav"
        audio.export(str(wav_path), format="wav")
        
        # Chia audio thành các đoạn nhỏ (30 giây mỗi đoạn)
        recognizer = sr.Recognizer()
        segments = []
        chunk_length_ms = 30000  # 30 giây
        
        print("Đang transcribe audio (có thể mất vài phút)...")
        total_chunks = len(list(range(0, len(audio), chunk_length_ms)))
        
        for i, start_ms in enumerate(range(0, len(audio), chunk_length_ms)):
            end_ms = min(start_ms + chunk_length_ms, len(audio))
            chunk = audio[start_ms:end_ms]
            
            chunk_path = self.job_dir / f"temp_chunk_{i}.wav"
            chunk.export(str(chunk_path), format="wav")
            
            # Cập nhật progress
            progress = 25 + int((i / total_chunks) * 20)
            self.update_job_status(JobStatus.TRANSCRIBING, progress,
                                  f"Đang transcribe đoạn {i+1}/{total_chunks}...")
            
            # Retry logic cho từng chunk
            text = None
            max_retries = 3
            
            for retry in range(max_retries):
                try:
                    with sr.AudioFile(str(chunk_path)) as source:
                        audio_data = recognizer.record(source)
                        text = recognizer.recognize_google(audio_data, language='en-US')
                        print(f"Đoạn {i+1}: {text[:50]}...")
                        break  # Thành công, thoát vòng lặp retry
                        
                except sr.UnknownValueError:
                    print(f"Không thể nhận dạng đoạn {i+1} (lần thử {retry+1}/{max_retries})")
                    if retry == max_retries - 1:
                        print(f"Bỏ qua đoạn {i+1} sau {max_retries} lần thử")
                        text = ""  # Gán empty nếu không nhận dạng được
                        
                except sr.RequestError as e:
                    print(f"Lỗi API Google Speech đoạn {i+1} (lần thử {retry+1}/{max_retries}): {e}")
                    if retry < max_retries - 1:
                        import time
                        time.sleep(2 ** retry)  # Exponential backoff: 1s, 2s, 4s
                    else:
                        print(f"Bỏ qua đoạn {i+1} sau {max_retries} lần thử")
                        text = ""
                        
                except Exception as e:
                    print(f"Lỗi không xác định đoạn {i+1} (lần thử {retry+1}/{max_retries}): {e}")
                    if retry == max_retries - 1:
                        text = ""
            
            # Chỉ thêm segment nếu có text
            if text:
                segments.append({
                    'start': start_ms / 1000.0,
                    'duration': (end_ms - start_ms) / 1000.0,
                    'text': text
                })
            
            chunk_path.unlink()
        
        wav_path.unlink()
        
        if not segments:
            raise Exception("Không thể transcribe bất kỳ đoạn audio nào")
        
        return segments
    
    def _merge_segments_by_duration(self, segments: List, target_duration: float = 30.0) -> List[Dict]:
        """Gộp các đoạn transcript nhỏ thành các đoạn dài khoảng target_duration giây"""
        merged_segments = []
        current_text = []
        current_start = 0.0
        current_duration = 0.0
        
        for segment in segments:
            segment_text = segment.text.strip()
            segment_start = segment.start
            segment_duration = segment.duration
            
            # Bỏ qua đoạn trống
            if not segment_text:
                continue
            
            # Nếu đây là đoạn đầu tiên
            if not current_text:
                current_start = segment_start
                current_duration = segment_duration
                current_text.append(segment_text)
            else:
                # Kiểm tra xem thêm đoạn này có vượt quá target_duration không
                potential_duration = segment_start + segment_duration - current_start
                
                if potential_duration <= target_duration:
                    # Thêm vào đoạn hiện tại
                    current_text.append(segment_text)
                    current_duration = segment_start + segment_duration - current_start
                else:
                    # Kết thúc đoạn hiện tại và bắt đầu đoạn mới
                    merged_segments.append({
                        "text": " ".join(current_text),
                        "start": current_start,
                        "duration": current_duration
                    })
                    
                    # Bắt đầu đoạn mới
                    current_start = segment_start
                    current_duration = segment_duration
                    current_text = [segment_text]
        
        # Thêm đoạn cuối cùng nếu còn
        if current_text:
            merged_segments.append({
                "text": " ".join(current_text),
                "start": current_start,
                "duration": current_duration
            })
        
        return merged_segments
    
    def get_transcript(self, url: str) -> List[Dict]:
        """Lấy transcript từ YouTube"""
        self.update_job_status(JobStatus.TRANSCRIBING, 20, "Đang lấy transcript...")
        
        video_id = self.extract_video_id(url)
        
        try:
            # Thử phương pháp mới trước (phiên bản >= 0.5.0)
            try:

                if TRANS_FROM_AUDIO:
                    return self.transcribe_from_audio(url)
                
                # transcript_list = YouTubeTranscriptApi(
                #     proxy_config=WebshareProxyConfig(
                #     proxy_username="cvccfzjw",
                #     proxy_password="b3nz4yr2rc8r",
                # )
                # ).list(video_id)
                
                transcript_list = YouTubeTranscriptApi().list(video_id)
                

                # Ưu tiên transcript thủ công (chính xác hơn)
                try:
                    transcript = transcript_list.find_manually_created_transcript(['en'])
                except:
                    transcript = transcript_list.find_generated_transcript(['en'])
                
                segments = transcript.fetch()
            except AttributeError:
                # Fallback cho phiên bản cũ
                segments = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
            
            print(f"Đã lấy {len(segments)} đoạn transcript")
            
            # 🔹 Gộp các đoạn nhỏ thành các đoạn 30 giây
            merged_segments = self._merge_segments_by_duration(segments, target_duration=30.0)
            
            print(f"Đã gộp thành {len(merged_segments)} đoạn transcript dài")

            # 🔹 Chuyển sang list of dict để JSON serializable
            segments_list = [
                {
                    "text": segment["text"],
                    "start": segment["start"],
                    "duration": segment["duration"]
                }
                for segment in merged_segments
            ]
            
            return segments_list
        
        except Exception as e:
            print(f"Lỗi khi lấy transcript: {e}")
            print("Video không có transcript. Sẽ sử dụng speech recognition...")
            return self.transcribe_from_audio(url)
    

    def translate_with_deepseek(self, text: str) -> str:
        """Dịch văn bản sang tiếng Việt bằng DeepSeek API"""
        if not DEEPSEEK_API_KEYS:
            raise ValueError("Chưa thiết lập DEEPSEEK_API_KEY")
        
        api_key = random.choice(DEEPSEEK_API_KEYS)

        headers = {
            "Authorization": f"Bearer {api_key}",

            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system",
                    "content": "Bạn là phiên dịch chuyên nghiệp. Dịch sang tiếng Việt tự nhiên, lưu loát."
                },
                {
                    "role": "user",
                    "content": f"Dịch sang tiếng Việt:\n\n{text}"
                }
            ],
            "temperature": 0.3,
        }
        
        max_retries = 3
        last_error = None
        
        time.sleep(1)

        for retry in range(max_retries):
            try:
                response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=45)
                response.raise_for_status()
                result = response.json()
                translated = result['choices'][0]['message']['content'].strip()
                
                if translated:  # Đảm bảo có kết quả
                    return translated
                else:
                    raise ValueError("API trả về kết quả rỗng")
                    
            except requests.exceptions.Timeout as e:
                last_error = f"Timeout (lần {retry+1}/{max_retries})"
                print(f"DeepSeek API timeout (lần thử {retry+1}/{max_retries})")
                
            except requests.exceptions.HTTPError as e:
                last_error = f"HTTP Error {e.response.status_code} (lần {retry+1}/{max_retries})"
                print(f"DeepSeek API HTTP error {e.response.status_code} (lần thử {retry+1}/{max_retries})")
                
                # Không retry nếu là lỗi 4xx (client error)
                if 400 <= e.response.status_code < 500:
                    print(f"Lỗi client, không retry. Response: {e.response.text[:200]}")
                    return text  # Trả về text gốc
                    
            except requests.exceptions.RequestException as e:
                last_error = f"Request Error (lần {retry+1}/{max_retries}): {str(e)}"
                print(f"DeepSeek API request error (lần thử {retry+1}/{max_retries}): {e}")
                
            except (KeyError, ValueError) as e:
                last_error = f"Parse Error (lần {retry+1}/{max_retries}): {str(e)}"
                print(f"DeepSeek API parse error (lần thử {retry+1}/{max_retries}): {e}")
                
            except Exception as e:
                last_error = f"Unknown Error (lần {retry+1}/{max_retries}): {str(e)}"
                print(f"DeepSeek API unknown error (lần thử {retry+1}/{max_retries}): {e}")
            
            # Exponential backoff nếu chưa phải lần thử cuối
            if retry < max_retries - 1:
                wait_time = 60 ** retry  # 1s, 2s, 4s
                print(f"Đợi {wait_time}s trước khi thử lại...")
                time.sleep(wait_time)
        
        # Sau 3 lần thử vẫn lỗi, trả về text gốc
        print(f"⚠️ Không thể dịch sau {max_retries} lần thử. Lỗi cuối: {last_error}")
        print(f"Trả về text gốc: {text[:100]}...")
        return text
    
    def translate_transcript(self, segments: List[Dict]) -> List[Dict]:
        """Dịch transcript sang tiếng Việt"""
        self.update_job_status(JobStatus.TRANSLATING, 45, "Đang dịch transcript...")
        
        translated_segments = []
        batch_size = 5
        total_batches = (len(segments) + batch_size - 1) // batch_size
        
        for i in range(0, len(segments), batch_size):
            batch = segments[i:i+batch_size]
            combined_text = " ".join([seg['text'] for seg in batch])
            
            # Cập nhật progress (45% -> 50%)
            current_batch = i // batch_size + 1
            progress = 45 + int((current_batch / total_batches) * 5)
            self.update_job_status(JobStatus.TRANSLATING, progress, 
                                  f"Đang dịch batch {current_batch}/{total_batches}...")
            print(f"[{self.job_id[:8]}] Dịch batch {current_batch}/{total_batches} - {progress}%")
            
            translated_text = self.translate_with_deepseek(combined_text)
            
            words = translated_text.split()
            words_per_segment = max(1, len(words) // len(batch))
            
            for j, seg in enumerate(batch):
                start_idx = j * words_per_segment
                end_idx = start_idx + words_per_segment if j < len(batch) - 1 else len(words)
                
                translated_segments.append({
                    'start': seg['start'],
                    'duration': seg['duration'],
                    'text': ' '.join(words[start_idx:end_idx])
                })
        
        return translated_segments
    
    def generate_audio_segments(self, segments: List[Dict]) -> List[Dict]:
        """Tạo audio cho từng segment"""
        self.update_job_status(JobStatus.GENERATING_AUDIO, 50, "Đang tạo audio...")
        
        audio_files = []
        tld = "com.au" if self.voice_gender == "male" else "com"
        total = len(segments)
        
        for i, seg in enumerate(segments):
            if not seg['text'].strip():
                continue
                
            audio_path = self.job_dir / f"segment_{i:04d}.mp3"
            
            try:
                tts = gTTS(text=seg['text'], lang='vi', tld=tld, slow=False)
                tts.save(str(audio_path))
                
                self.temp_files.append(audio_path)
                
                audio_files.append({
                    'path': str(audio_path),
                    'start': seg['start'],
                    'duration': seg['duration'],
                    'text': seg['text']
                })
                
                # Cập nhật progress (50% -> 80%)
                progress = 50 + int(((i + 1) / total) * 30)
                self.update_job_status(JobStatus.GENERATING_AUDIO, progress,
                                      f"Đã tạo audio {i+1}/{total}")
                print(f"[{self.job_id[:8]}] Tạo audio {i+1}/{total} - {progress}%")
            except Exception as e:
                print(f"Lỗi tạo audio segment {i}: {e}")
        
        return audio_files
    
    def adjust_audio_speed(self, audio_path: str, target_duration: float) -> AudioSegment:
        """Điều chỉnh tốc độ audio"""
        audio = AudioSegment.from_mp3(audio_path)
        current_duration = len(audio) / 1000.0
        
        if current_duration == 0:
            return audio
        
        speedup = current_duration / target_duration
        speedup = max(0.8, min(1.5, speedup))
        
        if speedup != 1.0:
            new_frame_rate = int(audio.frame_rate * speedup)
            audio = audio._spawn(audio.raw_data, overrides={'frame_rate': new_frame_rate})
            audio = audio.set_frame_rate(44100)
        
        return audio
    
    def merge_audio_with_video(self, video_path: str, audio_segments: List[Dict], output_path: str):
        """Ghép audio vào video"""
        self.update_job_status(JobStatus.MERGING, 80, "Đang tạo audio track...")
        print(f"[{self.job_id[:8]}] Bắt đầu merge audio - 80%")
        
        # Lấy thời lượng video
        video_info = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
             '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
            capture_output=True, text=True
        )
        video_duration = float(video_info.stdout.strip()) * 1000
        
        final_audio = AudioSegment.silent(duration=int(video_duration))
        
        # Overlay audio segments
        total_segments = len(audio_segments)
        for idx, seg in enumerate(audio_segments):
            try:
                audio = self.adjust_audio_speed(seg['path'], seg['duration'])
                start_ms = int(seg['start'] * 1000)
                final_audio = final_audio.overlay(audio, position=start_ms)
                
                # Cập nhật progress trong quá trình overlay (80% -> 85%)
                if idx % 5 == 0 or idx == total_segments - 1:
                    progress = 80 + int((idx / total_segments) * 5)
                    self.update_job_status(JobStatus.MERGING, progress,
                                          f"Đang overlay audio {idx+1}/{total_segments}")
            except Exception as e:
                print(f"Lỗi xử lý segment: {e}")
        
        # Xuất audio
        self.update_job_status(JobStatus.MERGING, 85, "Đang xuất audio track...")
        print(f"[{self.job_id[:8]}] Xuất audio track - 85%")
        
        audio_output = self.job_dir / "translated_audio.mp3"
        final_audio.export(str(audio_output), format="mp3", bitrate="192k")
        self.temp_files.append(audio_output)
        
        # Ghép bằng FFmpeg
        self.update_job_status(JobStatus.MERGING, 90, "Đang ghép audio vào video...")
        print(f"[{self.job_id[:8]}] FFmpeg merge - 90%")
        
        cmd = [
            'ffmpeg', '-i', video_path, '-i', str(audio_output),
            '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-shortest', '-y', output_path
        ]
        
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"[{self.job_id[:8]}] Hoàn thành merge - 100%")
    
    def cleanup_temp_files(self):
        """Xóa file tạm"""
        # Xóa JSON files
        for json_file in self.job_dir.glob("*.json"):
            try:
                json_file.unlink()
            except:
                pass
        
        # Xóa temp files
        for temp_file in self.temp_files:
            try:
                Path(temp_file).unlink()
            except:
                pass
        
        # Xóa segments
        for seg in self.job_dir.glob("segment_*.mp3"):
            try:
                seg.unlink()
            except:
                pass
    
    def process(self, youtube_url: str) -> str:
        """Xử lý toàn bộ quy trình"""
        try:
            # 1. Tải video
            video_path = self.download_video(youtube_url)
            video_id = self.extract_video_id(youtube_url)
            
            # Cập nhật video_id vào job status
            with jobs_lock:
                jobs_status[self.job_id]['video_id'] = video_id
            
            # 2. Lấy transcript
            segments = self.get_transcript(youtube_url)
            
            # Lưu transcript gốc
            with open(self.job_dir / "original_transcript.json", 'w', encoding='utf-8') as f:
                json.dump(segments, f, ensure_ascii=False, indent=2)
            
            # 3. Dịch transcript
            translated_segments = self.translate_transcript(segments)
            
            # Lưu transcript đã dịch
            with open(self.job_dir / "translated_transcript.json", 'w', encoding='utf-8') as f:
                json.dump(translated_segments, f, ensure_ascii=False, indent=2)
            
            # 4. Tạo audio
            audio_segments = self.generate_audio_segments(translated_segments)
            
            # 5. Ghép audio vào video
            output_filename = f"{video_id}_vietnamese.mp4"
            output_path = str(self.job_dir / output_filename)
            self.merge_audio_with_video(video_path, audio_segments, output_path)
            
            # 6. Dọn dẹp
            self.cleanup_temp_files()
            
            # 7. Hoàn thành
            self.update_job_status(JobStatus.COMPLETED, 100, "Hoàn thành!")
            with jobs_lock:
                jobs_status[self.job_id]['output_file'] = output_filename
            
            return output_path
            
        except Exception as e:
            error_msg = str(e)
            self.update_job_status(JobStatus.FAILED, 0, "Lỗi xử lý", error_msg)
            self.cleanup_temp_files()
            raise


def process_video_background(job_id: str, youtube_url: str, voice_gender: str):
    """Hàm xử lý video trong background thread"""
    translator = YouTubeTranslator(job_id=job_id, voice_gender=voice_gender)
    try:
        translator.process(youtube_url)
    except Exception as e:
        print(f"Lỗi xử lý job {job_id}: {e}")

def get_youtube_title_pytube(video_url):
    """
    Lấy title video YouTube sử dụng pytube
    """
    # try:
    #     yt = YouTube(video_url)
    #     return yt.title
    # except Exception as e:
    #     print(f"Lỗi: {e}")
    #     return None
    extractor = YouTubeTitleExtractor()
    return extractor.get_title(video_url)


@app.get("/", response_class=HTMLResponse)
def read_root():
    """Trang chủ với frontend"""
    html_content = """
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link rel="icon" href="/assets/images/favicon.ico" type="image/x-icon">
        <title>YouTube Video Translator - Dịch video sang tiếng Việt</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
            
            header {
                background: white;
                padding: 30px;
                border-radius: 15px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.1);
                margin-bottom: 30px;
            }
            
            h1 {
                color: #333;
                margin-bottom: 20px;
                font-size: 28px;
            }
            
            .input-group {
                display: flex;
                gap: 10px;
                margin-bottom: 15px;
            }
            
            input[type="text"] {
                flex: 1;
                padding: 15px;
                border: 2px solid #e0e0e0;
                border-radius: 10px;
                font-size: 16px;
                transition: border 0.3s;
            }
            
            input[type="text"]:focus {
                outline: none;
                border-color: #667eea;
            }
            
            .voice-select {
                display: flex;
                gap: 15px;
                align-items: center;
                margin-bottom: 15px;
            }
            
            .voice-option {
                display: flex;
                align-items: center;
                gap: 5px;
            }
            
            button {
                padding: 15px 40px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: transform 0.2s;
            }
            
            button:hover {
                transform: translateY(-2px);
            }
            
            button:disabled {
                opacity: 0.6;
                cursor: not-allowed;
            }
            
            .status {
                margin-top: 15px;
                padding: 15px;
                border-radius: 10px;
                display: none;
            }
            
            .status.info {
                background: #e3f2fd;
                color: #1976d2;
                display: block;
            }
            
            .status.success {
                background: #e8f5e9;
                color: #388e3c;
                display: block;
            }
            
            .status.error {
                background: #ffebee;
                color: #d32f2f;
                display: block;
            }
            
            .progress-bar {
                width: 100%;
                height: 6px;
                background: #e0e0e0;
                border-radius: 3px;
                overflow: hidden;
                margin-top: 10px;
                display: none;
            }
            
            .progress-bar.active {
                display: block;
            }
            
            .progress-fill {
                height: 100%;
                background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
                width: 0%;
                transition: width 0.3s;
            }
            
            .videos-section {
                background: white;
                padding: 30px;
                border-radius: 15px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            }
            
            .videos-section h2 {
                color: #333;
                margin-bottom: 20px;
                font-size: 24px;
            }
            
            .video-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 20px;
            }
            
            .video-card {
                background: #f5f5f5;
                border-radius: 10px;
                overflow: hidden;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
            }
            
            .video-card:hover {
                transform: translateY(-5px);
                box-shadow: 0 10px 30px rgba(0,0,0,0.15);
            }
            
            .video-thumbnail {
                width: 100%;
                height: 180px;
                object-fit: cover;
                background: #e0e0e0;
            }
            
            .video-info {
                padding: 15px;
            }
            
            .video-title {
                font-weight: 600;
                color: #333;
                margin-bottom: 5px;
                font-size: 14px;
                line-height: 1.4;
                display: -webkit-box;
                -webkit-line-clamp: 2;
                -webkit-box-orient: vertical;
                overflow: hidden;
            }
            
            .video-id {
                color: #666;
                font-size: 12px;
            }
            
            .empty-state {
                text-align: center;
                padding: 60px 20px;
                color: #999;
            }
            
            .empty-state svg {
                width: 80px;
                height: 80px;
                margin-bottom: 20px;
                opacity: 0.3;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>🎬 YouTube Video Translator</h1>
                <p style="color: #666; margin-bottom: 20px;">Dịch video YouTube sang tiếng Việt với voice-over tự động</p>
                
                <div class="input-group">
                    <input 
                        type="text" 
                        id="youtubeUrl" 
                        placeholder="Nhập YouTube URL (vd: https://youtube.com/watch?v=...)"
                    >
                    <button id="translateBtn" onclick="translateVideo()">
                        Dịch
                    </button>
                </div>
                <div class="voice-select">
                    <span style="color: #666;">Giọng đọc:</span>
                    <div class="voice-option">
                        <input type="radio" id="voiceMale" name="voice" value="male" checked>
                        <label for="voiceMale">Nam</label>
                    </div>
                    <div class="voice-option">
                        <input type="radio" id="voiceFemale" name="voice" value="female">
                        <label for="voiceFemale">Nữ</label>
                    </div>
                </div>
                <div id="status" class="status"></div>
                <div id="progressBar" class="progress-bar">
                    <div id="progressFill" class="progress-fill"></div>
                </div>
            </header>
            
            <div class="videos-section">
                <h2>📚 Video đã dịch</h2>
                <div id="videoGrid" class="video-grid">
                    <div class="empty-state">
                        <svg fill="currentColor" viewBox="0 0 20 20">
                            <path d="M2 6a2 2 0 012-2h6a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V6zM14.553 7.106A1 1 0 0014 8v4a1 1 0 00.553.894l2 1A1 1 0 0018 13V7a1 1 0 00-1.447-.894l-2 1z"/>
                        </svg>
                        <p>Chưa có video nào được dịch</p>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            let currentJobId = null;
            let pollingInterval = null;
            
            async function translateVideo() {
                const url = document.getElementById('youtubeUrl').value.trim();
                const voiceGender = document.querySelector('input[name="voice"]:checked').value;
                
                if (!url) {
                    showStatus('Vui lòng nhập YouTube URL', 'error');
                    return;
                }
                
                const btn = document.getElementById('translateBtn');
                btn.disabled = true;
                btn.textContent = 'Đang xử lý...';
                
                showStatus('Đang tạo job dịch video...', 'info');
                
                try {
                    const response = await fetch('/translate', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({
                            youtube_url: url,
                            voice_gender: voiceGender
                        })
                    });
                    
                    const data = await response.json();
                    
                    if (response.ok) {
                        currentJobId = data.job_id;
                        showStatus(`Job đã tạo! ID: ${currentJobId.substring(0, 8)}...`, 'info');
                        startPolling(currentJobId);
                    } else {
                        throw new Error(data.detail || 'Lỗi khi tạo job');
                    }
                } catch (error) {
                    showStatus('Lỗi: ' + error.message, 'error');
                    btn.disabled = false;
                    btn.textContent = 'Dịch sang Tiếng Việt';
                }
            }
            
            function startPolling(jobId) {
                const progressBar = document.getElementById('progressBar');
                const progressFill = document.getElementById('progressFill');
                progressBar.classList.add('active');
                
                pollingInterval = setInterval(async () => {
                    try {
                        const response = await fetch(`/status/${jobId}`);
                        const data = await response.json();
                        
                        progressFill.style.width = data.progress + '%';
                        showStatus(`${data.message} (${data.progress}%)`, 'info');
                        
                        if (data.status === 'completed') {
                            clearInterval(pollingInterval);
                            showStatus('✅ Hoàn thành! Đang tải lại danh sách...', 'success');
                            progressBar.classList.remove('active');
                            
                            const btn = document.getElementById('translateBtn');
                            btn.disabled = false;
                            btn.textContent = 'Dịch sang Tiếng Việt';
                            
                            // Reload videos
                            setTimeout(() => {
                                loadVideos();
                                document.getElementById('youtubeUrl').value = '';
                            }, 1000);
                            
                        } else if (data.status === 'failed') {
                            clearInterval(pollingInterval);
                            showStatus('❌ Lỗi: ' + (data.error || 'Không xác định'), 'error');
                            progressBar.classList.remove('active');
                            
                            const btn = document.getElementById('translateBtn');
                            btn.disabled = false;
                            btn.textContent = 'Dịch sang Tiếng Việt';
                        }
                    } catch (error) {
                        console.error('Polling error:', error);
                    }
                }, 2000);
            }
            
            function showStatus(message, type) {
                const status = document.getElementById('status');
                status.textContent = message;
                status.className = 'status ' + type;
            }
            
            async function loadVideos() {
                try {
                    const response = await fetch('/api/videos');
                    const data = await response.json();
                    
                    const grid = document.getElementById('videoGrid');
                    
                    if (data.videos.length === 0) {
                        grid.innerHTML = `
                            <div class="empty-state">
                                <svg fill="currentColor" viewBox="0 0 20 20">
                                    <path d="M2 6a2 2 0 012-2h6a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V6zM14.553 7.106A1 1 0 0014 8v4a1 1 0 00.553.894l2 1A1 1 0 0018 13V7a1 1 0 00-1.447-.894l-2 1z"/>
                                </svg>
                                <p>Chưa có video nào được dịch</p>
                            </div>
                        `;
                        return;
                    }
                    
                    grid.innerHTML = data.videos.map(video => `
                        <div class="video-card" onclick="window.open('/output/${video.video_id}/${video.filename}', '_blank')">
                            <img 
                                src="https://img.youtube.com/vi/${video.video_id}/mqdefault.jpg" 
                                alt="${video.title || video.video_id}"
                                class="video-thumbnail"
                            >
                            <div class="video-info">
                                <div class="video-title">${video.title || 'Video đã dịch'}</div>
                                <div class="video-id">ID: ${video.video_id}</div>
                            </div>
                        </div>
                    `).join('');
                } catch (error) {
                    console.error('Load videos error:', error);
                }
            }
            
            // Load videos on page load
            loadVideos();
            
            // Enter key support
            document.getElementById('youtubeUrl').addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    translateVideo();
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.post("/translate", response_model=JobResponse)
def translate_video(request: TranslateRequest, background_tasks: BackgroundTasks):
    """
    Tạo job dịch video YouTube
    
    - **youtube_url**: URL video YouTube
    - **voice_gender**: Giọng đọc (male/female)
    """
    # Tạo job ID
    job_id = str(uuid.uuid4())
    
    # Khởi tạo job status
    now = datetime.now().isoformat()
    with jobs_lock:
        jobs_status[job_id] = {
            'job_id': job_id,
            'status': JobStatus.PENDING,
            'message': 'Đang khởi tạo...',
            'youtube_url': str(request.youtube_url),
            'video_id': None,
            'progress': 0,
            'output_file': None,
            'error': None,
            'created_at': now,
            'updated_at': now
        }
    
    # Thêm vào background tasks
    background_tasks.add_task(
        process_video_background,
        job_id,
        str(request.youtube_url),
        request.voice_gender
    )
    
    return JobResponse(**jobs_status[job_id])


@app.get("/status/{job_id}", response_model=JobResponse)
def get_job_status(job_id: str):
    """
    Lấy trạng thái của job
    
    - **job_id**: ID của job cần kiểm tra
    """
    with jobs_lock:
        if job_id not in jobs_status:
            raise HTTPException(status_code=404, detail="Job không tồn tại")
        return JobResponse(**jobs_status[job_id])


@app.get("/download/{job_id}")
def download_video(job_id: str):
    """
    Tải video đã dịch
    
    - **job_id**: ID của job đã hoàn thành
    """
    with jobs_lock:
        if job_id not in jobs_status:
            raise HTTPException(status_code=404, detail="Job không tồn tại")
        
        job = jobs_status[job_id]
        
        if job['status'] != JobStatus.COMPLETED:
            raise HTTPException(status_code=400, detail="Job chưa hoàn thành")
        
        video_id = job['video_id']
        output_file = job['output_file']
        
        if not video_id or not output_file:
            raise HTTPException(status_code=404, detail="File không tồn tại")
        
        file_path = Path("output") / video_id / output_file
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File không tồn tại")
        
        return FileResponse(
            path=str(file_path),
            media_type="video/mp4",
            filename=output_file
        )


@app.get("/api/videos")
def list_translated_videos():
    """
    API để list tất cả video đã dịch trong folder output
    """
    output_dir = Path("output")
    videos = []
    
    if not output_dir.exists():
        return {"videos": []}
    
    # Duyệt qua các folder video_id
    for video_dir in output_dir.iterdir():
        if not video_dir.is_dir():
            continue
        
        video_id = video_dir.name
        
        # Tìm file *_vietnamese.mp4
        vietnamese_files = list(video_dir.glob("*_vietnamese.mp4"))
        
        if vietnamese_files:
            video_file = vietnamese_files[0]
            # Lấy title video sử dụng pytube
            title = get_youtube_title_pytube(f"https://www.youtube.com/watch?v={video_id}")
            
            # Lấy thông tin file
            file_stat = video_file.stat()
            
            videos.append({
                "video_id": video_id,
                "filename": video_file.name,
                "title": title,  
                "file_size": file_stat.st_size,
                "created_at": datetime.fromtimestamp(file_stat.st_ctime).isoformat(),
                "url": f"/output/{video_id}/{video_file.name}"
            })
    
    # Sắp xếp theo thời gian tạo (mới nhất trước)
    videos.sort(key=lambda x: x['created_at'], reverse=True)
    
    return {"videos": videos}


@app.get("/jobs")
def list_jobs(status: Optional[JobStatus] = None, limit: int = Query(10, ge=1, le=100)):
    """
    Liệt kê các job
    
    - **status**: Lọc theo trạng thái (pending/downloading/translating/...)
    - **limit**: Số lượng job tối đa trả về (1-100)
    """
    with jobs_lock:
        jobs_list = list(jobs_status.values())
        
        # Lọc theo status nếu có
        if status:
            jobs_list = [job for job in jobs_list if job['status'] == status]
        
        # Sắp xếp theo thời gian tạo (mới nhất trước)
        jobs_list.sort(key=lambda x: x['created_at'], reverse=True)
        
        # Giới hạn số lượng
        jobs_list = jobs_list[:limit]
        
        return {
            "total": len(jobs_list),
            "jobs": jobs_list
        }


@app.delete("/job/{job_id}")
def delete_job(job_id: str):
    """
    Xóa job và file liên quan
    
    - **job_id**: ID của job cần xóa
    """
    with jobs_lock:
        if job_id not in jobs_status:
            raise HTTPException(status_code=404, detail="Job không tồn tại")
        
        job = jobs_status[job_id]
        video_id = job.get('video_id')
        
        # Xóa folder nếu có
        if video_id:
            job_dir = Path("output") / video_id
            if job_dir.exists():
                shutil.rmtree(job_dir)
        
        # Xóa khỏi jobs_status
        del jobs_status[job_id]
        
        return {"message": f"Đã xóa job {job_id}"}


if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Starting YouTube Video Translator API...")
    print("📖 API Documentation: http://localhost:8000/docs")
    print("🔍 Alternative docs: http://localhost:8000/redoc")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
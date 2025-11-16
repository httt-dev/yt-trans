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
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import requests
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum

# FastAPI
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, HttpUrl

# Thư viện xử lý YouTube
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi

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
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

# FastAPI app
app = FastAPI(
    title="YouTube Video Translator API",
    description="API để dịch video YouTube sang tiếng Việt với voice-over",
    version="1.0.0"
)

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
        
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': str(self.job_dir / f"{video_id}.%(ext)s"),
            'quiet': True,
            'no_warnings': True,
        }
        
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
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': str(self.job_dir / f"{video_id}_audio.%(ext)s"),
            'quiet': True,
            'no_warnings': True,
        }
        
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
            
            try:
                with sr.AudioFile(str(chunk_path)) as source:
                    audio_data = recognizer.record(source)
                    text = recognizer.recognize_google(audio_data, language='en-US')
                    
                    segments.append({
                        'start': start_ms / 1000.0,
                        'duration': (end_ms - start_ms) / 1000.0,
                        'text': text
                    })
                    print(f"Đoạn {i+1}: {text[:50]}...")
            except Exception as e:
                print(f"Không thể transcribe đoạn {i+1}: {e}")
            
            chunk_path.unlink()
        
        wav_path.unlink()
        return segments
    
    def get_transcript(self, url: str) -> List[Dict]:
        """Lấy transcript từ YouTube"""
        self.update_job_status(JobStatus.TRANSCRIBING, 20, "Đang lấy transcript...")
        
        video_id = self.extract_video_id(url)
        
        try:
            # Thử phương pháp mới trước (phiên bản >= 0.5.0)
            try:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                
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
            return segments
            
        except Exception as e:
            print(f"Lỗi khi lấy transcript: {e}")
            print("Video không có transcript. Sẽ sử dụng speech recognition...")
            return self.transcribe_from_audio(url)
    
    def translate_with_deepseek(self, text: str) -> str:
        """Dịch văn bản sang tiếng Việt bằng DeepSeek API"""
        if not DEEPSEEK_API_KEY:
            raise ValueError("Chưa thiết lập DEEPSEEK_API_KEY")
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
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
        
        try:
            response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            return result['choices'][0]['message']['content'].strip()
        except Exception as e:
            print(f"Lỗi DeepSeek API: {e}")
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


@app.get("/")
def read_root():
    """Endpoint kiểm tra API"""
    return {
        "service": "YouTube Video Translator API",
        "version": "1.0.0",
        "status": "running"
    }


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
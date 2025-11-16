"""
YouTube Video Translator with Vietnamese Voice-over
Yêu cầu cài đặt:
pip install yt-dlp youtube-transcript-api openai gtts pydub requests SpeechRecognition
Tùy chọn (cho speech recognition): pip install pocketsphinx
"""

import os
import json
import re
import time
from pathlib import Path
from typing import List, Dict
import requests
import subprocess

# Thư viện xử lý YouTube
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi

# Thư viện xử lý audio
from gtts import gTTS
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

# Speech recognition (phương án dự phòng)
try:
    import speech_recognition as sr
    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    SPEECH_RECOGNITION_AVAILABLE = False

# Lấy API keys từ biến môi trường
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"


class YouTubeTranslator:
    def __init__(self, output_dir: str = "output", voice_gender: str = "male"):
        """
        Args:
            output_dir: Thư mục lưu output
            voice_gender: Giọng đọc - "male" (nam) hoặc "female" (nữ)
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.voice_gender = voice_gender
        self.temp_files = []  # Danh sách file cần xóa
        
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
    
    def download_audio(self, url: str) -> str:
        """Tải audio từ YouTube"""
        video_id = self.extract_video_id(url)
        output_path = self.output_dir / f"{video_id}_audio.mp3"
        
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
            'outtmpl': str(self.output_dir / f"{video_id}_audio.%(ext)s"),
            'quiet': False,
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
        
        print("Đang tải audio để transcribe...")
        audio_path = self.download_audio(url)
        
        # Convert sang WAV để xử lý
        audio = AudioSegment.from_mp3(audio_path)
        wav_path = self.output_dir / "temp_audio.wav"
        audio.export(str(wav_path), format="wav")
        
        # Chia audio thành các đoạn nhỏ (30 giây mỗi đoạn)
        recognizer = sr.Recognizer()
        segments = []
        chunk_length_ms = 30000  # 30 giây
        
        print("Đang transcribe audio (có thể mất vài phút)...")
        for i, start_ms in enumerate(range(0, len(audio), chunk_length_ms)):
            end_ms = min(start_ms + chunk_length_ms, len(audio))
            chunk = audio[start_ms:end_ms]
            
            chunk_path = self.output_dir / f"temp_chunk_{i}.wav"
            chunk.export(str(chunk_path), format="wav")
            
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
    
    def download_video(self, url: str) -> str:
        """Tải video YouTube"""
        video_id = self.extract_video_id(url)
        output_path = self.output_dir / f"{video_id}.mp4"
        
        if output_path.exists():
            print(f"Video đã tồn tại: {output_path}")
            self.temp_files.append(output_path)
            return str(output_path)
        
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': str(self.output_dir / f"{video_id}.%(ext)s"),
            'quiet': False,
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            print(f"Đang tải video: {url}")
            ydl.download([url])
        
        self.temp_files.append(output_path)
        return str(output_path)
    
    def get_transcript(self, url: str) -> List[Dict]:
        """Lấy transcript từ YouTube"""
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
            raise ValueError("Chưa thiết lập DEEPSEEK_API_KEY trong biến môi trường")
        
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system",
                    "content": "Bạn là một phiên dịch chuyên nghiệp. Hãy dịch văn bản sang tiếng Việt một cách tự nhiên, lưu loát và giữ đúng ý nghĩa gốc."
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
            print(f"Lỗi khi gọi DeepSeek API: {e}")
            return text  # Fallback về text gốc nếu lỗi
    
    def translate_transcript(self, segments: List[Dict]) -> List[Dict]:
        """Dịch transcript sang tiếng Việt"""
        translated_segments = []
        
        # Gộp các câu ngắn để dịch hiệu quả hơn
        batch_size = 5
        for i in range(0, len(segments), batch_size):
            batch = segments[i:i+batch_size]
            
            # Ghép text để dịch
            combined_text = " ".join([seg['text'] for seg in batch])
            
            print(f"Đang dịch đoạn {i+1}-{min(i+batch_size, len(segments))}/{len(segments)}...")
            translated_text = self.translate_with_deepseek(combined_text)
            
            # Tách lại thành các segment với timing gốc
            # Đơn giản hoá: chia đều translated text theo số segment
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
    
    def generate_audio_segments(self, segments: List[Dict]) -> List[str]:
        """Tạo audio cho từng segment"""
        audio_files = []
        
        # Cấu hình giọng đọc
        if self.voice_gender == "male":
            # Sử dụng các TLD khác nhau để có giọng nam tự nhiên hơn
            # com.au (Úc), co.uk (Anh), co.in (Ấn Độ) thường có giọng nam tốt
            tld = "com.au"
            slow = False
        else:
            tld = "com"  # Giọng nữ mặc định
            slow = False
        
        for i, seg in enumerate(segments):
            if not seg['text'].strip():
                continue
                
            audio_path = self.output_dir / f"segment_{i:04d}.mp3"
            
            try:
                # Tạo giọng đọc với tham số tùy chỉnh
                tts = gTTS(text=seg['text'], lang='vi', tld=tld, slow=slow)
                tts.save(str(audio_path))
                
                self.temp_files.append(audio_path)  # Đánh dấu để xóa sau
                
                audio_files.append({
                    'path': str(audio_path),
                    'start': seg['start'],
                    'duration': seg['duration'],
                    'text': seg['text']
                })
                print(f"Đã tạo audio {i+1}/{len(segments)} ({'nam' if self.voice_gender == 'male' else 'nữ'})")
            except Exception as e:
                print(f"Lỗi khi tạo audio cho segment {i}: {e}")
        
        return audio_files
    
    def adjust_audio_speed(self, audio_path: str, target_duration: float) -> AudioSegment:
        """Điều chỉnh tốc độ audio để khớp với duration mong muốn"""
        audio = AudioSegment.from_mp3(audio_path)
        current_duration = len(audio) / 1000.0  # Convert to seconds
        
        if current_duration == 0:
            return audio
        
        # Tính toán speedup factor (giới hạn trong khoảng 0.8 - 1.5)
        speedup = current_duration / target_duration
        speedup = max(0.8, min(1.5, speedup))
        
        # Thay đổi tốc độ bằng cách thay đổi frame rate
        if speedup != 1.0:
            new_frame_rate = int(audio.frame_rate * speedup)
            audio = audio._spawn(audio.raw_data, overrides={'frame_rate': new_frame_rate})
            audio = audio.set_frame_rate(44100)  # Normalize lại frame rate
        
        return audio
    
    def merge_audio_with_video(self, video_path: str, audio_segments: List[Dict], output_path: str):
        """Ghép audio đã dịch vào video gốc"""
        # Tạo audio track hoàn chỉnh
        print("Đang tạo audio track hoàn chỉnh...")
        
        # Tạo silent audio làm nền
        video_info = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
             '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
            capture_output=True, text=True
        )
        video_duration = float(video_info.stdout.strip()) * 1000  # Convert to ms
        
        final_audio = AudioSegment.silent(duration=int(video_duration))
        
        # Thêm từng segment audio vào đúng vị trí
        for seg in audio_segments:
            try:
                audio = self.adjust_audio_speed(seg['path'], seg['duration'])
                start_ms = int(seg['start'] * 1000)
                
                # Overlay audio segment
                final_audio = final_audio.overlay(audio, position=start_ms)
            except Exception as e:
                print(f"Lỗi khi xử lý segment {seg['text'][:30]}...: {e}")
        
        # Xuất audio track
        audio_output = self.output_dir / "translated_audio.mp3"
        final_audio.export(str(audio_output), format="mp3", bitrate="192k")
        print(f"Đã tạo audio track: {audio_output}")
        self.temp_files.append(audio_output)  # Đánh dấu để xóa
        
        # Ghép audio vào video bằng FFmpeg
        print("Đang ghép audio vào video...")
        cmd = [
            'ffmpeg', '-i', video_path, '-i', str(audio_output),
            '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-shortest', '-y', output_path
        ]
        
        subprocess.run(cmd, check=True)
        print(f"Hoàn thành! Video đã dịch: {output_path}")
    
    def process(self, youtube_url: str):
        """Xóa tất cả file tạm và file gốc, chỉ giữ file đã dịch"""
        print("\n=== DỌN DẸP FILE TẠM ===")
        
        # Xóa các file JSON transcript
        json_files = [
            self.output_dir / "original_transcript.json",
            self.output_dir / "translated_transcript.json"
        ]
        
        for json_file in json_files:
            if json_file.exists():
                try:
                    json_file.unlink()
                    print(f"Đã xóa: {json_file.name}")
                except Exception as e:
                    print(f"Không thể xóa {json_file.name}: {e}")
        
        # Xóa các file đã đánh dấu
        for temp_file in self.temp_files:
            if isinstance(temp_file, (str, Path)):
                temp_path = Path(temp_file)
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                        print(f"Đã xóa: {temp_path.name}")
                    except Exception as e:
                        print(f"Không thể xóa {temp_path.name}: {e}")
        
        # Xóa các file segment còn sót
        for segment_file in self.output_dir.glob("segment_*.mp3"):
            try:
                segment_file.unlink()
                print(f"Đã xóa: {segment_file.name}")
            except Exception as e:
                print(f"Không thể xóa {segment_file.name}: {e}")
        
        # Xóa các file temp chunk từ speech recognition
        for chunk_file in self.output_dir.glob("temp_*.wav"):
            try:
                chunk_file.unlink()
                print(f"Đã xóa: {chunk_file.name}")
            except Exception as e:
                print(f"Không thể xóa {chunk_file.name}: {e}")
        
        print("Hoàn tất dọn dẹp!")
        """Xử lý toàn bộ quy trình"""
        print("=== BẮT ĐẦU XỬ LÝ ===")
        
        # 1. Tải video
        video_path = self.download_video(youtube_url)
        
        # 2. Lấy transcript
        print("\n=== LẤY TRANSCRIPT ===")
        segments = self.get_transcript(youtube_url)
        
        # Lưu transcript gốc
        with open(self.output_dir / "original_transcript.json", 'w', encoding='utf-8') as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        
        # 3. Dịch transcript
        print("\n=== DỊCH TRANSCRIPT ===")
        translated_segments = self.translate_transcript(segments)
        
        # Lưu transcript đã dịch
        with open(self.output_dir / "translated_transcript.json", 'w', encoding='utf-8') as f:
            json.dump(translated_segments, f, ensure_ascii=False, indent=2)
        
        # 4. Tạo audio
        print("\n=== TẠO AUDIO ===")
        audio_segments = self.generate_audio_segments(translated_segments)
        
        # 5. Ghép audio vào video
        print("\n=== GHÉP AUDIO VÀO VIDEO ===")
        video_id = self.extract_video_id(youtube_url)
        output_path = str(self.output_dir / f"{video_id}_vietnamese.mp4")
        self.merge_audio_with_video(video_path, audio_segments, output_path)
        
        print("\n=== HOÀN THÀNH ===")
        return output_path


if __name__ == "__main__":
    # Sử dụng
    youtube_url = input("Nhập YouTube URL: ").strip()
    
    # Chọn giọng đọc
    print("\nChọn giọng đọc:")
    print("1. Giọng Nam")
    print("2. Giọng Nữ")
    choice = input("Chọn (1/2) [Mặc định: 1]: ").strip() or "1"
    
    voice_gender = "male" if choice == "1" else "female"
    
    translator = YouTubeTranslator(output_dir="output", voice_gender=voice_gender)
    
    try:
        # Bắt đầu tính thời gian
        start_time = time.time()
        
        result = translator.process(youtube_url)
        
        # Tính thời gian hoàn thành
        end_time = time.time()
        processing_time = end_time - start_time
        processing_time_minutes = processing_time / 60
        
        print(f"\n✅ Thành công!")
        print(f"📁 Video đã dịch: {result}")
        print(f"⏱️  Thời gian xử lý: {processing_time_minutes:.2f} phút")
    except Exception as e:
        print(f"\n❌ Lỗi: {e}")
        import traceback
        traceback.print_exc()
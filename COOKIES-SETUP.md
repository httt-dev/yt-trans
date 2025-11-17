# 🍪 Hướng dẫn lấy Cookies YouTube để bypass bot detection

## Tại sao cần cookies?

YouTube đang chặn bot bằng CAPTCHA. Để bypass, cần pass cookies từ browser đã đăng nhập YouTube.

## 🚀 Cách 1: Dùng Browser Extension (Dễ nhất - Khuyến nghị)

### Chrome/Edge:

1. Cài extension **"Get cookies.txt LOCALLY"**
   - Link: https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc

2. Mở YouTube và đăng nhập tài khoản

3. Click vào icon extension → Chọn "Export" → "All Cookies"

4. Save file `cookies.txt`

### Firefox:

1. Cài extension **"cookies.txt"**
   - Link: https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/

2. Mở YouTube và đăng nhập

3. Click vào icon extension → "Current Site" → Save file

## 🔧 Cách 2: Dùng yt-dlp (Command line)

### Trên máy local (Windows/Mac/Linux):

```bash
# Cài yt-dlp nếu chưa có
pip install yt-dlp

# Export cookies từ browser
yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://youtube.com"

# Hoặc từ Firefox
yt-dlp --cookies-from-browser firefox --cookies cookies.txt "https://youtube.com"

# Test cookies
yt-dlp --cookies cookies.txt --list-formats "https://youtube.com/watch?v=dQw4w9WgXcQ"
```

Browsers hỗ trợ: `chrome`, `chromium`, `edge`, `firefox`, `opera`, `safari`, `brave`, `vivaldi`

## 📝 Cách 3: Export thủ công (Advanced)

### Bước 1: Lấy cookies từ browser

1. Mở YouTube và đăng nhập
2. Mở DevTools (F12)
3. Tab "Application" (Chrome) hoặc "Storage" (Firefox)
4. Mở "Cookies" → "https://www.youtube.com"
5. Copy tất cả cookies

### Bước 2: Tạo file cookies.txt theo format Netscape

Format:
```
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	0	CONSENT	YES+
.youtube.com	TRUE	/	FALSE	1234567890	VISITOR_INFO1_LIVE	xxx
.youtube.com	TRUE	/	TRUE	1234567890	LOGIN_INFO	xxx
```

**Lưu ý:** Cách này phức tạp, khuyến nghị dùng Cách 1 hoặc 2.

## 🐳 Deploy với Docker

### Bước 1: Lấy cookies.txt (dùng cách 1 hoặc 2 ở trên)

### Bước 2: Upload lên server

```bash
# Copy cookies.txt lên server
scp cookies.txt user@server:/opt/yt-translator/cookies.txt

# Hoặc dùng nano/vim
ssh user@server
cd /opt/yt-translator
nano cookies.txt
# Paste nội dung cookies
```

### Bước 3: Kiểm tra file

```bash
cd /opt/yt-translator
ls -la cookies.txt

# File phải có format đúng
head cookies.txt
# Nên thấy: # Netscape HTTP Cookie File
```

### Bước 4: Deploy

```bash
# File cookies.txt đã được mount trong docker-compose.yml
docker-compose down
docker-compose up -d

# Kiểm tra logs
docker-compose logs -f
```

## ✅ Verify cookies hoạt động

```bash
# Vào container
docker exec -it yt-translator bash

# Test download
yt-dlp --cookies /app/cookies.txt --list-formats "https://youtube.com/watch?v=dQw4w9WgXcQ"

# Nếu thành công, sẽ thấy list formats
# Nếu lỗi, kiểm tra lại cookies
```

## 🔒 Bảo mật

### ⚠️ Quan trọng:

- **Cookies chứa session của tài khoản YouTube**
- **KHÔNG share cookies.txt với người khác**
- **KHÔNG commit cookies.txt vào Git**

### Thêm vào .gitignore:

```bash
echo "cookies.txt" >> .gitignore
```

### Permissions:

```bash
# Chỉ owner đọc được
chmod 600 cookies.txt
```

## 🔄 Khi nào cần cập nhật cookies?

- **Session hết hạn** (thường 1-2 tháng)
- **Thay đổi password YouTube**
- **Logout/login lại YouTube**
- **Gặp lỗi "Sign in to confirm you're not a bot"**

Khi đó, lấy cookies mới và restart container:

```bash
# Upload cookies.txt mới
docker-compose restart
```

## 🐛 Troubleshooting

### Lỗi: "Sign in to confirm you're not a bot"

**Nguyên nhân:** Cookies không hợp lệ hoặc hết hạn

**Giải pháp:**
1. Lấy cookies mới từ browser
2. Đảm bảo đã đăng nhập YouTube
3. Upload cookies.txt mới
4. Restart container

### Lỗi: "Unable to extract video data"

**Nguyên nhân:** Video bị giới hạn vùng hoặc riêng tư

**Giải pháp:**
1. Thử video khác
2. Kiểm tra cookies từ tài khoản có quyền xem video

### Lỗi: Cookies file không được đọc

```bash
# Kiểm tra file tồn tại trong container
docker exec -it yt-translator ls -la /app/cookies.txt

# Kiểm tra nội dung
docker exec -it yt-translator head /app/cookies.txt

# Kiểm tra volume mount
docker inspect yt-translator | grep -A 10 Mounts
```

### Video vẫn bị chặn dù có cookies

**Thử thêm options:**

Sửa trong `api.py`, hàm `get_ydl_opts`:
```python
opts = {
    # ... existing options ...
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web'],
            'player_skip': ['webpage', 'configs'],
        }
    }
}
```

## 📚 Tài liệu tham khảo

- yt-dlp cookies: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp
- Browser extensions: 
  - Chrome: https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
  - Firefox: https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/

## ✅ Checklist

- [ ] Đã lấy cookies.txt từ browser
- [ ] File có format Netscape đúng
- [ ] Đã upload lên server tại `/opt/yt-translator/cookies.txt`
- [ ] File được mount vào container (`docker-compose.yml`)
- [ ] Đã restart container
- [ ] Test download thành công

Nếu tất cả ✅, YouTube download sẽ hoạt động! 🎉
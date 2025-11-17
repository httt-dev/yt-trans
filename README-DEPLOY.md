# 🐳 YouTube Video Translator - Docker Deployment

## 📋 Yêu cầu hệ thống

- **OS**: Linux (Ubuntu 20.04+, Debian 11+, CentOS 8+)
- **RAM**: Tối thiểu 4GB (khuyến nghị 8GB+)
- **CPU**: 2+ cores
- **Disk**: 20GB+ trống
- **Docker**: 20.10+
- **Docker Compose**: 2.0+

## 🚀 Cài đặt nhanh

### 1. Cài đặt Docker (nếu chưa có)

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Log out và log in lại để áp dụng quyền

# Cài Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### 2. Clone/Upload code lên server

```bash
# Tạo thư mục project
mkdir -p /opt/yt-translator
cd /opt/yt-translator

# Upload các file:
# - Dockerfile
# - docker-compose.yml
# - requirements.txt
# - api.py
# - .env.example
# - deploy.sh
```

### 3. Cấu hình

```bash
# Copy file env
cp .env.example .env

# Chỉnh sửa và thêm API key
nano .env
# Hoặc
vim .env

# Thêm:
# DEEPSEEK_API_KEY=your-actual-api-key-here
```

### 4. Deploy

```bash
# Cấp quyền execute cho script
chmod +x deploy.sh

# Chạy deployment
./deploy.sh
```

## 📝 Cấu trúc thư mục

```
/opt/yt-translator/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── api.py
├── .env
├── .dockerignore
├── deploy.sh
├── output/              # Video đã dịch (auto-created)
└── logs/                # Logs (auto-created)
```

## 🔧 Quản lý

### Xem logs
```bash
docker-compose logs -f
docker-compose logs -f --tail=100
```

### Khởi động lại
```bash
docker-compose restart
```

### Dừng service
```bash
docker-compose down
```

### Dừng và xóa data
```bash
docker-compose down -v
```

### Update code
```bash
# Upload file api.py mới
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Kiểm tra status
```bash
docker-compose ps
docker stats yt-translator
```

## 🌐 Expose ra Internet

### Option 1: Nginx Reverse Proxy

```bash
# Cài Nginx
sudo apt install nginx

# Tạo config
sudo nano /etc/nginx/sites-available/yt-translator
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 500M;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeout cao cho video processing
        proxy_read_timeout 600s;
        proxy_connect_timeout 600s;
        proxy_send_timeout 600s;
    }
}
```

```bash
# Enable site
sudo ln -s /etc/nginx/sites-available/yt-translator /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Option 2: SSL với Certbot

```bash
# Cài Certbot
sudo apt install certbot python3-certbot-nginx

# Lấy SSL certificate
sudo certbot --nginx -d your-domain.com
```

### Option 3: Cloudflare Tunnel (không cần mở port)

```bash
# Cài cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# Login
cloudflared tunnel login

# Tạo tunnel
cloudflared tunnel create yt-translator

# Config
nano ~/.cloudflared/config.yml
```

```yaml
tunnel: YOUR_TUNNEL_ID
credentials-file: /root/.cloudflared/YOUR_TUNNEL_ID.json

ingress:
  - hostname: your-domain.com
    service: http://localhost:8000
  - service: http_status:404
```

```bash
# Chạy tunnel
cloudflared tunnel run yt-translator

# Hoặc chạy as service
sudo cloudflared service install
sudo systemctl start cloudflared
```

## 🔒 Bảo mật

### 1. Thêm Basic Auth (Nginx)

```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd admin
```

Thêm vào Nginx config:
```nginx
location / {
    auth_basic "Restricted Access";
    auth_basic_user_file /etc/nginx/.htpasswd;
    
    proxy_pass http://localhost:8000;
    # ... rest of config
}
```

### 2. Firewall

```bash
# Chỉ cho phép port 80, 443
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### 3. Giới hạn rate (Nginx)

```nginx
limit_req_zone $binary_remote_addr zone=translate:10m rate=5r/m;

location /translate {
    limit_req zone=translate burst=2;
    # ... rest of config
}
```

## 📊 Monitoring

### Xem resource usage
```bash
docker stats yt-translator
```

### Setup log rotation
```bash
# Tạo file
sudo nano /etc/logrotate.d/yt-translator
```

```
/opt/yt-translator/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 root root
}
```

## 🐛 Troubleshooting

### Container không khởi động
```bash
docker-compose logs
docker inspect yt-translator
```

### Lỗi FFmpeg
```bash
# Vào container
docker exec -it yt-translator bash
ffmpeg -version
```

### Port đã được sử dụng
```bash
# Tìm process đang dùng port 8000
sudo lsof -i :8000
sudo kill -9 PID
```

### Disk đầy
```bash
# Xóa output cũ
rm -rf /opt/yt-translator/output/*

# Xóa Docker images không dùng
docker system prune -a
```

## 📈 Performance Tuning

### Tăng workers (trong docker-compose.yml)
```yaml
command: ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### Tăng memory limit (trong docker-compose.yml)
```yaml
deploy:
  resources:
    limits:
      memory: 8G
```

## 🔄 Backup

```bash
# Backup output folder
tar -czf yt-translator-backup-$(date +%Y%m%d).tar.gz output/

# Restore
tar -xzf yt-translator-backup-20240101.tar.gz
```

## 📞 Support

- Issues: GitHub Issues
- Logs: `docker-compose logs -f`
- Health: `http://your-domain.com/`
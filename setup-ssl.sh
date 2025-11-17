#!/bin/bash

# Script để setup SSL cho yt.h3t.dev với Let's Encrypt

set -e

echo "🔐 Setup SSL cho yt.h3t.dev"
echo "================================"

# Kiểm tra quyền root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ Vui lòng chạy với quyền root (sudo)"
    exit 1
fi

# Kiểm tra DNS
echo ""
echo "📡 Kiểm tra DNS record..."
if ! nslookup yt.h3t.dev | grep -q "Address:"; then
    echo "⚠️  Cảnh báo: DNS record cho yt.h3t.dev chưa được cấu hình đúng"
    echo "Vui lòng thêm A record trỏ yt.h3t.dev về IP server này"
    read -p "Tiếp tục? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✅ DNS record đã được cấu hình"
fi

# Cài đặt Certbot nếu chưa có
if ! command -v certbot &> /dev/null; then
    echo ""
    echo "📦 Cài đặt Certbot..."
    apt-get update
    apt-get install -y certbot python3-certbot-nginx
    echo "✅ Đã cài đặt Certbot"
fi

# Tạo thư mục cho ACME challenge
echo ""
echo "📁 Tạo thư mục ACME challenge..."
mkdir -p /var/www/certbot
chown -R www-data:www-data /var/www/certbot

# Backup nginx config hiện tại
echo ""
echo "💾 Backup nginx config..."
cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.backup.$(date +%Y%m%d_%H%M%S)
echo "✅ Đã backup tại: /etc/nginx/nginx.conf.backup.$(date +%Y%m%d_%H%M%S)"

# Thêm config tạm cho HTTP (để Certbot verify)
echo ""
echo "⚙️  Thêm config tạm cho HTTP..."
cat > /etc/nginx/sites-available/yt.h3t.dev.temp << 'EOF'
server {
    listen 80;
    server_name yt.h3t.dev;
    
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    location / {
        return 301 https://$server_name$request_uri;
    }
}
EOF

# Enable site
ln -sf /etc/nginx/sites-available/yt.h3t.dev.temp /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Lấy SSL certificate
echo ""
echo "🔐 Lấy SSL certificate từ Let's Encrypt..."
certbot certonly \
    --nginx \
    --non-interactive \
    --agree-tos \
    --email admin@h3t.dev \
    --domains yt.h3t.dev

if [ $? -eq 0 ]; then
    echo "✅ Đã lấy SSL certificate thành công!"
else
    echo "❌ Lỗi khi lấy SSL certificate"
    exit 1
fi

# Tạo full config với HTTPS
echo ""
echo "⚙️  Tạo full config với HTTPS..."
cat > /etc/nginx/sites-available/yt.h3t.dev << 'EOF'
# HTTP -> HTTPS redirect
server {
    listen 80;
    server_name yt.h3t.dev;
    
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    location / {
        return 301 https://$server_name$request_uri;
    }
}

# HTTPS
server {
    listen 443 ssl http2;
    server_name yt.h3t.dev;
    
    # SSL certificates
    ssl_certificate /etc/letsencrypt/live/yt.h3t.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yt.h3t.dev/privkey.pem;
    
    # SSL settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # Client settings
    client_max_body_size 500M;
    client_body_timeout 600s;
    
    # Proxy to Docker container
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
    
    # Static files (output videos)
    location /output {
        proxy_pass http://localhost:8000/output;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # Cache
        proxy_cache_valid 200 1h;
        proxy_buffering on;
    }
    
    # Access log
    access_log /var/log/nginx/yt.h3t.dev.access.log;
    error_log /var/log/nginx/yt.h3t.dev.error.log;
}
EOF

# Remove temp config
rm -f /etc/nginx/sites-enabled/yt.h3t.dev.temp

# Enable new config
ln -sf /etc/nginx/sites-available/yt.h3t.dev /etc/nginx/sites-enabled/

# Test và reload nginx
echo ""
echo "✅ Test nginx config..."
nginx -t

if [ $? -eq 0 ]; then
    echo "✅ Reload nginx..."
    systemctl reload nginx
    echo ""
    echo "================================"
    echo "✅ Hoàn thành!"
    echo ""
    echo "📋 Thông tin:"
    echo "  - URL: https://yt.h3t.dev"
    echo "  - SSL: Let's Encrypt"
    echo "  - Auto-renew: Enabled"
    echo ""
    echo "📝 Certificate info:"
    certbot certificates | grep -A 5 "yt.h3t.dev"
    echo ""
    echo "🔄 Auto-renewal:"
    echo "  Certbot sẽ tự động renew certificate"
    echo "  Kiểm tra: sudo certbot renew --dry-run"
    echo ""
else
    echo "❌ Lỗi config nginx"
    exit 1
fi
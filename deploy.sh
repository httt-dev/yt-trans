#!/bin/bash

# Script để deploy YouTube Translator với nginx-proxy

set -e

echo "🚀 YouTube Video Translator - Deployment Script (nginx-proxy)"
echo "============================================================="

# Kiểm tra Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker chưa được cài đặt!"
    exit 1
fi

if ! command -v docker compose &> /dev/null; then
    echo "❌ Docker Compose chưa được cài đặt!"
    exit 1
fi

echo "✅ Docker và Docker Compose đã được cài đặt"

# Kiểm tra nginx-proxy network
echo ""
echo "🔍 Kiểm tra nginx-proxy network..."
if ! docker network ls | grep -q "nginx-proxy"; then
    echo "⚠️  nginx-proxy network chưa tồn tại. Đang tạo..."
    docker network create nginx-proxy
    echo "✅ Đã tạo nginx-proxy network"
else
    echo "✅ nginx-proxy network đã tồn tại"
fi

# Kiểm tra nginx-proxy container
echo ""
echo "🔍 Kiểm tra nginx-proxy container..."
if ! docker ps | grep -q "nginx-proxy"; then
    echo "⚠️  nginx-proxy container không chạy!"
    echo "Vui lòng khởi động nginx-proxy trước:"
    echo "  docker-compose up -d nginx-proxy acme-companion"
    read -p "Tiếp tục? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Tạo .env nếu chưa có
if [ ! -f .env ]; then
    echo ""
    echo "⚙️ Tạo file .env..."
    cp .env.example .env
    echo "📝 Vui lòng cập nhật DEEPSEEK_API_KEY trong file .env"
    echo ""
    read -p "Nhập DEEPSEEK_API_KEY của bạn: " api_key
    sed -i "s/your-deepseek-api-key-here/$api_key/" .env
    echo "✅ Đã cập nhật .env"
fi

# Tạo thư mục cần thiết
echo ""
echo "📁 Tạo thư mục..."
mkdir -p output
mkdir -p logs

# Stop container cũ nếu có
echo ""
echo "🛑 Dừng container cũ (nếu có)..."
docker compose down || true

# Build image
echo ""
echo "🔨 Build Docker image..."
docker compose build

# Start services
echo ""
echo "▶️ Khởi động services..."
docker compose up -d

# Kết nối vào nginx-proxy network (nếu chưa)
echo ""
echo "🔗 Kết nối vào nginx-proxy network..."
if docker network connect nginx-proxy yt-translator 2>/dev/null; then
    echo "✅ Đã kết nối vào nginx-proxy network"
else
    echo "✅ Đã trong nginx-proxy network"
fi

# Đợi service khởi động
echo ""
echo "⏳ Đợi service khởi động..."
sleep 10

# Kiểm tra health (internal)
echo ""
echo "🏥 Kiểm tra health..."
for i in {1..10}; do
    if docker exec yt-translator curl -f http://localhost:8000/ > /dev/null 2>&1; then
        echo "✅ Service đã sẵn sàng!"
        break
    fi
    if [ $i -eq 10 ]; then
        echo "⚠️  Service chưa ready, nhưng sẽ được nginx-proxy tự động phát hiện"
        break
    fi
    echo "Đợi... ($i/10)"
    sleep 3
done

# Kiểm tra SSL certificate
echo ""
echo "🔐 Kiểm tra SSL..."
echo "SSL certificate sẽ được tự động tạo bởi acme-companion trong vài phút"
echo "Bạn có thể kiểm tra logs: docker logs acme-companion -f"

# Hiển thị thông tin
echo ""
echo "============================================================="
echo "✅ Deployment hoàn tất!"
echo ""
echo "📋 Thông tin:"
echo "  - URL: https://yt.h3t.dev (sẽ hoạt động sau khi SSL được tạo)"
echo "  - HTTP: http://yt.h3t.dev (chuyển hướng sang HTTPS)"
echo "  - Container: yt-translator"
echo "  - Network: nginx-proxy"
echo ""
echo "🔐 SSL Certificate:"
echo "  - Auto SSL bởi Let's Encrypt"
echo "  - Được quản lý bởi acme-companion"
echo "  - Tự động renew trước khi hết hạn"
echo ""
echo "📝 Lệnh hữu ích:"
echo "  - Xem logs app: docker-compose logs -f"
echo "  - Xem logs nginx: docker logs nginx-proxy -f"
echo "  - Xem logs SSL: docker logs acme-companion -f"
echo "  - Dừng: docker-compose down"
echo "  - Khởi động lại: docker-compose restart"
echo "  - Xem status: docker-compose ps"
echo ""
echo "⏳ Đợi 2-5 phút để SSL certificate được tạo tự động"
echo "============================================================="
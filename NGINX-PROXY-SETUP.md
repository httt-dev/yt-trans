# 🎯 Setup với nginx-proxy + acme-companion

## Tổng quan

Bạn đang dùng **jwilder/nginx-proxy** + **acme-companion**, đây là giải pháp tự động:
- ✅ Tự động phát hiện container mới
- ✅ Tự động tạo Nginx config
- ✅ Tự động tạo SSL certificate (Let's Encrypt)
- ✅ Tự động renew SSL
- ✅ Không cần chỉnh sửa Nginx config thủ công!

## 🔧 Cách hoạt động

1. **nginx-proxy** lắng nghe Docker events
2. Khi container mới có `VIRTUAL_HOST` được tạo
3. nginx-proxy tự động:
   - Tạo Nginx config
   - Proxy request đến container
4. **acme-companion** thấy `LETSENCRYPT_HOST`
5. acme-companion tự động:
   - Tạo SSL certificate
   - Renew khi sắp hết hạn

## 📋 Setup Steps

### Bước 1: Cấu hình DNS

Thêm A record:
```
Type: A
Name: yt
Value: YOUR_SERVER_IP
TTL: 3600
```

Kiểm tra:
```bash
nslookup yt.h3t.dev
```

### Bước 2: Deploy YouTube Translator

```bash
# Vào thư mục project
cd /opt/yt-translator

# Chạy deploy script
chmod +x deploy.sh
./deploy.sh
```

**Đó là tất cả!** Không cần config Nginx thủ công.

### Bước 3: Đợi SSL được tạo (2-5 phút)

Xem logs của acme-companion:
```bash
docker logs acme-companion -f
```

Bạn sẽ thấy:
```
Creating/renewal yt.h3t.dev certificates...
Reloading nginx proxy...
```

### Bước 4: Kiểm tra

```bash
# Test HTTP (sẽ redirect sang HTTPS)
curl -I http://yt.h3t.dev

# Test HTTPS
curl -I https://yt.h3t.dev

# Mở browser
https://yt.h3t.dev
```

## 🔍 Kiểm tra nginx-proxy network

Đảm bảo cả Planka và YT Translator đều trong cùng network:

```bash
# Xem network
docker network inspect nginx-proxy

# Kết quả nên có:
# - nginx-proxy container
# - acme-companion container  
# - planka container (hoặc tên khác)
# - yt-translator container
```

## 📝 Environment Variables giải thích

```yaml
environment:
  # Domain cho service này
  - VIRTUAL_HOST=yt.h3t.dev
  
  # Port mà container expose (FastAPI chạy port 8000)
  - VIRTUAL_PORT=8000
  
  # Domain để tạo SSL certificate
  - LETSENCRYPT_HOST=yt.h3t.dev
  
  # Email cho Let's Encrypt notifications
  - LETSENCRYPT_EMAIL=admin@h3t.dev
```

## 🔗 Cập nhật docker-compose.yml cho Planka (nếu cần)

Nếu Planka chưa có SSL tự động, thêm vào service planka:

```yaml
planka:
  # ... existing config ...
  environment:
    - VIRTUAL_HOST=kb.h3t.dev
    - VIRTUAL_PORT=1337
    - LETSENCRYPT_HOST=kb.h3t.dev
    - LETSENCRYPT_EMAIL=admin@h3t.dev
  networks:
    - nginx-proxy
    - default

networks:
  nginx-proxy:
    external: true
```

## 🎛️ Advanced Configuration (Optional)

### Custom Nginx settings cho yt.h3t.dev

Tạo file: `./vhost.d/yt.h3t.dev`

```nginx
# Increase timeouts for video processing
client_max_body_size 500M;
proxy_read_timeout 600s;
proxy_connect_timeout 600s;
proxy_send_timeout 600s;

# Custom headers
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
```

### Rate limiting

Tạo file: `./vhost.d/yt.h3t.dev_location`

```nginx
# Rate limit for translate endpoint
location /translate {
    limit_req zone=one rate=5r/m;
    proxy_pass http://yt-translator:8000;
}
```

### IP Whitelist

Thêm vào environment của nginx-proxy:

```yaml
nginx-proxy:
  environment:
    - DEFAULT_HOST=yt.h3t.dev
    - TRUST_DOWNSTREAM_PROXY=true
```

## 🐛 Troubleshooting

### 1. SSL không được tạo

```bash
# Kiểm tra logs
docker logs acme-companion -f

# Kiểm tra DNS
nslookup yt.h3t.dev

# Restart acme-companion
docker restart acme-companion
```

### 2. 502 Bad Gateway

```bash
# Kiểm tra yt-translator có chạy không
docker ps | grep yt-translator

# Kiểm tra health
docker exec yt-translator curl http://localhost:8000/

# Kiểm tra network
docker network inspect nginx-proxy | grep yt-translator
```

### 3. Container không được phát hiện

```bash
# Kiểm tra environment variables
docker inspect yt-translator | grep VIRTUAL_HOST

# Restart nginx-proxy
docker restart nginx-proxy

# Xem logs nginx-proxy
docker logs nginx-proxy -f
```

### 4. Certificate error

```bash
# Xem certificates hiện có
docker exec acme-companion /app/cert_status

# Force renew
docker exec acme-companion /app/force_renew
```

## 📊 Monitoring

### Xem tất cả domains được proxy

```bash
docker exec nginx-proxy cat /etc/nginx/conf.d/default.conf | grep server_name
```

### Xem certificates

```bash
ls -la ./certs/
```

### Xem logs realtime

```bash
# App logs
docker-compose logs -f yt-translator

# Nginx logs  
docker logs nginx-proxy -f

# SSL logs
docker logs acme-companion -f
```

## 🔄 So sánh với setup thủ công

| Feature | nginx-proxy | Manual Nginx |
|---------|-------------|--------------|
| Auto SSL | ✅ | ❌ Phải setup |
| Auto config | ✅ | ❌ Phải viết config |
| Multiple domains | ✅ Easy | ⚠️ Phải config từng domain |
| SSL Renew | ✅ Auto | ⚠️ Cần cron job |
| Add service | ✅ Chỉ cần env vars | ❌ Phải viết config |

## 🎯 Best Practices

1. **Luôn dùng external network** cho nginx-proxy
2. **Set resource limits** cho containers
3. **Backup ./certs** folder định kỳ
4. **Monitor logs** của acme-companion
5. **Test certificate renewal**: `docker exec acme-companion /app/force_renew`

## 📚 Tài liệu tham khảo

- nginx-proxy: https://github.com/nginx-proxy/nginx-proxy
- acme-companion: https://github.com/nginx-proxy/acme-companion
- Let's Encrypt: https://letsencrypt.org/docs/

## ✅ Checklist

- [ ] DNS A record đã trỏ đúng
- [ ] nginx-proxy network đã tồn tại
- [ ] VIRTUAL_HOST đã set đúng
- [ ] LETSENCRYPT_HOST đã set đúng
- [ ] Container trong nginx-proxy network
- [ ] Port 80, 443 đã mở
- [ ] Đợi 2-5 phút cho SSL

Nếu tất cả ✅, service sẽ tự động hoạt động tại https://yt.h3t.dev! 🎉
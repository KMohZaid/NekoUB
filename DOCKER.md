# 🐳 Docker Deployment Guide

Run NekoUB in a Docker container for easy deployment and isolation!

## 🚀 Quick Start

1. **Clone and setup:**
```bash
git clone <your-repo-url>
cd NekoUB
cp .env.example .env
```

2. **Configure your `.env` file:**
```bash
nano .env
# Add your API_ID, API_HASH, and SESSION_STRING
```

3. **Run with Docker Compose:**
```bash
docker-compose up -d
```

That's it! Your userbot is now running in a Docker container.

## 📋 Docker Commands

### Start the bot
```bash
docker-compose up -d
```

### Stop the bot
```bash
docker-compose down
```

### View logs (with colors!)
```bash
# Follow logs with colors
docker-compose logs -f --no-log-prefix

# Or with timestamps
docker-compose logs -f

# View last 100 lines
docker-compose logs --tail=100 -f
```

**Note:** Colored output is enabled by default! You'll see:
- 🟢 Green for INFO logs
- 🟡 Yellow for WARNING logs
- 🔴 Red for ERROR logs
- 🔵 Blue for timestamps
- 🟣 Purple for logger names

### Restart the bot
```bash
docker-compose restart
```

### Rebuild after code changes
```bash
docker-compose up -d --build
```

### Access container shell
```bash
docker-compose exec nekoub bash
```

## 📂 Volume Mounts

The following directories are mounted as volumes:

- `./private_plugins` - Your private plugins (persisted)
- `./logs` - Log files (optional, persisted)
- `.env` - Configuration file

## 🔧 Advanced Configuration

### Colored Logs

Colored output is **enabled by default**! The configuration uses:
- `tty: true` - Enables TTY for colored output
- `FORCE_COLOR=1` - Forces colorama to output colors
- `COLORTERM=truecolor` - Enables true color support

To disable colors (not recommended):
```yaml
services:
  nekoub:
    tty: false
    environment:
      - FORCE_COLOR=0
```

### Custom Network

Edit `docker-compose.yml` to change network mode:

```yaml
# Use bridge network instead of host
network_mode: bridge
```

### Resource Limits

Add resource limits in `docker-compose.yml`:

```yaml
services:
  nekoub:
    # ... existing config ...
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 512M
        reservations:
          memory: 256M
```

### Environment Variables

You can override environment variables in `docker-compose.yml`:

```yaml
services:
  nekoub:
    # ... existing config ...
    environment:
      - CMD_PREFIX=!
      - MAX_SPAM_COUNT=50
```

## 🐛 Troubleshooting

### Check container status
```bash
docker ps
```

### View detailed logs
```bash
docker-compose logs --tail=100
```

### Container won't start
```bash
# Check for errors
docker-compose logs

# Rebuild the image
docker-compose build --no-cache
docker-compose up -d
```

### Update dependencies
```bash
# Rebuild with updated requirements
docker-compose build --no-cache
docker-compose up -d
```

## 🔄 Updating

```bash
# Pull latest changes
git pull

# Rebuild and restart
docker-compose up -d --build
```

## 🗑️ Cleanup

### Remove container and image
```bash
docker-compose down
docker rmi nekoub_nekoub
```

### Full cleanup (including volumes)
```bash
docker-compose down -v
```

## 📝 Notes

- Session files are created inside the container
- Private plugins are persisted in `./private_plugins`
- Logs can be accessed via `docker-compose logs`
- The bot runs as a non-root user inside the container
- Automatic restart is enabled (`unless-stopped`)

---

**Made with 💝 by NekoUB Team**

Nya~ 🐱

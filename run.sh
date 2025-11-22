#!/bin/bash
# NekoUB - Neko Userbot Runner Script using uv
# Nya~ This script makes it easy to run your userbot with hot-reloading!

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}╔═══════════════════════════════════╗${NC}"
echo -e "${BLUE}║    🐱 NekoUB - Neko Userbot (uv)  ║${NC}"
echo -e "${BLUE}║    Nya~ Starting your userbot...  ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════╝${NC}"
echo ""

# 1. Check if uv is installed
if ! command -v uv &>/dev/null; then
  echo -e "${RED}❌ 'uv' is not installed.${NC}"
  echo -e "${YELLOW}Install it with: pip install uv${NC}"
  exit 1
fi

echo -e "${BLUE}Using uv: $(uv --version)${NC}"

# 2. Check if .env exists
if [ ! -f ".env" ]; then
  echo -e "${RED}❌ .env file not found!${NC}"
  echo -e "${YELLOW}Please create one from .env.example${NC}"
  exit 1
fi

# 3. Create venv if missing
if [ ! -d ".venv" ]; then
  echo -e "${YELLOW}🐍 Creating virtual environment (.venv)...${NC}"
  uv venv .venv
  if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Failed to create venv.${NC}"
    exit 1
  fi
fi

# 4. Sync dependencies
echo -e "${YELLOW}📦 Syncing dependencies with uv...${NC}"
uv pip install -r requirements.txt
if [ $? -ne 0 ]; then
  echo -e "${RED}❌ Dependency sync failed.${NC}"
  exit 1
fi
echo -e "${GREEN}✅ Environment is up to date.${NC}"

# 5. Run with hot-reloading
echo -e "${GREEN}🚀 Starting NekoUB with hot-reloading...${NC}"
echo -e "${YELLOW}   Watching: main.py + private_plugins/${NC}"
echo ""

uv run watchfiles -- "python3 main.py" private_plugins

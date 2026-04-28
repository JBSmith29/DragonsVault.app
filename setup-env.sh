#!/bin/bash
# Setup .env file for DragonsVault

set -e

echo "=========================================="
echo "DragonsVault Environment Setup"
echo "=========================================="
echo ""

# Check if .env already exists
if [ -f .env ]; then
    echo "⚠️  .env file already exists!"
    read -p "Do you want to overwrite it? (yes/no): " OVERWRITE
    if [ "$OVERWRITE" != "yes" ]; then
        echo "Cancelled. Existing .env file preserved."
        exit 0
    fi
    mv .env .env.backup.$(date +%Y%m%d_%H%M%S)
    echo "✓ Backed up existing .env file"
fi

echo ""
echo "This script will help you create a .env file."
echo ""
echo "If you had a working system before, you should use the SAME"
echo "POSTGRES_PASSWORD that was in your old .env file."
echo ""
echo "If this is a fresh install, you can generate a new password."
echo ""

# Ask for postgres password
read -p "Enter POSTGRES_PASSWORD (or press Enter to use the one from git history): " POSTGRES_PASSWORD

if [ -z "$POSTGRES_PASSWORD" ]; then
    # Try to get from git history
    POSTGRES_PASSWORD=$(git show HEAD~1:.env 2>/dev/null | grep "^POSTGRES_PASSWORD=" | cut -d'=' -f2 || echo "")
    
    if [ -z "$POSTGRES_PASSWORD" ]; then
        echo ""
        echo "Could not find password in git history."
        echo "Generating a new secure password..."
        POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
        echo ""
        echo "⚠️  WARNING: Using a new password will require recreating the database!"
        echo "Generated password: $POSTGRES_PASSWORD"
        echo ""
        read -p "Use this password? (yes/no): " USE_NEW
        if [ "$USE_NEW" != "yes" ]; then
            echo "Please run this script again and enter your password manually."
            exit 1
        fi
    else
        echo "✓ Found password from git history"
    fi
fi

# Get Django secret key from file or generate
if [ -f .secrets/django_secret_key ]; then
    DJANGO_SECRET_KEY=$(cat .secrets/django_secret_key)
    echo "✓ Using Django secret key from .secrets/django_secret_key"
else
    DJANGO_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
    echo "✓ Generated new Django secret key"
fi

# Get game engine secret
read -p "Enter GAME_ENGINE_SHARED_SECRET (or press Enter to generate): " GAME_ENGINE_SECRET
if [ -z "$GAME_ENGINE_SECRET" ]; then
    GAME_ENGINE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "✓ Generated new game engine secret"
fi

# Create .env file
cat > .env << EOF
# DragonsVault Production Environment Configuration
# Generated: $(date)

# PostgreSQL Database
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
DATABASE_URL=postgresql+psycopg2://dvapp:\${POSTGRES_PASSWORD}@pgbouncer:6432/dragonsvault

# Django API
DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY
DJANGO_ALLOWED_HOSTS=django-api,localhost,127.0.0.1,dragonsvault.app,www.dragonsvault.app

# Game Engine Service
GAME_ENGINE_URL=http://game-engine:5000
GAME_ENGINE_SHARED_SECRET=$GAME_ENGINE_SECRET

# Gunicorn sizing
WEB_CONCURRENCY=12
WEB_THREADS=3
WEB_TIMEOUT=180

# Redis
CACHE_TYPE=RedisCache
CACHE_REDIS_URL=redis://redis:6379/2
REDIS_URL=redis://redis:6379/0
RATELIMIT_STORAGE_URI=redis://redis:6379/1

# Security
ENABLE_TALISMAN=1
TALISMAN_FORCE_HTTPS=1
SESSION_COOKIE_SECURE=1
PREFERRED_URL_SCHEME=https
EOF

chmod 600 .env

echo ""
echo "=========================================="
echo "✓ .env file created successfully!"
echo "=========================================="
echo ""
echo "File location: $(pwd)/.env"
echo "Permissions: 600 (read/write for owner only)"
echo ""
echo "Next steps:"
echo "1. Review the .env file: cat .env"
echo "2. Start your services: docker compose up -d"
echo ""
echo "⚠️  IMPORTANT: Never commit .env to git!"
echo ""

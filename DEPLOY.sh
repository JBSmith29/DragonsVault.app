#!/bin/bash
# Production Deployment Script for DragonsVault
# This script commits all changes and provides deployment instructions

set -e  # Exit on error

echo "=========================================="
echo "DragonsVault Production Deployment"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo -e "${RED}Error: Not in a git repository${NC}"
    exit 1
fi

# Check for uncommitted changes
if [[ -n $(git status -s) ]]; then
    echo -e "${YELLOW}Found uncommitted changes. Preparing to commit...${NC}"
    echo ""
    
    # Show what will be committed
    echo "Changes to be committed:"
    git status -s
    echo ""
    
    # Stage all changes
    echo "Staging all changes..."
    git add -A
    
    # Create commit message
    COMMIT_MSG="feat: comprehensive security and documentation improvements

Security:
- Remove hardcoded secrets from .env (moved to .env.example)
- Fix SQL injection risks in migrations
- Add pip-audit and safety security scanning
- Add GitHub Actions security workflow

Documentation:
- Add OpenAPI/Swagger API documentation at /api/docs
- Add comprehensive database schema documentation
- Add Architecture Decision Records (ADRs)
- Add production deployment guide
- Add troubleshooting runbook
- Update README with documentation links

Testing:
- Add pytest-cov for test coverage reporting
- Set 60% coverage threshold
- Add coverage to CI/CD pipeline

Operations:
- Add Docker resource limits configuration
- Add upgrade guide for existing installations
- Add security scanning to pre-commit hooks

Previous fixes:
- Fix games domain bugs (variable shadowing, API issues)
- Fix metrics dashboards
- Add caching to games landing
- Fix simulated API calls in templates

See docs/IMPROVEMENTS_SUMMARY.md for full details."
    
    # Commit changes
    echo ""
    echo "Committing changes..."
    git commit -m "$COMMIT_MSG"
    
    echo -e "${GREEN}✓ Changes committed successfully${NC}"
    echo ""
else
    echo -e "${GREEN}✓ No uncommitted changes${NC}"
    echo ""
fi

# Show current branch
CURRENT_BRANCH=$(git branch --show-current)
echo "Current branch: ${CURRENT_BRANCH}"
echo ""

# Check if we're on main
if [[ "$CURRENT_BRANCH" != "main" ]]; then
    echo -e "${YELLOW}Warning: Not on main branch${NC}"
    echo "You may want to merge to main before deploying to production"
    echo ""
fi

# Show remote status
echo "Checking remote status..."
git fetch origin --quiet
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "")
BASE=$(git merge-base @ @{u} 2>/dev/null || echo "")

if [[ -z "$REMOTE" ]]; then
    echo -e "${YELLOW}Warning: No upstream branch configured${NC}"
elif [[ $LOCAL = $REMOTE ]]; then
    echo -e "${GREEN}✓ Local branch is up to date with remote${NC}"
elif [[ $LOCAL = $BASE ]]; then
    echo -e "${YELLOW}Warning: Remote has changes. Pull before pushing.${NC}"
elif [[ $REMOTE = $BASE ]]; then
    echo -e "${GREEN}✓ Ready to push${NC}"
else
    echo -e "${RED}Error: Branches have diverged${NC}"
    exit 1
fi
echo ""

# Pre-deployment checklist
echo "=========================================="
echo "PRE-DEPLOYMENT CHECKLIST"
echo "=========================================="
echo ""
echo "Before pushing to production, ensure:"
echo ""
echo "1. Security:"
echo "   [ ] .env file is NOT in git (should be in .gitignore)"
echo "   [ ] .env.example has no real secrets"
echo "   [ ] .secrets/ directory has proper permissions (700)"
echo "   [ ] All secret files have 600 permissions"
echo ""
echo "2. Testing:"
echo "   [ ] All tests pass: pytest"
echo "   [ ] Coverage meets threshold: pytest --cov=backend"
echo "   [ ] Security audit clean: pip-audit -r backend/requirements.txt"
echo "   [ ] Pre-commit hooks pass: pre-commit run --all-files"
echo ""
echo "3. Database:"
echo "   [ ] Migrations tested on both SQLite and PostgreSQL"
echo "   [ ] Backup of production database created"
echo "   [ ] Migration rollback plan documented"
echo ""
echo "4. Documentation:"
echo "   [ ] README updated with new features"
echo "   [ ] API docs accessible at /api/docs"
echo "   [ ] Deployment guide reviewed"
echo ""
echo "5. Configuration:"
echo "   [ ] Production .env file prepared (not in git)"
echo "   [ ] Secrets generated for production"
echo "   [ ] Resource limits appropriate for production"
echo ""

# Ask for confirmation
echo ""
read -p "Have you completed the checklist above? (yes/no): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
    echo -e "${YELLOW}Deployment cancelled. Complete the checklist first.${NC}"
    exit 0
fi

echo ""
echo "=========================================="
echo "DEPLOYMENT COMMANDS"
echo "=========================================="
echo ""
echo "To push to Git repository:"
echo -e "${GREEN}git push origin ${CURRENT_BRANCH}${NC}"
echo ""
echo "To deploy to production server:"
echo ""
echo "1. SSH to production server:"
echo "   ssh user@production-server"
echo ""
echo "2. Navigate to application directory:"
echo "   cd /path/to/dragonsvault"
echo ""
echo "3. Pull latest changes:"
echo "   git pull origin main"
echo ""
echo "4. Create production secrets (if not exists):"
echo "   mkdir -p .secrets && chmod 700 .secrets"
echo "   python -c \"import secrets; print(secrets.token_hex(32))\" > .secrets/secret_key"
echo "   python -c \"import secrets; print(secrets.token_urlsafe(50))\" > .secrets/django_secret_key"
echo "   chmod 600 .secrets/*"
echo ""
echo "5. Update .env file (create from .env.example if needed):"
echo "   cp .env.example .env"
echo "   # Edit .env with production values"
echo ""
echo "6. Backup database:"
echo "   docker compose exec postgres pg_dump -U dvapp dragonsvault | gzip > backup_\$(date +%Y%m%d_%H%M%S).sql.gz"
echo ""
echo "7. Rebuild and restart services:"
echo "   docker compose build --no-cache"
echo "   docker compose run --rm web flask db upgrade"
echo "   docker compose down"
echo "   docker compose up -d"
echo ""
echo "8. Verify deployment:"
echo "   docker compose ps"
echo "   curl http://localhost/healthz"
echo "   curl http://localhost/readyz"
echo "   curl http://localhost/api/docs"
echo ""
echo "9. Monitor logs:"
echo "   docker compose logs -f web worker"
echo ""
echo "=========================================="
echo ""

# Ask if user wants to push now
read -p "Push to Git repository now? (yes/no): " PUSH_NOW

if [[ "$PUSH_NOW" == "yes" ]]; then
    echo ""
    echo "Pushing to origin/${CURRENT_BRANCH}..."
    git push origin "$CURRENT_BRANCH"
    echo ""
    echo -e "${GREEN}✓ Successfully pushed to Git repository${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Review the deployment commands above"
    echo "2. SSH to production server"
    echo "3. Follow the deployment steps"
    echo "4. Monitor application health"
    echo ""
    echo "For detailed instructions, see:"
    echo "- docs/DEPLOYMENT.md"
    echo "- UPGRADE_GUIDE.md"
else
    echo ""
    echo -e "${YELLOW}Push cancelled. Run 'git push origin ${CURRENT_BRANCH}' when ready.${NC}"
fi

echo ""
echo "=========================================="
echo "IMPORTANT REMINDERS"
echo "=========================================="
echo ""
echo "1. The .env file with real secrets should NEVER be committed"
echo "2. Always backup the database before deploying"
echo "3. Test migrations on staging before production"
echo "4. Monitor logs after deployment"
echo "5. Have a rollback plan ready"
echo ""
echo "For troubleshooting, see: docs/TROUBLESHOOTING.md"
echo ""

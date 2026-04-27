# Documentation Hosting Guide

This guide explains how to host your Sphinx documentation in multiple ways.

## Option 1: Serve on Your DragonsVault Server (via Nginx)

**Status**: ✅ Already configured!

Your documentation will be available at: `https://yourdomain.com/docs/`

### How It Works

The nginx configuration now includes:

```nginx
location /docs/ {
    alias /app/docs/_build/html/;
    index index.html;
    expires 1h;
    add_header Cache-Control "public, max-age=3600";
    try_files $uri $uri/ $uri.html =404;
}
```

### Deployment Steps

1. **Build the docs** (if not already built):
   ```bash
   # Install Sphinx
   pip install sphinx sphinx-rtd-theme myst-parser
   
   # Build docs
   sphinx-build -M html docs docs/_build
   ```

2. **Restart nginx** to apply the new configuration:
   ```bash
   docker compose restart nginx
   ```

3. **Access your docs**:
   ```
   http://localhost/docs/
   # or
   https://yourdomain.com/docs/
   ```

### Updating Docs

When you update documentation:

```bash
# Rebuild docs
sphinx-build -M html docs docs/_build

# Restart nginx (optional, but ensures fresh cache)
docker compose restart nginx
```

## Option 2: GitHub Pages (Public Documentation)

**Status**: ✅ Workflow created!

Your documentation will be available at: `https://yourusername.github.io/DragonsVault.app/`

### Setup Steps

1. **Enable GitHub Pages** in your repository:
   - Go to: `Settings` → `Pages`
   - Source: `GitHub Actions`
   - Click `Save`

2. **Push the workflow** (already created):
   ```bash
   git add .github/workflows/docs-pages.yml
   git commit -m "feat: add GitHub Pages workflow for docs"
   git push origin main
   ```

3. **Wait for deployment**:
   - Go to `Actions` tab in GitHub
   - Watch the "Documentation Pages" workflow
   - Takes ~2-3 minutes

4. **Access your docs**:
   ```
   https://yourusername.github.io/DragonsVault.app/
   ```

### Automatic Updates

The workflow automatically rebuilds and deploys when:
- You push changes to `docs/**` on the `main` branch
- You manually trigger it from the Actions tab

### Custom Domain (Optional)

To use a custom domain like `docs.yourdomain.com`:

1. **Add CNAME record** in your DNS:
   ```
   docs.yourdomain.com → yourusername.github.io
   ```

2. **Configure in GitHub**:
   - Go to: `Settings` → `Pages`
   - Custom domain: `docs.yourdomain.com`
   - Check "Enforce HTTPS"

3. **Update workflow** (add custom domain):
   ```yaml
   - name: Add CNAME file
     run: echo "docs.yourdomain.com" > docs/_build/html/CNAME
   ```

## Option 3: Read the Docs (Alternative)

If you prefer Read the Docs:

1. **Sign up** at https://readthedocs.org/

2. **Import your repository**:
   - Click "Import a Project"
   - Connect GitHub
   - Select DragonsVault repository

3. **Configure** (create `.readthedocs.yml`):
   ```yaml
   version: 2
   
   build:
     os: ubuntu-22.04
     tools:
       python: "3.12"
   
   sphinx:
     configuration: docs/conf.py
   
   python:
     install:
       - requirements: backend/requirements-dev.txt
   ```

4. **Access your docs**:
   ```
   https://dragonsvault.readthedocs.io/
   ```

## Option 4: Local Development Server

For local testing:

```bash
# Serve on port 8000
python3 -m http.server 8000 --directory docs/_build/html

# Access at:
http://localhost:8000
```

Or use the Hatch command:

```bash
hatch run docs-serve
```

## Comparison

| Method | URL | Updates | Access | Best For |
|--------|-----|---------|--------|----------|
| **Nginx** | `yourdomain.com/docs/` | Manual rebuild | Private/Public | Production users |
| **GitHub Pages** | `username.github.io/repo/` | Automatic on push | Public | Open source |
| **Read the Docs** | `project.readthedocs.io/` | Automatic on push | Public | Documentation-focused |
| **Local** | `localhost:8000` | Manual rebuild | Private | Development |

## Recommended Setup

**For Production**: Use **Nginx** (Option 1)
- Integrated with your app
- Same domain and SSL certificate
- Can be private or public
- No external dependencies

**For Open Source**: Use **GitHub Pages** (Option 2)
- Free hosting
- Automatic updates
- Custom domain support
- Public by default

**For Both**: Use both!
- Nginx for authenticated users
- GitHub Pages for public documentation

## Documentation Structure

Your docs include:

```
docs/
├── _build/html/          # Built HTML (served by nginx)
├── _static/              # Static assets
├── _templates/           # Sphinx templates
├── adr/                  # Architecture Decision Records
│   ├── 0001-*.md
│   ├── 0002-*.md
│   └── 0003-*.md
├── architecture.rst      # Architecture overview
├── conf.py               # Sphinx configuration
├── DATABASE_SCHEMA.md    # Database documentation
├── DEPLOYMENT.md         # Deployment guide
├── getting-started.rst   # Getting started guide
├── index.rst             # Documentation home
├── operations.rst        # Operations guide
└── TROUBLESHOOTING.md    # Troubleshooting guide
```

## Linking Between Docs

### From Sphinx to Markdown

In `.rst` files:

```rst
See the :doc:`DATABASE_SCHEMA` for details.
```

### From Markdown to Sphinx

In `.md` files (using MyST):

```markdown
See the [Architecture](architecture.rst) for details.
```

### External Links

```rst
`GitHub Repository <https://github.com/JBSmith29/DragonsVault>`_
```

## Customization

### Theme

Edit `docs/conf.py`:

```python
html_theme = 'sphinx_rtd_theme'  # Read the Docs theme
# or
html_theme = 'alabaster'  # Default theme
# or
html_theme = 'furo'  # Modern theme
```

### Logo and Favicon

```python
html_logo = '_static/logo.png'
html_favicon = '_static/favicon.ico'
```

### Custom CSS

```python
html_static_path = ['_static']
html_css_files = ['custom.css']
```

## Troubleshooting

### Docs not showing on server

```bash
# Check nginx config
docker compose exec nginx nginx -t

# Check file permissions
ls -la docs/_build/html/

# Restart nginx
docker compose restart nginx
```

### GitHub Pages not deploying

```bash
# Check workflow status
# Go to: https://github.com/yourusername/DragonsVault.app/actions

# Check Pages settings
# Go to: Settings → Pages → Source should be "GitHub Actions"
```

### Build errors

```bash
# Clean build
rm -rf docs/_build

# Rebuild with verbose output
sphinx-build -M html docs docs/_build -v
```

## Security Considerations

### Private Documentation

To restrict access via nginx:

```nginx
location /docs/ {
    # Require authentication
    auth_basic "Documentation";
    auth_basic_user_file /etc/nginx/.htpasswd;
    
    alias /app/docs/_build/html/;
    index index.html;
    try_files $uri $uri/ $uri.html =404;
}
```

Create password file:

```bash
# Install htpasswd
apt-get install apache2-utils

# Create password file
htpasswd -c /etc/nginx/.htpasswd admin
```

### Public Documentation

If using GitHub Pages, remember:
- All content is public
- Don't include secrets or sensitive data
- Review before pushing

## Next Steps

1. **Choose your hosting method** (Nginx, GitHub Pages, or both)
2. **Build your docs**: `sphinx-build -M html docs docs/_build`
3. **Deploy** using the method above
4. **Update regularly** as you add features
5. **Link from your app** (add "Documentation" link in navbar)

## Support

- Sphinx Documentation: https://www.sphinx-doc.org/
- GitHub Pages: https://docs.github.com/en/pages
- Read the Docs: https://docs.readthedocs.io/

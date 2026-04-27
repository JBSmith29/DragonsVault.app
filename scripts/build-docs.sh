#!/bin/bash
# Build Sphinx documentation

set -e

echo "=========================================="
echo "Building DragonsVault Documentation"
echo "=========================================="
echo ""

# Check if sphinx is installed
if ! command -v sphinx-build &> /dev/null; then
    echo "Sphinx not found. Installing..."
    pip install sphinx sphinx-rtd-theme myst-parser
fi

# Clean previous build
echo "Cleaning previous build..."
rm -rf docs/_build

# Build documentation
echo "Building HTML documentation..."
sphinx-build -M html docs docs/_build -W --keep-going

# Check if build was successful
if [ -f "docs/_build/html/index.html" ]; then
    echo ""
    echo "=========================================="
    echo "✓ Documentation built successfully!"
    echo "=========================================="
    echo ""
    echo "View locally:"
    echo "  python3 -m http.server 8000 --directory docs/_build/html"
    echo "  Then open: http://localhost:8000"
    echo ""
    echo "Or use Hatch:"
    echo "  hatch run docs-serve"
    echo ""
    echo "On your server:"
    echo "  https://yourdomain.com/docs/"
    echo ""
    echo "On GitHub Pages (after pushing):"
    echo "  https://yourusername.github.io/DragonsVault.app/"
    echo ""
else
    echo ""
    echo "=========================================="
    echo "✗ Documentation build failed!"
    echo "=========================================="
    exit 1
fi

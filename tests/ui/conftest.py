import threading
import time
from contextlib import closing
from socket import socket, AF_INET, SOCK_STREAM

import pytest
from werkzeug.serving import make_server

from app import create_app


def _get_free_port(host: str = "127.0.0.1") -> int:
    """Find an available port for the temporary live server."""
    with closing(socket(AF_INET, SOCK_STREAM)) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    """Spin up the Flask app in a background thread for browser-based tests."""
    app = create_app()
    app.testing = True

    host = "127.0.0.1"
    port = _get_free_port(host)
    server = make_server(host, port, app, threaded=True)

    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    base_url = f"http://{host}:{port}"

    # Wait briefly for the server to accept connections
    deadline = time.time() + 5
    healthy = False
    while time.time() < deadline:
        with closing(socket(AF_INET, SOCK_STREAM)) as s:
            try:
                s.connect((host, port))
            except OSError:
                time.sleep(0.05)
            else:
                healthy = True
                break
    if not healthy:
        server.shutdown()
        thread.join(timeout=1)
        raise RuntimeError("Live server failed to start for UI tests.")

    yield base_url

    server.shutdown()
    thread.join(timeout=5)

import subprocess
import time
import socket
import pytest
from pathlib import Path
import tempfile


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def test_install_from_mirror():
    """
    Test that we can serve the mirror and install a package from it using uv.
    """
    mirror_dir = Path("mirror").resolve()
    if not mirror_dir.exists() or not any(mirror_dir.iterdir()):
        pytest.skip(
            "Mirror directory is empty or does not exist. Run build_mirror first."
        )

    port = get_free_port()

    # Start the server in a subprocess
    server_process = subprocess.Popen(
        ["python3", "-m", "http.server", str(port)],
        cwd=mirror_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to start
    time.sleep(2)

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Initialize a new uv project
            subprocess.run(["uv", "init", "--no-workspace"], cwd=tmp_path, check=True)

            # Attempt to install ipykernel from the local server
            # We use --index-url to point to our local server and --no-cache to ensure it actually fetches
            index_url = f"http://localhost:{port}/"

            result = subprocess.run(
                ["uv", "add", "ipykernel", "--index-url", index_url, "--no-cache"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                print(f"STDOUT: {result.stdout}")
                print(f"STDERR: {result.stderr}")

            assert result.returncode == 0
            assert "ipykernel" in result.stdout or "ipykernel" in result.stderr

    finally:
        server_process.terminate()
        server_process.wait()


if __name__ == "__main__":
    test_install_from_mirror()

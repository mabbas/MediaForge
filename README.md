## GrabItDown

GrabItDown is a production-grade media downloader platform with multi-site video/audio support, transcripts, resume capability, and a plugin-friendly architecture.

### Setup

- **Install (development mode)**:

```bash
pip install -e ".[dev]"
```

- **Desktop app (native window)** — optional. The desktop runs in your browser by default. For a native window:

```bash
pip install -e ".[desktop]"
```

  On Windows this installs `pywebview` and `pythonnet`. If `pythonnet` fails to build (common on **Python 3.14** or without .NET tooling), use **Python 3.11 or 3.12** for the desktop extra, or keep using the browser. You also need [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) (usually installed with Edge).

### Development

- **Run unit tests**:

```bash
make test
```

- **Verify configuration wiring**:

```bash
make verify
```


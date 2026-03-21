```md
# yt-dlp GUI Interface

Simple and lightweight graphical interface for **yt-dlp**, focused on ease of use, automatic updates and compatibility with Windows and macOS.

The application allows you to download videos or audio from supported platforms using a clean interface, without requiring command-line knowledge.

## Features

- Simple GUI for yt-dlp
- Automatic yt-dlp update
- Automatic ffmpeg download and update
- Download best quality video in MP4
- Convert audio to MP3
- 1080p and 720p quality options
- Clean file naming
- Portable executable (no installation required)
- Windows and macOS support

## Output structure

When running the application, a folder named `arquivos` is automatically created in the same directory as the executable:

```

app_folder/
│
├── yt-dlp-gui-interface.exe (or macOS binary)
└── arquivos/
├── yt-dlp
├── ffmpeg
├── ffprobe
└── downloaded files

```

All downloaded media and required binaries are stored inside this folder.

## Build from source

If you prefer to compile the application yourself:

### Requirements

- Python 3.9+
- pip
- PyInstaller

Install dependency:

```

pip install pyinstaller

```

### Windows

```

python -m PyInstaller --noconsole --onefile yt_dlp_gui.py

```

### macOS

```

python3 -m PyInstaller --windowed --onefile yt_dlp_gui.py

```

The compiled executable will be available inside the `dist` folder.

## About yt-dlp

This project is a graphical interface and does not modify yt-dlp itself.

yt-dlp repository:
https://github.com/yt-dlp/yt-dlp

## License

This project is provided as-is for educational and personal use.
```
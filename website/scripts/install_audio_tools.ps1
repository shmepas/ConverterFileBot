# Helper PowerShell script to install audio/video/text conversion requirements on Windows
# Requires: Chocolatey installed for ffmpeg/pandoc installation

Write-Host "Installing FFmpeg and Pandoc (requires admin privileges)"
choco install ffmpeg -y
choco install pandoc -y

Write-Host "Installing Python packages in virtual environment (activate venv first)"
python -m pip install -r "website\web_requirements.txt"

Write-Host "If you still see 'No module named audioop' error, try installing pyaudioop"
python -m pip install pyaudioop
Write-Host "Done. Restart your terminal and try running 'python website\web.py' again."
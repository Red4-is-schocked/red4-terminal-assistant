# Red4

A colorful terminal AI assistant powered by Google Gemini. It can chat, remember
the conversation, open apps and websites, and run shell commands (with your approval).

## Features
- Animated gradient banner and 5 color themes (`/theme fire`)
- Streaming replies with Markdown and code highlighting
- Memory saved between sessions
- Tools: open apps, open URLs, run commands (asks `Allow? [y/N]` first)
- Automatic switching to another model when one is busy or slow
- Commands: `/help`, `/clear`, `/theme`, `/model`, `/save`, `/banner`, `/exit`

## Requirements
- Python 3.9+
- A free Gemini API key: https://aistudio.google.com/apikey
- A terminal with true color (GNOME Terminal, Konsole, iTerm2, Windows Terminal)

## Install
```bash
git clone https://github.com/YOUR-USERNAME/red4.git
cd red4
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY="your-key-here"
python3 red4.py
```

To keep the key permanently, add the `export` line to `~/.bashrc` or `~/.zshrc`.

## Run it by name (optional)
```bash
chmod +x red4.py
mkdir -p ~/.local/bin
cp red4.py ~/.local/bin/red4
red4
```

## Security
Red4 can launch apps and run shell commands on your computer. Every command
needs your approval. Read it before typing `y`.
Never commit your API key.

## License
MIT

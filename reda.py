#!/usr/bin/env python3
import os
import sys
import json
import time
import shutil
import tempfile
import functools
import getpass
import subprocess
import webbrowser
from datetime import datetime

try:
    import readline
except ImportError:
    readline = None

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from rich import box
from rich.align import Align
from rich.cells import cell_len
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

API_KEY = "YOUR_GEMINI_API_KEY_HERE"
MODELS = ["gemini-3.5-flash", "gemini-flash-lite-latest", "gemini-3.1-flash-lite"]
model_index = 0


THINK_LEVELS = ["minimal", "low", None]
think_i = 0
HISTORY_LIMIT = 10
TIMEOUT_MS = 10000

HISTORY_FILE = os.path.expanduser("~/.red4_history.json")
USER = getpass.getuser()

SYSTEM_PROMPT = (
    "You are Red4, a warm, kind and friendly AI assistant running in the user's "
    "Linux terminal. Talk naturally like a thoughtful friend. "
    "Reply in the same language the user writes in (English, French, Arabic, Darija...). "
    "You may use Markdown (bold, lists, code blocks) because the terminal renders it nicely. "
    "Keep answers short and clear unless asked for details. "
    "You can control the user's computer with your tools: open_app, open_url, "
    "run_command. When asked to open something, use the tool right away, "
    "then confirm in one short sentence."
)

client = genai.Client(
    api_key=API_KEY,
    http_options=types.HttpOptions(
        timeout=TIMEOUT_MS,
        retry_options=types.HttpRetryOptions(attempts=1),  # no hidden retries
    ),
)
console = Console()

THEMES = {
    "rainbow": ["#ff5f5f", "#ffd75f", "#5fff87", "#5fd7ff", "#af87ff", "#ff5f5f"],
    "fire":    ["#ff0000", "#ff8800", "#ffd000", "#ff8800", "#ff0000"],
    "ocean":   ["#00c6ff", "#0072ff", "#7f5aff", "#0072ff", "#00c6ff"],
    "neon":    ["#ff00cc", "#7a00ff", "#00f0ff", "#7a00ff", "#ff00cc"],
    "sunset":  ["#ff5e62", "#ff9966", "#ffcc70", "#c471ed", "#ff5e62"],
}
theme_name = "rainbow"


def _rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def grad_color(t):
    stops = THEMES[theme_name]
    n = len(stops) - 1
    pos = (t % 1.0) * n
    i = int(pos)
    f = pos - i
    a, b = _rgb(stops[i]), _rgb(stops[i + 1])
    return "#%02x%02x%02x" % tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def accent():
    return grad_color(0.0)


def gradient_text(s, offset=0.0, span=1.0, bold=True):
    t = Text()
    n = max(1, len(s))
    for i, ch in enumerate(s):
        t.append(ch, style=("bold " if bold else "") + grad_color(offset + i / n * span))
    return t

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Thinking:
    """Animated 'thinking' line with a flowing gradient."""

    def __init__(self):
        self.t0 = time.time()

    def __rich_console__(self, console, options):
        el = time.time() - self.t0
        t = Text("  ")
        t.append(SPIN[int(el * 12) % len(SPIN)] + " ", style="bold " + grad_color(el * 0.5))
        t.append_text(gradient_text("Red4 is thinking", offset=-el * 0.6, span=0.5, bold=False))
        t.append("." * (int(el * 3) % 4), style="dim")
        t.append(f"   {el:.1f}s", style="dim")
        yield t


def reply_panel(text, model=None, elapsed=None):
    sub = Text(f"{model} · {elapsed:.1f}s", style="dim") if model else None
    return Panel(
        Markdown(text, code_theme="monokai"),
        title=gradient_text(" ◆ Red4 ", span=0.5),
        title_align="left",
        subtitle=sub,
        subtitle_align="right",
        border_style=accent(),
        box=box.ROUNDED,
        padding=(0, 2),
        width=min(console.width, 110),
    )


class StreamView:
    """Live reply while streaming. Shows the tail if it's taller than the screen."""

    def __init__(self):
        self.text = ""

    def __rich_console__(self, console, options):
        panel = reply_panel(self.text + " ▌")
        lines = console.render_lines(panel, options, pad=False)
        maxh = max(5, console.height - 3)
        if len(lines) > maxh:
            lines = lines[-maxh:]
        for i, line in enumerate(lines):
            yield from line
            if i < len(lines) - 1:
                yield Segment.line()


_live = None


def pause_live():
    if _live is not None and _live.is_started:
        _live.stop()
        return _live
    return None


def resume_live(lv):
    if lv is not None:
        lv.start()

BANNER = [
    "██████╗ ███████╗██████╗ ██╗  ██╗",
    "██╔══██╗██╔════╝██╔══██╗██║  ██║",
    "██████╔╝█████╗  ██║  ██║███████║",
    "██╔══██╗██╔══╝  ██║  ██║╚════██║",
    "██║  ██║███████╗██████╔╝     ██║",
    "╚═╝  ╚═╝╚══════╝╚═════╝      ╚═╝",
]


def banner_frame(offset, reveal=1.0):
    w = max(len(l) for l in BANNER)
    t = Text(justify="center")
    for r, line in enumerate(BANNER):
        shown = line[: int(len(line) * reveal)].ljust(w)
        for c, ch in enumerate(shown):
            t.append(ch, style="bold " + grad_color(offset + c / w * 0.7 + r * 0.05))
        t.append("\n")
    return Align.center(t)


def info_cards():
    cards = [
        ("MODEL", MODELS[model_index]),
        ("MEMORY", f"{len(messages) // 2} exchanges"),
        ("TOOLS", "apps · web · shell"),
        ("TIME", datetime.now().strftime("%a %d %b · %H:%M")),
    ]
    panels = [
        Panel(
            Align.center(Text(v, style="bold white")),
            title=Text(k, style="bold " + accent()),
            border_style=accent(),
            box=box.ROUNDED,
            padding=(0, 1),
        )
        for k, v in cards
    ]
    return Columns(panels, equal=True, expand=True)


def show_banner(animate=True):
    console.clear()
    console.print()
    if animate and console.is_terminal:
        with Live(banner_frame(0, 0), console=console, refresh_per_second=30, transient=False) as live:
            for f in range(30):
                live.update(banner_frame(f * 0.03, min(1.0, f / 10)), refresh=True)
                time.sleep(0.03)
    else:
        console.print(banner_frame(0))
    hour = datetime.now().hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    console.print(Align.center(gradient_text(f"{greet}, {USER}.  Your terminal AI assistant.", bold=False)))
    console.print()
    console.print(gradient_text("━" * console.width, span=1.0, bold=False))
    console.print(info_cards())
    hint = Text("type a message  ·  ", style="dim")
    hint.append("/help", style="bold " + accent())
    hint.append(" for commands  ·  Tab completes commands", style="dim")
    console.print(Align.center(hint))
    console.print()


def show_help():
    t = Table(box=box.SIMPLE_HEAD, border_style=accent(), header_style="bold " + accent(), expand=False)
    t.add_column("Command", style="bold white")
    t.add_column("What it does", style="dim")
    for c, d in [
        ("/help", "show this list"),
        ("/clear", "forget the whole conversation"),
        ("/theme [name]", "change colors: " + ", ".join(THEMES)),
        ("/model [number]", "list or switch the AI model"),
        ("/save", "export this chat to a Markdown file"),
        ("/banner", "replay the intro animation"),
        ("/exit", "quit (or Ctrl+D)"),
    ]:
        t.add_row(c, d)
    console.print(Panel(t, title=gradient_text(" ✦ Commands ", span=0.5), border_style=accent(),
                        box=box.ROUNDED, expand=False))

def logged(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        arg = ", ".join([str(x) for x in a] + [str(v) for v in k.values()])
        console.print(Text.assemble(("  ⚙ ", "bold yellow"), (fn.__name__, "bold"),
                                    (" ❯ ", "dim"), (arg[:80], "cyan")))
        out = fn(*a, **k)
        bad = str(out).lower().startswith(("could not", "the user denied", "command timed"))
        console.print(Text("  ✗ failed" if bad else "  ✓ done", style="red" if bad else "green"))
        return out
    return wrapper


def _launch(cmd):
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


@logged
def open_app(name: str) -> str:
    """Open an application on the user's computer by name, e.g. 'spotify', 'firefox', 'calculator', 'code'."""
    n = name.strip().lower()
    if shutil.which(n):
        _launch([n])
        return f"Opened {name}"
    flatpak_ids = {
        "spotify": "com.spotify.Client",
        "discord": "com.discordapp.Discord",
        "telegram": "org.telegram.desktop",
        "vlc": "org.videolan.VLC",
    }
    app_id = flatpak_ids.get(n)
    if app_id and shutil.which("flatpak"):
        check = subprocess.run(["flatpak", "info", app_id], capture_output=True)
        if check.returncode == 0:
            _launch(["flatpak", "run", app_id])
            return f"Opened {name} (flatpak)"
    if shutil.which("gtk-launch"):
        r = subprocess.run(["gtk-launch", n], capture_output=True)
        if r.returncode == 0:
            return f"Opened {name}"
    return f"Could not find an app called '{name}' on this computer."


@logged
def open_url(url: str) -> str:
    """Open a website in the default browser."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}"


def run_command(command: str) -> str:
    """Run a shell command on the user's computer and return its output. The user will be asked to approve it first. Apps and long-running programs keep running in the background."""
    lv = pause_live()
    try:
        console.print()
        console.print(Panel(Text(command, style="bold white"),
                            title="[bold yellow]⚠ Red4 wants to run[/]",
                            border_style="yellow", box=box.HEAVY, padding=(0, 2)))
        ok = input("\001\033[1;33m\002Allow? [y/N] ❯ \001\033[0m\002").strip().lower()
        if ok != "y":
            console.print("  [red]✗ denied[/]\n")
            return "The user denied this command."
        console.print("  [green]✓ running[/]\n")
        with tempfile.TemporaryFile("w+") as f:
            p = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL, stdout=f,
                                 stderr=subprocess.STDOUT, start_new_session=True)
            try:
                p.wait(timeout=5)
                status = ""
            except subprocess.TimeoutExpired:
                status = "(still running in the background)"
            f.seek(0)
            out = f.read().strip()[:3000]
        return (out + " " + status).strip() or "(no output)"
    finally:
        resume_live(lv)

def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(msgs):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False, indent=2)
    os.chmod(HISTORY_FILE, 0o600)


def recent(msgs):
    h = msgs[-HISTORY_LIMIT:]
    while h and h[0]["role"] != "user":
        h = h[1:]
    return h


def make_chat(msgs):
    history = [
        types.Content(role=m["role"], parts=[types.Part.from_text(text=m["text"])])
        for m in recent(msgs)
    ]
    kwargs = dict(system_instruction=SYSTEM_PROMPT, tools=[open_app, open_url, run_command])
    if THINK_LEVELS[think_i]:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=THINK_LEVELS[think_i])
    return client.chats.create(
        model=MODELS[model_index],
        history=history,
        config=types.GenerateContentConfig(**kwargs),
    )


def save_chat():
    path = os.path.expanduser(f"~/red4_chat_{datetime.now():%Y%m%d_%H%M%S}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Red4 chat\n\n")
        for m in messages:
            f.write(f"**{'You' if m['role'] == 'user' else 'Red4'}:** {m['text']}\n\n")
    return path

COMMANDS = ["/help", "/clear", "/theme", "/model", "/save", "/banner", "/exit"]
if readline:
    def _complete(text, state):
        opts = [c for c in COMMANDS if c.startswith(text)]
        return opts[state] if state < len(opts) else None
    readline.set_completer_delims(" \t\n")
    readline.set_completer(_complete)
    readline.parse_and_bind("tab: complete")


def read_user():
    console.print(Text.assemble(("╭─ ", accent()), (USER, "bold cyan"), (" ─ ", accent()),
                                (datetime.now().strftime("%H:%M"), "dim")))
    r, g, b = _rgb(accent())
    return input(f"\001\033[1;38;2;{r};{g};{b}m\002╰─❯ \001\033[0m\002").strip()


def erase_input(user):
    """Remove the raw typed lines so we can show a nice bubble instead."""
    if not console.is_terminal:
        return
    width = max(10, console.width)
    lines = max(1, -(-(4 + cell_len(user)) // width))
    sys.stdout.write("\033[1A\033[2K" * (lines + 1) + "\r")
    sys.stdout.flush()


def user_bubble(user):
    console.print(Align.right(Panel(
        Text(user), title=Text("you", style="bold cyan"), title_align="right",
        border_style="cyan", box=box.ROUNDED, expand=False, padding=(0, 2),
    )))


def switch_model(reason):
    global model_index, chat
    model_index = (model_index + 1) % len(MODELS)
    chat = make_chat(messages)
    console.print(f"[yellow]⚡ {reason}, switching to {MODELS[model_index]}[/]")


def ask(user):
    """Send a message and stream the reply. Returns the reply text, or None on failure."""
    global chat, think_i, _live
    for _ in range(len(MODELS) + len(THINK_LEVELS) + 2):
        t0 = time.time()
        reply = ""
        view = StreamView()
        live = Live(Thinking(), console=console, refresh_per_second=15,
                    transient=True, vertical_overflow="crop")
        _live = live
        live.start()
        err = None
        try:
            for chunk in chat.send_message_stream(user):
                text = chunk.text or ""
                if not text:
                    continue
                if not reply:
                    live.update(view)
                reply += text
                view.text = reply
        except (ServerError, ClientError) as e:
            err = e
        except KeyboardInterrupt:
            err = "cancel"
        except Exception:
            err = "timeout"
        finally:
            live.stop()
            _live = None

        if err is None:
            console.print(reply_panel(reply or "*(done)*", MODELS[model_index], time.time() - t0))
            console.print()
            return reply
        if err == "cancel":
            console.print("[dim]cancelled[/]\n")
            return None
        if err == "timeout":
            switch_model("too slow")
            continue
        if isinstance(err, ServerError):
            switch_model("model busy")
            continue

        msg = str(err.message or "")
        low = msg.lower()
        if think_i < len(THINK_LEVELS) - 1 and ("thinking" in low or "invalid argument" in low):
            think_i += 1
            chat = make_chat(messages)
            console.print("[yellow]adjusting speed settings...[/]")
            continue
        if getattr(err, "code", None) in (404, 429):
            switch_model("model unavailable" if err.code == 404 else "rate limit")
            continue
        console.print(Panel(Text(msg), title="✗ Error", border_style="red", box=box.ROUNDED))
        return None
    console.print(Panel("All models are busy. Try again in a moment.", border_style="red", box=box.ROUNDED))
    return None

messages = load_history()
chat = make_chat(messages)
show_banner()

while True:
    try:
        user = read_user()
    except (KeyboardInterrupt, EOFError):
        console.print()
        break

    erase_input(user)
    if not user:
        continue

    low = user.lower()
    if low in ("exit", "quit", "/exit", "/quit"):
        break

    if user.startswith("/"):
        cmd, _, arg = user.partition(" ")
        arg = arg.strip().lower()
        if cmd == "/help":
            show_help()
        elif cmd == "/clear":
            messages = []
            save_history(messages)
            chat = make_chat(messages)
            show_banner(animate=False)
            console.print("[green]✓ Memory cleared[/]\n")
        elif cmd == "/theme":
            if arg in THEMES:
                theme_name = arg
                show_banner(animate=False)
            else:
                console.print(Text("Themes: ", style="dim") + gradient_text(", ".join(THEMES), bold=False))
                console.print("[dim]Use: /theme fire[/]\n")
        elif cmd == "/model":
            if arg.isdigit() and 1 <= int(arg) <= len(MODELS):
                model_index = int(arg) - 1
                chat = make_chat(messages)
            for i, m in enumerate(MODELS, 1):
                mark = "[bold green]●[/]" if i - 1 == model_index else "[dim]○[/]"
                console.print(f"  {mark} {i}. {m}")
            console.print("[dim]Switch with: /model 2[/]\n")
        elif cmd == "/save":
            console.print(f"[green]✓ Saved to[/] {save_chat()}\n")
        elif cmd == "/banner":
            show_banner()
        else:
            console.print("[yellow]Unknown command. Try /help[/]\n")
        continue

    user_bubble(user)
    reply = ask(user)
    if reply is not None:
        messages.append({"role": "user", "text": user})
        messages.append({"role": "model", "text": reply})
        save_history(messages)

console.print(Align.center(gradient_text("Bye! See you soon 👋")))
console.print(gradient_text("━" * console.width, span=1.0, bold=False))

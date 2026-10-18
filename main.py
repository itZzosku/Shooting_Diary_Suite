from flask import Flask, Response, abort, render_template
from dotenv import load_dotenv
import os
import re
from collections import OrderedDict
import socket
import markdown
from waitress import serve
import diary

# Load environment variables from .env file
load_dotenv()


app = Flask(__name__)

# Shooting diary (password-protected) at /diary
diary.init_app(app)


# Landing page: the shooting diary and any notes (templates/index.html)
@app.route('/')
def index():
    return render_template('index.html', notes=list_notes())


# Markdown notes: drop a .md file into the notes/ folder and it shows up on the site.
NOTES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'notes')


def note_title(path, stem):
    """Use the file's first '# ' heading as the title, fall back to the filename."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('# '):
                    return line[2:].strip()
    except OSError:
        pass
    return stem.replace('_', ' ').replace('-', ' ').strip().capitalize()


def list_notes():
    """Scan notes/ and return an OrderedDict of slug -> {title, file}."""
    notes = OrderedDict()
    if not os.path.isdir(NOTES_DIR):
        return notes
    for filename in sorted(os.listdir(NOTES_DIR)):
        if not filename.lower().endswith('.md'):
            continue
        stem = os.path.splitext(filename)[0]
        slug = re.sub(r'[^a-z0-9]+', '-', stem.lower()).strip('-')
        if not slug:
            continue
        notes[slug] = {
            "title": note_title(os.path.join(NOTES_DIR, filename), stem),
            "file": filename,
        }
    return notes


NOTE_PAGE_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #14171c;
    --card: #1c2027;
    --line: #2c313b;
    --text: #e6e9ef;
    --muted: #8a93a3;
    --accent: #5aa9e6;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 15px/1.6 -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
    padding: 24px 16px 64px;
  }}
  .wrap {{ max-width: 760px; margin: 0 auto; }}
  a.back {{ color: var(--accent); font-size: 13px; text-decoration: none; }}
  .note {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 20px 24px;
    margin-top: 12px;
  }}
  .note h1 {{ font-size: 22px; margin: 0 0 16px; }}
  .note h2 {{ font-size: 17px; margin: 28px 0 10px; color: var(--accent); }}
  .note p {{ margin: 10px 0; }}
  .note hr {{ border: 0; border-top: 1px solid var(--line); margin: 24px 0; }}
  .note em {{ color: var(--muted); }}
  .note ul {{ padding-left: 20px; }}
  .note li {{ margin: 4px 0; }}
  .note code {{
    background: #0f1216;
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 1px 5px;
    font-size: 13px;
  }}
  .table-scroll {{ overflow-x: auto; }}
  .note table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-variant-numeric: tabular-nums;
  }}
  .note th, .note td {{
    border: 1px solid var(--line);
    padding: 8px 10px;
    text-align: left;
  }}
  .note th {{ background: #0f1216; color: var(--muted); font-size: 13px; font-weight: 600; }}
  .note tbody tr:nth-child(even) {{ background: rgba(255, 255, 255, 0.02); }}
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/">&larr; back</a>
  <div class="note">
{body}
  </div>
</div>
</body>
</html>'''


def render_note(filename):
    """Read a markdown file from notes/ and return it as HTML."""
    path = os.path.join(NOTES_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()

    # Task list checkboxes -> symbols (python-markdown has no task list support)
    text = re.sub(r'^(\s*[-*] )\[x\] ', r'\1☑ ', text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'^(\s*[-*] )\[ \] ', r'\1☐ ', text, flags=re.MULTILINE)

    html = markdown.markdown(text, extensions=['tables'])
    # Let wide tables scroll instead of breaking the layout on a phone
    html = html.replace('<table>', '<div class="table-scroll"><table>')
    html = html.replace('</table>', '</table></div>')
    return html


@app.route('/notes/<slug>')
def note_page(slug):
    note = list_notes().get(slug)
    if note is None:
        abort(404)
    try:
        body = render_note(note["file"])
    except OSError:
        abort(404, description="Note file not found")
    page = NOTE_PAGE_TEMPLATE.format(title=note["title"], body=body)
    return Response(page, mimetype='text/html')


def get_local_ip():
    """Function to get the local IP address of the machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'  # Fallback to localhost if unable to determine local IP
    finally:
        s.close()
    return IP


if __name__ == '__main__':
    local_ip = get_local_ip()
    port = 5000
    print("Flask app starting")
    print(f"http://{local_ip}:{port}/")
    print(f"http://{local_ip}:{port}/diary/")
    for slug in list_notes():
        print(f"http://{local_ip}:{port}/notes/{slug}")
    serve(app, host=local_ip, port=port)

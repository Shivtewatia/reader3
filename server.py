import os
import pickle
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from reader3 import Book, BookMetadata, ChapterContent, TOCEntry, process_epub, save_to_pickle

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Where are the book folders located?
BOOKS_DIR = "."

# OpenAI configuration - reads from env var first, then .env file
def load_env_file():
    """Load key=value pairs from any *.env file in the project directory."""
    import glob
    base = os.path.dirname(os.path.abspath(__file__))
    # Find any file ending in .env (e.g. key.env, .env, mykeys.env)
    candidates = glob.glob(os.path.join(base, "*.env")) + glob.glob(os.path.join(base, ".env"))
    for env_path in candidates:
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and v:
                            os.environ.setdefault(k, v)
            break  # stop after the first .env file found

load_env_file()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


class ChatRequest(BaseModel):
    book_id: str
    chapter_index: int
    selected_text: str
    action: str = "explain"


@lru_cache(maxsize=10)
def load_book_cached(folder_name: str) -> Optional[Book]:
    """
    Loads the book from the pickle file.
    Cached so we don't re-read the disk on every click.
    """
    file_path = os.path.join(BOOKS_DIR, folder_name, "book.pkl")
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "rb") as f:
            book = pickle.load(f)
        return book
    except Exception as e:
        print(f"Error loading book {folder_name}: {e}")
        return None

@app.get("/", response_class=HTMLResponse)
async def library_view(request: Request):
    """Lists all available processed books."""
    books = []

    # Scan directory for folders ending in '_data' that have a book.pkl
    if os.path.exists(BOOKS_DIR):
        for item in os.listdir(BOOKS_DIR):
            if item.endswith("_data") and os.path.isdir(item):
                # Try to load it to get the title
                book = load_book_cached(item)
                if book:
                    books.append({
                        "id": item,
                        "title": book.metadata.title,
                        "author": ", ".join(book.metadata.authors),
                        "chapters": len(book.spine),
                        "has_cover": bool(getattr(book.metadata, 'cover_image', None))
                    })

    return templates.TemplateResponse("library.html", {"request": request, "books": books})


@app.get("/cover/{book_id}")
async def serve_cover(book_id: str):
    """Serves the cover image for a book."""
    safe_book_id = os.path.basename(book_id)
    book = load_book_cached(safe_book_id)

    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    cover_path = getattr(book.metadata, 'cover_image', None)
    if not cover_path:
        raise HTTPException(status_code=404, detail="No cover image")

    full_path = os.path.join(BOOKS_DIR, safe_book_id, cover_path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Cover file not found")

    return FileResponse(full_path)


@app.get("/read/{book_id}", response_class=HTMLResponse)
async def redirect_to_first_chapter(book_id: str):
    """Helper to just go to chapter 0."""
    return await read_chapter(book_id=book_id, chapter_index=0)

@app.get("/read/{book_id}/{chapter_index}", response_class=HTMLResponse)
async def read_chapter(request: Request, book_id: str, chapter_index: int):
    """The main reader interface."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")

    current_chapter = book.spine[chapter_index]

    # Calculate Prev/Next links
    prev_idx = chapter_index - 1 if chapter_index > 0 else None
    next_idx = chapter_index + 1 if chapter_index < len(book.spine) - 1 else None

    return templates.TemplateResponse("reader.html", {
        "request": request,
        "book": book,
        "current_chapter": current_chapter,
        "chapter_index": chapter_index,
        "book_id": book_id,
        "prev_idx": prev_idx,
        "next_idx": next_idx
    })

@app.get("/read/{book_id}/images/{image_name}")
async def serve_image(book_id: str, image_name: str):
    """Serves images specifically for a book."""
    safe_book_id = os.path.basename(book_id)
    safe_image_name = os.path.basename(image_name)

    img_path = os.path.join(BOOKS_DIR, safe_book_id, "images", safe_image_name)

    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(img_path)


@app.post("/api/chat")
async def chat_with_llm(req: ChatRequest):
    """LLM chat endpoint - sends selected text + book context to Claude."""
    if not ANTHROPIC_API_KEY:
        return JSONResponse(
            status_code=400,
            content={"error": "Anthropic API key not configured. Set ANTHROPIC_API_KEY environment variable."}
        )

    book = load_book_cached(req.book_id)
    if not book:
        return JSONResponse(status_code=404, content={"error": "Book not found"})

    if req.chapter_index < 0 or req.chapter_index >= len(book.spine):
        return JSONResponse(status_code=404, content={"error": "Chapter not found"})

    # Build book context - use full book if small enough, otherwise nearby chapters
    all_text = " ".join([ch.text for ch in book.spine])
    word_count = len(all_text.split())

    if word_count <= 75000:
        # Send full book
        book_context = all_text
    else:
        # Send current chapter + 2 adjacent chapters on each side
        start = max(0, req.chapter_index - 2)
        end = min(len(book.spine), req.chapter_index + 3)
        book_context = " ".join([book.spine[i].text for i in range(start, end)])

    current_chapter_text = book.spine[req.chapter_index].text

    system_prompt = f"""You are an intelligent reading assistant. The user is reading "{book.metadata.title}" by {', '.join(book.metadata.authors)}.

They are currently reading chapter/section {req.chapter_index + 1} of {len(book.spine)}.

Here is the content of the current chapter for context:
---
{current_chapter_text[:8000]}
---

Here is broader book context:
---
{book_context[:30000]}
---

The user has selected some text and wants you to explain it. Provide a clear, insightful explanation that:
1. Explains the meaning of the selected text in the context of the book
2. Provides relevant background or context from the book
3. Uses examples where helpful
4. Is concise but thorough"""

    user_message = f'Please explain this passage from the book:\n\n"{req.selected_text}"'

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}]
        )

        return {"response": response.content[0].text}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Claude API error: {str(e)}"}
        )


@app.post("/upload")
async def upload_epub(file: UploadFile = File(...)):
    """Upload and process an EPUB file, adding it to the library."""
    if not file.filename.lower().endswith(".epub"):
        return JSONResponse(status_code=400, content={"error": "Only .epub files are supported."})

    # Sanitize filename
    safe_name = "".join(c for c in file.filename if c.isalnum() or c in "._- ").strip()
    if not safe_name.lower().endswith(".epub"):
        safe_name += ".epub"

    epub_path = os.path.join(BOOKS_DIR, safe_name)
    out_dir = os.path.join(BOOKS_DIR, os.path.splitext(safe_name)[0] + "_data")

    try:
        # Save uploaded EPUB to disk
        content = await file.read()
        with open(epub_path, "wb") as f:
            f.write(content)

        # Process into structured book data
        book = process_epub(epub_path, out_dir)
        save_to_pickle(book, out_dir)

        # Clear the cache so the new book appears in the library
        load_book_cached.cache_clear()

        book_id = os.path.basename(out_dir)
        return {"book_id": book_id, "title": book.metadata.title}

    except Exception as e:
        # Clean up partial files on failure
        if os.path.exists(epub_path):
            os.remove(epub_path)
        return JSONResponse(status_code=500, content={"error": str(e)})


if __name__ == "__main__":
    import socket
    import threading
    import webbrowser
    import uvicorn

    # Find a free port starting at 8123
    def find_free_port(start=8123):
        for port in range(start, start + 10):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port
        return start

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    # Open the browser automatically after the server is up
    def open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    print(f"Starting server at {url}")
    uvicorn.run(app, host="127.0.0.1", port=port)

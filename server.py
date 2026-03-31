import os
import pickle
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from reader3 import Book, BookMetadata, ChapterContent, TOCEntry

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Where are the book folders located?
BOOKS_DIR = "."

# OpenAI configuration - reads from env var first, then .env file
def load_env_file():
    """Load key=value pairs from .env file if it exists."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value:
                        os.environ.setdefault(key, value)

load_env_file()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


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
    """LLM chat endpoint - sends selected text + book context to OpenAI."""
    if not OPENAI_API_KEY:
        return JSONResponse(
            status_code=400,
            content={"error": "OpenAI API key not configured. Set OPENAI_API_KEY environment variable."}
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
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=2000,
            temperature=0.7
        )

        return {"response": response.choices[0].message.content}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"OpenAI API error: {str(e)}"}
        )


if __name__ == "__main__":
    import uvicorn
    print("Starting server at http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123)

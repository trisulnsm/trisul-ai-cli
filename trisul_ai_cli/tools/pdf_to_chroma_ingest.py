import time
import google.generativeai as genai
import chromadb
from pypdf import PdfReader
import tiktoken
from google.api_core.exceptions import ResourceExhausted
import os

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
embedding_model = "models/gemini-embedding-001"


def get_embedding_with_retry(text: str, retries=5, backoff=10):
    """
    Get embedding with retry logic for rate limit (429) errors.
    Exponential backoff ensures longer waits if multiple failures occur.
    """
    for attempt in range(retries):
        try:
            resp = genai.embed_content(model=embedding_model, content=text)
            return resp['embedding']
        except ResourceExhausted:
            wait_time = backoff * (2 ** attempt)
            print(f"⚠️ Rate limit hit. Waiting {wait_time}s before retry...")
            time.sleep(wait_time)
    raise RuntimeError("❌ Failed after multiple retries due to rate limits.")


def load_pdf(file_path: str) -> str:
    reader = PdfReader(file_path)
    return "\n".join([page.extract_text() for page in reader.pages])


def chunk_text(text, max_tokens=500):
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    chunks = []
    for i in range(0, len(tokens), max_tokens):
        chunk = enc.decode(tokens[i:i + max_tokens])
        chunks.append(chunk)
    return chunks


def index_pdf(pdf_path: str, collection_name="pdf_docs"):
    # Persistent Chroma
    chroma_client = chromadb.PersistentClient(path="/tmp/chroma_store")
    collection = chroma_client.get_or_create_collection(collection_name)

    # Load + chunk
    pdf_text = load_pdf(pdf_path)
    chunks = chunk_text(pdf_text)

    # Store embeddings
    for i, chunk in enumerate(chunks):
        print(f"➡️ Embedding chunk {i+1}/{len(chunks)}")
        emb = get_embedding_with_retry(chunk)
        collection.add(
            documents=[chunk],
            embeddings=[emb],
            ids=[f"{pdf_path}_chunk_{i}"]
        )

    print(f"✅ Indexed {len(chunks)} chunks from {pdf_path} into {collection_name}")


if __name__ == "__main__":
    index_pdf("/home/partha/Downloads/Trisul_User_Guide_v1.3.pdf")

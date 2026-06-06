from llama_index.core import SimpleDirectoryReader, StorageContext, VectorStoreIndex
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore
from chromadb import PersistentClient
from llama_index.core import Settings
from llama_index.llms.ollama import Ollama
import os

INDEX_DIR = "./runbook_index"
DATA_DIR = "./data/runbooks"
COLLECTION_NAME = "sre_runbooks"

def create_index():
    Settings.llm = Ollama(model="llama3.1", request_timeout=360.0)
    embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en")
    Settings.embed_model = embed_model

    documents = SimpleDirectoryReader(DATA_DIR).load_data()
    print(f"Loaded {len(documents)} documents")

    pipeline = IngestionPipeline(
        transformations=[
            SentenceSplitter(chunk_size=512, chunk_overlap=50),
            embed_model,
        ],
    )

    nodes = pipeline.run(documents=documents)
    print(f"Created {len(nodes)} nodes")

    chroma_client = PersistentClient(path=INDEX_DIR)
    chroma_collection = chroma_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    index = VectorStoreIndex(
        nodes,
        storage_context=storage_context,
        embed_model=embed_model,
        show_progress=True,
    )
    index.storage_context.persist()
    print(f"Index persisted with {len(nodes)} nodes to {INDEX_DIR}")
    return index

if __name__ == "__main__":
    create_index()

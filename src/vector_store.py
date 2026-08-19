import json

import streamlit as st
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from src import config


def _load_few_shots() -> list[dict]:
    with open(config.FEW_SHOT_PATH, encoding="utf-8") as f:
        return json.load(f)


def _build_embeddings() -> Embeddings:
    if config.EMBEDDING_PROVIDER == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=config.OPENAI_EMBEDDING_MODEL,
            api_key=config.OPENAI_API_KEY,
        )

    from langchain_community.embeddings import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(model_name=config.LOCAL_EMBEDDING_MODEL)


@st.cache_resource(show_spinner="Few-Shot 임베딩 모델 및 벡터 인덱스 로딩 중...")
def get_vectorstore() -> VectorStore:
    """임베딩 모델 로딩 + FAISS 인덱스 구축을 프로세스당 한 번만 수행한다."""
    from langchain_community.vectorstores import FAISS

    few_shots = _load_few_shots()
    docs = [
        Document(page_content=item["question"], metadata={"sql": item["sql"]})
        for item in few_shots
    ]
    return FAISS.from_documents(docs, _build_embeddings())


def get_similar_sql_examples(user_query: str, k: int = 2) -> list[dict]:
    store = get_vectorstore()
    results = store.similarity_search(user_query, k=k)
    return [
        {"question": doc.page_content, "sql": doc.metadata["sql"]}
        for doc in results
    ]

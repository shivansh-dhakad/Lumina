import logging
from functools import lru_cache

from langchain_huggingface import ChatHuggingFace, HuggingFaceEmbeddings, HuggingFacePipeline

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def get_embedding_model(model_name: str) -> HuggingFaceEmbeddings:
    logger.info("Loading embedding model: %s", model_name)
    return HuggingFaceEmbeddings(model_name=model_name)


@lru_cache(maxsize=2)
def get_chat_model(model_id: str) -> ChatHuggingFace:
    logger.info("Loading LLM: %s", model_id)
    llm = HuggingFacePipeline.from_model_id(
        model_id=model_id,
        task="text-generation",
        pipeline_kwargs={
            "return_full_text": False,
            "max_new_tokens": 1024,
            "min_new_tokens": 128,
            "do_sample": True,
            "temperature": 0.4,
            "top_p": 0.9,
            "repetition_penalty": 1.15,
            "no_repeat_ngram_size": 3,
        },
    )
    return ChatHuggingFace(llm=llm)

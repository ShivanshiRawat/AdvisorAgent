"""
Similarity package.
Exposes the vector-based use case similarity engine.
"""
from .engine import find_similar_cases, generate_signature_vectors

__all__ = ["find_similar_cases", "generate_signature_vectors"]

from pathlib import Path

from querymind.learning.feedback import FeedbackStore, QueryExample, format_few_shot_examples
from querymind.learning.similarity import jaccard_similarity, rank_similar_examples, tokenize


def test_tokenize():
    tokens = tokenize("Find the top 5 customers with highest revenue in Germany")
    assert "customers" in tokens
    assert "revenue" in tokens
    assert "germany" in tokens
    # Stopwords filtered
    assert "the" not in tokens
    assert "in" not in tokens
    assert "with" not in tokens


def test_jaccard_similarity():
    s1 = {"orders", "customers", "germany"}
    s2 = {"orders", "customers", "france"}
    score = jaccard_similarity(s1, s2)
    assert 0.4 < score < 0.6
    assert jaccard_similarity(s1, s1) == 1.0
    assert jaccard_similarity(set(), s1) == 0.0


def test_rank_similar_examples():
    examples = [
        {"question": "How many customers are in Germany?", "sql": "SELECT count(*) FROM customers WHERE country='DE'"},
        {"question": "What is the total revenue in 2024?", "sql": "SELECT sum(price) FROM orders"},
        {"question": "List all products in stock", "sql": "SELECT * FROM products WHERE stock > 0"},
    ]
    ranked = rank_similar_examples("Show me customers from Germany", examples, top_k=2)
    assert len(ranked) >= 1
    assert "Germany" in ranked[0]["question"]


def test_feedback_store_record_and_load(tmp_path: Path):
    store_file = tmp_path / "feedback.jsonl"
    store = FeedbackStore(store_file)

    assert store.load_all() == []

    store.record(
        question="top customers by revenue",
        sql="SELECT customer_id, SUM(price) FROM orders GROUP BY customer_id",
        database="shop",
        explanation="aggregates spend per customer",
    )
    store.record(
        question="products low in stock",
        sql="SELECT * FROM products WHERE stock < 10",
        database="shop",
    )

    all_ex = store.load_all(database="shop")
    assert len(all_ex) == 2
    assert all_ex[0].question == "top customers by revenue"

    similar = store.find_similar("who are top spending customers?", database="shop", top_k=1)
    assert len(similar) == 1
    assert "revenue" in similar[0].question or "customers" in similar[0].question

    prompt_text = format_few_shot_examples(similar)
    assert "Similar verified queries" in prompt_text
    assert "User: top customers by revenue" in prompt_text
